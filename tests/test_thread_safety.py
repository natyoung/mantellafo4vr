"""Thread safety tests for output_manager and sentence_queue.

Tests Parts 1-4 of the thread safety fix plan:
  1. asyncio.Event → threading.Event
  2. __is_generating protected by __gen_lock
  3. is_more_to_come protected by __more_lock
  4. Per-NPC max_response_sentences override
"""
import asyncio
import threading
import time
import pytest
from unittest.mock import MagicMock

from src.output_manager import ChatManager
from src.config.config_loader import ConfigLoader
from src.config.definitions.llm_definitions import NarrationHandlingEnum
from src.tts.ttsable import TTSable
from src.tts.synthesization_options import SynthesizationOptions
from src.llm.sentence_queue import SentenceQueue
from src.llm.message_thread import message_thread
from src.characters_manager import Characters
from src.character_manager import Character
from src.llm.sentence_content import SentenceTypeEnum, SentenceContent
from src.llm.sentence import Sentence
from src.conversation.action import Action


# ---------------------------------------------------------------------------
# MockAIClient (same pattern as test_output_manager.py)
# ---------------------------------------------------------------------------

class MockAIClient:
    def __init__(self, response_pattern=None, delay=0.01):
        self.response_pattern = response_pattern if response_pattern is not None else ["Hello there."]
        self.delay = delay
        self.call_count = 0

    async def streaming_call(self, messages=None, is_multi_npc=False, tools=None, model_override=None):
        self.call_count += 1
        for chunk in self.response_pattern:
            yield ("content", chunk)
            await asyncio.sleep(self.delay)

    def get_count_tokens(self, text):
        return len(str(text).split())

    def is_too_long(self, messages, token_limit_percent):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ai_client():
    return MockAIClient()


@pytest.fixture
def mock_queue() -> SentenceQueue:
    return SentenceQueue()


@pytest.fixture
def mock_messages(default_config: ConfigLoader) -> message_thread:
    return message_thread(default_config, None)


@pytest.fixture
def mock_actions() -> list[Action]:
    return [
        Action(
            identifier="wave", name="Wave", keyword="Wave",
            description="Waves at the player",
            prompt_text="If the player asks you to wave, begin your response with 'Wave:'.",
            requires_response=False, is_interrupting=False,
            one_on_one=True, multi_npc=False, radiant=False,
        )
    ]


class StubTTS(TTSable):
    """Minimal TTS stub that returns a fake audio path without calling any external process."""
    def __init__(self, config: ConfigLoader):
        super().__init__(config)

    def change_voice(self, voice, in_game_voice=None, csv_in_game_voice=None,
                     advanced_voice_model=None, voice_accent=None, voice_gender=None,
                     voice_race=None, voice_language=None):
        pass

    def tts_synthesize(self, voiceline: str, final_voiceline_file: str, synth_options: SynthesizationOptions):
        import wave, struct
        n_frames = 160
        with wave.open(final_voiceline_file, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))


@pytest.fixture
def output_manager(default_config: ConfigLoader, mock_ai_client: MockAIClient, monkeypatch) -> ChatManager:
    stub_tts = StubTTS(default_config)
    monkeypatch.setattr('src.utils.get_audio_duration', lambda *args, **kwargs: 1.0)
    return ChatManager(default_config, stub_tts, mock_ai_client)


def get_sentence_list_from_queue(queue: SentenceQueue) -> list[Sentence]:
    sentences = []
    while True:
        sentence = queue.get_next_sentence()
        if not sentence:
            break
        sentences.append(sentence)
    return sentences


# ===========================================================================
# Part 1: asyncio.Event → threading.Event
# ===========================================================================

def test_stop_generation_event_is_threading_event(output_manager: ChatManager):
    """__stop_generation must be a threading.Event, not asyncio.Event."""
    event = output_manager._ChatManager__stop_generation
    assert isinstance(event, threading.Event), (
        f"Expected threading.Event, got {type(event).__module__}.{type(event).__qualname__}"
    )


# ===========================================================================
# Part 2: __is_generating lock
# ===========================================================================

def test_gen_lock_exists(output_manager: ChatManager):
    """ChatManager must have a __gen_lock for protecting __is_generating."""
    lock = output_manager._ChatManager__gen_lock
    assert isinstance(lock, type(threading.Lock()))


def test_stop_generation_waits_for_generating(output_manager: ChatManager):
    """stop_generation() should block until __is_generating becomes False."""
    manager = output_manager
    # Simulate generation in progress
    with manager._ChatManager__gen_lock:
        manager._ChatManager__is_generating = True

    stopped = threading.Event()

    def release_after_delay():
        time.sleep(0.1)
        with manager._ChatManager__gen_lock:
            manager._ChatManager__is_generating = False

    release_thread = threading.Thread(target=release_after_delay)
    release_thread.start()

    start = time.time()
    manager.stop_generation()
    elapsed = time.time() - start

    release_thread.join()
    # stop_generation should have blocked at least ~0.1s waiting for release
    assert elapsed >= 0.05, f"stop_generation returned too quickly ({elapsed:.3f}s)"
    # __is_generating should be False now
    assert not manager._ChatManager__is_generating


def test_stop_generation_timeout_forces_reset(output_manager: ChatManager, monkeypatch):
    """If generation is stuck, stop_generation times out and force-resets __is_generating."""
    manager = output_manager
    with manager._ChatManager__gen_lock:
        manager._ChatManager__is_generating = True

    # Patch the deadline to be in the past so timeout triggers immediately
    original_time = time.time
    call_count = 0
    def fast_time():
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return original_time()
        # After the first call (deadline calculation), return far future
        return original_time() + 20
    monkeypatch.setattr(time, 'time', fast_time)

    manager.stop_generation()
    assert not manager._ChatManager__is_generating, "Timeout should have force-reset __is_generating"


# ===========================================================================
# Part 3: is_more_to_come lock
# ===========================================================================

def test_more_lock_exists():
    """SentenceQueue must have a __more_lock."""
    sq = SentenceQueue()
    lock = sq._SentenceQueue__more_lock
    assert isinstance(lock, type(threading.Lock()))


def test_is_more_to_come_protected_by_lock():
    """Setting is_more_to_come should use the lock (verify via property)."""
    sq = SentenceQueue()
    sq.is_more_to_come = True
    assert sq.is_more_to_come is True
    sq.is_more_to_come = False
    assert sq.is_more_to_come is False


def test_clear_resets_is_more_to_come_atomically():
    """clear() should set is_more_to_come to False atomically."""
    sq = SentenceQueue()
    sq.is_more_to_come = True
    # Add a dummy sentence so clear has something to drain
    mock_sentence = MagicMock(spec=Sentence)
    mock_sentence.text = "test"
    mock_sentence.voice_file = "fake.wav"
    mock_sentence.duration = 1.0
    sq.put(mock_sentence)
    sq.clear()
    assert sq.is_more_to_come is False


# ===========================================================================
# Part 4: Per-NPC max_response_sentences
# ===========================================================================

def test_default_max_response_sentences_when_no_override(
    output_manager: ChatManager,
    example_skyrim_npc_character: Character,
    example_characters_pc_to_npc: Characters,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
):
    """Without a per-NPC override, the global config default is used."""
    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    # 3 sentences from LLM, config allows 2
    client.response_pattern = ["One.", " Two.", " Three."]
    config.max_response_sentences_single = 2
    config.number_words_tts = 1

    asyncio.run(output_manager.process_response(
        example_skyrim_npc_character, mock_queue, mock_messages,
        example_characters_pc_to_npc, mock_actions, tools=None,
    ))

    sentences = get_sentence_list_from_queue(mock_queue)
    # Filter out the empty terminator
    text_sentences = [s for s in sentences if s.content.text.strip()]
    assert len(text_sentences) == 2, f"Expected 2 sentences (config default), got {len(text_sentences)}"


def test_per_npc_max_response_sentences_limits_output(
    output_manager: ChatManager,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    example_skyrim_player_character: Character,
):
    """NPC with max_response_sentences=1 in custom_values should get only 1 sentence."""
    from src.games.equipment import Equipment, EquipmentItem
    # Create NPC with per-NPC override
    npc_with_override = Character(
        base_id='0', ref_id='0', name='Guard', gender=0,
        race='[Race <ImperialRace (00013744)>]',
        is_player_character=False, bio='You are a guard.',
        is_in_combat=False, is_enemy=False, relationship_rank=0,
        is_generic_npc=True, ingame_voice_model='MaleEvenToned',
        tts_voice_model='MaleEvenToned', csv_in_game_voice_model='MaleEvenToned',
        advanced_voice_model='MaleEvenToned', voice_accent='en', voice_language=None,
        equipment=Equipment({'righthand': EquipmentItem('Iron Sword')}),
        custom_character_values={'max_response_sentences': 1},
    )
    chars = Characters()
    chars.add_or_update_character(example_skyrim_player_character)
    chars.add_or_update_character(npc_with_override)

    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One.", " Two.", " Three."]
    config.max_response_sentences_single = 5  # High config default
    config.number_words_tts = 1

    asyncio.run(output_manager.process_response(
        npc_with_override, mock_queue, mock_messages,
        chars, mock_actions, tools=None,
    ))

    sentences = get_sentence_list_from_queue(mock_queue)
    text_sentences = [s for s in sentences if s.content.text.strip()]
    assert len(text_sentences) == 1, f"Expected 1 sentence (per-NPC override), got {len(text_sentences)}"


def test_per_npc_override_higher_than_default(
    output_manager: ChatManager,
    mock_queue: SentenceQueue,
    mock_messages: message_thread,
    mock_actions: list[Action],
    example_skyrim_player_character: Character,
):
    """NPC with max_response_sentences=5 should get all sentences even if config default is 2."""
    from src.games.equipment import Equipment, EquipmentItem
    npc_verbose = Character(
        base_id='0', ref_id='0', name='Guard', gender=0,
        race='[Race <ImperialRace (00013744)>]',
        is_player_character=False, bio='You are a guard.',
        is_in_combat=False, is_enemy=False, relationship_rank=0,
        is_generic_npc=True, ingame_voice_model='MaleEvenToned',
        tts_voice_model='MaleEvenToned', csv_in_game_voice_model='MaleEvenToned',
        advanced_voice_model='MaleEvenToned', voice_accent='en', voice_language=None,
        equipment=Equipment({'righthand': EquipmentItem('Iron Sword')}),
        custom_character_values={'max_response_sentences': 5},
    )
    chars = Characters()
    chars.add_or_update_character(example_skyrim_player_character)
    chars.add_or_update_character(npc_verbose)

    client = output_manager._ChatManager__client
    config = output_manager._ChatManager__config
    client.response_pattern = ["One.", " Two.", " Three."]
    config.max_response_sentences_single = 2  # Low config default
    config.number_words_tts = 1

    asyncio.run(output_manager.process_response(
        npc_verbose, mock_queue, mock_messages,
        chars, mock_actions, tools=None,
    ))

    sentences = get_sentence_list_from_queue(mock_queue)
    text_sentences = [s for s in sentences if s.content.text.strip()]
    assert len(text_sentences) == 3, f"Expected 3 sentences (per-NPC override allows all), got {len(text_sentences)}"
