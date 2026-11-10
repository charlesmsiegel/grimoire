"""The import, migration and change review API (110): the review UI's ops, as JSON over one open library.

``ReviewApi`` is trusted application code acting for the library's owner, like 044's ``TextApi``. It keeps no
record of its own and writes only through the services that own each write:

- 037's ``CardImporter`` (character, scenario and lorebook cards)
- 036's ``StImporter`` (a SillyTavern chat history)
- 011's ``scan`` (read-only V0.2.1 inventory) and 026's ``plan_import`` (the reviewed apply)
- 042's ``SourceRebase`` (take-or-keep rebase of a campaign actor)
- 043's ``ChangeSetLibrary`` (a reviewed world change set)

Every write is a two-step **proposal**: a ``*.preview`` op plans it and returns the exact bytes of every file
and their digest; ``proposal.apply`` names the proposal and that digest. Nothing is written by a preview. See
docs/REVIEW_UI.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from grimoire.archaeologystore import ImportRefused as StRefused
from grimoire.archaeologystore import MAX_EXPORT_BYTES, StImporter, scan_st_export
from grimoire.cards import CardError
from grimoire.cardstore import MAX_CARD_BYTES, CardImporter, ImportDenied
from grimoire.changesets import ChangeSetError
from grimoire.changesets import decode as decode_changeset
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.changesetstore.library import ChangeSetConflict
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef
from grimoire.import_v1 import ImportRefused as V1Refused
from grimoire.import_v1 import Selection, plan_import, read_receipts
from grimoire.library import LibraryPathError
from grimoire.migrate_v1 import ManifestMismatch, NotAV1Library, render_json, scan
from grimoire.migrate_v1._fs import SourceChanged, TooLarge
from grimoire.rebase import RebaseError
from grimoire.rebasestore import SourceRebase
from grimoire.security import Permission
from grimoire.store import LibraryStore, StaleWrite, StoreError
from grimoire.store.errors import WriteDenied
from grimoire.textapi import ApiRefused

from .proposals import Proposals, preview_digest
from .views import bounded, file_views, jsonable

#: 038's cap on one picture: a card's portrait may be no larger
MAX_PORTRAIT_BYTES = 32 * 1024 * 1024
#: V1 scans and rebase reviews held at once (each is a few previews' worth); the oldest is dropped past this
MAX_HELD = 4
#: rows of one manifest list shown at once; the rest are counted
MAX_ROWS = 500
MAX_TEXT = 1000

NOT_FOUND = frozenset({"unknown_op", "no_proposal", "no_scan", "no_review", "no_chat", "no_actor"})
BAD_REQUEST = frozenset({
    "invalid_field", "too_large", "invalid_json", "invalid_card", "unsupported_format", "invalid_options",
    "invalid_target", "not_a_card_source", "not_a_v1_library", "unsafe_paths", "unknown_speaker", "role_kind",
    "invalid_selection", "unstorable", "unstorable_id",
})


class ReviewRefused(ApiRefused):
    """One review op could not be done; nothing was written. ``details`` carries what the reviewer needs to
    act on (the stale paths, the conflicts, the paths a handle may not write), never a record's text."""

    def __init__(self, reason: str, message: str = "", status: int | None = None, details: object = None) -> None:
        if status is None:
            status = 404 if reason in NOT_FOUND else 400 if reason in BAD_REQUEST else 409
        super().__init__(reason, message, status)
        self.details = details

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, str(self), self.status, self.details))

    def to_json(self) -> dict:
        out = super().to_json()
        if self.details is not None:
            out["details"] = self.details
        return out


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _field(args: Mapping[str, object], key: str, kind: type, *, required: bool = True, default=None):  # type: ignore[no-untyped-def]
    value = args.get(key, default)
    if value is None and not required:
        return default
    if type(value) is not kind:
        raise ReviewRefused("invalid_field", f"{key} must be a {kind.__name__}")
    return value


def _text(args: Mapping[str, object], key: str, *, required: bool = True, limit: int = MAX_TEXT) -> str | None:
    value = _field(args, key, str, required=required)
    if value is not None and (not value or len(value) > limit):
        raise ReviewRefused("invalid_field", f"{key} must be nonblank text of at most {limit} characters")
    return value


def _ref(parse: Callable[[str], object], args: Mapping[str, object], key: str, *, required: bool = True):  # type: ignore[no-untyped-def]
    text = _field(args, key, str, required=required)
    if text is None:
        return None
    try:
        return parse(text)
    except (ValueError, TypeError):
        raise ReviewRefused("invalid_field", f"{key} is not a valid reference") from None


def _bytes(args: Mapping[str, object], key: str, limit: int, *, required: bool = True) -> bytes | None:
    """A base64 argument, decoded, at most ``limit`` bytes."""
    text = _field(args, key, str, required=required)
    if text is None:
        return None
    if len(text) > (limit + 2) // 3 * 4 + 4:
        raise ReviewRefused("too_large", f"{key} is larger than {limit} bytes")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ReviewRefused("invalid_field", f"{key} must be base64") from None
    if len(data) > limit:
        raise ReviewRefused("too_large", f"{key} is larger than {limit} bytes")
    return data


def _refused(error: Exception, details: object = None) -> ReviewRefused:
    reason = getattr(error, "reason", None)
    return ReviewRefused(reason if isinstance(reason, str) and reason else type(error).__name__, str(error),
                         details=details)


def _stale(error: StaleWrite) -> ReviewRefused:
    return ReviewRefused("stale", "the library changed since this was previewed; nothing was written. Preview "
                         "it again", details=[{"path": path, "reason": reason} for path, reason in error.stale])


class _Held(OrderedDict):
    """A bounded id -> value map; the oldest goes past ``limit``."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def hold(self, key: str, value: object) -> None:
        self[key] = value
        while len(self) > self.limit:
            self.popitem(last=False)


class _OwnerProposes:
    """037's planner asks its handle to ``propose`` every staged path. Here the owner proposes to
    themselves and approves on this very screen, so a path the owner may ``apply`` may be proposed:
    the answer is the owner's own ``propose`` decision, or failing that their ``apply`` decision for the
    same bytes. Every other ask, and every write (037 writes through the real handle), is the owner's."""

    __slots__ = ("_handle",)

    def __init__(self, handle) -> None:  # type: ignore[no-untyped-def]
        self._handle = handle

    def authorize(self, permission, resource, payload=None, approval_id=None):  # type: ignore[no-untyped-def]
        decision = self._handle.authorize(permission, resource, payload, approval_id)
        if permission is Permission.PROPOSE and not decision.allowed:
            applied = self._handle.authorize(Permission.APPLY, resource, payload, approval_id)
            if applied.allowed:
                return applied
        return decision


class ReviewApi:
    """The review ops on one open library, for its ``owner`` (a ``human:`` principal). ``authority`` is the
    library's durable authority: its trusted owner handle is what every planner and service writes with, and
    a change set that needs a per-file approval gets it registered there on the owner's own ``apply``. Calls
    are serialized: one library, one writer at a time. ``environ`` and ``home`` are what 011's path checks
    read to keep a V1 library and its scan apart from this one (default: this process's)."""

    def __init__(self, store: LibraryStore, authority, *, owner: str,  # type: ignore[no-untyped-def]
                 clock: Callable[[], str] = _utc_now, new_id: Callable[[str], str] | None = None,
                 environ: Mapping[str, str] | None = None, home=None) -> None:
        self.store = store
        self.authority = authority
        self.owner = owner
        self.handle = authority.handle_for(owner, trusted=True)
        self._clock = clock
        self._new_id = new_id or (lambda prefix: f"{prefix}-{uuid.uuid4().hex[:16]}")
        self._environ = environ
        self._home = home
        self.proposals = Proposals(new_id=lambda: self._new_id("p"))
        self.scans = _Held(MAX_HELD)
        self.reviews = _Held(MAX_HELD)
        self.cards = CardImporter(store, self.handle, owner=owner, clock=clock)
        self.cards.handle = _OwnerProposes(self.handle)  # only its plan-time propose check reads this
        self.histories = StImporter(store, self.handle, clock=clock, owner=owner)
        self.chats = ChatLibrary(store, self.handle, clock=clock, new_id=self._new_id, owner=owner)
        self.rebases = SourceRebase(store, self.handle, clock=clock)
        self.changesets = ChangeSetLibrary(store, self.handle)
        self._lock = threading.RLock()
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "review.capabilities": self._capabilities,
            "card.preview": self._card_preview,
            "history.scan": self._history_scan, "history.preview": self._history_preview,
            "v1.scan": self._v1_scan, "v1.preview": self._v1_preview, "v1.receipts": self._v1_receipts,
            "rebase.candidates": self._rebase_candidates, "rebase.review": self._rebase_review,
            "rebase.preview": self._rebase_preview,
            "changeset.review": self._changeset_review,
            "proposal.get": self._proposal_get, "proposal.apply": self._proposal_apply,
            "proposal.reject": self._proposal_reject,
        }

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(self._ops)

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        """Run one op. ``ReviewRefused`` (an ``ApiRefused``) carries the domain service's own reason."""
        handler = self._ops.get(op)
        if handler is None:
            raise ReviewRefused("unknown_op", f"no op {op!r}")
        if args is not None and not isinstance(args, Mapping):
            raise ReviewRefused("invalid_field", "the arguments must be an object")
        with self._lock:
            return handler(dict(args or {}))

    # -- what this slice can and cannot do ------------------------------------------------------
    def _capabilities(self, args: Mapping[str, object]) -> dict:
        return {
            "ops": list(self._ops),
            "imports": {
                "card": {"formats": ["chara_card_v3 (JSON)", "grimoire.scenario-card/1", "lorebook_v3"],
                         "max_bytes": MAX_CARD_BYTES, "portrait_max_bytes": MAX_PORTRAIT_BYTES},
                "history": {"formats": ["SillyTavern chat export (JSONL)"], "max_bytes": MAX_EXPORT_BYTES},
                "v1": {"formats": ["Grimoire 0.2.1 library folder, read only"]},
            },
            "unavailable": [
                {"feature": "png_and_charx_cards", "blocked_by": "037 later milestone",
                 "detail": "PNG and CHARX card containers are refused unsupported_format"},
                {"feature": "card_v1_v2_and_world_info", "blocked_by": "037 later milestone",
                 "detail": "Character Card V1/V2 and SillyTavern world info are refused unsupported_format"},
                {"feature": "remote_card_art", "blocked_by": "037 (nothing is fetched)",
                 "detail": "remote pictures stay unresolved slots"},
                {"feature": "choose_link_suggestions", "blocked_by": "037 later milestone",
                 "detail": "name-mention suggestions are shown, never linked"},
                {"feature": "persisted_changeset_proposals", "blocked_by": "043 milestone 3",
                 "detail": "a change set is reviewed from its document; pending proposals are not stored"},
                {"feature": "non_actor_rebase", "blocked_by": "042 later milestone",
                 "detail": "a pinned scenario or lorebook moves only by detaching and attaching it again"},
                {"feature": "v1_campaign_sprites_and_voice_anchors", "blocked_by": "026 later milestones",
                 "detail": "listed as not imported in the V1 review"},
            ],
        }

    # -- proposals ------------------------------------------------------------------------------
    def _propose(self, kind: str, preview: Callable[[], dict[str, bytes]], commit: Callable[[], dict], view: dict,
                 *, diffs: Mapping[str, str] | None = None, reject: Callable[[], None] | None = None) -> dict:
        files = file_views(preview(), self._current, diffs)
        return self.proposals.add(kind, preview=preview, commit=commit, view=view, files=files,
                                  reject=reject).describe()

    def _current(self, path: str) -> bytes | None:
        return self.store.read_exact(path)

    def _proposal(self, args: Mapping[str, object]):  # type: ignore[no-untyped-def]
        proposal_id = _text(args, "proposal", limit=128)
        proposal = self.proposals.get(proposal_id)  # type: ignore[arg-type]
        if proposal is None:
            raise ReviewRefused("no_proposal", "no such proposal here: it was applied, rejected, dropped or made "
                                "before the app restarted. Preview it again")
        return proposal

    def _proposal_get(self, args: Mapping[str, object]) -> dict:
        return self._proposal(args).describe()

    def _proposal_apply(self, args: Mapping[str, object]) -> dict:
        """Commit a proposal, only for the digest the reviewer approved, and only while its plan still stages
        exactly those bytes. The domain service re-checks the library under its lock."""
        proposal = self._proposal(args)
        digest = _text(args, "digest", limit=128)
        if digest != proposal.digest:
            raise ReviewRefused("stale_approval", "the approval names other bytes than this proposal stages; "
                                "review it again", details={"proposal": proposal.digest})
        if preview_digest(proposal.kind, proposal.preview()) != proposal.digest:  # never expected: plans are fixed
            proposal.state = "failed"
            self.proposals.drop(proposal.proposal_id)
            raise ReviewRefused("stale_approval", "the staged bytes changed since they were shown; preview again")
        try:
            result = proposal.commit()
        except StaleWrite as error:
            proposal.state = "failed"
            self.proposals.drop(proposal.proposal_id)
            raise _stale(error) from None
        except ChangeSetConflict as error:
            proposal.state = "failed"
            self.proposals.drop(proposal.proposal_id)
            raise ReviewRefused("conflict", "the world changed since review; nothing was written",
                                details={"conflicts": jsonable(error.conflicts), "redraft": error.needs_redraft}) \
                from None
        except WriteDenied as error:
            raise ReviewRefused("denied", "the library's authority refused this write; nothing was written",
                                details=jsonable(error.decisions)) from None
        except (CardError, StRefused, V1Refused, RebaseError, ChangeSetError, StoreError) as error:
            proposal.state = "failed"
            self.proposals.drop(proposal.proposal_id)
            raise _refused(error) from None
        proposal.state = "applied"
        self.proposals.drop(proposal.proposal_id)
        return {"proposal": proposal.proposal_id, "kind": proposal.kind, "state": "applied", "result": result}

    def _proposal_reject(self, args: Mapping[str, object]) -> dict:
        proposal = self._proposal(args)
        if proposal.reject is not None:
            try:
                proposal.reject()
            except (ChangeSetError, StoreError) as error:
                raise _refused(error) from None
        proposal.state = "rejected"
        self.proposals.drop(proposal.proposal_id)
        return {"proposal": proposal.proposal_id, "kind": proposal.kind, "state": "rejected"}

    # -- 037: cards -----------------------------------------------------------------------------
    def _card_preview(self, args: Mapping[str, object]) -> dict:
        card = _bytes(args, "card", MAX_CARD_BYTES)
        portrait = _bytes(args, "portrait", MAX_PORTRAIT_BYTES, required=False)
        lorebook = _bytes(args, "lorebook", MAX_CARD_BYTES, required=False)
        origin = _text(args, "origin", required=False)
        target = _text(args, "target", required=False, limit=256)
        as_scenario = _field(args, "as_scenario", bool, required=False, default=False)
        replace_edits = _field(args, "replace_edits", bool, required=False, default=False)
        try:
            planned = self.cards.plan(card, origin=origin, target=target, portrait=portrait, lorebook=lorebook,  # type: ignore[arg-type]
                                      as_scenario=as_scenario, replace_edits=replace_edits)
        except CardError as error:
            raise ReviewRefused(error.reason, str(error), details={"detail": getattr(error, "detail", "")}) \
                from None
        except ImportDenied as error:
            raise ReviewRefused("denied", str(error), details=[list(item) for item in error.denied]) from None
        except StaleWrite as error:
            raise _stale(error) from None
        except StoreError as error:
            raise _refused(error) from None
        report = planned.report

        def commit() -> dict:
            result = planned.commit(None)
            return {"status": report.status, "source": report.source, "main": str(report.main),
                    "records": [str(ref) for ref in report.records], "written": sorted(result.etags)}

        return self._propose("card", planned.preview, commit, jsonable(report))

    # -- 036: chat history ----------------------------------------------------------------------
    def _history_scan(self, args: Mapping[str, object]) -> dict:
        data = _bytes(args, "export", MAX_EXPORT_BYTES)
        try:
            found = scan_st_export(data)  # type: ignore[arg-type]
        except StRefused as error:
            raise ReviewRefused(error.reason, str(error), details={"line": error.line}) from None
        return {"sha256": found.sha256, "bytes": found.byte_length, "counts": dict(found.counts),
                "speakers": jsonable(found.speakers), "losses": jsonable(found.losses),
                "campaign": found.campaign_id}

    def _history_preview(self, args: Mapping[str, object]) -> dict:
        data = _bytes(args, "export", MAX_EXPORT_BYTES)
        given = _field(args, "speakers", dict, required=False, default={})
        speakers: dict[str, SourceRef] = {}
        for sid, ref in given.items():
            if type(ref) is not str:
                raise ReviewRefused("invalid_field", "each speaker maps to a character source ref")
            try:
                speakers[sid] = SourceRef.parse(ref)
            except ValueError:
                raise ReviewRefused("invalid_field", f"speaker {sid} does not map to a valid source ref") from None
        books = _field(args, "lorebooks", list, required=False, default=[])
        lorebooks = []
        for ref in books:
            try:
                lorebooks.append(SourceRef.parse(ref) if type(ref) is str else None)
            except ValueError:
                lorebooks.append(None)
        if None in lorebooks:
            raise ReviewRefused("invalid_field", "lorebooks must be source refs")
        label = _text(args, "label", required=False)
        name = _text(args, "name", required=False)
        try:
            planned = self.histories.plan(data, speakers=speakers, lorebooks=tuple(lorebooks), label=label,  # type: ignore[arg-type]
                                          name=name)
        except StRefused as error:
            raise ReviewRefused(error.reason, str(error), details={"line": error.line}) from None
        except StoreError as error:
            raise _refused(error) from None
        view = {"status": planned.status, "chat": str(planned.chat), "provider_calls": planned.provider_calls,
                "speakers": {sid: str(ref) for sid, ref in planned.speakers.items()},
                "actors": {sid: str(ref) for sid, ref in planned.actors.items()},
                "counts": dict(planned.scan.counts), "pages": len(planned.pages),
                "losses": jsonable(planned.losses), "following": [str(ref) for ref in planned.following],
                "accepted_events": [], "memories": []}

        def commit() -> dict:
            result = planned.commit(None)
            return {"status": planned.status, "chat": str(planned.chat),
                    "written": sorted(getattr(result, "etags", {}) or {})}

        return self._propose("history", planned.preview, commit, view)

    # -- 011 + 026: V0.2.1 libraries ---------------------------------------------------------------
    def _v1_scan(self, args: Mapping[str, object]) -> dict:
        source = _text(args, "source", limit=4096)
        archive_id = _text(args, "archive_id", limit=128)
        try:
            result = scan(source, archive_id=archive_id, destination=self.store.root,  # type: ignore[arg-type]
                          environ=self._environ, home=self._home)
        except LibraryPathError as error:
            raise ReviewRefused("unsafe_paths", str(error)) from None
        except NotAV1Library as error:
            raise ReviewRefused("not_a_v1_library", str(error)) from None
        except SourceChanged as error:
            raise ReviewRefused("source_changed", f"the V1 library changed while it was read; stop V1 or scan a "
                                f"frozen copy ({error})") from None
        except TooLarge as error:
            raise ReviewRefused("too_large", str(error)) from None
        except ValueError as error:  # an archive id that is no identity segment
            raise ReviewRefused("invalid_field", str(error)) from None
        manifest = json.loads(render_json(result.manifest))  # exactly what a reviewer saves and reloads
        scan_id = self._new_id("scan")
        self.scans.hold(scan_id, (source, manifest))
        view = _manifest_view(manifest)
        view["scan"] = scan_id
        view["proposed_selection"] = _selection_view(Selection.proposed(manifest))
        return view

    def _scan(self, args: Mapping[str, object]) -> tuple[str, dict]:
        scan_id = _text(args, "scan", limit=128)
        held = self.scans.get(scan_id)
        if held is None:
            raise ReviewRefused("no_scan", "no such scan here; scan the V1 library again")
        return held  # type: ignore[return-value]

    def _v1_preview(self, args: Mapping[str, object]) -> dict:
        source, manifest = self._scan(args)
        selection = _selection(args.get("selection"), manifest)
        try:
            plan = plan_import(self.store, source, manifest, handle=self.handle, owner=self.owner,
                               selection=selection, clock=self._clock, environ=self._environ, home=self._home)
        except ManifestMismatch as error:
            raise ReviewRefused(error.reason, f"{error}; scan the V1 library again") from None
        except V1Refused as error:
            raise _refused(error) from None
        except LibraryPathError as error:
            raise ReviewRefused("unsafe_paths", str(error)) from None
        except SourceChanged as error:
            raise ReviewRefused("source_changed", f"the V1 library changed while it was read ({error})") from None
        except StoreError as error:
            raise _refused(error) from None
        review = plan.review()

        def commit() -> dict:
            result = plan.apply(None)
            return {"receipt": None if result.receipt is None else str(result.receipt),
                    "written": sorted(result.etags), "provider_calls": 0}

        return self._propose("v1", plan.preview, commit, _v1_review_view(review))

    def _v1_receipts(self, args: Mapping[str, object]) -> dict:
        archive_id = _text(args, "archive_id", limit=128)
        try:
            receipts = read_receipts(self.store, archive_id)  # type: ignore[arg-type]
        except V1Refused as error:
            raise _refused(error) from None
        except ValueError as error:
            raise ReviewRefused("invalid_field", str(error)) from None
        return {"archive_id": archive_id, "runs": receipts.run, "written": len(receipts.written),
                "mapping": {key: len(rows) for key, rows in receipts.mapping.items()}}

    # -- 042: rebase ----------------------------------------------------------------------------
    def _rebase_candidates(self, args: Mapping[str, object]) -> dict:
        """The actors of one chat whose pinned source has a newer head: what a rebase review could offer."""
        chat = _ref(ChatRef.parse, args, "chat")
        try:
            record, _ = self.chats.read_chat(chat)  # type: ignore[arg-type]
        except StoreError as error:
            raise ReviewRefused("no_chat", f"{chat} cannot be read ({type(error).__name__})") from None
        except Exception as error:  # noqa: BLE001 - a chat library refusal: shown by reason
            raise _refused(error) from None
        rows = []
        for binding in record.bindings:
            if binding.detached_seq is not None or binding.actor is None:
                continue
            found = self.store.check_definition(binding.ref.unpinned())
            head = None if isinstance(found, str) else str(found.ref)
            rows.append({"actor": str(binding.actor), "role": binding.role, "binding": str(binding.ref),
                         "pinned": binding.ref.pinned, "head": head,
                         "problem": found if isinstance(found, str) else None,
                         "behind": head is not None and binding.ref.pinned and head != str(binding.ref)})
        return {"chat": str(chat), "actors": rows}

    def _rebase_review(self, args: Mapping[str, object]) -> dict:
        actor = _ref(InstanceRef.parse, args, "actor")
        target = _ref(SourceRef.parse, args, "target", required=False)
        try:
            review = self.rebases.review(actor, target)  # type: ignore[arg-type]
        except RebaseError as error:
            raise ReviewRefused(error.reason, str(error), details={"fields": list(getattr(error, "fields", ()))}) \
                from None
        except (StoreError, ValueError) as error:
            raise _refused(error) from None
        review_id = self._new_id("rebase")
        self.reviews.hold(review_id, review)
        return {"review": review_id, **_rebase_view(review)}

    def _rebase_preview(self, args: Mapping[str, object]) -> dict:
        review_id = _text(args, "review", limit=128)
        review = self.reviews.get(review_id)
        if review is None:
            raise ReviewRefused("no_review", "no such review here; review the rebase again")
        decisions = _field(args, "decisions", dict, required=False, default={})
        if not all(type(key) is str and value in ("take", "keep") for key, value in decisions.items()):
            raise ReviewRefused("invalid_field", "each decision is take or keep")
        try:
            change = self.rebases.apply(review, decisions)  # type: ignore[arg-type]
        except RebaseError as error:
            raise ReviewRefused(error.reason, str(error), details={"fields": list(getattr(error, "fields", ()))}) \
                from None
        view = {**_rebase_view(review), "decisions": dict(decisions)}

        def commit() -> dict:
            result = change.commit(None)
            self.reviews.pop(review_id, None)
            return {"actor": str(review.actor), "upstream": str(review.upstream.ref),
                    "repinned": [str(ref) for ref in review.repinned], "written": sorted(result.etags)}

        return self._propose("rebase", change.preview, commit, view)

    # -- 043: world change sets -----------------------------------------------------------------
    def _changeset_review(self, args: Mapping[str, object]) -> dict:
        document = _field(args, "changeset", dict)
        try:
            changeset = decode_changeset(document)
            review = self.changesets.review(changeset)
        except ChangeSetError as error:
            raise ReviewRefused(error.reason, str(error), details={"detail": error.detail}) from None
        except StoreError as error:
            raise _refused(error) from None
        diffs = {item.path: item.diff for item in review.files}
        view = {"changeset": changeset.changeset_id, "source": changeset.source, "title": changeset.title,
                "author": changeset.author, "rationale": changeset.rationale,
                "changes": [{"path": item.path, "action": item.action, "target": item.target,
                             "summary": item.summary, "old_path": item.old_path} for item in review.files],
                "conflicts": jsonable(review.conflicts), "permissions": jsonable(review.permissions),
                "blocked": list(review.blocked), "applicable": review.applicable,
                "needs_approval": list(review.needs_approval),
                "approvals": review.describe_approvals(self.owner) if review.needs_approval else {}}
        library, authority, owner = self.changesets, self.authority, self.owner

        def commit() -> dict:
            approvals = {}
            if review.needs_approval:  # the owner's own apply is the human action 007's approval needs
                approvals = library.approve(review, authority, approved_by=owner,
                                            new_id=lambda: self._new_id("approval"))
            result = library.apply(review, approvals)
            return {"changeset": result.changeset_id, "revisions": [str(ref) for ref in result.revisions],
                    "written": sorted(result.etags)}

        def reject() -> None:
            if review.state in ("open", "failed"):
                library.reject(review, authority)

        return self._propose("changeset", review.preview, commit, view, diffs=diffs, reject=reject)


# -- views ----------------------------------------------------------------------------------------
def _manifest_view(manifest: Mapping) -> dict:
    """The parts of an 011 inventory a reviewer decides on; ``entries`` (every file) are only counted."""
    identities = manifest.get("identity_candidates", {})
    images = manifest.get("images", {})
    return {
        "archive_id": manifest.get("archive_id"), "status": manifest.get("status"),
        "source_fingerprint": manifest.get("source_fingerprint"), "counts": manifest.get("counts", {}),
        "roots": manifest.get("roots", {}), "provider_calls": manifest.get("inference", {}).get("provider_calls", 0),
        "worlds": manifest.get("worlds", []),
        "campaigns": [{"id": row.get("id"), "name": row.get("name"), "appearances": len(row.get("appearances", ())),
                       "scenes": len(row.get("scenes", ())) if isinstance(row.get("scenes"), list) else row.get("scenes")}
                      for row in manifest.get("campaigns", [])],
        "findings": bounded(list(manifest.get("findings", [])), MAX_ROWS),
        "identities": bounded([{key: row.get(key) for key in ("legacy_id", "family", "status", "candidate", "path")}
                               for row in identities.get("definitions", [])], MAX_ROWS),
        "images": {key: (len(value) if isinstance(value, list) else value) for key, value in images.items()},
        "placements": bounded(list(images.get("placements", [])), MAX_ROWS) if isinstance(images.get("placements"), list)
        else None,
        "losses": bounded(list(manifest.get("losses", [])), MAX_ROWS),
        "unresolved": bounded(list(manifest.get("unresolved", [])), MAX_ROWS),
        "scenes": bounded([{key: row.get(key) for key in ("campaign", "path", "authorship", "number")}
                           for row in manifest.get("scenes", [])], MAX_ROWS),
    }


def _selection_view(selection: Selection) -> dict:
    return {"worlds": dict(selection.worlds), "campaigns": dict(selection.campaigns), "skip": sorted(selection.skip),
            "unproposed": [list(item) for item in selection.unproposed]}


def _selection(value: object, manifest: Mapping) -> Selection:
    if value is None:
        return Selection.proposed(manifest)
    if not isinstance(value, Mapping):
        raise ReviewRefused("invalid_field", "selection must be an object")
    worlds, campaigns, skip = value.get("worlds", {}), value.get("campaigns", {}), value.get("skip", [])
    for label, mapping in (("worlds", worlds), ("campaigns", campaigns)):
        if not isinstance(mapping, Mapping) or not all(type(k) is str and type(v) is str for k, v in mapping.items()):
            raise ReviewRefused("invalid_field", f"selection.{label} maps V1 ids to V2 ids")
    if not isinstance(skip, list) or not all(type(item) is str for item in skip):
        raise ReviewRefused("invalid_field", "selection.skip is a list of legacy ids")
    return Selection(dict(worlds), dict(campaigns), frozenset(skip))


def _v1_review_view(review: Mapping) -> dict:
    outcomes = review.get("outcomes", {})
    tally: dict[str, int] = {}
    for outcome in outcomes.values():
        tally[outcome] = tally.get(outcome, 0) + 1
    return {"archive_id": review.get("archive_id"), "provider_calls": review.get("provider_calls", 0),
            "selection": review.get("selection"), "outcome_counts": tally,
            "outcomes": bounded([{"path": path, "outcome": outcome} for path, outcome in outcomes.items()], MAX_ROWS),
            "mapping": {key: bounded(list(rows), MAX_ROWS) for key, rows in review.get("mapping", {}).items()},
            "unresolved": bounded(list(review.get("unresolved", [])), MAX_ROWS),
            "not_imported": bounded(list(review.get("not_imported", [])), MAX_ROWS),
            "skipped": review.get("skipped", []), "flags": bounded(list(review.get("flags", [])), MAX_ROWS),
            "manifest_unresolved": bounded(list(review.get("manifest_unresolved", [])), MAX_ROWS),
            "losses": bounded(list(review.get("losses", [])), MAX_ROWS)}


def _rebase_view(review) -> dict:  # type: ignore[no-untyped-def]
    return {"actor": str(review.actor), "ancestor": str(review.ancestor.ref), "upstream": str(review.upstream.ref),
            "rows": [{"field": row.field, "status": row.status, "needs_decision": row.needs_decision,
                      "keepable": row.keepable, "ancestor": jsonable(row.ancestor), "upstream": jsonable(row.upstream),
                      "local": jsonable(row.local), "changed": list(row.changed)} for row in review.rows],
            "decisions_needed": list(review.decisions_needed),
            "repinned": [str(ref) for ref in review.repinned], "following": [str(ref) for ref in review.following],
            "history": {name: len(getattr(review.history, name)) for name in
                        ("chats", "relations", "accepted", "turns", "transcripts", "knowledge", "problems")},
            "notices": list(review.notices), "art": jsonable(review.art)}


__all__ = ["MAX_PORTRAIT_BYTES", "ReviewApi", "ReviewRefused"]
