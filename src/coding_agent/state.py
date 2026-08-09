from __future__ import annotations

import pathlib
import sqlite3
import threading
from datetime import datetime, timezone


class State:
    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, repository TEXT NOT NULL DEFAULT '',
                issue_number INTEGER NOT NULL, branch TEXT,
                status TEXT NOT NULL, detail TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)")}
        if "repository" not in columns:
            self.connection.execute(
                "ALTER TABLE runs ADD COLUMN repository TEXT NOT NULL DEFAULT ''"
            )
        self.connection.commit()

    def start(self, repository: str, issue_number: int, branch: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            cursor = self.connection.execute(
                "INSERT INTO runs(repository, issue_number, branch, status, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (repository, issue_number, branch, "running", now, now),
            )
            self.connection.commit()
        return int(cursor.lastrowid)

    def finish(self, run_id: int, status: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.connection.execute(
                "UPDATE runs SET status=?, detail=?, updated_at=? WHERE id=?",
                (status, detail[-20000:], now, run_id),
            )
            self.connection.commit()

    def pending_prs(self, repository: str) -> list[tuple[int, int, str]]:
        with self.lock:
            return [
                (int(run_id), int(issue_number), str(detail or ""))
                for run_id, issue_number, detail in self.connection.execute(
                    "SELECT id, issue_number, detail FROM runs "
                    "WHERE repository=? AND status='pr-open' ORDER BY id",
                    (repository,),
                )
            ]

    def recent(self, limit: int = 20) -> list[tuple[object, ...]]:
        with self.lock:
            return list(self.connection.execute(
                "SELECT repository, issue_number, branch, status, updated_at, detail "
                "FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ))
