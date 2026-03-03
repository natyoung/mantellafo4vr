"""Tests for stale F4SE_HTTP queue drain fix.

Root cause: when a conversation ends, multiple concurrent continue_conversation
requests each get an end_conversation response from Python. Only one is consumed;
the rest sit in the F4SE_HTTP queue. Starting a new conversation reads a stale
end_conversation, killing the new conversation immediately.

Fix has two parts:
1. Papyrus: drain the F4SE_HTTP queue before sending start_conversation
2. Python: only return one end_conversation per conversation ending; suppress duplicates
"""
import pathlib
import pytest


def test_papyrus_drains_queue_before_start():
    """StartConversation must drain F4SE_HTTP queue before sending the start request.

    Without this, stale end_conversation responses from a previous conversation's
    concurrent threads will be READ before the new start_conversation_completed,
    immediately killing the new conversation.
    """
    source = (pathlib.Path(__file__).parent.parent / 'papyrus' / 'MantellaConversation.psc').read_text()

    # Find StartConversation function
    in_method = False
    method_lines = []
    method_indent = None
    for line in source.split('\n'):
        if 'function StartConversation' in line:
            in_method = True
            method_indent = len(line) - len(line.lstrip())
            continue
        if in_method:
            stripped = line.lstrip()
            if (stripped.startswith('Function ') or stripped.startswith('function ')) and (len(line) - len(stripped)) <= method_indent:
                break
            method_lines.append(line)

    assert len(method_lines) > 0, "Could not find StartConversation function"

    # Find the queue drain loop and the sendHTTPRequest call
    drain_line = None
    send_line = None
    for i, line in enumerate(method_lines):
        if 'GetHandle()' in line and drain_line is None:
            drain_line = i
        if 'sendHTTPRequest' in line:
            send_line = i

    assert drain_line is not None, (
        "StartConversation must call F4SE_HTTP.GetHandle() to drain stale responses. "
        "Without this, stale end_conversation responses pollute the queue across conversations."
    )
    assert send_line is not None, "Could not find sendHTTPRequest in StartConversation"
    assert drain_line < send_line, (
        f"Queue drain (line {drain_line}) must happen BEFORE sendHTTPRequest (line {send_line}). "
        f"Stale responses must be consumed before sending the new start request."
    )


def test_python_suppresses_duplicate_end_conversation():
    """continue_conversation should not return mantella_end_conversation from stale
    concurrent threads after the first end has already been sent.

    When the LLM ends a conversation, all blocked continue_conversation threads
    wake up. If they ALL return end_conversation, 5+ stale responses pile up in
    the F4SE_HTTP queue. The first end_conversation is legitimate; subsequent ones
    from concurrent threads should be suppressed.
    """
    source = (pathlib.Path(__file__).parent.parent / 'src' / 'game_manager.py').read_text()

    # Check that GameStateManager has an end_response deduplication mechanism
    assert '_end_conversation_sent' in source or '__end_conversation_sent' in source, (
        "GameStateManager must track whether an end_conversation response has been sent "
        "(e.g., with a threading.Event named _end_conversation_sent) to suppress duplicates."
    )

    # The continue_conversation method should check this flag before returning end_conversation
    # Extract continue_conversation method
    in_method = False
    method_lines = []
    method_indent = None
    for line in source.split('\n'):
        if 'def continue_conversation' in line:
            in_method = True
            method_indent = len(line) - len(line.lstrip())
            continue
        if in_method:
            stripped = line.lstrip()
            if stripped.startswith('def ') and (len(line) - len(stripped)) <= method_indent:
                break
            method_lines.append(line)

    method_text = '\n'.join(method_lines)
    assert '_deduplicated_end_response' in method_text, (
        "continue_conversation must use _deduplicated_end_response() instead of "
        "directly returning KEY_REPLYTYPE_ENDCONVERSATION to suppress duplicates."
    )
    # Verify no direct end_conversation returns bypass the deduplication
    for i, line in enumerate(method_lines):
        if 'KEY_REPLYTYPE_ENDCONVERSATION' in line and 'return' in line:
            assert '_deduplicated_end_response' in line or 'reply.get' in line, (
                f"Line {i} returns end_conversation directly without deduplication: {line.strip()}"
            )
