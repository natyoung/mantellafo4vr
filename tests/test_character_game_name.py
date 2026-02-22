"""Tests for Character.game_name — preserves original game name for Papyrus communication."""
from src.character_manager import Character
from src.games.equipment import Equipment


def _make_character(name: str, game_name: str = None, **kwargs) -> Character:
    defaults = dict(
        base_id='000001', ref_id='000002', gender=0, race='Human',
        is_player_character=False, bio='', is_in_combat=False, is_enemy=False,
        relationship_rank=0, is_generic_npc=False, ingame_voice_model='MaleBoston',
        tts_voice_model='rand_m01', csv_in_game_voice_model='', advanced_voice_model='',
        voice_accent='en', voice_language=None, equipment=Equipment({}),
        custom_character_values={},
    )
    defaults.update(kwargs)
    return Character(name=name, game_name=game_name, **defaults)


def test_game_name_defaults_to_name():
    c = _make_character(name='Piper')
    assert c.game_name == 'Piper'


def test_game_name_defaults_to_name_when_none():
    c = _make_character(name='Piper', game_name=None)
    assert c.game_name == 'Piper'


def test_game_name_preserves_original():
    c = _make_character(name='Earnest Todd', game_name='Resident')
    assert c.name == 'Earnest Todd'
    assert c.game_name == 'Resident'


def test_game_name_settable():
    c = _make_character(name='Piper')
    c.game_name = 'Resident'
    assert c.game_name == 'Resident'
    assert c.name == 'Piper'
