"""``python -m grimoire.capture.v021``: capture the unmodified 0.2.1 app's per-NPC usage.

Runs ``backend/tools/v021_driver.py`` under a separate interpreter that has 0.2.1 installed (V2
never imports 0.2.1: both packages are named ``grimoire``), in a throwaway 0.2.1 library, and
turns what it reports into a ``grimoire.story-quality/2`` run plus its evidence.

Explicitly opt-in. ``--dry-run`` sends nothing anywhere: 0.2.1's own provider adapter answers
from its test transport, and every number is synthetic. ``--live`` is billed and needs
``--confirm-billing`` and the *name* of an environment variable holding the key, which only the
child process reads. Exit status: 0 written, 1 capture failed, 2 refused or bad usage. On any
failure nothing is left in ``--out`` and the temporary library (which holds the key) is gone.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from grimoire.evals.format import BenchmarkFormatError, Scenario
from grimoire.evals.format2 import parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import benchmark_file, evidence_digest, load_json, load_scenario, loads_strict
from grimoire.evals.report import render_v2
from grimoire.evals.v021 import (
    PINNED_V021_COMMIT, PROVIDER_KINDS, convert, partial_ledger, verify_evidence,
)
from grimoire.library import (
    DEFAULT_DIRNAME, V1_DEFAULT_DIRNAME, V1_HOME_ENV, V2_HOME_ENV, LibraryPathError,
    check_write_destination,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
#: The 0.2.1-side driver. A module attribute so tests can substitute a fake; never a CLI flag.
DRIVER = REPO_ROOT / "backend" / "tools" / "v021_driver.py"
#: The commit the checkout must be at. Tests substitute a scratch repository's commit.
EXPECTED_COMMIT = PINNED_V021_COMMIT
#: Extra driver arguments, for tests only (e.g. a dry-run fault); never a CLI flag.
DRIVER_EXTRA_ARGS: tuple[str, ...] = ()
#: The only environment the 0.2.1 child gets, plus, in live mode, the named key variable and
#: the TLS/proxy settings 0.2.1's own HTTPS client would honour.
CHILD_ENV_NAMES = ("PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "GRIMOIRE_HOME", "HOME",
                   "XDG_STATE_HOME", "TMPDIR", "GRIMOIRE_CACHE_WARM",
                   "GRIMOIRE_INFERENCE_AUTOMIGRATE", "SYSTEMROOT")
LIVE_NETWORK_ENV = ("SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")
#: A key variable must be an ordinary name that cannot override the child's isolation.
_KEY_ENV = re.compile(r"[A-Z_][A-Z0-9_]*")
_RESERVED_KEY_ENV_PREFIXES = ("GRIMOIRE", "XDG_", "PYTHON", "SSL_", "HTTP", "ALL_PROXY",
                              "NO_PROXY", "LC_")
TIMEOUT_SECONDS: float = 1800
OUTPUTS = ("run.json", "evidence.json", "capture-report.txt")


class CaptureRefused(Exception):
    """The capture will not start (or will not write): exit 2."""


class CaptureFailed(Exception):
    """The capture ran but produced nothing usable: exit 1."""


@dataclass(frozen=True)
class CaptureOptions:
    scenario: str
    v1_checkout: Path
    v1_python: Path
    out: Path
    mode: str
    provider: str
    model: str | None = None
    api_key_env: str | None = None


# --- checks -------------------------------------------------------------------------------------

def _git(checkout: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True,
                              text=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureRefused(f"cannot inspect the 0.2.1 checkout with git: {error}") from None
    return done.stdout


def verify_checkout(path: Path, *, expected_commit: str | None = None) -> str:
    """The checkout's HEAD, if it is the pinned commit with no modified or untracked files."""
    expected = EXPECTED_COMMIT if expected_commit is None else expected_commit
    checkout = Path(path).resolve()
    if not checkout.is_dir():
        raise CaptureRefused(f"no 0.2.1 checkout at {checkout}")
    head = _git(checkout, "rev-parse", "HEAD").strip()
    if head != expected:
        raise CaptureRefused(f"the checkout is at {head[:12]}, not the pinned 0.2.1 commit "
                             f"{expected[:12]}")
    if _git(checkout, "status", "--porcelain").strip():
        raise CaptureRefused("the 0.2.1 checkout has modified or untracked files; the baseline "
                             "must be the unmodified tag")
    return head


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def forbidden_roots(v1_checkout: Path, *, environ: Mapping[str, str] | None = None,
                    home: Path | None = None) -> list[Path]:
    """Where capture output must never go: this repository, 0.2.1, and every library."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    roots = [REPO_ROOT, Path(v1_checkout), home / V1_DEFAULT_DIRNAME, home / DEFAULT_DIRNAME]
    account_home = _account_home()
    if account_home is not None:  # 0.2.1's machine-local lock state lives here
        roots.append(account_home / ".local" / "state" / "grimoire")
    for name in (V1_HOME_ENV, V2_HOME_ENV):
        if environ.get(name):
            roots.append(Path(os.path.expanduser(environ[name])))
    return [Path(os.path.realpath(root)) for root in roots]


def _account_home() -> Path | None:
    """The home 0.2.1 itself resolves (from the account database, not ``$HOME``), on POSIX."""
    try:
        import pwd
    except ImportError:
        return None
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return None


def check_key_env(name: str) -> str:
    """``name`` if it can carry the key without overriding anything the child relies on."""
    if (not _KEY_ENV.fullmatch(name) or name in CHILD_ENV_NAMES or name in LIVE_NETWORK_ENV
            or name.startswith(_RESERVED_KEY_ENV_PREFIXES)):
        raise CaptureRefused(f"--api-key-env {name!r} must be a plain upper-case variable name "
                             "that is not one of the capture's own (PATH, HOME, GRIMOIRE_*, "
                             "XDG_*, PYTHON*, TLS/proxy settings ...)")
    return name


def check_out_dir(out: Path, *, forbidden: Sequence[Path]) -> Path:
    """``out`` resolved, if it is absent or an empty directory outside every forbidden root."""
    resolved = Path(os.path.realpath(out))
    for root in forbidden:
        if _inside(resolved, Path(os.path.realpath(root))):
            raise CaptureRefused(f"--out {resolved} is inside {root}")
    try:
        check_write_destination(resolved)  # also refuses any V1 library, pointer included
    except LibraryPathError as error:
        raise CaptureRefused(f"--out: {error}") from None
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise CaptureRefused(f"--out {resolved} must be a new or empty directory")
    elif not resolved.parent.is_dir():
        raise CaptureRefused(f"--out's parent {resolved.parent} does not exist")
    return resolved


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "[redacted]") if secret else text


# --- the capture --------------------------------------------------------------------------------

def _scenario_and_setup(name: str) -> tuple[Scenario, Path]:
    try:
        scenario = load_scenario(benchmark_file(name, "scenario.json"))
        setup_path = Path(str(benchmark_file(name, "v021_setup.json")))
        setup = load_json(setup_path)
    except BenchmarkFormatError as error:
        raise CaptureRefused(f"scenario {name!r}: {error}") from None
    if not isinstance(setup, dict) or setup.get("scenario_id") != scenario.scenario_id:
        raise CaptureRefused(f"scenario {name!r}: v021_setup.json is for another scenario")
    return scenario, setup_path


def _child_env(temp: Path, options: CaptureOptions, key: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GRIMOIRE_HOME": str(temp / "library"),
        "HOME": str(temp / "user"),
        "XDG_STATE_HOME": str(temp / "state"),
        "TMPDIR": str(temp / "tmp"),
        "GRIMOIRE_CACHE_WARM": "0",
        "GRIMOIRE_INFERENCE_AUTOMIGRATE": "0",
    }
    if os.environ.get("SYSTEMROOT"):  # Windows cannot start Python without it
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    if options.mode == "live":
        for name in LIVE_NETWORK_ENV:
            if os.environ.get(name):
                env[name] = os.environ[name]
    if key is not None and options.api_key_env:
        env[check_key_env(options.api_key_env)] = key
    return env


def _run_driver(options: CaptureOptions, checkout: Path, setup: Path, temp: Path,
                key: str | None) -> str:
    for name in ("library", "user", "state", "tmp"):
        (temp / name).mkdir()
    command = [str(options.v1_python), "-B", "-I", str(DRIVER), "--setup", str(setup),
               "--checkout", str(checkout), "--mode", options.mode, "--provider",
               options.provider, "--state", str(temp / "state")]
    if options.model:
        command += ["--model", options.model]
    if options.api_key_env and options.mode == "live":
        command += ["--key-env", options.api_key_env]
    command += list(DRIVER_EXTRA_ARGS)
    try:
        child = subprocess.Popen(command, env=_child_env(temp, options, key), cwd=temp,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
    except OSError as error:
        raise CaptureRefused(f"cannot start {options.v1_python}: {error}") from None
    try:
        # Bytes, decoded here: in text mode Windows decodes in a reader thread, where a bad
        # byte is lost as a thread warning and the output silently becomes None.
        raw_out, raw_err = child.communicate(timeout=TIMEOUT_SECONDS)
    except BaseException as error:  # timeout, interrupt, bad output: never leave 0.2.1 running
        child.kill()
        child.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise CaptureFailed(f"the 0.2.1 driver timed out after {TIMEOUT_SECONDS} s") from None
        raise
    stderr = raw_err.decode("utf-8", errors="replace")
    if child.returncode != 0:
        tail = "\n".join(_scrub(stderr, key).strip().splitlines()[-20:])
        raise CaptureFailed(f"the 0.2.1 driver exited {child.returncode}:\n{tail}")
    try:
        return raw_out.decode("utf-8")
    except UnicodeDecodeError:
        raise CaptureFailed("the 0.2.1 driver's output is not UTF-8") from None


def _outputs(options: CaptureOptions, scenario: Scenario, stdout: str) -> dict[str, str]:
    try:
        bundle = loads_strict(stdout, "driver output")
    except BenchmarkFormatError as error:
        raise CaptureFailed(str(error)) from None
    if not isinstance(bundle, dict):
        raise CaptureFailed("the driver did not report an evidence bundle")
    digest = evidence_digest(bundle)
    try:
        raw = convert(bundle, scenario, digest=digest,
                      run_id=f"v021-{options.mode.replace('_', '-')}-{options.provider}",
                      label=f"0.2.1 two-call capture ({options.mode}, {options.provider})")
        run = parse_run_v2(raw, scenario)
    except ValueError as error:
        # The calls were made (and, live, billed) even though the run is unusable: say what
        # they cost, as ids and counts only.
        spent = json.dumps(partial_ledger(bundle.get("usage_rows")))
        raise CaptureFailed(f"the capture cannot be converted: {error}\n"
                            f"partial ledger (counts only): {spent}") from None
    evidence = verify_evidence(run, bundle, digest, scenario)
    report = compare_v2(scenario, run, run, baseline_evidence=evidence,
                        candidate_evidence=evidence)
    header = (f"0.2.1 capture: {options.mode}, provider {options.provider}, "
              f"commit {bundle.get('v1_commit', '?')[:12]}, evidence sha256 {digest}\n"
              "self-comparison of the captured run (hard findings and usage):\n\n")
    return {
        "run.json": json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        "evidence.json": json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        "capture-report.txt": header + render_v2(report),
    }


def _write(out: Path, files: Mapping[str, str]) -> None:
    """All outputs appear together, or none do."""
    try:
        partial = Path(tempfile.mkdtemp(prefix=f".{out.name}.partial-", dir=out.parent))
    except OSError as error:
        raise CaptureFailed(f"cannot write next to --out: {error}") from None
    try:
        for name, text in files.items():
            (partial / name).write_text(text, encoding="utf-8")
        if out.exists():
            out.rmdir()  # empty when the capture started; refuses if anything arrived since
        os.replace(partial, out)
    except OSError as error:
        raise CaptureFailed(f"cannot write --out {out} (did something else write there?): "
                            f"{error}") from None
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)


def run_capture(options: CaptureOptions) -> Path:
    if options.provider not in PROVIDER_KINDS:
        raise CaptureRefused(f"--provider must be one of {', '.join(PROVIDER_KINDS)}")
    key = None
    if options.mode == "live":
        if not options.api_key_env or not options.model:
            raise CaptureRefused("--live needs --model and --api-key-env")
        check_key_env(options.api_key_env)
        key = os.environ.get(options.api_key_env) or None
        if key is None:
            raise CaptureRefused(f"the environment variable {options.api_key_env} is not set")
    # Absolute but not resolved: the child runs in a temporary directory, and a virtual
    # environment's interpreter must keep its own (symlinked) path to find its packages.
    options = replace(options, v1_python=Path(os.path.abspath(options.v1_python)))
    scenario, setup = _scenario_and_setup(options.scenario)
    checkout = Path(options.v1_checkout).resolve()
    verify_checkout(checkout)
    out = check_out_dir(options.out, forbidden=forbidden_roots(checkout))
    if not Path(options.v1_python).is_file():
        raise CaptureRefused(f"--v1-python {options.v1_python} is not a file")

    temp = Path(tempfile.mkdtemp(prefix="grimoire-v021-"))
    try:
        stdout = _run_driver(options, checkout, setup, temp, key)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        if temp.exists():  # it may hold the key (0.2.1 stores it in llm_connections)
            raise CaptureFailed(f"could not remove the temporary 0.2.1 library {temp}; it may "
                                "hold the provider key, so delete it by hand")
    verify_checkout(checkout)  # the run left 0.2.1 exactly as it was
    files = _outputs(options, scenario, stdout)
    if key and any(key in text for text in files.values()):
        raise CaptureFailed("an output contains the provider key; nothing was written")
    _write(out, files)
    return out


# --- command line -------------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m grimoire.capture.v021",
                     description="Capture the unmodified 0.2.1 app's per-NPC model usage.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--v1-checkout", required=True, type=Path)
    parser.add_argument("--v1-python", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="no network; synthetic numbers")
    mode.add_argument("--live", action="store_true", help="real provider calls; billed")
    parser.add_argument("--provider", required=True, help="openrouter or anthropic")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", metavar="VAR",
                        help="name of the environment variable holding the key (live)")
    parser.add_argument("--confirm-billing", action="store_true",
                        help="required with --live: real calls are billed")
    return parser


def _interrupt(signum, frame):  # SIGTERM/SIGHUP take the same cleanup path as Ctrl-C
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)
    previous = {}
    if threading.current_thread() is threading.main_thread():  # signals are main-thread only
        for number in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)):
            handler = None if number is None else signal.getsignal(number)
            # An ignored signal (`nohup`) stays ignored; an unknown (non-Python) one is left be.
            if handler is not None and handler is not signal.SIG_IGN:
                previous[number] = signal.signal(number, _interrupt)
    try:
        return _main(args)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _main(args: argparse.Namespace) -> int:
    try:
        if args.live and not args.confirm_billing:
            raise CaptureRefused("--live makes billed provider calls; add --confirm-billing")
        if args.live and not args.api_key_env:
            raise CaptureRefused("--live needs --api-key-env naming the key's variable")
        if args.api_key_env:
            check_key_env(args.api_key_env)
        options = CaptureOptions(args.scenario, args.v1_checkout, args.v1_python, args.out,
                                 "live" if args.live else "dry_run", args.provider, args.model,
                                 args.api_key_env)
        out = run_capture(options)
    except CaptureRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    except CaptureFailed as error:
        print(f"capture failed: {error}", file=sys.stderr)
        return 1
    print(f"capture written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
