"""Tests for FO4 topicID forced alternation — prevents audio caching when game sends duplicate topicIDs."""
import pytest


def test_alternation_normal_sequence():
    """Normal alternating sequence passes through unchanged."""
    from src.games.fallout4 import Fallout4
    game = Fallout4.__new__(Fallout4)
    game._Fallout4__last_fuz_topic_id = 0

    assert game.get_corrected_topic_id(1) == 1
    assert game.get_corrected_topic_id(2) == 2
    assert game.get_corrected_topic_id(1) == 1
    assert game.get_corrected_topic_id(2) == 2


def test_alternation_fixes_consecutive_same():
    """Consecutive same topicID gets flipped to the other slot."""
    from src.games.fallout4 import Fallout4
    game = Fallout4.__new__(Fallout4)
    game._Fallout4__last_fuz_topic_id = 0

    assert game.get_corrected_topic_id(1) == 1
    assert game.get_corrected_topic_id(1) == 2  # forced to 2
    assert game.get_corrected_topic_id(2) == 1  # forced to 1 (still alternating)


def test_alternation_fixes_triple_same():
    """Three consecutive same topicIDs alternate correctly."""
    from src.games.fallout4 import Fallout4
    game = Fallout4.__new__(Fallout4)
    game._Fallout4__last_fuz_topic_id = 0

    assert game.get_corrected_topic_id(2) == 2
    assert game.get_corrected_topic_id(2) == 1  # forced
    assert game.get_corrected_topic_id(2) == 2  # forced back


def test_alternation_first_call_passes_through():
    """First topicID always passes through unchanged."""
    from src.games.fallout4 import Fallout4
    game = Fallout4.__new__(Fallout4)
    game._Fallout4__last_fuz_topic_id = 0

    assert game.get_corrected_topic_id(2) == 2
