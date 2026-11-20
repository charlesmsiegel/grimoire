"""Serialized, bounded, memory-only speech session with no implicit fallback.

Adapters are trusted synchronous code and must bound their own execution time.
Calls hold the lock: configure/delete wait for a call, then purge its artifacts.
No callbacks into the session are permitted. A returned value belongs to the
caller; neither deletion nor configuration can recall that caller's copy.
"""
from threading import Lock
from uuid import uuid4

from grimoire.intent import RoutingContext, Utterance

from .control import ControlResult, Recognition, SpeechControls
from .model import (
    ARTIFACTS, FEATURES, MAX_SEQUENCE, Backend, BackendInfo, Disclosure, Policy,
    SpeechResult, UnsupportedBackend, audio, identifier, text, transcript,
)


class SpeechSession:
    def __init__(self, backends: tuple[Backend, ...] = (), policy: Policy = Policy(),
                 context: RoutingContext = RoutingContext(None, False)):
        if not isinstance(policy, Policy):
            raise ValueError("invalid speech policy")
        self._backends = {}
        for backend in (UnsupportedBackend(), *backends):
            info = backend.info
            if not isinstance(info, BackendInfo) or info.id in self._backends:
                raise ValueError("invalid or duplicate backend")
            self._backends[info.id] = (info, backend)
        self._policy = policy
        self._capture_token = uuid4().hex
        self._closed = False
        self._sequence = 0
        self._artifacts = {}
        self._controls = SpeechControls(context)
        self._lock = Lock()

    @property
    def capture_token(self) -> str:
        with self._lock:
            return self._capture_token

    def _describe(self, feature: str) -> Disclosure:
        if feature not in FEATURES:
            raise ValueError("invalid speech feature")
        selection = getattr(self._policy, feature)
        pair = self._backends.get(selection.backend_id)
        info = pair[0] if pair else None
        if self._closed:
            status = "closed"
        elif feature == "stt" and not self._policy.recording:
            status = "recording_off"
        elif selection.mode == "off":
            status = "off"
        elif info is None:
            status = "backend_missing"
        elif feature not in info.features or not info.available:
            status = "unavailable"
        elif selection.mode == "local_only" and (
                info.locality != "local" or info.network is not False or info.retains_inputs is not False):
            status = "privacy_refused"
        elif info.billing != "free":
            status = "spending_unsupported"
        elif selection.mode == "provider" and selection.consent_backend_id != info.id:
            status = "consent_required"
        else:
            status = "ready"
        return Disclosure(status, selection.backend_id, "audio" if feature == "stt" else "text",
                          info.locality if info else "unknown", info.network if info else None,
                          info.retains_inputs if info else None, info.billing if info else "unknown")

    def describe(self, feature: str) -> Disclosure:
        with self._lock:
            return self._describe(feature)

    def perform(self, sequence: int, feature: str, payload: bytes | str, *,
                confirmation_token: str | None = None, capture_token: str | None = None) -> SpeechResult:
        if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
            raise ValueError("invalid speech sequence")
        if feature not in FEATURES:
            raise ValueError("invalid speech feature")
        (audio if feature == "stt" else text)(payload)
        if confirmation_token is not None:
            identifier(confirmation_token)
            if feature != "stt":
                raise ValueError("only STT can capture confirmation")
        with self._lock:
            if sequence <= self._sequence:
                return SpeechResult("already_processed")
            self._sequence = sequence
            if feature == "stt":
                if capture_token != self._capture_token:
                    self._controls.recover()
                    return SpeechResult("stale_capture")
                pending = self._controls.pending
                if confirmation_token is None:
                    self._controls.recover()
                elif pending is None or confirmation_token != pending.token:
                    self._controls.recover()
                    return SpeechResult("stale_confirmation")
            disclosure = self._describe(feature)
            if disclosure.status != "ready":
                self._controls.recover()
                return SpeechResult(disclosure.status)
            backend = self._backends[disclosure.backend][1]
            # Clear the old output before attempting a replacement. An error must not
            # leave yesterday's caption or audio looking like today's result.
            kinds = ("raw_audio", "provisional_transcript") if feature == "stt" else ("synthesized_audio",)
            for kind in kinds:
                self._artifacts.pop(kind, None)
            try:
                output = backend.transcribe(payload) if feature == "stt" else backend.synthesize(payload)
            except Exception:
                # Provider messages can contain private speech and credentials. Do not
                # keep exceptions/tracebacks or silently retry a potentially billed call.
                self._controls.recover()
                return SpeechResult("backend_failed")
            try:
                (transcript if feature == "stt" else audio)(output)
            except ValueError:
                self._controls.recover()
                return SpeechResult("invalid_output")
            if feature == "stt":
                self._retain("raw_audio", payload)
                self._retain("provisional_transcript", output.text)
                return SpeechResult("ok", confirmation_token=confirmation_token,
                                    capture_token=self._capture_token, transcript=output)
            self._retain("synthesized_audio", output)
            return SpeechResult("ok", audio=output)

    def _retain(self, kind: str, value: bytes | str) -> None:
        if kind in self._policy.retain:
            self._artifacts[kind] = value

    def correct_draft(self, draft: str) -> None:
        text(draft)
        with self._lock:
            if self._closed:
                raise ValueError("speech session closed")
            self._controls.recover()
            self._retain("corrected_draft", draft)

    def export(self, kinds: frozenset[str] = ARTIFACTS) -> dict[str, bytes | str]:
        if not isinstance(kinds, frozenset) or not kinds <= ARTIFACTS:
            raise ValueError("invalid artifact selection")
        with self._lock:
            return {k: v for k, v in self._artifacts.items() if k in kinds}

    def delete(self) -> None:
        with self._lock:
            self._capture_token = uuid4().hex
            self._artifacts.clear()
            self._controls.recover()

    def configure(self, policy: Policy) -> None:
        if not isinstance(policy, Policy):
            raise ValueError("invalid speech policy")
        with self._lock:
            if self._closed:
                raise ValueError("speech session closed")
            self._capture_token = uuid4().hex
            self._artifacts.clear()
            self._controls.recover()
            self._policy = policy

    def close(self) -> None:
        with self._lock:
            self._capture_token = uuid4().hex
            self._artifacts.clear()
            self._controls.recover()
            self._closed = True

    @property
    def pending(self):
        with self._lock:
            return self._controls.pending

    def accept(self, event: Recognition) -> ControlResult:
        with self._lock:
            if not isinstance(event, Recognition) or event.capture_token != self._capture_token:
                self._controls.recover()
                return ControlResult("blocked", "stale_capture")
            status = self._describe("stt").status
            if status != "ready":
                self._controls.recover()
                return ControlResult("blocked", status)
            return self._controls.accept(event)

    def confirm(self, answer: Utterance | Recognition, *, token: str | None = None):
        with self._lock:
            if isinstance(answer, Recognition) and (answer.capture_token != self._capture_token
                                                  or self._describe("stt").status != "ready"):
                self._controls.recover()
                return None
            return self._controls.confirm(answer, token=token)

    def keyboard(self, utterance: Utterance):
        with self._lock:
            return self._controls.keyboard(utterance)

    def recover(self) -> None:
        self.delete()

    def configure_context(self, context: RoutingContext) -> None:
        with self._lock:
            self._controls.configure_context(context)
            self._capture_token = uuid4().hex
            self._artifacts.clear()
