"""Reviewed rebase of one campaign actor onto a newer revision of its source, over the store (042).

``SourceRebase.review`` reads **one** ``LibraryView`` and returns everything a human needs to decide:
the field rows (``grimoire.rebase.review_fields``), the chats that will be re-pinned and the ones
that follow their head, and the actor's history that the rebase keeps exactly as it is. ``apply``
turns the human's ``take``/``keep`` decisions into one ``RebaseChange``: ``preview`` shows the exact
bytes, ``commit`` writes them in one guarded store transaction with CAS on every record it touches.

It writes the actor's instance file and the chats that pin it, nothing else: the actor keeps its id,
so every relation, accepted event, knowledge record, holding and placement that names it stays
valid and unrewritten, and no other campaign's actor is read or written. A campaign that does not
rebase keeps its pinned revision. No model is called.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.chats import CAMPAIGN_CONTINUITY, CHAT_SCHEMA, MAX_BINDINGS, ChatCodecError, ChatRecord, NewBinding
from grimoire.chats import chat_from_payload, chat_to_payload
from grimoire.core.entity import EntityInstance
from grimoire.core.entity_codec import CodecError, instance_from_payload, instance_to_payload
from grimoire.core.identity import AcceptedRef, ChatRef, InstanceRef, RelationRef, SourceRef
from grimoire.core.portrayal import EffectiveDefinition, compose
from grimoire.effective import entity_decoder, explainer
from grimoire.rebase import FieldReview, RebaseError, rebase_instance, review_fields
from grimoire.store import (
    CommitResult, LibraryStore, LibraryView, RecordEnvelope, ScopeRecord, StaleWrite, StoreError,
)
from grimoire.store.layout import address_parts, chat_parts, definition_parts, manifest_parts

from .scan import names_instance

#: notice: upstream changes authored text (description or a section) and the actor has history
HISTORY_REVIEW = "history_review"
#: notice: the actor's chats pin another revision than the one its file records (a 107 rebind)
ACTOR_REBOUND = "actor_rebound"
#: notice: no adoption etag is recorded for the ancestor, so a hand edit of it cannot be ruled out
ANCESTOR_UNVERIFIED = "ancestor_unverified"


@dataclass(frozen=True)
class RebaseHistory:
    """What already names the actor in its campaign. The rebase keeps all of it unchanged.

    ``problems`` lists the paths of files that could not be read for this list (reported, never a
    refusal: the rebase does not write them).
    """

    chats: tuple[ChatRef, ...] = ()
    relations: tuple[RelationRef, ...] = ()
    accepted: tuple[AcceptedRef, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.chats or self.relations or self.accepted)


@dataclass(frozen=True)
class _PinnedChat:
    ref: ChatRef
    record: ChatRecord
    stored: ScopeRecord
    etag: str
    binding: SourceRef
    role: str


@dataclass(frozen=True)
class RebaseReview:
    """One actor's rebase, ready for a human: the rows to decide and what will and will not change."""

    actor: InstanceRef
    instance: EntityInstance
    instance_etag: str
    ancestor: EffectiveDefinition
    upstream: EffectiveDefinition
    rows: tuple[FieldReview, ...]
    #: chats whose active binding pins the ancestor: each is re-pinned to the upstream revision
    repinned: tuple[ChatRef, ...]
    #: chats whose active binding of the actor follows the head: not rewritten
    following: tuple[ChatRef, ...]
    history: RebaseHistory
    notices: tuple[str, ...]
    _envelope: RecordEnvelope = field(repr=False)
    _ancestor_etag: str = field(repr=False)
    _upstream_etag: str = field(repr=False)
    _chats: tuple[_PinnedChat, ...] = field(repr=False, default=())
    _chat_etags: tuple[tuple[ChatRef, str], ...] = field(repr=False, default=())

    @property
    def decisions_needed(self) -> tuple[str, ...]:
        return tuple(row.field for row in self.rows if row.needs_decision)


@dataclass
class RebaseChange:
    """One store transaction, not yet committed: ``preview`` for a human, then ``commit``."""

    store: LibraryStore
    handle: object
    puts: tuple[tuple[RecordEnvelope | ScopeRecord, str | None], ...]
    instance: EntityInstance
    #: (pinned definition, etag) the commit re-checks under the store lock: every layer of the
    #: ancestor's and the upstream's derivation chains, since the decisions were made on composed values
    definitions: tuple[tuple[SourceRef, str | None], ...]
    #: the campaign and the etag of every chat of it the review read: a chat added, removed or edited
    #: since could bind the actor to the old revision, so the commit re-checks them all under the lock
    campaign: str = ""
    chats: tuple[tuple[ChatRef, str], ...] = ()
    #: for an instance that follows its head: (unpinned source, the head the review adopted); a new
    #: head since would leave the rebased overrides composing over a revision nobody reviewed
    head: tuple[SourceRef, SourceRef] | None = None
    _committed: bool = field(default=False, repr=False)

    def _transaction(self):  # type: ignore[no-untyped-def]
        transaction = self.store.transaction()
        for envelope, expected in self.puts:
            transaction.put(envelope, expected)
        return transaction

    def preview(self) -> dict[str, bytes]:
        """The exact bytes each record will hold, keyed by logical path."""
        return self._transaction().preview()

    def commit(self, approvals: Mapping[str, str] | None = None) -> CommitResult:
        """Write exactly the previewed bytes, or nothing.

        ``StaleWrite`` when the actor, any chat of its campaign (one added or removed included), any
        layer of the ancestor's or the upstream's derivation chain, or (for an instance that follows
        its head) the head changed since the review (a revision file edited by hand is ``changed``);
        the store's authorization applies.
        """
        with self.store.lock:
            if self._committed:
                raise StoreError("this rebase was already committed")
            moved = []
            for ref, etag in self.definitions:
                found = self.store.check_definition(ref)
                current = None if isinstance(found, str) else found.etag
                if current is None or current != etag:
                    moved.append(("/".join(definition_parts(ref)), "absent" if current is None else "changed"))
            if self.head is not None:
                followed, adopted = self.head
                found_head = self.store.check_definition(followed)
                if isinstance(found_head, str) or found_head.ref != adopted:
                    moved.append(("/".join(manifest_parts(followed.source)), "changed"))
            found = self.store.campaign_chats(self.campaign)
            moved += [(problem.path, "changed") for problem in found.problems]
            expected = dict(self.chats)
            current = {ref: stored.etag for ref, stored in found.records.items()}
            for ref in sorted(set(expected) | set(current), key=str):
                if expected.get(ref) != current.get(ref):
                    moved.append(("/".join(chat_parts(ref)), "absent" if ref not in current else "changed"))
            if moved:
                raise StaleWrite(moved)
            result = self._transaction().commit(self.handle, approvals)
            self._committed = True
        return result


def _fail(message: str, reason: str) -> RebaseError:
    return RebaseError(message, reason)


class SourceRebase:
    """Reviewed rebases of campaign actors over one open, writable ``LibraryStore``.

    Trusted application code, like 107's ``ChatLibrary``: it is never handed to a worker. ``clock``
    gives the time a re-pinned chat binding is recorded at.
    """

    def __init__(self, store: LibraryStore, handle: object, *, clock: Callable[[], str]) -> None:
        self.store = store
        self.handle = handle
        self._clock = clock

    # -- review -------------------------------------------------------------------------------

    def review(self, actor: InstanceRef, target: SourceRef | None = None) -> RebaseReview:
        """The rebase of ``actor`` onto ``target`` (default: its source's head), or ``RebaseError``.

        See docs/REBASE.md for every refusal. Composition refusals of either revision propagate as
        ``PortrayalError``.
        """
        if not isinstance(actor, InstanceRef):
            raise TypeError(f"expected an InstanceRef, not {type(actor).__name__}")
        if target is not None and not isinstance(target, SourceRef):
            raise TypeError(f"expected a SourceRef target, not {type(target).__name__}")
        view = self.store.load()
        envelope, etag, instance = self._instance(view, actor)
        source = instance.source
        if source is None:
            raise _fail(f"{actor} is a campaign original; it has no source to rebase", "campaign_original")
        pinned, following, chat_etags = self._bindings(view, actor, source)
        notices: list[str] = []
        recorded = self._recorded_base(envelope, source, actor)
        revisions = {chat.binding for chat in pinned}
        if len(revisions) > 1:
            raise _fail(f"the chats of {actor.campaign_id} pin {actor} to {sorted(map(str, revisions))}; one chat "
                        "must be rebound or detached first, so that one revision is the common ancestor",
                        "binding_disagrees")
        if revisions:
            # composition follows the chat binding (107: a rebind reuses the actor without rewriting it),
            # so the revision the campaign has been playing is the common ancestor
            ancestor = next(iter(revisions))
            if recorded is not None and ancestor != recorded:
                notices.append(ACTOR_REBOUND)
        elif recorded is not None:
            ancestor = recorded
        else:
            raise _fail(f"{actor} follows {source} and records no base revision, so there is no common ancestor",
                        "no_ancestor")
        index = view.index
        broken = index.check(ancestor)
        if broken is not None:
            why = explainer(view)(ancestor) or broken.reason
            raise _fail(f"{actor}'s ancestor {ancestor} does not resolve ({why})", "missing_ancestor")
        ancestor_etag = index.resolve(ancestor).etag  # type: ignore[attr-defined]
        if ancestor == recorded and envelope.base_etag is not None:
            if envelope.base_etag != ancestor_etag:
                raise _fail(f"{ancestor} was edited by hand since {actor} adopted it, so it is no longer the common "
                            "ancestor; review the edit first", "ancestor_edited")
        else:  # no adoption etag recorded for this revision: the review cannot rule out a hand edit
            notices.append(ANCESTOR_UNVERIFIED)
        wanted = target if target is not None else source.unpinned()
        if wanted.entity_key != source.entity_key:
            raise _fail(f"{wanted} is not {source.unpinned()}: a rebase stays within one entity, and a same-named "
                        "or derived definition is never substituted", "other_entity")
        if index.check(wanted) is not None:
            raise _fail(f"{wanted} does not resolve", "missing_target")
        upstream_ref = index.resolve(wanted).ref
        if not source.pinned and upstream_ref != index.resolve(source).ref:
            raise _fail(f"{actor} follows the head of {source}; only the head can be adopted", "follows_head")
        full = [chat.ref for chat in pinned if len(chat.record.bindings) >= MAX_BINDINGS]
        if full:
            raise _fail(f"{[str(ref) for ref in full]} already hold {MAX_BINDINGS} bindings, so they cannot be "
                        "re-pinned", "binding_limit")
        read = _reader(view)
        explain = explainer(view)
        old = compose(ancestor, index, read, explain)
        new = compose(upstream_ref, index, read, explain)
        rows = review_fields(old, new, instance)
        history = self._history(view, actor)
        authored = any(row.field == "description" or row.field.startswith("section:") for row in rows)
        if authored and not history.empty:
            notices.append(HISTORY_REVIEW)
        return RebaseReview(actor, instance, etag, old, new, rows, tuple(chat.ref for chat in pinned), following,
                            history, tuple(notices), envelope, ancestor_etag,
                            index.resolve(upstream_ref).etag, tuple(pinned), chat_etags)  # type: ignore[attr-defined]

    def _instance(self, view: LibraryView, actor: InstanceRef) -> tuple[RecordEnvelope, str, EntityInstance]:
        stored = view.instances.get(actor)
        if stored is None:
            path = "/".join(address_parts(actor))
            reason = "undecodable_actor" if any(problem.path == path for problem in view.problems) else "missing_actor"
            raise _fail(f"{actor} cannot be read ({reason})", reason)
        envelope = stored.envelope
        schema = envelope.extra.get("schema")
        try:
            instance = instance_from_payload(actor, schema if isinstance(schema, str) else "", envelope.payload)
        except (CodecError, ValueError):
            raise _fail(f"{actor} cannot be decoded as an entity instance", "undecodable_actor") from None
        return envelope, stored.etag, instance

    @staticmethod
    def _recorded_base(envelope: RecordEnvelope, source: SourceRef, actor: InstanceRef) -> SourceRef | None:
        """The revision the instance records it was based on: its ``base``, else a pinned source."""
        if envelope.base is not None:
            try:
                recorded = SourceRef.parse(envelope.base)
            except ValueError:
                raise _fail(f"{actor}'s recorded base {envelope.base!r} is not a source revision",
                            "no_ancestor") from None
            if not recorded.pinned:
                raise _fail(f"{actor}'s recorded base {recorded} is not one revision", "no_ancestor")
        elif source.pinned:
            recorded = source
        else:
            return None
        if recorded.entity_key != source.entity_key:
            raise _fail(f"{actor}'s base {recorded} is not a revision of its source {source.unpinned()}",
                        "ancestor_mismatch")
        return recorded

    def _bindings(self, view: LibraryView, actor: InstanceRef, source: SourceRef
                  ) -> tuple[list[_PinnedChat], tuple[ChatRef, ...], tuple[tuple[ChatRef, str], ...]]:
        """Active bindings of the actor in its campaign's chats: pinned ones (re-pinned), following ones,
        and the etag of every chat of the campaign (the commit re-checks them all).

        Every chat of the campaign is read, because any of them may bind the actor; one that cannot
        be read or interpreted refuses the review (``chats_unreadable``) rather than leave a binding
        pinned to the old revision behind.
        """
        campaign = actor.campaign_id
        prefix = f"campaigns/{campaign}/chats"
        damaged = sorted({problem.path for problem in view.problems
                          if problem.path == prefix or problem.path.startswith(prefix + "/")})
        if damaged:
            raise _fail(f"chat files of {campaign} cannot be read ({', '.join(damaged)}), so the actor's bindings "
                        "cannot all be found", "chats_unreadable")
        pinned: list[_PinnedChat] = []
        following: list[ChatRef] = []
        refs = sorted((ref for ref in view.chats if ref.campaign_id == campaign), key=str)
        for ref in refs:
            stored = view.chats[ref]
            try:
                record = chat_from_payload(ref, stored.record.schema, stored.record.payload)
            except ChatCodecError:
                raise _fail(f"{ref} cannot be interpreted by this version, so its bindings cannot be checked",
                            "chats_unreadable") from None
            for binding in record.active_bindings():
                if binding.actor != actor:
                    continue
                if binding.ref.entity_key != source.entity_key:  # 107's roster check makes this unreachable
                    raise _fail(f"{ref} binds {actor} to {binding.ref}, not {source.unpinned()}", "binding_disagrees")
                if binding.ref.pinned:
                    pinned.append(_PinnedChat(ref, record, stored.record, stored.etag, binding.ref, binding.role))
                else:
                    following.append(ref)
        return pinned, tuple(following), tuple((ref, view.chats[ref].etag) for ref in refs)

    def _history(self, view: LibraryView, actor: InstanceRef) -> RebaseHistory:
        campaign = actor.campaign_id
        chats = tuple(sorted((ref for ref, stored in view.chats.items()
                              if ref.campaign_id == campaign and names_instance(stored.record.payload, actor)),
                             key=str))
        relations = self.store.campaign_relations(campaign)
        accepted = self.store.campaign_accepted(campaign)
        return RebaseHistory(
            chats,
            tuple(sorted((ref for ref, stored in relations.records.items()  # type: ignore[misc]
                          if names_instance(stored.record.payload, actor)), key=str)),
            tuple(sorted((ref for ref, stored in accepted.records.items()  # type: ignore[misc]
                          if names_instance(stored.record.payload, actor)), key=str)),
            tuple(sorted({problem.path for problem in (*relations.problems, *accepted.problems)})))

    # -- apply --------------------------------------------------------------------------------

    def apply(self, review: RebaseReview, decisions: Mapping[str, str]) -> RebaseChange:
        """The decided rebase as one change; nothing is written until ``commit``."""
        if not isinstance(review, RebaseReview):
            raise TypeError(f"expected a RebaseReview, not {type(review).__name__}")
        rebased, _ = rebase_instance(review.instance, review.rows, decisions, review.upstream)
        upstream = review.upstream.ref
        old = review._envelope
        derived_from = old.derived_from
        if derived_from is not None:
            try:
                followed = SourceRef.parse(derived_from)
            except ValueError:
                followed = None
            if followed is not None and followed.pinned and followed.entity_key == upstream.entity_key:
                derived_from = str(upstream)  # it named the pinned revision the actor was made from
        envelope = RecordEnvelope(review.actor, instance_to_payload(rebased), name=old.name, owner=old.owner,
                                  derived_from=derived_from, base=str(upstream), base_etag=review._upstream_etag,
                                  extra=old.extra)
        puts: list[tuple[RecordEnvelope | ScopeRecord, str | None]] = [(envelope, review.instance_etag)]
        for chat in review._chats:
            at = self._clock()
            repinned = NewBinding(upstream, chat.role, upstream.revision, review.actor,  # type: ignore[arg-type]
                                  CAMPAIGN_CONTINUITY)
            record = chat.record.detach(chat.binding, chat.role, at).attach(repinned, at)
            puts.append((ScopeRecord(chat.ref, CHAT_SCHEMA, chat_to_payload(record), owner=chat.stored.owner,
                                     extra=chat.stored.extra), chat.etag))
        chain = {node.ref: node.etag for node in (*review.ancestor.chain, *review.upstream.chain)}
        chain[review.ancestor.ref] = review._ancestor_etag
        chain[upstream] = review._upstream_etag
        source = review.instance.source
        head = None if source is None or source.pinned else (source.unpinned(), upstream)
        return RebaseChange(self.store, self.handle, tuple(puts), rebased, tuple(chain.items()),
                            review.actor.campaign_id, review._chat_etags, head)


def _reader(view: LibraryView):  # type: ignore[no-untyped-def]
    def read(ref: SourceRef):  # type: ignore[no-untyped-def]
        raw = view.index.resolve(ref)
        return entity_decoder(raw), getattr(raw, "etag", None)
    return read


__all__ = ["ACTOR_REBOUND", "ANCESTOR_UNVERIFIED", "HISTORY_REVIEW", "RebaseChange", "RebaseHistory", "RebaseReview",
           "SourceRebase"]
