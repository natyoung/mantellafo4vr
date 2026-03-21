from src.config.types.config_value import ConfigValue, ConfigValueTag
from src.config.types.config_value_selection import ConfigValueSelection
from src.config.types.config_value_string import ConfigValueString


class LanguageDefinitions:    
    @staticmethod
    def get_language_config_value() -> ConfigValue:
        return ConfigValueSelection("language","Language","The language used by Mantella (speech-to-text, LLM responses, and text-to-speech).","en",["en", "ar", "cs", "da", "de", "el", "es", "fi", "fr", "hi", "hu", "it", "ja", "ko", "nl", "pl", "pt", "ro", "ru", "sv", "sw", "uk", "ha", "tr", "vi", "yo", "zh"])
    
    @staticmethod
    def get_end_conversation_keyword_config_value() -> ConfigValue:
        description = """The keyword(s) Mantella will listen out for to end the conversation (lowercase / uppercase does not matter).
                        To add multiple options, you can split keywords using commas."""
        return ConfigValueString("end_conversation_keyword","End Conversation Keyword(s)",description,"goodbye, bye, good-bye, good bye, good-by, good by, good to buy")
    
    @staticmethod
    def get_goodbye_npc_response() -> ConfigValue:
        return ConfigValueString("goodbye_npc_response","NPC Response: Goodbye","Comma-separated list of goodbye responses. One is picked at random each time.","Safe travels, Watch yourself out there, Stay sharp, Take it easy, Until next time, Be seeing you, Keep your head down, Don't be a stranger",tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])

    @staticmethod
    def get_collecting_thoughts_npc_response() -> ConfigValue:
        return ConfigValueString("collecting_thoughts_npc_response", "NPC Response: Collecting Thoughts","The response the NPC gives when they need to summarise the conversation because the maximum token count has been reached.","I need to gather my thoughts for a moment", tags=[ConfigValueTag.advanced,ConfigValueTag.share_row])
