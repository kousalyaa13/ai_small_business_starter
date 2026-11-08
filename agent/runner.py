"""Drives graph runs in the background with a per-run lease.

The lease guarantees one worker per run, so duplicate submissions (double clicks,
two app workers, restart recovery) never execute the same run concurrently.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from langgraph.types import Command

from .budget import BudgetExceeded
from .store import new_id

log = logging.getLogger(__name__)

WAITING = {"clarification": "waiting_input", "approval": "waiting_approval"}


class InlineExecutor:
    """Runs work immediately; used by tests and scripts for deterministic behaviour."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Runner:
    def __init__(self, store, graph, executor=None, lease_ttl=900):
        self.store, self.graph = store, graph
        self.executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-run")
        self.lease_ttl = lease_ttl
        self.owner = new_id("wrk")

    def submit(self, run_id, resume=None, from_statuses=("queued",)):
        self.executor.submit(self.drive, run_id, resume, tuple(from_statuses))

    def recover(self):
        for run_id in self.store.recoverable_runs():
            self.submit(run_id)

    def drive(self, run_id, resume=None, from_statuses=("queued",)) -> bool:
        if not self.store.acquire_lease(run_id, self.owner, self.lease_ttl, from_statuses):
            return False  # another worker owns the run, or it is not in a resumable state
        run = self.store.get_run(run_id)
        config = {"configurable": {"thread_id": run_id}}
        try:
            if resume is not None:
                self.graph.invoke(Command(resume=resume), config)
            else:
                state = self.graph.get_state(config)
                if state.values or state.next:
                    self.graph.invoke(None, config)  # continue from the last checkpoint
                else:
                    version, _ = self.store.latest_requirements(run["project_id"])
                    initial = {"project_id": run["project_id"], "run_id": run_id,
                               "requirement_version": version, "research_notes": []}
                    seed = run["snapshot"].get("seed")
                    if seed:  # follow-up run: reuse the parts, research the requested ones again
                        initial.update(summary=seed.get("summary", ""), components=seed["components"],
                                       force_research=seed.get("force_research", []),
                                       research_goal=seed.get("goal", "both"))
                    self.graph.invoke(initial, config)
            state = self.graph.get_state(config)
            pending = [i.value for task in state.tasks for i in task.interrupts]
            if pending:
                self.store.release(run_id, self.owner, WAITING.get(pending[0].get("type"), "waiting_input"))
            elif not state.next:
                self.store.release(run_id, self.owner, "completed")
            else:
                self.store.release(run_id, self.owner, "queued")
        except BudgetExceeded as exc:
            self.store.release(run_id, self.owner, "failed", f"Stopped: {exc}.")
        except Exception:
            log.exception("agent run %s failed", run_id)
            self.store.release(run_id, self.owner, "failed", "The agent hit an unexpected error. Please try again.")
        return True
