"""Quest trigger detection and quest list processing.

This module handles the 'what's the plan?' feature — a configurable trigger phrase
that causes the companion to list the player's running quests grouped by faction.
Completely decoupled from the existing NPC-scoped quest context system.
"""
import re
import logging

logger = logging.getLogger(__name__)

_FACTION_MAP = {
    'rr': 'Railroad', 'bos': 'Brotherhood of Steel', 'BoS': 'Brotherhood of Steel',
    'inst': 'Institute', 'Inst': 'Institute', 'minm': 'Minutemen',
}


def _normalize(text: str) -> str:
    """Strip punctuation and lowercase for fuzzy trigger matching."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


def is_quest_trigger(player_input: str, trigger_phrase: str) -> bool:
    """Check if player input matches the quest trigger phrase.

    Exact match only (after normalizing case/punctuation).
    Does not match if the phrase is embedded in a longer sentence.
    """
    if not player_input or not trigger_phrase:
        return False
    return _normalize(player_input) == _normalize(trigger_phrase)


def parse_running_quests(raw: str) -> list[dict]:
    """Parse Papyrus running quest data into structured list.

    Input format: "FormID:QuestName:stage|FormID:QuestName:stage|..."
    Returns list of dicts with formid_decimal, name, stage.
    """
    if not raw or raw == "NONE":
        return []

    quests = []
    for entry in raw.split("|"):
        parts = entry.split(":", 2)
        if len(parts) < 3:
            continue
        quests.append({
            'formid_decimal': parts[0],
            'name': parts[1],
            'stage': parts[2],
        })
    return quests


def enrich_quests_with_metadata(quests: list[dict], db=None) -> list[dict]:
    """Enrich parsed quests with faction/location metadata from wiki DB.

    Modifies quests in-place, adding 'faction' and 'location' keys.
    """
    for quest in quests:
        quest.setdefault('faction', '')
        quest.setdefault('location', '')

        if not db or not db.is_available:
            continue

        try:
            formid_hex = f"{int(quest['formid_decimal']):08X}"
            quest_info = db.get_quest_by_formid(formid_hex)
            if quest_info:
                quest['name'] = quest_info.get('title', quest['name'])
                quest_type = quest_info.get('quest_type', '')
                quest['location'] = quest_info.get('location', '')
                for prefix, faction_name in _FACTION_MAP.items():
                    if quest_type.startswith(prefix):
                        quest['faction'] = faction_name
                        break
                if not quest['faction'] and 'main' in quest_type:
                    quest['faction'] = 'Main Quest'
        except (ValueError, Exception) as e:
            logger.debug(f"Error enriching quest {quest['formid_decimal']}: {e}")

    return quests


def group_quests_by_faction(quests: list[dict]) -> dict[str, list[dict]]:
    """Group quests by faction. Quests without a faction go to 'Other'."""
    if not quests:
        return {}

    groups: dict[str, list[dict]] = {}
    for quest in quests:
        faction = quest.get('faction', '') or 'Other'
        if faction not in groups:
            groups[faction] = []
        groups[faction].append(quest)
    return groups


def build_quest_context_for_llm(quests: list[dict]) -> str:
    """Build the context string to inject into the LLM conversation.

    No stage numbers or game system info — just quest names, factions, locations.
    Instructs the LLM to summarize factions and let the player drill down.
    """
    groups = group_quests_by_faction(quests)

    # Build faction summary
    summary_parts = []
    for faction, faction_quests in groups.items():
        summary_parts.append(f"{faction}: {len(faction_quests)} quest(s)")
    faction_summary = ", ".join(summary_parts)

    # Build quest list grouped by faction (no stage numbers)
    grouped_list = ""
    for faction, faction_quests in groups.items():
        quest_names = []
        for q in faction_quests:
            desc = f"  - {q['name']}"
            if q.get('location'):
                desc += f" at {q['location']}"
            quest_names.append(desc)
        grouped_list += f"\n{faction}:\n" + "\n".join(quest_names) + "\n"

    return (
        f"[SYSTEM: The player is asking about quests. They have {len(quests)} running quests "
        f"across these categories: {faction_summary}\n"
        f"\nQuest list by category:{grouped_list}\n"
        f"INSTRUCTIONS: Do NOT mention stage numbers, quest IDs, or any game system information — "
        f"speak naturally in character. "
        f"Summarize the categories and ask which area they want to focus on "
        f"(e.g. 'We've got Railroad business, Brotherhood orders, and some loose ends — what's on your mind?'). "
        f"If they then mention a specific faction or quest name, narrow to those quests from the list. "
        f"Keep it conversational and in-character.]"
    )
