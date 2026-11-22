"""Sprint 116's transfer ops (084): copy a chat's history into a new chat in another campaign, with every actor's
identity reviewed. Called as ``transfer.<op>``; see docs/TRANSFER_PAGE.md and docs/SCENE_TRANSFER.md.

Only Sprint 116 edits this module. It keeps no record of its own: every op runs 084's ``SceneTransfer`` over the
app's open library, acting as its owner with the app's trusted handle, and its one write is 084's own commit.

- **The director's.** A scan shows *every* actor of the origin's roster (active or not), and the target campaign's
  actors that share a definition or only a name with each, including characters the table has never met there;
  a plan shows what of the history the table never heard (``omitted``, ``unheard``/``private``) and the accepted
  state that stays behind. That is director-facing, so every op refuses ``director_only`` unless ``audience`` is
  ``director``. Nothing here is written as or for the table.
- **Reviewed, then made as reviewed.** ``plan`` builds 084's plan for the decisions given and answers what it would
  write (pages, posts, omitted, left, notes, the files) with a ``review`` fingerprint; it writes nothing.
  ``commit`` builds the same plan again with the same client ``operation_id`` and refuses ``changed`` unless its
  fingerprint is the one reviewed; 084's own commit then re-checks the origin under the store lock (a change there is
  a stale write, answered ``changed`` too). The new chat's id is derived from the operation id, the origin and the
  target, so a commit retried after a lost answer finds the chat it made and writes nothing again
  (``already_applied``), whatever decisions it carries; a chat at that id that is not a copy of this origin is
  ``operation_conflict``. The page makes a fresh operation id for every plan.
- **Every 084 refusal keeps its reason** (``same_campaign``, ``different_individual``, ``history_incomplete``, ...).
- **Not built (named, never offered):** transferring chosen state or attached assets, a chronology mapping, and
  presenting missing art are 084's later milestones.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone

from grimoire.app.adapter import Adapter, AdapterContext
from grimoire.campaignapi import parse
from grimoire.chats import ChatCodecError, chat_from_payload
from grimoire.core.identity import ChatRef, InstanceRef, ScopeRef, SourceRef, validate_segment
from grimoire.play.history import history_pages
from grimoire.scenestore import SceneLibrary
from grimoire.scenetransfer import LEAVE, NEW, SceneTransfer, TransferRefused
from grimoire.store import RecordNotFound, ScopeRecord, StoreError
from grimoire.textapi import ApiRefused
from grimoire.transcripts import page_to_payload

from .campaign import ADAPTER as CAMPAIGN

#: What the page says instead of a control, for each 084 milestone that is not built.
NOT_AVAILABLE = {
    "state": "084 copies the transcript only: accepted state (events, beliefs, relations, holdings, memories) and "
             "attached assets stay in the origin campaign; a reviewed transfer of chosen state is a later milestone",
    "chronology": "the copy's history comes before its first turn; mapping it onto the target campaign's own "
                  "chronology is a later milestone",
    "art": "pictures the origin shows are not carried, and presenting missing art is a later milestone",
}
#: The longest label a copy takes here (084's own limit is 1000).
LABEL_MAX = 200
#: At most this many copied posts are listed in a plan's answer; the rest are counted.
POSTS_SHOWN = 200
DECISIONS = (NEW, LEAVE)


# -- request fields ----------------------------------------------------------------------------------------------
def _director(args: Mapping[str, object]) -> None:
    audience = parse.field(args, "audience", str, required=False, default="table")
    if audience not in ("table", "director"):
        raise ApiRefused("invalid_field", "audience must be table or director")
    if audience != "director":
        raise ApiRefused("director_only", "copying a chat into another campaign shows every actor of both "
                                          "campaigns and what the table never heard: it is the director's")


def _target(args: Mapping[str, object]) -> ScopeRef:
    value = args.get("target")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "target must be a campaign id")
    try:
        validate_segment(value, "campaign")
    except ValueError:
        raise ApiRefused("invalid_field", "target is not a valid campaign id") from None
    return ScopeRef(value)


def _operation(args: Mapping[str, object]) -> str:
    value = args.get("operation_id")
    if type(value) is not str or len(value) > 80:
        raise ApiRefused("invalid_field", "operation_id must be an id of at most 80 characters")
    try:
        validate_segment(value, "operation_id")
    except ValueError:
        raise ApiRefused("invalid_field", "operation_id is not a valid id") from None
    return value


def _cast(args: Mapping[str, object], campaign: str) -> dict[InstanceRef, InstanceRef | str]:
    """Each origin actor's decision: ``new``, ``leave`` or a target actor's reference (084 checks which)."""
    value = args.get("cast")
    if not isinstance(value, Mapping) or len(value) > 256:
        raise ApiRefused("invalid_field", "cast maps each origin actor to new, leave or a target actor")
    found: dict[InstanceRef, InstanceRef | str] = {}
    for key, choice in value.items():
        actor = parse.actor_of(key, "cast", campaign)
        if choice in DECISIONS:
            found[actor] = str(choice)
        else:
            found[actor] = parse.parsed(InstanceRef.parse, choice, "a cast decision")
    return found


def _sources(args: Mapping[str, object]) -> list[SourceRef] | None:
    value = args.get("sources")
    if value is None:
        return None
    if type(value) is not list or len(value) > 64:
        raise ApiRefused("invalid_field", "sources is a list of the scenario and lorebook bindings to carry")
    return [parse.parsed(SourceRef.parse, item, "a source") for item in value]


def _label(args: Mapping[str, object]) -> str | None:
    value = args.get("label")
    if value is None or value == "":
        return None
    if type(value) is not str or not value.strip() or len(value) > LABEL_MAX:
        raise ApiRefused("invalid_field", f"label must be text of at most {LABEL_MAX} characters")
    return value.strip()


# -- 084 over the app's library ------------------------------------------------------------------------------------
def _refused(error: TransferRefused) -> ApiRefused:
    return ApiRefused(error.reason, str(error))


def _chat_id(origin: ChatRef, target: ScopeRef, operation_id: str) -> str:
    digest = hashlib.sha256(f"{origin}\n{target.campaign_id}\n{operation_id}".encode()).hexdigest()[:20]
    return f"t-copy-{digest}"


class _Ids:
    """Ids for one plan: the new chat's from the operation (the same on every rebuild), then numbered others."""

    def __init__(self, chat_id: str) -> None:
        self.chat_id, self.count = chat_id, 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        if prefix == "t" and self.count == 1:
            return self.chat_id
        return f"{prefix}-{self.chat_id[7:]}-{self.count}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _service(ctx: AdapterContext, ids=None) -> SceneTransfer:  # type: ignore[no-untyped-def]
    clock = getattr(ctx.chats, "_clock", None) or _now
    return SceneTransfer(ctx.store, ctx.handle, clock=clock, new_id=ids or _Ids("t-copy-scan"), owner=ctx.owner)


def _names(view, campaign: str) -> dict[InstanceRef, str | None]:  # type: ignore[no-untyped-def]
    """Every instance of ``campaign`` by the name its definition gives it (084's own rule for names), or its own."""
    found: dict[InstanceRef, str | None] = {}
    for ref, stored in view.instances.items():
        if ref.campaign_id != campaign:
            continue
        name = None
        try:
            name = view.index.resolve(SourceRef.parse(stored.envelope.derived_from)).name
        except (TypeError, ValueError, LookupError):
            payload = getattr(stored.envelope, "payload", None)
            local = payload.get("local_name") if isinstance(payload, Mapping) else None
            name = local if isinstance(local, str) else None
        found[ref] = name if isinstance(name, str) else None
    return found


def _definition_name(view, ref: SourceRef | None) -> str | None:  # type: ignore[no-untyped-def]
    if ref is None:
        return None
    try:
        name = view.index.resolve(ref).name
    except (LookupError, ValueError):
        return None
    return name if isinstance(name, str) else None


def _who(ref: InstanceRef, names: Mapping[InstanceRef, str | None]) -> dict:
    return {"actor": str(ref), "name": names.get(ref), "kind": ref.kind}


# -- ops ---------------------------------------------------------------------------------------------------------
def _targets(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The campaigns this chat may be copied into: every campaign the library lists chats of, but its own."""
    _director(args)
    chat = parse.chat_of(args)
    listing = CAMPAIGN.ops["chats"](ctx, {})
    return {"chat": str(chat), "origin": chat.campaign_id,
            "targets": [{"campaign": item["campaign"], "title": item["title"], "world": item["world"],
                         "chats": len(item["chats"])}
                        for item in listing["campaigns"] if item["campaign"] != chat.campaign_id],
            "not_available": dict(NOT_AVAILABLE)}


def _scan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """084's scan: the cast to decide (with reuse candidates and namesakes), the sources, the history and what
    stands in its way. Writes nothing and asks no model."""
    _director(args)
    chat = parse.chat_of(args)
    target = _target(args)
    try:
        scan = _service(ctx).scan(chat, target)
    except TransferRefused as error:
        raise _refused(error) from None
    view = ctx.store.load()
    names = _names(view, target.campaign_id)
    cast = [{"actor": str(member.actor), "role": member.role, "name": member.name, "active": member.active,
             "definition": None if member.definition is None else str(member.definition), "problem": member.problem,
             "candidates": [_who(ref, names) for ref in member.candidates],
             "namesakes": [_who(ref, names) for ref in member.namesakes],
             "can_create": member.problem is None and member.definition is not None
             and member.role in ("character", "persona")}
            for member in scan.cast]
    sources = [{"ref": str(item.ref), "role": item.role, "name": _definition_name(view, item.ref),
                "broken": item.broken} for item in scan.sources]
    return {"chat": str(chat), "target": target.campaign_id, "cast": cast, "sources": sources,
            "history": dict(scan.history),
            "history_problems": [{"kind": kind, "detail": detail} for kind, detail in scan.history_problems],
            "notes": [{"code": code, "detail": detail} for code, detail in scan.notes],
            "provider_calls": scan.provider_calls, "not_available": dict(NOT_AVAILABLE)}


def _built(ctx: AdapterContext, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
    """The plan the request names, with ids drawn from its operation (nothing is written)."""
    _director(args)
    chat = parse.chat_of(args)
    target = _target(args)
    operation_id = _operation(args)
    cast = _cast(args, chat.campaign_id)
    sources = _sources(args)
    label = _label(args)
    chat_id = _chat_id(chat, target, operation_id)
    try:
        plan = _service(ctx, _Ids(chat_id)).plan(chat, target, cast=cast, sources=sources, label=label)
    except TransferRefused as error:
        raise _refused(error) from None
    return chat, target, chat_id, cast, plan


def _bindings(plan) -> list[dict]:  # type: ignore[no-untyped-def]
    for envelope, _ in plan.change.puts:
        if isinstance(envelope, ScopeRecord) and envelope.address == plan.chat:
            record = chat_from_payload(plan.chat, envelope.schema, envelope.payload)
            return [{"role": binding.role, "ref": str(binding.ref), "revision": binding.resolved_revision,
                     "actor": None if binding.actor is None else str(binding.actor),
                     "detached": binding.detached_seq is not None}
                    for binding in record.bindings]
    return []  # pragma: no cover - 084 always plans the chat


def _origin_texts(ctx: AdapterContext, origin: ChatRef) -> dict[str, str]:
    """What each place of the origin's history says (a turn's post, a kept line, an imported post), by 084's own
    names for them (``Omitted.where``), so an omission is shown by its words and not only its place."""
    found: dict[str, str] = {}
    try:
        for turn in SceneLibrary(ctx.store, ctx.handle, owner=ctx.owner).turns(origin).records():
            for index, post in enumerate(turn.posts, start=1):
                found[turn.post_id(index)] = post.text
            if turn.kept is not None:
                for line in turn.kept.shown():
                    found[line.line_id] = line.text
        pages, _ = history_pages(ctx.store, origin)
        for page in pages:
            for post in page.posts:
                found[f"page {page.ref.seq} {post.post_id}"] = post.text or ""
    except (StoreError, ValueError):
        pass  # an omission is still shown by its place
    return found


def _plan_view(ctx: AdapterContext, origin: ChatRef, plan, cast) -> dict:  # type: ignore[no-untyped-def]
    view = ctx.store.load()
    said = _origin_texts(ctx, origin)
    origin_names: dict[InstanceRef, str] = {}
    try:
        scan = _service(ctx).scan(origin, ScopeRef(plan.chat.campaign_id))
        origin_names = {member.actor: member.name for member in scan.cast}
    except TransferRefused:
        pass
    target_names = _names(view, plan.chat.campaign_id)
    for actor, mapped in plan.mapping.items():
        if mapped is not None and target_names.get(mapped) is None:
            target_names[mapped] = origin_names.get(actor)  # a new actor is named by the definition it is made from
    mapping = []
    for actor, mapped in plan.mapping.items():
        choice = cast.get(actor)
        decision = "leave" if mapped is None else "new" if choice == NEW else "reuse"
        mapping.append({"actor": str(actor), "name": origin_names.get(actor), "decision": decision,
                        "target": None if mapped is None else str(mapped),
                        "target_name": None if mapped is None else target_names.get(mapped)})
    where = dict(plan.copied)  # origin place -> new post id
    origin_of = {post_id: place for place, post_id in where.items()}
    posts = [{"post": post.post_id, "from": origin_of.get(post.post_id), "role": post.speaker.role,
              "speaker": post.speaker.label, "text": post.text, "hidden": post.hidden,
              "actor": None if post.speaker.actor is None else str(post.speaker.actor)}
             for page in plan.pages for post in page.posts]
    files = sorted(plan.preview())
    answer = {"chat": str(plan.chat), "origin": str(origin), "mapping": mapping,
              "pages": len(plan.pages), "posts": posts[:POSTS_SHOWN], "posts_total": len(posts),
              "omitted": [{"where": item.where, "reason": item.reason, "text": said.get(item.where),
                           "missed": [{"actor": str(ref), "name": origin_names.get(ref)} for ref in item.missed]}
                          for item in plan.omitted],
              "left": [{"kind": item.kind, "where": item.where, "detail": item.detail} for item in plan.left],
              "notes": [{"code": code, "detail": detail} for code, detail in plan.notes],
              "bindings": _bindings(plan), "files": files, "provider_calls": plan.provider_calls}
    pages = [page_to_payload(page) for page in plan.pages]
    answer["review"] = hashlib.sha256(json.dumps({**answer, "page_payloads": pages}, sort_keys=True,
                                                 default=str).encode()).hexdigest()
    return answer


def _plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What the copy would write, for review; nothing is written."""
    origin, _, _, cast, plan = _built(ctx, args)
    return {"plan": _plan_view(ctx, origin, plan, cast), "saved": False, "not_available": dict(NOT_AVAILABLE)}


def _existing(ctx: AdapterContext, chat: ChatRef, origin: ChatRef) -> dict | None:
    """The copy this operation already made, or ``None``; ``operation_conflict`` when the chat its id names is not
    a copy of ``origin``."""
    try:
        ctx.store.read(chat)
    except RecordNotFound:
        return None
    except StoreError:
        raise ApiRefused("changed", "the chat this copy would make cannot be read; plan again") from None
    found = ctx.store.chat_transcript(chat)
    first = next((stored for _, stored in sorted(found.records.items(), key=lambda item: item[0].seq)), None)
    payload = None if first is None else first.record.payload
    came = payload.get("origin") if isinstance(payload, Mapping) else None
    if not isinstance(came, Mapping) or came.get("from") != str(origin):
        raise ApiRefused("operation_conflict", "this operation id already made a copy of another chat")
    return {"chat": str(chat), "already_applied": True, "pages": len(found.records)}


def _commit(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """The copy reviewed with ``plan``, built again with the same operation and committed once, as reviewed."""
    _director(args)
    review = args.get("review")
    if type(review) is not str or not review:
        raise ApiRefused("review_required", "plan the copy and review it before committing it")
    origin = parse.chat_of(args)
    target = _target(args)
    done = _existing(ctx, ChatRef(target.campaign_id, _chat_id(origin, target, _operation(args))), origin)
    if done is not None:
        return {"done": done}
    origin, _, _, cast, plan = _built(ctx, args)
    shown = _plan_view(ctx, origin, plan, cast)
    if shown["review"] != review:
        raise ApiRefused("changed", "what this copy would write changed since it was reviewed: plan it again")
    try:
        plan.commit()  # 084 re-checks the origin under the store lock: a change is StaleWrite, answered changed
    except ChatCodecError as error:
        raise ApiRefused("changed", str(error)) from None
    return {"done": {"chat": str(plan.chat), "already_applied": False, "pages": len(plan.pages),
                     "posts": shown["posts_total"], "files": shown["files"]}}


ADAPTER = Adapter("transfer", owner="116",
                  ops={"targets": _targets, "scan": _scan, "plan": _plan, "commit": _commit},
                  refusals={TransferRefused: "transfer_refused"})

__all__ = ["ADAPTER", "NOT_AVAILABLE"]
