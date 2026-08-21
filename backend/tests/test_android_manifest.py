"""The Android resources parse, and the manifest agrees with itself.

`make check-apk` is the real build, and it is deliberately outside `make check`
because it needs `make android-bootstrap` first -- a JDK, the SDK and accepted
licences. The cost of that exclusion is that nothing in the ordinary gate reads
these files at all, and a malformed one reaches CI: an XML comment may not
contain `--`, so a single line of prose in `AndroidManifest.xml` failed
`processDebugMainManifest` with "Error parsing" and nothing local said so.

These are the checks that need no toolchain. They do not replace the APK build;
they catch the two failures that are pure text.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
ANDROID_SRC = ROOT / "android" / "app" / "src" / "main"
MANIFEST = ANDROID_SRC / "AndroidManifest.xml"
NS = "{http://schemas.android.com/apk/res/android}"


def _xml_files() -> list[pathlib.Path]:
    # `build/` holds generated copies; they are the compiler's problem, not the
    # repository's, and they do not exist on a clean checkout.
    return sorted(p for p in ANDROID_SRC.rglob("*.xml") if "build" not in p.parts)


def test_there_are_android_resources_to_check():
    """The sweep below passes vacuously if the tree moves, and a guard that
    cannot fail is worse than no guard -- it reads as coverage."""
    assert MANIFEST.exists(), f"no manifest at {MANIFEST}"
    assert len(_xml_files()) >= 2


@pytest.mark.parametrize("path", _xml_files(), ids=lambda p: p.name)
def test_every_android_resource_is_well_formed(path):
    """Prose is what breaks these, not markup: `--` inside a comment, or an
    unescaped `&` in a string. Both are invisible until the manifest merger or
    `aapt2` refuses, which is minutes into a job most contributors cannot run."""
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        pytest.fail(f"{path.relative_to(ROOT)} is not well-formed XML: {exc}")


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
