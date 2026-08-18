"""Two-way sync of the grimoire store between this PC and a USB-connected phone.

Both ends hold the same shape of tree — the markdown/JSON store rooted at
whatever ``store.paths.home()`` resolves to — so the sync is a file-level
three-way merge, not a record-level one. It never parses a world or a scene; a
file is a blob with a hash, which is what keeps it correct for record kinds
this script has never heard of.

**Finding the two roots.** Neither side is hardcoded. The PC root follows the
same order ``store/paths.py`` documents (``GRIMOIRE_HOME`` → the ``data_dir``
in the ``~/.grimoire.json`` bootstrap pointer → ``~/.grimoire``), and the phone
root follows it from the app's own HOME, which ``ServerRuntime.bootstrap``
points at the external files dir. Read the pointer rather than assuming the
default, or a library the user relocated from the Storage-location page syncs
into the wrong directory.

**Deciding which side is fresher.** Not by timestamp: ``adb push`` stamps the
mtime it writes, and /sdcard's mount has never promised the resolution the
comparison would need. Instead every sync records a *baseline* — the hash each
file had when the two sides last agreed — and the next run compares both sides
against it. That is what separates "changed here" from "changed there" from
"changed in both places", which a two-way diff alone cannot tell apart.

**What it will not do.** It never deletes. A file present on one side and
absent on the other is always treated as new-on-the-other and copied, never as
a deletion to mirror, because the two are indistinguishable without trusting
the baseline further than a personal campaign library warrants. Deleting a
record therefore means deleting it in both places yourself. It also never
resolves a conflict: when both sides changed, the phone's copy lands beside the
PC's as ``<name>.sync-conflict-<stamp>-<serial><ext>`` and both originals are
left alone. That name is not decorative — ``store/external.py`` matches
``*.sync-conflict-*``, so the Configuration page lists these in the same place
it lists Syncthing's.

Dry-run is the default. ``--apply`` is what writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

APP_ID = "app.grimoire"

# Mirrors store/external.py's SKIP_AT_ROOT, for its reasons: `.cache/` is
# derived data the app rebuilds and `backups/` holds archives of the very tree
# being synced. `.git/` is this script's own addition -- the PC store is a git
# repo and its object database is not campaign content. All three are matched
# at the store root only, so a world legitimately named `backups` still syncs.
SKIP_AT_ROOT = frozenset({".cache", "backups", ".git"})

# Artifacts of a *previous* conflict, on either side. Copying them around would
# spread one unresolved edit across every device that ever syncs. `*.pyc` joins
# them because calendar plugins are real python modules the store imports, so
# their bytecode caches sit inside the library -- machine-specific, rebuilt on
# demand, and stamped with an interpreter version the other device may not have.
SKIP_GLOBS = ("*.sync-conflict-*", "*.orig", "*.pyc")

# Pruned at any depth, unlike SKIP_AT_ROOT: `__pycache__` is never a name a
# user gave a world.
SKIP_DIRS = frozenset({"__pycache__"})

# Written beside the phone's store by android_entry._open_store_to_usb.
SKIP_NAMES = frozenset({".usb_readable"})

BASELINE_DIR = Path.home() / ".grimoire-sync"


# ---------------------------------------------------------------- adb plumbing


class AdbError(RuntimeError):
    pass


class Adb:
    """Thin `adb` wrapper bound to one device serial."""

    def __init__(self, binary: str, serial: str | None = None) -> None:
        self.binary = binary
        self.serial = serial

    def _base(self) -> list[str]:
        return [self.binary] + (["-s", self.serial] if self.serial else [])

    def run(self, *args: str, binary_out: bool = False) -> bytes | str:
        proc = subprocess.run(
            self._base() + list(args), capture_output=True, check=False
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise AdbError(f"adb {' '.join(args)} failed: {err}")
        return proc.stdout if binary_out else proc.stdout.decode("utf-8", "replace")

    def shell(self, command: str) -> str:
        """Run a shell command, via exec-out so no pty rewrites the newlines.

        `adb shell` runs the command under a pty, which turns every \\n into
        \\r\\n -- harmless until a hash line or a file path is being parsed out
        of the output, which is all this script does with it.
        """
        return self.run("exec-out", command)  # type: ignore[return-value]

    def devices(self) -> list[str]:
        out = subprocess.run(
            [self.binary, "devices"], capture_output=True, check=False
        ).stdout.decode("utf-8", "replace")
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found.append(parts[0])
        return found


def find_adb(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    # The SDK location `make android-bootstrap` writes, then the two env vars
    # the Android tooling conventionally sets.
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Android/Sdk/platform-tools/adb.exe")
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(var)
        if root:
            candidates.append(Path(root) / "platform-tools/adb")
            candidates.append(Path(root) / "platform-tools/adb.exe")
    candidates.append(Path.home() / "Android/Sdk/platform-tools/adb")
    for c in candidates:
        if c.exists():
            return str(c)
    raise AdbError(
        "adb not found. Install platform-tools, or pass --adb /path/to/adb."
    )


def quote(path: str) -> str:
    """Single-quote a path for the device shell."""
    return "'" + path.replace("'", "'\\''") + "'"


# ------------------------------------------------------------- root discovery


def pc_root(explicit: str | None) -> Path:
    """The PC store root, resolved exactly as store/paths.home() resolves it."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("GRIMOIRE_HOME")
    if env:
        return Path(env)
    pointer = Path.home() / ".grimoire.json"
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
        raw = data.get("data_dir") if isinstance(data, dict) else None
        if raw:
            return Path(raw).expanduser()
    except (OSError, ValueError):
        pass  # a corrupt pointer falls back, the same way the app's does
    return Path.home() / ".grimoire"


def phone_root(adb: Adb, app_id: str, explicit: str | None) -> str:
    """The phone store root, resolved the same way from the app's HOME."""
    if explicit:
        return explicit
    home = f"/sdcard/Android/data/{app_id}/files"
    pointer = f"{home}/.grimoire.json"
    raw = adb.shell(f"cat {quote(pointer)} 2>/dev/null || true").strip()
    if raw:
        try:
            data = json.loads(raw)
            configured = data.get("data_dir") if isinstance(data, dict) else None
            if configured:
                return str(configured)
        except ValueError:
            pass
    return f"{home}/.grimoire"


# ------------------------------------------------------------------- hashing


def _excluded(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if parts and parts[0] in SKIP_AT_ROOT:
        return True
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return True
    name = rel.name
    if name in SKIP_NAMES:
        return True
    from fnmatch import fnmatch

    return any(fnmatch(name, pattern) for pattern in SKIP_GLOBS)


def hash_pc(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = PurePosixPath(here.relative_to(root).as_posix())
        if str(rel_dir) == ".":
            dirnames[:] = [d for d in dirnames if d not in SKIP_AT_ROOT]
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            rel = rel_dir / name if str(rel_dir) != "." else PurePosixPath(name)
            if _excluded(rel):
                continue
            digest = hashlib.sha256()
            try:
                with open(here / name, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue  # unreadable on our own side: report by omission
            out[str(rel)] = digest.hexdigest()
    return out


def hash_phone(adb: Adb, root: str) -> tuple[dict[str, str], list[str]]:
    """Hash on the device rather than pulling the tree to hash it here.

    A library of any size makes the difference between one `find` and copying
    every file over USB just to discover that nothing changed.
    """
    listing = adb.shell(
        f"cd {quote(root)} 2>/dev/null && find . -type f -exec sha256sum {{}} + 2>&1 || true"
    )
    out: dict[str, str] = {}
    denied: list[str] = []
    for line in listing.splitlines():
        line = line.rstrip("\r")
        if not line:
            continue
        if "Permission denied" in line or line.startswith("sha256sum:"):
            denied.append(line)
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            continue
        digest, path = parts[0].strip(), parts[1].strip()
        if len(digest) != 64:
            continue
        rel = PurePosixPath(path[2:] if path.startswith("./") else path)
        if _excluded(rel):
            continue
        out[str(rel)] = digest
    return out, denied


# ------------------------------------------------------------------ baseline


def baseline_path(serial: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in serial)
    return BASELINE_DIR / f"{safe}.json"


def load_baseline(
    serial: str, pc: Path, phone: str
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The last agreed state for this device, or nothing if it is not about
    these two directories.

    The roots are part of the identity, not just a note in the file: point
    either side somewhere else -- a relocated library, a second store, a test
    run -- and the recorded hashes describe a pairing that no longer exists.
    Reusing them there would read "absent from the baseline" as "new file" for
    the whole tree, which is survivable, and "matches the baseline" as "the
    other side deleted it", which is not.
    """
    try:
        data = json.loads(baseline_path(serial).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    if data.get("pc_root") != str(pc) or data.get("phone_root") != phone:
        return {}, {}
    return (data.get("files") or {}), (data.get("conflicts") or {})


def save_baseline(
    serial: str,
    pc: Path,
    phone: str,
    files: dict[str, str],
    conflicts: dict[str, list[str]],
) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "serial": serial,
        "pc_root": str(pc),
        "phone_root": phone,
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "conflicts": conflicts,
    }
    baseline_path(serial).write_text(
        json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8"
    )


# ------------------------------------------------------------------ planning


class Plan:
    def __init__(self) -> None:
        self.to_pc: list[str] = []          # copy phone -> PC
        self.to_phone: list[str] = []       # copy PC -> phone
        self.conflicts: list[str] = []      # both changed since the baseline
        self.pending: list[str] = []        # conflict already reported, unchanged
        self.in_sync = 0


def plan_sync(
    pc: dict[str, str],
    phone: dict[str, str],
    base: dict[str, str],
    known_conflicts: dict[str, list[str]],
) -> Plan:
    plan = Plan()
    for rel in sorted(set(pc) | set(phone)):
        p, d = pc.get(rel), phone.get(rel)
        if p == d:
            plan.in_sync += 1
            continue
        prior = known_conflicts.get(rel)
        if prior and list(prior) == [p, d]:
            # Same standoff as last run, and neither side has moved since. The
            # conflict copy from that run is still sitting there; making a
            # second one with a fresher timestamp would only add noise.
            plan.pending.append(rel)
            continue
        b = base.get(rel)
        if p is None:
            plan.to_pc.append(rel)          # new on phone (or deleted here)
        elif d is None:
            plan.to_phone.append(rel)       # new here (or deleted on phone)
        elif b is None:
            plan.conflicts.append(rel)      # differ, no common ancestor
        elif p == b:
            plan.to_pc.append(rel)          # only the phone moved
        elif d == b:
            plan.to_phone.append(rel)       # only the PC moved
        else:
            plan.conflicts.append(rel)
    return plan


def conflict_name(rel: str, stamp: str, serial: str) -> str:
    """`world.md` -> `world.sync-conflict-<stamp>-<serial>.md`.

    Shaped to match store/external.py's `*.sync-conflict-*` rule so the app
    surfaces it, and suffixed like the original so an editor still opens it.
    """
    p = PurePosixPath(rel)
    return str(p.with_name(f"{p.stem}.sync-conflict-{stamp}-{serial}{p.suffix}"))


# ------------------------------------------------------------------ applying


def git_snapshot(root: Path) -> str | None:
    """Commit the PC store as-is, so anything this run writes is recoverable."""
    inside = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, check=False,
    )
    if inside.returncode != 0 or inside.stdout.decode().strip() != "true":
        return None
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, check=False,
    ).stdout.decode("utf-8", "replace").strip()
    if not dirty:
        return "clean"
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=False)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m",
         f"Pre-sync snapshot {stamp}"],
        check=False,
    )
    rev = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        capture_output=True, check=False,
    ).stdout.decode().strip()
    return rev or None


# One `adb push` of a directory moves its whole subtree over a single
# connection; one `adb push` per file pays process startup and a USB round trip
# each time. On a first sync of a real library -- tens of thousands of files --
# that is the difference between a minute and most of an hour, so transfers are
# staged into a scratch tree and sent in one call.
BATCH_THRESHOLD = 25


def _stage(files: list[tuple[str, Path]], stage: Path) -> None:
    """Lay `rel -> source` out under `stage` at their relative paths.

    Hard-linked where the filesystem allows it, so staging a large library
    costs directory entries rather than a second copy of every byte.
    """
    for rel, src in files:
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dest)
        except (OSError, NotImplementedError):
            shutil.copy2(src, dest)


def push_files(adb: Adb, phone: str, files: list[tuple[str, Path]],
               scratch: Path) -> None:
    if not files:
        return
    if len(files) < BATCH_THRESHOLD:
        for rel, src in files:
            remote = f"{phone}/{rel}"
            adb.shell(f"mkdir -p {quote(str(PurePosixPath(remote).parent))}")
            adb.run("push", str(src), remote)
        return
    stage = scratch / "push"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    _stage(files, stage)
    adb.shell(f"mkdir -p {quote(phone)}")
    # The trailing `/.` is what makes adb copy the *contents* of the staging
    # directory into the target, rather than the directory itself into it.
    adb.run("push", f"{stage}{os.sep}.", phone)


def pull_files(adb: Adb, phone: str, files: list[tuple[str, Path]],
               scratch: Path) -> None:
    """Copy `rel -> destination` from the phone.

    Above the threshold this pulls the remote tree once into scratch and copies
    out of it. That transfers files the plan did not ask for, which is still
    cheaper than a per-file round trip once the count is high -- and the case
    that gets there is a first sync, where the plan wants nearly all of them.
    """
    if not files:
        return
    if len(files) < BATCH_THRESHOLD:
        for rel, dest in files:
            dest.parent.mkdir(parents=True, exist_ok=True)
            adb.run("pull", "-a", f"{phone}/{rel}", str(dest))
        return
    mirror = scratch / "pull"
    shutil.rmtree(mirror, ignore_errors=True)
    mirror.mkdir(parents=True)
    adb.run("pull", "-a", f"{phone}/.", str(mirror))
    for rel, dest in files:
        src = mirror / rel
        if not src.is_file():
            raise AdbError(f"{rel}: not present in the pulled tree")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


# --------------------------------------------------------------------- report


def describe(plan: Plan, denied: list[str]) -> None:
    total = (
        len(plan.to_pc) + len(plan.to_phone) + len(plan.conflicts) + len(plan.pending)
    )
    print(f"  in sync:              {plan.in_sync}")
    print(f"  phone -> PC:          {len(plan.to_pc)}")
    print(f"  PC -> phone:          {len(plan.to_phone)}")
    print(f"  conflicts (new):      {len(plan.conflicts)}")
    print(f"  conflicts (standing): {len(plan.pending)}")
    print(f"  actions to take:      {total}")
    if denied:
        print(
            f"\n  {len(denied)} file(s) on the phone could not be read. The app "
            "writes\n  group-readable only from the 16 KB/umask build onward -- "
            "open the app\n  once on that build to migrate the existing tree, "
            "then re-run."
        )
        for line in denied[:5]:
            print(f"    {line}")
        if len(denied) > 5:
            print(f"    ... and {len(denied) - 5} more")


def list_paths(label: str, paths: list[str], limit: int) -> None:
    if not paths:
        return
    print(f"\n{label} ({len(paths)}):")
    for rel in paths[:limit]:
        print(f"  {rel}")
    if len(paths) > limit:
        print(f"  ... and {len(paths) - limit} more")


# ----------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Two-way sync of the grimoire store between this PC and a "
                    "USB-connected phone. Dry run unless --apply is given."
    )
    ap.add_argument("--apply", action="store_true",
                    help="actually copy files (default: report only)")
    ap.add_argument("--serial", help="device serial, when more than one is attached")
    ap.add_argument("--adb", help="path to the adb binary")
    ap.add_argument("--app-id", default=APP_ID, help=f"phone package (default {APP_ID})")
    ap.add_argument("--pc-home", help="override the PC store root")
    ap.add_argument("--phone-home", help="override the phone store root")
    ap.add_argument("--no-stop", action="store_true",
                    help="do not force-stop the app before writing")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="do not git-commit the PC store before writing")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many paths to list per section (default 20)")
    args = ap.parse_args(argv)

    try:
        adb = Adb(find_adb(args.adb), args.serial)
    except AdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    attached = adb.devices()
    if not attached:
        print("error: no device. Check the cable, and that USB debugging is on "
              "and authorized.", file=sys.stderr)
        return 2
    if args.serial is None:
        if len(attached) > 1:
            print(f"error: {len(attached)} devices attached; pass --serial "
                  f"({', '.join(attached)})", file=sys.stderr)
            return 2
        adb.serial = attached[0]

    local = pc_root(args.pc_home)
    try:
        remote = phone_root(adb, args.app_id, args.phone_home)
    except AdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"device: {adb.serial}")
    print(f"PC:     {local}")
    print(f"phone:  {remote}")
    if not local.is_dir():
        print(f"error: PC store {local} does not exist", file=sys.stderr)
        return 2

    print("\nhashing both sides ...")
    pc_files = hash_pc(local)
    phone_files, denied = hash_phone(adb, remote)
    print(f"  PC:    {len(pc_files)} files")
    print(f"  phone: {len(phone_files)} files")

    base, known = load_baseline(adb.serial or "", local, remote)
    if not base:
        print("\nno baseline for this device yet -- first sync. Files that differ\n"
              "on both sides are reported as conflicts rather than guessed at.")
    plan = plan_sync(pc_files, phone_files, base, known)

    print()
    describe(plan, denied)
    list_paths("phone -> PC", plan.to_pc, args.limit)
    list_paths("PC -> phone", plan.to_phone, args.limit)
    list_paths("conflicts (phone copy will land beside the PC file)",
               plan.conflicts, args.limit)
    list_paths("conflicts already reported (resolve by hand)",
               plan.pending, args.limit)

    if not args.apply:
        print("\nDry run. Nothing was written. Re-run with --apply to copy.")
        return 0

    if not (plan.to_pc or plan.to_phone or plan.conflicts):
        print("\nNothing to do.")
        return 0

    if not args.no_snapshot:
        rev = git_snapshot(local)
        if rev == "clean":
            print("\ngit: PC store already clean, nothing to snapshot")
        elif rev:
            print(f"\ngit: snapshotted PC store at {rev} before writing")
    if not args.no_stop:
        adb.shell(f"am force-stop {args.app_id}")
        print(f"stopped {args.app_id} on the device")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    serial = adb.serial or "device"
    new_base = dict(base)
    new_conflicts = dict(known)
    failures: list[str] = []

    scratch = Path(tempfile.mkdtemp(prefix="grimoire-sync-"))
    try:
        # Conflicts are pulls too -- the phone's version, landing under a name
        # that says so -- so they ride along with the phone -> PC transfer.
        pulls = [(rel, local / rel) for rel in plan.to_pc]
        conflict_targets = {
            rel: conflict_name(rel, stamp, serial) for rel in plan.conflicts
        }
        pulls += [(rel, local / conflict_targets[rel]) for rel in plan.conflicts]
        pushes = [(rel, local / rel) for rel in plan.to_phone]

        if pulls:
            print(f"pulling {len(pulls)} file(s) from the phone ...")
            try:
                pull_files(adb, remote, pulls, scratch)
                for rel in plan.to_pc:
                    new_base[rel] = phone_files[rel]
                    new_conflicts.pop(rel, None)
                for rel in plan.conflicts:
                    # Both originals stay put; record the standoff so the next
                    # run recognises it instead of writing a second copy.
                    new_conflicts[rel] = [pc_files.get(rel), phone_files.get(rel)]
                    new_base.pop(rel, None)
            except AdbError as exc:
                failures.append(f"pull: {exc}")

        if pushes:
            print(f"pushing {len(pushes)} file(s) to the phone ...")
            try:
                push_files(adb, remote, pushes, scratch)
                for rel in plan.to_phone:
                    new_base[rel] = pc_files[rel]
                    new_conflicts.pop(rel, None)
            except AdbError as exc:
                failures.append(f"push: {exc}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    for rel, target in ((r, conflict_targets.get(r)) for r in plan.conflicts):
        if target:
            print(f"  conflict: {rel} -> {target}")

    # Only paths that genuinely agree now belong in the baseline -- it is the
    # record of the last state both sides shared, and anything else in it would
    # make the next run mistake an unsynced file for an edited one.
    for rel, digest in pc_files.items():
        if phone_files.get(rel) == digest:
            new_base[rel] = digest

    save_baseline(serial, local, remote, new_base, new_conflicts)
    print(f"\nbaseline written to {baseline_path(serial)}")

    if failures:
        print(f"\n{len(failures)} file(s) failed:", file=sys.stderr)
        for line in failures[:10]:
            print(f"  {line}", file=sys.stderr)
        return 1
    if plan.conflicts:
        print(f"\n{len(plan.conflicts)} conflict copy/copies written next to the "
              "PC originals.\nThe app lists them under Configuration -> store "
              "conflicts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
