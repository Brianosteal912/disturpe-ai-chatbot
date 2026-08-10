from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "before",
    "being",
    "could",
    "does",
    "from",
    "have",
    "here",
    "into",
    "just",
    "more",
    "most",
    "only",
    "other",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "through",
    "very",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "your",
}


@dataclass(frozen=True)
class MemoryMessage:
    id: int
    user_id: int
    session_id: str
    channel_id: int | None
    role: str
    content: str
    attachments: list[dict[str, str]]
    ts: float

    def as_history(self) -> dict[str, str]:
        role = "assistant" if self.role == "assistant" else "user"
        return {"role": role, "content": self.content}


class QuotaExceeded(RuntimeError):
    """Raised when a user has no remaining message quota."""


class MemoryStore:
    """SQLite-backed, single-file conversation memory."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self.fts_available = False
        self._initialize()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("PRAGMA temp_store = MEMORY")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    session_id  TEXT NOT NULL,
                    channel_id  INTEGER,
                    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content     TEXT NOT NULL,
                    attachments TEXT NOT NULL DEFAULT '[]',
                    ts          REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_recent
                    ON messages(user_id, session_id, ts DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_user
                    ON messages(user_id, ts DESC);

                CREATE TABLE IF NOT EXISTS user_usage (
                    user_id     INTEGER PRIMARY KEY,
                    used_count  INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0),
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS quota_state (
                    key    TEXT PRIMARY KEY,
                    value  TEXT NOT NULL
                );
                """
            )
            self._migrate_vectors(conn)
            try:
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                        content,
                        content='messages',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    );

                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES ('delete', old.id, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES ('delete', old.id, old.content);
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    """
                )
                conn.execute(
                    "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')"
                )
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False

    @staticmethod
    def _migrate_vectors(conn: sqlite3.Connection) -> None:
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors'"
        ).fetchone()
        if not legacy:
            return

        conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (id, user_id, session_id, channel_id, role, content, ts)
            SELECT id,
                   user_id,
                   'discord:channel:' || COALESCE(channel_id, 0),
                   channel_id,
                   CASE WHEN role = 'assistant' THEN 'assistant' ELSE 'user' END,
                   COALESCE(content, ''),
                   COALESCE(ts, 0)
              FROM vectors
             ORDER BY id
            """
        )
        conn.execute("DROP INDEX IF EXISTS idx_user_vec")
        conn.execute("DROP TABLE vectors")

    def add_exchange(
        self,
        *,
        user_id: int,
        session_id: str,
        channel_id: int | None,
        user_content: str,
        assistant_content: str,
        attachments: list[dict[str, str]] | None = None,
        ts: float | None = None,
        quota_limit: int | None = None,
    ) -> int | None:
        created_at = ts or time.time()
        attachment_json = json.dumps(attachments or [], ensure_ascii=False)
        with self._lock, self._connect() as conn:
            remaining: int | None = None
            if quota_limit is not None:
                used_row = conn.execute(
                    "SELECT used_count FROM user_usage WHERE user_id = ?", (user_id,)
                ).fetchone()
                used_count = int(used_row[0]) if used_row else 0
                if used_count >= quota_limit:
                    raise QuotaExceeded(
                        f"User {user_id} has exhausted their message quota."
                    )
                new_used_count = used_count + 1
                conn.execute(
                    """
                    INSERT INTO user_usage(user_id, used_count, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        used_count = excluded.used_count,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, new_used_count, created_at),
                )
                remaining = max(quota_limit - new_used_count, 0)

            conn.execute(
                """
                INSERT INTO messages
                    (user_id, session_id, channel_id, role, content, attachments, ts)
                VALUES (?, ?, ?, 'user', ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    channel_id,
                    user_content,
                    attachment_json,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO messages
                    (user_id, session_id, channel_id, role, content, attachments, ts)
                VALUES (?, ?, ?, 'assistant', ?, '[]', ?)
                """,
                (
                    user_id,
                    session_id,
                    channel_id,
                    assistant_content,
                    created_at + 0.001,
                ),
            )
        return remaining

    def remaining_quota(self, user_id: int, quota_limit: int) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT used_count FROM user_usage WHERE user_id = ?", (user_id,)
            ).fetchone()
        used_count = int(row[0]) if row else 0
        return max(quota_limit - used_count, 0)

    def ensure_daily_quota(self, current_date: str) -> tuple[bool, int]:
        """Clear counters after a date change while preserving first-run usage."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM quota_state WHERE key = 'last_reset_date'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO quota_state(key, value) VALUES ('last_reset_date', ?)",
                    (current_date,),
                )
                return False, 0
            if str(row[0]) == current_date:
                return False, 0

            affected = int(
                conn.execute("SELECT COUNT(*) FROM user_usage").fetchone()[0]
            )
            conn.execute("DELETE FROM user_usage")
            conn.execute(
                "UPDATE quota_state SET value = ? WHERE key = 'last_reset_date'",
                (current_date,),
            )
        return True, affected

    def reset_user_quota(self, user_id: int) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT used_count FROM user_usage WHERE user_id = ?", (user_id,)
            ).fetchone()
            used_count = int(row[0]) if row else 0
            conn.execute("DELETE FROM user_usage WHERE user_id = ?", (user_id,))
        return used_count

    def recent(
        self, user_id: int, session_id: str, *, limit: int = 20
    ) -> list[MemoryMessage]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT id, user_id, session_id, channel_id, role,
                           content, attachments, ts
                      FROM messages
                     WHERE user_id = ? AND session_id = ?
                     ORDER BY ts DESC, id DESC
                     LIMIT ?
                )
                ORDER BY ts, id
                """,
                (user_id, session_id, limit),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def relevant(
        self,
        user_id: int,
        query: str,
        *,
        exclude_ids: set[int] | None = None,
        limit: int = 6,
    ) -> list[MemoryMessage]:
        terms = self._search_terms(query)
        if not terms:
            return []
        excluded = exclude_ids or set()
        with self._lock, self._connect() as conn:
            if self.fts_available:
                rows = self._fts_search(conn, user_id, terms, limit * 3)
            else:
                rows = self._like_search(conn, user_id, terms, limit * 3)
        return [
            self._row_to_message(row) for row in rows if int(row["id"]) not in excluded
        ][:limit]

    @staticmethod
    def _fts_search(
        conn: sqlite3.Connection, user_id: int, terms: list[str], limit: int
    ) -> list[sqlite3.Row]:
        match_query = " OR ".join(f'"{term}"*' for term in terms)
        try:
            return conn.execute(
                """
                SELECT m.id, m.user_id, m.session_id, m.channel_id, m.role,
                       m.content, m.attachments, m.ts,
                       bm25(messages_fts) AS rank
                  FROM messages_fts
                  JOIN messages AS m ON m.id = messages_fts.rowid
                 WHERE messages_fts MATCH ? AND m.user_id = ?
                 ORDER BY rank, m.ts DESC
                 LIMIT ?
                """,
                (match_query, user_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _like_search(
        conn: sqlite3.Connection, user_id: int, terms: list[str], limit: int
    ) -> list[sqlite3.Row]:
        patterns: list[str | None] = [f"%{term}%" for term in terms[:10]]
        patterns.extend([None] * (10 - len(patterns)))
        params: list[object] = [user_id, *patterns, limit]
        return conn.execute(
            """
            SELECT id, user_id, session_id, channel_id, role,
                   content, attachments, ts
              FROM messages
             WHERE user_id = ? AND (
                    LOWER(content) LIKE ? OR LOWER(content) LIKE ? OR
                    LOWER(content) LIKE ? OR LOWER(content) LIKE ? OR
                    LOWER(content) LIKE ? OR LOWER(content) LIKE ? OR
                    LOWER(content) LIKE ? OR LOWER(content) LIKE ? OR
                    LOWER(content) LIKE ? OR LOWER(content) LIKE ?
             )
             ORDER BY ts DESC
             LIMIT ?
            """,
            params,
        ).fetchall()

    @staticmethod
    def _search_terms(query: str) -> list[str]:
        result: list[str] = []
        for raw in _WORD_RE.findall(query.casefold()):
            if len(raw) < 4 or raw in _STOP_WORDS:
                continue
            term = raw[: max(4, min(len(raw), 7))]
            if term not in result:
                result.append(term)
        return result[:10]

    def export_user(self, user_id: int) -> bytes:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, session_id, channel_id, role,
                       content, attachments, ts
                  FROM messages
                 WHERE user_id = ?
                 ORDER BY ts, id
                """,
                (user_id,),
            ).fetchall()
        output = []
        for row in rows:
            message = self._row_to_message(row)
            item = asdict(message)
            item["datetime"] = datetime.fromtimestamp(message.ts, UTC).isoformat()
            output.append(item)
        return json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")

    def clear_user(self, user_id: int) -> int:
        with self._lock, self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        self._truncate_wal()
        return int(count)

    def purge_older_than(self, cutoff_ts: float) -> int:
        with self._lock, self._connect() as conn:
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE ts < ?", (cutoff_ts,)
                ).fetchone()[0]
            )
            if count:
                conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff_ts,))
        if count:
            self._truncate_wal()
        return count

    def _truncate_wal(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def analysis_text(self, *, limit: int = 400, max_chars: int = 24_000) -> str:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, role, content, ts
                  FROM messages
                 ORDER BY ts DESC, id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        lines = [
            f"user_id:{row['user_id']} {row['role']}: {row['content']}"
            for row in reversed(rows)
        ]
        return "\n".join(lines)[-max_chars:]

    def stats(self) -> tuple[int, int]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM messages"
            ).fetchone()
        return int(row[0]), int(row[1])

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MemoryMessage:
        try:
            attachments = json.loads(row["attachments"] or "[]")
        except (json.JSONDecodeError, TypeError):
            attachments = []
        return MemoryMessage(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            session_id=str(row["session_id"]),
            channel_id=row["channel_id"],
            role=str(row["role"]),
            content=str(row["content"]),
            attachments=attachments,
            ts=float(row["ts"]),
        )


def format_relevant_memories(messages: list[MemoryMessage]) -> str:
    if not messages:
        return ""
    lines = []
    for message in sorted(messages, key=lambda item: (item.ts, item.id)):
        date = datetime.fromtimestamp(message.ts, UTC).strftime("%d.%m.%Y")
        speaker = "Disturpe AI Chatbot" if message.role == "assistant" else "User"
        content = " ".join(message.content.split())[:500]
        lines.append(f"- [{date}] {speaker}: {content}")
    return "\n".join(lines)
