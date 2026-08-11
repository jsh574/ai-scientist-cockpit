"""问题理解模块的跨轮状态存储。

只保存本模块自己的轮次快照，不依赖总控修改。SQLite 让状态可跨进程重启，
并以 task/question/iteration 作为幂等键，重复执行同一轮时覆盖而不追加。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Protocol

from .schema import PriorRound


def _default_db_path() -> Path:
    configured = os.getenv("PROBLEM_UNDERSTANDING_STATE_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / ".runtime" / "iteration_state.sqlite3"


class RoundStateProvider(Protocol):
    def load_history(
        self,
        task_id: str,
        question_id: str,
        original_question: str,
        before_iteration: Optional[int] = None,
        limit: int = 5,
    ) -> list[PriorRound]: ...

    def latest_iteration(
        self, task_id: str, question_id: str, original_question: str
    ) -> Optional[int]: ...

    def save(
        self,
        task_id: str,
        question_id: str,
        original_question: str,
        snapshot: PriorRound,
    ) -> None: ...

    def clear(self, task_id: str, question_id: str, original_question: str) -> None: ...


class RoundStateStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()

    @staticmethod
    def question_fingerprint(original_question: str) -> str:
        normalized = " ".join(str(original_question or "").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def load_history(
        self,
        task_id: str,
        question_id: str,
        original_question: str,
        before_iteration: Optional[int] = None,
        limit: int = 5,
    ) -> list[PriorRound]:
        fingerprint = self.question_fingerprint(original_question)
        where_iteration = " AND iteration < ?" if before_iteration is not None else ""
        params: list[object] = [task_id, question_id, fingerprint]
        if before_iteration is not None:
            params.append(int(before_iteration))
        params.append(max(1, int(limit)))
        query = (
            "SELECT snapshot_json FROM round_snapshots "
            "WHERE task_id = ? AND question_id = ? AND question_fingerprint = ?"
            f"{where_iteration} ORDER BY iteration DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        snapshots: list[PriorRound] = []
        for row in rows:
            try:
                snapshots.append(PriorRound.model_validate_json(row[0]))
            except Exception:
                # 单条旧版/损坏快照不能阻断其他有效轮次恢复。
                continue
        snapshots.reverse()
        return snapshots

    def latest_iteration(
        self,
        task_id: str,
        question_id: str,
        original_question: str,
    ) -> Optional[int]:
        fingerprint = self.question_fingerprint(original_question)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(iteration) FROM round_snapshots "
                "WHERE task_id = ? AND question_id = ? AND question_fingerprint = ?",
                (task_id, question_id, fingerprint),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def save(
        self,
        task_id: str,
        question_id: str,
        original_question: str,
        snapshot: PriorRound,
    ) -> None:
        fingerprint = self.question_fingerprint(original_question)
        payload = snapshot.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO round_snapshots (
                    task_id, question_id, question_fingerprint, iteration,
                    snapshot_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id, question_id, question_fingerprint, iteration)
                DO UPDATE SET snapshot_json = excluded.snapshot_json,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (task_id, question_id, fingerprint, snapshot.iteration, payload),
            )
            conn.commit()

    def clear(self, task_id: str, question_id: str, original_question: str) -> None:
        fingerprint = self.question_fingerprint(original_question)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM round_snapshots "
                "WHERE task_id = ? AND question_id = ? AND question_fingerprint = ?",
                (task_id, question_id, fingerprint),
            )
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS round_snapshots (
                    task_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    question_fingerprint TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (task_id, question_id, question_fingerprint, iteration)
                )
                """
            )
            yield conn
        finally:
            conn.close()
