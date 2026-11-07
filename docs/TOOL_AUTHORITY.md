# Tool authority and untrusted content

**Status:** first slice of [Safe Tool Execution](roadmap/sprints/safe-tool-execution.md) (Wave 1, #007). `grimoire.security` is a pure, in-memory authorization kernel. Its proving scenario is **simulated** in `backend/tests/test_security_card_import.py` with a synthetic, deliberately obedient worker; there is no importer, file store, provider adapter or approval UI yet, and green tests are not an app-integrated proof. This page records the accepted *behavioral* contract. The dataclass shapes are not a persisted schema.

## Threat model

Imported Markdown, character cards (including extensions and greetings), rules scripts and every worker input, including model output, are **untrusted data**. They can contain prompt injection designed to make a worker read provider secrets, write into another world or campaign, traverse paths, call external tools or forge consent. V2 does not try to detect injection. It makes authority structurally independent of content: whatever text a worker reads, the most it can do is what a human already granted. Applying or executing additionally needs that human's approval of the exact bytes.

## Trust architecture

- Trusted application code owns the `Authorizer`: grants, approvals, policy and audit.
- Each worker gets only a `WorkerHandle` from `authorizer.handle_for(principal, trusted=False)`. The handle fixes the principal and origin and exposes only `authorize(permission, resource, payload=None, approval_id=None)`. Its API offers no way to add grants, register approvals, pick an identity or claim trust, and it refuses attribute writes. Arguments must be exact `str`/`bytes` instances; subclasses that could override hashing, equality or methods are `malformed_request`.
- `authorizer.issued(handle)` tells trusted code (such as the provider adapter) whether that authorizer's `handle_for` made a handle; a hand-built `WorkerHandle(...)` or one from another authorizer is not issued.
- Only `human:` and `app:` principals may hold a trusted handle. **Any code path that parses imported content or model output must use an untrusted handle, whatever its principal namespace.** For example, an `app:` importer must not be trusted.
- A bare `Authorizer` and its handles are single-threaded; callers that share one across threads must serialize access. A sink attached with a lock (`attach_sink(sink, lock=...)`) makes each decision (decide, record, consume) one step under that lock; `DurableAuthority` passes the store's lock and takes it in its read accessors too, so it is safe to share across threads.
- This is an API boundary enforced by tests and review. It is not an in-process sandbox: Python code that deliberately reaches into private attributes, closures or other modules can subvert any in-process check; isolating community scripts remains the sprint's moonshot.

## Permissions

| Permission | Meaning | Untrusted handle needs |
|---|---|---|
| `inspect` | read the addressed resource (no payload) | a covering grant |
| `propose` | record a staged proposal; never changes canonical state | a covering grant |
| `apply` | commit a change to canonical state | a covering grant **and** a matching approval |
| `execute` | invoke an external tool, provider or network endpoint (e.g. `providers/<name>`) | a covering grant **and** a matching approval |

A trusted `human:` or `app:` handle needs only a covering grant for any permission.

No permission implies another. `execute` is only an authorization decision until provider adapters exist; those adapters must call through it.

## Logical addresses

Requests name **logical addresses** inside one V2 library, such as `worlds/harbor/characters/wren`. An address is an app-generated slug path, not a display name or an OS path. Only canonical spellings are accepted, so two accepted strings never name the same resource:

- relative and `/`-separated, with 1–32 segments and at most 1024 characters in total;
- each segment has 1–128 characters and at most 255 UTF-8 bytes, and equals its `NFKC(casefold(NFKC(·)))` form;
- allowed characters are Unicode letters, combining marks (not as a segment's first character), decimal digits and `-`, `_`, `.`; Unicode `Default_Ignorable_Code_Point` characters (variation selectors, Hangul fillers, zero-width joiners) are rejected — other near-invisible marks are caught only by the non-ASCII flag described below;
- no segment may start or end with `.`, and no segment may be a Windows device name (`con`, `nul.txt`, `com1` and so on).

These rules reject traversal, absolute paths, separators, whitespace, control/format/surrogate/private/unassigned characters, uppercase and compatibility aliases. Scope containment compares whole segments, so `worlds/harbor2` is not inside `worlds/harbor`. Some words cannot appear verbatim (`straße`, final sigma); display names belong in record content. Validation follows the running Python's Unicode tables, so a newer address may be rejected by an older runtime. That fails closed.

**Reserved roots:** `secrets`, and the V1-only root entries (`llm_connections`, `sampler_presets`, `response_presets`, `prompt_layout.json`, `regex.json`, `chores.json`; the dotted V1 names are not valid addresses at all), because a V2 library root holding one beside `worlds/` reads as a V1 library and is refused ([library isolation](ARCHITECTURE.md#library-location-and-v1-isolation-implemented-grimoirelibrary)). Only the first segment is reserved: `worlds/w/regex.json` stays an ordinary address. No grant or approval can name a reserved address, and every request for one is denied.

## Grants, approvals and readable policy

- `Authorizer(owners)` takes the `human:` principals allowed to issue grants. A `Grant` gives one principal some permissions over one address subtree.
- An `Approval` consents to exactly one apply or execute. It names the principal, the permission, the exact canonical address and the `sha256:` digest of the payload. The kernel hashes the bytes actually submitted, and the executor must act on exactly those bytes and on `decision.resource`.
- Registration pins an approval to an active covering grant issued by the approving human. Without one, registration fails, so a human approves only uses of authority they delegated.
- An approval is consumed when it allows a request. Revoking its grant revokes it permanently, and a later equivalent grant does not revive it. Grant and approval IDs are never reused.
- `describe_policy()` lists active grants and pending approvals as sentences, such as `card-import may inspect, propose in worlds/harbor (granted by human:owner)`. Addresses with non-ASCII characters are flagged `[non-ASCII address]` so look-alike letters are visible.

## Decisions and audit

`authorize` never raises for its arguments and never allows on error. It denies with the first matching reason: `malformed_request`, `invalid_path`, `reserved_resource`, `not_granted`, `approval_required`. Any unexpected failure is `internal_error`. Allows are `granted` or `approved`. Every decision is recorded in the audit log before it takes effect; if recording fails, the request is denied as `internal_error` (possibly unrecorded) and no approval is consumed. Each record holds its principal, permission, canonical address (when valid), origin, outcome, reason, grant and consumed approval. Payload bytes and caller-supplied approval IDs are never recorded. Addresses chosen by content are recorded because they are the attempted target.

## Requirements on later sprints

- **Native file store:** map a validated address to an OS path only by joining its segments under the library root, never follow symlinks out of the library, and refuse to create a name that collides case- or normalization-insensitively with an existing differently spelled entry.
- **Approval UI ([World Change Sets](roadmap/sprints/world-change-sets.md), [Character Card Ingestion](roadmap/sprints/character-card-ingestion.md) preview):** issue approvals only from a direct human action. Show `describe()` text, including the non-ASCII flag and the staged content the digest refers to. **Headless half met by 043:** `ChangeSetLibrary.approve` issues one approval per reviewed file over its exact bytes, and the review carries the `describe()` sentences and the staged bytes to show ([CHANGESETS](CHANGESETS.md#approvals-the-first-approval-ui-007)). The UI that calls it on a human's click is not built.
- **Durable audit:** implemented by `grimoire.ledgers.DurableAuthority` (010b; see [Durable authority](#durable-authority-010b)). Surfacing it to people belongs to [Durable Action Recovery](roadmap/sprints/durable-action-recovery.md).
- **Provider adapters and network ([Model Routing And Budgeting](roadmap/sprints/model-routing-and-budgeting.md)):** route every call through `execute`. Secrets stay under reserved roots and are read only by trusted adapter code, never through a worker grant. **Partly met by [`grimoire.providers`](PROVIDERS.md):**
  - The authorized payload is an envelope binding the destination URL and the exact body.
  - Handles must be `issued` by the adapter's authorizer.
  - Keys are read only by trusted adapter code, after an allow.
  - **Recorded deviation:** keys come from environment variables, not from the reserved `secrets/` root. The native file store (010) reserves `secrets/` but does not read it yet; until a store-backed secrets reader exists, this deviation stands.

  **Decided for 027 (the requester, Wave 4 audit): the generation runtime holds a trusted `app:` handle.** This is a narrow exception to "code that parses model output uses an untrusted handle", and it holds only while all of these stay true:
  - the destination of every call comes from trusted routing configuration, never from prompt or reply text;
  - model text is payload only: no reply grants, names or widens anything, and the runtime runs no tool or command a reply asks for;
  - the handle is used for provider calls only, so a live scene asks for no per-attempt approval.

  Anything that would act on model output (tools, writes a reply names) still needs its own untrusted handle and approval.
- **Scene runner (035), recorded in the Wave 5 audit.** `SceneRunner` stores generated lines in turn files with the library owner's trusted human handle. This is a second narrow exception to the rule above, extending the 027 decision, and it holds only while all of these stay true:
  - the runner alone chooses what it writes and where: turn files of the open chat, at ids it allocates, never a path, family or record a reply names;
  - model text is stored as payload only (a take's text and usage), never parsed into a grant, a record of another family or an accepted effect;
  - turning a kept line into canon (labelling, 029, 021) goes through those sprints' own checks and authority, never this handle's trust.

  Anything that would turn model output into state beyond the turn file needs its own untrusted handle and approval.
- **Labelling (035 milestone 2), decided by the requester in the Wave 6 audit ("Split them").** The labeller reads model output into proposed 029 labels, so it does not share the runner's trust: its provider calls run on its own untrusted handle (`LabelRuntime` refuses a trusted one), each attempt approved for the human press that asked. A labelling that holds anything read from a reply is written only once the runner's untrusted `label_handle`, issued by the library's authority, is allowed to `propose` that turn's canonical payload at its address. The owner's handle still writes the file, as the narrow exception above, because the runner alone chose the address and the reply can only cite offered facts by number ([SCENES](SCENES.md#labelling-an-action)).
- **Framing and deciding (035 milestone 2), decided by the requester in Wave 6 ("Player confirms").** No model output reaches 029 unseen: the acting player confirms a proposed labelling unedited, and 029 records it as a frame only with that player's own contribution and trusted handle. The adjudication key is the app's own trusted handle, distinct from the labeller's and every player's; the runner uses it to decide only by rule, on the director's (the library owner's) adjudication, or on the player's consent given with their own handle ([SCENES](SCENES.md#confirming-and-deciding-an-action)).
- **Accepting an effect (035 milestone 2), recorded in the Wave 6 effects slice.** No model output is accepted: an effect is proposed as structured 021 operations by the director or the posting player, and accepted only with the outcome key (the effects library's handle, which is the runner's own owner handle) and each acting player character's endorsement of the exact fingerprint. A third narrow exception: an endorsement is checked against that player's own trusted handle when it is given. The runner then has that same handle make a `propose` request at `campaigns/<c>/turns/<turn>.json/endorsements/<contribution>/<fingerprint hex>` (the turn file's own address, then the endorsement) (`grimoire.play.accept.endorsement_address`), so the library's durable authority records it in its `security` audit, before the turn file records the endorsement (the turn write's own `apply` allow flushes it to the ledger first). Its outcome does not matter (a player holds no grant there, so it is normally `not_granted`): what counts is the audit record, with that principal, `trusted` and that exact address, which only a trusted handle of that player could have made. When the director's approval is the last key, or after a restart (030's `adopt`), the runner presents a recorded endorsement to 021 again with a handle it mints for that same player, only when that audit record exists; otherwise it refuses `invalid_endorsement` and the player endorses again (or, for an acceptance cut short, is present with their own trusted handle when the director recovers it). What is trusted is therefore the authority's ledger and the trusted code holding it, not the turn file: an endorsement written into `turns/<turn>.json` by a hand edit or a peer device is never presented. The ledger's own limit stands: with local file access its tail can be forged ([STORAGE](STORAGE.md#reading)), as can every grant and approval; sync never carries `authority/`. An abort of a landed effect still needs every endorser's own live handle ([SCENES](SCENES.md#accepting-an-effect)).
- **Device sync (028):** pairing keys and device credentials live under reserved roots, and peer devices are non-owner principals.
- **Multiplayer (102):** each human approves only uses of grants they issued.
- **V1 migration ([V1 To V2 Import Apply](roadmap/sprints/v1-to-v2-import-apply.md)):** V1 libraries have no address in the V2 namespace. A future read-only migration source must be an explicit, separate root.
- **Card import ([Character Card Ingestion](roadmap/sprints/character-card-ingestion.md)):** the importer is an untrusted worker; this is the first real use of the simulated proving scenario.
- **App commands ([Minimal Text Scene Runner](roadmap/sprints/minimal-text-scene-runner.md)):** every `grimoire.intent` `ui_command` an executor runs is authorized through the same principal and grant path as the equivalent click; a confirmation is never an approval.
- **Process sandboxing** of community automation stays this sprint's moonshot; script-running owners ([Mechanics Module Authoring](roadmap/sprints/mechanics-module-authoring.md)) must not run untrusted code in-process.

## Durable authority (010b)

`grimoire.ledgers.DurableAuthority` rebuilds the kernel `Authorizer` from the library's `security` ledger ([STORAGE](STORAGE.md#authority-ledgers-010b)) and records everything after it.

**What is recorded.** The ledger holds:
- the owner set, at genesis (fixed for the ledger's life in this slice);
- grants and revocations;
- approvals, each with the grant it was pinned to, and their revocations;
- every decision, as its `AuditRecord`.

Replay validates each entry with the kernel, including `replay_decision`'s strict checks, and fails closed (`LedgerInvalid`).

**A grant outlives what it names.** A grant is scoped to an address, so deleting `campaigns/<id>`, or one individual's files, by hand leaves it in force over whatever is later written there. The campaign and actor id allocators therefore never reuse an id the ledger names (chat, relation and event ids rely on `new_id` being unique, and a grant on one of those survives too): a new campaign's id is outside `security_campaign_ids`, and a new actor's outside `security_instance_ids`, part of [`campaign_taken_ids`](CHATS.md#taken-instance-ids). Both scan the batches raw (grant scopes and approval resources, revoked or not), and fail closed when a batch cannot be read.

**Kernel hooks (pure).**
- `attach_sink(sink)`: every decision passes through the sink before it joins the audit and before an approval is consumed. If the sink raises, the request is denied `internal_error` and nothing is consumed.
- `issued(handle)`: whether this authorizer minted the handle.
- `replay_decision(record)`: rebuilds a stored decision, with validation.

**Durable before effect.**
- An allow for `apply` or `execute` is on disk before it is returned.
- Inside a commit's consequential scope, a save's allows are flushed once, before anything is staged.
- Lifecycle changes are validated, applied, and flushed before they return. The durable layer also requires exact `Grant` and `Approval` instances, and grant and approval ids of at most 128 characters; anything else is refused with `ValueError` before any change.
- Other decisions (inspect, propose, denials) are flushed with the next flush, at 500 pending (outside a save, whose decisions all wait for its single flush), or on `close()`; a crash may lose them. Any later consequential allow flushes them first, in order.

**Consumption (requester decision).** An approval is consumed when an allow that uses it is returned. It stays consumed across restarts, even if the save it authorized never lands; the user re-approves. Two refinements:
- **A decision on disk was made, but may not have taken effect.** If a flush fails after its own commit point, recovery may publish the batch. The approval is then consumed on disk although the caller was denied.
- **A failed save flush leaves its approvals pending.** Inside a save's scope, if the single flush fails, the authority is poisoned and nothing is written. After reopening, those approvals are pending again.

**Poisoning.** Any failure to persist poisons the authority. Every handle, including handles issued earlier, is then denied `internal_error`, lifecycle calls raise `AuthorityUnavailable`, and nothing more is written. Reopen to recover; the disk is the truth.

**Threads.** The authority shares the store's re-entrant lock, and the kernel decides, records and consumes under it, so concurrent requests get distinct audit sequences and an approval is spent once. Scope depth is per thread, so another thread's `execute` allow flushes immediately; it waits only while a commit holds the lock.

**`consequential_scope()` is for `LibraryStore` commits only.** Inside it an allow is returned before it is on disk; the store makes that safe by staging nothing until the scope's single flush succeeds. Any other caller would act on an allow that might never be recorded.

**Leaving `with authority:`.** A normal exit, an `Exception` or `SystemExit` flushes and closes. Any other `BaseException`, such as `KeyboardInterrupt`, closes without I/O, and buffered non-consequential decisions are lost, as in a crash.
