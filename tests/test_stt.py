"""Tests for STT error handling in whisper_transcribe."""
import json
import threading
import time
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


class FakeTranscriber:
    """Minimal stand-in with just the attributes whisper_transcribe needs."""
    SAMPLING_RATE = 16000

    def __init__(self):
        self.transcribe_model = None  # forces server path
        self.whisper_url = "https://api.openai.com/v1"  # triggers openai branch
        self.whisper_model = "whisper-1"
        self.language = None
        self.whisper_service = "OpenAI"
        self._Transcriber__ignore_list = ["thank you", "thanks for watching"]


class TestWhisperTranscribeOpenAIErrorHandling:
    """Bug: e.code accessed on generic Exception (no .code attr) + response_data unbound after exception."""

    def _call_whisper_transcribe(self, fake, audio):
        """Call the real whisper_transcribe method bound to our fake instance."""
        from src.stt import Transcriber
        return Transcriber.whisper_transcribe(fake, audio, prompt="")

    @patch("src.stt.utils.play_error_sound")
    def test_exception_without_code_attr_does_not_crash(self, mock_sound):
        """A ConnectionError (no .code) must not raise AttributeError."""
        fake = FakeTranscriber()

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = ConnectionError("network down")
        fake._Transcriber__generate_sync_client = MagicMock(return_value=mock_client)

        audio = np.zeros(16000, dtype=np.float32)

        # Currently crashes with AttributeError: 'ConnectionError' object has no attribute 'code'
        # After fix: should return '' (empty transcription) without crashing
        with patch("builtins.input", return_value=""):  # prevent blocking on input()
            result = self._call_whisper_transcribe(fake, audio)

        assert result == ""
        mock_client.close.assert_called_once()

    @patch("src.stt.utils.play_error_sound")
    def test_model_not_found_error_with_code_still_works(self, mock_sound):
        """An exception WITH .code=404 should still be handled properly."""
        fake = FakeTranscriber()

        error = Exception("model not found")
        error.code = 404
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = error
        fake._Transcriber__generate_sync_client = MagicMock(return_value=mock_client)

        audio = np.zeros(16000, dtype=np.float32)

        with patch("builtins.input", return_value=""):
            result = self._call_whisper_transcribe(fake, audio)

        assert result == ""
        mock_client.close.assert_called_once()


class FakeTranscriberCustomServer(FakeTranscriber):
    """Fake with custom server URL (non-openai branch)."""
    def __init__(self):
        super().__init__()
        self.whisper_url = "http://localhost:9000/transcribe"  # no 'openai' → custom server branch


class TestWhisperTranscribeCustomServerErrorHandling:
    """Bug: non-200 response logged but execution continues to json.loads() which crashes."""

    def _call(self, fake, audio):
        from src.stt import Transcriber
        return Transcriber.whisper_transcribe(fake, audio, prompt="")

    @patch("src.stt.requests.post")
    def test_non_200_returns_empty_instead_of_crashing(self, mock_post):
        """A 500 error with HTML body must not crash on json.loads()."""
        fake = FakeTranscriberCustomServer()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b"Internal Server Error"
        mock_response.text = "<html>Internal Server Error</html>"
        mock_post.return_value = mock_response

        audio = np.zeros(16000, dtype=np.float32)
        result = self._call(fake, audio)
        assert result == ""

    @patch("src.stt.requests.post")
    def test_200_with_missing_text_key_returns_empty(self, mock_post):
        """A 200 response with no 'text' key should return '' not None."""
        fake = FakeTranscriberCustomServer()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({"error": "no speech detected"})
        mock_post.return_value = mock_response

        audio = np.zeros(16000, dtype=np.float32)
        result = self._call(fake, audio)
        assert result == ""


class TestStopListeningTimeout:
    """Bug: _processing_thread.join() has no timeout — hangs if thread is stuck."""

    def test_stop_listening_does_not_hang_on_stuck_thread(self):
        """stop_listening() must return within a few seconds even if thread won't die."""
        from src.stt import Transcriber
        import queue as queue_mod

        fake = MagicMock()
        fake._running = True
        fake._speech_detected = True
        fake._stream = None
        fake._audio_queue = queue_mod.Queue()
        fake.loglevel = 23

        # Create a thread that blocks for a long time
        hang_event = threading.Event()
        def hang():
            hang_event.wait(timeout=30)  # would block 30s without the fix
        stuck_thread = threading.Thread(target=hang, daemon=True)
        stuck_thread.start()
        fake._processing_thread = stuck_thread

        start = time.time()
        Transcriber.stop_listening(fake)
        elapsed = time.time() - start

        # Must return quickly (within 5s), not hang for 30s
        assert elapsed < 5.0, f"stop_listening hung for {elapsed:.1f}s (thread join has no timeout)"
        hang_event.set()  # clean up the thread
        stuck_thread.join(timeout=2)
