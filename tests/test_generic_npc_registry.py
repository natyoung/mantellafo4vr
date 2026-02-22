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

    def test_lookup_after_register(self, tmp_path: Path):
        registry = GenericNPCRegistry(str(tmp_path / "registry.json"))
        voice_pool = {"male": ["rand_m01"], "female": ["rand_f01"]}
        registered = registry.register("AABB01", gender=0, race="Human", original_name="Resident", voice_pool=voice_pool)
        looked_up = registry.lookup("AABB01")
        assert looked_up is not None
        assert looked_up.ref_id == registered.ref_id
        assert looked_up.assigned_name == registered.assigned_name
        assert looked_up.voice_model == registered.voice_model

    def test_persists_to_disk(self, tmp_path: Path):
        path = str(tmp_path / "registry.json")
        voice_pool = {"male": ["rand_m01"], "female": ["rand_f01"]}
        registry1 = GenericNPCRegistry(path)
        registry1.register("PERSIST1", gender=0, race="Human", original_name="Settler", voice_pool=voice_pool)
        # Create a new registry from the same file
        registry2 = GenericNPCRegistry(path)
        found = registry2.lookup("PERSIST1")
        assert found is not None
        assert found.assigned_name == registry1.lookup("PERSIST1").assigned_name

    def test_deterministic_names(self, tmp_path: Path):
        voice_pool = {"male": ["rand_m01"], "female": ["rand_f01"]}
        reg1 = GenericNPCRegistry(str(tmp_path / "reg1.json"))
        reg2 = GenericNPCRegistry(str(tmp_path / "reg2.json"))
        id1 = reg1.register("DETERM1", gender=0, race="Human", original_name="Settler", voice_pool=voice_pool)
        id2 = reg2.register("DETERM1", gender=0, race="Human", original_name="Settler", voice_pool=voice_pool)
        assert id1.assigned_name == id2.assigned_name

    def test_unique_names(self, tmp_path: Path):
        registry = GenericNPCRegistry(str(tmp_path / "registry.json"))
        voice_pool = {"male": ["rand_m01", "rand_m02", "rand_m03"], "female": ["rand_f01", "rand_f02", "rand_f03"]}
        names = set()
        for i in range(10):
            identity = registry.register(f"UNIQUE{i:04d}", gender=0, race="Human", original_name="Settler", voice_pool=voice_pool)
            names.add(identity.assigned_name)
        assert len(names) == 10
