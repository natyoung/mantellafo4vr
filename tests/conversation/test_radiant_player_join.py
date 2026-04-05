"""Tests for player joining a radiant conversation mid-stream."""
import pytest
from unittest.mock import MagicMock
from src.conversation.context import Context
from src.conversation.conversation import Conversation
from src.conversation.conversation_type import radiant, pc_to_npc, multi_npc
from src.character_manager import Character
from src.characters_manager import Characters
from src.config.config_loader import ConfigLoader
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from src.output_manager import ChatManager


@pytest.fixture
def mock_chat_manager(default_config, llm_client) -> ChatManager:
    """ChatManager backed by a mock TTS so Piper doesn't need to be installed."""
    mock_tts = MagicMock()
    mock_tts.tts_lock = MagicMock()
    mock_tts.tts_running = False
    return ChatManager(default_config, mock_tts, llm_client)


# ---------------------------------------------------------------------------
# Context: cached_player_character
# ---------------------------------------------------------------------------

class TestCachedPlayerCharacter:

    def test_returns_none_before_any_player_added(
        self, default_config, llm_client, default_rememberer, english_language_info
    ):
        ctx = Context('world1', default_config, llm_client, default_rememberer, english_language_info)
        assert ctx.cached_player_character is None

    def test_caches_player_when_added(
        self,
        default_context: Context,
        example_skyrim_player_character: Character,
    ):
        default_context.add_or_update_characters([example_skyrim_player_character])
        assert default_context.cached_player_character is example_skyrim_player_character

    def test_cache_persists_after_player_removed(
        self,
        default_context: Context,
        example_skyrim_player_character: Character,
    ):
        default_context.add_or_update_characters([example_skyrim_player_character])
        default_context.remove_character(example_skyrim_player_character)
        # Player left the conversation but cached reference should still be available
        assert default_context.cached_player_character is example_skyrim_player_character

    def test_cache_updated_when_player_rejoins_with_new_data(
        self,
        default_context: Context,
        example_skyrim_player_character: Character,
    ):
        default_context.add_or_update_characters([example_skyrim_player_character])
        updated_player = Character(
            base_id=example_skyrim_player_character.base_id,
            ref_id=example_skyrim_player_character.ref_id,
            name='Dragonborn_Updated',
            gender=example_skyrim_player_character.gender,
            race=example_skyrim_player_character.race,
            is_player_character=True,
            bio='',
            is_in_combat=False,
            is_enemy=False,
            relationship_rank=0,
            is_generic_npc=False,
            ingame_voice_model='MaleEvenToned',
            tts_voice_model='MaleEvenToned',
            csv_in_game_voice_model='MaleEvenToned',
            advanced_voice_model='MaleEvenToned',
            voice_accent='en',
            voice_language=None,
            equipment=example_skyrim_player_character.equipment,
            custom_character_values=None,
        )
        default_context.add_or_update_characters([updated_player])
        assert default_context.cached_player_character.name == 'Dragonborn_Updated'


# ---------------------------------------------------------------------------
# Conversation: handle_radiant_player_speech
# ---------------------------------------------------------------------------

@pytest.fixture
def radiant_context(
    default_config: ConfigLoader,
    llm_client,
    default_rememberer,
    english_language_info,
    example_skyrim_player_character: Character,
    example_skyrim_npc_character: Character,
    another_example_skyrim_npc_character: Character,
):
    """Context with two NPCs (radiant) but cached player from a prior session."""
    ctx = Context('world1', default_config, llm_client, default_rememberer, english_language_info)
    # Simulate: player was seen before (normal convo), now only NPCs are active
    ctx.add_or_update_characters([example_skyrim_player_character])
    ctx.remove_character(example_skyrim_player_character)
    ctx.add_or_update_characters([example_skyrim_npc_character, another_example_skyrim_npc_character])
    return ctx


@pytest.fixture
def radiant_conversation(radiant_context: Context, mock_chat_manager, default_rememberer, llm_client):
    return Conversation(radiant_context, mock_chat_manager, default_rememberer, llm_client, None, False, False)


class TestHandleRadiantPlayerSpeech:

    def test_no_op_when_no_cached_player(
        self,
        default_context: Context,
        mock_chat_manager,
        default_rememberer,
        llm_client,
        example_skyrim_npc_character: Character,
        another_example_skyrim_npc_character: Character,
    ):
        """If there's no cached player Character, injecting speech should be a safe no-op."""
        ctx = Context('world1', default_context.config, llm_client, default_rememberer, {'alpha2': 'en', 'language': 'English', 'hello': 'Hello'})
        ctx.add_or_update_characters([example_skyrim_npc_character, another_example_skyrim_npc_character])
        conv = Conversation(ctx, mock_chat_manager, default_rememberer, llm_client, None, False, False)

        assert ctx.cached_player_character is None
        # Should not raise and should not add player to active conversation
        conv.handle_radiant_player_speech("Hello there.")
        assert not ctx.npcs_in_conversation.contains_player_character()

    def test_adds_player_to_conversation(self, radiant_conversation: Conversation, radiant_context: Context):
        assert not radiant_context.npcs_in_conversation.contains_player_character()
        radiant_conversation.handle_radiant_player_speech("Hello there.")
        assert radiant_context.npcs_in_conversation.contains_player_character()

    def test_conversation_type_switches_from_radiant(
        self, radiant_conversation: Conversation, radiant_context: Context
    ):
        # Prime the conversation type to radiant
        radiant_conversation._Conversation__conversation_type = radiant(radiant_context.config)
        radiant_conversation.handle_radiant_player_speech("Hello there.")
        assert not isinstance(radiant_conversation._Conversation__conversation_type, radiant)

    def test_player_text_appears_in_message_thread(
        self, radiant_conversation: Conversation, radiant_context: Context
    ):
        # Give the conversation a message thread to inject into
        sys_msg = radiant_context.generate_system_message(radiant_context.config.radiant_prompt, [])
        radiant_conversation._Conversation__messages = message_thread(radiant_context.config, sys_msg)

        radiant_conversation.handle_radiant_player_speech("What are you two talking about?")

        messages = radiant_conversation._Conversation__messages
        user_messages = [m for m in messages.get_talk_only(include_system_generated_messages=False) if isinstance(m, UserMessage)]
        assert any("What are you two talking about?" in m.text for m in user_messages)

    def test_player_name_prefixed_in_message(
        self, radiant_conversation: Conversation, radiant_context: Context
    ):
        sys_msg = radiant_context.generate_system_message(radiant_context.config.radiant_prompt, [])
        radiant_conversation._Conversation__messages = message_thread(radiant_context.config, sys_msg)

        radiant_conversation.handle_radiant_player_speech("What are you two talking about?")

        messages = radiant_conversation._Conversation__messages
        user_messages = [m for m in messages.get_talk_only(include_system_generated_messages=False) if isinstance(m, UserMessage)]
        player_name = radiant_context.cached_player_character.name
        assert any(player_name in m.get_formatted_content() for m in user_messages)

    def test_system_prompt_regenerated_with_player_context(
        self, radiant_conversation: Conversation, radiant_context: Context
    ):
        """After joining, the system message should reference the player."""
        sys_msg_before = radiant_context.generate_system_message(radiant_context.config.radiant_prompt, [])
        radiant_conversation._Conversation__messages = message_thread(radiant_context.config, sys_msg_before)

        radiant_conversation.handle_radiant_player_speech("Hey.")

        thread = radiant_conversation._Conversation__messages
        new_system_content = thread.get_openai_messages()[0]['content']
        player_name = radiant_context.cached_player_character.name
        assert player_name in new_system_content
