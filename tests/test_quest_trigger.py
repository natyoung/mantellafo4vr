"""Tests for the GetActiveQuest trigger phrase detection and quest list enrichment."""
import pytest


class TestQuestTriggerPhrase:
    """Test that the trigger phrase is detected correctly from STT input."""

    def test_exact_match(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("what's the plan?", "what's the plan?") is True

    def test_case_insensitive(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("What's The Plan?", "what's the plan?") is True

    def test_no_punctuation(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("whats the plan", "what's the plan?") is True

    def test_no_match(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("hello there", "what's the plan?") is False

    def test_partial_match_rejects(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("what's the plan for dinner", "what's the plan?") is False

    def test_embedded_in_sentence_rejects(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("so what's the plan then", "what's the plan?") is False

    def test_custom_trigger(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("brief me", "brief me") is True

    def test_empty_input(self):
        from src.quest_trigger import is_quest_trigger
        assert is_quest_trigger("", "what's the plan?") is False


class TestQuestListEnrichment:
    """Test parsing Papyrus quest data and enriching with DB metadata."""

    def test_parse_single_quest(self):
        from src.quest_trigger import parse_running_quests
        raw = "736898:Concierge:100"
        result = parse_running_quests(raw)
        assert len(result) == 1
        assert result[0]['formid_decimal'] == '736898'
        assert result[0]['name'] == 'Concierge'
        assert result[0]['stage'] == '100'

    def test_parse_multiple_quests(self):
        from src.quest_trigger import parse_running_quests
        raw = "736898:Concierge:100|141822:Long Time Coming:105"
        result = parse_running_quests(raw)
        assert len(result) == 2
        assert result[1]['name'] == 'Long Time Coming'

    def test_parse_none(self):
        from src.quest_trigger import parse_running_quests
        result = parse_running_quests("NONE")
        assert result == []

    def test_parse_empty(self):
        from src.quest_trigger import parse_running_quests
        result = parse_running_quests("")
        assert result == []

    def test_parse_malformed_entry_skipped(self):
        from src.quest_trigger import parse_running_quests
        raw = "736898:Concierge:100|baddata|141822:Long Time Coming:105"
        result = parse_running_quests(raw)
        assert len(result) == 2


class TestQuestFactionGrouping:
    """Test grouping quests by faction."""

    def test_group_by_faction(self):
        from src.quest_trigger import group_quests_by_faction
        quests = [
            {'name': 'Butcher\'s Bill', 'faction': 'Railroad', 'location': ''},
            {'name': 'Concierge', 'faction': 'Railroad', 'location': ''},
            {'name': 'Semper Invicta', 'faction': 'Brotherhood of Steel', 'location': ''},
            {'name': 'Long Time Coming', 'faction': '', 'location': ''},
        ]
        groups = group_quests_by_faction(quests)
        assert len(groups['Railroad']) == 2
        assert len(groups['Brotherhood of Steel']) == 1
        assert len(groups['Other']) == 1

    def test_empty_list(self):
        from src.quest_trigger import group_quests_by_faction
        groups = group_quests_by_faction([])
        assert groups == {}


class TestBuildQuestContext:
    """Test building the LLM context string from grouped quests."""

    def test_context_has_no_stage_numbers(self):
        from src.quest_trigger import build_quest_context_for_llm
        quests = [
            {'name': 'Concierge', 'faction': 'Railroad', 'location': 'Mercer Safehouse', 'stage': '100'},
        ]
        context = build_quest_context_for_llm(quests)
        # Stage number should not appear in the quest list portion
        quest_list_section = context.split("Quest list by category:")[1].split("INSTRUCTIONS:")[0]
        assert '100' not in quest_list_section
        assert 'Concierge' in context

    def test_context_has_faction_info(self):
        from src.quest_trigger import build_quest_context_for_llm
        quests = [
            {'name': 'Semper Invicta', 'faction': 'Brotherhood of Steel', 'location': 'Cambridge Police Station', 'stage': '200'},
        ]
        context = build_quest_context_for_llm(quests)
        assert 'Brotherhood of Steel' in context

    def test_context_instructs_no_game_data(self):
        from src.quest_trigger import build_quest_context_for_llm
        quests = [
            {'name': 'Concierge', 'faction': 'Railroad', 'location': '', 'stage': '100'},
        ]
        context = build_quest_context_for_llm(quests)
        assert 'stage numbers' in context.lower() or 'game system' in context.lower()
