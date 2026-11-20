"""Local speech engines for 083's performances (docs/VOICE_CASTING.md): adapters that run a synthesizer on this
device. Each one meets ``grimoire.voicecast.Synthesizer``; ``flite`` is the default (``ffmpeg``'s Flite voices)."""

from .flite import ENGINE, FliteSynthesizer

__all__ = ["ENGINE", "FliteSynthesizer"]
