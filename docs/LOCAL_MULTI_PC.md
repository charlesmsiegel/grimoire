# Local multi-PC composition (058)

`grimoire.localplay.LocalPlay` is a headless trusted-host service over an existing
`SceneRunner`. The host supplies human handles issued by that library's durable
authority. Strings naming people cannot act as handles. TextApi remains owner-only;
there is no new HTTP endpoint or authentication scheme.

The owner creates an ordered composition of one to sixteen present PCs. Each
controller edits and reads only their own contributions. Reads also show the
participant and controller order, without anyone else's draft text. Draft edits
route through the same intent and message interpretation as ordinary posts.
Commands and uncertain intents are refused; commands never execute during drafting.
NPCs cannot acquire player control through this service.

Every edit and reorder writes a fresh revision through guarded secret-state CAS
and invalidates confirmations. Each controller confirms the exact revision and
explicitly opts into sharing their routed contribution with the owner/director.
Confirmation covers their own routed text/audience and the visible actor/order
metadata, without showing other controllers' unpublished text. The owner can accept
only after all controllers confirm. Control-history or on-stage participant changes
invalidate the composition, including control moving away and back. Start a new
composition after such a change.

Draft JSON lives under `secrets/local-pc/<campaign>/<chat>/<draft>.json`, through
the existing transaction/recovery machinery, with a strict bounded decoder and an
exact-depth path allowlist. This reserved root cannot receive normal grants and is
excluded from replication. These are device-local unpublished drafts. Restart
recovers draft text and order; it deliberately requires fresh confirmations.
Confirmations retain issued handles in service memory, so edited JSON cannot mint
human approval. Hosts should retain one LocalPlay instance during a composition.

Acceptance calls `SceneRunner.submit_combined` once. The resulting `TurnRecord`
retains the existing primary `post`, ordered `extra_posts`, and a persisted
acceptance ID, control epoch and content digest. Each contribution has its own
stable ID and authenticated submitting controller. Receipt lookup returns an
already committed matching turn even after a lost acknowledgement or subsequent
control transition. A mismatching retry is refused. No per-PC sequence of writes
is used. The receipt does not grant authority to execute new effects after control
changes.

Generation remains an explicit `runner.generate` call after acceptance. A retry of
acceptance never invokes generation. Existing persisted running takes and fencing
still govern generation recovery. A director response uses every public post once;
each NPC gets its own call and history filtered by each post's original audience.
Owner-sharing consent does not add NPC listeners or disclose private messages to
the director's normal table history. No knowledge records are copied across PCs.
Generating an accepted turn after its control epoch changes is refused.

Combined adjudication and canonical effects are explicitly unsupported until a
multi-action policy is reviewed; primary-PC-only adjudication is refused. Existing
single-post callers and stored turn payloads remain compatible. Existing application
turn views explicitly refuse combined rendering. Sprint 115 must add the local
controller UI/adapter and its review flow; this headless proof does not claim that
UI exists. Network collaboration and voice input remain separate integrations.

Synthetic `backend/tests/test_localplay.py` exercises two independent PCs, one fake
narrator response, per-NPC prompt filtering, lost acknowledgement and restart,
precommit failure, malformed approval data, stale revisions, consent invalidation,
private audiences, NPC refusal, and permanent delegation termination on control
regain. No real library data or paid provider calls are used.
