from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Optional
import json


@dataclass
class AVPaths:
    attempt_dir: Path
    video_path: Path
    audio_dir: Path


class InterviewAV:
    """Optional local AV utilities for interview mode."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.disable_camera = _env_flag("MEDPREPAI_DISABLE_CAMERA")
        self.disable_mic = _env_flag("MEDPREPAI_DISABLE_MIC")
        self.disable_tts = _env_flag("MEDPREPAI_DISABLE_TTS")
        self.disable_transcribe = _env_flag("MEDPREPAI_DISABLE_TRANSCRIBE")

        self._video_thread: Optional[threading.Thread] = None
        self._video_stop = threading.Event()
        self._preview_proc: Optional[subprocess.Popen] = None
        self._preview_path: Optional[Path] = None
        self._preview_error: Optional[str] = None
        self._preview_reader: Optional[threading.Thread] = None
        self._mic_stream = None
        self._mic_file = None
        self._mic_audio_q: "queue.Queue[bytes]" = queue.Queue()
        self.paths: Optional[AVPaths] = None

    def start_attempt(self, start_video: bool = False) -> AVPaths:
        ts = time.strftime("%Y%m%d_%H%M%S")
        attempt_dir = self.base_dir / f"attempt_{ts}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        video_path = attempt_dir / "session_video.mp4"
        audio_dir = attempt_dir / "answers_audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        self.paths = AVPaths(attempt_dir=attempt_dir, video_path=video_path, audio_dir=audio_dir)
        if start_video and not self.disable_camera:
            self._start_video_recording(video_path)
        if not self.disable_camera:
            self._start_preview_capture()
        return self.paths

    def get_camera_frame(self):
        if self._preview_path is None or not self._preview_path.exists():
            return None
        try:
            return self._preview_path.read_bytes()
        except Exception:
            return None
        
    def get_gaze_status(self) -> dict | None:
        """Read latest gaze status written by camera_preview subprocess."""
        if self._preview_path is None:
            return None
        gaze_path = self._preview_path.with_name("gaze_status.json")
        try:
            return json.loads(gaze_path.read_text())
        except Exception:
            return None
        
    def preview_status(self) -> tuple[str, Optional[str]]:
        if self._preview_proc is None:
            return "not_running", self._preview_error
        code = self._preview_proc.poll()
        if code is None:
            return "running", self._preview_error
        return "exited", self._preview_error or f"preview process exited ({code})"

    def stop_attempt(self):
        self._stop_mic_recording()
        self._stop_video_recording()
        self._stop_preview_capture()

    def stop_mic_only(self):
        self._stop_mic_recording()

    def start_mic_recording(self, out_wav: Path) -> tuple[bool, str]:
        if self.disable_mic:
            return False, "Microphone disabled (MEDPREPAI_DISABLE_MIC=1)."
        try:
            import sounddevice as sd
            import soundfile as sf
        except Exception:
            return False, "Microphone dependencies missing. Install `sounddevice` and `soundfile`."

        if self._mic_stream is not None:
            return False, "Microphone recording already in progress."

        try:
            self._mic_file = sf.SoundFile(str(out_wav), mode="w", samplerate=16000, channels=1, subtype="PCM_16")
        except Exception as e:
            return False, f"Cannot create audio file: {e}"

        self._mic_audio_q = queue.Queue()

        def audio_callback(indata, frames, _time_info, status):
            if status:
                pass
            self._mic_audio_q.put(indata.copy())

        def writer_loop():
            while self._mic_stream is not None:
                try:
                    data = self._mic_audio_q.get(timeout=0.2)
                    if self._mic_file is not None:
                        self._mic_file.write(data)
                except queue.Empty:
                    continue

        try:
            self._mic_stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="float32",
                callback=audio_callback,
            )
            self._mic_stream.start()
            threading.Thread(target=writer_loop, daemon=True).start()
            return True, f"Recording audio to {out_wav}"
        except Exception as e:
            self._mic_stream = None
            if self._mic_file is not None:
                self._mic_file.close()
                self._mic_file = None
            return False, f"Failed to start microphone: {e}"

    def stop_mic_and_transcribe(self) -> tuple[bool, str]:
        self._stop_mic_recording()
        if not self.paths:
            return False, "No active interview attempt."
        wavs = sorted(self.paths.audio_dir.glob("*.wav"))
        if not wavs:
            return False, "No audio files recorded yet."
        if self.disable_transcribe:
            return False, "Transcription disabled (MEDPREPAI_DISABLE_TRANSCRIBE=1)."
        latest = wavs[-1]
        ok, text_or_reason = self._transcribe_with_faster_whisper(latest)
        if not ok and text_or_reason:
            return False, text_or_reason
        if not ok:
            return False, "Transcription failed."
        return True, text_or_reason

    def speak(self, text: str, on_done=None):
        if not text.strip():
            if on_done:
                on_done()
            return
        if self.disable_tts:
            if on_done:
                on_done()
            return
        threading.Thread(target=self._speak_impl, args=(text, on_done), daemon=True).start()

    def _speak_impl(self, text: str, on_done=None):
        if sys.platform != "darwin":
            try:
                import pyttsx3

                engine = pyttsx3.init()
                engine.setProperty("rate", 175)
                engine.say(text)
                engine.runAndWait()
                if on_done:
                    on_done()
                return
            except Exception:
                pass
        
        try:
            subprocess.run(["say", "-v", "Daniel", "-r", "165", text], check=False)
            if on_done:
                on_done()
        except Exception:
            return
        # try:
        #     subprocess.run(["say", text], check=False)
        #     if on_done:
        #         on_done()
        # except Exception:
        #     return

    def _start_video_recording(self, out_path: Path):
        try:
            import cv2
        except Exception:
            return

        self._video_stop.clear()

        def run():
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
            fps = cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps < 1:
                fps = 20.0
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(out_path), fourcc, fps, (width, height))
            try:
                while not self._video_stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.05)
                        continue
                    writer.write(frame)
            finally:
                writer.release()
                cap.release()

        self._video_thread = threading.Thread(target=run, daemon=True)
        self._video_thread.start()

    def _stop_video_recording(self):
        if self._video_thread is None:
            return
        self._video_stop.set()
        self._video_thread.join(timeout=2.0)
        self._video_thread = None

    def _start_preview_capture(self):
        if self._preview_proc is not None:
            return
        if not self.paths:
            return
        self._preview_path = self.paths.attempt_dir / "preview.jpg"
        self._preview_error = None
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--camera-preview", str(self._preview_path)]
        else:
            cmd = [sys.executable, "-m", "app.camera_preview", str(self._preview_path)]
        try:
            self._preview_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._preview_reader = threading.Thread(target=self._read_preview_output, daemon=True)
            self._preview_reader.start()
        except Exception:
            self._preview_proc = None
            self._preview_path = None
            self._preview_reader = None

    def _stop_preview_capture(self):
        if self._preview_proc is None:
            return
        try:
            # Send SIGTERM first — this triggers our signal handler in camera_preview.py
            # which sets _running=False and lets the finally block release the camera cleanly.
            self._preview_proc.terminate()
            self._preview_proc.wait(timeout=3)  # Give it 3s to release camera gracefully
        except subprocess.TimeoutExpired:
            # Only force kill if graceful shutdown timed out
            try:
                self._preview_proc.kill()
                self._preview_proc.wait(timeout=1)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if self._preview_proc.stdout:
                self._preview_proc.stdout.close()
        except Exception:
            pass
        # Add a small delay after camera release before allowing a new subprocess
        # to grab the camera — critical on macOS to avoid autorelease pool corruption.
        time.sleep(0.5)
        self._preview_proc = None
        self._preview_path = None
        self._preview_reader = None

    def _stop_mic_recording(self):
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
        if self._mic_file is not None:
            try:
                self._mic_file.close()
            except Exception:
                pass
            self._mic_file = None

    def close(self):
        self._stop_mic_recording()
        self._stop_video_recording()
        self._stop_preview_capture()

    def _transcribe_with_faster_whisper(self, wav_path: Path) -> tuple[bool, str]:
        try:
            from faster_whisper import WhisperModel
        except Exception:
            return False, "Missing dependency: `faster-whisper` (pip install faster-whisper)."

        try:
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(wav_path), language="en")
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
            return True, text
        except Exception as e:
            return False, f"Whisper transcription error: {e}"

    def _read_preview_output(self):
        proc = self._preview_proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                cleaned = (line or "").strip()
                if cleaned:
                    self._preview_error = cleaned
        except Exception:
            return


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}
