import pytest
import time
from pathlib import Path
from src.conversation.conversation_db import ConversationDB


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    db = ConversationDB(tmp_path / "test.db")
    yield db
    db.close()


class TestTableCreation:
    def test_creates_tables_on_init(self, db: ConversationDB):
        cur = db.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cur.fetchall()}
        assert "conversations" in tables
        assert "messages" in tables
        assert "summaries" in tables

    def test_wal_mode_enabled(self, db: ConversationDB):
        cur = db.conn.cursor()
        cur.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0] == "wal"


class TestConversations:
    def test_start_conversation_returns_uuid(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        assert conv_id is not None
        assert len(conv_id) == 36  # UUID format

    def test_start_conversation_inserts_row(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        cur = db.conn.cursor()
        cur.execute("SELECT id, world_id, started_at, ended_at FROM conversations WHERE id = ?", (conv_id,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == conv_id
        assert row[1] == "world1"
        assert row[2] > 0  # started_at timestamp
        assert row[3] is None  # ended_at not set yet

    def test_end_conversation_sets_ended_at(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.end_conversation(conv_id)
        cur = db.conn.cursor()
        cur.execute("SELECT ended_at FROM conversations WHERE id = ?", (conv_id,))
        row = cur.fetchone()
        assert row[0] is not None
        assert row[0] > 0


class TestMessages:
    def test_save_and_count_messages(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello there")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "assistant", "Hi, how can I help?")
        assert db.get_message_count("world1", "Preston", "00ABC") == 2

    def test_message_count_filters_by_npc(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.save_message(conv_id, "world1", "Piper", "00DEF", "user", "Hey")
        assert db.get_message_count("world1", "Preston", "00ABC") == 1
        assert db.get_message_count("world1", "Piper", "00DEF") == 1

    def test_save_message_with_system_generated_flag(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Goodbye", is_system_generated=True)
        cur = db.conn.cursor()
        cur.execute("SELECT is_system_generated FROM messages WHERE conversation_id = ?", (conv_id,))
        assert cur.fetchone()[0] == 1


class TestUnsummarizedMessages:
    def test_get_unsummarized_messages_returns_all_when_no_summaries(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "assistant", "Hi there")
        msgs = db.get_unsummarized_messages("world1", "Preston", "00ABC")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_get_unsummarized_messages_excludes_already_summarized(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        # Summary covers everything up to now + buffer
        summary_to_ts = time.time() + 1
        db.save_summary("world1", "Preston", "00ABC", "They greeted each other.", 0.0, summary_to_ts)
        # Small delay so the next message's created_at is after summary_to_ts
        time.sleep(1.1)
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "How are you?")
        msgs = db.get_unsummarized_messages("world1", "Preston", "00ABC")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "How are you?"

    def test_get_unsummarized_excludes_system_generated(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Goodbye", is_system_generated=True)
        msgs = db.get_unsummarized_messages("world1", "Preston", "00ABC")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Hello"


class TestOrphanedConversations:
    def test_get_orphaned_conversation_ids(self, db: ConversationDB):
        # Create a conversation that was never ended (simulates crash)
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "assistant", "Hi")
        # Don't call end_conversation — simulating a crash

        orphans = db.get_orphaned_conversation_ids("world1", "Preston", "00ABC")
        assert conv_id in orphans

    def test_ended_conversation_not_orphaned(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.end_conversation(conv_id)

        orphans = db.get_orphaned_conversation_ids("world1", "Preston", "00ABC")
        assert conv_id not in orphans

    def test_mark_conversations_summarized(self, db: ConversationDB):
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        # It's orphaned (not ended)
        assert conv_id in db.get_orphaned_conversation_ids("world1", "Preston", "00ABC")

        # Mark as summarized (sets ended_at)
        db.mark_conversations_summarized([conv_id])
        assert conv_id not in db.get_orphaned_conversation_ids("world1", "Preston", "00ABC")


class TestSummaries:
    def test_save_and_get_summaries(self, db: ConversationDB):
        db.save_summary("world1", "Preston", "00ABC", "They had a chat.", 100.0, 200.0)
        summaries = db.get_all_summaries("world1", "Preston", "00ABC")
        assert len(summaries) == 1
        assert summaries[0]["content"] == "They had a chat."
        assert summaries[0]["from_ts"] == 100.0
        assert summaries[0]["to_ts"] == 200.0

    def test_get_latest_summary_to_ts(self, db: ConversationDB):
        db.save_summary("world1", "Preston", "00ABC", "First chat.", 100.0, 200.0)
        db.save_summary("world1", "Preston", "00ABC", "Second chat.", 200.0, 300.0)
        latest = db.get_latest_summary_to_ts("world1", "Preston", "00ABC")
        assert latest == 300.0

    def test_get_latest_summary_to_ts_returns_none_when_empty(self, db: ConversationDB):
        latest = db.get_latest_summary_to_ts("world1", "Preston", "00ABC")
        assert latest is None

    def test_replace_summaries(self, db: ConversationDB):
        db.save_summary("world1", "Preston", "00ABC", "First.", 100.0, 200.0)
        db.save_summary("world1", "Preston", "00ABC", "Second.", 200.0, 300.0)
        db.replace_summaries("world1", "Preston", "00ABC", "Condensed summary of all chats.")
        summaries = db.get_all_summaries("world1", "Preston", "00ABC")
        assert len(summaries) == 1
        assert summaries[0]["content"] == "Condensed summary of all chats."
        assert summaries[0]["from_ts"] == 100.0
        assert summaries[0]["to_ts"] == 300.0

    def test_summaries_scoped_by_npc(self, db: ConversationDB):
        db.save_summary("world1", "Preston", "00ABC", "Preston chat.", 100.0, 200.0)
        db.save_summary("world1", "Piper", "00DEF", "Piper chat.", 100.0, 200.0)
        assert len(db.get_all_summaries("world1", "Preston", "00ABC")) == 1
        assert len(db.get_all_summaries("world1", "Piper", "00DEF")) == 1


class TestMigration:
    def test_migrate_existing_summary_files(self, tmp_path: Path):
        """Test that existing summary text files are imported on first DB creation."""
        # Set up file structure: conversations/world1/Preston - 00ABC/Preston_summary_1.txt
        conv_folder = tmp_path / "conversations"
        npc_folder = conv_folder / "world1" / "Preston - 00ABC"
        npc_folder.mkdir(parents=True)
        (npc_folder / "Preston_summary_1.txt").write_text("They met at the settlement.\n\nThey discussed defense plans.\n\n")

        db_path = conv_folder / "conversations.db"
        db = ConversationDB(db_path)
        try:
            summaries = db.get_all_summaries("world1", "Preston", "00ABC")
            assert len(summaries) == 1
            assert "They met at the settlement." in summaries[0]["content"]
            assert "They discussed defense plans." in summaries[0]["content"]
        finally:
            db.close()

    def test_migrate_multiple_summary_files(self, tmp_path: Path):
        """Test migration with multiple summary files (re-summarized)."""
        conv_folder = tmp_path / "conversations"
        npc_folder = conv_folder / "world1" / "Piper - 00DEF"
        npc_folder.mkdir(parents=True)
        (npc_folder / "Piper_summary_1.txt").write_text("Old summary content.\n\n")
        (npc_folder / "Piper_summary_2.txt").write_text("Newer summary content.\n\n")

        db_path = conv_folder / "conversations.db"
        db = ConversationDB(db_path)
        try:
            summaries = db.get_all_summaries("world1", "Piper", "00DEF")
            # Should have 2 summaries, one per file
            assert len(summaries) == 2
        finally:
            db.close()

    def test_migrate_legacy_name_only_folders(self, tmp_path: Path):
        """Test migration of legacy folders that use just the NPC name (no ref_id)."""
        conv_folder = tmp_path / "conversations"
        npc_folder = conv_folder / "world1" / "Preston"
        npc_folder.mkdir(parents=True)
        (npc_folder / "Preston_summary_1.txt").write_text("Legacy summary.\n\n")

        db_path = conv_folder / "conversations.db"
        db = ConversationDB(db_path)
        try:
            # Legacy folders have no ref_id, so we store with empty ref_id
            summaries = db.get_all_summaries("world1", "Preston", "")
            assert len(summaries) == 1
            assert "Legacy summary." in summaries[0]["content"]
        finally:
            db.close()

    def test_no_migration_if_db_already_has_data(self, tmp_path: Path):
        """Ensure migration doesn't run twice."""
        conv_folder = tmp_path / "conversations"
        npc_folder = conv_folder / "world1" / "Preston - 00ABC"
        npc_folder.mkdir(parents=True)
        (npc_folder / "Preston_summary_1.txt").write_text("Summary text.\n\n")

        db_path = conv_folder / "conversations.db"
        # First open: migrates
        db1 = ConversationDB(db_path)
        assert len(db1.get_all_summaries("world1", "Preston", "00ABC")) == 1
        db1.close()

        # Add another file and reopen — should NOT migrate again
        (npc_folder / "Preston_summary_2.txt").write_text("Should not be imported.\n\n")
        db2 = ConversationDB(db_path)
        assert len(db2.get_all_summaries("world1", "Preston", "00ABC")) == 1
        db2.close()


class TestClose:
    def test_close_and_reopen(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        db = ConversationDB(db_path)
        conv_id = db.start_conversation("world1")
        db.save_message(conv_id, "world1", "Preston", "00ABC", "user", "Hello")
        db.close()

        db2 = ConversationDB(db_path)
        assert db2.get_message_count("world1", "Preston", "00ABC") == 1
        db2.close()
