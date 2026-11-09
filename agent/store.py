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
    option TEXT NOT NULL DEFAULT '{}',  -- the approved option: which tool covers which need
    UNIQUE (project_id, idempotency_key));
CREATE TABLE IF NOT EXISTS setup_plans (
    approval_id TEXT PRIMARY KEY REFERENCES approvals(id), status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS setup_progress (
    approval_id TEXT NOT NULL REFERENCES approvals(id), step_key TEXT NOT NULL, done INTEGER NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (approval_id, step_key));
CREATE TABLE IF NOT EXISTS connection_cache (
    pair_key TEXT PRIMARY KEY, payload TEXT NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, project_id TEXT NOT NULL,
    kind TEXT NOT NULL, provider TEXT, model TEXT, task TEXT,
    input_tokens INTEGER DEFAULT 0, cached_input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    units INTEGER DEFAULT 0, est_usd REAL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovered (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, title TEXT, summary TEXT, evidence TEXT,
    created_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS discovered_tools (
    tool_id TEXT PRIMARY KEY, name TEXT NOT NULL, site TEXT NOT NULL, description TEXT,
    capabilities TEXT NOT NULL, needs TEXT NOT NULL, plans TEXT NOT NULL, evidence TEXT NOT NULL,
    pricing_source_url TEXT, status TEXT NOT NULL DEFAULT 'unvetted',
    first_seen TEXT NOT NULL, last_checked TEXT NOT NULL, times_seen INTEGER NOT NULL DEFAULT 1,
    audience TEXT, setup_complexity TEXT, best_for TEXT, mentions INTEGER DEFAULT 0, small_business_fit INTEGER,
    found_for TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS fit_cache (
    key TEXT PRIMARY KEY, fit TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_cache (
    run_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, key));
"""


DISCOVERED_COLUMNS = {"audience": "TEXT", "setup_complexity": "TEXT", "best_for": "TEXT",
                      "mentions": "INTEGER DEFAULT 0", "small_business_fit": "INTEGER",
                      "found_for": "TEXT NOT NULL DEFAULT '[]'"}  # capabilities of the needs a tool was found for


APPROVAL_COLUMNS = {"option": "TEXT NOT NULL DEFAULT '{}'"}


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
            # Databases created before these columns existed get them added in place.
            for table, columns in (("discovered_tools", DISCOVERED_COLUMNS), ("approvals", APPROVAL_COLUMNS)):
                have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                for column, kind in columns.items():
                    if column not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

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

    def start_followup_run(self, project_id, seed: dict):
        """Start a run that reuses the current parts (no new breakdown), e.g. a user-requested search
        for more tools. Supersedes an unapproved waiting plan; refused once a plan is approved."""
        run_id, ts = new_id("run"), now_iso()
        with self.tx() as con:
            last = con.execute("SELECT id, status FROM agent_runs WHERE project_id=? "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
            if not last:
                raise RunActive("No existing consultation run was found.")
            if last["status"] in ("queued", "running", "waiting_input"):
                raise RunActive("Please wait until the current search finishes.")
            approved = con.execute("SELECT 1 FROM approvals a JOIN plans p ON p.id = a.plan_id "
                                   "WHERE p.project_id=? ORDER BY p.version DESC LIMIT 1", (project_id,)).fetchone()
            if approved:
                raise RunActive("This plan is already approved.")
            if last["status"] == "waiting_approval":
                con.execute("UPDATE agent_runs SET status='superseded', updated_at=? WHERE id=?", (ts, last["id"]))
            con.execute("INSERT INTO agent_runs(id, project_id, status, step, snapshot, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?)", (run_id, project_id, "queued", "queued",
                                                   json.dumps({"seed": seed}), ts, ts))
        return run_id

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

    def approve(self, project_id, plan_version, option_id, max_build_budget, idempotency_key, authorized_actions,
                custom_option=None):
        """Record the approval exactly once. Returns (approval, created).

        custom_option is an option the server priced from the owner's own selections (option_id "custom")."""
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
            if custom_option is not None:
                options[custom_option["id"]] = custom_option
            if option_id not in options:
                raise UnknownOption("Choose one of the listed options.")
            option = options[option_id]
            # Unlisted prices don't block saving (the setup plan asks the owner to confirm them);
            # a need with no choice at all does.
            if any(a["source"] == "none" for a in option["assignments"]):
                raise UnknownOption("Choose a tool, “I already have this” or “Skip” for every need before saving.")
            recurring = {"monthly_total": option["monthly_total"], "total_is_complete": option["total_is_complete"],
                         "tools": [{"tool": a["tool_name"], "plan": a["plan_name"], "monthly_cost": a["monthly_cost"],
                                    "need": a.get("description", ""), "choice": a["source"]}
                                   for a in option["assignments"]], "assumptions": option["assumptions"]}
            approval_id = new_id("apr")
            con.execute("INSERT INTO approvals (id, project_id, plan_id, plan_version, option_id, recurring_cost, "
                        "max_build_budget, authorized_actions, idempotency_key, created_at, option) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (approval_id, project_id, plan["id"], plan_version, option_id, json.dumps(recurring),
                         max_build_budget, json.dumps(authorized_actions), idempotency_key, now_iso(),
                         json.dumps(option)))
            row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return self._approval(row), True

    def approval_for_project(self, project_id):
        """The project's most recent approval (only one plan can be approved per version)."""
        with self.connect() as con:
            row = con.execute("SELECT * FROM approvals WHERE project_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (project_id,)).fetchone()
        return self._approval(row)

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
        approval["option"] = json.loads(approval.get("option") or "{}")
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

    def save_discovered(self, candidate, need, capability=None):
        """Upsert a web-discovered tool so later runs can reuse it.

        Rediscovery refreshes plans, evidence and last_checked, and merges capabilities and
        the needs it was found for. Tools stay 'unvetted' until a person reviews them.
        """
        ts = now_iso()
        with self.tx() as con:
            row = con.execute("SELECT capabilities, needs, found_for FROM discovered_tools WHERE tool_id=?",
                              (candidate.tool_id,)).fetchone()
            found_for = list(dict.fromkeys([*(json.loads(row["found_for"] or "[]") if row else []),
                                            *([capability] if capability else [])]))
            caps = list(dict.fromkeys([*(json.loads(row["capabilities"]) if row else []), *candidate.capabilities]))
            needs = list(dict.fromkeys([*(json.loads(row["needs"]) if row else []), need]))[-10:]
            values = (candidate.name, candidate.site, candidate.description, json.dumps(caps), json.dumps(needs),
                      json.dumps([p.model_dump() for p in candidate.plans]),
                      json.dumps([e.model_dump() for e in candidate.evidence]), candidate.pricing_source_url, ts,
                      candidate.audience, candidate.setup_complexity, candidate.best_for, candidate.mentions, 1,
                      json.dumps(found_for))
            columns = ("name, site, description, capabilities, needs, plans, evidence, pricing_source_url, "
                       "last_checked, audience, setup_complexity, best_for, mentions, small_business_fit, found_for")
            if row:
                assignments = ", ".join(f"{c.strip()}=?" for c in columns.split(","))
                con.execute(f"UPDATE discovered_tools SET {assignments}, times_seen=times_seen+1 WHERE tool_id=?",
                            (*values, candidate.tool_id))
            else:
                con.execute(f"INSERT INTO discovered_tools({columns}, tool_id, first_seen) "
                            f"VALUES ({', '.join('?' * (len(values) + 2))})", (*values, candidate.tool_id, ts))

    def discovered_tools(self, max_age_days=30):
        """Discovered tools checked within max_age_days; older ones should be researched again."""
        cutoff = datetime.fromtimestamp(time.time() - max_age_days * 86400, timezone.utc).isoformat(timespec="seconds")
        with self.connect() as con:
            # Only tools that passed the small-business check are reused; older entries saved
            # before that check (small_business_fit NULL) are researched again instead.
            rows = con.execute("SELECT * FROM discovered_tools WHERE last_checked >= ? AND status != 'rejected' "
                               "AND small_business_fit = 1 ORDER BY times_seen DESC, last_checked DESC",
                               (cutoff,)).fetchall()
        tools = []
        for row in rows:
            tool = dict(row)
            for key in ("capabilities", "needs", "plans", "evidence", "found_for"):
                tool[key] = json.loads(tool[key])
            tools.append(tool)
        return tools

    def save_fit(self, key, fit, reason):
        with self.tx() as con:
            con.execute("INSERT OR REPLACE INTO fit_cache VALUES (?,?,?,?)", (key, fit, reason, now_iso()))

    def get_fit(self, key):
        with self.connect() as con:
            row = con.execute("SELECT fit, reason FROM fit_cache WHERE key=?", (key,)).fetchone()
        return (row["fit"], row["reason"]) if row else None

    # ---- setup plans (built once per approval), checklist progress, connection checks

    def setup_plan(self, approval_id):
        with self.connect() as con:
            row = con.execute("SELECT * FROM setup_plans WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            return None
        plan = dict(row)
        plan["payload"] = json.loads(plan["payload"])
        return plan

    def claim_setup_plan(self, approval_id):
        """Mark a setup plan as building; False if one is already built or being built (idempotent)."""
        ts = now_iso()
        with self.tx() as con:
            cur = con.execute("INSERT OR IGNORE INTO setup_plans (approval_id, status, created_at, updated_at) "
                              "VALUES (?,?,?,?)", (approval_id, "building", ts, ts))
            if cur.rowcount:
                return True
            row = con.execute("SELECT status FROM setup_plans WHERE approval_id=?", (approval_id,)).fetchone()
            if row["status"] == "failed":  # a failed attempt may be retried
                con.execute("UPDATE setup_plans SET status='building', error=NULL, updated_at=? WHERE approval_id=?",
                            (ts, approval_id))
                return True
            return False

    def finish_setup_plan(self, approval_id, payload=None, error=None):
        with self.tx() as con:
            con.execute("UPDATE setup_plans SET status=?, payload=?, error=?, updated_at=? WHERE approval_id=?",
                        ("failed" if error else "ready", json.dumps(payload or {}), error, now_iso(), approval_id))

    def set_progress(self, approval_id, step_key, done):
        with self.tx() as con:
            con.execute("INSERT OR REPLACE INTO setup_progress VALUES (?,?,?,?)",
                        (approval_id, step_key, 1 if done else 0, now_iso()))

    def progress(self, approval_id):
        with self.connect() as con:
            rows = con.execute("SELECT step_key FROM setup_progress WHERE approval_id=? AND done=1",
                               (approval_id,)).fetchall()
        return [r["step_key"] for r in rows]

    def get_connection(self, pair_key, max_age_days=30):
        cutoff = datetime.fromtimestamp(time.time() - max_age_days * 86400, timezone.utc).isoformat(timespec="seconds")
        with self.connect() as con:
            row = con.execute("SELECT payload FROM connection_cache WHERE pair_key=? AND checked_at >= ?",
                              (pair_key, cutoff)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_connection(self, pair_key, payload):
        with self.tx() as con:
            con.execute("INSERT OR REPLACE INTO connection_cache VALUES (?,?,?)", (pair_key, json.dumps(payload), now_iso()))

    def cache_get(self, run_id, kind, key):
        with self.connect() as con:
            row = con.execute("SELECT payload FROM research_cache WHERE run_id=? AND kind=? AND key=?",
                              (run_id, kind, key)).fetchone()
        return json.loads(row["payload"]) if row else None

    def cache_put(self, run_id, kind, key, payload):
        with self.tx() as con:
            con.execute("INSERT OR REPLACE INTO research_cache VALUES (?,?,?,?)", (run_id, kind, key, json.dumps(payload)))
