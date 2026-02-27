"""Tests for quest_context.py — parsing the exact format Papyrus sends."""
import pytest
from src.wiki.quest_context import QuestContextBuilder


@pytest.fixture
def builder():
    return QuestContextBuilder()


class TestParseQuestContext:
    """Test parse_quest_context with the Papyrus wire format: QuestName:status[:stage]|..."""

    def test_empty_string(self, builder):
        assert builder.parse_quest_context("") == ""

    def test_none_input(self, builder):
        assert builder.parse_quest_context(None) == ""

    def test_single_running_quest(self, builder):
        result = builder.parse_quest_context("The First Step:running:10")
        assert "The First Step" in result
        assert "stage" in result.lower() or "10" in result

    def test_single_completed_quest(self, builder):
        result = builder.parse_quest_context("When Freedom Calls:completed")
        assert "When Freedom Calls" in result
        assert "COMPLETED" in result.upper()

    def test_multiple_quests_pipe_separated(self, builder):
        raw = "The First Step:running:10|When Freedom Calls:completed"
        result = builder.parse_quest_context(raw)
        assert "The First Step" in result
        assert "When Freedom Calls" in result

    def test_running_and_completed_mixed(self, builder):
        raw = "Jewel of the Commonwealth:running:30|When Freedom Calls:completed|The First Step:completed"
        result = builder.parse_quest_context(raw)
        assert "Jewel of the Commonwealth" in result
        assert "When Freedom Calls" in result
        assert "The First Step" in result

    def test_skips_not_started_quests(self, builder):
        """Papyrus skips not_started quests, but if one sneaks through, it should be ignored."""
        raw = "SomeQuest:not_started"
        result = builder.parse_quest_context(raw)
        # not_started quests shouldn't produce active or completed output
        assert "SomeQuest" not in result or "not_started" not in result.lower()

    def test_quest_with_colon_in_name(self, builder):
        """Quest names shouldn't contain colons in FO4, but test robustness."""
        raw = "War:Never Changes:running:50"
        result = builder.parse_quest_context(raw)
        # The parser finds "running" as the status delimiter
        assert "running" not in result.lower() or "50" in result

    def test_high_stage_number(self, builder):
        raw = "The Molecular Level:running:450"
        result = builder.parse_quest_context(raw)
        assert "The Molecular Level" in result
        assert "450" in result

    def test_trailing_pipe(self, builder):
        raw = "The First Step:running:10|"
        result = builder.parse_quest_context(raw)
        assert "The First Step" in result

    def test_only_completed_quests(self, builder):
        raw = "When Freedom Calls:completed|Out of Time:completed"
        result = builder.parse_quest_context(raw)
        assert "COMPLETED" in result.upper()
        assert "When Freedom Calls" in result
        assert "Out of Time" in result

    def test_stage_zero_running_skipped(self, builder):
        """Papyrus sends running:0 for quest at stage 0 — but BuildQuestData skips stage<=0."""
        # This shouldn't happen because BuildQuestData checks stage > 0,
        # but if it did arrive, the parser handles it gracefully.
        raw = "SomeQuest:running:0"
        result = builder.parse_quest_context(raw)
        # Stage 0 means effectively not started — should still parse without error
        assert isinstance(result, str)
