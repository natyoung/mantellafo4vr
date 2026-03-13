"""Faction rumor generation from diary entries.

When an NPC's diary entry is created, a third-person rumor is generated
and shared with other members of the same faction. This simulates
information spreading through social groups — settlers gossip, companions
share intel, faction members pass along news.
"""
import logging

from src.conversation.conversation_db import ConversationDB
from src.llm.message_thread import message_thread
from src.llm.messages import UserMessage

logger = logging.getLogger(__name__)


class RumorGenerator:
    """Generates faction rumors from diary entries."""

    def __init__(self, db: ConversationDB, client, config, language_name: str):
        self.__db = db
        self.__client = client
        self.__config = config
        self.__language_name = language_name

    def maybe_generate(self, world_id: str, npc_name: str, npc_ref_id: str,
                       diary_content: str, game_days: float) -> bool:
        """Generate a faction rumor from a diary entry if the NPC has a faction.

        Returns True if a rumor was created.
        """
        char = self.__db.get_character(world_id, npc_ref_id)
        if not char or not char.get("faction"):
            return False

        faction = char["faction"]

        prompt = self.__config.rumor_prompt.format(
            name=npc_name,
            language=self.__language_name,
        )
        thread = message_thread(self.__config, prompt)
        thread.add_message(UserMessage(self.__config, diary_content))
        with self.__client.override_params(max_tokens=500):
            rumor_content = self.__client.request_call(thread)

        if not rumor_content:
            logger.warning(f"Rumor generation failed for {npc_name} — LLM returned empty response")
            return False

        self.__db.save_faction_rumor(
            world_id, faction, npc_name, npc_ref_id, rumor_content, game_days,
        )

        logger.info(f"Created faction rumor from {npc_name}'s diary (faction: {faction})")
        return True
