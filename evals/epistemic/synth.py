"""Real-store recipes for epistemic cases: scenes, presence, replies and
overrides, written through the store's own writers.

Every store here is real: presence comes from `appearances.transitions`,
replies from the responses ledger, overrides from `knowledge.add` with the
scene's own fingerprints, and the evidence from 08's prompt-phase expansion
mapped the way 09's adapter maps it. So a post text is exactly what 09 would
hand over. Placeholder names only.
"""

from __future__ import annotations

import json
import uuid

from grimoire.store import chronicle, knowledge, knowledge_scene, overlay, responses, scenes
from grimoire.store.appearances import paths as appearances_paths
from grimoire.store.appearances import transitions
from grimoire.store.context import epistemic
from grimoire.store.history import model
from grimoire.store.scenes import identity as scenes_identity
from grimoire.store.searchdocs import expand as searchdocs_expand


def hex_id() -> str:
    return uuid.uuid4().hex


class Stage:
    """A scene and the helpers that play into it."""

    def __init__(self, cid: str, title: str = "The Pier"):
        self.cid = cid
        self.sid = scenes.create_scene(cid, title)
        self.identity = scenes_identity.ensure_identity(cid, self.sid)

    def say(self, text: str, speaker: str | None = None) -> int:
        return scenes.append_message(self.cid, self.sid, "user", text, speaker=speaker,
                                     post_id=hex_id())

    def legacy(self, text: str) -> int:
        """A post with no key, as an older store holds them."""
        return scenes.append_message(self.cid, self.sid, "assistant", text)

    def reply(self, ref: str, name: str, text: str) -> str:
        rnd = responses.new_round(self.cid, self.sid, eligible=[], automatic=False,
                                  post=None, run_id="test")
        rec = responses.prepare(self.cid, self.sid, rnd["id"], ref, name, {})
        responses.save_variant(self.cid, self.sid, rec["id"], text, "complete")
        return rec["id"]

    def index_of(self, needle: str) -> int:
        return next(i for i, m in enumerate(self.messages()) if needle in m["content"])

    def messages(self) -> list[dict]:
        return scenes.read_scene(self.cid, self.sid)["messages"]

    def item(self, *, summary: str = "", title: str | None = None,
             identity: str | bool | None = True) -> model.EvidenceItem:
        """The whole scene as 09 would hand it over: one excerpt of every
        eligible post, through 08's prompt expansion."""
        found = searchdocs_expand.expand(self.cid, self.sid, phase="prompt", terms=(),
                                         identity=self.identity, allow_whole=True,
                                         max_bytes=200_000, max_posts=200)
        assert found is not None
        excerpts = tuple(model.Excerpt(
            tuple(model.EvidencePost(p.index, p.key, p.part, p.speaker, p.content)
                  for p in e.posts), e.text) for e in found.excerpts)
        ident = self.identity if identity is True else identity
        meta_title = str(scenes.read_scene(self.cid, self.sid)["meta"].get("title") or "")
        return model.EvidenceItem(model.SceneRef(self.sid, ident),
                                  meta_title if title is None else title,
                                  date="Day 3", location="The pier", summary=summary,
                                  excerpts=excerpts)

    def absorb(self, summary: str) -> None:
        """Absorbed with `summary` as its chronicle record: what a whole-scene
        grant vouches for, so grant after absorbing."""
        chronicle.absorb(self.cid, {"id": self.sid, "one_line": summary, "summary": summary,
                                    "keywords": [], "cast": [], "location": "", "date": ""})
        scenes.mark_absorbed(self.cid, self.sid, summary, summary)

    def facts(self):
        return knowledge_scene.current(self.cid, self.sid)[2]


def npc(cid: str, name: str) -> tuple[str, str, str]:
    """A campaign character: its ref, id and version."""
    aid, vid = overlay.create_character(cid, name)
    return f"characters:{aid}", aid, vid


def enter(st: Stage, aid: str, vid: str, role: str = "npc") -> None:
    transitions.appear(st.cid, st.sid, "characters", aid, vid, role, narrate=False)


def leave(st: Stage, aid: str) -> None:
    transitions.leave(st.cid, st.sid, "characters", aid)


def grant(st: Stage, audience: str, name: str, status: str = "knows", *,
          keys: tuple[str, ...] | None = None, created: str | None = None,
          note: str = "") -> str:
    """An override about `keys` (whole scene when None), fingerprinted now."""
    facts = st.facts()
    if keys is None:
        subject = {"kind": "scene", "scene": st.identity, "digest": facts.digest}
    else:
        subject = {"kind": "posts", "scene": st.identity, "posts": [
            {"key": k, "part": "", "variant": facts.posts[(k, "")][0],
             "digest": facts.posts[(k, "")][1]} for k in keys]}
    entry = {"audience": audience, "audience_name": name, "status": status,
             "subject": subject, "note": note, "source": "manual"}
    if created:
        entry["created"] = created
    return knowledge.add(st.cid, entry)


def forget_intervals(cid: str, aid: str, sid: str) -> None:
    """`aid`'s presence intervals for `sid` removed, as a store written before
    intervals were recorded holds it (R5): the scene list still names it."""
    record = appearances_paths.record(cid)
    record.get(f"characters/{aid}", {}).get("presence", {}).pop(sid, None)
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")


def key(st: Stage, i: int) -> str:
    return knowledge.post_key(st.messages()[i])


def view(st: Stage, actor: str | None, items, present=()):
    """`items` classified for `actor` (None: the narrator) in `st`'s scene."""
    p = epistemic.Perspective.of(st.cid, st.sid, actor, present, identity=st.identity)
    return epistemic.classify(p, items)


def for_compose(cid: str, sid: str, actor: str | None, items,
                ceiling: int = 1200) -> epistemic.EpistemicView:
    """`items` as the view a compose of `sid` takes: classified for `actor`
    over everyone on stage there, carrying a retrieval of them."""
    p = epistemic.Perspective.of(cid, sid, actor, epistemic.present_roster(cid, sid),
                                 identity=scenes_identity.scene_identity(cid, sid))
    return epistemic.classify_evidence(p, model.Evidence(tuple(items), ceiling=ceiling))
