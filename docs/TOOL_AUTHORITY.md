# Tool authority and untrusted content

**Status:** first slice of [Safe Tool Execution](roadmap/sprints/safe-tool-execution.md) (Wave 1, #007). `grimoire.security` is a pure, in-memory authorization kernel. Its proving scenario is **simulated** in `backend/tests/test_security_card_import.py` with a synthetic, deliberately obedient worker; there is no importer, file store, provider adapter or approval UI yet, and green tests are not an app-integrated proof. This page records the accepted *behavioral* contract. The dataclass shapes are not a persisted schema.

## Threat model

Imported Markdown, character cards (including extensions and greetings), rules scripts and every worker input, including model output, are **untrusted data**. They can contain prompt injection designed to make a worker read provider secrets, write into another world or campaign, traverse paths, call external tools or forge consent. V2 does not try to detect injection. It makes authority structurally independent of content: whatever text a worker reads, the most it can do is what a human already granted. Applying or executing additionally needs that human's approval of the exact bytes.

## Trust architecture

- Trusted application code owns the `Authorizer`: grants, approvals, policy and audit.
- Each worker gets only a `WorkerHandle` from `authorizer.handle_for(principal, trusted=False)`. The handle fixes the principal and origin and exposes only `authorize(permission, resource, payload=None, approval_id=None)`. Its API offers no way to add grants, register approvals, pick an identity or claim trust, and it refuses attribute writes. Arguments must be exact `str`/`bytes` instances; subclasses that could override hashing, equality or methods are `malformed_request`.
- Only `human:` and `app:` principals may hold a trusted handle. **Any code path that parses imported content or model output must use an untrusted handle, whatever its principal namespace.** For example, an `app:` importer must not be trusted.
- An `Authorizer` and its handles are single-threaded; callers that share one across threads must serialize access (approval consumption is not locked).
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
- **Approval UI:** issue approvals only from a direct human action. Show `describe()` text, including the non-ASCII flag and the staged content the digest refers to.
- **Durable audit:** persist decisions and also audit grant/approval lifecycle, which this slice exposes only through `describe_policy()`.
- **Provider adapters and network:** route every call through `execute`. Secrets stay under reserved roots and are read only by trusted adapter code, never through a worker grant.
- **Device sync (028):** pairing keys and device credentials live under reserved roots, and peer devices are non-owner principals.
- **Multiplayer (102):** each human approves only uses of grants they issued.
- **V1 migration:** V1 libraries have no address in the V2 namespace. A future read-only migration source must be an explicit, separate root.
