# Spec 07 S6 independent brutal review

**Verdict: NON-BLOCKING.** No concrete defect survived the scoped attack pass.

Frozen source: `a04da7a336146d89f2baa6521bb0f9cbc5caeffa`, diff base `1a2d4ab49`. Reviewed the approved S6 plan and spec sections 10/17.5; backend graph family/assembly/error boundaries, frontend unions, model, layout, detail, route controls and drawing interaction owners. This certifies only this review scope, not the combined branch gate.

## Ranked findings

None. The group family consumes effective rosters once, after every existing actor-producing family; it creates no actor nodes. It preserves dual Leader/Member edges with separate IDs, and assembly deduplicates identical IDs. GM-only secrecy remains explicit on the GM graph. Ordinary group failures omit that family; strict identity errors escape the generic softening boundary.

## Independent evidence

Extracted frozen public source with `git archive` into `C:/Users/charl/AppData/Local/Temp/spec07-s6-review-a04da7a`. All backend fixtures use disposable store roots. Reviewer probes are confined to that Temp source; product source and tracked tests were not edited.

Shared Python with `PYTHONPATH=<snapshot>/backend/src`:

`python -m pytest backend/tests/test_s6_independent_review.py backend/tests/test_group_graph.py -q --disable-warnings`

**10 passed, 1 warning in 6.04s.** Three independent probes:

- Write invalid UTF-8 bytes into the actual group file while a scene names Mara: graph retains Mara/scene and reports exactly `groups` omitted, without a group node.
- Duplicate Mara in Members, also make Mara Leader, and include absent Winifred: graph has exactly two distinct role edges, no Winifred actor, and every edge has existing endpoints.
- Inject an actor only at the plot-family return seam, with no earlier actor family: group edge exists. This is an assembly-order probe, not a separate proof of plot involvement extraction.

Focused existing controls additionally cover birthday-only actors, inherited-detached versus owned-authored affiliation, GM-only display, one index call outside the campaign hold, and cold/warm strict failures for both ledgers.

UI used the existing dependency directory via a Temp junction:

`vitest run src/components/storyGraph/groups.test.tsx --root frontend`

**6 passed in 1.91s**, including two independent probes:

- Campaign `saltmarch#1` and group `salt-circle#1` produce `/campaigns/saltmarch%231/world/groups/salt-circle%231` from the detail link; neither fragment truncation nor scope substitution occurs.
- All four Actors/Groups toggle combinations on both play/calendar axes preserve closed edge endpoints and place each visible node exactly once.

Existing focused controls pin duplicate-label ordering below locations, independent role chips, marked GM-only secrecy, Cast defaults, and Groups-off behavior. Keyboard access is structurally shared: group drawing and role chips are ordinary buttons using existing selection callbacks; the generated Show controls enumerate `SHOW_LABELS`. No group-specific key handler was added.

## Limits

No full graph/view suite, keyboard browser simulation, full `make check`, performance benchmark, native Codex gate or later-slice changes were independently run here. Existing typed/lint gate results were supplied by the implementation owner, not rerun; optional code-doctor analysis was not run. Native identity semantics remain delegated to the already-reviewed inverse rather than reconstructed in the graph. Read-only review inspected those callers but did not repeat their entire native-filesystem suite. No private store or frozen campaign fixture was read or changed. Artifact is uncommitted.
