"""Character arc consolidation for hierarchical NPC memory (Tier 4).

Periodically consolidates diary entries into character arc summaries,
giving NPCs coherent long-term personality development over very long playthroughs.
Diary entries (weekly narratives) compress into arc summaries (monthly+ character development).
"""
import logging

from src.conversation.conversation_db import ConversationDB
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage

logger = logging.getLogger(__name__)


class ArcConsolidator:
    """Consolidates diary entries into character arc summaries when thresholds are met."""

    def __init__(self, db: ConversationDB, client, config, language_name: str, game_name: str):
        self.__db = db
        self.__client = client
        self.__config = config
        self.__summary_model: str | None = config.summary_model
        self.__language_name = language_name
        self.__game_name = game_name

    def maybe_consolidate(self, world_id: str, npc_name: str, npc_ref_id: str,
                          current_game_days: float) -> bool:
        """Check if arc consolidation is due and perform it if so.

        Requires BOTH thresholds to be met:
        - At least arc_interval_days since last arc
        - At least arc_min_diaries unconsolidated diary entries

        Returns True if a character arc was created.
        """
        interval = self.__config.arc_interval_days
        min_diaries = self.__config.arc_min_diaries

        # Check time threshold
        last_arc_days = self.__db.get_latest_arc_game_days(world_id, npc_name, npc_ref_id)
        days_since = current_game_days - (last_arc_days or 0.0)
        if days_since < interval:
            return False

        # Check diary count threshold
        all_diaries = self.__db.get_all_diary_entries(world_id, npc_name, npc_ref_id)
        if len(all_diaries) < min_diaries:
            return False

        # Build input text from diary entries
        diary_texts = [d["content"].strip() for d in all_diaries if d["content"].strip()]
        if not diary_texts:
            return False

        combined_text = "\n\n".join(diary_texts)

        # Call LLM to generate character arc
        prompt = self.__config.arc_prompt.format(
            name=npc_name,
            language=self.__language_name,
            game=self.__game_name,
        )
        thread = message_thread(self.__config, prompt)
        thread.add_message(UserMessage(self.__config, combined_text))
        with self.__client.override_params(max_tokens=2000):
            arc_content = self.__client.request_call(thread, model_override=self.__summary_model)

        if not arc_content:
            logger.warning(f"Arc consolidation failed for {npc_name} — LLM returned empty response")
            return False

        # Clean up LLM artifacts
        arc_content = arc_content.replace('The assistant', npc_name)
        arc_content = arc_content.replace('the assistant', npc_name)
        arc_content = arc_content.replace('The user', 'The player')
        arc_content = arc_content.replace('the user', 'the player')

        # Save character arc
        game_days_from = last_arc_days or 0.0
        diary_from_ts = all_diaries[0]["summaries_from_ts"]
        diary_to_ts = all_diaries[-1]["summaries_to_ts"]

        self.__db.save_character_arc(
            world_id, npc_name, npc_ref_id, arc_content,
            game_days_from=game_days_from, game_days_to=current_game_days,
            diary_from_ts=diary_from_ts, diary_to_ts=diary_to_ts,
        )

        # Delete consolidated diary entries
        self.__db.delete_diary_entries_before_ts(world_id, npc_name, npc_ref_id, diary_to_ts)

        logger.info(f"Created character arc for {npc_name} (consolidated {len(all_diaries)} diary entries, "
                     f"days {game_days_from:.0f}-{current_game_days:.0f})")
        return True
