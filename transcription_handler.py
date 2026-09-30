"""
Audio transcription handler using faster-whisper (local, no API cost).

Transcribes audio from video files and URLs using OpenAI Whisper models
running entirely on-device via the faster-whisper library.
"""
import os
import subprocess
import tempfile
import time
from utils import logger
import config


class TranscriptionHandler:
    """Handles audio transcription from video files and URLs using Whisper"""

    def __init__(self):
        """Initialize transcription handler and load Whisper model"""
        self.enabled = getattr(config, 'TRANSCRIPTION_ENABLED', False)
        self.model_name = getattr(config, 'TRANSCRIPTION_MODEL', 'large-v3-turbo')
        self.device = getattr(config, 'TRANSCRIPTION_DEVICE', 'auto')
        self.max_duration = getattr(config, 'TRANSCRIPTION_MAX_DURATION', 300)
        self._model = None

        if not self.enabled:
            logger.info("TranscriptionHandler disabled (TRANSCRIPTION_ENABLED=False)")
            return

        try:
            from faster_whisper import WhisperModel  # noqa: F401 — import check only
            logger.info(
                f"TranscriptionHandler initialized "
                f"(model={self.model_name}, device={self.device}, "
                f"max_duration={self.max_duration}s)"
            )
        except ImportError:
            logger.error(
                "faster-whisper is not installed. "
                "Run: pip install faster-whisper\n"
                "Transcription will be disabled."
            )
            self.enabled = False

    def _get_model(self):
        """Lazy-load the Whisper model on first use."""
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel

        device = self.device
        compute_type = "auto"

        if device == "auto":
            # Try CUDA first, fall back to CPU
            try:
                import torch
                if torch.cuda.is_available():
                    device = "cuda"
                    compute_type = "float16"
                else:
                    device = "cpu"
                    compute_type = "int8"
            except ImportError:
                device = "cpu"
                compute_type = "int8"

        logger.info(f"Loading Whisper model '{self.model_name}' on {device} ({compute_type})...")
        start = time.time()
        self._model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        elapsed = time.time() - start
        logger.info(f"Whisper model loaded in {elapsed:.1f}s")
        return self._model

    def _get_duration(self, file_path):
        """
        Get video/audio duration in seconds using ffprobe.

        Returns:
            float: Duration in seconds, or None if it cannot be determined.
        """
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'error',
                    '-show_entries', 'format=duration',
                    '-of', 'default=noprint_wrappers=1:nokey=1',
                    file_path
                ],
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        return None

    def transcribe_audio_file(self, file_path):
        """
        Transcribe audio from a local video or audio file.

        Args:
            file_path: Path to the media file (mp4, mov, avi, mp3, wav, etc.)

        Returns:
            str: Transcribed text, or empty string if transcription fails or is skipped.
        """
        if not self.enabled:
            return ""

        if not os.path.exists(file_path):
            logger.warning(f"Transcription skipped — file not found: {file_path}")
            return ""

        # Duration guard
        duration = self._get_duration(file_path)
        if duration is not None:
            if duration > self.max_duration:
                logger.info(
                    f"Transcription skipped — video too long "
                    f"({duration:.0f}s > {self.max_duration}s limit): {os.path.basename(file_path)}"
                )
                return ""
            logger.debug(f"Video duration: {duration:.1f}s")

        try:
            start = time.time()
            model = self._get_model()
            segments, info = model.transcribe(file_path, beam_size=5)
            transcript = " ".join(seg.text.strip() for seg in segments).strip()
            elapsed = time.time() - start

            if transcript:
                logger.info(
                    f"✓ Transcribed {os.path.basename(file_path)}: "
                    f"{len(transcript)} chars in {elapsed:.1f}s "
                    f"(detected language: {info.language})"
                )
            else:
                logger.debug(f"Transcription returned no text for: {os.path.basename(file_path)}")

            return transcript

        except Exception as e:
            logger.error(f"Error transcribing {file_path}: {e}", exc_info=True)
            return ""

    def transcribe_from_url(self, url):
        """
        Download audio from a URL (audio-only stream) and transcribe it.

        Uses ffmpeg to extract only the audio stream into a temporary WAV file,
        then transcribes and deletes the temp file.

        Args:
            url: Direct media URL (e.g. Twitter video URL from gallery-dl -g)

        Returns:
            str: Transcribed text, or empty string if download/transcription fails.
        """
        if not self.enabled:
            return ""

        if not url or not url.startswith('http'):
            return ""

        tmp_path = None
        try:
            # Write audio-only to a temp WAV file
            tmp_fd, tmp_path = tempfile.mkstemp(suffix='.wav')
            os.close(tmp_fd)

            logger.debug(f"Downloading audio stream from URL for transcription...")
            result = subprocess.run(
                [
                    'ffmpeg', '-y',
                    '-i', url,
                    '-vn',               # no video
                    '-acodec', 'pcm_s16le',
                    '-ar', '16000',      # Whisper expects 16 kHz
                    '-ac', '1',          # mono
                    tmp_path
                ],
                capture_output=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.warning(
                    f"ffmpeg failed to download audio from URL "
                    f"(exit {result.returncode}): {url[:80]}"
                )
                return ""

            return self.transcribe_audio_file(tmp_path)

        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg timed out downloading audio from: {url[:80]}")
            return ""
        except FileNotFoundError:
            logger.error(
                "ffmpeg not found in PATH. "
                "Install ffmpeg to enable URL-based audio transcription."
            )
            return ""
        except Exception as e:
            logger.error(f"Error in transcribe_from_url: {e}", exc_info=True)
            return ""
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
