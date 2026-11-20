"""A local speech engine for performances (083): ``ffmpeg``'s built-in Flite voices.

Flite runs inside ``ffmpeg`` on this device: no network, no account, no cost, and nothing kept once a cue is
spoken. Its voices (``kal``, ``awb``, ``rms``, ``slt``, ...) are synthetic computer voices that ship with the
engine, so a casting names them with ``rights: engine``. Many ``ffmpeg`` builds leave Flite out; then the engine
reports no voices and every cue cast to it stays text.

The script reaches ``ffmpeg`` as a file in a private temporary folder, named by the folder's own relative name,
so no part of the line is ever parsed as filter syntax or a command line. The folder is removed after each cue.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave
from threading import Lock

ENGINE = "flite"
RATE = 16000
#: The longest script one cue may send, and how long one synthesis may run.
MAX_SCRIPT = 4000
TIMEOUT = 60
_SCRIPT_FILE = "line.txt"


def to_wav(samples: bytes, rate: int = RATE) -> bytes:
    """16-bit mono samples as a WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples)
    return buffer.getvalue()


class FliteSynthesizer:
    """Flite through ``ffmpeg``. ``tool`` is the ``ffmpeg`` to run (found on ``PATH`` by default)."""

    engine = ENGINE

    def __init__(self, tool: str | None = None) -> None:
        self._tool = tool if tool is not None else shutil.which("ffmpeg")
        self._voices: tuple[str, ...] | None = None
        self._lock = Lock()

    def _run(self, args: list[str], cwd: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
        assert self._tool is not None
        return subprocess.run([self._tool, "-hide_banner", "-nostdin", *args], capture_output=True, cwd=cwd,
                              timeout=timeout, check=False)

    def voices(self) -> tuple[str, ...]:
        """The Flite voices this ``ffmpeg`` has, asked once (none when it has no ffmpeg or no Flite)."""
        with self._lock:
            if self._voices is None:
                self._voices = self._list()
            return self._voices

    def _list(self) -> tuple[str, ...]:
        if self._tool is None:
            return ()
        try:
            listed = self._run(["-f", "lavfi", "-i", "flite=list_voices=1"])
        except (OSError, subprocess.SubprocessError):
            return ()
        found = []
        for line in listed.stderr.decode("utf-8", "replace").splitlines():
            if line.startswith("[Parsed_flite_") and "]" in line:
                name = line.split("]", 1)[1].strip()
                if name.isalnum() and name.islower() and name not in found:
                    found.append(name)
        return tuple(found)

    def synthesize(self, script: str, voice: str) -> bytes:
        """``script`` spoken in ``voice``, as a 16 kHz mono 16-bit WAV."""
        if not isinstance(script, str) or not script.strip() or len(script) > MAX_SCRIPT:
            raise ValueError("a script is 1 to 4000 characters")
        if voice not in self.voices():
            raise ValueError("this ffmpeg has no such Flite voice")
        folder = tempfile.mkdtemp(prefix="grimoire-voice-")
        try:
            with open(os.path.join(folder, _SCRIPT_FILE), "w", encoding="utf-8") as handle:
                handle.write(script)
            done = self._run(["-loglevel", "error", "-f", "lavfi", "-i",
                              f"flite=textfile={_SCRIPT_FILE}:voice={voice}",
                              "-ar", str(RATE), "-ac", "1", "-f", "s16le", "-"], cwd=folder, timeout=TIMEOUT)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if done.returncode != 0 or not done.stdout:
            raise RuntimeError("flite produced no speech")
        return to_wav(done.stdout, RATE)


__all__ = ["ENGINE", "MAX_SCRIPT", "RATE", "FliteSynthesizer"]
