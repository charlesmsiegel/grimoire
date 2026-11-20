"""A test-owned reference recognizer for 067's acoustic evaluation: CMU PocketSphinx, offline, free.

This is evaluation equipment, not 061's production backend. It decodes against a small command grammar with
an out-of-grammar word loop, so speech that is not a command can come out as ordinary words instead of being
forced into the nearest command. ``confidence`` is PocketSphinx's lattice posterior for the hypothesis. That
posterior is not a calibrated probability; the evaluation pins its routing threshold (``MIN_CONFIDENCE``) from
the clean positive clips only, never from negative cases.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from pocketsphinx import Decoder

from grimoire.intent import Utterance
from grimoire.speech import BackendInfo
from grimoire.speech.acoustic import read_wav

MIN_CONFIDENCE = 0.05

GRAMMAR = """#JSGF V1.0;
grammar commands;
public <utterance> = <command> | <other>+;
<command> = (open | show) [the] (inventory | map | journal | settings) | pause | resume | stop | cancel
          | never mind | undo | delete [the] scene | yes | no;
<other> = the | a | and | of | to | in | it | is | we | you | was | for | that | this | at | or;
"""


class SphinxRecognizer:
    info = BackendInfo("pocketsphinx-eval", frozenset({"stt"}), "local", False, False, "free", True)

    def __init__(self):
        with tempfile.TemporaryDirectory() as folder:
            grammar = Path(folder) / "commands.gram"
            grammar.write_text(GRAMMAR)
            self._decoder = Decoder(samprate=16000, jsgf=str(grammar), bestpath=True, loglevel="FATAL",
                                    beam=1e-30, wbeam=1e-20, pbeam=1e-30)

    def transcribe(self, audio: bytes) -> Utterance:
        pcm = read_wav(audio)
        if pcm.rate != 16000:
            raise ValueError("the reference model is 16 kHz")
        decoder = self._decoder
        decoder.start_utt()
        decoder.process_raw(pcm.samples.tobytes(), full_utt=True)
        decoder.end_utt()
        hypothesis = decoder.hyp()
        text = hypothesis.hypstr if hypothesis is not None else ""
        confidence = min(1.0, max(0.0, hypothesis.prob)) if hypothesis is not None else 0.0
        return Utterance(text, origin="voice", confidence=confidence)

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError("recognition only")
