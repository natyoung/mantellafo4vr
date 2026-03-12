import pytest
from pathlib import Path
from unittest.mock import MagicMock
from src.conversation.conversation_db import ConversationDB
from src.remember.diary import DiaryConsolidator


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    db = ConversationDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.request_call.return_value = "Dear diary, I had a rough week in the wasteland."
    return client


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.diary_prompt = "You are {name} in {game}. Write a diary in {language}."
    config.diary_interval_days = 7
    config.diary_min_summaries = 3
    return config


@pytest.fixture
def consolidator(db, mock_client, mock_config):
    return DiaryConsolidator(db, mock_client, mock_config, language_name="English", game_name="Fallout4")


class TestDiaryConsolidator:
    def test_no_consolidation_when_not_enough_days(self, consolidator, db):
        # Add 5 summaries but only 3 game days have passed
        for i in range(5):
            db.save_summary("world1", "Preston", "00ABC", f"Summary {i}.", float(i), float(i + 1))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=3.0)
        assert result is False

    def test_no_consolidation_when_no_summaries(self, consolidator):
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=100.0)
        assert result is False

    def test_no_consolidation_when_fewer_than_min_summaries(self, consolidator, db):
        db.save_summary("world1", "Preston", "00ABC", "Only one.", 100.0, 200.0)
        db.save_summary("world1", "Preston", "00ABC", "Only two.", 200.0, 300.0)
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=100.0)
        assert result is False

    def test_consolidation_calls_llm_and_saves_diary(self, consolidator, db, mock_client):
        for i in range(4):
            db.save_summary("world1", "Preston", "00ABC", f"Summary {i}.", float(i * 100), float((i + 1) * 100))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=10.0)
        assert result is True
        # LLM was called
        mock_client.request_call.assert_called_once()
        # Diary entry was saved
        entries = db.get_all_diary_entries("world1", "Preston", "00ABC")
        assert len(entries) == 1
        assert "rough week" in entries[0]["content"]

    def test_consolidation_deletes_old_summaries(self, consolidator, db):
        for i in range(4):
            db.save_summary("world1", "Preston", "00ABC", f"Summary {i}.", float(i * 100), float((i + 1) * 100))
        consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=10.0)
        remaining = db.get_all_summaries("world1", "Preston", "00ABC")
        assert len(remaining) == 0

    def test_consolidation_respects_interval_after_previous_diary(self, consolidator, db):
        # Previous diary covers up to day 7
        db.save_diary_entry("world1", "Preston", "00ABC", "Old diary.",
                           game_days_from=1.0, game_days_to=7.0,
                           summaries_from_ts=0.0, summaries_to_ts=100.0)
        # Add enough summaries
        for i in range(4):
            db.save_summary("world1", "Preston", "00ABC", f"Summary {i}.",
                           float(100 + i * 100), float(200 + i * 100))
        # Only 5 days since last diary — not enough
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=12.0)
        assert result is False
        # 14 days since last diary — enough
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=14.0)
        assert result is True

    def test_consolidation_skipped_when_llm_returns_empty(self, consolidator, db, mock_client):
        mock_client.request_call.return_value = ""
        for i in range(4):
            db.save_summary("world1", "Preston", "00ABC", f"Summary {i}.", float(i * 100), float((i + 1) * 100))
        result = consolidator.maybe_consolidate("world1", "Preston", "00ABC", current_game_days=10.0)
        assert result is False
        # No diary entry saved
        assert len(db.get_all_diary_entries("world1", "Preston", "00ABC")) == 0
        # Summaries NOT deleted (consolidation failed)
        assert len(db.get_all_summaries("world1", "Preston", "00ABC")) == 4
