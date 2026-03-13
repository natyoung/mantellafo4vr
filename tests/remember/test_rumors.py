"""Tests for faction rumor generation from diary entries."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from src.conversation.conversation_db import ConversationDB
from src.remember.rumors import RumorGenerator


@pytest.fixture
def db(tmp_path: Path) -> ConversationDB:
    db = ConversationDB(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.request_call.return_value = "Word around the settlement is that things got rough with some raiders."
    return client


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.rumor_prompt = "Rewrite this diary entry as a brief third-person rumor in {language}. The NPC's name is {name}."
    return config


@pytest.fixture
def generator(db, mock_client, mock_config):
    return RumorGenerator(db, mock_client, mock_config, language_name="English")


class TestRumorGenerator:
    def test_generates_rumor_from_diary(self, generator, db, mock_client):
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        result = generator.maybe_generate(
            world_id="world1", npc_name="Preston", npc_ref_id="00ABC",
            diary_content="It's been a rough week dealing with raiders.",
            game_days=10.0,
        )
        assert result is True
        mock_client.request_call.assert_called_once()
        rumors = db.get_faction_rumors("world1", "minutemen")
        assert len(rumors) == 1

    def test_skips_when_no_faction(self, generator, db, mock_client):
        db.upsert_character("world1", "Wanderer", "00ZZZ", faction=None)
        result = generator.maybe_generate(
            world_id="world1", npc_name="Wanderer", npc_ref_id="00ZZZ",
            diary_content="Just another day.",
            game_days=10.0,
        )
        assert result is False
        mock_client.request_call.assert_not_called()

    def test_skips_when_character_not_found(self, generator, mock_client):
        result = generator.maybe_generate(
            world_id="world1", npc_name="Nobody", npc_ref_id="FFFFF",
            diary_content="Hello.",
            game_days=10.0,
        )
        assert result is False
        mock_client.request_call.assert_not_called()

    def test_skips_when_llm_returns_empty(self, generator, db, mock_client):
        mock_client.request_call.return_value = ""
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        result = generator.maybe_generate(
            world_id="world1", npc_name="Preston", npc_ref_id="00ABC",
            diary_content="Entry.",
            game_days=10.0,
        )
        assert result is False
        assert len(db.get_faction_rumors("world1", "minutemen")) == 0

    def test_rumor_attributed_to_source_npc(self, generator, db):
        db.upsert_character("world1", "Preston", "00ABC", faction="minutemen")
        generator.maybe_generate(
            world_id="world1", npc_name="Preston", npc_ref_id="00ABC",
            diary_content="Entry.",
            game_days=10.0,
        )
        rumors = db.get_faction_rumors("world1", "minutemen")
        assert rumors[0]["source_npc_name"] == "Preston"
        assert rumors[0]["source_npc_ref_id"] == "00ABC"
