"""Tests for FO4 topicID forced alternation — prevents audio caching when game sends duplicate topicIDs."""
import inspect
import pytest
from src.http.communication_constants import communication_constants as comm_consts


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


def test_topic_correction_not_called_before_sentence_loop():
    """Regression: get_corrected_topic_id must be called AFTER the while-True sentence loop.

    Before the fix, continue_conversation called get_corrected_topic_id at request entry
    (before blocking in the while loop for a sentence). Multiple concurrent threads would
    snapshot their topicID early, and when sentences became available out of order, consecutive
    FUZ copies would go to the same slot — causing duplicate audio playback in-game.
    """
    import pathlib
    source = (pathlib.Path(__file__).parent.parent / 'src' / 'game_manager.py').read_text()

    # Extract only the continue_conversation method body
    in_method = False
    method_lines = []
    method_indent = None
    for line in source.split('\n'):
        if 'def continue_conversation' in line:
            in_method = True
            method_indent = len(line) - len(line.lstrip())
            continue
        if in_method:
            # End of method: next def at same or lower indent
            stripped = line.lstrip()
            if stripped.startswith('def ') and (len(line) - len(stripped)) <= method_indent:
                break
            method_lines.append(line)

    assert len(method_lines) > 0, "Could not find continue_conversation method"

    while_line = None
    break_line = None
    correction_lines = []
    for i, line in enumerate(method_lines):
        if 'while True:' in line:
            while_line = i
        if 'break' in line and while_line is not None and break_line is None:
            break_line = i
        if 'get_corrected_topic_id' in line:
            correction_lines.append(i)

    assert while_line is not None, "Could not find 'while True' in continue_conversation"
    assert break_line is not None, "Could not find 'break' in continue_conversation"
    assert len(correction_lines) > 0, "get_corrected_topic_id not called in continue_conversation"

    # The critical invariant: get_corrected_topic_id must NOT appear before the while loop
    for cl in correction_lines:
        assert cl > while_line, (
            f"get_corrected_topic_id at line {cl} is before while loop at line {while_line}. "
            f"This causes a race condition with concurrent continue_conversation requests."
        )
        assert cl > break_line, (
            f"get_corrected_topic_id at line {cl} is before break at line {break_line}. "
            f"It must be called at the point of use, after the sentence wait loop exits."
        )
