"""Tests for start_conversation debounce logic.

Prevents rapid-fire restarts (e.g. FO4VR returning from main menu) from
killing an existing conversation with the same NPC.

Uses mocks to avoid heavy TTS/LLM infrastructure.
"""
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.game_manager import GameStateManager
from src.http.communication_constants import communication_constants as comm_consts


# --- Helpers ---

def _make_start_request(*base_ids):
    """Build a minimal start_conversation input_json with the given actor base IDs.

    First base_id is treated as the player, rest as NPCs.
    """
    actors = []
    for i, bid in enumerate(base_ids):
        actors.append({
            comm_consts.KEY_ACTOR_BASEID: bid,
            comm_consts.KEY_ACTOR_REFID: bid,
            comm_consts.KEY_ACTOR_NAME: f"Actor{bid}",
            comm_consts.KEY_ACTOR_GENDER: 0,
            comm_consts.KEY_ACTOR_RACE: "[Race <HumanRace (00013746)>]",
            comm_consts.KEY_ACTOR_ISPLAYER: (i == 0),
            comm_consts.KEY_ACTOR_ISENEMY: False,
            comm_consts.KEY_ACTOR_ISINCOMBAT: False,
            comm_consts.KEY_ACTOR_RELATIONSHIPRANK: 0,
            comm_consts.KEY_ACTOR_VOICETYPE: "[VoiceType <MaleEvenToned (00013AD2)>]",
        })
    return {
        comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION,
        comm_consts.KEY_ACTORS: actors,
        comm_consts.KEY_CONTEXT: {
            comm_consts.KEY_CONTEXT_LOCATION: "TestLocation",
            comm_consts.KEY_CONTEXT_TIME: 12,
            comm_consts.KEY_CONTEXT_INGAMEEVENTS: [],
        },
        comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_TEXT,
        comm_consts.KEY_STARTCONVERSATION_WORLDID: "TestWorld",
    }


@pytest.fixture
def game_manager():
    """Create a GameStateManager with mocked heavy dependencies."""
    mock_game = MagicMock()
    mock_chat_manager = MagicMock()
    mock_config = MagicMock()
    mock_config.game.base_game = "Skyrim"  # Not Fallout4 — skip quest lookup
    mock_config.automatic_greeting = True
    mock_config.narration_handling = "use_narrator"
    mock_language_info = {'alpha2': 'en', 'language': 'English', 'hello': 'Hello'}
    mock_client = MagicMock()

    gm = GameStateManager(
        mock_game, mock_chat_manager, mock_config,
        mock_language_info, mock_client,
        "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt"
    )
    return gm


@pytest.fixture
def patched_conversation():
    """Patch Conversation so start_conversation doesn't need real infrastructure.

    Each call to Conversation() returns a distinct MagicMock (so we can tell
    whether the debounce reused or replaced the conversation object).
    """
    with patch("src.game_manager.Conversation") as MockConv:
        # Each call to MockConv() returns a new MagicMock instance
        MockConv.side_effect = lambda *a, **kw: MagicMock()
        yield MockConv


# --- Tests ---

def test_debounce_same_actors_within_cooldown(game_manager, patched_conversation):
    """Second start_conversation with same actors within cooldown reuses the existing conversation."""
    request = _make_start_request(0, 100)  # player=0, npc=100

    # First call — creates conversation
    response1 = game_manager.start_conversation(request)
    assert response1[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTTYPE_STARTCONVERSATIONCOMPLETED
    conv1 = game_manager._GameStateManager__talk

    # Second call — same actors, within cooldown → should reuse
    response2 = game_manager.start_conversation(request)
    assert response2[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTTYPE_STARTCONVERSATIONCOMPLETED
    conv2 = game_manager._GameStateManager__talk

    # Conversation object must be the same (not replaced)
    assert conv1 is conv2
    # end() should NOT have been called on the existing conversation
    conv1.end.assert_not_called()


def test_no_debounce_different_actors(game_manager, patched_conversation):
    """start_conversation with different actors is NOT debounced even within cooldown."""
    request_a = _make_start_request(0, 100)  # player=0, npc=100
    request_b = _make_start_request(0, 200)  # player=0, npc=200 (different NPC)

    # First call with NPC 100
    game_manager.start_conversation(request_a)
    conv1 = game_manager._GameStateManager__talk

    # Second call with NPC 200 → should NOT debounce
    game_manager.start_conversation(request_b)
    conv2 = game_manager._GameStateManager__talk

    # Conversation must be a NEW object (replaced)
    assert conv1 is not conv2
    # Previous conversation should have been ended
    conv1.end.assert_called_once()


def test_no_debounce_after_explicit_end(game_manager, patched_conversation):
    """After end_conversation, same actors should start a fresh conversation (no debounce)."""
    request = _make_start_request(0, 100)

    # First call
    game_manager.start_conversation(request)
    conv1 = game_manager._GameStateManager__talk

    # Explicitly end the conversation
    game_manager.end_conversation({})

    # Start again with same actors → debounce should be cleared
    game_manager.start_conversation(request)
    conv2 = game_manager._GameStateManager__talk

    assert conv2 is not None
    assert conv1 is not conv2


def test_no_debounce_after_cooldown_expires(game_manager, patched_conversation, monkeypatch):
    """After cooldown expires, same actors should start a fresh conversation."""
    request = _make_start_request(0, 100)

    # First call at real time
    game_manager.start_conversation(request)
    conv1 = game_manager._GameStateManager__talk

    # Advance time past the cooldown (>10s) by patching the time module directly
    fake_time = time.time() + 15
    monkeypatch.setattr(time, "time", lambda: fake_time)

    # Second call — same actors but cooldown expired → should restart
    game_manager.start_conversation(request)
    conv2 = game_manager._GameStateManager__talk

    assert conv1 is not conv2
