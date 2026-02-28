"""Tests for STT error handling in whisper_transcribe."""
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
