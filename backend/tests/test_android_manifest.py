"""Foreground service permissions required when Android promotes the service."""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
ANDROID_SRC = ROOT / "android" / "app" / "src" / "main"
MANIFEST = ANDROID_SRC / "AndroidManifest.xml"
NS = "{http://schemas.android.com/apk/res/android}"


def test_the_foreground_service_type_is_permitted_by_a_declared_permission():
    """A `foregroundServiceType` with no matching permission is a
    `SecurityException` the first time the service is promoted -- on API 34+,
    at runtime, on a device, and only once a turn is actually generating. The
    build is perfectly happy with it, so this is the only place it can be said.

    The promotion is what makes a detached turn survive a locked phone, so
    getting it refused is the whole feature failing on the one platform it was
    built for.
    """
    root = ET.parse(MANIFEST).getroot()
    permissions = {e.get(f"{NS}name") for e in root.iter("uses-permission")}
    types = {e.get(f"{NS}foregroundServiceType")
             for e in root.iter("service") if e.get(f"{NS}foregroundServiceType")}

    assert types == {"dataSync"}, f"unexpected foreground service types: {types}"
    assert "android.permission.FOREGROUND_SERVICE" in permissions
    assert "android.permission.FOREGROUND_SERVICE_DATA_SYNC" in permissions
