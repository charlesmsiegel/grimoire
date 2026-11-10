"""Sprint 112's adapter ops: the app's model settings, each chat's model usage and this device's call traces.
Called as ``providers.<op>``; see docs/INFERENCE_UI.md and docs/APP_SHELL.md.

Only Sprint 112 edits this module. The ops are ``grimoire.app.inference.InferenceApi``'s, one per open library:
``serve`` gives it the settings file named by ``--model`` (``configure``) before it listens. A library nobody
configured gets read-only settings that leave whatever runtime the text API was built with as it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.app.inference import InferenceApi, SettingsFile
from grimoire.textapi import TextApi

#: Where a text API holds its settings ops: on the text API itself, so they live exactly as long as it does (a
#: table keyed by it would be held alive by the ops, which hold the text API).
_ATTRIBUTE = "_providers_settings"


def configure(api: TextApi, path: Path | None, *, environ: Mapping[str, str] | None = None,
              transport=None) -> InferenceApi:  # type: ignore[no-untyped-def]
    """Give ``api``'s library the settings file ``path`` (``None``: read-only, nothing configured). The runner is
    given the runtime and policy the file configures at once; a missing or unusable file leaves it without a
    model, with the reason ``providers.models`` shows. Called once, before the app serves ``api``."""
    with api.lock:
        if getattr(api, _ATTRIBUTE, None) is not None:
            raise ValueError("this library's model settings are already configured")
        made = InferenceApi(api, SettingsFile(path), owner=api.owner, environ=environ, transport=transport)
        setattr(api, _ATTRIBUTE, made)
        return made


def table(api: TextApi) -> InferenceApi:
    """``api``'s settings ops: the ones ``configure`` made, else read-only ones over no file."""
    with api.lock:
        found = getattr(api, _ATTRIBUTE, None)
        return found if found is not None else configure(api, None)


def _op(name: str):  # type: ignore[no-untyped-def]
    def handler(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
        return table(ctx.api).call(name, args)

    handler.__name__ = handler.__qualname__ = name
    return handler


#: The ops, called as ``providers.<name>`` (docs/INFERENCE_UI.md).
OPS = ("models", "check_models", "save_models", "usage", "diagnostics")

ADAPTER = Adapter("providers", owner="112", ops={name: _op(name) for name in OPS})

__all__ = ["ADAPTER", "OPS", "configure", "table"]
