"""Boot and background I/O that used to scale with the library's size.

Each case here pins a cost that was paid on every start, every listing or
every call regardless of whether anything had changed -- and, beside it, the
correctness the cheaper path must not give up:

- the identity backfill head-read every scene in the library at every boot,
  under each campaign's lock and before the server accepted a connection;
- `best_stamp` ran two `strptime`s per scene on every `GET /campaigns`;
- a tiktoken load that failed (offline, no cached BPE) was retried on every
  `count_tokens` call, because `lru_cache` does not cache a raise;
- the image fetcher built a certifi SSL context at import, on every process
  start, for a feature most sessions never touch;
- backups deflated already-compressed media at a ratio of ~1.0, and a killed
  backup left an archive-sized temp that nothing ever removed.
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import grimoire.store as store
from grimoire.store import backups, fetch, frontmatter, migrations, statcache, tokens
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.scenes import identity as scenes_identity

# ---- the identity backfill watermark --------------------------------------


def _new_campaign(name: str = "Saltmarch") -> str:
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign(name, wid)


def _scenes_dir(cid: str) -> Path:
    return store.scenes.paths._scenes_dir(cid)


def _age(path: Path, seconds: float = 30.0) -> None:
    """Push a directory's mtime out of the racy window, as the next boot would
    find it. Every write this test makes lands within the same second, so
    without this the watermark would (correctly) refuse to trust any of them."""
    then = time.time_ns() - int(seconds * 1e9)
    os.utime(path, ns=(then, then))


def _count_head_reads(monkeypatch) -> list[str]:
    """Record every scene the backfill head-reads. `scene_identity` is the
    per-scene open the watermark exists to avoid."""
    seen: list[str] = []
    real = scenes_identity.scene_identity

    def counting(cid: str, sid: str):
        seen.append(sid)
        return real(cid, sid)

    monkeypatch.setattr(scenes_identity, "scene_identity", counting)
    return seen


def _marks(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cache" / "scene-identities.json").read_text(encoding="utf-8"))


def test_a_second_backfill_over_an_unchanged_store_opens_no_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    store.scenes.create_scene(cid, "Mara")
    store.scenes.create_scene(cid, "Winifred")
    _age(_scenes_dir(cid))

    reads = _count_head_reads(monkeypatch)
    migrations.backfill_scene_identities()      # a full pass, which records
    assert len(reads) == 2
    assert cid in _marks(tmp_path)

    reads.clear()
    migrations.backfill_scene_identities()      # nothing moved: skipped
    assert reads == []


def test_a_copied_scene_with_a_duplicate_identity_is_re_minted_next_boot(
        tmp_path, monkeypatch):
    """The case the watermark could most plausibly miss, and does not: a file
    copied in by hand (or restored, or landed by a sync client) creates a
    directory entry, which moves the signature, so the campaign is rescanned
    and the duplicate token re-minted exactly as it was before the watermark."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    a = store.scenes.create_scene(cid, "Mara")
    d = _scenes_dir(cid)
    _age(d)
    migrations.backfill_scene_identities()
    assert cid in _marks(tmp_path)

    copy = d / "0099--copy-of-mara.md"
    shutil.copyfile(d / f"{a}.md", copy)
    assert store.scenes.scene_identity(cid, copy.stem) == store.scenes.scene_identity(cid, a)

    migrations.backfill_scene_identities()

    ia, ib = store.scenes.scene_identity(cid, a), store.scenes.scene_identity(cid, copy.stem)
    assert ia and ib and ia != ib
    assert store.scenes.find_by_identity(cid, ib) == copy.stem


@pytest.mark.skipif(os.name == "nt", reason="st_ctime is the creation time on Windows")
def test_a_directory_whose_mtime_was_put_back_is_still_rescanned(tmp_path, monkeypatch):
    """A tool that changes a directory's entries and then restores its mtime --
    `rsync -a` or `cp -a` over the top, a sync client that preserves directory
    times -- leaves mtime and inode exactly as recorded. ctime is the part of
    the signature that still moves, because nothing outside the kernel can set
    it, so the copied-in duplicate is re-minted all the same."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    a = store.scenes.create_scene(cid, "Mara")
    d = _scenes_dir(cid)
    _age(d)
    migrations.backfill_scene_identities()
    assert cid in _marks(tmp_path)
    recorded = d.stat()

    copy = d / "0099--copy-of-mara.md"
    shutil.copyfile(d / f"{a}.md", copy)
    # `utime` stamps ctime with the kernel's clock, which ticks coarsely: repeat
    # it until the tick has passed rather than hoping the steps above took long
    # enough.
    deadline = time.monotonic() + 2.0
    os.utime(d, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    while d.stat().st_ctime_ns == recorded.st_ctime_ns and time.monotonic() < deadline:
        os.utime(d, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    now = d.stat()
    assert (now.st_mtime_ns, now.st_ino) == (recorded.st_mtime_ns, recorded.st_ino)
    assert now.st_ctime_ns != recorded.st_ctime_ns, "the premise: utime moves ctime"

    migrations.backfill_scene_identities()

    ia, ib = store.scenes.scene_identity(cid, a), store.scenes.scene_identity(cid, copy.stem)
    assert ia and ib and ia != ib


def test_a_scenes_dir_inside_the_racy_window_is_not_recorded(tmp_path, monkeypatch):
    """A directory touched within the timestamp granularity may be touched
    again without its mtime moving, so a signature taken then cannot vouch for
    what comes after it. Every scene here was created a moment ago."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    store.scenes.create_scene(cid, "Mara")
    assert time.time_ns() - _scenes_dir(cid).stat().st_mtime_ns < statcache.RACY_WINDOW_NS

    reads = _count_head_reads(monkeypatch)
    migrations.backfill_scene_identities()
    migrations.backfill_scene_identities()

    assert len(reads) == 2, "the second pass trusted a signature taken in the racy window"
    marks = tmp_path / ".cache" / "scene-identities.json"
    assert not marks.exists() or cid not in _marks(tmp_path)


def test_a_pass_that_wrote_is_not_recorded_and_the_next_one_is(tmp_path, monkeypatch):
    """Minting an identity rewrites a transcript, which moves the directory's
    signature -- so the pass that wrote cannot record, and the one after it
    (which finds every scene settled) does."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    p = store.scenes.paths._scene_path(cid, sid)
    meta, body = frontmatter.parse_frontmatter(p.read_text(encoding="utf-8"))
    meta.pop("identity", None)          # a scene written before identities existed
    p.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")
    assert store.scenes.scene_identity(cid, sid) is None
    _age(_scenes_dir(cid))

    migrations.backfill_scene_identities()
    assert store.scenes.scene_identity(cid, sid)
    marks = tmp_path / ".cache" / "scene-identities.json"
    assert not marks.exists() or cid not in _marks(tmp_path)

    _age(_scenes_dir(cid))
    migrations.backfill_scene_identities()
    assert cid in _marks(tmp_path)


def test_a_campaign_with_a_skipped_scene_is_not_recorded(tmp_path, monkeypatch):
    """A scene the pass could not repair (read-only, held by a sync client) is
    logged and left for the next boot. Recording the campaign anyway would make
    that next boot skip it -- the watermark would turn a retry into a
    permanent omission."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    sid = store.scenes.create_scene(cid, "Mara")
    _age(_scenes_dir(cid))

    def unreadable(c: str, s: str):
        raise scenes_identity.UnreadableError(f"{s}: held by a sync client")

    monkeypatch.setattr(scenes_identity, "scene_identity", unreadable)
    migrations.backfill_scene_identities()          # must not raise

    marks = tmp_path / ".cache" / "scene-identities.json"
    assert not marks.exists() or cid not in _marks(tmp_path), sid


def test_a_contended_campaign_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    store.scenes.create_scene(cid, "Mara")
    _age(_scenes_dir(cid))

    def busy(c: str) -> bool:
        raise store.locks.StoreBusy("held by another process")

    monkeypatch.setattr(migrations, "_backfill_campaign", busy)
    migrations.backfill_scene_identities()

    marks = tmp_path / ".cache" / "scene-identities.json"
    assert not marks.exists() or cid not in _marks(tmp_path)


@pytest.mark.parametrize("garbage", ["{not json", "[1, 2]", '{"x": 1', "\udcff"])
def test_a_corrupt_record_falls_back_to_a_full_pass(tmp_path, monkeypatch, garbage):
    """The record is derived and deletable, so a bad one costs exactly what a
    missing one does -- a full pass -- and is then replaced."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = _new_campaign()
    store.scenes.create_scene(cid, "Mara")
    _age(_scenes_dir(cid))
    marks = tmp_path / ".cache" / "scene-identities.json"
    marks.parent.mkdir(parents=True, exist_ok=True)
    marks.write_bytes(garbage.encode("utf-8", "surrogateescape"))

    reads = _count_head_reads(monkeypatch)
    migrations.backfill_scene_identities()

    assert len(reads) == 1
    assert cid in _marks(tmp_path)


def test_a_deleted_campaign_leaves_the_record(tmp_path, monkeypatch):
    """A slug is reusable. A record still carrying a deleted campaign's entry
    would be harmless (a new directory cannot share its signature) but would
    grow for the life of the install."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    keep = _new_campaign("Saltmarch")
    gone = _new_campaign("Winifred")
    for cid in (keep, gone):
        store.scenes.create_scene(cid, "Mara")
        _age(_scenes_dir(cid))
    migrations.backfill_scene_identities()
    assert set(_marks(tmp_path)) == {keep, gone}

    store.campaigns.delete_campaign(gone)
    migrations.backfill_scene_identities()

    assert set(_marks(tmp_path)) == {keep}


def test_a_store_with_nothing_to_record_writes_no_record(tmp_path, monkeypatch):
    """The record is written only when it changed, so a boot with nothing to
    say -- an empty library, or one whose campaigns have no scenes yet -- adds
    nothing to the store."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    migrations.backfill_scene_identities()
    _new_campaign()
    migrations.backfill_scene_identities()
    assert not (tmp_path / ".cache").exists()


# ---- best_stamp -----------------------------------------------------------


def _old_best_stamp(*candidates: str) -> str:
    """What `best_stamp` computed before it stopped validating every input."""
    return max((c for c in candidates if campaigns_read._valid_stamp(c)), default="")


#: Past-dated, so the far-future ceiling never depends on the machine's clock.
STAMPS = [
    "2024-08-01T10:00:00Z", "zzzz", "9999-12-31T23:59:59Z", "2024-8-07T01:02:03Z",
    "2024-07-15T09:30:00Z", "", "2024-08-01T10:00:01Z", "not a stamp",
    "2024-13-01T00:00:00Z", "2023-12-31T23:59:59Z",
]


@pytest.mark.parametrize("order", ["as-is", "reversed", "sorted", "sorted-desc"])
def test_best_stamp_equals_the_max_over_the_valid_stamps(order):
    stamps = {"as-is": STAMPS, "reversed": STAMPS[::-1], "sorted": sorted(STAMPS),
              "sorted-desc": sorted(STAMPS, reverse=True)}[order]
    assert campaigns_read.best_stamp(*stamps) == _old_best_stamp(*stamps) == "2024-08-01T10:00:01Z"


def test_best_stamp_of_nothing_valid_is_empty():
    assert campaigns_read.best_stamp("zzzz", "9999-12-31T23:59:59Z", "") == ""
    assert campaigns_read.best_stamp() == ""


def test_best_stamp_validates_only_what_could_raise_the_max(monkeypatch):
    """The listing hands it every scene's `updated`, newest first. Once the
    first believable stamp is found, nothing that sorts at or below it can
    change the answer, so nothing that does is parsed."""
    checked: list[str] = []
    real = campaigns_read._valid_stamp

    def counting(text: str) -> bool:
        checked.append(text)
        return real(text)

    monkeypatch.setattr(campaigns_read, "_valid_stamp", counting)
    newest_first = [f"2024-08-{day:02d}T12:00:00Z" for day in range(28, 0, -1)]

    assert campaigns_read.best_stamp("zzzz", *newest_first) == newest_first[0]
    assert checked == ["zzzz", newest_first[0]]


# ---- a failed tiktoken load is remembered ---------------------------------


class _BrokenTiktoken:
    """A tiktoken whose BPE cannot be fetched -- the offline first run."""

    def __init__(self) -> None:
        self.attempts = 0

    def get_encoding(self, name: str):
        self.attempts += 1
        raise OSError("no route to host")


def test_a_failed_encoder_load_is_attempted_once_not_per_call(monkeypatch):
    broken = _BrokenTiktoken()
    monkeypatch.setattr(tokens, "tiktoken", broken)
    monkeypatch.setattr(tokens, "_loader", tokens._Loader())

    counts = [tokens.count_tokens("the salt is owed " * 3) for _ in range(50)]

    assert broken.attempts == 1
    assert counts == [-(-len("the salt is owed " * 3) // 4)] * 50   # the heuristic


def test_a_failed_encoder_load_is_retried_after_the_back_off(monkeypatch):
    """Remembered, not permanent: a laptop that was offline at the first turn
    gets exact counts back once it can fetch the file, without a restart."""
    broken = _BrokenTiktoken()
    clock = [1000.0]
    monkeypatch.setattr(tokens, "tiktoken", broken)
    monkeypatch.setattr(tokens, "_loader", tokens._Loader())
    monkeypatch.setattr(tokens.time, "monotonic", lambda: clock[0])

    tokens.count_tokens("Mara")
    clock[0] += tokens.RETRY_AFTER_S - 1
    tokens.count_tokens("Mara")
    assert broken.attempts == 1

    clock[0] += 2
    tokens.count_tokens("Mara")
    assert broken.attempts == 2


def test_a_loaded_encoder_is_reused(monkeypatch):
    class _Enc:
        def encode(self, text: str) -> list[int]:
            return [0] * len(text.split())

    class _Working:
        attempts = 0

        def get_encoding(self, name: str):
            self.attempts += 1
            return _Enc()

    working = _Working()
    monkeypatch.setattr(tokens, "tiktoken", working)
    monkeypatch.setattr(tokens, "_loader", tokens._Loader())

    assert [tokens.count_tokens("Seraphine and Mara") for _ in range(5)] == [3] * 5
    assert working.attempts == 1


# ---- the fetcher's SSL context is built on first use ----------------------


def test_the_ssl_context_is_built_once_on_first_use(monkeypatch):
    built: list[object] = []
    real = ssl.create_default_context

    def counting(*args, **kwargs):
        ctx = real(*args, **kwargs)
        built.append(ctx)
        return ctx

    monkeypatch.setattr(fetch, "_SSL_CTX", None)
    monkeypatch.setattr(fetch.ssl, "create_default_context", counting)
    fetch._default_ssl_ctx.cache_clear()
    try:
        first, second = fetch._ssl_ctx(), fetch._ssl_ctx()
    finally:
        fetch._default_ssl_ctx.cache_clear()

    assert first is second
    assert len(built) == 1


def test_an_overridden_ssl_context_wins(monkeypatch):
    """`_SSL_CTX` stays the seam the TLS tests set."""
    ours = ssl.create_default_context()
    monkeypatch.setattr(fetch, "_SSL_CTX", ours)
    assert fetch._ssl_ctx() is ours


# ---- backups: media stored, stale temps swept -----------------------------

AT = datetime(2026, 8, 14, 21, 0, 0, tzinfo=UTC)
JPEG = b"\xff\xd8\xff\xe0" + os.urandom(4096)
PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(4096)


def _small_store(root: Path) -> dict[str, bytes]:
    files = {
        "worlds/realm/world.md": b"# Realm\n" * 200,
        "worlds/realm/assets/seraphine.jpg": JPEG,
        "worlds/realm/assets/Winifred.PNG": PNG,
        "campaigns/saltmarch/campaign.md": b"# Saltmarch\n",
        "config.md": b"---\ntheme: system\n---\n",
    }
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return files


def test_media_is_stored_and_text_deflated_and_both_round_trip(tmp_path, monkeypatch):
    """Deflating a JPEG spends the whole backup's CPU to save nothing; a
    stored member is standard zip, and mixing the two in one archive is too."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    files = _small_store(tmp_path)

    archive = backups.create_backup(when=AT)

    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        kinds = {i.filename: i.compress_type for i in z.infolist()}
        assert {n: z.read(n) for n in z.namelist()} == files
    assert kinds["worlds/realm/assets/seraphine.jpg"] == zipfile.ZIP_STORED
    assert kinds["worlds/realm/assets/Winifred.PNG"] == zipfile.ZIP_STORED
    assert kinds["worlds/realm/world.md"] == zipfile.ZIP_DEFLATED
    assert kinds["config.md"] == zipfile.ZIP_DEFLATED


def _temp(directory: Path, name: str, age: timedelta) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"half an archive")
    then = time.time() - age.total_seconds()
    os.utime(p, (then, then))
    return p


def test_a_backup_sweeps_a_stale_temp_and_keeps_a_fresh_one(tmp_path, monkeypatch):
    """A backup killed mid-zip leaves its temp, archive-sized, where nothing
    lists or removes it. The next one clears it -- but not a temp still being
    written, which a backup on another machine sharing this folder could own:
    a live build rewrites its temp continuously, so its mtime is never old."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    _small_store(tmp_path)
    d = tmp_path / "backups"
    stale = _temp(d, ".grimoire-20260101T000000Z.zip.ab12cd34.tmp", timedelta(hours=3))
    stale2 = _temp(d, ".grimoire-20260101T000000Z-2.zip.zz99_x00.tmp", timedelta(days=9))
    fresh = _temp(d, ".grimoire-20260814T205959Z.zip.ef56gh78.tmp", timedelta(seconds=5))

    archive = backups.create_backup(when=AT)

    assert not stale.exists() and not stale2.exists()
    assert fresh.exists()
    assert archive.exists()


def test_the_temp_sweep_touches_nothing_it_did_not_write(tmp_path, monkeypatch):
    """The backup directory is a real directory a user can point anywhere --
    the store root included, where every atomic write parks its temps. Only an
    abandoned temp of one of OUR archives is ours to delete."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    _small_store(tmp_path)
    store.config.write_config(backup_dir=str(tmp_path))
    old = timedelta(days=2)
    others = [
        _temp(tmp_path, ".config.md.ab12cd34.tmp", old),           # another record's temp
        _temp(tmp_path, ".grimoire-notes.tmp", old),               # not atomic's shape
        _temp(tmp_path, ".grimoire-notes.txt.ab12cd34.tmp", old),  # not an archive's
        _temp(tmp_path, "grimoire-20260101T000000Z.zip.ab12cd34.tmp", old),  # no dot
    ]

    backups.create_backup(when=AT)

    assert all(p.exists() for p in others)


def test_a_temp_that_cannot_be_swept_does_not_fail_the_backup(tmp_path, monkeypatch):
    """Litter is not a reason to have no backup."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    _small_store(tmp_path)
    stale = _temp(tmp_path / "backups", ".grimoire-20260101T000000Z.zip.ab12cd34.tmp",
                  timedelta(hours=3))
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == stale:
            raise PermissionError("held by a sync client")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    archive = backups.create_backup(when=AT)

    assert archive.exists() and stale.exists()
