"""Play a synthetic scene through the unmodified 0.2.1 backend and report its usage evidence.

Run ONLY by ``python -m grimoire.capture.v021``, under an interpreter that has 0.2.1 installed,
with a throwaway ``GRIMOIRE_HOME``. It imports 0.2.1's ``grimoire`` package (never V2's: the two
share a name) from ``--checkout`` and is never imported by V2.

Nothing in 0.2.1 is edited. The driver uses the seams 0.2.1's own test suite uses: it keeps the
store's machine-local lock directory and bootstrap pointer inside the temporary home, and it
gives the app's gateway client a transport that counts and bounds every provider request. In
``dry_run`` that transport is 0.2.1's own ``tests.llm_fakes.SSEProvider`` answering with scripted
provider-shaped streams (synthetic usage); in ``live`` it is a real HTTPS transport.

Prints one JSON evidence bundle (``grimoire.v021-capture-evidence/1``) on stdout and nothing
else. No prompt text and no credential is written to it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

EVIDENCE_FORMAT = "grimoire.v021-capture-evidence/1"
CHAT_PATHS = {"openrouter": "/chat/completions", "anthropic": "/messages",
              "openai_compatible": "/chat/completions"}
IDLE_TIMEOUT_SECONDS = 120


class DriverError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise DriverError(message)


def check_accounting(rows: list[dict], *, forwarded: int, planned: int, budget: int) -> None:
    """Every request the guard forwarded is an attempt some 0.2.1 usage row accounts for.

    A retry is allowed (0.2.1 books it as ``attempts`` on the row that served); a request no
    row accounts for, a planned call that never went out, or a run over budget is not.
    """
    llm = [row for row in rows if row.get("kind") == "llm"]
    attempts = sum(row.get("attempts", 1) for row in llm)
    if forwarded > budget:
        fail(f"0.2.1 sent {forwarded} provider requests, over the budget of {budget}")
    if forwarded < planned:
        fail(f"0.2.1 sent {forwarded} provider requests, fewer than the {planned} planned calls")
    if attempts != forwarded:
        fail(f"0.2.1's usage rows record {attempts} attempts, which do not add up to the "
             f"{forwarded} requests sent")


PARTIAL_FIELDS = ("prompt_tokens", "completion_tokens", "cache_read_tokens",
                  "cache_write_tokens", "cost_usd")


def partial_ledger(rows: list[dict]) -> list[dict]:
    """What a failed capture still cost, as ids and counts only (never text)."""
    return [{"task": row.get("task"), "response_id": row.get("response_id"),
             "attempts": row.get("attempts", 1), "status": row.get("status"),
             **{name: row.get(name) for name in PARTIAL_FIELDS}}
            for row in rows if row.get("kind") == "llm"]


def encode_bundle(bundle: dict) -> bytes:
    """The evidence as ASCII-escaped JSON: the same bytes whatever the pipe's locale encoding."""
    return json.dumps(bundle, ensure_ascii=True).encode("ascii")


def _usage_rows() -> list[dict]:
    rows = []
    for path in sorted((Path(os.environ["GRIMOIRE_HOME"]) / "usage").glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
    return rows


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("dry_run", "live"))
    parser.add_argument("--provider", required=True, choices=("anthropic", "openrouter"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--model")
    parser.add_argument("--key-env")
    # Test-only fault injection for dry runs (the capture CLI never passes it).
    parser.add_argument("--fault", choices=("retry-first",))
    return parser.parse_args(argv)


def _import_v021(checkout: Path, state: Path):
    """0.2.1's packages from the checkout, with its machine-local state kept in ``state``."""
    checkout = checkout.resolve()
    sys.path[0:0] = [str(checkout / "backend" / "src"), str(checkout / "backend")]
    import grimoire  # noqa: PLC0415  (0.2.1's, from the checkout)
    from tests import llm_fakes  # noqa: PLC0415  (0.2.1's own test fakes)
    for module in (grimoire, llm_fakes):
        if not Path(module.__file__).resolve().is_relative_to(checkout):
            fail(f"{module.__name__} was not imported from the verified checkout")
    from grimoire.store import paths, proclock  # noqa: PLC0415
    # 0.2.1 resolves its lock directory from the real account home (pwd), not $HOME; its own
    # test suite redirects these three the same way (tests/conftest.py).
    (state / "locks").mkdir(parents=True, exist_ok=True)
    proclock.lock_dir = lambda: state / "locks"
    paths.DEFAULT_HOME = state / "default-home"
    paths.pointer_path = lambda: state / "pointer.json"
    return llm_fakes


class Guard:
    """Counts provider requests, refuses anything but chat and anything over the budget."""

    def __init__(self, httpx, inner, provider: str, budget: int):
        self.httpx, self.inner, self.path, self.budget = httpx, inner, CHAT_PATHS[provider], budget
        self.forwarded = 0
        self.max_tokens: set[object] = set()

    def transport(self):
        guard = self

        class Transport(guard.httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if not request.url.path.endswith(guard.path):
                    raise RuntimeError(f"refused a non-chat provider request ({request.url.path})")
                if guard.forwarded >= guard.budget:
                    raise RuntimeError("refused a provider request over the planned budget")
                guard.forwarded += 1
                try:
                    guard.max_tokens.add(json.loads(request.content).get("max_tokens"))
                except ValueError:
                    guard.max_tokens.add("unreadable")
                return await guard.inner.handle_async_request(request)

            async def aclose(self):
                await guard.inner.aclose()

        return Transport()


def _sse(llm_fakes, provider: str, reply: dict) -> str:
    prompt, completion = reply["prompt_tokens"], reply["completion_tokens"]
    cached = reply["cache_read_tokens"]
    if provider == "openrouter":
        return llm_fakes.openai_tool_sse(text=reply["text"], finish="stop", usage={
            "prompt_tokens": prompt, "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached}})
    event = llm_fakes._event
    return "".join([
        event({"type": "message_start", "message": {
            "id": "msg_dry_run", "model": "synthetic-rp-model", "role": "assistant",
            "content": [], "usage": {"input_tokens": prompt - cached,
                                     "cache_read_input_tokens": cached,
                                     "cache_creation_input_tokens": 0}}}),
        event({"type": "content_block_start", "index": 0,
               "content_block": {"type": "text", "text": ""}}),
        event({"type": "content_block_delta", "index": 0,
               "delta": {"type": "text_delta", "text": reply["text"]}}),
        event({"type": "content_block_stop", "index": 0}),
        event({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
               "usage": {"output_tokens": completion}}),
        event({"type": "message_stop"}),
    ])


def _ok(response, what: str):
    if response.status_code >= 400:
        fail(f"{what}: HTTP {response.status_code}")
    return response.json() if response.content else {}


def main(argv: list[str]) -> int:
    args = _args(argv)
    setup = json.loads(args.setup.read_text("utf-8"))
    llm_fakes = _import_v021(args.checkout, args.state)
    commit = subprocess.run(["git", "-C", str(args.checkout), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()

    import httpx  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from grimoire import store  # noqa: PLC0415
    from grimoire.main import create_app  # noqa: PLC0415

    speakers = setup["speakers"]
    npcs = {npc["actor_id"]: npc for npc in setup["npcs"]}
    turns = setup["turns"]
    retries = store.config.llm_retries()
    planned = len(turns) * len(speakers)
    if args.mode == "dry_run":
        model = setup["dry_run"]["model"]
        replies = setup["dry_run"]["replies"]
        bodies = [_sse(llm_fakes, args.provider, replies[str(t["turn"])][speaker])
                  for t in turns for speaker in speakers]
        if args.fault == "retry-first":  # the first call fails once and 0.2.1 retries it
            bodies.insert(0, (429, "synthetic rate limit"))  # 0.2.1 retries rate limits
        sse = llm_fakes.SSEProvider(args.provider, bodies)
        inner = httpx.MockTransport(sse._answer)
        api_key = "dry-run-not-a-key"
        # 0.2.1 counts what a provider did not report with tiktoken, which downloads its
        # encoding on first use; its tests force the offline heuristic through this seam.
        from grimoire.store import tokens  # noqa: PLC0415
        tokens._encoder = lambda: None
    else:
        if args.fault:
            fail("--fault is only for dry runs")
        model = args.model or fail("--model is required for a live capture")
        api_key = os.environ.get(args.key_env or "") or fail("the key variable is not set")
        inner = None  # built below with 0.2.1's own TLS context

    app = create_app()
    gateway = app.state.llm
    client_attr = {"openrouter": "_openrouter", "anthropic": "_anthropic"}[args.provider]
    kind_client = getattr(gateway, client_attr)
    if inner is None:
        # What 0.2.1's own lazily built client would use (provider_http.ProviderHTTP): its CA
        # bundle, and the environment's proxy (a custom transport ignores proxy variables).
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY") or None
        inner = httpx.AsyncHTTPTransport(verify=kind_client._verify(), proxy=proxy)
    guard = Guard(httpx, inner, args.provider, planned * (1 + retries))
    # 0.2.1's own timeouts (provider_http.ProviderHTTP._client), so latency means the same.
    kind_client._http = httpx.AsyncClient(transport=guard.transport(),
                                          timeout=httpx.Timeout(120.0, connect=30.0))
    kind_client._owns = False
    for kind, attr in (("openrouter", "_openrouter"), ("anthropic", "_anthropic"),
                       ("openai_compatible", "_openai_compatible")):
        if kind != args.provider:  # a fallback to another kind must not reach the network
            other = getattr(gateway, attr)
            other._http = httpx.AsyncClient(transport=Guard(httpx, inner, kind, 0).transport())
            other._owns = False
    adapter = gateway._adapters[args.provider]
    if adapter._client is not kind_client or adapter._client._http is not kind_client._http:
        fail("the guarded transport does not reach the provider adapter")

    refs: dict[str, str] = {}
    with TestClient(app) as client:
        made = _ok(client.post("/api/llm-connections", json={
            "kind": args.provider, "name": f"capture-{args.provider}", "api_key": api_key}),
            "create the provider connection")
        connection = made.get("id") or fail("the connection has no id")
        _ok(client.put("/api/inference/settings", json={"roles": {"primary": {"selection": {
            "provider": connection, "model": model}}}}), "set the primary role")
        roles = _ok(client.get("/api/inference/settings"), "read inference settings").get(
            "roles", {})
        embedding = (roles.get("embedding") or {}).get("selection") or {}
        if embedding.get("provider") or embedding.get("model"):
            fail("an embedding role is configured; embeddings are not behind the request guard")

        wid = store.worlds.create_world(setup["world"])
        cid = store.campaigns.create_campaign(setup["campaign"], wid)
        sid = store.scenes.create_scene(cid, setup["scene"])
        base = f"/api/campaigns/{cid}/scenes/{sid}"
        for actor_id in [*speakers, *(a for a in npcs if a not in speakers)]:
            npc = npcs[actor_id]
            card = {"spec": "chara_card_v3", "spec_version": "3.0",
                    "data": {"name": npc["name"], **npc["card"]}}
            made = _ok(client.post(f"/api/campaigns/{cid}/characters",
                                   json={"name": npc["name"], "card": card}),
                       f"create {actor_id}")
            ref = f"characters:{made['character']}"
            refs[ref] = actor_id
            _ok(client.post(f"{base}/cast", json={"id": made["character"]}), f"cast {actor_id}")
        pc = _ok(client.post(f"/api/campaigns/{cid}/pcs", json={"name": setup["pc"]["name"]}),
                 "create the PC")
        refs[f"pcs:{pc['pc']}"] = setup["pc"]["actor_id"]
        _ok(client.post(f"{base}/cast", json={"id": pc["pc"], "kind": "pcs"}), "cast the PC")

        speaker_refs = [ref for actor in speakers for ref, a in refs.items() if a == actor]
        silent_refs = [ref for ref, a in refs.items() if a in npcs and a not in speakers]
        _ok(client.put(f"{base}/group", json={"order": "list", "order_list": speaker_refs,
                                              "sitting_out": silent_refs}), "set group order")
        _ok(client.put(f"/api/campaigns/{cid}/tracker", json={"setting": "off"}), "tracker off")
        if _ok(client.get(f"/api/campaigns/{cid}/tracker"), "read tracker").get("enabled"):
            fail("the tracker is still enabled")
        _ok(client.put(f"/api/campaigns/{cid}/settings", json={
            "rolling_summary_every": "0", "scene_break_every": "0"}), "cadences off")
        effective = _ok(client.get(f"/api/campaigns/{cid}/settings"), "read settings")
        effective = effective.get("effective", effective)
        for name in ("rolling_summary_every", "scene_break_every"):
            if str(effective.get(name)) != "0":
                fail(f"campaign setting {name} is {effective.get(name)!r}, not 0")
        # A fresh format-2 store has one connection and no fallback route; the other kinds'
        # clients refuse every request anyway (below). Images stay at 0.2.1's default, off.
        config = _ok(client.get("/api/config"), "read config")
        for name, off in (("send_images", "off"), ("decide_speaker_sampling", "on"),
                          ("decide_turn_plan", "off"),
                          ("decide_play_tool", "off"),
                          ("epistemic_decide", "off"), ("history_recall_depth", "0"),
                          ("semantic_recall_depth", "0")):
            value = config.get(name, off)
            if str(value) != off:
                fail(f"0.2.1 setting {name} is {value!r}, not {off}")
        if store.config.llm_retries() != retries:
            fail("llm_retries changed during setup")

        boundaries = []
        for turn in turns:
            response = client.post(f"{base}/chat", json={"content": turn["player_text"]})
            if response.status_code >= 400 or '"error"' in response.text:
                fail(f"turn {turn['turn']} failed: HTTP {response.status_code}")
            boundaries.append(len(store.scenes.read_scene(cid, sid)["messages"]))
        deadline = time.monotonic() + IDLE_TIMEOUT_SECONDS
        while app.state.runs.any_live() is not None:
            if time.monotonic() > deadline:
                fail("0.2.1 still has a live run")
            time.sleep(0.05)

    messages = store.scenes.read_scene(cid, sid)["messages"]
    usage_rows = _usage_rows()
    check_accounting(usage_rows, forwarded=guard.forwarded, planned=planned, budget=guard.budget)

    by_name = {npcs[a]["name"]: ref for ref, a in refs.items() if a in npcs}
    turn_records, start = [], 0
    for turn, end in zip(turns, boundaries):
        responses: dict[str, dict] = {}
        for message in messages[start:end]:
            rid = message.get("response_id")
            if not rid:
                continue
            ref = "grimoire" if message.get("speaker") in (None, "", "grimoire") else by_name.get(
                message["speaker"], f"unknown:{message['speaker']}")
            entry = responses.setdefault(rid, {"response_id": rid, "speaker_ref": ref,
                                               "text": ""})
            # A roll continuation is a second message (part) under the same response.
            entry["text"] = "\n".join(part for part in (entry["text"], message.get("content", ""))
                                      if part)
        turn_records.append({"turn": turn["turn"], "player_text": turn["player_text"],
                             "responses": list(responses.values())})
        start = end
    max_tokens = sorted("unset" if value is None else str(value) for value in guard.max_tokens)
    choice_outcomes = (dict(setup["dry_run"]["choice_outcomes"]) if args.mode == "dry_run"
                       else {choice: "unlabelled" for choice in setup["choice_ids"]})
    bundle = {
        "format": EVIDENCE_FORMAT,
        "scenario_id": setup["scenario_id"],
        "mode": args.mode,
        "provider_kind": args.provider,
        "v1_commit": commit,
        "profile": "two-call",
        "llm_retries": retries,
        "model": model,
        "budget": "max_tokens=" + ",".join(max_tokens),
        "campaign": cid,
        "scene": sid,
        "actor_map": refs,
        "choice_outcomes": choice_outcomes,
        "turns": turn_records,
        "usage_rows": usage_rows,
    }
    sys.stdout.buffer.write(encode_bundle(bundle))
    sys.stdout.buffer.flush()
    return 0


def run(argv: list[str]) -> None:
    """``main``, reporting what was already spent if the capture fails for any reason."""
    try:
        code = main(argv)
    except BaseException as error:  # noqa: BLE001 -- a crashed live capture still cost money
        if isinstance(error, SystemExit) and not error.code:
            raise
        message = str(error) if isinstance(error, DriverError) else (
            f"unexpected {type(error).__name__}")
        print(f"v021 driver: {message}", file=sys.stderr)
        try:
            spent = partial_ledger(_usage_rows())
        except (OSError, ValueError, KeyError):
            spent = []
        if spent:
            print("v021 driver: partial ledger (counts only): " + json.dumps(spent),
                  file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(code)


if __name__ == "__main__":
    run(sys.argv[1:])
