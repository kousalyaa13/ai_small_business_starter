"""SQLite persistence for projects, runs, plans, approvals and usage.

Every write that can be repeated by graph replay or a double-click is keyed so a
retry returns the existing row instead of creating a second one.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

ACTIVE_STATUSES = ("queued", "running", "waiting_input", "waiting_approval")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL DEFAULT 'default',
    business_name TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requirement_versions (
    project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
    intake TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, version));
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    status TEXT NOT NULL, step TEXT, snapshot TEXT NOT NULL DEFAULT '{}', error TEXT,
    lease_owner TEXT, lease_until REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_project ON agent_runs(project_id)
    WHERE status IN ('queued','running','waiting_input','waiting_approval');
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    run_id TEXT NOT NULL UNIQUE, version INTEGER NOT NULL, requirement_version INTEGER NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (project_id, version));
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    plan_id TEXT NOT NULL UNIQUE REFERENCES plans(id), plan_version INTEGER NOT NULL,
    option_id TEXT NOT NULL, recurring_cost TEXT NOT NULL, max_build_budget REAL NOT NULL,
    authorized_actions TEXT NOT NULL, idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (project_id, idempotency_key));
CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, project_id TEXT NOT NULL,
    kind TEXT NOT NULL, provider TEXT, model TEXT, task TEXT,
    input_tokens INTEGER DEFAULT 0, cached_input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    units INTEGER DEFAULT 0, est_usd REAL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovered (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT, summary TEXT, evidence TEXT,
    created_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS research_cache (
    run_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, key));
"""


class RunActive(Exception):
    pass


class StalePlan(Exception):
    pass


class AlreadyApproved(Exception):
    def __init__(self, approval):
        super().__init__("This plan was already approved.")
        self.approval = approval


class UnknownOption(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(16)}"


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def tx(self):
        """Serialised write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    # ---- projects & requirements

    def create_project(self, intake: dict, org_id="default"):
        project_id, run_id, ts = new_id("prj"), new_id("run"), now_iso()
        with self.tx() as con:
            con.execute("INSERT INTO projects(id, org_id, business_name, created_at) VALUES (?,?,?,?)",
                        (project_id, org_id, intake.get("business_name", ""), ts))
            con.execute("INSERT INTO requirement_versions VALUES (?,?,?,?)", (project_id, 1, json.dumps(intake), ts))
            con.execute("INSERT INTO agent_runs(id, project_id, status, step, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (run_id, project_id, "queued", "queued", ts, ts))
        return project_id, run_id

    def get_project(self, project_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return dict(row) if row else None

    def add_requirement_version(self, project_id, intake: dict, based_on: int):
        """Create version based_on+1; replaying the same step returns the existing version."""
        version = based_on + 1
        with self.tx() as con:
            con.execute("INSERT OR IGNORE INTO requirement_versions VALUES (?,?,?,?)",
                        (project_id, version, json.dumps(intake), now_iso()))
        return version

    def latest_requirements(self, project_id):
        with self.connect() as con:
            row = con.execute("SELECT version, intake FROM requirement_versions WHERE project_id=? "
                              "ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        return (row["version"], json.loads(row["intake"])) if row else (None, None)

    # ---- runs (one active run per project; one worker drives a run at a time)

    def create_run(self, project_id):
        run_id, ts = new_id("run"), now_iso()
        try:
            with self.tx() as con:
                con.execute("INSERT INTO agent_runs(id, project_id, status, step, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                            (run_id, project_id, "queued", "queued", ts, ts))
        except sqlite3.IntegrityError:
            raise RunActive("This project already has an active run.")
        return run_id

    def revise_project(self, project_id, intake: dict):
        """Atomically supersede an unapproved plan and start a new versioned run."""
        run_id, ts = new_id("run"), now_iso()
        with self.tx() as con:
            last = con.execute(
                "SELECT id,status FROM agent_runs WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (project_id,)).fetchone()
            if not last:
                raise RunActive("No existing consultation run was found.")
            if last["status"] in ("queued", "running", "waiting_input"):
                raise RunActive("Please wait until the current consultation finishes before changing assumptions.")
            if last["status"] == "waiting_approval":
                con.execute("UPDATE agent_runs SET status='superseded',updated_at=? WHERE id=?",
                            (ts, last["id"]))
            version = con.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM requirement_versions WHERE project_id=?",
                (project_id,)).fetchone()[0]
            con.execute("INSERT INTO requirement_versions VALUES (?,?,?,?)",
                        (project_id, version, json.dumps(intake), ts))
            con.execute("INSERT INTO agent_runs(id,project_id,status,step,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?)", (run_id, project_id, "queued", "queued", ts, ts))
        return version, run_id

    def get_run(self, run_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row)

    def latest_run(self, project_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM agent_runs WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (project_id,)).fetchone()
        return self._run(row)

    @staticmethod
    def _run(row):
        if not row:
            return None
        run = dict(row)
        run["snapshot"] = json.loads(run["snapshot"] or "{}")
        return run

    def acquire_lease(self, run_id, owner, ttl, from_statuses):
        """Atomically claim a run. Expired 'running' leases (crashed workers) can be reclaimed."""
        placeholders = ",".join("?" for _ in from_statuses)
        with self.tx() as con:
            cur = con.execute(
                f"UPDATE agent_runs SET status='running', lease_owner=?, lease_until=?, updated_at=? "
                f"WHERE id=? AND (status IN ({placeholders}) OR (status='running' AND lease_until < ?))",
                (owner, time.time() + ttl, now_iso(), run_id, *from_statuses, time.time()))
        return cur.rowcount == 1

    def extend_lease(self, run_id, owner, ttl):
        with self.tx() as con:
            con.execute("UPDATE agent_runs SET lease_until=? WHERE id=? AND lease_owner=?",
                        (time.time() + ttl, run_id, owner))

    def release(self, run_id, owner, status, error=None):
        with self.tx() as con:
            con.execute("UPDATE agent_runs SET status=?, error=?, lease_owner=NULL, lease_until=NULL, updated_at=? "
                        "WHERE id=? AND lease_owner=?", (status, error, now_iso(), run_id, owner))

    def update_snapshot(self, run_id, step=None, **fields):
        with self.tx() as con:
            row = con.execute("SELECT snapshot FROM agent_runs WHERE id=?", (run_id,)).fetchone()
            snap = json.loads(row["snapshot"] or "{}")
            snap.update(fields)
            con.execute("UPDATE agent_runs SET snapshot=?, step=COALESCE(?, step), updated_at=? WHERE id=?",
                        (json.dumps(snap), step, now_iso(), run_id))

    def recoverable_runs(self, queued_grace=30):
        """Runs whose worker vanished: expired 'running' leases, or 'queued' runs nobody picked up."""
        cutoff = datetime.fromtimestamp(time.time() - queued_grace, timezone.utc).isoformat(timespec="seconds")
        with self.connect() as con:
            rows = con.execute("SELECT id FROM agent_runs WHERE (status='running' AND lease_until < ?) "
                               "OR (status='queued' AND updated_at < ?)", (time.time(), cutoff)).fetchall()
        return [r["id"] for r in rows]

    # ---- plans & approvals

    def persist_plan(self, project_id, run_id, requirement_version, build_payload):
        """One plan per run. Replays return the stored plan unchanged."""
        with self.tx() as con:
            row = con.execute("SELECT * FROM plans WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                version = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM plans WHERE project_id=?",
                                      (project_id,)).fetchone()[0]
                payload = build_payload(version)
                con.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?)",
                            (new_id("pln"), project_id, run_id, version, requirement_version,
                             json.dumps(payload), now_iso()))
                row = con.execute("SELECT * FROM plans WHERE run_id=?", (run_id,)).fetchone()
        return self._plan(row)

    def latest_plan(self, project_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM plans WHERE project_id=? ORDER BY version DESC LIMIT 1",
                              (project_id,)).fetchone()
        return self._plan(row)

    @staticmethod
    def _plan(row):
        if not row:
            return None
        plan = dict(row)
        plan["payload"] = json.loads(plan["payload"])
        return plan

    def approve(self, project_id, plan_version, option_id, max_build_budget, idempotency_key, authorized_actions):
        """Record the approval exactly once. Returns (approval, created)."""
        with self.tx() as con:
            same_key = con.execute("SELECT * FROM approvals WHERE project_id=? AND idempotency_key=?",
                                   (project_id, idempotency_key)).fetchone()
            if same_key:
                return self._approval(same_key), False
            plan = con.execute("SELECT * FROM plans WHERE project_id=? ORDER BY version DESC LIMIT 1",
                               (project_id,)).fetchone()
            if plan is None or plan["version"] != plan_version:
                raise StalePlan("That plan version is no longer current. Reload to see the latest options.")
            latest_requirements = con.execute(
                "SELECT MAX(version) FROM requirement_versions WHERE project_id=?",
                (project_id,)).fetchone()[0]
            if plan["requirement_version"] != latest_requirements:
                raise StalePlan("Requirements changed. Wait for the updated recommendation before approving.")
            existing = con.execute("SELECT * FROM approvals WHERE plan_id=?", (plan["id"],)).fetchone()
            if existing:
                existing = self._approval(existing)
                if existing["option_id"] == option_id:
                    return existing, False
                raise AlreadyApproved(existing)
            options = {o["id"]: o for o in json.loads(plan["payload"])["options"]}
            if option_id not in options:
                raise UnknownOption("Choose one of the listed options.")
            option = options[option_id]
            if (not option["total_is_complete"]
                    or any(a["source"] == "none" for a in option["assignments"])):
                raise UnknownOption("This option has unresolved tools or prices. Refine your request before approving.")
            recurring = {"monthly_total": option["monthly_total"], "total_is_complete": option["total_is_complete"],
                         "tools": [{"tool": a["tool_name"], "plan": a["plan_name"], "monthly_cost": a["monthly_cost"]}
                                   for a in option["assignments"]], "assumptions": option["assumptions"]}
            approval_id = new_id("apr")
            con.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (approval_id, project_id, plan["id"], plan_version, option_id, json.dumps(recurring),
                         max_build_budget, json.dumps(authorized_actions), idempotency_key, now_iso()))
            row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return self._approval(row), True

    def approval_for_plan(self, plan_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM approvals WHERE plan_id=?", (plan_id,)).fetchone()
        return self._approval(row)

    def count_approvals(self, project_id):
        with self.connect() as con:
            return con.execute("SELECT COUNT(*) FROM approvals WHERE project_id=?", (project_id,)).fetchone()[0]

    @staticmethod
    def _approval(row):
        if not row:
            return None
        approval = dict(row)
        approval["recurring_cost"] = json.loads(approval["recurring_cost"])
        approval["authorized_actions"] = json.loads(approval["authorized_actions"])
        return approval

    # ---- usage & research cache

    def record_usage(self, run_id, project_id, kind, units=1, est_usd=None, provider=None, model=None, task=None,
                     input_tokens=0, cached_input_tokens=0, output_tokens=0):
        with self.tx() as con:
            con.execute("INSERT INTO usage_records(run_id, project_id, kind, provider, model, task, input_tokens, "
                        "cached_input_tokens, output_tokens, units, est_usd, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, project_id, kind, provider, model, task, input_tokens, cached_input_tokens,
                         output_tokens, units, est_usd, now_iso()))

    def usage_totals(self, run_id):
        with self.connect() as con:
            rows = con.execute("SELECT kind, COUNT(*) calls, SUM(units) units, SUM(input_tokens) input_tokens, "
                               "SUM(cached_input_tokens) cached_input_tokens, SUM(output_tokens) output_tokens, "
                               "SUM(est_usd) est_usd, SUM(est_usd IS NULL) unpriced FROM usage_records "
                               "WHERE run_id=? GROUP BY kind", (run_id,)).fetchall()
        return {r["kind"]: dict(r) for r in rows}

    def save_discovered(self, candidate):
        """Web finds go to the review queue; they are never auto-promoted to the vetted catalog."""
        with self.tx() as con:
            if con.execute("SELECT 1 FROM discovered WHERE url=?", (candidate.site,)).fetchone():
                return
            con.execute("INSERT INTO discovered(url, title, summary, evidence, created_at, status) VALUES (?,?,?,?,?,?)",
                        (candidate.site, candidate.name, candidate.description,
                         json.dumps([e.model_dump() for e in candidate.evidence]), now_iso(), "Needs review"))

    def cache_get(self, run_id, kind, key):
        with self.connect() as con:
            row = con.execute("SELECT payload FROM research_cache WHERE run_id=? AND kind=? AND key=?",
                              (run_id, kind, key)).fetchone()
        return json.loads(row["payload"]) if row else None

    def cache_put(self, run_id, kind, key, payload):
        with self.tx() as con:
            con.execute("INSERT OR REPLACE INTO research_cache VALUES (?,?,?,?)", (run_id, kind, key, json.dumps(payload)))
