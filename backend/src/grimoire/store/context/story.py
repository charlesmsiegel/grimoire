"""The narrative blocks: the conversation history projected into chat roles,
the present cast's relationship lines, and the story-so-far recap.

The two that read stored JSON (`_relationship_lines`, `_story_entries`) swallow
a garbled file and return nothing: neither block is worth failing a turn over.
`_project_history` only reshapes messages it is handed.
"""

from __future__ import annotations

from ... import prompts
from .. import chronicle, config, export, relationships


def _project_history(messages: list[dict]) -> list[dict]:
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
    """
    out: list[dict] = []
    for message in messages:
        m = {**message, "content": export.drop_images(message["content"])}
        line = prompts.render("scene/history_line.j2", m=m)
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    return out


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
