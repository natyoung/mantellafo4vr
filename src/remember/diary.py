"""Diary consolidation for hierarchical NPC memory.

Periodically consolidates conversation summaries into first-person diary entries,
giving NPCs more natural long-term memory. Inspired by human memory consolidation:
recent memories stay detailed (summaries), older ones become compressed narratives (diary).
"""
import logging

from src.conversation.conversation_db import ConversationDB
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage

logger = logging.getLogger(__name__)


class DiaryConsolidator:
    """Consolidates conversation summaries into diary entries when thresholds are met."""

    def __init__(self, db: ConversationDB, client, config, language_name: str, game_name: str):
        self.__db = db
        self.__client = client
        self.__config = config
        self.__summary_model: str | None = config.summary_model
        self.__language_name = language_name
        self.__game_name = game_name

    def maybe_consolidate(self, world_id: str, npc_name: str, npc_ref_id: str,
                          current_game_days: float, player_name: str = "the player",
                          location: str = "") -> bool:
        """Check if diary consolidation is due and perform it if so.

        Requires BOTH thresholds to be met:
        - At least diary_interval_days since last diary entry
        - At least diary_min_summaries unconsolidated summaries

        Returns True if a diary entry was created.
        """
        interval = self.__config.diary_interval_days
        min_summaries = self.__config.diary_min_summaries

        # Check time threshold
        last_diary_days = self.__db.get_latest_diary_game_days(world_id, npc_name, npc_ref_id)
        days_since = current_game_days - (last_diary_days or 0.0)
        if days_since < interval:
            return False

        # Check summary count threshold
        all_summaries = self.__db.get_all_summaries(world_id, npc_name, npc_ref_id)
        if len(all_summaries) < min_summaries:
            return False

        # Build input text from summaries
        summary_texts = [s["content"].strip() for s in all_summaries if s["content"].strip()]
        if not summary_texts:
            return False

        combined_text = "\n\n".join(summary_texts)

        # Call LLM to generate diary entry
        prompt = self.__config.diary_prompt.format(
            name=npc_name,
            language=self.__language_name,
            game=self.__game_name,
            player_name=player_name,
        )
        thread = message_thread(self.__config, prompt)
        thread.add_message(UserMessage(self.__config, combined_text))
        with self.__client.override_params(max_tokens=3000):
            diary_content = self.__client.request_call(thread, model_override=self.__summary_model)

        if not diary_content:
            logger.warning(f"Diary consolidation failed for {npc_name} — LLM returned empty response")
            return False

        # Clean up LLM artifacts
        diary_content = diary_content.replace('The assistant', npc_name)
        diary_content = diary_content.replace('the assistant', npc_name)
        diary_content = diary_content.replace('The user', player_name)
        diary_content = diary_content.replace('the user', player_name)

        # Save diary entry
        game_days_from = last_diary_days or 0.0
        summaries_from_ts = all_summaries[0]["from_ts"]
        summaries_to_ts = all_summaries[-1]["to_ts"]

        self.__db.save_diary_entry(
            world_id, npc_name, npc_ref_id, diary_content,
            game_days_from=game_days_from, game_days_to=current_game_days,
            summaries_from_ts=summaries_from_ts, summaries_to_ts=summaries_to_ts,
            location=location,
        )

        # Delete consolidated summaries
        self.__db.delete_summaries_before_ts(world_id, npc_name, npc_ref_id, summaries_to_ts)

        logger.info(f"Created diary entry for {npc_name} (consolidated {len(all_summaries)} summaries, "
                     f"days {game_days_from:.0f}-{current_game_days:.0f})")
        return True
