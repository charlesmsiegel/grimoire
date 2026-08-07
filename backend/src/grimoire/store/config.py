"""config.md read/write (frontmatter only)."""

from __future__ import annotations

import math

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home
from . import atomic, locks

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "codex"
DEFAULT_SCAN_DEPTH = "8"
DEFAULT_RECAP_DEPTH = "5"
# How many older scenes keyword-triggered archive retrieval may recall at once
# (context/archive.py). 0 disables it.
DEFAULT_ARCHIVE_DEPTH = "3"
# Token ceiling the context packer fits the prompt into (context/pack.py).
# "0" = unbounded, which is what every install gets until the user sets one:
# the backend cannot see the model's window size, only the frontend can.
DEFAULT_CONTEXT_BUDGET = "0"
DEFAULT_USER_LABEL = "You"
DEFAULT_ASSISTANT_LABEL = "Grimoire"
DEFAULT_CLAUDE_MODEL = "opus"
# Seconds without a delta before a stream is declared hung (#243). 120 is what
# the OpenRouter/openai-compatible httpx clients already used as their read
# timeout, so this only tightens the Claude provider, which had no bound at all.
DEFAULT_LLM_TIMEOUT = "120"
# Wall-clock ceiling on one absorb, whose LLM calls (extraction, one dossier
# per present NPC, audit) run sequentially inside a single request.
DEFAULT_ABSORB_BUDGET = "600"
# Whether the first-run setup wizard has been finished or dismissed. "off" on
# every store that has never recorded an answer -- including one written before
# this key existed, which is why the route backfills it rather than trusting
# the default to mean "never set up".
DEFAULT_SETUP_DONE = "off"
# Frozen per-turn prompt snapshots kept per campaign (store/prompt_log.py).
# "0" disables capture. Counted per campaign rather than per scene because the
# payloads hold whole prompts -- see that module for the tradeoff.
DEFAULT_PROMPT_LOG_DEPTH = "50"
# How many posts back the transient per-turn state ledger is injected over
# (store/turnstate.py). This is the decay window AND the feature's switch: at
# "0" no tracker instruction is added to the prompt and nothing is injected,
# which is the default because the instruction asks the model to end every
# reply with a machine-readable block — a real change to what it is being told
# to write, and not one to turn on behind an existing install's back.
DEFAULT_TURNSTATE_DEPTH = "0"
# How many consecutive recorded values promote a transient field to canonical
# character state at absorb (#121). Only reachable once the ledger has content,
# so it is safe to default to something useful.
DEFAULT_PROMOTE_STREAK = "3"
# How many posts may land before the live per-scene rolling summary is refolded
# (#85). Each refresh is one extra LLM call, so this is the knob that decides
# what the feature costs; "0" turns it off, leaving only the panel's explicit
# Refresh button.
DEFAULT_ROLLING_SUMMARY_EVERY = "10"

# Wall-clock ceiling on one one-shot generation route (#272). The idle bound
# above cannot stop an upstream that dribbles a frame just inside it forever,
# and a tagline / voice anchor / scene suggestion has no partial output worth
# protecting, so those routes get a stopwatch too. Streamed prose deliberately
# does not, and neither does absorb, which carries its own sequence budget --
# see `routes.common.bounded_call` for both exclusions.
DEFAULT_LLM_CALL_BUDGET = "300"
# The global scope of the response-preset cascade. These MUST be listed here:
# read_config() narrows its return to _CONFIG_KEYS, so a key omitted from this
# tuple is silently dropped and the global scope resolves as if unset — no
# error, just the wrong budget.
_LENGTH_KEYS = ("response_preset", "length_reply_words", "length_blocks",
                "length_paragraphs", "length_speakers", "length_blocks_per_speaker")

_CONFIG_KEYS = ("theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "archive_depth", "context_budget",
                "user_label", "assistant_label",
                "default_style_id", "active_connection_id",
                "llm_timeout", "absorb_budget", "setup_done",
                "prompt_log_depth",
                "turnstate_depth", "promote_streak",
                "rolling_summary_every", "llm_call_budget") + _LENGTH_KEYS


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    defaults = {"theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off",
                "recap_depth": DEFAULT_RECAP_DEPTH,
                "archive_depth": DEFAULT_ARCHIVE_DEPTH,
                "context_budget": DEFAULT_CONTEXT_BUDGET,
                "user_label": DEFAULT_USER_LABEL, "assistant_label": DEFAULT_ASSISTANT_LABEL,
                "default_style_id": "", "active_connection_id": "",
                "llm_timeout": DEFAULT_LLM_TIMEOUT, "absorb_budget": DEFAULT_ABSORB_BUDGET,
                "setup_done": DEFAULT_SETUP_DONE,
                "prompt_log_depth": DEFAULT_PROMPT_LOG_DEPTH,
                "turnstate_depth": DEFAULT_TURNSTATE_DEPTH,
                "promote_streak": DEFAULT_PROMOTE_STREAK,
                "rolling_summary_every": DEFAULT_ROLLING_SUMMARY_EVERY,

                "llm_call_budget": DEFAULT_LLM_CALL_BUDGET,
                **{k: "" for k in _LENGTH_KEYS}}
    if not path.exists():
        # Materializing the defaults is a write, and two first-ever readers
        # racing here would each publish a whole file.
        with locks.config_lock():
            if not path.exists():
                atomic.write_text(path, dump_frontmatter(defaults, ""))
                return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {k: meta.get(k, default) for k, default in defaults.items()}


def _seconds(key: str, default: str) -> float:
    """A duration setting, in seconds. Anything non-numeric (a hand-edited
    config.md, a field cleared in the UI) falls back to the default rather
    than raising: a malformed timeout must not take scene generation down
    with it. Any non-positive value means "no bound" — the escape hatch for
    a slow local endpoint whose first token legitimately takes minutes."""
    try:
        value = float(read_config().get(key, default))
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(value):
        # "inf"/"nan" parse as floats but are not durations: inf would be an
        # unbounded call that never says so, and nan compares false against
        # everything, silently landing in the disabled branch below.
        return float(default)
    return value if value > 0 else 0.0


def llm_timeout() -> float:
    """Seconds a single LLM call may go without producing a delta."""
    return _seconds("llm_timeout", DEFAULT_LLM_TIMEOUT)


def absorb_budget() -> float:
    """Wall-clock seconds one absorb's whole LLM sequence may take."""
    return _seconds("absorb_budget", DEFAULT_ABSORB_BUDGET)


def mark_setup_done() -> None:
    """Record that this store has been set up, unless it already says so.

    Called when the first world or campaign is created, not only when a config
    read happens to notice content exists. Those are not the same guarantee:
    a user who escapes the wizard, makes a world and deletes it again before
    anything reads the config leaves no trace at all, and the next navigation
    reopens setup over a store that has plainly been used (#194 review).

    Reading first keeps this off the write path for every creation after the
    first, which is all of them on any store that has been used at all.
    """
    if read_config().get("setup_done") != "on":
        write_config(setup_done="on")
def _count(key: str, default: str) -> int:
    """A whole-number setting, clamped at 0. Same tolerance `_seconds` has and
    for the same reason: a hand-edited config.md or a field cleared in the UI
    must not take a scene's generation down, so anything unparseable falls back
    to the default. A negative value means the same thing as 0 -- disabled --
    rather than an index that would slice from the wrong end."""
    try:
        return max(int(str(read_config().get(key, default)).strip()), 0)
    except (TypeError, ValueError):
        return max(int(default), 0)


def turnstate_depth() -> int:
    """Posts of transcript tail the transient-state ledger is read over. 0 turns
    the whole feature off — no tracker instruction, no injected section."""
    return _count("turnstate_depth", DEFAULT_TURNSTATE_DEPTH)


def promote_streak() -> int:
    """Consecutive recorded values that promote a transient field to canonical
    character state. 0 disables promotion. `turnstate.streaks` clamps this to
    the ledger's per-scene memory — the ceiling belongs where the retention
    limit is, not here."""
    return _count("promote_streak", DEFAULT_PROMOTE_STREAK)
def rolling_summary_every() -> int:
    """Posts between rolling-summary refreshes; 0 means off (#85).

    Same failure posture as `_seconds` and deliberately not folded into it: a
    malformed value falls back to the default rather than raising, because this
    is read on the play path and a hand-edited config.md must not take a scene
    down. What differs is the shape and what non-positive MEANS -- a count, not
    a duration, and "0" here is a documented setting (the feature is off) rather
    than "no bound". So a negative reads as 0, but an unparseable value reads as
    the default: clearing the field in the UI is a mistake to recover from,
    while typing 0 is an instruction to obey.

    `int(float(...))`, so "10.0" -- which is what a numeric input can serialize
    to -- is 10 rather than a fallback. Non-finite values are rejected first:
    `int(float("inf"))` raises, and `nan` compares false against everything, so
    it would land in neither branch below on merit.
    """
    try:
        value = float(read_config().get("rolling_summary_every",
                                        DEFAULT_ROLLING_SUMMARY_EVERY))
    except (TypeError, ValueError):
        return int(DEFAULT_ROLLING_SUMMARY_EVERY)
    if not math.isfinite(value):
        return int(DEFAULT_ROLLING_SUMMARY_EVERY)
    return int(value) if value > 0 else 0

def llm_call_budget() -> float:
    """Wall-clock seconds one non-streaming LLM call may take in total."""
    return _seconds("llm_call_budget", DEFAULT_LLM_CALL_BUDGET)


def write_config(**fields: str) -> dict[str, str]:
    # Merge onto the file's RAW frontmatter (not read_config()'s narrowed
    # reconstruction) so any key not in _CONFIG_KEYS — including the legacy
    # openrouter_key/model/provider/claude_model fields on a pre-migration
    # install — survives every write untouched. This is what makes the
    # design spec's "legacy fields stay physically present for recovery if
    # llm_connections/ is ever deleted" claim actually true: migration's own
    # first write (ensure_migrated's config.write_config(active_connection_id=...))
    # would otherwise silently erase them immediately.
    ensure_home()
    path = _config_path()
    # The read, the merge and the write are one critical section: they are
    # three steps against a file that is rewritten whole, so two concurrent
    # callers both merge onto the same pre-image and the second publication
    # silently drops the first's fields. That is reachable without any user
    # doing two things at once -- `_setup_state` backfills `setup_done` from
    # `GET /api/config`, so a second tab merely loading the app can race a
    # setting saved in the first (#194 review).
    with locks.config_lock():
        raw, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
        for key, value in fields.items():
            if key in _CONFIG_KEYS and value is not None:
                raw[key] = value
        atomic.write_text(path, dump_frontmatter(raw, ""))
        return read_config()
