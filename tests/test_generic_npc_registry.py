import pytest
from pathlib import Path
from src.generic_npc_registry import GenericNPCRegistry


class TestGenericNPCRegistry:
    def test_empty_lookup_returns_none(self, tmp_path: Path):
        registry = GenericNPCRegistry(str(tmp_path / "registry.json"))
        assert registry.lookup("13C4A4") is None

    def test_register_returns_identity(self, tmp_path: Path):
        registry = GenericNPCRegistry(str(tmp_path / "registry.json"))
        voice_pool = {"male": ["rand_m01", "rand_m02"], "female": ["rand_f01", "rand_f02"]}
        identity = registry.register("13C4A4", gender=1, race="Human", original_name="Settler", voice_pool=voice_pool)
        assert identity.ref_id == "13C4A4"
        assert identity.assigned_name != "Settler"
        assert len(identity.assigned_name) > 0
        assert identity.gender == 1
        assert identity.race == "Human"
        assert identity.original_game_name == "Settler"
