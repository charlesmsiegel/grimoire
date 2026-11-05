"""Named peer-transfer outcomes (028). A store or 019 sync error that is not listed here propagates."""

from __future__ import annotations


class PeerSyncError(Exception):
    """Base of every peer-transfer refusal."""


class PairingError(PeerSyncError):
    """A pairing request that cannot be recorded (a bad peer id, the device itself, a bad key)."""


class NotPaired(PeerSyncError):
    """There is no active pairing with that device on this library; nothing was sent."""


class AuthenticationFailed(PeerSyncError):
    """The other end could not prove it holds this pairing's key (or refused ours); nothing was synced.

    ``code`` says which: ``unauthenticated`` (a proof did not match), or the serving device's named
    refusal (``not_paired`` when it holds no pairing with this device, ``wrong_device``, ``protocol``).
    """

    def __init__(self, message: str, code: str = "unauthenticated") -> None:
        super().__init__(message)
        self.code = code


class TransportError(PeerSyncError):
    """The connection failed, closed early, timed out or carried a malformed or forged frame."""


class PeerRefused(PeerSyncError):
    """The peer answered a request with a named refusal (``code``); its library is unchanged by that request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class MirrorMismatch(PeerSyncError):
    """The local stand-in of the peer's library does not scan as the peer's own scan does."""
