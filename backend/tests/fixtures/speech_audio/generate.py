"""Regenerate the synthetic speech clips (067). Run from the repository root:

    python backend/tests/fixtures/speech_audio/generate.py

Needs ``espeak-ng``, the MBROLA voices ``us1 us2 us3 en1`` (Debian/Ubuntu: ``espeak-ng mbrola-us1 mbrola-us2
mbrola-us3 mbrola-en1``) and ``ffmpeg`` (to resample to 16 kHz mono PCM16). Every clip is invented text read by
a speech synthesizer: no person was recorded, and no voice stands in for any real speaker, accent or
disability. ``MANIFEST.json`` records each clip's text, voice, rate, pitch and SHA-256 so a test can prove the
committed bytes are what this script made. Synthesizer versions differ, so regenerating elsewhere may change
bytes; commit regenerated clips together with their manifest and rerun the acoustic tests.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The person at the device: four synthesized voices, two US English, one British English, and the plain
# formant synthesizer, whose robotic delivery is the "atypical" stimulus a recognizer may fail on.
SPEAKERS = {"us1": "mb-us1", "us2": "mb-us2", "us3": "mb-us3", "en1": "mb-en1", "robot": "en-us"}
COMMANDS = {"open": "open inventory", "pause": "pause", "stop": "stop", "delete": "delete scene", "yes": "yes"}
# Delivery variants on one voice: slow and fast speech, a higher pitch.
VARIANTS = {"slow": dict(rate=95), "fast": dict(rate=215), "high": dict(pitch=85)}
# Other people in the room: never the person at the device.
BACKGROUND = {
    "bg-delete": ("mb-us2", "okay so we should delete the scene and start over tomorrow"),
    "bg-yes": ("mb-us3", "yes"),
    "bg-chatter": ("mb-en1", "the weather was lovely so we walked home along the river"),
    "bg-command": ("mb-us1", "delete scene"),
    "bg-open": ("mb-us3", "open the map"),
}


def _synth(path: Path, voice: str, text: str, rate: int = 140, pitch: int = 50) -> None:
    raw = path.with_suffix(".raw.wav")
    subprocess.run(["espeak-ng", "-v", voice, "-s", str(rate), "-p", str(pitch), "-w", str(raw), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1",
                    str(path)], check=True)
    raw.unlink()


def main() -> None:
    clips = {}

    def add(name: str, voice: str, text: str, role: str, **delivery) -> None:
        path = HERE / f"{name}.wav"
        _synth(path, voice, text, **delivery)
        clips[name] = {"text": text, "voice": voice, "role": role, **delivery,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    for speaker, voice in SPEAKERS.items():
        for key, text in COMMANDS.items():
            add(f"{speaker}-{key}", voice, text, "foreground")
    for variant, delivery in VARIANTS.items():
        for key in ("open", "yes"):
            add(f"us1-{key}-{variant}", "mb-us1", COMMANDS[key], "foreground", **delivery)
    add("us1-disfluent", "mb-us1", "um pause", "foreground")
    for name, (voice, text) in BACKGROUND.items():
        add(name, voice, text, "background")
    (HERE / "MANIFEST.json").write_text(json.dumps({
        "generator": "espeak-ng + MBROLA, resampled by ffmpeg to 16 kHz mono PCM16",
        "synthetic": True, "clips": clips}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
