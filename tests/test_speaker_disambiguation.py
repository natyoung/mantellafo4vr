"""Tests for speaker disambiguation when multiple NPCs share the same game name.

Bug: In radiant conversations between two generic settlers, Papyrus disambiguates
duplicate names via faction rank (e.g. "Settler", "Settler 2"), but Python always
sends game_name="Settler" for both, so GetActorInConversation always returns the
first settler and makes her speak every line.

The fix: GameStateManager._disambiguate_game_names must replicate the Papyrus
naming convention so that sentence_to_json sends "Settler 2" for the second
settler, etc.
"""
from src.game_manager import GameStateManager
from src.character_manager import Character
from src.games.equipment import Equipment
from src.http.communication_constants import communication_constants as comm_consts
from src.llm.sentence import Sentence
from src.llm.sentence_content import SentenceContent, SentenceTypeEnum


def _make_char(name: str, ref_id: str, gender: int = 0,
               game_name: str = None, is_player: bool = False) -> Character:
    """Build a minimal Character for disambiguation tests."""
    return Character(
        base_id='020533', ref_id=ref_id, name=name, gender=gender,
        race='Human', is_player_character=is_player, bio='', is_in_combat=False,
        is_enemy=False, relationship_rank=0, is_generic_npc=not is_player,
        ingame_voice_model='MaleBoston', tts_voice_model='rand_m01',
        csv_in_game_voice_model='', advanced_voice_model='',
        voice_accent='en', voice_language=None, equipment=Equipment({}),
        custom_character_values={}, game_name=game_name,
    )


class TestDisambiguateGameNames:
    """GameStateManager._disambiguate_game_names must mirror Papyrus GetActorName()."""

    def test_two_settlers_second_gets_suffix(self):
        """First 'Settler' keeps bare name, second gets 'Settler 2'."""
        actors = [
            _make_char("Edith Wynn", "AA0001", gender=1, game_name="Settler"),
            _make_char("Deacon Nye", "AA0002", gender=0, game_name="Settler"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[0].game_name == "Settler"
        assert actors[1].game_name == "Settler 2"

    def test_three_settlers_incremental_suffixes(self):
        """Three same-named actors: 'Settler', 'Settler 2', 'Settler 3'."""
        actors = [
            _make_char("Alice", "AA0001", gender=1, game_name="Settler"),
            _make_char("Bob", "AA0002", gender=0, game_name="Settler"),
            _make_char("Charlie", "AA0003", gender=0, game_name="Settler"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[0].game_name == "Settler"
        assert actors[1].game_name == "Settler 2"
        assert actors[2].game_name == "Settler 3"

    def test_unique_names_unchanged(self):
        """Actors with unique names should not be modified."""
        actors = [
            _make_char("Piper", "AA0001", gender=1, game_name="Piper"),
            _make_char("Preston", "AA0002", gender=0, game_name="Preston Garvey"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[0].game_name == "Piper"
        assert actors[1].game_name == "Preston Garvey"

    def test_mixed_unique_and_duplicate(self):
        """Only duplicate names get suffixed; unique names are untouched."""
        actors = [
            _make_char("Piper", "AA0001", gender=1, game_name="Piper"),
            _make_char("Edith", "AA0002", gender=1, game_name="Settler"),
            _make_char("Deacon", "AA0003", gender=0, game_name="Settler"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[0].game_name == "Piper"
        assert actors[1].game_name == "Settler"
        assert actors[2].game_name == "Settler 2"

    def test_player_excluded_from_disambiguation(self):
        """Player character is never suffixed, even if name collides."""
        actors = [
            _make_char("Player", "AA0000", is_player=True, game_name="Settler"),
            _make_char("Edith", "AA0001", gender=1, game_name="Settler"),
            _make_char("Deacon", "AA0002", gender=0, game_name="Settler"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        # Player keeps bare name (Papyrus ignores player in its naming loop)
        assert actors[0].game_name == "Settler"
        # NPCs get suffixed among themselves
        assert actors[1].game_name == "Settler"
        assert actors[2].game_name == "Settler 2"

    def test_empty_list(self):
        """No crash on empty list."""
        GameStateManager._disambiguate_game_names([])

    def test_single_actor(self):
        """Single actor is never suffixed."""
        actors = [_make_char("Edith", "AA0001", game_name="Settler")]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[0].game_name == "Settler"

    def test_idempotent_on_already_disambiguated(self):
        """Running disambiguation twice must not double-suffix."""
        actors = [
            _make_char("Edith", "AA0001", gender=1, game_name="Settler"),
            _make_char("Deacon", "AA0002", gender=0, game_name="Settler"),
        ]
        GameStateManager._disambiguate_game_names(actors)
        assert actors[1].game_name == "Settler 2"

        # Run again (simulates a second __update_context call)
        GameStateManager._disambiguate_game_names(actors)
        # Must still be "Settler 2", not "Settler 2 2"
        assert actors[0].game_name == "Settler"
        assert actors[1].game_name == "Settler 2"

    def test_sentence_to_json_uses_disambiguated_name(self):
        """End-to-end: sentence_to_json must output the suffixed game_name."""
        actor = _make_char("Deacon Nye", "AA0002", gender=0, game_name="Settler 2")

        sentence_content = SentenceContent(
            speaker=actor,
            text="Nice evening.",
            sentence_type=SentenceTypeEnum.SPEECH,
            is_system_generated_sentence=False,
            actions=[],
        )
        sentence = Sentence(sentence_content, "test.wav", 1.5)

        # sentence_to_json reads speaker.game_name directly
        assert sentence.speaker.game_name == "Settler 2"
