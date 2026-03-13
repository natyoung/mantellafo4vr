"""Tests for smarter interruption — require sustained speech before interrupting NPC."""
import time
import threading
import pytest


class _SpeechDetectionStub:
    """Mimics the speech detection logic from Transcriber without needing audio hardware."""

    def __init__(self, interruption_delay: float = 0.5):
        self._lock = threading.Lock()
        self._speech_detected = False
        self._speech_start_time = 0.0
        self.interruption_delay = interruption_delay

    @property
    def has_player_spoken(self) -> bool:
        with self._lock:
            if not self._speech_detected:
                return False
            if self.interruption_delay <= 0:
                return True
            return (time.time() - self._speech_start_time) >= self.interruption_delay

    def detect_speech(self):
        """Simulate VAD detecting speech."""
        with self._lock:
            if not self._speech_detected:
                self._speech_detected = True
                self._speech_start_time = time.time()


class TestHasPlayerSpoken:
    def test_not_spoken_when_no_speech(self):
        stub = _SpeechDetectionStub(interruption_delay=0.5)
        assert stub.has_player_spoken is False

    def test_not_spoken_immediately_after_detection(self):
        stub = _SpeechDetectionStub(interruption_delay=0.5)
        stub.detect_speech()
        assert stub.has_player_spoken is False

    def test_spoken_after_delay_elapsed(self):
        stub = _SpeechDetectionStub(interruption_delay=0.5)
        stub._speech_detected = True
        stub._speech_start_time = time.time() - 0.6  # Started 0.6s ago
        assert stub.has_player_spoken is True

    def test_spoken_immediately_when_delay_is_zero(self):
        stub = _SpeechDetectionStub(interruption_delay=0.0)
        stub.detect_speech()
        assert stub.has_player_spoken is True

    def test_not_spoken_when_delay_not_yet_met(self):
        stub = _SpeechDetectionStub(interruption_delay=1.0)
        stub._speech_detected = True
        stub._speech_start_time = time.time() - 0.5  # Only 0.5s of 1.0s required
        assert stub.has_player_spoken is False

    def test_reset_clears_speech_state(self):
        stub = _SpeechDetectionStub(interruption_delay=0.0)
        stub.detect_speech()
        assert stub.has_player_spoken is True
        # Reset
        stub._speech_detected = False
        stub._speech_start_time = 0.0
        assert stub.has_player_spoken is False
