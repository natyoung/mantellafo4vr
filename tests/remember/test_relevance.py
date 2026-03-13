"""Tests for relevance-based memory scoring."""
import pytest
from src.remember.relevance import score_memories


class TestScoreMemories:
    def test_returns_all_when_fewer_than_max(self):
        memories = [
            {"content": "We talked about the raiders at the checkpoint."},
            {"content": "The player helped me fix the water purifier."},
        ]
        result = score_memories(memories, "raiders attacking the settlement", max_results=5)
        assert len(result) == 2

    def test_scores_relevant_higher(self):
        memories = [
            {"content": "We talked about farming tatos and mutfruit."},
            {"content": "Raiders attacked the settlement last night."},
            {"content": "The player asked me about my favorite color."},
        ]
        result = score_memories(memories, "raiders settlement attack", max_results=2)
        assert len(result) == 2
        # The raider memory should be included
        contents = [m["content"] for m in result]
        assert any("Raiders" in c for c in contents)

    def test_empty_memories_returns_empty(self):
        result = score_memories([], "raiders", max_results=5)
        assert result == []

    def test_empty_query_returns_most_recent(self):
        memories = [
            {"content": "Old memory about farming."},
            {"content": "Recent memory about building."},
            {"content": "Latest memory about trading."},
        ]
        result = score_memories(memories, "", max_results=2)
        # With empty query, should return last N (most recent = end of list)
        assert len(result) == 2
        assert result[-1]["content"] == "Latest memory about trading."

    def test_preserves_recent_memories(self):
        """Most recent memories should always be included regardless of relevance."""
        memories = [
            {"content": "Ancient memory about totally unrelated cooking recipes."},
            {"content": "Old memory about Diamond City market."},
            {"content": "Recent memory about sleeping arrangements."},  # Recent but irrelevant
        ]
        result = score_memories(memories, "Diamond City market trading", max_results=2, recent_guaranteed=1)
        assert len(result) == 2
        contents = [m["content"] for m in result]
        # Most recent should be included even though it's about sleeping
        assert "Recent memory about sleeping arrangements." in contents
        # Diamond City one should be included by relevance
        assert "Old memory about Diamond City market." in contents

    def test_max_results_caps_output(self):
        memories = [{"content": f"Memory {i} about raiders."} for i in range(20)]
        result = score_memories(memories, "raiders", max_results=5)
        assert len(result) == 5

    def test_case_insensitive_matching(self):
        memories = [
            {"content": "We discussed the BROTHERHOOD OF STEEL operations."},
            {"content": "The player wanted to talk about flowers."},
        ]
        result = score_memories(memories, "brotherhood of steel", max_results=1)
        assert "BROTHERHOOD" in result[0]["content"]

    def test_preserves_original_order(self):
        """Results should maintain chronological order (input order), not relevance order."""
        memories = [
            {"content": "First: raiders attacked from the north."},
            {"content": "Second: we planted tatos."},
            {"content": "Third: more raiders spotted nearby."},
        ]
        result = score_memories(memories, "raiders", max_results=2)
        # Both raider memories selected, should be in original order
        assert "First" in result[0]["content"]
        assert "Third" in result[1]["content"]
