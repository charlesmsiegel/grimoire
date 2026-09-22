"""The ledger's campaign prefilter, and the memo behind the unpriced-model list.

Every per-campaign rollup used to `json.loads` every row of every campaign in
its window and then throw most of them away; the prefilter rejects a line on
its text before it is parsed. That is only safe because the rule is EXACT, so
most of this file is about the two ways it could stop being exact -- a
campaign spelled with a JSON escape, and a campaign id that is a substring of
another -- and about the single-pass rollups agreeing, row for row, with the
two-pass implementations they replaced. The old code is kept inline below as
the oracle rather than trusted from memory.
"""

from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta

import pytest

from grimoire.store import pricing, usage


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(usage, "_today", lambda: "2026-08-14")
    return tmp_path


def _write(home, month: str, rows: list) -> None:
    """Hand-write a month file: each item is a dict (dumped) or a raw line."""
    path = home / "usage" / f"{month}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines((row if isinstance(row, str) else json.dumps(row)) + "\n"
                     for row in rows)


def _call(ts: str, campaign: str, **fields) -> dict:
    return {"ts": ts, "kind": "llm", "task": fields.pop("task", "chat"),
            "campaign": campaign, "model": fields.pop("model", "realm/opus"),
            "prompt_tokens": 100, "completion_tokens": 20, "duration_ms": 10,
            "status": "ok", **fields}


def _without_stamp(out: dict) -> dict:
    return {k: v for k, v in out.items() if k != "generated_at"}


# ---- (a) a campaign spelled with an escape is still that campaign ----
def test_a_campaign_spelled_with_a_json_escape_is_still_counted(home):
    """`json.dumps` never writes one, but a hand edit or another tool can:
    `"s\\u0061ltmarch"` decodes to `saltmarch` while not containing it. The
    prefilter lets every line holding a backslash through to the parser, which
    is what keeps it exact."""
    escaped = ('{"ts": "2026-08-14T10:00:00Z", "kind": "llm", "task": "chat", '
               '"campaign": "s\\u0061ltmarch", "scene": "001--arrival", '
               '"cost_usd": 0.5, "prompt_tokens": 10, "completion_tokens": 2}')
    assert "saltmarch" not in escaped, "the test line must not spell the id literally"
    _write(home, "2026-08", [escaped])

    assert usage.budget("saltmarch", 10, "monthly")["spent_usd"] == 0.5
    assert usage.summary(days=30, campaign="saltmarch")["totals"]["calls"] == 1
    assert [row["cost_usd"] for row in usage.calls(30, "saltmarch")] == [0.5]
    assert usage.campaign_scenes("saltmarch")["totals"]["cost_usd"] == 0.5
    scene = usage.scene_usage("saltmarch", "001--arrival", since="2026-08-01")
    assert scene["totals"]["cost_usd"] == 0.5


def test_a_rename_filed_under_an_escaped_campaign_is_still_followed(home):
    rename = ('{"ts": "2026-08-14T11:00:00Z", "kind": "rename", '
              '"campaign": "s\\u0061ltmarch", "scene": "001--dated", '
              '"was": "001--arrival"}')
    _write(home, "2026-08", [
        _call("2026-08-14T10:00:00Z", "saltmarch", scene="001--arrival", cost_usd=0.25),
        rename,
        _call("2026-08-14T12:00:00Z", "saltmarch", scene="001--dated", cost_usd=0.5),
    ])

    assert usage.scene_usage("saltmarch", "001--dated",
                             since="2026-08-01")["totals"]["cost_usd"] == 0.75
    assert {b["scene"] for b in usage.campaign_scenes("saltmarch")["scenes"]} \
        == {"001--dated"}


def test_a_rename_whose_kind_is_spelled_with_an_escape_is_still_a_rename(home):
    """The trail pass filters on `kind` as well as on the campaign, and the
    same escape hatch has to hold for both: this line names the campaign
    literally but never says "rename"."""
    rename = ('{"ts": "2026-08-14T11:00:00Z", "kind": "ren\\u0061me", '
              '"campaign": "saltmarch", "scene": "001--dated", "was": "001--arrival"}')
    assert "rename" not in rename
    _write(home, "2026-08", [
        _call("2026-08-14T10:00:00Z", "saltmarch", scene="001--arrival", cost_usd=0.25),
        rename,
    ])

    assert usage.scene_usage("saltmarch", "001--dated",
                             since="2026-08-01")["totals"]["cost_usd"] == 0.25


# ---- (b) a substring is not a match ----
def test_a_campaign_id_inside_another_is_filtered_exactly(home):
    """`salt` is a substring of `saltmarch`, so every `saltmarch` line passes
    the text test for `salt` -- and must then be dropped once parsed. And a row
    that merely MENTIONS the id somewhere else (a scene named after it, a
    model) is not that campaign's."""
    _write(home, "2026-08", [
        _call("2026-08-14T10:00:00Z", "salt", scene="a", cost_usd=1.0),
        _call("2026-08-14T10:01:00Z", "saltmarch", scene="a", cost_usd=2.0),
        _call("2026-08-14T10:02:00Z", "realm", scene="salt", cost_usd=4.0),
        _call("2026-08-14T10:03:00Z", "realm", scene="b", model="salt/opus", cost_usd=8.0),
    ])

    for cid, spent in (("salt", 1.0), ("saltmarch", 2.0), ("realm", 12.0), ("sal", 0.0)):
        assert usage.budget(cid, 100, "monthly")["spent_usd"] == spent, cid
        assert usage.campaign_scenes(cid)["totals"]["cost_usd"] == spent, cid
        assert usage.summary(days=30, campaign=cid)["totals"]["cost_usd"] == spent, cid
        assert sum(r["cost_usd"] for r in usage.calls(30, cid)) == spent, cid
    assert usage.scene_usage("salt", "a", since="2026-08-01")["totals"]["cost_usd"] == 1.0


def test_an_unscoped_read_still_sees_every_campaign(home):
    """The prefilter only runs when a campaign is asked for; the library-wide
    rollups read every row, including those that belong to no campaign."""
    _write(home, "2026-08", [
        _call("2026-08-14T10:00:00Z", "salt", cost_usd=1.0),
        {"ts": "2026-08-14T10:01:00Z", "kind": "llm", "task": "tagline",
         "cost_usd": 2.0},
    ])

    assert usage.summary(days=30)["totals"]["cost_usd"] == 3.0
    assert len(list(usage.calls(30))) == 2


# ---- (c) the single pass agrees with the two passes it replaced ----
# The implementations below are the ones this module shipped before the
# prefilter: a full-ledger trail pass, then a second full pass that parsed every
# campaign's rows. They are the oracle, so they are copied rather than imported
# and use a reader that knows nothing about campaigns.
def _old_read_rows(since, until):
    for path in usage._window_files(since, until):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    row = usage._row(line, since, until)
                    if row is not None:
                        yield row
        except (OSError, ValueError):
            continue


def _old_rename_trail(campaign, since, until):
    trail: dict[str, list[tuple[str, str]]] = {}
    for row in _old_read_rows(since, until):
        if row.get("kind") != usage.KIND_RENAME or row.get("campaign") != campaign:
            continue
        was, now, ts = row.get("was"), row.get("scene"), row.get("ts")
        if not (isinstance(was, str) and isinstance(now, str) and isinstance(ts, str)):
            continue
        if was and now and was != now:
            trail.setdefault(was, []).append((ts, now))
    for hops in trail.values():
        hops.sort()
    return trail


def _old_scene_usage(campaign, scene, *, since="", limit=usage.SCENE_TURNS):
    until = usage._today()
    start = usage._scan_since(since, until)
    clamped = bool(usage._valid_day(since)) and start > usage._valid_day(since)
    trail = _old_rename_trail(campaign, start, until)
    rates = usage.Rates.current()
    totals = dict(usage._ZERO)
    by_task: dict = {}
    by_post: dict = {}
    rerolls: dict = {}
    turns: list = []
    for row in _old_read_rows(start, until):
        if not usage._is_call(row) or row.get("campaign") != campaign:
            continue
        if usage._scene_now(row.get("scene"), row["ts"], trail) != scene:
            continue
        usage._add(totals, row, rates)
        usage._add(by_task.setdefault(usage._label(row.get("task")), dict(usage._ZERO)),
                   row, rates)
        post = usage._post(row)
        if post is not None:
            usage._add(by_post.setdefault(post, dict(usage._ZERO)), row, rates)
            if row.get("task") in usage.REROLL_TASKS:
                rerolls[post] = rerolls.get(post, 0) + 1
        turns.append(usage._turn(row, rates))
    turns.sort(key=lambda turn: turn["ts"], reverse=True)
    limit = max(0, int(limit))
    return {"campaign": campaign, "scene": scene, "since": start, "until": until,
            "clamped": clamped, "totals": usage._rounded(totals),
            "by_task": usage._ranked(by_task),
            "by_post": [{"post": i, "rerolls": rerolls.get(i, 0),
                         **usage._rounded(by_post[i])} for i in sorted(by_post)],
            "turns": turns[:limit], "listed": min(len(turns), limit),
            "truncated": len(turns) > limit}


def _old_campaign_scenes(campaign, *, since="", order="cost", limit=usage.CAMPAIGN_SCENES):
    until = usage._today()
    start = min(usage._valid_day(since) or usage.lifetime_since(), until)
    forward = _old_rename_trail(campaign, start, until)
    rates = usage.Rates.current()
    totals = dict(usage._ZERO)
    buckets: dict = {}
    seen: dict = {}
    for row in _old_read_rows(start, until):
        if not usage._is_call(row) or row.get("campaign") != campaign:
            continue
        usage._add(totals, row, rates)
        scene = row.get("scene")
        sid = usage._scene_now(scene, row["ts"], forward) \
            if isinstance(scene, str) and scene else usage.NO_SCENE
        usage._add(buckets.setdefault(sid, dict(usage._ZERO)), row, rates)
        stamps = seen.setdefault(sid, [row["ts"], row["ts"]])
        stamps[0] = min(stamps[0], row["ts"])
        stamps[1] = max(stamps[1], row["ts"])
    scenes = [{"scene": sid, "first_ts": seen[sid][0], "last_ts": seen[sid][1],
               **usage._rounded(bucket)} for sid, bucket in buckets.items()]
    order = order if order in usage.SCENE_ORDERS else usage.SCENE_ORDERS[0]
    usage._sort_scenes(scenes, order)
    limit = max(0, int(limit))
    return {"campaign": campaign, "since": start, "until": until,
            "totals": usage._rounded(totals), "order": order,
            "scenes": scenes[:limit], "listed": min(len(scenes), limit),
            "truncated": len(scenes) > limit}


def _old_budget(campaign, limit_usd, period=""):
    limit = usage.normalize_limit(limit_usd)
    period = usage.normalize_period(period)
    if not limit:
        return {"limit_usd": 0.0, "period": period, "level": usage.OFF,
                "warn_fraction": usage.WARN_FRACTION}
    since, until = usage.period_window(period)
    totals = dict(usage._ZERO)
    rates = usage.Rates.off()
    for row in _old_read_rows(since, until):
        if not usage._is_call(row) or row.get("campaign") != campaign:
            continue
        usage._add(totals, row, rates)
    spent = round(totals["cost_usd"], usage._CENTS)
    fraction = spent / limit
    level = usage.OVER if spent >= limit else \
        usage.WARN if fraction >= usage.WARN_FRACTION else usage.OK
    return {"limit_usd": limit, "period": period, "since": since, "until": until,
            "spent_usd": spent, "estimated_usd": round(totals["estimated_usd"], usage._CENTS),
            "unpriced_calls": totals["unpriced_calls"], "calls": totals["calls"],
            "fraction": round(fraction, 4), "level": level,
            "warn_fraction": usage.WARN_FRACTION}


def _reference_rows(since, until, campaign="", **_ignored):
    """A reader with the campaign filter applied AFTER parsing every line --
    the old semantics, for the rollups whose own shape did not change."""
    for row in _old_read_rows(since, until):
        if campaign and row.get("campaign") != campaign:
            continue
        yield row


CAMPAIGNS = ("saltmarch", "salt", "realm", "winifred-and-mara")


def _random_call(rng: random.Random, day: date, cid: str, sid: str) -> tuple[str, str]:
    """One call's ledger line, drawn from every shape a rollup has to fold:
    all three prices and none, unmetered, errors, rerolls, posts, rows with no
    scene or no campaign, a campaign spelled with an escape, and a backslash in
    an unrelated field. Returns the line and the scene it names ("" for none)."""
    ts = f"{day.isoformat()}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z"
    row = _call(ts, cid, scene=sid, task=rng.choice(
        ("chat", "chat", "retry", "regenerate", "absorb", "continuation")))
    price = rng.random()
    if price < 0.5:
        row["cost_usd"] = round(rng.random() / 10, 6)
    elif price < 0.65:
        row["cost_usd"] = round(rng.random() / 10, 6)
        row["cost_basis"] = "equivalent"
    elif price < 0.8:
        row["model"] = "local/glm"                    # priced by the rate table
    elif price < 0.9:
        del row["prompt_tokens"]                      # unmetered
    if rng.random() < 0.6:
        row["post"] = rng.randint(0, 12)
    if rng.random() < 0.05:
        row["status"] = "error"
        row["error"] = "timeout"
    if rng.random() < 0.08:
        row.pop("scene")                              # a campaign-level call
    elif rng.random() < 0.04:
        row.pop("campaign")                           # belongs to no campaign
    return _spelled(rng, json.dumps(row), cid), row.get("scene", "")


def _spelled(rng: random.Random, line: str, cid: str) -> str:
    """Now and then, a line no `json.dumps` of ours would write but a hand edit
    or another tool could: both take the parser's slow path."""
    if rng.random() < 0.05:
        # Spelled with an escape: still this campaign's row.
        return line.replace(f'"campaign": "{cid}"', '"campaign": "' + "".join(
            f"\\u{ord(c):04x}" for c in cid) + '"')
    if rng.random() < 0.05:
        # A backslash somewhere else entirely: parsed, then judged.
        return line.replace('"task": ', '"note": "caf\\u00e9", "task": ')
    return line


def _synthetic_ledger(home) -> set[str]:
    """Four months of interleaved campaigns (`_random_call` for the rows),
    with renames among them -- a chain, a recycled id, a loop, and hops filed
    out of stamp order -- plus blank and garbled lines. Returns every scene id
    the ledger names, before or after a rename."""
    rng = random.Random(1153)
    scenes = {cid: [f"{n:03d}--{cid}-scene" for n in range(1, 6)] for cid in CAMPAIGNS}
    names: set[str] = set()
    day = date(2026, 5, 20)
    months: dict[str, list] = {}
    while day <= date(2026, 8, 14):
        for _ in range(rng.randint(4, 9)):
            cid = rng.choice(CAMPAIGNS)
            line, scene = _random_call(rng, day, cid, rng.choice(scenes[cid]))
            months.setdefault(day.isoformat()[:7], []).append(line)
            names.add(scene)
        if rng.random() < 0.12:
            cid = rng.choice(CAMPAIGNS)
            was = rng.choice(scenes[cid])
            now = f"{was}--{day.isoformat()}"
            months.setdefault(day.isoformat()[:7], []).append(
                {"ts": f"{day.isoformat()}T12:30:00Z", "kind": "rename",
                 "campaign": cid, "scene": now, "was": was})
            names.add(now)
            # Sometimes the freed id is taken straight back by a new scene, and
            # sometimes the renamed scene keeps being played under its new id.
            if rng.random() < 0.5:
                scenes[cid].append(now)
        day += timedelta(days=1)
    months.setdefault("2026-07", []).extend([
        "", "   ", "{not json", "[1, 2]",
        json.dumps({"kind": "llm", "campaign": "salt", "cost_usd": 9.0}),     # no ts
        # A loop, which the trail walk must bound rather than follow forever.
        {"ts": "2026-07-02T01:00:00Z", "kind": "rename", "campaign": "realm",
         "scene": "loop-b", "was": "loop-a"},
        {"ts": "2026-07-02T01:00:01Z", "kind": "rename", "campaign": "realm",
         "scene": "loop-a", "was": "loop-b"},
        _call("2026-07-01T23:00:00Z", "realm", scene="loop-a", cost_usd=0.3),
        # Two hops off one id, filed in the opposite order to their stamps (a
        # synced or hand-merged file): the trail is sorted, not trusted.
        {"ts": "2026-07-20T09:00:00Z", "kind": "rename", "campaign": "saltmarch",
         "scene": "shuffle-c", "was": "shuffle-a"},
        {"ts": "2026-07-05T09:00:00Z", "kind": "rename", "campaign": "saltmarch",
         "scene": "shuffle-b", "was": "shuffle-a"},
        _call("2026-07-01T09:00:00Z", "saltmarch", scene="shuffle-a", cost_usd=0.01),
        _call("2026-07-10T09:00:00Z", "saltmarch", scene="shuffle-a", cost_usd=0.02),
        _call("2026-07-25T09:00:00Z", "saltmarch", scene="shuffle-a", cost_usd=0.04),
    ])
    names.update({"loop-a", "loop-b", "shuffle-a", "shuffle-b", "shuffle-c"})
    for month, rows in sorted(months.items()):
        _write(home, month, rows)
    return names - {""}


def test_the_single_pass_rollups_agree_with_the_two_pass_ones(home, monkeypatch):
    names = _synthetic_ledger(home)
    (home / "pricing.json").write_text(json.dumps(
        {"local/glm": {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0}}),
        encoding="utf-8")

    for cid in (*CAMPAIGNS, "nobody", "sal"):
        for order in usage.SCENE_ORDERS:
            for since in ("", "2026-07-01"):
                assert _without_stamp(usage.campaign_scenes(cid, since=since, order=order)) \
                    == _old_campaign_scenes(cid, since=since, order=order), (cid, order, since)
        assert _without_stamp(usage.campaign_scenes(cid, limit=2)) \
            == _old_campaign_scenes(cid, limit=2), cid
        # This campaign's own ids -- which for `salt` includes every one of
        # `saltmarch`'s, since the names embed the campaign -- plus the loop and
        # an id nothing ever filed under.
        for sid in sorted(n for n in names | {"never-played"}
                          if cid in n or not n.startswith("0")):
            for since in ("", "2026-05-01", "2026-07-15", "2027-01-01"):
                assert _without_stamp(usage.scene_usage(cid, sid, since=since)) \
                    == _old_scene_usage(cid, sid, since=since), (cid, sid, since)
        for limit, period in ((1, "monthly"), (1, "total"), (1000, "total"), (0, "total")):
            assert usage.budget(cid, limit, period) == _old_budget(cid, limit, period), \
                (cid, limit, period)


def test_the_prefiltered_summary_and_calls_agree_with_a_full_parse(home, monkeypatch):
    _synthetic_ledger(home)
    actual = {cid: (_without_stamp(usage.summary(days=120, campaign=cid)),
                    list(usage.calls(120, cid)))
              for cid in (*CAMPAIGNS, "nobody", "sal", "")}

    monkeypatch.setattr(usage, "_read_rows", _reference_rows)
    for cid, (summary, calls) in actual.items():
        assert summary == _without_stamp(usage.summary(days=120, campaign=cid)), cid
        assert calls == list(usage.calls(120, cid)), cid


# ---- (d) the unpriced-model list, remembered per month file ----
def _age(path, seconds_ago: int) -> None:
    """Move a file's mtime out of `statcache`'s racy window, where nothing is
    cached at all -- a file written a moment ago is always re-read."""
    stamp = os.stat(path).st_mtime - seconds_ago
    os.utime(path, (stamp, stamp))


def test_an_appended_row_reaches_the_unpriced_list_once_the_file_changes(home, monkeypatch):
    path = home / "usage" / "2026-08.jsonl"
    _write(home, "2026-08", [_call("2026-08-14T10:00:00Z", "saltmarch", model="local/glm")])
    _age(path, 60)
    parsed = []
    real = usage._month_unpriced
    monkeypatch.setattr(usage, "_month_unpriced", lambda p: parsed.append(p) or real(p))

    assert usage.unpriced_models() == [{"model": "local/glm", "calls": 1}]
    assert usage.unpriced_models() == [{"model": "local/glm", "calls": 1}]
    assert len(parsed) == 1, "an unchanged month file is not parsed twice"

    _write(home, "2026-08", [_call("2026-08-14T11:00:00Z", "saltmarch", model="local/glm"),
                             _call("2026-08-14T11:01:00Z", "saltmarch", model="local/mistral")])
    _age(path, 30)
    assert usage.unpriced_models() == [{"model": "local/glm", "calls": 2},
                                       {"model": "local/mistral", "calls": 1}]
    assert len(parsed) == 2


def test_a_pricing_edit_applies_without_the_ledger_changing(home, monkeypatch):
    """The memo holds counts, never verdicts: the rate table is applied after
    it, so typing a rate clears a model from the list on the next read even
    though no month file moved."""
    path = home / "usage" / "2026-08.jsonl"
    _write(home, "2026-08", [_call("2026-08-14T10:00:00Z", "saltmarch", model="local/glm"),
                             _call("2026-08-14T10:01:00Z", "saltmarch", model="z-ai/glm")])
    _age(path, 60)
    assert {m["model"] for m in usage.unpriced_models()} == {"local/glm", "z-ai/glm"}

    pricing.write_pricing({"local/glm": {"prompt_usd_per_1k": 1.0,
                                         "completion_usd_per_1k": 1.0}})
    assert usage.unpriced_models() == [{"model": "z-ai/glm", "calls": 1}]


def test_the_unpriced_list_still_counts_only_what_a_rate_could_price(home):
    """Unchanged by the memo: a priced call, and a call missing either count,
    are not something a rate would rescue. Two newest months only, busiest
    model first."""
    _write(home, "2026-06", [_call("2026-06-01T10:00:00Z", "saltmarch", model="old/model")])
    _write(home, "2026-07", [_call("2026-07-01T10:00:00Z", "saltmarch", model="a/one")])
    _write(home, "2026-08", [
        _call("2026-08-01T10:00:00Z", "saltmarch", model="b/two"),
        _call("2026-08-01T10:01:00Z", "saltmarch", model="b/two"),
        _call("2026-08-01T10:02:00Z", "saltmarch", model="priced", cost_usd=0.1),
        {"ts": "2026-08-01T10:03:00Z", "kind": "llm", "model": "unmetered",
         "prompt_tokens": 5},
        _call("2026-08-01T10:04:00Z", "saltmarch", model=""),
    ])

    assert usage.unpriced_models() == [{"model": "b/two", "calls": 2},
                                       {"model": "a/one", "calls": 1}]


def test_an_unreadable_month_is_not_remembered_as_empty(home, monkeypatch):
    """A month a sync client has locked is skipped for this read -- and only
    this one. Remembering the failure would pin the list short until the file
    next changed, which for a past month is never."""
    path = home / "usage" / "2026-08.jsonl"
    _write(home, "2026-08", [_call("2026-08-14T10:00:00Z", "saltmarch", model="local/glm")])
    _age(path, 60)
    real = usage._month_unpriced

    def locked(p):
        raise PermissionError(13, "locked", str(p))

    monkeypatch.setattr(usage, "_month_unpriced", locked)
    assert usage.unpriced_models() == []
    monkeypatch.setattr(usage, "_month_unpriced", real)
    assert usage.unpriced_models() == [{"model": "local/glm", "calls": 1}]


def test_bytes_that_are_not_utf8_cost_their_own_month_not_the_list(home):
    """`_read_rows`' tolerance: a month the decoder chokes on is drawn short
    (from wherever it met the bad bytes, which is chunk-granular), and the
    other month still counts. Reading the file whole used to raise out of the
    chore instead."""
    _write(home, "2026-08", [_call("2026-08-14T10:00:00Z", "saltmarch", model="local/glm")])
    path = home / "usage" / "2026-07.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"ts": "2026-07-14T10:01:00Z", "model": "\xff\xfe"}\n')

    assert usage.unpriced_models() == [{"model": "local/glm", "calls": 1}]
