"""BDD-style conversation flow tests.

Tests the full conversation lifecycle through the GameStateManager public API,
validating the same sequences that broke in production (deadlocks, stale threads,
empty responses, stale conversation references).

These tests hit a real LLM (DeepSeek via OpenRouter) — no mocking of internals.
"""
import os
import wave
import struct
import pytest
import jsonschema
from src.game_manager import GameStateManager
from src.output_manager import ChatManager
from src.config.config_loader import ConfigLoader
from src.llm.llm_client import LLMClient
from src.tts.ttsable import TTSable
from src.tts.synthesization_options import SynthesizationOptions
from src.http import models
from src.http.communication_constants import communication_constants as comm_consts
from tests.test_game_manager import setup_conversation, advance_to_player_turn


# ---------------------------------------------------------------------------
# Stub TTS (no external process needed)
# ---------------------------------------------------------------------------

class StubTTS(TTSable):
    """Minimal TTS that writes a silent WAV file instead of calling an external service."""

    def __init__(self, config: ConfigLoader):
        super().__init__(config)

    def change_voice(self, voice, in_game_voice=None, csv_in_game_voice=None,
                     advanced_voice_model=None, voice_accent=None, voice_gender=None,
                     voice_race=None, voice_language=None):
        self._last_voice = voice

    def tts_synthesize(self, voiceline: str, final_voiceline_file: str, synth_options: SynthesizationOptions):
        # Write a minimal valid WAV (0.1s silence, 16kHz mono 16-bit)
        n_frames = 1600
        with wave.open(final_voiceline_file, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_config(default_config: ConfigLoader) -> ConfigLoader:
    """Override LLM to use DeepSeek (cheaper) and disable advanced actions."""
    default_config.llm = "deepseek/deepseek-chat"
    default_config.advanced_actions_enabled = False
    return default_config


@pytest.fixture
def stub_tts(test_config: ConfigLoader) -> StubTTS:
    return StubTTS(test_config)


@pytest.fixture
def test_chat_manager(test_config: ConfigLoader, stub_tts: StubTTS, llm_client: LLMClient) -> ChatManager:
    """ChatManager using StubTTS so no external TTS process is required."""
    return ChatManager(test_config, stub_tts, llm_client)


@pytest.fixture
def game_manager(skyrim, test_chat_manager, test_config, english_language_info, llm_client) -> GameStateManager:
    """GameStateManager wired to use DeepSeek + StubTTS."""
    return GameStateManager(skyrim, test_chat_manager, test_config, english_language_info, llm_client, "STT_SECRET_KEY.txt", "GPT_SECRET_KEY.txt")


@pytest.fixture
def another_npc_actor() -> models.Actor:
    """Lydia-like actor matching another_example_skyrim_npc_character in conftest."""
    return models.Actor(
        **{
            comm_consts.KEY_ACTOR_BASEID: 1,
            comm_consts.KEY_ACTOR_CUSTOMVALUES: None,
            comm_consts.KEY_ACTOR_GENDER: 1,
            comm_consts.KEY_ACTOR_ISENEMY: False,
            comm_consts.KEY_ACTOR_ISINCOMBAT: False,
            comm_consts.KEY_ACTOR_ISPLAYER: False,
            comm_consts.KEY_ACTOR_NAME: "Lydia",
            comm_consts.KEY_ACTOR_RACE: "Nord",
            comm_consts.KEY_ACTOR_REFID: 1,
            comm_consts.KEY_ACTOR_RELATIONSHIPRANK: 0,
            comm_consts.KEY_ACTOR_VOICETYPE: "[VoiceType <FemaleEvenToned (00013AE3)>]",
            comm_consts.KEY_ACTOR_EQUIPMENT: {
                "body": "Iron Armor",
                "feet": "Iron Boots",
                "hands": "Iron Gauntlets",
                "head": "Iron Helmet",
                "righthand": "Iron Sword",
            },
        }
    )


@pytest.fixture
def multi_npc_start_request(
    example_player_actor: models.Actor,
    example_npc_actor: models.Actor,
    another_npc_actor: models.Actor,
) -> models.StartConversationRequest:
    """Start conversation request with 2 NPCs (Guard + Lydia)."""
    return models.StartConversationRequest(
        **{
            comm_consts.KEY_ACTORS: [
                example_player_actor,
                example_npc_actor,
                another_npc_actor,
            ],
            comm_consts.KEY_CONTEXT: {
                comm_consts.KEY_CONTEXT_INGAMEEVENTS: [],
                comm_consts.KEY_CONTEXT_LOCATION: "Skyrim",
                comm_consts.KEY_CONTEXT_TIME: 12,
            },
            comm_consts.KEY_INPUTTYPE: comm_consts.KEY_INPUTTYPE_TEXT,
            comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_STARTCONVERSATION,
            comm_consts.KEY_STARTCONVERSATION_WORLDID: "TestMulti",
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dump(request) -> dict:
    """Serialize a Pydantic request model to a dict suitable for GameStateManager."""
    return request.model_dump(by_alias=True, exclude_none=True)


def _make_player_input(text: str) -> dict:
    """Build a player_input dict for the given text."""
    return _dump(models.PlayerInputRequest(
        **{
            comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_PLAYERINPUT,
            comm_consts.KEY_REQUESTTYPE_PLAYERINPUT: text,
        }
    ))


def _make_end_request() -> dict:
    """Build an end_conversation dict."""
    return _dump(models.EndConversationRequest(
        **{comm_consts.KEY_REQUESTTYPE: comm_consts.KEY_REQUESTTYPE_ENDCONVERSATION}
    ))


def _collect_npc_responses(game_manager: GameStateManager, continue_request: dict, max_sentences: int = 20) -> list[dict]:
    """Continue conversation and collect all NPC sentences until PLAYERTALK or end.

    Returns list of NPCTALK response dicts. Stops when PLAYERTALK or
    ENDCONVERSATION is received (those are not included in the returned list).
    """
    responses = []
    for _ in range(max_sentences):
        response = game_manager.continue_conversation(continue_request)
        rtype = response[comm_consts.KEY_REPLYTYPE]
        if rtype == comm_consts.KEY_REPLYTYPE_NPCTALK:
            responses.append(response)
        elif rtype in (comm_consts.KEY_REPLYTYPE_PLAYERTALK, comm_consts.KEY_REPLYTYPE_ENDCONVERSATION):
            break
        # ACTION replies — collect but keep going
        elif rtype == comm_consts.KEY_REPLYTYPE_NPCACTION:
            responses.append(response)
    return responses


def _find_end_action(responses: list[dict]) -> bool:
    """Check if any response in the list contains ACTION_ENDCONVERSATION."""
    for r in responses:
        npc_talk = r.get(comm_consts.KEY_REPLYTYPE_NPCTALK)
        if npc_talk and comm_consts.KEY_ACTOR_ACTIONS in npc_talk:
            for action in npc_talk[comm_consts.KEY_ACTOR_ACTIONS]:
                if isinstance(action, dict) and action.get("identifier") == comm_consts.ACTION_ENDCONVERSATION:
                    return True
    return False


# ===========================================================================
# Flow 1: Single NPC Conversation
# ===========================================================================

class TestSingleNpcConversation:
    def test_player_can_start_conversation_with_one_npc(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        # Start conversation
        response = game_manager.start_conversation(start_req)
        jsonschema.validate(response, models.StartConversationResponse.model_json_schema())

        # Advance to player turn — NPC must greet
        advance_to_player_turn(game_manager, cont_req)

    def test_player_can_talk_to_npc_and_get_response(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # Player speaks
        game_manager.player_input(_make_player_input("Hello, how are you today?"))

        # NPC response arrives via continue_conversation
        npc_responses = _collect_npc_responses(game_manager, cont_req)
        assert len(npc_responses) > 0, "Expected at least one NPC response"
        first = npc_responses[0]
        npc_talk = first.get(comm_consts.KEY_REPLYTYPE_NPCTALK)
        assert npc_talk is not None
        assert len(npc_talk[comm_consts.KEY_ACTOR_LINETOSPEAK]) > 0

    def test_player_can_have_multiple_exchanges_with_npc(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # First exchange
        game_manager.player_input(_make_player_input("What do you think about the weather?"))
        advance_to_player_turn(game_manager, cont_req)

        # Second exchange
        game_manager.player_input(_make_player_input("Tell me about this area."))
        npc_responses = _collect_npc_responses(game_manager, cont_req)
        assert len(npc_responses) > 0, "Expected at least one NPC response in second exchange"
        npc_talk = npc_responses[0].get(comm_consts.KEY_REPLYTYPE_NPCTALK)
        assert npc_talk is not None
        assert len(npc_talk[comm_consts.KEY_ACTOR_LINETOSPEAK]) > 0


# ===========================================================================
# Flow 2: Goodbye and End Conversation
# ===========================================================================

class TestGoodbyeAndEndConversation:
    def test_player_can_end_conversation_by_saying_goodbye(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # Say goodbye
        response = game_manager.player_input(_make_player_input("Goodbye."))
        assert response[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCTALK

        # Collect remaining NPC responses — one of them should contain ACTION_ENDCONVERSATION
        all_responses = [response]
        all_responses.extend(_collect_npc_responses(game_manager, cont_req))

        assert _find_end_action(all_responses), (
            "Expected ACTION_ENDCONVERSATION in goodbye response but none found"
        )

    def test_conversation_is_properly_ended_after_goodbye(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # Goodbye flow
        response = game_manager.player_input(_make_player_input("Goodbye."))
        _collect_npc_responses(game_manager, cont_req)

        # End conversation
        end_response = game_manager.end_conversation(_make_end_request())
        assert end_response[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_ENDCONVERSATION

    def test_player_can_start_new_conversation_after_ending_previous(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        """Regression test: starting a new conversation after ending the previous one
        must not deadlock or reuse stale conversation state."""
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        # First conversation
        setup_conversation(game_manager, start_req, cont_req)
        game_manager.player_input(_make_player_input("Goodbye."))
        _collect_npc_responses(game_manager, cont_req)
        game_manager.end_conversation(_make_end_request())

        # Second conversation — must work without stale state
        response = game_manager.start_conversation(start_req)
        jsonschema.validate(response, models.StartConversationResponse.model_json_schema())
        advance_to_player_turn(game_manager, cont_req)


# ===========================================================================
# Flow 3: Multi-NPC Conversation
# ===========================================================================

class TestMultiNpcConversation:
    def test_player_can_start_conversation_with_two_npcs(
        self,
        game_manager: GameStateManager,
        multi_npc_start_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(multi_npc_start_request)
        cont_req = _dump(example_continue_conversation_request)

        response = game_manager.start_conversation(start_req)
        jsonschema.validate(response, models.StartConversationResponse.model_json_schema())

        # NPCs should respond (may get multiple NPCTALK before PLAYERTALK)
        advance_to_player_turn(game_manager, cont_req)

    def test_player_can_talk_in_multi_npc_conversation(
        self,
        game_manager: GameStateManager,
        multi_npc_start_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(multi_npc_start_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        response = game_manager.player_input(_make_player_input("What do you both think about this place?"))
        assert response[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCTALK

    def test_multi_npc_conversation_can_be_ended_and_restarted(
        self,
        game_manager: GameStateManager,
        multi_npc_start_request: models.StartConversationRequest,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        """Regression test: ending a multi-NPC conversation and starting a new one
        must not deadlock or leave stale threads."""
        start_req = _dump(multi_npc_start_request)
        cont_req = _dump(example_continue_conversation_request)

        # Multi-NPC conversation
        setup_conversation(game_manager, start_req, cont_req)
        game_manager.player_input(_make_player_input("Goodbye."))
        _collect_npc_responses(game_manager, cont_req)
        game_manager.end_conversation(_make_end_request())

        # Start a new (single NPC) conversation — must work cleanly
        new_start_req = _dump(example_start_conversation_request)
        response = game_manager.start_conversation(new_start_req)
        jsonschema.validate(response, models.StartConversationResponse.model_json_schema())
        advance_to_player_turn(game_manager, cont_req)


# ===========================================================================
# Flow 4: Player Interrupts NPC
# ===========================================================================

class TestPlayerInterruptsNpc:
    def test_player_can_interrupt_npc_mid_response(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # First player input triggers NPC response
        game_manager.player_input(_make_player_input("Tell me a long story about dragons."))

        # Get first NPC sentence
        response = game_manager.continue_conversation(cont_req)
        assert response[comm_consts.KEY_REPLYTYPE] in (
            comm_consts.KEY_REPLYTYPE_NPCTALK,
            comm_consts.KEY_REPLYTYPE_PLAYERTALK,
        )

        # Interrupt immediately with new player input
        interrupt_response = game_manager.player_input(_make_player_input("Wait, hold on."))
        assert interrupt_response[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_NPCTALK

    def test_player_interruption_clears_remaining_npc_sentences(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        # Get NPC talking
        game_manager.player_input(_make_player_input("Tell me everything you know."))
        game_manager.continue_conversation(cont_req)

        # Interrupt — NPC should respond to the interruption, not the old topic
        game_manager.player_input(_make_player_input("Actually, never mind. What is your name?"))
        # Continue and get the new response
        advance_to_player_turn(game_manager, cont_req)


# ===========================================================================
# Flow 5: Response Validation
# ===========================================================================

class TestResponseValidation:
    def test_npc_response_contains_required_fields(
        self,
        game_manager: GameStateManager,
        example_start_conversation_request: models.StartConversationRequest,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        start_req = _dump(example_start_conversation_request)
        cont_req = _dump(example_continue_conversation_request)

        setup_conversation(game_manager, start_req, cont_req)

        response = game_manager.player_input(_make_player_input("Hello there."))
        # Validate full response against NpcTalkResponse schema
        jsonschema.validate(response, models.NpcTalkResponse.model_json_schema())

    def test_continue_conversation_without_start_returns_error(
        self,
        game_manager: GameStateManager,
        example_continue_conversation_request: models.ContinueConversationRequest,
    ):
        cont_req = _dump(example_continue_conversation_request)

        response = game_manager.continue_conversation(cont_req)
        assert response[comm_consts.KEY_REPLYTYPE] == "error"
