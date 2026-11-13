"""Local, headless multi-PC composition. No HTTP identity or authentication adapter.

Unpublished drafts are device-private guarded state. Confirmations are deliberately session-local:
reopening a service recovers drafts, but every controller must confirm again. A receipt already written
by SceneRunner survives any lost acknowledgement and is returned without generation or other effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import uuid4

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.scene import SceneRefused
from grimoire.store import etag
from grimoire.store.jsonfmt import parse_strict, thaw


def refuse(reason, message):
    raise SceneRefused(reason, message)


@dataclass(frozen=True)
class DraftView:
    revision: str
    participants: tuple[tuple[str, str], ...]
    own: tuple  # routed Post values, solely this controller's contributions


class LocalPlay:
    """A trusted host supplies issued handles; the service never creates a handle from a name.

    ``expected`` is the opaque revision returned by create/read/edit/reorder. Consent explicitly shares
    the controller's routed posts with the owner/director, never with additional NPC listeners.
    """
    def __init__(self, runner):
        self.runner = runner
        self._confirmations = {}  # (chat, draft, revision, principal) -> exact routed posts

    def _name(self, chat, draft):
        validate_segment(draft, "draft")
        return f"local-pc/{chat.campaign_id}/{chat.chat_id}/{draft}.json"

    def _epoch(self, chat):
        return max((e.seq for e in self.runner.sovereignty.load(chat.campaign_id).control_history), default=0)

    def _principal(self, scene, by):
        return self.runner._principal(scene, by)

    def _load(self, chat, draft, *, live=True):
        raw = self.runner.store.read_secret(self._name(chat, draft))
        if raw is None:
            refuse("no_draft", "no local draft with that id")
        try:
            if len(raw) > 256 * 1024:
                raise ValueError()
            state = thaw(parse_strict(raw))
            if (type(state) is not dict or set(state) != {"version", "epoch", "roster", "entries", "nonce"}
                    or type(state['version']) is not int or state['version'] != 1
                    or type(state['nonce']) is not str or len(state['nonce']) != 32
                    or type(state['epoch']) is not int or state['epoch'] < 0
                    or type(state['roster']) is not list or len(state['roster']) > 1000
                    or any(type(a) is not str for a in state['roster'])
                    or type(state['entries']) is not list or not 1 <= len(state['entries']) <= 16):
                raise ValueError()
            actors = set()
            for entry in state['entries']:
                if type(entry) is not dict or set(entry) != {'actor', 'principal', 'text', 'mode'}:
                    raise ValueError()
                if any(type(v) is not str for v in entry.values()):
                    raise ValueError()
                actor = InstanceRef.parse(entry['actor'])
                if actor.campaign_id != chat.campaign_id or actor in actors or len(entry['text']) > 16000:
                    raise ValueError()
                actors.add(actor)
            scene = self.runner._context(chat).scene
            if live:
                self.runner._ready(scene)
                if state['epoch'] != self._epoch(chat) or state['roster'] != [str(a) for a in scene.on_stage]:
                    refuse("controls_changed", "control history or participants changed; start a fresh composition")
                for e in state['entries']:
                    a = InstanceRef.parse(e['actor'])
                    if a not in scene.on_stage or scene.controller(a) != e['principal'] or a not in scene.players:
                        refuse("controls_changed", "a draft's controller or membership changed")
            return state, etag(raw), scene
        except SceneRefused:
            raise
        except (ValueError, TypeError, KeyError):
            refuse("invalid_draft", "local draft state is malformed")

    def _save(self, chat, draft, state, expected):
        state['nonce'] = uuid4().hex
        raw = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()
        if len(raw) > 256 * 1024:
            refuse("draft_too_large", "combined draft exceeds the local storage limit")
        tx = self.runner.store.transaction()
        tx.put_secret(self._name(chat, draft), raw, expected)
        tx.commit(self.runner.handle)
        self._confirmations = {key: value for key, value in self._confirmations.items()
                               if key[:2] != (chat, draft)}
        return etag(raw)

    def create(self, chat, by, actors, *, draft=None):
        draft = draft or f"draft-{uuid4().hex}"
        with self.runner.authority.lock, self.runner.store.lock:
            scene = self.runner._context(chat).scene
            principal = self._principal(scene, by)
            if principal not in self.runner.authority.owners:
                refuse("not_owner", "the owner assembles the participant order")
            self.runner._ready(scene)
            actors = tuple(actors)
            if not 1 <= len(actors) <= 16 or len(set(actors)) != len(actors):
                refuse("invalid_field", "one to sixteen distinct PCs required")
            if any(a not in scene.players or a not in scene.on_stage for a in actors):
                refuse("not_a_pc", "only present human-controlled PCs can draft")
            state = dict(version=1, epoch=self._epoch(chat), roster=[str(a) for a in scene.on_stage],
                         entries=[dict(actor=str(a), principal=scene.controller(a), text='', mode='auto') for a in actors])
            return draft, self._save(chat, draft, state, None)

    def _view(self, chat, draft, by):
        state, revision, scene = self._load(chat, draft)
        principal = self._principal(scene, by)
        entries = [e for e in state['entries'] if e['principal'] == principal]
        own = tuple(self.runner._prepare_post(scene, InstanceRef.parse(e['actor']), principal,
                                             e['text'], e['mode'], f"{chat.chat_id}.1", 1)
                    for e in entries if e['text'])
        return state, principal, DraftView(revision, tuple((e['actor'], e['principal']) for e in state['entries']), own)

    def read(self, chat, draft, by):
        with self.runner.authority.lock, self.runner.store.lock:
            return self._view(chat, draft, by)[2]

    def edit(self, chat, draft, by, actor, text, *, expected, mode='auto'):
        with self.runner.authority.lock, self.runner.store.lock:
            state, revision, scene = self._load(chat, draft)
            principal = self._principal(scene, by)
            if revision != expected:
                refuse("stale_draft", "read the current revision before editing")
            entry = next((e for e in state['entries'] if e['actor'] == str(actor)), None)
            if entry is None or entry['principal'] != principal:
                refuse("not_your_character", "only this PC's controller edits its draft")
            if type(text) is not str or len(text) > 16000:
                refuse("draft_too_large", "each draft is limited to 16000 characters")
            self.runner._prepare_post(scene, actor, principal, text, mode, f"{chat.chat_id}.1", 1)
            entry.update(text=text, mode=mode)
            return self._save(chat, draft, state, expected)

    def reorder(self, chat, draft, by, actors, *, expected):
        with self.runner.authority.lock, self.runner.store.lock:
            state, revision, scene = self._load(chat, draft)
            if self._principal(scene, by) not in self.runner.authority.owners:
                refuse("not_owner", "only the owner changes the order")
            if revision != expected:
                refuse("stale_draft", "read the current revision before reordering")
            order = [str(a) for a in actors]
            if len(order) != len(set(order)) or set(order) != {e['actor'] for e in state['entries']}:
                refuse("invalid_field", "reorder must retain every participant exactly once")
            state['entries'].sort(key=lambda e: order.index(e['actor']))
            return self._save(chat, draft, state, expected)

    def confirm(self, chat, draft, by, *, expected, share_with_owner=False):
        with self.runner.authority.lock, self.runner.store.lock:
            state, principal, view = self._view(chat, draft, by)
            if view.revision != expected:
                refuse("stale_draft", "consent covers the exact combined revision")
            if share_with_owner is not True or not view.own or any(not e['text'] for e in state['entries']):
                refuse("consent_required", "every draft needs text and explicit sharing consent")
            self._confirmations[(chat, draft, expected, principal)] = (by, view.own)
            return view

    def accept(self, chat, draft, by, *, expected):
        with self.runner.authority.lock, self.runner.store.lock:
            # Receipt first: control changes after the committed turn do not undo historical evidence.
            state, revision, scene = self._load(chat, draft, live=False)
            if self._principal(scene, by) not in self.runner.authority.owners:
                refuse("not_owner", "only the owner reviews accepted shared drafts")
            if revision != expected:
                refuse("stale_draft", "acceptance covers the exact combined revision")
            acceptance_id = "local-" + sha256(f"{draft}:{revision}".encode()).hexdigest()
            existing = next((s.record for s in scene.turns.turns if s.record.acceptance_id == acceptance_id), None)
            if existing is not None:
                digest = sha256(json.dumps([(e['actor'], e['principal'], e['text'], e['mode'])
                                            for e in state['entries']], ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
                if existing.acceptance_digest != digest or existing.acceptance_epoch != state['epoch']:
                    refuse("acceptance_mismatch", "receipt content does not match")
                return existing
            self._load(chat, draft)
            contributions = []
            for entry in state['entries']:
                key = (chat, draft, expected, entry['principal'])
                confirmation = self._confirmations.get(key)
                if confirmation is None:
                    refuse("consent_required", "every controller must confirm this revision in this session")
                handle, own = confirmation
                actor = InstanceRef.parse(entry['actor'])
                current = self.runner._prepare_post(scene, actor, entry['principal'], entry['text'], entry['mode'],
                                                    f"{chat.chat_id}.1", 1)
                if current not in own:
                    refuse("consent_required", "the routed contribution changed; confirm again")
                contributions.append((actor, handle, entry['text'], entry['mode']))
            return self.runner.submit_combined(chat, by, acceptance_id, contributions, epoch=state['epoch'])
