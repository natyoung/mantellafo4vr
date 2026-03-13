import pytest
from pathlib import Path
from unittest.mock import MagicMock
from src.conversation.conversation_db import ConversationDB
from src.remember.arc import ArcConsolidator


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    db = ConversationDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.request_call.return_value = "Looking back on these months in the Commonwealth, I've changed."
    return client


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.arc_prompt = "You are {name} in {game}. Write a character arc in {language}."
    config.arc_interval_days = 30
    config.arc_min_diaries = 3
    return config


@pytest.fixture
def consolidator(db, mock_client, mock_config):
    return ArcConsolidator(db, mock_client, mock_config, language_name="English", game_name="Fallout4")


class TestArcConsolidator:
    def test_no_consolidation_when_not_enough_days(self, consolidator, db):
        # Add 5 diary entries but only 10 game days have passed
        for i in range(5):
            db.save_diary_entry("world1", "Preston", "00ABC", f"Diary {i}.",
                               game_days_from=float(i * 2), game_days_to=float((i + 1) * 2),
                               summaries_from_ts=float(i * 100), summaries_to_ts=float((i + 1) * 100))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=10.0)
        assert result is False

    def test_no_consolidation_when_no_diaries(self, consolidator):
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=200.0)
        assert result is False

    def test_no_consolidation_when_fewer_than_min_diaries(self, consolidator, db):
        db.save_diary_entry("world1", "Preston", "00ABC", "Only one.",
                           game_days_from=1.0, game_days_to=7.0,
                           summaries_from_ts=100.0, summaries_to_ts=200.0)
        db.save_diary_entry("world1", "Preston", "00ABC", "Only two.",
                           game_days_from=7.0, game_days_to=14.0,
                           summaries_from_ts=200.0, summaries_to_ts=300.0)
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=200.0)
        assert result is False

    def test_consolidation_calls_llm_and_saves_arc(self, consolidator, db, mock_client):
        for i in range(4):
            db.save_diary_entry("world1", "Preston", "00ABC", f"Diary {i}.",
                               game_days_from=float(i * 10), game_days_to=float((i + 1) * 10),
                               summaries_from_ts=float(i * 1000), summaries_to_ts=float((i + 1) * 1000))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=50.0)
        assert result is True
        mock_client.request_call.assert_called_once()
        arcs = db.get_all_character_arcs("world1", "Preston", "00ABC")
        assert len(arcs) == 1
        assert "changed" in arcs[0]["content"]

    def test_consolidation_deletes_old_diaries(self, consolidator, db):
        for i in range(4):
            db.save_diary_entry("world1", "Preston", "00ABC", f"Diary {i}.",
                               game_days_from=float(i * 10), game_days_to=float((i + 1) * 10),
                               summaries_from_ts=float(i * 1000), summaries_to_ts=float((i + 1) * 1000))
        consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=50.0)
        remaining = db.get_all_diary_entries("world1", "Preston", "00ABC")
        assert len(remaining) == 0

    def test_consolidation_respects_interval_after_previous_arc(self, consolidator, db):
        # Previous arc covers up to day 100
        db.save_character_arc("world1", "Preston", "00ABC", "Old arc.",
                              game_days_from=1.0, game_days_to=100.0,
                              diary_from_ts=100.0, diary_to_ts=5000.0)
        # Add enough diary entries
        for i in range(4):
            db.save_diary_entry("world1", "Preston", "00ABC", f"Diary {i}.",
                               game_days_from=float(100 + i * 10), game_days_to=float(110 + i * 10),
                               summaries_from_ts=float(5000 + i * 1000), summaries_to_ts=float(6000 + i * 1000))
        # Only 20 days since last arc — not enough (need 30)
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=120.0)
        assert result is False
        # 30 days since last arc — enough
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=130.0)
        assert result is True

    def test_consolidation_skipped_when_llm_returns_empty(self, consolidator, db, mock_client):
        mock_client.request_call.return_value = ""
        for i in range(4):
            db.save_diary_entry("world1", "Preston", "00ABC", f"Diary {i}.",
                               game_days_from=float(i * 10), game_days_to=float((i + 1) * 10),
                               summaries_from_ts=float(i * 1000), summaries_to_ts=float((i + 1) * 1000))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=50.0)
        assert result is False
        assert len(db.get_all_character_arcs("world1", "Preston", "00ABC")) == 0
        # Diary entries NOT deleted (consolidation failed)
        assert len(db.get_all_diary_entries("world1", "Preston", "00ABC")) == 4
