"""Capturing a suite's output must not wait for an unrelated retained writer."""

import json
import os
import tempfile
import time

import pytest

from tests import synthetic_suite


def _await_file(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        assert time.monotonic() < deadline, f"descendant did not write {path.name}"
        time.sleep(0.01)


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_snapshot_does_not_reposition_a_retained_writer(tmp_path, monkeypatch, channel):
    root = synthetic_suite.write(tmp_path / "s", {
        "hold_output.py": '''
import json
import os
import pathlib
import sys
import time

channel = sys.argv[1]
writer = getattr(sys, channel)
print("retained diagnostic", file=writer, flush=True)
pathlib.Path("ready").touch()
deadline = time.monotonic() + 60
while not pathlib.Path("append").exists() and not pathlib.Path("release").exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("parent did not finish")
    time.sleep(0.01)
if pathlib.Path("append").exists():
    offset = os.lseek(writer.fileno(), 0, os.SEEK_CUR)
    print("late descendant diagnostic", file=writer, flush=True)
    pathlib.Path("written.json").write_text(json.dumps({"offset": offset}), encoding="utf-8")
while not pathlib.Path("release").exists() and time.monotonic() < deadline:
    time.sleep(0.01)
pathlib.Path("finished").touch()
''',
        "test_finished.py": f'''
import pathlib
import subprocess
import sys
import time

children = []

def test_finishes():
    children.append(subprocess.Popen([sys.executable, "hold_output.py", {channel!r}],
                                     stdout=sys.stdout, stderr=sys.stderr))
    deadline = time.monotonic() + 20
    while not pathlib.Path("ready").exists():
        assert time.monotonic() < deadline, "descendant did not start"
        time.sleep(0.01)
''',
    })
    actual_run = synthetic_suite.subprocess.run
    observed = {}

    def completed_controller(*args, **kwargs):
        result = actual_run(*args, **kwargs)
        writer = kwargs[channel]
        observed["offset"] = os.lseek(writer.fileno(), 0, os.SEEK_CUR)
        seek = writer.seek

        def seek_then_append(*seek_args, **seek_kwargs):
            # Schedule a real descendant write at the old extraction race:
            # after the parent moved the shared offset, before it read back.
            value = seek(*seek_args, **seek_kwargs)
            (root / "append").touch()
            _await_file(root / "written.json")
            return value

        monkeypatch.setattr(writer, "seek", seek_then_append)
        return result

    monkeypatch.setattr(synthetic_suite.subprocess, "run", completed_controller)
    try:
        result = synthetic_suite.run(root, "-s", timeout=30)
        # With an independent reader there is no writer seek to trigger the
        # race hook, so exercise the retained writer after extraction too.
        (root / "append").touch()
        _await_file(root / "written.json")
        written = json.loads((root / "written.json").read_text(encoding="utf-8"))
        assert observed["offset"] > 0
        assert written["offset"] == observed["offset"]
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout
        assert "retained diagnostic" in getattr(result, channel)
    finally:
        (root / "release").touch()
        if (root / "ready").exists():
            _await_file(root / "finished")


def test_capture_keeps_locale_decoding_and_universal_newlines(tmp_path):
    root = synthetic_suite.write(tmp_path / "s", {"test_text.py": '''
import locale
import os

def test_text():
    text = "caf\\u00e9\\r\\nsecond\\rthird\\n"
    for descriptor in (1, 2):
        os.write(descriptor, text.encode(locale.getpreferredencoding(False)))
'''})
    result = synthetic_suite.run(root, "-s", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "café\nsecond\nthird\n" in result.stdout
    assert result.stderr == "café\nsecond\nthird\n"


def test_snapshot_excludes_output_appended_after_its_size_sample(monkeypatch):
    with (tempfile.NamedTemporaryFile(mode="w+") as writer,
          open(writer.name, "rb", opener=synthetic_suite._capture_reader) as reader):
        writer.write("first\n")
        writer.flush()
        read = reader.read

        def append_then_read(*args, **kwargs):
            writer.write("late\n")
            writer.flush()
            return read(*args, **kwargs)

        monkeypatch.setattr(reader, "read", append_then_read)
        assert synthetic_suite._snapshot(reader, writer) == "first\n"


@pytest.mark.parametrize("encoding,tail", [
    ("utf-8", "\u20ac"), ("utf-16", "\u20ac"), ("utf-16-le", "\U0001f642"),
])
def test_snapshot_keeps_complete_prefix_when_retained_writer_finishes_a_character(monkeypatch, encoding, tail):
    text = "caf\u00e9\r\nsecond\rthird\n" + tail
    encoded = text.encode(encoding)
    # Leave a valid but incomplete code point at the sampled end. UTF-16 LE
    # also exercises a complete high surrogate awaiting its low surrogate.
    cut = 2 if encoding == "utf-16-le" else 1
    with (tempfile.NamedTemporaryFile(mode="w+", encoding=encoding) as writer,
          open(writer.name, "rb", opener=synthetic_suite._capture_reader) as reader):
        os.write(writer.fileno(), encoded[:-cut])
        retained = os.dup(writer.fileno())
        read = reader.read

        def finish_then_read(*args, **kwargs):
            # A retained output handle appends after fstat sampled the size,
            # but the snapshot must still stop at that original byte boundary.
            os.write(retained, encoded[-cut:])
            return read(*args, **kwargs)

        try:
            monkeypatch.setattr(reader, "read", finish_then_read)
            assert synthetic_suite._snapshot(reader, writer) == "caf\u00e9\nsecond\nthird\n"
            assert os.lseek(retained, 0, os.SEEK_CUR) == len(encoded)
            monkeypatch.setattr(reader, "read", read)
            reader.seek(0)
            assert synthetic_suite._snapshot(reader, writer) == "caf\u00e9\nsecond\nthird\n" + tail
        finally:
            os.close(retained)


@pytest.mark.parametrize("encoding,data", [
    ("utf-8", b"valid\xff"), ("utf-16-le", b"\x00\xd8a\x00"),
])
def test_snapshot_still_refuses_invalid_completed_sequences(encoding, data):
    with (tempfile.NamedTemporaryFile(mode="w+", encoding=encoding) as writer,
          open(writer.name, "rb", opener=synthetic_suite._capture_reader) as reader):
        os.write(writer.fileno(), data)
        with pytest.raises(UnicodeDecodeError):
            synthetic_suite._snapshot(reader, writer)


def test_snapshot_keeps_the_writers_decoding_error_policy():
    with (tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", errors="replace") as writer,
          open(writer.name, "rb", opener=synthetic_suite._capture_reader) as reader):
        os.write(writer.fileno(), b"valid\xff\r\npartial\xe2")
        assert synthetic_suite._snapshot(reader, writer) == "valid\ufffd\npartial"


def test_finished_suite_returns_while_descendant_holds_its_output(tmp_path):
    # A child pytest is the process whose result this helper promises. An
    # inherited pipe can stay open after that controller has finished; the
    # descendant below makes that lifetime independent of the suite result.
    root = synthetic_suite.write(tmp_path / "s", {
        "hold_output.py": '''
import pathlib
import sys
import time

print("descendant stdout", flush=True)
print("descendant stderr", file=sys.stderr, flush=True)
pathlib.Path("ready").touch()
deadline = time.monotonic() + 60
while not pathlib.Path("release").exists() and time.monotonic() < deadline:
    time.sleep(0.01)
''',
        "test_finished.py": '''
import pathlib
import subprocess
import sys
import time

children = []

def test_finishes():
    children.append(subprocess.Popen([sys.executable, "hold_output.py"],
                                     stdout=sys.stdout, stderr=sys.stderr))
    deadline = time.monotonic() + 20
    while not pathlib.Path("ready").exists():
        assert time.monotonic() < deadline, "descendant did not start"
        time.sleep(0.01)
''',
    })
    try:
        # No capture in the child: the descendant must retain the helper's
        # actual output handles, rather than pytest's own temporary files.
        result = synthetic_suite.run(root, "-s", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed" in result.stdout
        assert "descendant stdout" in result.stdout
        assert "descendant stderr" in result.stderr
        assert not (root / "release").exists()
    finally:
        (root / "release").touch()
