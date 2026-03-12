"""Crash-safe conversation storage using SQLite.

Stores messages with atomic writes so conversation data survives game crashes.
Summaries are decoupled by timestamp range (from_ts, to_ts).
"""
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    npc_name TEXT NOT NULL,
    npc_ref_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    is_system_generated INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    world_id TEXT NOT NULL,
    npc_name TEXT NOT NULL,
    npc_ref_id TEXT NOT NULL,
    content TEXT NOT NULL,
    from_ts REAL NOT NULL,
    to_ts REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    world_id TEXT NOT NULL,
    npc_name TEXT NOT NULL,
    npc_ref_id TEXT NOT NULL,
    content TEXT NOT NULL,
    game_days_from REAL NOT NULL,
    game_days_to REAL NOT NULL,
    summaries_from_ts REAL NOT NULL,
    summaries_to_ts REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_npc ON messages(world_id, npc_name, npc_ref_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sum_npc ON summaries(world_id, npc_name, npc_ref_id, to_ts);
CREATE INDEX IF NOT EXISTS idx_diary_npc ON diary_entries(world_id, npc_name, npc_ref_id, game_days_to);
"""


class ConversationDB:
    """SQLite-backed crash-safe conversation storage."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the DB to create tables immediately
        _ = self.conn
        self._migrate_existing_files()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._migrate_schema()
        return self._conn

    # -- Conversations --

    def start_conversation(self, world_id: str) -> str:
        conv_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO conversations (id, world_id, started_at) VALUES (?, ?, ?)",
            (conv_id, world_id, time.time()),
        )
        self.conn.commit()
        return conv_id

    def end_conversation(self, conversation_id: str, game_days: float | None = None):
        self.conn.execute(
            "UPDATE conversations SET ended_at = ?, game_days = ? WHERE id = ?",
            (time.time(), game_days, conversation_id),
        )
        self.conn.commit()

    def get_last_conversation_game_days(self, world_id: str, npc_name: str, npc_ref_id: str) -> float | None:
        """Get game_days of the most recent ended conversation with this NPC."""
        cur = self.conn.execute(
            """SELECT c.game_days FROM conversations c
               JOIN messages m ON m.conversation_id = c.id
               WHERE m.world_id = ? AND m.npc_name = ? AND m.npc_ref_id = ?
                 AND c.game_days IS NOT NULL
               ORDER BY c.ended_at DESC LIMIT 1""",
            (world_id, npc_name, npc_ref_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    # -- Messages --

    def save_message(self, conversation_id: str, world_id: str, npc_name: str,
                     npc_ref_id: str, role: str, content: str,
                     is_system_generated: bool = False):
        self.conn.execute(
            """INSERT INTO messages
               (conversation_id, world_id, npc_name, npc_ref_id, role, content, is_system_generated, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, world_id, npc_name, npc_ref_id, role, content,
             1 if is_system_generated else 0, time.time()),
        )
        self.conn.commit()

    def mark_last_user_message_system_generated(self, conversation_id: str):
        """Mark the most recent user message in a conversation as system-generated."""
        self.conn.execute(
            """UPDATE messages SET is_system_generated = 1
               WHERE id = (SELECT id FROM messages WHERE conversation_id = ? AND role = 'user'
                           ORDER BY created_at DESC LIMIT 1)""",
            (conversation_id,),
        )
        self.conn.commit()

    def get_message_count(self, world_id: str, npc_name: str, npc_ref_id: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?",
            (world_id, npc_name, npc_ref_id),
        )
        return cur.fetchone()[0]

    def get_unsummarized_messages(self, world_id: str, npc_name: str, npc_ref_id: str) -> list[dict]:
        latest_to_ts = self.get_latest_summary_to_ts(world_id, npc_name, npc_ref_id)
        if latest_to_ts is not None:
            cur = self.conn.execute(
                """SELECT role, content, created_at, conversation_id FROM messages
                   WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?
                     AND created_at > ? AND is_system_generated = 0
                   ORDER BY created_at""",
                (world_id, npc_name, npc_ref_id, latest_to_ts),
            )
        else:
            cur = self.conn.execute(
                """SELECT role, content, created_at, conversation_id FROM messages
                   WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?
                     AND is_system_generated = 0
                   ORDER BY created_at""",
                (world_id, npc_name, npc_ref_id),
            )
        return [dict(row) for row in cur.fetchall()]

    # -- Orphan detection --

    def get_orphaned_conversation_ids(self, world_id: str, npc_name: str, npc_ref_id: str) -> list[str]:
        """Get conversation IDs that have messages but were never properly ended."""
        cur = self.conn.execute(
            """SELECT DISTINCT m.conversation_id FROM messages m
               JOIN conversations c ON m.conversation_id = c.id
               WHERE m.world_id = ? AND m.npc_name = ? AND m.npc_ref_id = ?
                 AND c.ended_at IS NULL""",
            (world_id, npc_name, npc_ref_id),
        )
        return [row[0] for row in cur.fetchall()]

    def mark_conversations_summarized(self, conversation_ids: list[str]):
        """Mark orphaned conversations as ended (sets ended_at) so they're no longer orphaned."""
        now = time.time()
        for conv_id in conversation_ids:
            self.conn.execute(
                "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (now, conv_id),
            )
        self.conn.commit()

    # -- Summaries --

    def save_summary(self, world_id: str, npc_name: str, npc_ref_id: str,
                     content: str, from_ts: float, to_ts: float):
        self.conn.execute(
            """INSERT INTO summaries (world_id, npc_name, npc_ref_id, content, from_ts, to_ts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (world_id, npc_name, npc_ref_id, content, from_ts, to_ts, time.time()),
        )
        self.conn.commit()

    def get_all_summaries(self, world_id: str, npc_name: str, npc_ref_id: str) -> list[dict]:
        cur = self.conn.execute(
            """SELECT content, from_ts, to_ts, created_at FROM summaries
               WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?
               ORDER BY to_ts""",
            (world_id, npc_name, npc_ref_id),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_latest_summary_to_ts(self, world_id: str, npc_name: str, npc_ref_id: str) -> float | None:
        cur = self.conn.execute(
            """SELECT MAX(to_ts) FROM summaries
               WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?""",
            (world_id, npc_name, npc_ref_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def replace_summaries(self, world_id: str, npc_name: str, npc_ref_id: str,
                          condensed_content: str):
        """Replace all summaries for an NPC with a single condensed summary."""
        # Get time range of existing summaries
        cur = self.conn.execute(
            """SELECT MIN(from_ts), MAX(to_ts) FROM summaries
               WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?""",
            (world_id, npc_name, npc_ref_id),
        )
        row = cur.fetchone()
        from_ts = row[0] if row and row[0] is not None else 0.0
        to_ts = row[1] if row and row[1] is not None else time.time()

        self.conn.execute(
            "DELETE FROM summaries WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?",
            (world_id, npc_name, npc_ref_id),
        )
        self.conn.execute(
            """INSERT INTO summaries (world_id, npc_name, npc_ref_id, content, from_ts, to_ts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (world_id, npc_name, npc_ref_id, condensed_content, from_ts, to_ts, time.time()),
        )
        self.conn.commit()

    # -- Diary entries --

    def save_diary_entry(self, world_id: str, npc_name: str, npc_ref_id: str,
                         content: str, game_days_from: float, game_days_to: float,
                         summaries_from_ts: float, summaries_to_ts: float):
        self.conn.execute(
            """INSERT INTO diary_entries
               (world_id, npc_name, npc_ref_id, content, game_days_from, game_days_to,
                summaries_from_ts, summaries_to_ts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (world_id, npc_name, npc_ref_id, content, game_days_from, game_days_to,
             summaries_from_ts, summaries_to_ts, time.time()),
        )
        self.conn.commit()

    def get_all_diary_entries(self, world_id: str, npc_name: str, npc_ref_id: str) -> list[dict]:
        cur = self.conn.execute(
            """SELECT content, game_days_from, game_days_to, summaries_from_ts, summaries_to_ts, created_at
               FROM diary_entries
               WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?
               ORDER BY game_days_to""",
            (world_id, npc_name, npc_ref_id),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_latest_diary_game_days(self, world_id: str, npc_name: str, npc_ref_id: str) -> float | None:
        cur = self.conn.execute(
            """SELECT MAX(game_days_to) FROM diary_entries
               WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ?""",
            (world_id, npc_name, npc_ref_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def delete_summaries_before_ts(self, world_id: str, npc_name: str, npc_ref_id: str, to_ts_threshold: float):
        """Delete summaries with to_ts <= threshold (already consolidated into diary)."""
        self.conn.execute(
            "DELETE FROM summaries WHERE world_id = ? AND npc_name = ? AND npc_ref_id = ? AND to_ts <= ?",
            (world_id, npc_name, npc_ref_id, to_ts_threshold),
        )
        self.conn.commit()

    # -- Migration --

    def _migrate_schema(self):
        """Add columns/tables that may not exist in older databases."""
        try:
            self._conn.execute("SELECT game_days FROM conversations LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.execute("ALTER TABLE conversations ADD COLUMN game_days REAL")
            self._conn.commit()
        # diary_entries table is created by _SCHEMA for new DBs,
        # but older DBs need it added via migration
        try:
            self._conn.execute("SELECT id FROM diary_entries LIMIT 1")
        except sqlite3.OperationalError:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS diary_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id TEXT NOT NULL,
                    npc_name TEXT NOT NULL,
                    npc_ref_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    game_days_from REAL NOT NULL,
                    game_days_to REAL NOT NULL,
                    summaries_from_ts REAL NOT NULL,
                    summaries_to_ts REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_diary_npc ON diary_entries(world_id, npc_name, npc_ref_id, game_days_to);
            """)
            self._conn.commit()

    def _migrate_existing_files(self):
        """Import existing summary text files into DB on first run."""
        # Only migrate if the summaries table is empty
        cur = self.conn.execute("SELECT COUNT(*) FROM summaries")
        if cur.fetchone()[0] > 0:
            return

        # DB sits at {conversations_folder}/conversations.db
        conversations_folder = self.db_path.parent
        if not conversations_folder.exists():
            return

        summary_pattern = re.compile(r'^(.+)_summary_(\d+)\.txt$')
        migrated_count = 0

        for world_dir in conversations_folder.iterdir():
            if not world_dir.is_dir():
                continue
            world_id = world_dir.name

            for npc_dir in world_dir.iterdir():
                if not npc_dir.is_dir():
                    continue

                # Parse folder name: "Name - ref_id" or just "Name"
                folder_name = npc_dir.name
                if " - " in folder_name:
                    npc_name, npc_ref_id = folder_name.rsplit(" - ", 1)
                else:
                    npc_name = folder_name
                    npc_ref_id = ""

                # Find summary files, sorted by number
                summary_files = []
                for f in npc_dir.iterdir():
                    match = summary_pattern.match(f.name)
                    if match:
                        file_num = int(match.group(2))
                        summary_files.append((file_num, f))

                summary_files.sort(key=lambda x: x[0])

                for i, (file_num, summary_file) in enumerate(summary_files):
                    try:
                        content = summary_file.read_text(encoding="utf-8").strip()
                        if content:
                            # Synthetic timestamps: space them 1 hour apart
                            from_ts = float(i * 3600)
                            to_ts = float((i + 1) * 3600)
                            self.save_summary(world_id, npc_name, npc_ref_id, content, from_ts, to_ts)
                            migrated_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to migrate {summary_file}: {e}")

        if migrated_count > 0:
            logger.info(f"Migrated {migrated_count} existing summary files to conversation DB")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
