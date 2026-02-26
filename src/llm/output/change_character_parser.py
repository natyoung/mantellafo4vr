from typing import Callable, OrderedDict
from src.character_manager import Character
from src.characters_manager import Characters
from src.llm.output.output_parser import MarkedTextStateEnum, output_parser, sentence_generation_settings
from src.llm.sentence_content import SentenceContent
import src.utils as utils

logger = utils.get_logger()


class change_character_parser(output_parser):
    """Class to check if character change is in the current output of the LLM."""
    def __init__(self, characters_in_conversation: Characters) -> None:
        super().__init__()
        self.__dict_name_permutations: OrderedDict[str, Character] = OrderedDict() #Dictionary to hold permutations of the name for easy checks. e.g. "Svana Far-Shield" -> ["Svana Far-Shield", "Svana", "Far-Shield"]
        for actor in characters_in_conversation.get_all_characters():
            if actor.is_player_character:
                self.__dict_name_permutations["player"] = actor
            self.__dict_name_permutations[actor.name] = actor
        
        split_names_to_add: OrderedDict[str, Character] = OrderedDict()
        for name, character in self.__dict_name_permutations.items():
            split_name = character.name.split()        
            if len(split_name) > 1:
                for name in split_name:
                    if not split_names_to_add.__contains__(name):
                        split_names_to_add[name] = character
        
        for name, character in split_names_to_add.items():
            self.__dict_name_permutations[name] = character
        

    def __try_match_character(self, prefix: str) -> tuple[Character | None, str]:
        """Try to match a character name in the text before a colon.

        Checks both endswith (standard) and startswith (handles LLM using full names
        like "Piper Wright" when character is registered as "Piper").

        Returns (matched_character, remaining_prefix_text) or (None, prefix).
        """
        prefix_lower = prefix.lower()
        # First pass: endswith (original behavior, handles "some text Piper:")
        for name, character in self.__dict_name_permutations.items():
            if prefix_lower.endswith(name.lower()):
                remaining = prefix[:-len(name)].strip()
                return character, remaining

        # Second pass: startswith (handles "Piper Wright:" when registered as "Piper")
        prefix_stripped = prefix.strip()
        prefix_stripped_lower = prefix_stripped.lower()
        for name, character in self.__dict_name_permutations.items():
            name_lower = name.lower()
            if prefix_stripped_lower.startswith(name_lower):
                after_name = prefix_stripped[len(name):].strip()
                # Only match if the extra text is short (1-2 words, likely a surname)
                extra_words = after_name.split()
                if len(extra_words) <= 2 and all(w.isalpha() for w in extra_words):
                    return character, ""

        return None, prefix

    def cut_sentence(self, output: str, current_settings: sentence_generation_settings) -> tuple[SentenceContent | None, str]:
        if not ':' in output:
            return None, output

        parts = output.split(':', 1)
        character, cleaned_prefix_rest = self.__try_match_character(parts[0])
        if character is not None:
            if not len(cleaned_prefix_rest) == 0: #Special case where there is still text in front of a character change that needs to be processed first somehow
                rest = str.join("", [character.name, ":", parts[1]])
                return SentenceContent(current_settings.current_speaker, cleaned_prefix_rest, current_settings.sentence_type, False), rest
            else: #New sentence starts with character change
                if character.is_player_character:
                    logger.log(28, f"Stopped LLM from speaking on behalf of the player")
                    current_settings.stop_generation = True
                    return None, ""
                current_settings.current_speaker = character
                current_settings.sentence_type = current_settings.unmarked_text #Reset to the last unmarked text type
                current_settings.current_text_state = MarkedTextStateEnum.UNMARKED
                return None, parts[1]

        return None, output #There is a ':' in the text, but it doesn't seem to be part of a character change

    def modify_sentence_content(self, cut_content: SentenceContent, last_content: SentenceContent | None, settings: sentence_generation_settings) -> tuple[SentenceContent | None, SentenceContent | None]:
        return cut_content, last_content
    
    def get_cut_indicators(self) -> list[str]:
        return [":"]
