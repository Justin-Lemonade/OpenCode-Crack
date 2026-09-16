"""Durable checkpoints for resumable agent tasks (C-023).

This module persists only orchestration state, not prompts, credentials, or
model output. A caller can checkpoint after each meaningful state transition
and reconstruct the last safe continuation point after a process restart.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskCheckpoint:
    task_id: str
    run_id: str
    status: str
    step: str
    payload: dict
    updated_at: str


class TaskCheckpointStore:
    """Small SQLite-backed checkpoint store with atomic upserts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, run_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, checkpoint: TaskCheckpoint) -> None:
        payload = json.dumps(checkpoint.payload, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO task_checkpoints
                    (task_id, run_id, status, step, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, run_id) DO UPDATE SET
                    status=excluded.status,
                    step=excluded.step,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (checkpoint.task_id, checkpoint.run_id, checkpoint.status,
                 checkpoint.step, payload, checkpoint.updated_at),
            )

    def load(self, task_id: str, run_id: str) -> TaskCheckpoint | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT task_id, run_id, status, step, payload_json, updated_at
                   FROM task_checkpoints WHERE task_id = ? AND run_id = ?""",
                (task_id, run_id),
            ).fetchone()
        if row is None:
            return None
        return TaskCheckpoint(
            task_id=row[0], run_id=row[1], status=row[2], step=row[3],
            payload=json.loads(row[4]), updated_at=row[5],
        )

    def delete(self, task_id: str, run_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM task_checkpoints WHERE task_id = ? AND run_id = ?",
                (task_id, run_id),
            )
