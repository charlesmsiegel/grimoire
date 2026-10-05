"""The narrative blocks: the conversation history projected into chat roles,
the present cast's relationship lines, and the story-so-far recap.

The two that read stored JSON (`_relationship_lines`, `_story_entries`) swallow
a garbled file and return nothing: neither block is worth failing a turn over.
`_project_history` only reshapes messages it is handed.
"""

from __future__ import annotations

import secrets

from ... import prompts
from .. import chronicle, config, export, post_images, relationships
from ..scenes import serialize as scenes_serialize


def _project_history(messages: list[dict], reduce=None) -> list[dict]:
    """Script lines (templates/scene/history_line.j2) -> conversation roles; merge
    consecutive same-role messages so providers that expect strict alternation are
    happy.

    **Images in the transcript reach the model as their ALT TEXT, never as
    markdown.** A post can carry a picture two ways -- a reader inserts one
    through `PostImagePicker`, or the narrator writes an art handle that
    `context.art.resolve_handles` rewrites -- and either way what is stored is
    `![description](/api/campaigns/...)`. Sending that whole thing back costs
    ~27 tokens of URL per image on every remaining turn of the scene, and buys
    nothing: the description is the part that says what was shown.

    The URL is worse than useless, in fact. Left in, it is a worked example of
    a shape the model is not supposed to produce: art is requested by HANDLE,
    which resolution validates, while a raw markdown URL is passed through
    untouched -- so a model imitating what it sees in its own history can write
    a plausible URL for a picture that does not exist, and it lands in the
    transcript as a broken image. Not showing it is the cheapest way not to
    teach it.

    `export.drop_images` rather than a second regex here: it is the same
    question ("this reader gets text only, give it the alt text") that a
    plain-text export already answers, and two copies of that rule would agree
    right up until one of them was fixed.

    `reduce(index, message) -> str` replaces that reduction for one caller,
    `_project_history_refs`, which keeps chosen pictures as sentinels (#377).
    """
    out: list[dict] = []
    for index, message in enumerate(messages):
        # A director note is an instruction to the narrator, not a line in the
        # scene, and it is stored only so the turn it produced can be
        # attributed to it. Dropping it here is what keeps a prompt
        # byte-identical to what it was before notes were persisted: the note
        # still reaches the model exactly once, as the final user message
        # `compose_director_turn` appends. Left in, every later turn of the
        # scene would carry the player's stage directions as though they were
        # dialogue -- and the model would learn to write them back.
        if scenes_serialize.is_director_note(message):
            continue
        # A post the player hid from context stays in the transcript and
        # reaches no prompt -- this is the turn's half of that promise.
        if scenes_serialize.is_excluded(message):
            continue
        content = (export.drop_images(message["content"]) if reduce is None
                   else reduce(index, message))
        m = {**message, "content": content}
        line = prompts.render("scene/history_line.j2", m=m)
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    return out


def _chosen_images(messages: list[dict], images: int, cid: str) -> set[tuple[int, int]]:
    """`(message index, image index)` of the newest `images` sendable pictures:
    newest message first and, within one, the later image first. Director notes
    are skipped, as the projection skips them. "Sendable" is
    `post_images.eligible` -- an app image this campaign resolves to a file it
    can decode -- so a remote or broken picture never takes a slot."""
    chosen: set[tuple[int, int]] = set()
    for i in range(len(messages) - 1, -1, -1):
        if scenes_serialize.is_director_note(messages[i]):
            continue
        links = export.image_links(messages[i]["content"])
        for j in range(len(links) - 1, -1, -1):
            if len(chosen) >= images:
                return chosen
            if post_images.eligible(cid, links[j][1]):
                chosen.add((i, j))
    return chosen


def _project_history_refs(messages: list[dict], *, images: int,
                          cid: str) -> tuple[list[dict], dict[int, dict], str]:
    """`_project_history`, keeping the newest `images` pictures as SENTINELS.

    Each chosen image becomes its alt text followed by `⟦img:<nonce>:<n>⟧`;
    every other image is reduced to its alt text exactly as `_project_history`
    reduces it, and everything else -- director notes, rendering, merging -- is
    that function's. Returns the projected messages (strings), the table of
    what each sentinel stands for (`{n: {"url", "alt", "role"}}`) and the
    nonce. The nonce is fresh per composition, so a post cannot forge a
    sentinel; `assemble` splits on it after macro expansion (#377).
    """
    chosen = _chosen_images(messages, images, cid)
    nonce = secrets.token_hex(8)
    table: dict[int, dict] = {}

    def reduce(i: int, message: dict) -> str:
        def mark(j: int, alt: str, url: str) -> str:
            if (i, j) not in chosen:
                return alt
            n = len(table)
            table[n] = {"url": url, "alt": alt, "role": message["role"]}
            return f"{alt}⟦img:{nonce}:{n}⟧"
        return export.sub_images(message["content"], mark)

    return _project_history(messages, reduce), table, nonce


def _relationship_lines(cid: str, cast) -> list[str]:
    try:
        tokens = [f"{a['kind']}:{a['id']}" for a in cast]
        return relationships.render_present(cid, tokens, lambda t: relationships.actor_name(cid, t))
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return []


def _recap_depth(depth: int | None = None) -> int:
    """The recap window actually rendered: an explicit depth (the opener's full
    recap) or the configured `recap_depth`. Archive retrieval subtracts this
    window from its candidates, so both have to agree on how wide it is."""
    try:
        if depth is not None:
            return max(depth, 0)
        return max(int(config.read_config().get("recap_depth", "5")), 0)
    except (TypeError, ValueError):
        return 5


def _recap_ids(cid: str, depth: int | None = None) -> frozenset[str]:
    """Scene ids already inside the recap window. The archive excludes these so
    one scene cannot arrive twice, once as a recap line and once as a recalled
    summary."""
    try:
        return frozenset(str(r.get("id") or "")
                         for r in chronicle.recent(cid, _recap_depth(depth))) - {""}
    except Exception:  # noqa: BLE001 — corrupt chronicle.json: exclude nothing, don't crash
        return frozenset()


def _story_entries(cid: str, depth: int | None = None, full: bool = False) -> list[str]:
    # Always-on, non-critical: a garbled chronicle/config must omit the block, never
    # crash the context build (the store may live in a synced folder). `depth=None`
    # reads the configured recap_depth (compact one-liners); the opener passes an
    # explicit depth with full=True so the template renders full summaries.
    try:
        depth = _recap_depth(depth)
        if depth <= 0:
            return []
        first, second = ("summary", "one_line") if full else ("one_line", "summary")
        return [(r.get(first) or r.get(second) or "").strip()
                for r in chronicle.recent(cid, depth)]
    except Exception:  # noqa: BLE001 — corrupt chronicle.json / config: omit, don't crash
        return []
