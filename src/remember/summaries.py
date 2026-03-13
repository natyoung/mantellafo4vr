import time
from src.config.config_loader import ConfigLoader
from src.games.gameable import Gameable
from src.llm.llm_client import LLMClient
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage
from src.characters_manager import Characters
from src.remember.remembering import Remembering
from src.remember.arc import ArcConsolidator
from src.remember.diary import DiaryConsolidator
from src import utils

logger = utils.get_logger()


class Summaries(Remembering):
    """Stores and loads conversation summaries via SQLite."""
    def __init__(self, game: Gameable, config: ConfigLoader, client: LLMClient, language_name: str, summary_limit_pct: float = 0.3) -> None:
        super().__init__()
        self.loglevel = 28
        self.__config = config
        self.__game: Gameable = game
        self.__summary_limit_pct: float = summary_limit_pct
        self.__client: LLMClient = client
        self.__language_name: str = language_name
        self.__memory_prompt: str = config.memory_prompt
        self.__resummarize_prompt:str = config.resummarize_prompt
        self.__db = getattr(game, 'conversation_db', None)
        self.__diary = DiaryConsolidator(self.__db, client, config, language_name, game.game_name_in_filepath) if self.__db else None
        self.__arc = ArcConsolidator(self.__db, client, config, language_name, game.game_name_in_filepath) if self.__db else None

    @utils.time_it
    def get_prompt_text(self, npcs_in_conversation: Characters, world_id: str, current_game_days: float | None = None) -> str:
        """Load diary entries + recent summaries for all NPCs in the conversation.

        Triggers diary consolidation if thresholds are met (enough days + enough summaries).
        Returns combined text: diary entries (older, compressed) then recent summaries (detailed).
        """
        arc_paragraphs = []
        diary_paragraphs = []
        summary_paragraphs = []

        for character in npcs_in_conversation.get_all_characters():
            if not character.is_player_character:
                base_name = utils.remove_trailing_number(character.name)

                if current_game_days is not None and current_game_days > 1:
                    # Try diary consolidation before loading
                    if self.__diary:
                        try:
                            self.__diary.maybe_consolidate(world_id, base_name, character.ref_id, current_game_days)
                        except Exception as e:
                            logger.warning(f"Diary consolidation failed for {base_name}: {e}")

                    # Try arc consolidation (after diary, so new diaries exist)
                    if self.__arc:
                        try:
                            self.__arc.maybe_consolidate(world_id, base_name, character.ref_id, current_game_days)
                        except Exception as e:
                            logger.warning(f"Arc consolidation failed for {base_name}: {e}")

                if self.__db:
                    # Load character arcs (oldest, broadest memories)
                    db_arcs = self.__db.get_all_character_arcs(world_id, base_name, character.ref_id)
                    for a in db_arcs:
                        content = a["content"].strip()
                        if content and content not in arc_paragraphs:
                            arc_paragraphs.append(content)

                    # Load diary entries (older, consolidated memories)
                    db_diary = self.__db.get_all_diary_entries(world_id, base_name, character.ref_id)
                    for d in db_diary:
                        content = d["content"].strip()
                        if content and content not in diary_paragraphs:
                            diary_paragraphs.append(content)

                # Load remaining summaries (recent, detailed memories)
                db_summaries = self.__db.get_all_summaries(world_id, base_name, character.ref_id)
                for s in db_summaries:
                    content = s["content"].strip()
                    if content:
                        for line in content.split("\n"):
                            line = line.strip()
                            if line and line not in summary_paragraphs:
                                summary_paragraphs.append(line)

        if not arc_paragraphs and not diary_paragraphs and not summary_paragraphs:
            return ""

        parts = ["Below is your memory of past events. Do not read these back verbatim — paraphrase naturally in your own voice if asked:"]
        if arc_paragraphs:
            parts.append("\n".join(arc_paragraphs))
        if diary_paragraphs:
            parts.append("\n".join(diary_paragraphs))
        if summary_paragraphs:
            parts.append("\n".join(summary_paragraphs))
        return "\n".join(parts)

    @utils.time_it
    def save_conversation_state(self, messages: message_thread, npcs_in_conversation: Characters, world_id: str, is_reload=False, pending_shares: list[tuple[str, str, str]] | None = None, end_timestamp: float | None = None):
        # Generate a separate summary for each NPC from their own perspective
        first_summary = ''
        for npc in npcs_in_conversation.get_all_characters():
            if not npc.is_player_character:
                summary = self.__create_new_conversation_summary(messages, npc.name, end_timestamp)
                if not first_summary and summary:
                    first_summary = summary
                if len(summary) > 0:
                    base_name = utils.remove_trailing_number(npc.name)
                    from_ts = self.__db.get_latest_summary_to_ts(world_id, base_name, npc.ref_id) or 0.0
                    to_ts = time.time()
                    self.__db.save_summary(world_id, base_name, npc.ref_id, summary, from_ts, to_ts)
                    self.__check_db_summary_overflow(world_id, base_name, npc.ref_id, npc.name)

        # Handle pending shares (use first NPC's summary as the shared version)
        if pending_shares and len(first_summary) > 0:
            for sharer_name, recipient_name, recipient_ref_id in pending_shares:
                participant_names = []
                for npc in npcs_in_conversation.get_all_characters():
                    if npc.name == sharer_name:
                        continue
                    if npc.is_player_character:
                        participant_names.append(f"{npc.name} (the player)")
                    else:
                        participant_names.append(npc.name)

                participants_text = ", ".join(participant_names) if participant_names else "others"
                prefixed_summary = f"{sharer_name} shared with {recipient_name} a conversation with {participants_text}:\n{first_summary}"

                base_recipient = utils.remove_trailing_number(recipient_name)
                from_ts = self.__db.get_latest_summary_to_ts(world_id, base_recipient, recipient_ref_id) or 0.0
                self.__db.save_summary(world_id, base_recipient, recipient_ref_id, prefixed_summary, from_ts, time.time())
                logger.info(f"Shared conversation summary with {recipient_name}")

    @utils.time_it
    def __create_new_conversation_summary(self, messages: message_thread, npc_name: str, end_timestamp: float | None = None) -> str:
        prompt = self.__memory_prompt.format(
                    name=npc_name,
                    language=self.__language_name,
                    game=self.__game.game_name_in_filepath
                )
        while True:
            try:
                if len(messages) >= 5:
                    summary = self.summarize_conversation(messages.transform_to_dict_representation(messages.get_talk_only()), prompt, npc_name)
                    # Prepend timestamp to summary if available
                    if summary and end_timestamp is not None and self.__config.memory_prompt_datetime_prefix:
                        timestamp_prefix = self.__format_timestamp(end_timestamp)
                        summary = f"{timestamp_prefix}\n{summary}"
                    return summary
                else:
                    logger.info(f"Conversation summary not saved. Not enough dialogue spoken.")
                break
            except:
                logger.error('Failed to summarize conversation. Retrying...')
                time.sleep(5)
                continue
        return ""

    def __check_db_summary_overflow(self, world_id: str, base_name: str, ref_id: str, npc_name: str):
        """If total DB summaries exceed token limit, condense them."""
        if not self.__db:
            return
        all_summaries = self.__db.get_all_summaries(world_id, base_name, ref_id)
        if not all_summaries:
            return
        combined = "\n".join(s["content"] for s in all_summaries)
        summary_limit = round(self.__client.token_limit * self.__summary_limit_pct, 0)
        count_tokens = self.__client.get_count_tokens(combined)
        if count_tokens > summary_limit:
            logger.info(f'DB summary token limit reached ({count_tokens} / {summary_limit}). Condensing summaries for {base_name}...')
            prompt = self.__resummarize_prompt.format(
                name=npc_name,
                language=self.__language_name,
                game=self.__game.game_name_in_filepath
            )
            condensed = self.summarize_conversation(combined, prompt, npc_name)
            if condensed:
                self.__db.replace_summaries(world_id, base_name, ref_id, condensed)

    def recover_orphaned_conversations(self, npcs_in_conversation: Characters, world_id: str):
        """Recover and summarize messages from crashed conversations (orphans)."""
        if not self.__db:
            return

        for character in npcs_in_conversation.get_all_characters():
            if character.is_player_character:
                continue
            base_name = utils.remove_trailing_number(character.name)
            ref_id = character.ref_id

            orphan_ids = self.__db.get_orphaned_conversation_ids(world_id, base_name, ref_id)
            if not orphan_ids:
                continue

            unsummarized = self.__db.get_unsummarized_messages(world_id, base_name, ref_id)
            if len(unsummarized) < 5:
                # Not enough messages to summarize, just mark as handled
                self.__db.mark_conversations_summarized(orphan_ids)
                continue

            # Build text representation for summarization
            text_lines = []
            for msg in unsummarized:
                role_label = "Player" if msg["role"] == "user" else base_name
                text_lines.append(f"{role_label}: {msg['content']}")
            text_to_summarize = "\n".join(text_lines)

            prompt = self.__memory_prompt.format(
                name=base_name,
                language=self.__language_name,
                game=self.__game.game_name_in_filepath
            )
            summary = self.summarize_conversation(text_to_summarize, prompt, base_name)
            if summary:
                from_ts = self.__db.get_latest_summary_to_ts(world_id, base_name, ref_id) or 0.0
                self.__db.save_summary(world_id, base_name, ref_id, summary, from_ts, time.time())
                logger.info(f"Recovered orphaned conversation for {base_name} ({len(unsummarized)} messages)")

            self.__db.mark_conversations_summarized(orphan_ids)

    @utils.time_it
    def __format_timestamp(self, game_days: float) -> str:
        """Formats a game timestamp into readable format: [Day X, Y in the evening]
        
        Args:
            game_days: Game time as days passed (eg 42.75 = Day 42, 6pm)
        
        Returns:
            str: Formatted timestamp like "[Day 42, 6 in the evening]"
        """
        days = int(game_days)
        hours = int((game_days - days) * 24)
        in_game_time_twelve_hour = hours - 12 if hours > 12 else hours
        
        return f"[Day {days}, {in_game_time_twelve_hour} {utils.get_time_group(hours)}]"
    
    @utils.time_it
    def summarize_conversation(self, text_to_summarize: str, prompt: str, npc_name: str) -> str:
        summary = ''
        if len(text_to_summarize) > 5:
            messages = message_thread(self.__config, prompt)
            messages.add_message(UserMessage(self.__config, text_to_summarize))
            with self.__client.override_params(max_tokens=1000):
                summary = self.__client.request_call(messages)
            if not summary:
                logger.error(f"Summarizing conversation failed.")
                return ""

            summary = summary.replace('The assistant', npc_name)
            summary = summary.replace('the assistant', npc_name)
            summary = summary.replace('an assistant', npc_name)
            summary = summary.replace('an AI assistant', npc_name)
            summary = summary.replace('The user', 'The player')
            summary = summary.replace('the user', 'the player')
            summary += '\n\n'

            logger.log(self.loglevel, f'Conversation summary: {summary.strip()}')
            logger.info(f"Conversation summary saved")
        else:
            logger.info(f"Conversation summary not saved. Not enough dialogue spoken.")

        return summary
