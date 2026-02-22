"""Tests for async conversation save during end() — prevents blocking start_conversation."""
import threading
import time
from unittest.mock import MagicMock, patch
from src.conversation.conversation import Conversation


def _make_conversation_with_mock_save():
    """Create a Conversation with mocked dependencies, patching the save to be controllable."""
    context = MagicMock()
    context.config.allow_interruption = True
    context.config.end_conversation_keywords = []
    context.config.goodbye_npc_response = "Goodbye."
    context.config.collecting_thoughts_npc_response = "Hmm..."
    context.config.actions = []
    context.config.advanced_actions_enabled = False
    context.npcs_in_conversation = MagicMock()

    output_manager = MagicMock()
    rememberer = MagicMock()
    llm_client = MagicMock()
    stt = None

    conv = Conversation(context, output_manager, rememberer, llm_client, stt, False, False, None)
    return conv


def test_end_async_save_returns_before_save_completes():
    """end(async_save=True) should return immediately while save runs in background."""
    conv = _make_conversation_with_mock_save()

    save_started = threading.Event()
    save_done = threading.Event()

    # Patch the private save method to block until we signal
    original_save = conv._Conversation__save_conversation
    def slow_save(*args, **kwargs):
        save_started.set()
        time.sleep(1.0)  # Simulate slow LLM summary call
        save_done.set()

    conv._Conversation__save_conversation = slow_save

    start = time.monotonic()
    conv.end(async_save=True)
    elapsed = time.monotonic() - start

    # end() should return in well under 1 second (save takes 1s)
    assert elapsed < 0.5, f"end(async_save=True) blocked for {elapsed:.2f}s — should return immediately"

    # Save should eventually complete in the background
    assert save_done.wait(timeout=3.0), "Background save never completed"


def test_end_sync_save_blocks_until_complete():
    """Default end() (sync) should block until save completes."""
    conv = _make_conversation_with_mock_save()

    save_done = threading.Event()

    def slow_save(*args, **kwargs):
        time.sleep(0.3)
        save_done.set()

    conv._Conversation__save_conversation = slow_save

    conv.end()

    # Save should be complete by the time end() returns
    assert save_done.is_set(), "Sync end() returned before save completed"
