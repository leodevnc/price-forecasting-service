"""Transactional SQLite queue, snapshots and results for the reference deployment."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import time
from uuid import uuid4

from .contracts import ForecastInput, ForecastRequest, ForecastResult, RunView
from .registry import digest


class IdempotencyConflict(ValueError):
    pass


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
                    request TEXT NOT NULL, snapshot_id TEXT NOT NULL, package_digest TEXT NOT NULL,
                    status TEXT NOT NULL, error_code TEXT, result TEXT,
                    lease_until REAL, claim_token TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS runs_status ON runs(status, created_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(self, request: ForecastRequest, package_digest: str) -> RunView:
        body = request.model_dump_json()
        request_hash = digest(json.dumps(request.model_dump(mode="json"), sort_keys=True).encode())
        snapshot_body = request.inputs.model_dump_json()
        snapshot_id = digest(snapshot_body.encode())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM runs WHERE key=?", (request.idempotency_key,)
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise IdempotencyConflict(
                        "Idempotency key already used for a different request"
                    )
                run_id = existing["id"]
            else:
                run_id = str(uuid4())
                db.execute(
                    "INSERT OR IGNORE INTO snapshots VALUES (?,?)", (snapshot_id, snapshot_body)
                )
                db.execute(
                    """INSERT INTO runs
                    (id,key,request_hash,request,snapshot_id,package_digest,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        request.idempotency_key,
                        request_hash,
                        body,
                        snapshot_id,
                        package_digest,
                        "queued",
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return self.get(run_id)

    def get(self, run_id: str) -> RunView:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        request = ForecastRequest.model_validate_json(row["request"])
        return RunView(
            run_id=row["id"],
            status=row["status"],
            model_id=request.model_id,
            model_version=request.model_version,
            snapshot_id=row["snapshot_id"],
            error_code=row["error_code"],
            result=(ForecastResult.model_validate_json(row["result"]) if row["result"] else None),
        )

    def snapshot(self, snapshot_id: str) -> ForecastInput:
        with self.connect() as db:
            row = db.execute("SELECT body FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if row is None or digest(row["body"].encode()) != snapshot_id:
            raise ValueError("Snapshot missing or corrupted")
        return ForecastInput.model_validate_json(row["body"])

    def claim(self, lease_seconds: int = 300) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Explicit failure, rather than silently duplicating an abandoned execution.
            db.execute(
                """UPDATE runs SET status='failed',error_code='WORKER_LOST'
                          WHERE status='running' AND lease_until < ?""",
                (time(),),
            )
            row = db.execute(
                "SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            db.execute(
                "UPDATE runs SET status='running',lease_until=?,claim_token=? WHERE id=?",
                (time() + lease_seconds, token, row["id"]),
            )
            return {**dict(row), "claim_token": token}

    def finish(self, run_id: str, token: str, result: ForecastResult | None, error: str | None):
        with self.connect() as db:
            changed = db.execute(
                """UPDATE runs SET status=?,result=?,error_code=?
                WHERE id=? AND status='running' AND claim_token=? AND lease_until>=?""",
                (
                    "failed" if error else "succeeded",
                    result.model_dump_json() if result else None,
                    error,
                    run_id,
                    token,
                    time(),
                ),
            ).rowcount
        if changed != 1:
            raise RuntimeError("Execution lease expired or ownership was lost")

    def list_runs(self, limit: int = 50) -> list[RunView]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self.connect() as db:
            ids = db.execute(
                "SELECT id FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.get(row["id"]) for row in ids]

    def status_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
            ).fetchall()
        result = dict.fromkeys(("queued", "running", "succeeded", "failed"), 0)
        result.update({row["status"]: row["count"] for row in rows})
        return result
