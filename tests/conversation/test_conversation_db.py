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
        assert "characters" in tables
        assert "faction_rumors" in tables

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


class TestDiaryEntries:
    def test_creates_diary_entries_table(self, db: ConversationDB):
        cur = db.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cur.fetchall()}
        assert "diary_entries" in tables

    def test_save_and_get_diary_entries(self, db: ConversationDB):
        db.save_diary_entry("world1", "Preston", "00ABC", "Dear diary, the player helped me today.",
                           game_days_from=35.0, game_days_to=42.0, summaries_from_ts=100.0, summaries_to_ts=500.0)
        entries = db.get_all_diary_entries("world1", "Preston", "00ABC")
        assert len(entries) == 1
        assert entries[0]["content"] == "Dear diary, the player helped me today."
        assert entries[0]["game_days_from"] == 35.0
        assert entries[0]["game_days_to"] == 42.0

    def test_get_latest_diary_game_days(self, db: ConversationDB):
        db.save_diary_entry("world1", "Preston", "00ABC", "First week.",
                           game_days_from=1.0, game_days_to=7.0, summaries_from_ts=100.0, summaries_to_ts=200.0)
        db.save_diary_entry("world1", "Preston", "00ABC", "Second week.",
                           game_days_from=7.0, game_days_to=14.0, summaries_from_ts=200.0, summaries_to_ts=300.0)
        latest = db.get_latest_diary_game_days("world1", "Preston", "00ABC")
        assert latest == 14.0

    def test_get_latest_diary_game_days_returns_none_when_empty(self, db: ConversationDB):
        latest = db.get_latest_diary_game_days("world1", "Preston", "00ABC")
        assert latest is None

    def test_delete_summaries_before_ts(self, db: ConversationDB):
        db.save_summary("world1", "Preston", "00ABC", "Old summary.", 100.0, 200.0)
        db.save_summary("world1", "Preston", "00ABC", "Recent summary.", 200.0, 300.0)
        db.delete_summaries_before_ts("world1", "Preston", "00ABC", 250.0)
        remaining = db.get_all_summaries("world1", "Preston", "00ABC")
        assert len(remaining) == 1
        assert remaining[0]["content"] == "Recent summary."

    def test_diary_entries_scoped_by_npc(self, db: ConversationDB):
        db.save_diary_entry("world1", "Preston", "00ABC", "Preston diary.",
                           game_days_from=1.0, game_days_to=7.0, summaries_from_ts=100.0, summaries_to_ts=200.0)
        db.save_diary_entry("world1", "Piper", "00DEF", "Piper diary.",
                           game_days_from=1.0, game_days_to=7.0, summaries_from_ts=100.0, summaries_to_ts=200.0)
        assert len(db.get_all_diary_entries("world1", "Preston", "00ABC")) == 1
        assert len(db.get_all_diary_entries("world1", "Piper", "00DEF")) == 1

    def test_multiple_diary_entries_ordered_by_game_days(self, db: ConversationDB):
        db.save_diary_entry("world1", "Preston", "00ABC", "Second.",
                           game_days_from=7.0, game_days_to=14.0, summaries_from_ts=200.0, summaries_to_ts=300.0)
        db.save_diary_entry("world1", "Preston", "00ABC", "First.",
                           game_days_from=1.0, game_days_to=7.0, summaries_from_ts=100.0, summaries_to_ts=200.0)
        entries = db.get_all_diary_entries("world1", "Preston", "00ABC")
        assert len(entries) == 2
        assert entries[0]["content"] == "First."
        assert entries[1]["content"] == "Second."


class TestCharacterArcs:
    def test_save_and_get_character_arc(self, db: ConversationDB):
        db.save_character_arc("world1", "Preston", "00ABC", "Preston's arc: from reluctant leader to confident general.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        arcs = db.get_all_character_arcs("world1", "Preston", "00ABC")
        assert len(arcs) == 1
        assert "reluctant leader" in arcs[0]["content"]
        assert arcs[0]["game_days_from"] == 1.0
        assert arcs[0]["game_days_to"] == 100.0

    def test_get_latest_arc_game_days(self, db: ConversationDB):
        db.save_character_arc("world1", "Preston", "00ABC", "First arc.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        db.save_character_arc("world1", "Preston", "00ABC", "Second arc.",
                              game_days_from=100.0, game_days_to=200.0,
                              diary_from_ts=5000.0, diary_to_ts=10000.0)
        latest = db.get_latest_arc_game_days("world1", "Preston", "00ABC")
        assert latest == 200.0

    def test_get_latest_arc_game_days_returns_none_when_empty(self, db: ConversationDB):
        latest = db.get_latest_arc_game_days("world1", "Preston", "00ABC")
        assert latest is None

    def test_delete_diary_entries_before_ts(self, db: ConversationDB):
        db.save_diary_entry("world1", "Preston", "00ABC", "Old diary.",
                           game_days_from=1.0, game_days_to=7.0,
                           summaries_from_ts=100.0, summaries_to_ts=200.0)
        db.save_diary_entry("world1", "Preston", "00ABC", "Recent diary.",
                           game_days_from=7.0, game_days_to=14.0,
                           summaries_from_ts=200.0, summaries_to_ts=300.0)
        db.delete_diary_entries_before_ts("world1", "Preston", "00ABC", 250.0)
        remaining = db.get_all_diary_entries("world1", "Preston", "00ABC")
        assert len(remaining) == 1
        assert remaining[0]["content"] == "Recent diary."

    def test_character_arcs_scoped_by_npc(self, db: ConversationDB):
        db.save_character_arc("world1", "Preston", "00ABC", "Preston arc.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        db.save_character_arc("world1", "Piper", "00DEF", "Piper arc.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        assert len(db.get_all_character_arcs("world1", "Preston", "00ABC")) == 1
        assert len(db.get_all_character_arcs("world1", "Piper", "00DEF")) == 1

    def test_multiple_arcs_ordered_by_game_days(self, db: ConversationDB):
        db.save_character_arc("world1", "Preston", "00ABC", "Second arc.",
                              game_days_from=100.0, game_days_to=200.0,
                              diary_from_ts=5000.0, diary_to_ts=10000.0)
        db.save_character_arc("world1", "Preston", "00ABC", "First arc.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        arcs = db.get_all_character_arcs("world1", "Preston", "00ABC")
        assert len(arcs) == 2
        assert arcs[0]["content"] == "First arc."
        assert arcs[1]["content"] == "Second arc."


class TestCharacters:
    def test_upsert_character_creates_new(self, db: ConversationDB):
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        char = db.get_character("world1", "00ABC")
        assert char is not None
        assert char["npc_name"] == "Preston"
        assert char["faction"] == "minutemen"

    def test_upsert_character_updates_faction(self, db: ConversationDB):
        db.upsert_character("world1", "Preston", "00ABC", faction="settler")
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        char = db.get_character("world1", "00ABC")
        assert char["faction"] == "minutemen"

    def test_upsert_character_updates_last_seen(self, db: ConversationDB):
        db.upsert_character("world1", "Preston", "00ABC", faction="settler")
        first_seen = db.get_character("world1", "00ABC")["last_seen_at"]
        import time; time.sleep(0.05)
        db.upsert_character("world1", "Preston", "00ABC", faction="settler")
        second_seen = db.get_character("world1", "00ABC")["last_seen_at"]
        assert second_seen > first_seen

    def test_get_character_returns_none_when_missing(self, db: ConversationDB):
        assert db.get_character("world1", "99999") is None

    def test_get_faction_members(self, db: ConversationDB):
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        db.upsert_character("world1", "Piper", "00DEF", faction="companion")
        db.upsert_character("world1", "Sturges", "00GHI", faction="minutemen")
        members = db.get_faction_members("world1", "minutemen")
        names = [m["npc_name"] for m in members]
        assert "Preston" in names
        assert "Sturges" in names
        assert "Piper" not in names

    def test_upsert_character_null_faction(self, db: ConversationDB):
        db.upsert_character("world1", "Raider", "00ZZZ", faction=None)
        char = db.get_character("world1", "00ZZZ")
        assert char["faction"] is None

    def test_upsert_character_empty_string_faction_becomes_none(self, db: ConversationDB):
        db.upsert_character("world1", "Raider", "00ZZZ", faction="")
        char = db.get_character("world1", "00ZZZ")
        assert char["faction"] is None


class TestFactionRumors:
    def test_save_and_get_faction_rumors(self, db: ConversationDB):
        db.save_faction_rumor("world1", "minutemen", "Preston", "00ABC",
                              "Word is Preston dealt with some raiders.", game_days=10.0)
        rumors = db.get_faction_rumors("world1", "minutemen")
        assert len(rumors) == 1
        assert "Preston dealt with" in rumors[0]["content"]
        assert rumors[0]["source_npc_name"] == "Preston"

    def test_get_faction_rumors_excludes_source_npc(self, db: ConversationDB):
        db.save_faction_rumor("world1", "minutemen", "Preston", "00ABC",
                              "Preston's rumor.", game_days=10.0)
        db.save_faction_rumor("world1", "minutemen", "Sturges", "00GHI",
                              "Sturges' rumor.", game_days=12.0)
        rumors = db.get_faction_rumors("world1", "minutemen", exclude_ref_id="00ABC")
        assert len(rumors) == 1
        assert rumors[0]["source_npc_name"] == "Sturges"

    def test_get_faction_rumors_ordered_by_game_days(self, db: ConversationDB):
        db.save_faction_rumor("world1", "minutemen", "Preston", "00ABC",
                              "Later rumor.", game_days=20.0)
        db.save_faction_rumor("world1", "minutemen", "Sturges", "00GHI",
                              "Earlier rumor.", game_days=5.0)
        rumors = db.get_faction_rumors("world1", "minutemen")
        assert rumors[0]["content"] == "Earlier rumor."
        assert rumors[1]["content"] == "Later rumor."

    def test_faction_rumors_scoped_by_faction(self, db: ConversationDB):
        db.save_faction_rumor("world1", "minutemen", "Preston", "00ABC",
                              "Minutemen rumor.", game_days=10.0)
        db.save_faction_rumor("world1", "railroad", "Deacon", "00DEF",
                              "Railroad rumor.", game_days=10.0)
        assert len(db.get_faction_rumors("world1", "minutemen")) == 1
        assert len(db.get_faction_rumors("world1", "railroad")) == 1


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
