from typing import Any, Hashable
from src.conversation.action import Action
from src.http.communication_constants import communication_constants
from src.characters_manager import Characters
from src.remember.remembering import Remembering
from src import utils
from src.utils import get_time_group
from src.character_manager import Character
from src.config.config_loader import ConfigLoader
from src.llm.llm_client import LLMClient
from src.config.definitions.game_definitions import GameEnum
from src.wiki.wiki_loader import load_character_wiki

logger = utils.get_logger()


class Context:
    """Holds the context of a conversation
    """
    TOKEN_LIMIT_PERCENT: float = 0.45
    
    # Language code to full name mapping for LLM prompts
    LANGUAGE_CODE_TO_NAME = {
        'en': 'English', 'ar': 'Arabic', 'cs': 'Czech', 'da': 'Danish', 'de': 'German',
        'el': 'Greek', 'es': 'Spanish', 'fi': 'Finnish', 'fr': 'French', 'hi': 'Hindi',
        'hu': 'Hungarian', 'it': 'Italian', 'ja': 'Japanese', 'ko': 'Korean', 'nl': 'Dutch',
        'pl': 'Polish', 'pt': 'Portuguese', 'ro': 'Romanian', 'ru': 'Russian', 'sv': 'Swedish',
        'sw': 'Swahili', 'uk': 'Ukrainian', 'ha': 'Hausa', 'tr': 'Turkish', 'vi': 'Vietnamese',
        'yo': 'Yoruba', 'zh': 'Chinese', 'zh-cn': 'Chinese', 'sk': 'Slovak'
    }

    @utils.time_it
    def __init__(self, world_id: str, config: ConfigLoader, client: LLMClient, rememberer: Remembering, language: dict[Hashable, str], conversation_db=None) -> None:
        self.__world_id = world_id
        self.__hourly_time = config.hourly_time
        self.__prev_game_time: tuple[str | None, str] | None = None
        self.__npcs_in_conversation: Characters = Characters()
        self.__config: ConfigLoader = config
        self.__client: LLMClient = client
        self.__rememberer: Remembering = rememberer
        self.__conversation_db = conversation_db
        self.__language: dict[Hashable, str] = language
        self.__weather: str = ""
        self.__config_settings: dict[str, Any] = {}
        self.__custom_context_values: dict[str, Any] = {}
        self.__ingame_time: int = 12
        self.__game_days: float = 1.0  # Full game timestamp (days.fraction)
        self.__ingame_events: list[str] = []
        self.__vision_hints: str = ''
        self.__have_actors_changed: bool = False
        self.__game_context_changed: bool = False
        self.__game: GameEnum = config.game
        self.__prev_nearby_npc_names: list[str] = []  # Cache for nearby NPC names
        self.__last_known_player_name: str = ""  # Persists across radiant conversations

        self.__prev_location: str | None = None
        if self.__game.base_game == GameEnum.FALLOUT4:
            self.__location: str = 'the Commonwealth'
        else:
            self.__location: str = "Skyrim"

    @property
    def world_id(self) -> str:
        return self.__world_id

    @property
    def npcs_in_conversation(self) -> Characters:
        return self.__npcs_in_conversation
    
    @property
    def config(self) -> ConfigLoader:
        return self.__config

    @property
    def prompt_multinpc(self) -> str:
        return self.__config.multi_npc_prompt
    
    @property
    def location(self) -> str:
        return self.__location
    
    @property
    def language(self) -> dict[Hashable, str]:
        return self.__language
    
    @location.setter
    def location(self, value: str):
        self.__location = value

    @property
    def ingame_time(self) -> int:
        return self.__ingame_time
    
    @ingame_time.setter
    def ingame_time(self, value: int):
        self.__ingame_time = value

    @property
    def game_days(self) -> float:
        return self.__game_days

    @property
    def have_actors_changed(self) -> bool:
        return self.__have_actors_changed
    
    @have_actors_changed.setter
    def have_actors_changed(self, value: bool):
        self.__have_actors_changed = value

    @property
    def game_context_changed(self) -> bool:
        return self.__game_context_changed
    
    @game_context_changed.setter
    def game_context_changed(self, value: bool):
        self.__game_context_changed = value

    def get_config_setting(self, key: str) -> Any | None:
        if self.__config_settings.__contains__(key):
            return self.__config_settings[key]
        return None
    @property
    def vision_hints(self) -> dict[Hashable, str]:
        return self.__vision_hints
    
    @utils.time_it
    def set_vision_hints(self, names: str, distances: str):
        def get_category(distance):
            if distance < 150:
                return "very close"
            elif distance < 500:
                return "close"
            elif distance < 1000:
                return "medium distance"
            elif distance < 2500:
                return "far"
            else:
                return "very far"
        
        names = [x.strip('[]') for x in names.split(',')]
        distances = [float(x.strip('[]')) for x in distances.split(',')]

        pairs = sorted(zip(distances, names))
        descriptions = [f"{name} ({get_category(dist)})" for dist, name in pairs]
        self.__vision_hints = "Characters currently in view: " + ", ".join(descriptions)

    @utils.time_it
    def get_custom_context_value(self, key: str) -> Any | None:
        if self.__custom_context_values and key in self.__custom_context_values:
            return self.__custom_context_values[key]
        return None

    def get_game_context(self, days_since_last_spoke: float | None = None) -> str:
        """Build complete game context string from custom context values.

        Includes: situation, danger, player state, NPC state, nearby NPCs, settlement, quests.
        Returns content WITHOUT wrapper tags - the prompt template provides those.
        """
        sections = []

        # === TIME SINCE LAST CONVERSATION ===
        if days_since_last_spoke is not None and days_since_last_spoke >= 7:
            days = int(days_since_last_spoke)
            sections.append(f"You last spoke to the player {days} days ago.")
        
        # === SITUATION ===
        situation_parts = []
        
        # Location info (from location type context or fallback to regular location)
        loc_context = self.get_custom_context_value(communication_constants.KEY_CONTEXT_LOCATION_TYPE)
        if loc_context:
            loc_info = self._parse_location_context(loc_context)
            if loc_info:
                situation_parts.append(f"Location: {loc_info}")
        
        # Time
        if self.__ingame_time:
            time_str = self._format_time(self.__ingame_time)
            situation_parts.append(f"Time: {time_str}")
        
        # Danger context
        danger_context = self.get_custom_context_value(communication_constants.KEY_CONTEXT_DANGER)
        if danger_context:
            danger_info = self._parse_danger_context(danger_context)
            if danger_info:
                situation_parts.append(f"Danger: {danger_info}")
        else:
            situation_parts.append("Danger: None")
        
        # Environment context (hazards)
        env_context = self.get_custom_context_value(communication_constants.KEY_CONTEXT_ENVIRONMENT)
        if env_context:
            env_info = self._parse_environment_context(env_context)
            if env_info:
                situation_parts.append(f"Hazards: {env_info}")
        
        if situation_parts:
            sections.append("=== SITUATION ===\n" + "\n".join(situation_parts))
        
        # === PLAYER STATE ===
        player_state = self.get_custom_context_value(communication_constants.KEY_CONTEXT_PLAYER_STATE)
        if player_state:
            state_info = self._parse_player_state(player_state)
            if state_info:
                # Add player effects if available
                effects = self.get_custom_context_value(communication_constants.KEY_CONTEXT_PLAYER_EFFECTS)
                if effects:
                    effects_info = self._parse_player_effects(effects)
                    if effects_info:
                        state_info += f"\nActive effects: {effects_info}"
                sections.append("=== PLAYER STATE ===\n" + state_info)
        
        # === SETTLEMENT ===
        settlement_context = self.get_custom_context_value(communication_constants.KEY_CONTEXT_SETTLEMENT)
        if settlement_context:
            logger.debug(f"Raw settlement data: {settlement_context}")
            settlement_info = self._parse_settlement_context(settlement_context)
            if settlement_info:
                sections.append("=== SETTLEMENT ===\n" + settlement_info)
        
        # === NPC STATE ===
        npc_state = self.get_custom_context_value(communication_constants.KEY_CONTEXT_NPC_STATE)
        if npc_state:
            sections.append("=== NPC STATE ===\n" + npc_state)
        
        # === NEARBY NPCS ===
        nearby_npcs = self.get_custom_context_value(communication_constants.KEY_CONTEXT_NEARBY_NPCS)
        if nearby_npcs:
            npcs_info = self._parse_nearby_npcs(nearby_npcs)
            if npcs_info:
                sections.append("=== NEARBY NPCS ===\n" + npcs_info)
        
        # === NPC RELATIONSHIP ===
        npc_role = self.get_custom_context_value(communication_constants.KEY_CONTEXT_NPC_ROLE)
        if npc_role:
            role_info = self._parse_npc_role(npc_role)
            if role_info:
                sections.append("=== YOUR RELATIONSHIP WITH PLAYER ===\n" + role_info)
        
        # === COMPANION AFFINITY ===
        affinity_lines = []
        for npc in self.get_characters_excluding_player().get_all_characters():
            affinity = npc.get_custom_character_value("mantella_actor_affinity")
            if affinity is not None:
                affinity = float(affinity)
                if affinity >= 1000:
                    affinity_lines.append(f"You and the player share a profound bond — deep mutual trust and loyalty.")
                elif affinity >= 750:
                    affinity_lines.append(f"You trust the player deeply. You're willing to open up about personal matters.")
                elif affinity >= 500:
                    affinity_lines.append(f"You respect the player and consider them a reliable ally.")
                elif affinity >= 250:
                    affinity_lines.append(f"You're getting to know the player. You're cautiously warming up to them.")
                elif affinity >= 0:
                    affinity_lines.append(f"You and the player are still feeling each other out. You keep things professional.")
                else:
                    affinity_lines.append(f"You're frustrated with the player's recent choices. Your patience is wearing thin.")
        if affinity_lines:
            sections.append("=== YOUR BOND WITH THE PLAYER ===\n" + "\n".join(affinity_lines))

        # === SETTLER JOBS ===
        job_lines = []
        for npc in self.get_characters_excluding_player().get_all_characters():
            job = npc.get_custom_character_value("mantella_actor_job")
            if job:
                job_lines.append(f"{npc.prompt_name} works as a {job} at this settlement.")
        if job_lines:
            sections.append("=== SETTLER JOBS ===\n" + "\n".join(job_lines))

        # === NPC-TO-NPC FAMILIARITY ===
        npc_familiarity = self._get_npc_familiarity()
        if npc_familiarity:
            sections.append("=== NPC RELATIONSHIPS ===\n" + npc_familiarity)

        # === QUESTS ===
        quest_context = self.get_custom_context_value(communication_constants.KEY_CONTEXT_NPC_QUESTS)
        if quest_context:
            from src.wiki.quest_context import build_quest_context
            parsed = build_quest_context(quest_context)
            if parsed:
                sections.append("=== QUESTS ===\n" + parsed)

        return "\n\n".join(sections)
    
    def _parse_player_state(self, raw: str) -> str:
        """Parse player state from Papyrus format.
        
        Input: "level:15|weapon:10mm Pistol|weapon_drawn|power_armor|sneaking|in_combat|caps:1250_moderate"
        Output: Formatted player state info.
        """
        if not raw:
            return ""
        
        parts = raw.split("|")
        level = ""
        weapon = ""
        weapon_state = ""
        caps = ""
        flags = []
        
        for part in parts:
            if part.startswith("level:"):
                level = part.split(":", 1)[1]
            elif part.startswith("weapon:"):
                weapon = part.split(":", 1)[1]
            elif part == "weapon_drawn":
                weapon_state = "Drawn"
            elif part == "weapon_holstered":
                weapon_state = "Holstered"
            elif part.startswith("caps:"):
                caps_data = part.split(":", 1)[1]
                # Parse caps amount and wealth indicator
                if "_" in caps_data:
                    caps_amount, wealth = caps_data.rsplit("_", 1)
                    caps = f"{caps_amount} ({wealth})"
                else:
                    caps = caps_data
            elif part == "power_armor":
                flags.append("In Power Armor")
            elif part == "sneaking":
                flags.append("Sneaking")
            elif part == "sneaking_detected":
                flags.append("Sneaking (DETECTED!)")
            elif part == "in_combat":
                flags.append("In Combat")
            elif part == "searching":
                flags.append("Searching for enemies")
        
        lines = []
        if level:
            lines.append(f"Level: {level}")
        if weapon:
            lines.append(f"Equipped: {weapon}")
        if weapon_state:
            lines.append(f"Weapon: {weapon_state}")
        
        # Add health/rad from existing values
        health = self.get_custom_context_value("mantella_player_health_percent")
        rad = self.get_custom_context_value("mantella_player_rad_percent")
        if health is not None or rad is not None:
            health_pct = int(health * 100) if health is not None else 100
            rad_pct = int(rad * 100) if rad is not None else 0
            lines.append(f"Health: {health_pct}% | Radiation: {rad_pct}%")
        
        if caps:
            lines.append(f"Caps: {caps}")
        
        if flags:
            lines.append("Status: " + ", ".join(flags))
        
        return "\n".join(lines)
    
    def _parse_nearby_npcs(self, raw: str) -> str:
        """Parse nearby NPCs. Format: name=X;distance=Y;faction=Z|name=A;distance=B"""
        if not raw:
            return ""
        
        lines = []
        for entry in raw.split("|"):
            # Parse key=value pairs
            npc = {}
            for pair in entry.split(";"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    npc[key] = value
            
            name = npc.get("name", "")
            distance = npc.get("distance", "")
            if not name or not distance:
                continue
            
            faction = npc.get("faction", "NPC")
            line = f"- {name} ({distance} units) - {faction}"
            
            status = []
            activity = npc.get("activity", "idle").replace("_", " ")
            status.append(activity)
            
            health = npc.get("health", "healthy")
            status.append(health)
            
            if npc.get("weapon") == "drawn":
                status.append("weapon drawn")
            
            armed = npc.get("armed", "")
            if armed:
                status.append(f"armed with {armed}")
            
            line += " - " + ", ".join(status)
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def _parse_npc_role(self, raw: str) -> str:
        """Parse NPC role/relationship from Papyrus format.
        
        Input: "companion|relationship:3|faction:settler|essential"
        Output: Formatted relationship info.
        """
        if not raw:
            return ""
        
        parts = raw.split("|")
        lines = []
        
        is_companion = False
        relationship = 0
        faction = ""
        flags = []
        
        for part in parts:
            if part == "companion":
                is_companion = True
            elif part.startswith("relationship:"):
                relationship = int(part.split(":", 1)[1])
            elif part.startswith("faction:"):
                faction = part.split(":", 1)[1]
            elif part == "essential":
                flags.append("Essential")
            elif part == "bleeding_out":
                flags.append("Bleeding Out")
        
        # Relationship rank meanings
        rel_names = {
            -4: "Archnemesis", -3: "Enemy", -2: "Foe", -1: "Rival",
            0: "Acquaintance", 1: "Friend", 2: "Confidant", 3: "Ally", 4: "Lover"
        }
        
        if is_companion:
            lines.append("Role: Companion (following player)")
        
        rel_name = rel_names.get(relationship, "Unknown")
        lines.append(f"Affinity: {rel_name} ({relationship})")
        
        if faction:
            lines.append(f"Faction: {faction.title()}")
        
        if flags:
            lines.append("Status: " + ", ".join(flags))
        
        return "\n".join(lines)
    
    def _parse_location_context(self, raw: str) -> str:
        """Parse location context from Papyrus format.
        
        Input: "name:Sanctuary Hills|interior" or "exterior"
        Output: Formatted location string.
        """
        if not raw:
            return ""
        
        parts = raw.split("|")
        name = ""
        loc_type = ""
        
        for part in parts:
            if part.startswith("name:"):
                name = part.split(":", 1)[1]
            elif part in ("interior", "exterior"):
                loc_type = part
        
        result = name if name else self.__location
        if loc_type:
            result += f" ({loc_type})"
        
        return result
    
    def _format_time(self, hour: int) -> str:
        """Format hour into readable time string."""
        if hour == 0:
            return "midnight"
        elif hour == 12:
            return "noon"
        elif hour < 12:
            return f"{hour} in the morning"
        else:
            return f"{hour - 12} in the afternoon" if hour < 18 else f"{hour - 12} in the evening"
    
    def _parse_danger_context(self, raw: str) -> str:
        """Parse danger context from Papyrus format.
        
        Input: "hostiles:3|in_combat|dead_bodies:5"
        Output: Human-readable danger description.
        """
        if not raw:
            return ""
        
        parts = raw.split("|")
        danger_parts = []
        
        for part in parts:
            if part.startswith("hostiles:"):
                count = part.split(":", 1)[1]
                danger_parts.append(f"{count} hostiles nearby")
            elif part == "in_combat":
                danger_parts.append("player in combat")
            elif part == "searching":
                danger_parts.append("enemies searching")
            elif part.startswith("dead_bodies:"):
                count = part.split(":", 1)[1]
                danger_parts.append(f"{count} dead bodies nearby")
        
        return ", ".join(danger_parts) if danger_parts else ""
    
    def _parse_environment_context(self, raw: str) -> str:
        """Parse environment context from Papyrus format.
        
        Input: "radiation_high|health_low"
        Output: Human-readable environment description.
        """
        if not raw:
            return ""
        
        parts = raw.split("|")
        env_parts = []
        
        for part in parts:
            if part == "radiation_high":
                env_parts.append("High radiation zone")
            elif part == "radiation_moderate":
                env_parts.append("Moderate radiation")
            elif part == "health_low":
                env_parts.append("Player health critical")
        
        return ", ".join(env_parts) if env_parts else ""
    
    def _parse_settlement_context(self, raw: str) -> str:
        """Parse settlement context from Papyrus format into natural language.

        Input: "name:Sanctuary Hills|population:12|food:16|water:14|defense:87|power:22|beds:14|happiness:78"
        Output: Natural language description of settlement conditions.
        """
        if not raw:
            return ""

        # Parse key:value pairs
        state: dict[str, str] = {}
        for part in raw.split("|"):
            if ":" in part:
                key, val = part.split(":", 1)
                state[key] = val

        if not state:
            return ""

        def safe_int(val: str, default: int = 0) -> int:
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        name = state.get("name", "this settlement")

        # Locations that are technically settlements but shouldn't show settlement stats
        # (military bases, robot lairs, etc.)
        skip_settlement_entirely = {
            "the mechanist's lair", "mechanist's lair",
            "boston airport",
            "home plate",
        }
        if name.lower() in skip_settlement_entirely:
            return ""

        pop = safe_int(state.get("population", "0"))
        food = safe_int(state.get("food", "0"))
        water = safe_int(state.get("water", "0"))
        defense = safe_int(state.get("defense", "0"))
        beds = safe_int(state.get("beds", "0"))
        happiness = safe_int(state.get("happiness", "0"))
        power = safe_int(state.get("power", "0"))

        lines = [f"You are at {name}, a settlement owned and built by the player."]
        lines.append(f"The settlement has {pop} residents.")

        # Food assessment
        if pop > 0:
            food_ratio = food / pop
            if food_ratio >= 1.5:
                lines.append("Food is abundant — more than enough for everyone.")
            elif food_ratio >= 1.0:
                lines.append("Food supply is adequate, meeting everyone's needs.")
            elif food_ratio >= 0.5:
                lines.append("Food is tight — not quite enough to go around.")
            else:
                lines.append("Food is critically low. People are going hungry.")

        # Water assessment
        if pop > 0:
            water_ratio = water / pop
            if water_ratio >= 1.5:
                lines.append("Clean water is plentiful.")
            elif water_ratio >= 1.0:
                lines.append("Water supply is sufficient.")
            elif water_ratio >= 0.5:
                lines.append("Water is scarce — rationing may be necessary.")
            else:
                lines.append("Water is critically short.")

        # Defense assessment
        if pop > 0:
            if defense >= pop * 3:
                lines.append("The settlement is heavily fortified. Residents feel very safe.")
            elif defense >= pop:
                lines.append("Defenses are solid — enough to deter most threats.")
            elif defense >= pop * 0.5:
                lines.append("Defenses are thin. Attacks are a real concern.")
            else:
                lines.append("The settlement is barely defended. Residents are anxious about raids.")

        # Beds (workshop bed count can be unreliable — only flag major shortages)
        if pop > 0:
            if beds >= pop:
                lines.append("Everyone has a place to sleep.")
            elif beds >= pop - 2:
                pass  # Minor discrepancy, likely workshop counting error — don't mention
            else:
                lines.append("Some settlers don't have proper beds.")

        # Happiness
        if happiness >= 80:
            lines.append("Morale is high. People are content with life here.")
        elif happiness >= 60:
            lines.append("Morale is decent, though there are grumbles.")
        elif happiness >= 40:
            lines.append("Morale is low. People are unhappy with conditions.")
        else:
            lines.append("Morale is terrible. Residents are miserable and may leave.")

        # Recent attack
        last_attack_days = safe_int(state.get("last_attack_days", "-1"), -1)
        attacker = state.get("last_attack_by", "")
        by_whom = f" by {attacker}" if attacker else ""
        if last_attack_days == 0:
            lines.append(f"The settlement was attacked{by_whom} today. People are shaken and on edge.")
        elif last_attack_days == 1:
            lines.append(f"The settlement was attacked{by_whom} yesterday. Residents are still rattled.")
        elif 2 <= last_attack_days <= 3:
            lines.append(f"The settlement was attacked{by_whom} just a few days ago. Tension is still high.")
        elif 4 <= last_attack_days <= 7:
            lines.append(f"There was an attack{by_whom} on the settlement recently, within the past week.")

        # Power (only mention if notable)
        if power > 0:
            lines.append(f"The settlement has {power} units of power generation.")

        # Supply lines
        supply_lines = safe_int(state.get("supply_lines", "0"))
        if supply_lines > 0:
            lines.append("The settlement is connected to a supply network with other settlements, sharing resources.")
        elif pop > 5:
            lines.append("The settlement is isolated — no supply lines connect it to other settlements.")

        # Radio beacon (only mention sometimes — when it's contextually interesting)
        radio = safe_int(state.get("radio", "0"))
        if radio > 0 and pop < 5:
            lines.append("A radio beacon is broadcasting, trying to attract new settlers.")
        elif radio == 0 and pop < 3:
            lines.append("There's no recruitment radio beacon — the settlement isn't actively attracting newcomers.")

        lines.append(
            "Use this information naturally — don't recite statistics, but let your "
            "knowledge of conditions here inform how you talk about life at the settlement."
        )

        return "\n".join(lines)
    
    def _parse_player_effects(self, raw: str) -> str:
        """Parse player effects from Papyrus format.
        
        Input: "Jet|Psycho|Well Rested"
        Output: Comma-separated effects.
        """
        if not raw:
            return ""
        
        effects = raw.split("|")
        return ", ".join(effects) if effects else ""

    @utils.time_it
    def get_context_ingame_events(self) -> list[str]:
        return self.__ingame_events
    
    @utils.time_it
    def clear_context_ingame_events(self):
        self.__ingame_events.clear()

    @utils.time_it
    def add_or_update_characters(self, new_list_of_npcs: list[Character]) -> list[Character]:
        removed_npcs = []
        for npc in new_list_of_npcs:
            if not self.__npcs_in_conversation.contains_character(npc):
                self.__npcs_in_conversation.add_or_update_character(npc)
                if not npc.is_player_character:
                    if npc.is_generic_npc and npc.game_name != npc.prompt_name:
                        self.__ingame_events.append(f"The {npc.game_name} nearby is named {npc.prompt_name}. {npc.prompt_name} has joined the conversation.")
                    else:
                        self.__ingame_events.append(f"{npc.prompt_name} has joined the conversation.")
                self.__have_actors_changed = True
            else:
                #check for updates in the transient stats and generate update events
                self.__update_ingame_events_on_npc_change(npc)
                self.__npcs_in_conversation.add_or_update_character(npc)
        for npc in self.__npcs_in_conversation.get_all_characters():
            if not npc in new_list_of_npcs:
                removed_npcs.append(npc)
                self.__remove_character(npc)
        return removed_npcs
    
    @utils.time_it
    def remove_character(self, npc: Character):
        if self.__npcs_in_conversation.contains_character(npc):
            self.__remove_character(npc)
    
    @utils.time_it
    def __remove_character(self, npc: Character):
        self.__npcs_in_conversation.remove_character(npc)
        self.__ingame_events.append(f"{npc.name} has left the conversation.")
        self.__have_actors_changed = True

    @utils.time_it
    def get_time_group(self) -> str:
        return get_time_group(self.__ingame_time)
    
    @utils.time_it
    def update_context(self, location: str | None, in_game_time: int | None, custom_ingame_events: list[str] | None, weather: str | None, npcs_nearby: list[dict[str, Any]] | None, custom_context_values: dict[str, Any], config_settings: dict[str, Any] | None, game_days: float | None = None):
        # Check if game context (quests, settlement, etc.) is newly available
        old_quest_context = self.__custom_context_values.get(communication_constants.KEY_CONTEXT_NPC_QUESTS) if self.__custom_context_values else None
        new_quest_context = custom_context_values.get(communication_constants.KEY_CONTEXT_NPC_QUESTS) if custom_context_values else None
        if new_quest_context and new_quest_context != old_quest_context:
            self.__game_context_changed = True
            logger.info(f"Game context updated with quest info: {new_quest_context}")

        old_settlement = self.__custom_context_values.get(communication_constants.KEY_CONTEXT_SETTLEMENT) if self.__custom_context_values else None
        new_settlement = custom_context_values.get(communication_constants.KEY_CONTEXT_SETTLEMENT) if custom_context_values else None
        if new_settlement and not old_settlement:
            # Only regenerate prompt when settlement data first arrives, not on minor stat changes
            self.__game_context_changed = True
            logger.info(f"Game context updated with settlement info: {new_settlement}")
        elif new_settlement and new_settlement != old_settlement:
            # Silently update stored values without triggering expensive prompt regeneration
            logger.debug(f"Settlement stats updated (no prompt regen): {new_settlement}")
        
        self.__custom_context_values = custom_context_values

        # Store game_days if provided
        if game_days is not None:
            self.__game_days = game_days

        is_flying = custom_context_values.get(communication_constants.KEY_CONTEXT_IS_FLYING, False) if custom_context_values else False

        if location:
            if location != '':
                self.__location = location
            else:
                if self.__game.base_game == GameEnum.FALLOUT4:
                    self.__location: str = 'the Commonwealth'
                else:
                    self.__location: str = "Skyrim"
            if (self.__location != self.__prev_location) and (self.__prev_location != None):
                if not is_flying:
                    self.__prev_location = self.__location
                    self.__ingame_events.append(f"The location is now {location}.")
        
        if in_game_time:
            self.__ingame_time = in_game_time
            in_game_time_twelve_hour = in_game_time - 12 if in_game_time > 12 else in_game_time
            if self.__hourly_time:
                current_time: tuple[str | None, str] = str(in_game_time_twelve_hour), get_time_group(in_game_time)
            else:
                current_time: tuple[str | None, str] = None, get_time_group(in_game_time)

            if (current_time != self.__prev_game_time) and (self.__prev_game_time != None):
                self.__prev_game_time = current_time
                if self.__hourly_time:
                    self.__ingame_events.append(f"The time is {current_time[0]} {current_time[1]}.")
                else:
                    self.__ingame_events.append(f"The conversation now takes place {current_time[1]}.")

        if weather != self.__weather and weather is not None:
            if self.__weather != "":
                self.__ingame_events.append(weather)
            self.__weather = weather

        # Update nearby NPCs in the Characters manager
        self.__npcs_in_conversation.set_nearby_npcs(npcs_nearby)
        
        # Add vision hints to in-game events
        self.__vision_hints = ''
        if self.get_custom_context_value(communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSNAMEARRAY) and self.get_custom_context_value(communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSDISTANCEARRAY):
            self.set_vision_hints(
                str(self.get_custom_context_value(communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSNAMEARRAY)), 
                str(self.get_custom_context_value(communication_constants.KEY_CONTEXT_CUSTOMVALUES_VISION_HINTSDISTANCEARRAY)))
            self.__ingame_events.append(self.__vision_hints)
        elif npcs_nearby:
            # Sort by distance and list names (nearest to furthest)
            sorted_npcs = sorted(npcs_nearby, key=lambda x: float(x.get('distance', 0)))
            nearby_names = [npc['name'] for npc in sorted_npcs if 'name' in npc]
            # Only add event if the set of nearby NPCs has changed (not just order)
            if nearby_names and set(nearby_names) != set(self.__prev_nearby_npc_names):
                self.__vision_hints = "Characters nearby (from nearest to furthest): " + ", ".join(nearby_names)
                self.__ingame_events.append(self.__vision_hints)
                self.__prev_nearby_npc_names = nearby_names

        if custom_ingame_events:
            # Filter out noise events that are just game mechanics
            _NOISE_KEYWORDS = ['power armor', 't-45', 't-51', 't-60', 'x-01', 'raider power',
                               'fusion core', 'power armor frame']
            for event in custom_ingame_events:
                event_lower = event.lower()
                if any(kw in event_lower for kw in _NOISE_KEYWORDS):
                    continue
                self.__ingame_events.append(event)

        if config_settings:
            self.__config_settings = config_settings
    
    @utils.time_it
    def __update_ingame_events_on_npc_change(self, npc: Character):
        current_stats: Character = self.__npcs_in_conversation.get_character_by_name(npc.name)
        #Is in Combat
        if current_stats.is_in_combat != npc.is_in_combat:
            name = 'The player' if npc.is_player_character else npc.name
            if npc.is_in_combat:
                self.__ingame_events.append(f"{name} is now in combat!")
            else:
                self.__ingame_events.append(f"{name} is no longer in combat.")
        #update custom  values
        try:
            if (current_stats.get_custom_character_value("mantella_actor_pos_x") is not None and
                npc.get_custom_character_value("mantella_actor_pos_x") is not None and
                current_stats.get_custom_character_value("mantella_actor_pos_x") != npc.get_custom_character_value("mantella_actor_pos_x")):
                current_stats.set_custom_character_value("mantella_actor_pos_x", npc.get_custom_character_value("mantella_actor_pos_x"))

            if (current_stats.get_custom_character_value("mantella_actor_pos_y") is not None and
                npc.get_custom_character_value("mantella_actor_pos_y") is not None and
                current_stats.get_custom_character_value("mantella_actor_pos_y") != npc.get_custom_character_value("mantella_actor_pos_y")):
                current_stats.set_custom_character_value("mantella_actor_pos_y", npc.get_custom_character_value("mantella_actor_pos_y"))
        except Exception as e:
            logger.error(f"Updating custom values failed: {e}")
        if not npc.is_player_character:
            player_name = "the player"
            player = self.__npcs_in_conversation.get_player_character()
            if player:
                player_name = player.name
            #Is attacking player
            if current_stats.is_enemy != npc.is_enemy:
                if npc.is_enemy: 
                    # TODO: review if pronouns can be replaced with "they"
                    self.__ingame_events.append(f"{npc.name} is attacking {player_name}. This is either because {npc.personal_pronoun_subject} is an enemy or {player_name} has attacked {npc.personal_pronoun_object} first.")
                else:
                    self.__ingame_events.append(f"{npc.name} is no longer attacking {player_name}.")
            #Relationship rank
            if current_stats.relationship_rank != npc.relationship_rank:
                trust = self.__get_trust(npc)
                self.__ingame_events.append(f"{player_name} is now {trust} to {npc.name}.")
    
    @staticmethod
    def format_listing(listing: list[str]) -> str:
        """Returns a list of string concatenated by ',' and 'and' to be used in a text

        Args:
            listing (list[str]): the list of strings

        Returns:
            str: A natural language listing. Returns an empty string if listing is empty, returns the the string if length of listing is 1
        """
        if len(listing) == 0:
            return ""
        elif len(listing) == 1:
            return listing[0]
        else:
            return ', '.join(listing[:-1]) + ' and ' + listing[-1]
       
    @utils.time_it
    def __get_trust(self, npc: Character) -> str:
        """Calculates the trust of a NPC towards the player

        Args:
            npc (Character): the NPC to calculate the trust for

        Returns:
            str: a natural text representing the trust
        """
        trust_level = 0
        if self.__conversation_db:
            base_name = utils.remove_trailing_number(npc.name)
            trust_level = self.__conversation_db.get_message_count(self.__world_id, base_name, npc.ref_id)
        trust = 'a stranger'
        if npc.relationship_rank == 0:
            if trust_level < 1:
                trust = 'a stranger'
            elif trust_level < 10:
                trust = 'an acquaintance'
            elif trust_level < 50:
                trust = 'a friend'
            elif trust_level >= 50:
                trust = 'a close friend'
        elif npc.relationship_rank == 4:
            trust = 'a lover'
        elif npc.relationship_rank > 0:
            trust = 'a friend'
        elif npc.relationship_rank < 0:
            trust = 'an enemy'
        return trust
    
    @utils.time_it
    def __get_trusts(self) -> str:
        """Calculates the trust towards the player for all NPCs in the conversation

        Args:
            player_name (str, optional): _description_. Defaults to "". The name of the player, if empty string treated as if the player is not in the conversation

        Returns:
            str: A combined natural text describing their relationship towards the player, empty if there is no player 
        """
        # if player_name == "" or len(self.__npcs_in_conversation) < 1:
        #     return ""
        
        relationships = []
        for npc in self.get_characters_excluding_player().get_all_characters():
            trust = self.__get_trust(npc)
            relationships.append(f"{npc.prompt_name}'s {trust}")
        
        return Context.format_listing(relationships)

    def _get_npc_familiarity(self) -> str:
        """Build NPC-to-NPC relationship text based on shared conversation history."""
        if not self.__conversation_db:
            return ""

        npcs = [npc for npc in self.get_characters_excluding_player().get_all_characters() if not npc.is_player_character]
        if len(npcs) < 2:
            return ""

        lines = []
        seen_pairs = set()
        for i, npc_a in enumerate(npcs):
            name_a = utils.remove_trailing_number(npc_a.name)
            for npc_b in npcs[i+1:]:
                name_b = utils.remove_trailing_number(npc_b.name)
                pair_key = tuple(sorted([name_a, name_b]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                count = self.__conversation_db.get_shared_conversation_count(
                    self.__world_id, name_a, name_b
                )
                prompt_a = npc_a.prompt_name or name_a
                prompt_b = npc_b.prompt_name or name_b
                if count >= 10:
                    lines.append(f"{prompt_a} and {prompt_b} know each other well — they've talked many times.")
                elif count >= 5:
                    lines.append(f"{prompt_a} and {prompt_b} are familiar with each other.")
                elif count >= 2:
                    lines.append(f"{prompt_a} and {prompt_b} have spoken a few times before.")
                # count 0-1: strangers, don't mention

        return "\n".join(lines)

    @utils.time_it
    def get_character_names_as_text(self, include_player: bool, include_nearby: bool = False, nearby_only: bool = False) -> str:
        """Gets the names of the NPCs in the conversation as a natural language list

        Args:
            include_player (bool): If True, includes the player in the list
            include_nearby (bool): If True, includes nearby NPCs in the list
            nearby_only (bool): If True, only includes nearby NPCs (not in conversation)

        Returns:
            str: text containing the names concatenated by ',' and 'and'
        """
        names = self.__npcs_in_conversation.get_all_names_w_nearby(
            include_player=include_player,
            include_nearby=include_nearby,
            nearby_only=nearby_only
        )
        return Context.format_listing(names)
    
    @utils.time_it
    def __get_bios_text(self) -> str:
        """Gets the bios of all characters in the conversation

        Returns:
            str: the bios concatenated together into a single string
        """
        bio_descriptions = []
        npcs_only = self.get_characters_excluding_player()
        for character in npcs_only.get_all_characters():
            if len(npcs_only) == 1:
                bio_descriptions.append(character.bio)
            else:
                bio_descriptions.append(f"{character.prompt_name}: {character.bio}")
        return "\n".join(bio_descriptions)
    
    @utils.time_it
    def __get_npc_equipment_text(self) -> str:
        """Gets the equipment description of all npcs in the conversation

        Returns:
            str: the equipment descriptions concatenated together into a single string
        """
        equipment_descriptions = []
        for character in self.get_characters_excluding_player().get_all_characters():
                equipment_descriptions.append(character.equipment.get_equipment_description(character.name))
        return " ".join(equipment_descriptions)
    
    @utils.time_it
    def __get_action_texts(self, actions: list[Action]) -> str:
        """Generates the prompt text for the available actions

        Args:
            actions (list[Action]): the list of possible actions. Already filtered for conversation type and config choices

        Returns:
            str: the text for the {actions} variable
        """
        result = ""
        for a in actions:
            if a.prompt_text:
                result += a.prompt_text.format(key=a.keyword) + "\n"
        return result
    
    @utils.time_it
    def generate_system_message(self, prompt: str, actions_for_prompt: list[Action]) -> str:
        """Fills the variables in the prompt with the values calculated from the context

        Args:
            prompt (str): The conversation specific system prompt to fill
            actions_for_prompt (list[Action]): the list of possible actions

        Returns:
            str: the filled prompt
        """
        player: Character | None = self.__npcs_in_conversation.get_player_character()
        player_name = ""
        player_description = self.__config.player_character_description
        player_equipment = ""
        if player:
            player_name = player.name
            self.__last_known_player_name = player_name
        elif self.__last_known_player_name:
            player_name = self.__last_known_player_name
            player_equipment = player.equipment.get_equipment_description('')
            game_sent_description = player.get_custom_character_value(communication_constants.KEY_ACTOR_PC_DESCRIPTION)
            if game_sent_description and game_sent_description != "":
                player_description = game_sent_description
        if self.npcs_in_conversation.last_added_character:
            name: str = self.npcs_in_conversation.last_added_character.prompt_name
        else:
            name = self.get_character_names_as_text(False)
        names = self.get_character_names_as_text(False)
        names_w_player = self.get_character_names_as_text(True)
        bios = self.__get_bios_text()
        trusts = self.__get_trusts()
        equipment = self.__get_npc_equipment_text()
        location = self.__location
        # Settlement context is now handled by the === SETTLEMENT === section in get_game_context()
        # Only add the simple suffix if no rich settlement data was received
        is_player_settlement = self.get_custom_context_value(communication_constants.KEY_CONTEXT_IS_PLAYER_SETTLEMENT)
        has_settlement_data = self.get_custom_context_value(communication_constants.KEY_CONTEXT_SETTLEMENT)
        if is_player_settlement and not has_settlement_data:
            location = f"{self.__location}, the player's settlement"
        self.__prev_location = self.__location
        weather = self.__weather
        time = self.__ingame_time - 12 if self.__ingame_time > 12 else self.__ingame_time
        time_group = get_time_group(self.__ingame_time)
        
        # Calculate current day number from game_days
        current_day = int(self.__game_days) if self.__game_days > 1 else 1
        
        if self.__hourly_time:
            self.__prev_game_time = str(time), time_group
        else:
            self.__prev_game_time = None, time_group
        # Build context hint for relevance-based memory filtering
        context_hint = " ".join(p for p in [location, names, bios] if p)

        conversation_summaries = self.__rememberer.get_prompt_text(self.get_characters_excluding_player(), self.__world_id, current_game_days=self.__game_days, context_hint=context_hint)
        
        # Only include legacy action prompts if advanced actions are disabled
        actions = self.__get_action_texts(actions_for_prompt) if not self.__config.advanced_actions_enabled else ""
        
        # Calculate days since last conversation with this NPC
        days_since = None
        if self.__game_days > 1 and self.__conversation_db:
            for char in self.get_characters_excluding_player().get_all_characters():
                base_name = utils.remove_trailing_number(char.name)
                last_days = self.__conversation_db.get_last_conversation_game_days(self.__world_id, base_name, char.ref_id)
                if last_days is not None:
                    days_since = self.__game_days - last_days
                    break

        # Get game context (quests, etc.) from Papyrus
        game_context = self.get_game_context(days_since_last_spoke=days_since)
        has_quest_context = bool(game_context)
        logger.info(f"generate_system_message: game_context length = {len(game_context)}, has content = {has_quest_context}")
        
        # Character background: wiki → bio (simple priority)
        # TODO:: make it  as function? maybe only fallout4 ???
        if self.npcs_in_conversation.last_added_character:
            char = self.npcs_in_conversation.last_added_character
            char_name = char.name
            
            # Priority 1: Wiki from override file
            if char.wiki and char.wiki.strip():
                bios = char.wiki
                logger.info(f"Using wiki from override ({len(bios)} chars)")
            else:
                # Priority 2: Wiki from wiki_loader
                game_name = self.__config.game.base_game.display_name
                wiki_content = load_character_wiki(
                    char_name, game_name, 
                    strip_quests=has_quest_context, 
                    player_name=player_name
                )
                if wiki_content:
                    bios = wiki_content
                    logger.info(f"Using wiki from loader ({len(bios)} chars)")
                elif char.bio and char.bio.strip():
                    # Priority 3: Bio (fallback)
                    bios = char.bio
                    logger.info(f"Using bio as fallback ({len(bios)} chars)")

        

        # Determine conversation language - use NPC's language if set, otherwise global language
        conversation_language = self.__language['language']
        if self.npcs_in_conversation.last_added_character and self.npcs_in_conversation.last_added_character.voice_language:
            conversation_language = self.npcs_in_conversation.last_added_character.voice_language
        
        # Convert language code to full name for LLM prompt
        conversation_language_name = Context.LANGUAGE_CODE_TO_NAME.get(conversation_language, conversation_language)

        removal_content: list[tuple[str, str]] = [(bios, conversation_summaries),(bios,""),("","")]
        have_bios_been_dropped = False
        have_summaries_been_dropped = False
        logger.log(23, f'Maximum size of prompt is {self.__client.token_limit} x {self.TOKEN_LIMIT_PERCENT} = {int(round(self.__client.token_limit * self.TOKEN_LIMIT_PERCENT, 0))} tokens.')
        for content in removal_content:
            result = prompt.format(
                player_name = player_name,
                player_description = player_description,
                player_equipment = player_equipment,
                name=name,
                names=names,
                names_w_player = names_w_player,
                bio=content[0],
                bios=content[0], 
                trust=trusts,
                equipment = equipment,
                location=location,
                weather = weather,
                time=time,
                current_day=current_day,
                time_group=time_group, 
                language=conversation_language_name, 
                conversation_summary=content[1],
                conversation_summaries=content[1],
                actions = actions,
                wiki = "",  # Deprecated: kept for backwards compatibility, content now in {bio}
                game_context = game_context
                )
            if self.__client.is_too_long(result, self.TOKEN_LIMIT_PERCENT):
                if content[0] != "":
                    have_summaries_been_dropped = True
                else:
                    have_bios_been_dropped = True
            else:
                break
        
        logger.log(23, f'Prompt sent to LLM ({self.__client.get_count_tokens(result)} tokens): {result.strip()}')
        if have_summaries_been_dropped and have_bios_been_dropped:
            logger.log(logger.WARNING, f'Both the bios and summaries of the NPCs selected could not fit into the maximum prompt size of {int(round(self.__client.token_limit * self.TOKEN_LIMIT_PERCENT, 0))} tokens. NPCs will not remember previous conversations and will have limited knowledge of who they are.')
        elif have_summaries_been_dropped:
            logger.log(logger.WARNING, f'The summaries of the NPCs selected could not fit into the maximum prompt size of {int(round(self.__client.token_limit * self.TOKEN_LIMIT_PERCENT, 0))} tokens. NPCs will not remember previous conversations.')
        return result
    
    @utils.time_it
    def get_characters_excluding_player(self) -> Characters:
        new_characters = Characters()
        for actor in self.__npcs_in_conversation.get_all_characters():
            if not actor.is_player_character:
                new_characters.add_or_update_character(actor)
        return new_characters
