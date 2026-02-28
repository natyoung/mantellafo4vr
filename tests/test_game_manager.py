from src.game_manager import GameStateManager
import pytest
import jsonschema
from unittest.mock import MagicMock
from src.http import models
from src.http.communication_constants import communication_constants as comm_consts
from src.conversation import conversation as conv_module
from src.character_manager import Character
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum

def setup_conversation(game_manager: GameStateManager, start_request: dict, continue_request: dict):
    # Start conversation
    response = game_manager.start_conversation(start_request)
    jsonschema.validate(response, models.StartConversationResponse.model_json_schema())

    # Advance to player's turn
    advance_to_player_turn(game_manager, continue_request)

def advance_to_player_turn(game_manager: GameStateManager, continue_request: dict):
    players_turn = False
    while not players_turn:
        response = game_manager.continue_conversation(continue_request)
        if response[comm_consts.KEY_REPLYTYPE] == comm_consts.KEY_REPLYTYPE_PLAYERTALK:
            jsonschema.validate(response, models.PlayerTalkResponse.model_json_schema())
            players_turn = True
        else:
            jsonschema.validate(response, models.NpcTalkResponse.model_json_schema())


def test_reload_conversation(
        default_game_manager: GameStateManager, 
        example_start_conversation_request: models.StartConversationRequest, 
        example_continue_conversation_request: models.ContinueConversationRequest, 
        example_player_input_textbox_request: models.PlayerInputRequest,
        monkeypatch
    ):
    # Set up conversation
    setup_conversation(default_game_manager, example_start_conversation_request.model_dump(by_alias=True, exclude_none=True), example_continue_conversation_request.model_dump(by_alias=True, exclude_none=True))

    # Send player (textbox) input
    response = default_game_manager.player_input(example_player_input_textbox_request.model_dump(by_alias=True, exclude_none=True))
    jsonschema.validate(response, models.NpcTalkResponse.model_json_schema())

    advance_to_player_turn(default_game_manager, example_continue_conversation_request.model_dump(by_alias=True, exclude_none=True))
    # Send player (textbox) input once more (summaries do not trigger if a conversation is too short)
    response = default_game_manager.player_input(example_player_input_textbox_request.model_dump(by_alias=True, exclude_none=True))
    jsonschema.validate(response, models.NpcTalkResponse.model_json_schema())

    # Note the current system message
    orig_system_message = default_game_manager._GameStateManager__talk._Conversation__messages._message_thread__messages[0].text

    # Set TOKEN_LIMIT_PERCENT to 0 to simulate a reload
    monkeypatch.setattr(conv_module.Conversation, "TOKEN_LIMIT_PERCENT", 0)
    response = default_game_manager.continue_conversation(example_continue_conversation_request.model_dump(by_alias=True, exclude_none=True))
    # Assert that the response indicates to the player that a reload will occur
    assert response[comm_consts.KEY_REPLYTYPE_NPCTALK][comm_consts.KEY_ACTOR_LINETOSPEAK] == "I need to gather my thoughts for a moment"
    
    # Reset TOKEN_LIMIT_PERCENT
    monkeypatch.setattr(conv_module.Conversation, "TOKEN_LIMIT_PERCENT", 0.9)
    # The next continue should trigger the reload
    response = default_game_manager.continue_conversation(example_continue_conversation_request.model_dump(by_alias=True, exclude_none=True))
    # Once reloaded, the message list should be reset and contain only the system prompt and automatic player greeting
    new_system_message = default_game_manager._GameStateManager__talk._Conversation__messages._message_thread__messages[0].text
    assert new_system_message != orig_system_message


def test_player_action_command(
        default_game_manager: GameStateManager, 
        example_start_conversation_request: models.StartConversationRequest, 
        example_continue_conversation_request: models.ContinueConversationRequest, 
        example_player_input_textbox_action_command_request: models.PlayerInputRequest,
    ):
    ''' Test that a player action command (eg "Follow.") results in the action being returned in the response'''

    # Set up conversation
    setup_conversation(default_game_manager, example_start_conversation_request.model_dump(by_alias=True, exclude_none=True), example_continue_conversation_request.model_dump(by_alias=True, exclude_none=True))

    # Send player (textbox) input
    response = default_game_manager.player_input(example_player_input_textbox_action_command_request.model_dump(by_alias=True, exclude_none=True))
    
    # Assert that the response contains the action
    assert response[comm_consts.KEY_REPLYTYPE_NPCACTION][comm_consts.KEY_ACTOR_ACTIONS][0]['identifier'] == 'mantella_npc_follow'


def test_sentence_to_json_with_dict_actions(default_game_manager: GameStateManager, example_skyrim_npc_character: Character):
    """Test sentence_to_json extracts full action dicts"""    
    # Create a sentence with full action dicts
    actions = [
        {
            "identifier": "mantella_npc_follow",
            "arguments": {"source": ["Lydia", "Serana"]}
        }
    ]
    
    sentence_content = SentenceContent(
        speaker=example_skyrim_npc_character,
        text="Of course, we'll follow.",
        sentence_type=SentenceTypeEnum.SPEECH,
        is_system_generated_sentence=False,
        actions=actions
    )
    
    sentence = Sentence(sentence_content, "test.wav", 2.5)
    
    # Convert to JSON
    result = default_game_manager.sentence_to_json(sentence, topicID=1)
    
    assert comm_consts.KEY_ACTOR_ACTIONS in result
    assert len(result[comm_consts.KEY_ACTOR_ACTIONS]) == 1
    assert result[comm_consts.KEY_ACTOR_ACTIONS][0]["identifier"] == "mantella_npc_follow"
    assert result[comm_consts.KEY_ACTOR_ACTIONS][0]["arguments"] == {"source": ["Lydia", "Serana"]}


def test_sentence_to_json_with_multiple_dict_actions(default_game_manager: GameStateManager, example_skyrim_npc_character: Character):
    """Test sentence_to_json handles multiple action dicts correctly"""
    
    # Create a sentence with multiple full action dicts
    actions = [
        {
            "identifier": "mantella_npc_follow",
            "arguments": {"source": ["Erik"]}
        },
        {
            "identifier": "mantella_npc_inventory",
            "arguments": {"source": ["Erik"]}
        }
    ]
    
    sentence_content = SentenceContent(
        speaker=example_skyrim_npc_character,
        text="Ready for battle.",
        sentence_type=SentenceTypeEnum.SPEECH,
        is_system_generated_sentence=False,
        actions=actions
    )
    
    sentence = Sentence(sentence_content, "test.wav", 1.5)
    
    # Convert to JSON
    result = default_game_manager.sentence_to_json(sentence, topicID=1)
    
    assert comm_consts.KEY_ACTOR_ACTIONS in result
    assert len(result[comm_consts.KEY_ACTOR_ACTIONS]) == 2
    assert result[comm_consts.KEY_ACTOR_ACTIONS][0]["identifier"] == "mantella_npc_follow"
    assert result[comm_consts.KEY_ACTOR_ACTIONS][1]["identifier"] == "mantella_npc_inventory"
    assert result[comm_consts.KEY_ACTOR_ACTIONS][0]["arguments"] == {"source": ["Erik"]}
    assert result[comm_consts.KEY_ACTOR_ACTIONS][1]["arguments"] == {"source": ["Erik"]}


def test_sentence_to_json_with_empty_actions(default_game_manager: GameStateManager,example_skyrim_npc_character: Character):
    """Test sentence_to_json handles sentences with no actions correctly"""
    
    # Create a sentence with no actions
    sentence_content = SentenceContent(
        speaker=example_skyrim_npc_character,
        text="Hello there.",
        sentence_type=SentenceTypeEnum.SPEECH,
        is_system_generated_sentence=False,
        actions=[]
    )
    
    sentence = Sentence(sentence_content, "test.wav", 1.0)
    
    # Convert to JSON
    result = default_game_manager.sentence_to_json(sentence, topicID=1)
    
    assert comm_consts.KEY_ACTOR_ACTIONS in result
    assert result[comm_consts.KEY_ACTOR_ACTIONS] == []


def test_load_character_survives_talk_nulled_mid_execution():
    """load_character() must not crash if self.__talk is nulled by another thread mid-call.

    Simulates: Thread A is in load_character() checking talk.contains_character().
    Between that check and later usage of talk, Thread B calls end_conversation()
    which sets self.__talk = None. Without a local capture, this causes AttributeError.
    """
    from src.game_manager import GameStateManager

    # Build a minimal GameStateManager with all-mock dependencies
    gm = object.__new__(GameStateManager)
    gm._GameStateManager__game = MagicMock()
    gm._GameStateManager__config = MagicMock()
    gm._GameStateManager__config.voice_player_input = False

    # Set up a mock conversation
    mock_talk = MagicMock()
    mock_talk.contains_character.return_value = False  # NPC not already loaded

    # Mock load_external_character_info to return valid data
    mock_ext_info = MagicMock()
    mock_ext_info.bio = "A test NPC."
    mock_ext_info.wiki = ""
    mock_ext_info.tts_voice_model = "MaleEvenToned"
    mock_ext_info.csv_in_game_voice_model = "MaleEvenToned"
    mock_ext_info.advanced_voice_model = ""
    mock_ext_info.voice_accent = ""
    mock_ext_info.voice_language = None
    mock_ext_info.is_generic_npc = False
    mock_ext_info.name = "TestNPC"
    mock_ext_info.prompt_name = None
    mock_ext_info.ingame_voice_model = "MaleEvenToned"
    mock_ext_info.max_response_sentences = None
    gm._GameStateManager__game.load_external_character_info = MagicMock(return_value=mock_ext_info)

    # Wire: contains_character returns True (NPC already loaded), but between that check
    # and the next line (self.__talk.get_character), __talk gets nulled.
    def contains_then_null(ref_id):
        gm._GameStateManager__talk = None  # concurrent end_conversation
        return True  # triggers the get_character path

    mock_talk.contains_character.side_effect = contains_then_null
    mock_talk.get_character.return_value = None
    gm._GameStateManager__talk = mock_talk

    actor_json = {
        comm_consts.KEY_ACTOR_BASEID: "100",
        comm_consts.KEY_ACTOR_REFID: "200",
        comm_consts.KEY_ACTOR_NAME: "TestNPC",
        comm_consts.KEY_ACTOR_GENDER: 0,
        comm_consts.KEY_ACTOR_RACE: "[Race <HumanRace (00013746)>]",
        comm_consts.KEY_ACTOR_VOICETYPE: "[VTYP <MaleEvenToned (0002F7C3)>]",
        comm_consts.KEY_ACTOR_ISINCOMBAT: False,
        comm_consts.KEY_ACTOR_ISENEMY: False,
        comm_consts.KEY_ACTOR_RELATIONSHIPRANK: 0,
        comm_consts.KEY_ACTOR_ISPLAYER: False,
    }

    # Without the fix: `if self.__talk and self.__talk.contains_character(ref_id)`
    # passes (mock_talk is truthy), contains_then_null nulls __talk and returns True,
    # then `self.__talk.get_character(ref_id)` on line 492 calls None.get_character() → crash.
    # The except Exception handler catches it and returns None, but that's still wrong:
    # the character should be successfully created (just with defaults since get_character=None).
    result = gm.load_character(actor_json)
    assert isinstance(result, Character), (
        f"Expected a Character instance (local capture prevents crash), got {result!r}. "
        "load_character likely hit the except handler due to __talk being None."
    )