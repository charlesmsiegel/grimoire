"""config.md read/write (frontmatter only)."""

from __future__ import annotations

import math

from . import atomic, locks
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
# One theme in two modes. The field is free-form here on purpose: the frontend
# owns what a value means, and it still answers to `codex`/`manuscript`/`astral`
# from the three-theme era by mapping them on read. "system" defers to the OS's
# prefers-color-scheme, which is the only default that is never wrong at 2am.
DEFAULT_THEME = "system"
DEFAULT_SCAN_DEPTH = "8"
DEFAULT_RECAP_DEPTH = "5"
# How many older scenes keyword-triggered archive retrieval may recall at once
# (context/archive.py). 0 disables it.
DEFAULT_ARCHIVE_DEPTH = "3"
# Token ceiling the context packer fits the prompt into (context/pack.py).
# "0" = unbounded, which is what every install gets until the user sets one:
# the backend cannot see the model's window size, only the frontend can.
DEFAULT_CONTEXT_BUDGET = "0"
# How many characters tier 3 of the off-scene cast directory may name — the
# "Known to exist" one-liners for characters the campaign can see but has never
# cast (context/cast.py). It listed EVERY briefed character, unbounded, which
# on a large world is a section that grows without limit and silently crowds
# out everything the packer is allowed to drop under it. Over the ceiling the
# characters the in-scene cast actually mentions are kept first and the tail is
# logged rather than silently dropped. "0" restores the old unbounded listing.
# 40 is chosen to be well clear of any world small enough for the question not
# to arise, so nothing is cut on an install that never had the problem.
DEFAULT_OFFSCENE_KNOWN_LIMIT = "40"
# --- semantic recall (context/semantic.py) ---
# The llm_connection whose OpenAI-compatible endpoint serves /embeddings, and
# the embedding model to ask it for. Credentials deliberately do NOT live here:
# this file lost its API keys when llm_connections/ took over (see that
# module), and re-introducing one for embeddings would undo that.
DEFAULT_EMBEDDINGS_CONNECTION_ID = ""
DEFAULT_EMBEDDINGS_MODEL = ""
# How many world-info entries a similarity pass may add on top of the keyword
# ones. "0" disables semantic recall entirely — the default, so every
# pre-existing install keeps a byte-identical prompt and makes no network call
# it did not make before.
DEFAULT_SEMANTIC_RECALL_DEPTH = "0"
# Cosine floor a candidate must clear to be recalled. Model-dependent by
# nature: what counts as "related" differs between embedding models, so this
# is a starting point to tune against the scene inspector, not a constant with
# a defensible universal value.
DEFAULT_SEMANTIC_RECALL_THRESHOLD = "0.4"
# How many described images `context.art` may offer the model in one turn. A
# menu, not a gallery: every line costs budget in a section that renders on
# every turn, and the instruction beside it says most replies should use none.
# "0" offers nothing, which is the same answer as switching the section off in
# the prompt layout -- and cheaper to reach for a reader who only wants it quiet
# on one install.
DEFAULT_ART_CATALOG_DEPTH = "4"
# Cosine floor for the semantic ranking of that offer. Model-dependent for the
# reason the recall threshold above is, and ignored entirely in keyword mode,
# which has a floor of its own (`art.KEYWORD_MIN_TERMS`).
DEFAULT_ART_CATALOG_THRESHOLD = "0.4"
# --- the two #29 layers, both off ---
# Whether prompt_layout.json is applied (context/layout.py). Off is
# byte-identical: the catalog renders as it always did. Off is also a BYPASS —
# the stored layout survives it — so a reader can A/B their ordering against
# the default without rebuilding it.
DEFAULT_PROMPT_LAYOUT_ENABLED = "off"
# Whether the active-speaker section renders (context/speaker.py). Off by
# default because it adds tokens to every group turn, and a cost may not
# arrive by upgrade.
DEFAULT_SPEAKER_TURN_TAKING = "off"
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
# How many of one absorb's LLM calls may be in flight at once. Its own key
# rather than a constant for two reasons: a per-account rate limit is a fact no
# default can know, and "1" has to stay available as an exact restoration of
# the sequential behaviour every version before the fan-out had -- which makes
# this change reversible from the config page instead of by a revert.
DEFAULT_ABSORB_CONCURRENCY = "4"
# --- retry + fallback (#144) ---
# How many times a failed generation is re-attempted before the connection is
# given up on. Only transient failures are retried at all (`llm.RETRYABLE_KINDS`)
# and only while nothing has reached the reader yet, so this is not a knob that
# can duplicate visible prose -- it is how long the app is willing to sit out a
# rate limit or a dropped connection. "0" turns retrying off, restoring the
# one-attempt behaviour every version before this had.
DEFAULT_LLM_RETRIES = "2"
# Retries are cheap individually and expensive in a row: each one waits, and a
# streamed turn has no total-duration ceiling above it (only the idle bound,
# which each attempt restarts). A hand-typed 500 would leave a scene apparently
# hung for an hour, so the setting is clamped rather than trusted. Ten attempts
# at the capped 8s backoff is already well over a minute of waiting.
MAX_LLM_RETRIES = 10
# The connection a generation falls back to once the active one's retries are
# exhausted, tried exactly once. "" -- the default -- means there is no
# fallback and an exhausted connection is simply an error, which is what every
# version before this did. A *connection*, not a second model name, so the
# fallback may be a different provider entirely (#141): the pre-connections
# design could only have meant "another model on the same account".
DEFAULT_FALLBACK_CONNECTION_ID = ""
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

# Posts between scene-break questions (#84). Only the CADENCE: a scene that has
# produced this many posts is merely eligible to be asked about, and the
# heuristic still has to reach `scene_break.THRESHOLD` before anything reaches a
# provider -- so the real cost is well under one call per this many posts.
# Twice the summary's cadence because the two answer questions of different
# sizes: a summary is behind after ten posts, where a scene is rarely over
# after ten. "0" turns the whole feature off, panel and all.
DEFAULT_SCENE_BREAK_EVERY = "20"

# Wall-clock ceiling on one one-shot generation route (#272). The idle bound
# above cannot stop an upstream that dribbles a frame just inside it forever,
# and a tagline / voice anchor / scene suggestion has no partial output worth
# protecting, so those routes get a stopwatch too. Streamed prose deliberately
# does not, and neither does absorb, which carries its own sequence budget --
# see `routes.common.bounded_call` for both exclusions.
DEFAULT_LLM_CALL_BUDGET = "300"
# --- automatic backups (#32) ---
# The whole store zipped into `backups/`, oldest archives swept past a
# retention count. Deliberately "off" on every install, new and upgraded: an
# archive is a copy of the entire library, the retention count multiplies it,
# and the store is explicitly allowed to live in a synced folder (CLAUDE.md) --
# where turning this on silently would mean re-uploading the whole library
# every interval. That is not a cost to impose behind someone's back, so the
# Configuration page asks.
DEFAULT_BACKUP_ENABLED = "off"
# Hours between automatic backups. Checked against the newest archive's own
# timestamp rather than a stored "last run", so the schedule survives restarts
# and means the same thing on a store shared between machines.
DEFAULT_BACKUP_INTERVAL_HOURS = "24"
# Archives kept; the sweep deletes the oldest beyond it. "0" keeps every
# archive -- retention off, the same "0 = no bound" the durations above use.
DEFAULT_BACKUP_KEEP = "7"
# Where archives are written. "" -- the default -- means `home()/backups`.
# Pointing it outside the store is the answer to a synced library: the archives
# stop being sync traffic, and stop being included in the very thing they back
# up. Either way the backup dir is excluded from its own archives.
DEFAULT_BACKUP_DIR = ""
# How many model turns a retcon replay may redo before it offers to fork the
# campaign first (#80). Ten because a replay costs one generation per turn, in
# money and in wall clock, and around ten is where "just redo it in place" stops
# being the obvious call — the number is configuration precisely because that
# judgement is the user's and depends on their model's price.
DEFAULT_REPLAY_FORK_THRESHOLD = "10"
# Days a campaign-clock advance may cross before the client offers to checkpoint
# the campaign first (#107). Thirty because a month is where a skip stops being
# "and then it was Tuesday" and starts being the kind of jump a reader might
# want to be able to walk back from -- and, like the replay threshold above,
# the number is configuration because the judgement is the user's: a saga told
# in seasons and a thriller told in hours do not agree about thirty days.
DEFAULT_ADVANCE_FORK_THRESHOLD = "30"
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
                "llm_timeout", "absorb_budget", "absorb_concurrency", "setup_done",
                "llm_retries", "fallback_connection_id",
                "prompt_log_depth",
                "turnstate_depth", "promote_streak",
                "rolling_summary_every", "scene_break_every", "llm_call_budget",
                "offscene_known_limit",
                "embeddings_connection_id", "embeddings_model",
                "semantic_recall_depth", "semantic_recall_threshold",
                # `context.art`'s two knobs, config.md-only like the recall pair
                # above. Omitted at first, which is exactly the failure the
                # comment on this tuple describes: both were documented, both
                # were silently dropped by `read_config`, and `art.settings()`
                # answered with its defaults no matter what anyone wrote.
                "art_catalog_depth", "art_catalog_threshold",
                "prompt_layout_enabled", "speaker_turn_taking",
                "backup_enabled", "backup_interval_hours", "backup_keep",
                "backup_dir", "replay_fork_threshold",
                "advance_fork_threshold") + _LENGTH_KEYS


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
                "absorb_concurrency": DEFAULT_ABSORB_CONCURRENCY,
                "setup_done": DEFAULT_SETUP_DONE,
                "replay_fork_threshold": DEFAULT_REPLAY_FORK_THRESHOLD,
                "advance_fork_threshold": DEFAULT_ADVANCE_FORK_THRESHOLD,
                "llm_retries": DEFAULT_LLM_RETRIES,
                "fallback_connection_id": DEFAULT_FALLBACK_CONNECTION_ID,
                "prompt_log_depth": DEFAULT_PROMPT_LOG_DEPTH,
                "turnstate_depth": DEFAULT_TURNSTATE_DEPTH,
                "promote_streak": DEFAULT_PROMOTE_STREAK,
                "rolling_summary_every": DEFAULT_ROLLING_SUMMARY_EVERY,
                "scene_break_every": DEFAULT_SCENE_BREAK_EVERY,
                "llm_call_budget": DEFAULT_LLM_CALL_BUDGET,
                "offscene_known_limit": DEFAULT_OFFSCENE_KNOWN_LIMIT,
                "embeddings_connection_id": DEFAULT_EMBEDDINGS_CONNECTION_ID,
                "embeddings_model": DEFAULT_EMBEDDINGS_MODEL,
                "semantic_recall_depth": DEFAULT_SEMANTIC_RECALL_DEPTH,
                "semantic_recall_threshold": DEFAULT_SEMANTIC_RECALL_THRESHOLD,
                "art_catalog_depth": DEFAULT_ART_CATALOG_DEPTH,
                "art_catalog_threshold": DEFAULT_ART_CATALOG_THRESHOLD,
                "prompt_layout_enabled": DEFAULT_PROMPT_LAYOUT_ENABLED,
                "speaker_turn_taking": DEFAULT_SPEAKER_TURN_TAKING,
                "backup_enabled": DEFAULT_BACKUP_ENABLED,
                "backup_interval_hours": DEFAULT_BACKUP_INTERVAL_HOURS,
                "backup_keep": DEFAULT_BACKUP_KEEP,
                "backup_dir": DEFAULT_BACKUP_DIR,
                **dict.fromkeys(_LENGTH_KEYS, "")}
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
    """Wall-clock seconds one absorb's LLM work may take.

    Its phases run concurrently, so each is given this whole window rather than
    a share of it -- which makes this a ceiling on the absorb, not on the sum
    of its calls. "0" means no ceiling at all, the escape hatch for a slow
    local endpoint (`test_the_one_shot_ceiling_does_not_bound_absorb`)."""
    return _seconds("absorb_budget", DEFAULT_ABSORB_BUDGET)


def absorb_concurrency() -> int:
    """How many of one absorb's LLM calls may be in flight at once.

    Clamped to at least 1. Zero would describe an absorb that never issues a
    call, which is nobody's intent -- and `_count`'s usual "0 means off"
    reading would invite exactly that."""
    return max(1, _count("absorb_concurrency", DEFAULT_ABSORB_CONCURRENCY))


def mark_setup_done() -> None:
    """Record that this store has been set up, unless it already says so.

    Called when the first world or campaign is created, not only when a config
    read happens to notice content exists. Those are not the same guarantee:
    a user who escapes the wizard, makes a world and deletes it again before
    anything reads the config leaves no trace at all, and the next navigation
    reopens setup over a store that has plainly been used (#194 review).

    Reading first keeps this off the write path for every creation after the
    first, which is all of them on any store that has been used at all.

    Best-effort, and that is the whole reason it swallows: the callers record
    this *after* the world or campaign is already on disk, so a raised
    `ConfigBusy` or `OSError` here would fail a request whose actual work
    succeeded. The caller then re-offers its creation form and the retry makes
    a uniquely-suffixed duplicate — a real record lost to a failed preference
    (#194 review). The cost of swallowing is that the wizard may be offered
    once more, which is what the read-side backfill is there to catch anyway.
    """
    try:
        if read_config().get("setup_done") != "on":
            write_config(setup_done="on")
    except (locks.StoreBusy, OSError):
        pass
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


def scan_depth() -> int:
    """Messages of transcript tail the keyword scan reads.

    The window every keyword-triggered section shares: world info, chronicle
    recall, keyed mechanics rules and the semantic-recall query are all matched
    against these messages and nothing older.

    0 is a real answer rather than a disabled feature -- it empties the window,
    so no keyword in the TRANSCRIPT activates anything. It does not silence the
    turn's own un-persisted input: the context builder seeds activation with a
    scene opener's prompt or a director's note regardless of this setting,
    because nothing in the history has said those words yet. A cleared or
    hand-mangled field falls back to the default instead, which is `_count`'s
    split between a choice and a mistake and the reason this is not parsed
    inline.
    """
    return _count("context_scan_depth", DEFAULT_SCAN_DEPTH)


def turnstate_depth() -> int:
    """Posts of transcript tail the transient-state ledger is read over. 0 turns
    the whole feature off — no tracker instruction, no injected section."""
    return _count("turnstate_depth", DEFAULT_TURNSTATE_DEPTH)


def speaker_turn_taking() -> bool:
    """Whether the active-speaker section renders (#29, context/speaker.py).

    Off by default: it adds a short section to every group-scene turn, and a
    cost may not arrive by upgrade. Off is byte-identical — nothing is computed
    and the section renders empty, so it drops out in `_render_sections`.
    """
    return read_config().get("speaker_turn_taking") == "on"


def promote_streak() -> int:
    """Consecutive recorded values that promote a transient field to canonical
    character state. 0 disables promotion. `turnstate.streaks` clamps this to
    the ledger's per-scene memory — the ceiling belongs where the retention
    limit is, not here."""
    return _count("promote_streak", DEFAULT_PROMOTE_STREAK)


def replay_fork_threshold() -> int:
    """Model turns a retcon replay may redo before the client offers to fork the
    campaign first (#80).

    A threshold, not a limit: nothing here refuses a long replay, and 0 means
    every replay is nudged rather than none — the same `_count` tolerance the
    settings beside it have, where a cleared or hand-mangled value falls back to
    the default rather than silently disabling the guard.
    """
    return _count("replay_fork_threshold", DEFAULT_REPLAY_FORK_THRESHOLD)


def advance_fork_threshold() -> int:
    """Days a clock advance may cross before the client offers to checkpoint the
    campaign first (#107).

    A threshold, not a limit, on the same terms as `replay_fork_threshold`:
    nothing refuses a long skip, 0 means every skip that crosses a day is asked
    about rather than none, and a cleared or hand-mangled value falls back to
    the default instead of silently turning the nudge off.
    """
    return _count("advance_fork_threshold", DEFAULT_ADVANCE_FORK_THRESHOLD)


def rolling_summary_every() -> int:
    """Posts between rolling-summary refreshes; 0 means off (#85). See `_every`
    for what a malformed value does, and why 0 and "junk" differ."""
    return _every("rolling_summary_every", DEFAULT_ROLLING_SUMMARY_EVERY)


def scene_break_every() -> int:
    """Posts between scene-break questions; 0 means off (#84).

    `rolling_summary_every`'s posture exactly, and it shares that function's
    parser for it: same shape (a count), same meaning for 0 (a documented "the
    feature is off" rather than "no bound"), and the same reason a malformed
    value falls back to the default instead of raising -- this is read on the
    play path, and a hand-edited config.md must not take a scene down.
    """
    return _every("scene_break_every", DEFAULT_SCENE_BREAK_EVERY)


def _every(key: str, default: str) -> int:
    """The two per-post cadences, parsed the one way (#84 joined #85 here).

    Deliberately not folded into `_seconds`: what differs from a duration is
    the shape and what non-positive MEANS -- a count, and "0" is an instruction
    to obey (the feature is off) rather than "no bound". So a negative reads as
    0, but an unparseable value reads as the default: clearing the field in the
    UI is a mistake to recover from, while typing 0 is a choice.

    `int(float(...))`, so "10.0" -- which is what a numeric input can serialize
    to -- is 10 rather than a fallback. Non-finite values are rejected first:
    `int(float("inf"))` raises, and `nan` compares false against everything, so
    it would land in neither branch below on merit.
    """
    try:
        value = float(read_config().get(key, default))
    except (TypeError, ValueError):
        return int(default)
    if not math.isfinite(value):
        return int(default)
    return int(value) if value > 0 else 0


def llm_call_budget() -> float:
    """Wall-clock seconds one non-streaming LLM call may take in total."""
    return _seconds("llm_call_budget", DEFAULT_LLM_CALL_BUDGET)


def llm_retries() -> int:
    """Re-attempts a transiently-failed generation gets before its connection
    is given up on (#144). 0 disables retrying; see `MAX_LLM_RETRIES` for why
    the upper end is clamped rather than taken at face value."""
    return min(_count("llm_retries", DEFAULT_LLM_RETRIES), MAX_LLM_RETRIES)


def backup_enabled() -> bool:
    """Whether the scheduler may write archives at all (#32). Off unless the
    file says exactly "on", so a half-set or hand-mangled value never starts
    zipping a library nobody asked to have zipped."""
    return str(read_config().get("backup_enabled",
                                 DEFAULT_BACKUP_ENABLED)).strip().lower() == "on"


def backup_interval_hours() -> float:
    """Hours between automatic backups.

    Same tolerance as `_seconds`, and one deliberate difference: a value of 0
    or less falls back to the default rather than meaning "no bound". Every
    other duration here bounds something that would otherwise run forever, so
    "unbounded" is a coherent answer; here it would mean re-zipping the whole
    library on every tick, which nobody types 0 to ask for -- and "off" already
    has its own switch.
    """
    try:
        value = float(str(read_config().get("backup_interval_hours",
                                            DEFAULT_BACKUP_INTERVAL_HOURS)).strip())
    except (TypeError, ValueError):
        return float(DEFAULT_BACKUP_INTERVAL_HOURS)
    if not math.isfinite(value) or value <= 0:
        return float(DEFAULT_BACKUP_INTERVAL_HOURS)
    return value


def backup_keep() -> int:
    """Archives the retention sweep keeps; 0 keeps every one of them."""
    return _count("backup_keep", DEFAULT_BACKUP_KEEP)


def backup_dir() -> str:
    """The configured archive directory, as typed -- "" means the default.

    A *string*, unexpanded: this is the setting. `store.backups.backup_dir()`
    is the one that resolves it to the directory archives are written to, and
    is what every caller outside this module wants.
    """
    return str(read_config().get("backup_dir", DEFAULT_BACKUP_DIR) or "").strip()


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
