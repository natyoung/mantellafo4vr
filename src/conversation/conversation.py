from enum import Enum
from threading import Thread, Lock
import random
import time
from typing import Any
from src.llm.ai_client import AIClient
from src.llm.sentence_content import SentenceTypeEnum, SentenceContent
from opentelemetry import context as OpenTelemetryContext
from src.telemetry.telemetry import set_parent_context
from src.characters_manager import Characters
from src.conversation.action import Action
from src.llm.sentence_queue import SentenceQueue
from src.llm.sentence import Sentence
from src.remember.remembering import Remembering
from src.output_manager import ChatManager
from src.llm.messages import AssistantMessage, SystemMessage, UserMessage
from src.conversation.context import Context
from src.llm.message_thread import message_thread
from src.conversation.conversation_type import conversation_type, multi_npc, pc_to_npc, radiant
from src.character_manager import Character
from src.http.communication_constants import communication_constants as comm_consts
from src.stt import Transcriber
import src.utils as utils
from src.actions.function_manager import FunctionManager
from src.llm import llm_debug

logger = utils.get_logger()


class conversation_continue_type(Enum):
    NPC_TALK = 1
    PLAYER_TALK = 2
    END_CONVERSATION = 3

class Conversation:
    TOKEN_LIMIT_PERCENT: float = 0.9
    TOKEN_LIMIT_RELOAD_MESSAGES: float = 0.1
    # Whisper prompt limits (total ~224 tokens max)
    WHISPER_PROMPT_MAX_CHARS: int = 850
    DYNAMIC_VOCAB_MAX_TERMS: int = 40
    # Special marker for "still listening" status
    STILL_LISTENING_MARKER: str = "__STILL_LISTENING__"
    """Controls the flow of a conversation."""
    def __init__(self, context_for_conversation: Context, output_manager: ChatManager, rememberer: Remembering, llm_client: AIClient, stt: Transcriber | None, mic_input: bool, mic_ptt: bool, game = None) -> None:
        
        self.__context: Context = context_for_conversation
        self.__game = game
        self.__mic_input: bool = mic_input
        self.__mic_ptt: bool = mic_ptt
        self.__allow_interruption: bool = context_for_conversation.config.allow_interruption # allow mic interruption
        logger.debug(f'Conversation initialized with allow_interruption={self.__allow_interruption}')
        self.__is_player_interrupting = False
        self.__stt: Transcriber | None = stt
        self.__events_refresh_time: float = context_for_conversation.config.events_refresh_time  # Time in seconds before events are considered stale
        self.__transcribed_text: str | None = None
        
        # Silence auto-response settings
        self.__silence_auto_response_enabled: bool = context_for_conversation.config.silence_auto_response_enabled
        self.__silence_auto_response_timeout: float = context_for_conversation.config.silence_auto_response_timeout
        self.__silence_auto_response_message: str = context_for_conversation.config.silence_auto_response_message
        self.__silence_auto_response_max_count: int = context_for_conversation.config.silence_auto_response_max_count
        self.__silence_auto_response_count: int = 0  # Track consecutive silent responses
        
        # Listening poll settings - for notifying game that we're still waiting for input
        self.__listening_poll_interval: float = 5.0  # Seconds between "still listening" notifications
        self.__listening_attempt: int = 0  # Track how many times we've polled
        
        if not self.__context.npcs_in_conversation.contains_player_character(): # TODO: fix this being set to a radiant conversation because of NPCs in conversation not yet being added
            self.__conversation_type: conversation_type = radiant(context_for_conversation.config)
        else:
            self.__conversation_type: conversation_type = pc_to_npc(context_for_conversation.config)        
        self.__messages: message_thread = message_thread(self.__context.config, None)
        self.__output_manager: ChatManager = output_manager
        self.__rememberer: Remembering = rememberer
        self.__llm_client: AIClient = llm_client
        self.__has_already_ended: bool = False
        self.__allow_mic_input: bool = True # this flag ensures mic input is disabled on conversation end
        self.__sentences: SentenceQueue = SentenceQueue()
        self.__generation_thread: Thread | None = None
        self.__generation_start_lock: Lock = Lock()
        
        # Set up Listen action callback to apply extended pause to STT
        if stt:
            self.__output_manager.set_on_listen_requested(lambda pause_secs: stt.set_temporary_pause(pause_secs))
        
        # self.__actions: list[Action] = actions
        self.last_sentence_audio_length = 0
        self.last_sentence_start_time = time.time()
        self.__end_conversation_keywords = utils.parse_keywords(context_for_conversation.config.end_conversation_keyword)
        self.__awaiting_action_result: bool = False
        self.__awaiting_action_since: float = 0
        self.__dynamic_vocab: str = ""  # LLM-generated vocabulary for Whisper
        self.__waiting_for_game_context: bool = False  # Delay LLM until game context arrives (for two-way communication)

        # Location event debounce: suppress rapid-fire "Current location is now X"
        # events from cell boundary crossings while walking
        self.__last_location_event_time: float = 0.0
        self.__last_location_name: str = ""

        # Crash-safe conversation DB persistence
        self.__conversation_db = getattr(game, 'conversation_db', None) if game else None
        self.__conversation_id: str | None = None
        self.__db_saved_msg_count: int = 0
        if self.__conversation_db:
            self.__conversation_id = self.__conversation_db.start_conversation(
                context_for_conversation.world_id
            )

    @property
    def has_already_ended(self) -> bool:
        return self.__has_already_ended
    
    @property
    def context(self) -> Context:
        return self.__context
    
    @property
    def output_manager(self) -> ChatManager:
        return self.__output_manager
    
    @property
    def transcribed_text(self) -> str | None:
        return self.__transcribed_text
    
    @property
    def stt(self) -> Transcriber | None:
        return self.__stt
    
    @property
    def waiting_for_game_context(self) -> bool:
        return self.__waiting_for_game_context

    @property
    def listening_attempt(self) -> int:
        return self.__listening_attempt

    @waiting_for_game_context.setter
    def waiting_for_game_context(self, value: bool):
        self.__waiting_for_game_context = value
    
    def __persist_new_messages(self):
        """Persist any new talk messages to the conversation DB for crash recovery."""
        if not self.__conversation_db or not self.__conversation_id:
            return
        try:
            talk_messages = self.__messages.get_talk_only(include_system_generated_messages=True)
            new_messages = talk_messages[self.__db_saved_msg_count:]
            if not new_messages:
                return

            world_id = self.__context.world_id
            npcs = [c for c in self.__context.npcs_in_conversation.get_all_characters() if not c.is_player_character]
            if not npcs:
                return

            is_multi_npc = len(npcs) > 1
            for msg in new_messages:
                if isinstance(msg, UserMessage):
                    role = "user"
                    content = msg.text
                    is_sys = msg.is_system_generated_message
                elif isinstance(msg, AssistantMessage):
                    role = "assistant"
                    content = msg.get_formatted_content()
                    is_sys = msg.is_system_generated_message
                else:
                    continue

                if not content or not content.strip():
                    continue

                if is_multi_npc and role == "assistant":
                    # Multi-NPC assistant messages contain all speakers' lines with
                    # "Name:" prefixes. Save once under the first NPC to avoid duplication.
                    npc = npcs[0]
                    base_name = utils.remove_trailing_number(npc.name)
                    self.__conversation_db.save_message(
                        self.__conversation_id, world_id, base_name, npc.ref_id,
                        role, content, is_system_generated=is_sys
                    )
                else:
                    for npc in npcs:
                        base_name = utils.remove_trailing_number(npc.name)
                        self.__conversation_db.save_message(
                            self.__conversation_id, world_id, base_name, npc.ref_id,
                            role, content, is_system_generated=is_sys
                        )

            self.__db_saved_msg_count = len(talk_messages)
        except Exception as e:
            logger.warning(f"Failed to persist messages to DB: {e}")

    @utils.time_it
    def add_or_update_character(self, new_character: list[Character]):
        """Adds or updates a character in the conversation.

        Args:
            new_character (Character): the character to add or update
        """
        characters_removed_by_update = self.__context.add_or_update_characters(new_character)
        if len(characters_removed_by_update) > 0:
            all_characters = self.__context.npcs_in_conversation.get_all_characters()
            all_characters.extend(characters_removed_by_update)
            # Run save in background to avoid blocking the game with LLM summary calls
            import threading
            from copy import deepcopy
            save_chars = list(all_characters)  # snapshot
            save_messages = deepcopy(self.__messages)  # deep copy to avoid race with main thread
            save_world_id = self.__context.world_id
            save_rememberer = self.__rememberer
            def _bg_save():
                try:
                    characters_object = Characters()
                    for npc in save_chars:
                        characters_object.add_or_update_character(npc)
                    save_rememberer.save_conversation_state(save_messages, characters_object, save_world_id, True, None, None)
                except Exception as e:
                    logger.error(f"Background conversation save failed: {e}")
            threading.Thread(target=_bg_save, daemon=True, name="conv_save_bg").start()
        
        # Set LLM debug log path to the character's conversation folder
        self.__update_llm_debug_path()
    
    def __update_llm_debug_path(self):
        """Update LLM debug log path to current character's conversation folder."""
        npc = self.__context.npcs_in_conversation.last_added_character
        if npc and not npc.is_player_character:
            from src.conversation.conversation_log import conversation_log
            from pathlib import Path
            base_name = utils.remove_trailing_number(npc.name)
            folder_name = f'{base_name} - {npc.ref_id}'
            folder_path = Path(conversation_log.game_path) / self.__context.world_id / folder_name
            llm_debug.set_log_folder(folder_path)

    @utils.time_it
    def start_conversation(self) -> tuple[str, Sentence | None]:
        """Starts a new conversation.

        Returns:
            tuple[str, sentence | None]: Returns a tuple consisting of a reply type and an optional sentence
        """
        greeting: UserMessage | None = self.__conversation_type.get_user_message(self.__context, self.__messages)
        if greeting:
            greeting = self.update_game_events(greeting)
            self.__messages.add_message(greeting)
            self.__persist_new_messages()
            # If waiting for game context (two-way communication), delay LLM call
            if self.__waiting_for_game_context:
                logger.info("Delaying LLM call - waiting for game context from Papyrus/simulator")
                return comm_consts.KEY_REPLYTYPE_NPCTALK, None
            self.__start_generating_npc_sentences()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, None
        else:
            return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None
    
    def start_generation_after_context(self):
        """Start LLM generation after game context has been received.
        
        Called when two-way communication completes and game context is available.
        """
        if self.__waiting_for_game_context:
            logger.info("Game context received - starting LLM generation now")
            self.__waiting_for_game_context = False
            # Update the prompt with new game context
            self.__update_conversation_type()
            self.__start_generating_npc_sentences()
            return True
        return False

    @utils.time_it
    def continue_conversation(self) -> tuple[str, Sentence | None]:
        """Main workhorse of the conversation. Decides what happens next based on the state of the conversation

        Returns:
            tuple[str, sentence | None]: Returns a tuple consisting of a reply type and an optional sentence
        """
        if self.__llm_client.is_too_long(self.__messages, self.TOKEN_LIMIT_PERCENT):
            # Check if conversation too long and if yes initiate intermittent reload
            self.__initiate_reload_conversation()

        # interrupt response if player has spoken
        if self.__stt and self.__stt.has_player_spoken:
            self.__stop_generation()
            self.__sentences.clear()
            self.__is_player_interrupting = True
            return comm_consts.KEY_REQUESTTYPE_TTS, None
        
        # restart mic listening as soon as NPC's first sentence is processed
        if self.__mic_input and self.__allow_interruption and not self.__mic_ptt and not self.__stt.is_listening and self.__allow_mic_input:
            # Wait for current NPC audio to finish playing to avoid mic picking up speaker audio
            time_elapsed = time.time() - self.last_sentence_start_time
            remaining_audio_time = self.last_sentence_audio_length - time_elapsed
            if remaining_audio_time > 0:
                logger.debug(f'[Interruption Mode] Waiting {round(remaining_audio_time, 1)} seconds for NPC audio to finish before starting interruption listening')
                time.sleep(remaining_audio_time)
            
            mic_prompt = self.__get_mic_prompt()
            self.__stt.start_listening(mic_prompt)
        
        #Grab the next sentence from the queue
        next_sentence: Sentence | None = self.retrieve_sentence_from_queue()
        
        # If conversation has ended and no more sentences, return end conversation
        if self.has_already_ended and not next_sentence:
            return comm_consts.KEY_REPLYTYPE_ENDCONVERSATION, None
        
        # Check if this is an action-only sentence (no text, but has actions)
        if next_sentence and len(next_sentence.text.strip()) == 0 and len(next_sentence.actions) > 0:
            if FunctionManager.any_action_requires_response(next_sentence.actions):
                self.__awaiting_action_result = True
                self.__awaiting_action_since = time.time()
            return comm_consts.KEY_REPLYTYPE_NPCACTION, next_sentence
        elif next_sentence and len(next_sentence.text) > 0:
            # Stop mic if it's listening (player's turn ended, NPC is talking now)
            if self.__stt and self.__stt.is_listening and not self.__allow_interruption:
                self.__stt.stop_listening()
            if {'identifier': comm_consts.ACTION_REMOVECHARACTER} in next_sentence.actions:
                self.__context.remove_character(next_sentence.speaker)
            # Before sending next voiceline, give the player the chance to interrupt
            while time.time() - self.last_sentence_start_time < self.last_sentence_audio_length:
                if self.__stt and self.__stt.has_player_spoken:
                    if isinstance(self.__conversation_type, radiant):
                        # Player spoke during radiant — inject their reply inline
                        transcription = self.__stt.get_latest_transcription(silence_timeout=3)
                        self.__stt.stop_listening()
                        if transcription and transcription.strip():
                            self.__stop_generation()
                            self.__sentences.clear()
                            player_name = self.__context.last_known_player_name
                            player_msg = UserMessage(self.__context.config, transcription.strip(), player_name, False)
                            player_msg.is_multi_npc_message = True
                            self.__messages.add_message(player_msg)
                            self.__persist_new_messages()
                            self.__start_generating_npc_sentences()
                        break
                    else:
                        self.__stop_generation()
                        self.__sentences.clear()
                        self.__is_player_interrupting = True
                        return comm_consts.KEY_REQUESTTYPE_TTS, None
                time.sleep(0.01)
            self.last_sentence_audio_length = next_sentence.voice_line_duration + self.__context.config.wait_time_buffer
            self.last_sentence_start_time = time.time()
            return comm_consts.KEY_REPLYTYPE_NPCTALK, next_sentence
        else:
            self.__persist_new_messages()  # NPC response complete — persist to DB
            # Check if end conversation was requested via tool call
            if self.__output_manager.end_conversation_requested:
                self.__output_manager.clear_end_conversation_requested()
                self.initiate_end_sequence()
                return comm_consts.KEY_REPLYTYPE_NPCTALK, None
            #Ask the conversation type here, if we should end the conversation
            if self.__conversation_type.should_end(self.__context, self.__messages):
                self.initiate_end_sequence()
                return comm_consts.KEY_REPLYTYPE_NPCTALK, None
            else:
                #If not ended, ask the conversation type for an automatic user message. If there is None, signal the game that the player must provide it 
                new_user_message = self.__conversation_type.get_user_message(self.__context, self.__messages)
                if new_user_message:
                    self.__messages.add_message(new_user_message)
                    self.__persist_new_messages()  # Radiant auto-message — persist to DB
                    self.__start_generating_npc_sentences()
                    return comm_consts.KEY_REPLYTYPE_NPCTALK, None
                else:
                    # Wait for the last NPC sentence to finish playing before allowing player input
                    if not self.__allow_interruption:
                        remaining = self.last_sentence_audio_length - (time.time() - self.last_sentence_start_time)
                        if remaining > 0:
                            logger.debug(f'Waiting {round(remaining, 1)}s for NPC audio before mic')
                            time.sleep(remaining)
                    return comm_consts.KEY_REPLYTYPE_PLAYERTALK, None

    @utils.time_it
    def process_player_input(self, player_text: str) -> tuple[str, bool, Sentence|None]:
        """Submit the input of the player to the conversation

        Args:
            player_text (str): The input text / voice transcribe of what the player character is supposed to say. Can be empty if mic input has not yet been parsed

        Returns:
            tuple[str, bool]: Returns a tuple consisting of updated player text (if using mic input) and whether or not in-game events need to be refreshed (depending on how much time has passed)
        """
        player_character = self.__context.npcs_in_conversation.get_player_character()
        if not player_character:
            return '', False, None # If there is no player in the conversation, exit here
        
        events_need_updating: bool = False
        is_silence_timeout: bool = False
        player_voiceline = None

        with self.__generation_start_lock: #This lock makes sure no new generation by the LLM is started while we clear this
            # For mic input with empty text (entering STT mode), don't kill NPC generation yet.
            # The player hasn't spoken — wait for actual speech before interrupting the NPC.
            is_entering_stt = self.__mic_input and len(player_text) == 0

            if not is_entering_stt:
                self.__stop_generation() # Stop generation of additional sentences right now
                self.__sentences.clear() # Clear any remaining sentences from the list

            # If the player's input does not already exist, parse mic input if mic is enabled
            if is_entering_stt:
                player_text = None
                
                if not self.__allow_mic_input:
                    return '', False, None
                
                listen_mode_active = self.__output_manager.listen_requested
                if listen_mode_active:
                    self.__output_manager.clear_listen_requested()
                
                if not self.__stt.is_listening:
                    # Wait for NPC's audio to finish to avoid mic picking up speaker
                    remaining = self.last_sentence_audio_length - (time.time() - self.last_sentence_start_time)
                    if remaining > 0:
                        logger.debug(f'Waiting {round(remaining, 1)}s for NPC audio before mic')
                        time.sleep(remaining)
                    self.__stt.start_listening(self.__get_mic_prompt())
                
                # Use timeout if enabled, max count not reached, and Listen mode not active
                use_silence_timeout = (self.__silence_auto_response_enabled and
                                       self.__silence_auto_response_count < self.__silence_auto_response_max_count and
                                       not listen_mode_active)
                silence_timeout = self.__silence_auto_response_timeout if use_silence_timeout else 0
                
                input_wait_start_time = time.time()
                while not player_text:
                    player_text = self.__stt.get_latest_transcription(silence_timeout=silence_timeout)
                    
                    # Handle silence timeout (None returned)
                    if player_text is None:
                        self.__silence_auto_response_count += 1
                        logger.log(23, f"Player silent for {self.__silence_auto_response_timeout} seconds. Auto-response count: {self.__silence_auto_response_count}/{self.__silence_auto_response_max_count}")
                        is_silence_timeout = True
                        player_text = ""  # No actual player input

                        # If max count reached, log that auto-response is now disabled
                        if self.__silence_auto_response_count >= self.__silence_auto_response_max_count:
                            logger.log(23, f"Max consecutive silence count ({self.__silence_auto_response_max_count}) reached. Auto-response disabled until player speaks")
                        break
                    elif player_text:
                        # Player spoke -> reset the silence counter
                        self.__silence_auto_response_count = 0

                # Stop listening once input detected to give NPC a chance to speak
                self.__stt.stop_listening()

                # NOW stop NPC generation — player has actually spoken (or silence timed out)
                logger.debug('Player STT complete, stopping NPC generation')
                self.__stop_generation()
                self.__sentences.clear()

                if not is_silence_timeout and time.time() - input_wait_start_time >= self.__events_refresh_time:
                    # If too much time has passed, in-game events need to be updated
                    events_need_updating = True
                    logger.debug('Updating game events...')
                    return player_text, events_need_updating, None

            if is_silence_timeout:
                # Player was silent — add a subtle nudge so the NPC continues naturally.
                # Without a user message the LLM returns empty (nothing new to respond to).
                silence_msg = self.__silence_auto_response_message
                logger.log(23, f"Silence timeout: sending '{silence_msg}' to prompt NPC continuation")
                new_message = UserMessage(self.__context.config, silence_msg, player_character.name, True)
                new_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
                new_message = self.update_game_events(new_message)
                self.__messages.add_message(new_message)
                text = silence_msg
            else:
                new_message: UserMessage = UserMessage(self.__context.config, player_text, player_character.name, False)
                new_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
                new_message = self.update_game_events(new_message)
                self.__messages.add_message(new_message)
                player_voiceline = self.__get_player_voiceline(player_character, player_text)
                text = new_message.text
                logger.log(23, f"Text passed to NPC: {text}")

                llm_debug.log_player_transcript(player_text, self.__stt.prompt if self.__stt else None)

        ejected_npc = self.__does_dismiss_npc_from_conversation(text) if not is_silence_timeout else None
        if ejected_npc:
            self.__prepare_eject_npc_from_conversation(ejected_npc)
        elif not is_silence_timeout and self.__has_conversation_ended(text):
            new_message.is_system_generated_message = True # Flag message containing goodbye as a system message to exclude from summary
            self.initiate_end_sequence()
        else:
            # Enable vision on silence timeout or player request
            if self.__llm_client and self.__context.config.vision_enabled:
                if is_silence_timeout or self.__is_vision_request(text):
                    self.__llm_client.enable_vision_for_next_call()
            self.__start_generating_npc_sentences()
        self.__persist_new_messages()  # Persist after flags are set (goodbye, summary recall, etc.)

        return player_text, events_need_updating, player_voiceline

    def __get_mic_prompt(self) -> str:
        """Generate a context-aware prompt for Whisper transcription.
        
        Whisper works best with a simple vocabulary list under 900 chars (224 tokens).
        No speaker labels, no quotes, no conversation history - just comma-separated terms.
        """
        config = self.__context.config
        prompt_names = self.__context.npcs_in_conversation.get_all_prompt_names(include_player=False)
        npc_names_str = ", ".join(prompt_names) if prompt_names else "NPC"

        # Use dynamic vocab if available (already includes static + conversation terms)
        # Otherwise fall back to static vocab from config
        if self.__dynamic_vocab:
            vocab = self.__dynamic_vocab
        else:
            # Static vocab from config (only used before first LLM response)
            vocab = config.stt_prompt

        # Build final prompt: all NPC names, location, vocabulary
        prompt = f"{npc_names_str}, {self.__context.location}. {vocab}"
        
        # Ensure under Whisper's limit (~224 tokens)
        if len(prompt) > self.WHISPER_PROMPT_MAX_CHARS:
            prompt = prompt[:self.WHISPER_PROMPT_MAX_CHARS].rsplit(",", 1)[0]
        
        return prompt
    
    def update_dynamic_vocab(self):
        """Update dynamic vocabulary in background thread.
        
        Extracts key terms from conversation context to improve Whisper recognition.
        Called after full LLM response is complete.
        """
        Thread(target=self.__extract_dynamic_vocab, daemon=True).start()
    
    def __extract_dynamic_vocab(self):
        """Use LLM to combine static vocab with conversation-specific terms."""
        try:
            # Get recent conversation for context
            recent_messages = self.__messages.get_last_n_messages(6)
            conversation_text = ""
            for msg in recent_messages:
                if isinstance(msg, SystemMessage):
                    continue
                content = msg.get_formatted_content() if isinstance(msg, AssistantMessage) else msg.text
                if content:
                    conversation_text += f"{content}\n"
            
            if not conversation_text.strip():
                return
            
            # Get static vocab from config
            static_vocab = self.__context.config.stt_prompt
            
            # Add NPC names and location
            npc_names = self.__context.npcs_in_conversation.get_all_prompt_names(include_player=False)
            context_terms = ", ".join(npc_names) + ", " + self.__context.location
            
            # Build prompt: combine existing vocab with conversation
            prompt = f"""
            Combine this vocabulary with terms from the conversation.
            Keep important existing terms, add new names/locations/items from conversation.
            Return ONLY comma-separated terms. Max 40 total.

            Current vocab: {context_terms}, {static_vocab}

            Conversation:
            {conversation_text}

            Combined vocab:"""
                                    
            # Use function_client (smaller/faster model) if available
            client = self.__llm_client.function_client or self.__llm_client
            vocab_message = UserMessage(self.__context.config, prompt, "system", True)
            response = client.request_call(vocab_message)
            
            if response:
                self.__dynamic_vocab = response.strip().replace("\n", ", ")
                logger.debug(f"LLM vocab: {self.__dynamic_vocab[:100]}...")
            
            llm_debug.log_dynamic_vocab(prompt, self.__dynamic_vocab if response else None)
            
        except Exception as e:
            logger.warning(f"Failed to generate dynamic vocab: {e}")
    
    @utils.time_it
    def __get_player_voiceline(self, player_character: Character | None, player_text: str) -> Sentence | None:
        """Synthesizes the player's input if player voice input is enabled, or else returns None
        """
        player_character_voiced_sentence: Sentence | None = None
        if self.__should_voice_player_input(player_character):
            player_character_voiced_sentence = self.__output_manager.generate_sentence(SentenceContent(player_character, player_text, SentenceTypeEnum.SPEECH, False))
            if player_character_voiced_sentence.error_message:
                player_message_content: SentenceContent = SentenceContent(player_character, player_text, SentenceTypeEnum.SPEECH, False)
                player_character_voiced_sentence = Sentence(player_message_content, "" , 2.0)

        return player_character_voiced_sentence

    @utils.time_it
    def update_context(self, location: str | None, time: int, custom_ingame_events: list[str] | None, weather: str | None, npcs_nearby: list[dict[str, Any]] | None, custom_context_values: dict[str, Any] | None, config_settings: dict[str, Any] | None, game_days: float | None = None):
        """Updates the context with a new set of values

        Args:
            location (str): the location the characters are currently in
            time (int): the current ingame time
            custom_ingame_events (list[str]): a list of events that happend since the last update
            custom_context_values (dict[str, Any]): the current set of context values
            game_days (float): the full game timestamp (days.fraction)
        """
        logger.debug(f"conversation.update_context called, custom_context_values keys: {list(custom_context_values.keys()) if custom_context_values else []}")
        self.__context.update_context(location, time, custom_ingame_events, weather, npcs_nearby, custom_context_values, config_settings, game_days)
        logger.debug(f"After context.update_context: actors_changed={self.__context.have_actors_changed}, game_context_changed={self.__context.game_context_changed}")
        if self.__context.have_actors_changed or self.__context.game_context_changed:
            npc_count = sum(1 for c in self.__context.npcs_in_conversation.get_all_characters() if not c.is_player_character)
            if npc_count > 0:
                logger.info(f"Regenerating prompt (actors_changed={self.__context.have_actors_changed}, game_context_changed={self.__context.game_context_changed})")
                self.__update_conversation_type()
            else:
                logger.info("Skipping prompt regeneration - no NPC characters present yet")
            self.__context.have_actors_changed = False
            self.__context.game_context_changed = False

    @utils.time_it
    def __update_conversation_type(self):
        """This changes between pc_to_npc, multi_npc and radiant conversation_types based on the current state of the context
        """
        # If the conversation can proceed for the first time, it starts and we add the system_message with the prompt
        if not self.__has_already_ended:
            self.__stop_generation()
            self.__sentences.clear()
            
            if not self.__context.npcs_in_conversation.contains_player_character():
                self.__conversation_type = radiant(self.__context.config)
            elif self.__context.npcs_in_conversation.active_character_count() >= 3:
                self.__conversation_type = multi_npc(self.__context.config)
            else:
                self.__conversation_type = pc_to_npc(self.__context.config)

            logger.info(f"Generating new prompt with conversation_type={type(self.__conversation_type).__name__}")
            new_prompt = self.__conversation_type.generate_prompt(self.__context)
            # Check if game_context is in the generated prompt
            has_game_context = "<game_context>" in new_prompt
            logger.info(f"Generated prompt has game_context: {has_game_context}, prompt length: {len(new_prompt)}")
            
            if len(self.__messages) == 0:
                self.__messages: message_thread = message_thread(self.__context.config, new_prompt)
            else:
                self.__conversation_type.adjust_existing_message_thread(new_prompt, self.__messages)
                self.__messages.reload_message_thread(new_prompt, self.__llm_client.is_too_long, self.TOKEN_LIMIT_RELOAD_MESSAGES)

            # For radiant conversations, auto-inject a continue message and start generation
            # so the conversation doesn't stall waiting for player input that will never come
            if isinstance(self.__conversation_type, radiant):
                new_user_message = self.__conversation_type.get_user_message(self.__context, self.__messages)
                if not new_user_message:
                    # Force a continue prompt if get_user_message returns None due to even message count
                    new_user_message = UserMessage(self.__context.config, self.__context.config.radiant_continue_prompt, "", True)
                    new_user_message.is_multi_npc_message = False
                self.__messages.add_message(new_user_message)
                self.__persist_new_messages()
                self.__start_generating_npc_sentences()
                logger.info("Radiant: auto-started generation after prompt regeneration")
        else:
            logger.warning("__update_conversation_type skipped: conversation has already ended")

    @utils.time_it
    def update_game_events(self, message: UserMessage) -> UserMessage:
        """Add in-game events to player's response"""

        all_ingame_events = self.__context.get_context_ingame_events()
        if self.__is_player_interrupting:
            all_ingame_events.append('Interrupting...')
            self.__is_player_interrupting = False
        
        # Filter and merge events before sending to LLM
        filtered_events = self.__filter_and_merge_events(all_ingame_events)
        
        max_events = min(len(filtered_events), self.__context.config.max_count_events)
        message.add_event(filtered_events[-max_events:])
        self.__context.clear_context_ingame_events()        

        if message.count_ingame_events() > 0:            
            logger.log(28, f'In-game events since previous exchange:\n{message.get_ingame_events_text()}')

        return message
    
    def __filter_and_merge_events(self, events: list[str]) -> list[str]:
        """Filter out minor events and merge repeated events within time window
        
        Args:
            events: Raw list of in-game events (ordered chronologically)
            
        Returns:
            Filtered and merged list of events
        """
        if not events:
            return []

        # Events to always discard (substring match, case-insensitive)
        always_discard = [
            'picked up', 'dropped', 'equipped', 'unequipped',
            'overencumbered', 'irradiated', 'radiation exposure',
            'sneaking', 'interacting with',
            'stood up from', 'rested on',
            'in power armor', 'searching for',
            'is crippled',
        ]

        # Events to always keep (substring match) — overrides always_discard
        always_keep = [
            'entered combat', 'has entered combat',
            'no longer in combat',
            'hit the player', 'hit piper',
            'attacking', 'attacked',
            'died', 'killed',
            'quest',
        ]

        LOCATION_DEBOUNCE_SECS = 30.0
        now = time.time()

        # Filter events
        filtered_events = []
        for event in events:
            if not event or not event.strip():
                continue
            event_lower = event.lower()

            # Debounce location change events (cell boundary noise while walking)
            if event_lower.startswith('current location is now'):
                location_name = event_lower.replace('current location is now', '').strip().rstrip('.')
                if (location_name == self.__last_location_name
                        or now - self.__last_location_event_time < LOCATION_DEBOUNCE_SECS):
                    continue
                self.__last_location_event_time = now
                self.__last_location_name = location_name
                filtered_events.append(event)
                continue

            should_keep = any(kw in event_lower for kw in always_keep)
            should_discard = any(kw in event_lower for kw in always_discard)

            if should_keep or not should_discard:
                filtered_events.append(event)
        
        # Smart merging: merge consecutive identical events, keep separated ones
        # Events are chronological, so we process them in order
        merged_events = []
        i = 0
        while i < len(filtered_events):
            current_event = filtered_events[i]
            count = 1
            
            # Count consecutive identical events (within the time window)
            j = i + 1
            while j < len(filtered_events) and filtered_events[j] == current_event:
                count += 1
                j += 1
            
            # Add event (with count if repeated consecutively)
            if count > 1:
                merged_events.append(f"{current_event} (x{count})")
            else:
                merged_events.append(current_event)
            
            i = j  # Skip to next different event
        
        return merged_events

    @utils.time_it
    def resume_after_interrupting_action(self) -> bool:
        """Inject a synthetic user message once action results arrive so the LLM can continue
        
        Returns:
            bool: True if conversation was resumed, False if no action was awaiting or no events available
        """
        if not self.__awaiting_action_result:
            return False

        pending_events = self.__context.get_context_ingame_events()
        if not pending_events:
            if time.time() - self.__awaiting_action_since > 30:
                logger.warning("Awaiting action result timed out after 30s, resuming conversation")
                self.__awaiting_action_result = False
            return False

        # Add synthetic user message containing just the new in-game events
        player_character = self.__context.npcs_in_conversation.get_player_character()
        player_name = player_character.name if player_character else ""
        synthetic_message = UserMessage(self.__context.config, "", player_name, True)
        synthetic_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
        synthetic_message = self.update_game_events(synthetic_message)
        self.__messages.add_message(synthetic_message)

        self.__sentences.clear()
        self.__awaiting_action_result = False
        # Do not allow the LLM to use tools a second time in a row (can cause an endless loop)
        self.__start_generating_npc_sentences(allow_tool_use=False)
        
        return True

    @utils.time_it
    def retrieve_sentence_from_queue(self) -> Sentence | None:
        """Retrieves the next sentence from the queue.
        If there is a sentence, adds the sentence to the last assistant_message of the message_thread.
        If the last message is not an assistant_message, a new one will be added.

        Returns:
            sentence | None: The next sentence from the queue or None if the queue is empty
        """
        next_sentence: Sentence | None = self.__sentences.get_next_sentence() #This is a blocking call. Execution will wait here until queue is filled again
        if not next_sentence:
            return None
        
        if not next_sentence.is_system_generated_sentence and not next_sentence.speaker.is_player_character:
            last_message = self.__messages.get_last_message()
            if not isinstance(last_message, AssistantMessage):
                last_message = AssistantMessage(self.__context.config)
                last_message.is_multi_npc_message = self.__context.npcs_in_conversation.contains_multiple_npcs()
                self.__messages.add_message(last_message)
            last_message.add_sentence(next_sentence)
        return next_sentence
   
    @utils.time_it
    def initiate_end_sequence(self):
        """Replaces all remaining sentences with a "goodbye" sentence that also prompts the game to request a stop to the conversation using an action
        """
        if not self.__has_already_ended:
            config = self.__context.config            
            self.__stop_generation()
            self.__sentences.clear()
            if self.__stt:
                self.__stt.stop_listening()
                self.__allow_mic_input = False
            # say goodbyes (pick random from comma-separated list)
            npc = self.__context.npcs_in_conversation.last_added_character
            if npc:
                goodbyes = [g.strip() for g in config.goodbye_npc_response.split(",") if g.strip()]
                goodbye_text = random.choice(goodbyes) if goodbyes else config.goodbye_npc_response
                goodbye_sentence = self.__output_manager.generate_sentence(SentenceContent(npc, goodbye_text, SentenceTypeEnum.SPEECH, True))
                if goodbye_sentence:
                    goodbye_sentence.actions.append({'identifier': comm_consts.ACTION_ENDCONVERSATION})
                    self.__sentences.put(goodbye_sentence)
            # Mark conversation as ended to prevent further continue_conversation calls
            self.__has_already_ended = True
                    
    @utils.time_it
    def contains_character(self, ref_id: str) -> bool:
        for actor in self.__context.npcs_in_conversation.get_all_characters():
            if actor.ref_id == ref_id:
                return True
        return False
    
    @utils.time_it
    def get_character(self, ref_id: str) -> Character | None:
        for actor in self.__context.npcs_in_conversation.get_all_characters():
            if actor.ref_id == ref_id:
                return actor
        return None

    @utils.time_it
    def handle_summary_recall(self) -> Sentence | None:
        """Generate a throwaway first-person summary for the NPC to speak aloud.

        Stops current LLM generation, marks the triggering message as system-generated
        (excluded from summaries), retrieves past summaries from DB, and uses LLM to
        create a short first-person recap. The response is NOT added to the message thread.
        """
        self.__stop_generation()
        self.__sentences.clear()

        # Mark the triggering "summary"/"recap" message as system-generated
        # so it won't appear in conversation summaries
        last_msg = self.__messages.get_last_user_message()
        if last_msg:
            last_msg.is_system_generated_message = True
        # Also update the already-persisted DB record
        if self.__conversation_db and self.__conversation_id:
            self.__conversation_db.mark_last_user_message_system_generated(self.__conversation_id)

        npc = self.__context.npcs_in_conversation.last_added_character
        if not npc or npc.is_player_character:
            return None

        # Retrieve summaries from DB
        db = getattr(self.__game, 'conversation_db', None) if self.__game else None
        if not db:
            return None

        base_name = utils.remove_trailing_number(npc.name)
        summaries_list = db.get_all_summaries(self.__context.world_id, base_name, npc.ref_id)

        if not summaries_list:
            no_history = "I don't think we've spoken before."
            return self.__output_manager.generate_sentence(
                SentenceContent(npc, no_history, SentenceTypeEnum.SPEECH, True)
            )

        # Combine summaries and call LLM for a short first-person recap
        summaries_text = "\n".join(s["content"] for s in summaries_list)
        prompt_name = npc.prompt_name if npc.prompt_name else npc.name
        system_prompt = (
            f"You are {prompt_name} recalling memories from your personal diary. "
            f"Stay fully in character as {prompt_name} — use their voice, personality, and mannerisms. "
            f"Reminisce naturally about the key moments below in 2-3 short sentences. "
            f"Speak casually like you're sharing memories with a friend, not reading a report. "
            f"This will be spoken aloud — no lists, no narration, no stage directions."
        )

        recall_thread = message_thread(self.__context.config, system_prompt)
        recall_thread.add_message(UserMessage(self.__context.config, summaries_text))

        # Use configured model for recall, or fall back to main conversation model
        recall_model = self.__context.config.summary_recall_model or None
        recall_text = self.__llm_client.request_call(recall_thread, model_override=recall_model)
        if not recall_text:
            recall_text = "I know we've talked before, but I can't quite remember the details."

        logger.log(23, f"Summary recall for {prompt_name}: {recall_text}")
        return self.__output_manager.generate_sentence(
            SentenceContent(npc, recall_text, SentenceTypeEnum.SPEECH, True)
        )

    @utils.time_it
    def end(self, end_timestamp: float | None = None, async_save: bool = False):
        """Ends a conversation

        Args:
            end_timestamp: Optional game timestamp (days passed as float) when conversation ends
            async_save: If True, run conversation save in a background thread (avoids blocking start_conversation)
        """
        self.__has_already_ended = True
        self.__stop_generation()
        self.__sentences.clear()
        self.__persist_new_messages()  # Final persist before ending
        if self.__conversation_db and self.__conversation_id:
            # Use actual game_days from context; fall back to end_timestamp if provided
            actual_game_days = end_timestamp
            if actual_game_days is None and self.__context.game_days > 1:
                actual_game_days = self.__context.game_days
            self.__conversation_db.end_conversation(self.__conversation_id, game_days=actual_game_days)
        if async_save:
            Thread(target=self.__save_conversation, args=(False, end_timestamp), daemon=True).start()
        else:
            self.__save_conversation(is_reload=False, end_timestamp=end_timestamp)
    
    @utils.time_it
    def add_message_and_generate(self, message):
        """Add a message to the conversation and start NPC generation.
        Used for injecting context (e.g. active quest data) that triggers a new LLM response.
        """
        self.__stop_generation()
        self.__sentences.clear()
        self.__messages.add_message(message)
        self.__persist_new_messages()
        self.__start_generating_npc_sentences()

    def __start_generating_npc_sentences(self, allow_tool_use: bool = True):
        """Starts a background Thread to generate sentences into the SentenceQueue"""
        with self.__generation_start_lock:
            if not self.__generation_thread or not self.__generation_thread.is_alive():
                self.__sentences.is_more_to_come = True
                # Generate tools if advanced actions are enabled
                tools = None
                if self.context.config.advanced_actions_enabled and allow_tool_use:
                    tools = FunctionManager.generate_context_aware_tools(self.__context, self.__game)
                # Capture current OpenTelemetry context for the new thread
                opentelemetry_context = OpenTelemetryContext.get_current()
                def thread_target():
                    set_parent_context(opentelemetry_context)
                    self.__output_manager.generate_response(self.__messages, self.__context.npcs_in_conversation, self.__sentences, self.context.config.actions, tools, self.__game)
                self.__generation_thread = Thread(target=thread_target)
                self.__generation_thread.start()

    @utils.time_it
    def __stop_generation(self):
        """Stops the current generation of sentences if there is one
        """
        self.__output_manager.stop_generation()
        deadline = time.time() + 20
        while self.__generation_thread and self.__generation_thread.is_alive():
            if time.time() > deadline:
                logger.warning("__stop_generation: generation thread still alive after 20s, giving up waiting")
                break
            time.sleep(0.1)
        self.__generation_thread = None

    @utils.time_it
    def __prepare_eject_npc_from_conversation(self, npc: Character):
        if not self.__has_already_ended:            
            self.__stop_generation()
            self.__sentences.clear()            
            # say goodbye (pick random from comma-separated list)
            goodbyes = [g.strip() for g in self.__context.config.goodbye_npc_response.split(",") if g.strip()]
            goodbye_text = random.choice(goodbyes) if goodbyes else self.__context.config.goodbye_npc_response
            goodbye_sentence = self.__output_manager.generate_sentence(SentenceContent(npc, goodbye_text, SentenceTypeEnum.SPEECH, False))
            if goodbye_sentence:
                goodbye_sentence.actions.append({'identifier':comm_consts.ACTION_REMOVECHARACTER})
                self.__sentences.put(goodbye_sentence)        

    @utils.time_it
    def __save_conversation(self, is_reload: bool, end_timestamp: float | None = None):
        """Saves conversation log and state for each NPC in the conversation"""
        self.__save_conversations_for_characters(self.__context.npcs_in_conversation.get_all_characters(), is_reload, end_timestamp)

    @utils.time_it
    def __save_conversations_for_characters(self, characters_to_save_for: list[Character], is_reload: bool, end_timestamp: float | None = None):
        characters_object = Characters()
        for npc in characters_to_save_for:
            characters_object.add_or_update_character(npc)
            if not npc.is_player_character:
                pass  # Messages already saved to DB via save_message() during conversation
        
        # Get and clear pending shares (only on final save, not reload)
        pending_shares = None
        if not is_reload:
            pending_shares = self.__context.npcs_in_conversation.get_pending_shares()
            self.__context.npcs_in_conversation.clear_pending_shares()
        
        self.__rememberer.save_conversation_state(self.__messages, characters_object, self.__context.world_id, is_reload, pending_shares, end_timestamp)

    @utils.time_it
    def __initiate_reload_conversation(self):
        """Places a "gather thoughts" sentence add the front of the queue that also prompts the game to request a reload of the conversation using an action"""
        latest_npc = self.__context.npcs_in_conversation.last_added_character
        if not latest_npc: 
            self.initiate_end_sequence()
            return
        
        # Play gather thoughts
        collecting_thoughts_text = self.__context.config.collecting_thoughts_npc_response
        collecting_thoughts_sentence = self.__output_manager.generate_sentence(SentenceContent(latest_npc, collecting_thoughts_text, SentenceTypeEnum.SPEECH, True))
        if collecting_thoughts_sentence:
            collecting_thoughts_sentence.actions.append({'identifier': comm_consts.ACTION_RELOADCONVERSATION})
            self.__sentences.put_at_front(collecting_thoughts_sentence)
    
    @utils.time_it
    def reload_conversation(self):
        """Reloads the conversation
        """
        self.__save_conversation(is_reload=True)
        # Reload
        new_prompt = self.__conversation_type.generate_prompt(self.__context)
        self.__messages.reload_message_thread(new_prompt, self.__llm_client.is_too_long, self.TOKEN_LIMIT_RELOAD_MESSAGES)

    @utils.time_it
    def __has_conversation_ended(self, last_user_text: str) -> bool:
        """Checks if the last player text has ended the conversation

        Args:
            last_user_text (str): the text to check

        Returns:
            bool: true if the conversation has ended, false otherwise
        """
        # transcriber = self.__stt
        config = self.__context.config
        transcript_cleaned = utils.clean_text(last_user_text)

        # check if user is ending conversation
        return Transcriber.activation_name_exists(transcript_cleaned, self.__end_conversation_keywords)

    _VISION_PHRASES = ["look at this", "look at that", "check this out", "see this", "see that",
                       "what do you see", "what can you see", "take a look", "have a look"]

    def __is_vision_request(self, text: str) -> bool:
        """Check if the player is asking the NPC to look at something."""
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in self._VISION_PHRASES)

    @utils.time_it
    def __does_dismiss_npc_from_conversation(self, last_user_text: str) -> Character | None:
        """Checks if the last player text dismisses an NPC from the conversation

        Args:
            last_user_text (str): the text to check

        Returns:
            bool: true if the conversation has ended, false otherwise
        """
        transcript_cleaned = utils.clean_text(last_user_text)

        words = transcript_cleaned.split()
        for i, word in enumerate(words):
            if word in self.__end_conversation_keywords:
                if i < (len(words) - 1):
                    next_word = words[i + 1]
                    for npc_name in self.__context.npcs_in_conversation.get_all_names():
                        if next_word in npc_name.lower().split():
                            return self.__context.npcs_in_conversation.get_character_by_name(npc_name)
        return None
    
    @utils.time_it
    def __should_voice_player_input(self, player_character: Character) -> bool:
        game_value: Any = player_character.get_custom_character_value(comm_consts.KEY_ACTOR_PC_VOICEPLAYERINPUT)
        if game_value == None:
            return self.__context.config.voice_player_input
        return game_value
