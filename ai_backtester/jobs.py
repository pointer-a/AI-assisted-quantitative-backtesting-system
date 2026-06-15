from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


JobRunner = Callable[[dict[str, Any], Callable[[dict[str, Any]], None]], dict[str, Any]]


class BacktestJobStore:
    def __init__(self, db_path: str | Path, runner: JobRunner) -> None:
        self.db_path = Path(db_path)
        self.runner = runner
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._mark_interrupted_jobs()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = time.time()
        progress = {"type": "progress", "phase": "已提交任务", "ratio": 0.0, "index": 0, "total": 0}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_jobs
                (id, status, payload_json, progress_json, result_json, error_json, created_at, updated_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, "queued", _json(payload), _json(progress), None, None, now, now, None, None),
            )
        self.start(job_id)
        return self.get(job_id) or {"id": job_id, "status": "queued", "progress": progress}

    def start(self, job_id: str) -> None:
        with self._lock:
            existing = self._threads.get(job_id)
            if existing and existing.is_alive():
                return
            thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
            self._threads[job_id] = thread
            thread.start()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM backtest_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def latest(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM backtest_jobs
                WHERE status IN ('queued', 'running', 'completed', 'failed', 'interrupted')
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return _row_to_job(row) if row else None

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE backtest_jobs SET progress_json = ?, updated_at = ? WHERE id = ?",
                (_json(progress), time.time(), job_id),
            )

    def _run_job(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return

        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE backtest_jobs SET status = ?, started_at = ?, updated_at = ? WHERE id = ?",
                ("running", now, now, job_id),
            )

        try:
            def on_progress(event: dict[str, Any]) -> None:
                self.update_progress(job_id, event)

            result = self.runner(job["payload"], on_progress)
            now = time.time()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE backtest_jobs
                    SET status = ?, result_json = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    ("completed", _json(result), now, now, job_id),
                )
        except Exception as exc:
            now = time.time()
            error = {"error": str(exc)}
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE backtest_jobs
                    SET status = ?, error_json = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    ("failed", _json(error), now, now, job_id),
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    progress_json TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL
                )
                """
            )

    def _mark_interrupted_jobs(self) -> None:
        now = time.time()
        error = {"error": "服务进程已重启，运行中的回测任务已中断，请重新提交。"}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE backtest_jobs
                SET status = ?, error_json = ?, updated_at = ?, completed_at = ?
                WHERE status IN ('queued', 'running')
                """,
                ("interrupted", _json(error), now, now),
            )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "payload": _loads(row["payload_json"], {}),
        "progress": _loads(row["progress_json"], None),
        "result": _loads(row["result_json"], None),
        "error": _loads(row["error_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
