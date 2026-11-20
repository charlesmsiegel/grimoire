# Location topology (059)

`grimoire.topology` and `grimoire.topologystore` implement the headless first useful
slice: persistent source-qualified places, containment, aliases, directed or
bidirectional paths, conditional traversal, and campaign-local path replacements.
Scene staging is separate and is never promoted to world canon by this service.

## Canonical ownership

A `Topology` is a pinned `SourceRef` with kind `topology`, stored as a generic
native definition envelope. Its `grimoire.topology/1` payload references existing
pinned place definitions; it does not copy their authored contents. Display names
come from those definitions. A place can have topology-specific aliases and one
parent. Containment is acyclic and does not imply a traversable road.

Paths have IDs local to this exact topology revision. Identity is the graph ref
plus path ID, never the label. Direction defaults to one-way; `bidirectional=True`
explicitly permits both directions. All `requires` tokens must occur in the
caller's explicit condition tuple. Blocked paths are never traversed. There are
no executable predicates, inferred conditions, model calls or travel costs.

`publish(graph, requested_by=DIRECTOR)` returns a change whose `preview()` contains
the exact proposed bytes. `commit()` uses the supplied native-store authorization
handle. Publication is create-only: use a new pinned revision to author a new
source graph. Native source manifests must already exist. Before publishing a
second revision, author an explicit manifest head using the existing source
writer; this service never silently moves a head. Source entity IDs share the
native namespace across kinds.

A campaign override is one deterministic generic instance envelope, with kind
`topology` and ID `topology-<full SHA256 of the pinned graph ref>`. It is
configuration without actor facets, not a character or a new chat participant.
The payload schema is `grimoire.topology-overlay/1`. Both its envelope and payload
retain the exact source graph pin and etag. A campaign must have a canonical scope
record. A cross-kind ID collision is refused rather than allocating another ID.
Only the deterministic address owns these overrides; unrelated files are never
picked by name or enumeration.

## Use from a trusted authoring service

```python
from dataclasses import replace
from grimoire.core import DIRECTOR
from grimoire.topologystore import TopologyLibrary

library = TopologyLibrary(store, authorized_handle)
view = library.view(graph_ref, campaign_id="campaign-a", requested_by=DIRECTOR)
route = view.index.route(start_ref, destination_ref, conditions=("ticket",),
                         requested_by=DIRECTOR)
# `road` is the explicitly selected Path value from this graph, not a name lookup.
change = library.change_path(
    graph_ref, "campaign-a", replace(road, blocked=True),
    operation_id="block-road-1", provenance="human:owner",
    expected=view.overlay_etag, requested_by=DIRECTOR,
)
preview = change.preview()
# Present the preview to the owner and pass any required store approval IDs.
receipt = change.commit(approvals)
```

An override replaces one existing path completely (endpoints, direction,
conditions and blocked status). Its endpoints must remain places in the graph.
Restore by submitting the original source path under a new operation ID. Other
campaigns and the source bytes stay unchanged. The ordered operation receipts
remain in the campaign file. Retrying identical ID/body after reopen or later
edits returns `replayed=True` without applying it again; reusing an ID with a
different body refuses. New operations require the current overlay etag (`None`
for creation). Dependencies and scope are checked again at commit under the
native store lock. Native authorization, journal recovery and V1 path guards
remain in force. This lock does not prevent a separate external editor writing
between validation and commit; callers must keep the library quiescent from
external editors, as with the underlying store.

## Queries and failures

Every query and persistence entry point requires `requested_by=DIRECTOR`. This is
an explicit perspective guard, not authentication: the caller must be trusted,
and actual writes still require native-store authorization. Do not pass this
index to an NPC or treat a reachable place as perceived or known.

- `matches(label)` case-folds names and aliases and returns **all** exact qualified
  candidates. It never resolves an ambiguous short name. Alias candidates can
  include missing places; inspect `index.missing` before navigation.
- `ancestors(ref)` and `children(ref)` query hierarchy. A missing ancestor refuses;
  missing children are omitted from the returned available places.
- `neighbors(ref, conditions=...)` returns `(path_id, destination_ref)` pairs.
- `reach(...)` returns reached references with shortest-hop routes and an explicit
  `truncated` flag. `route(...)` returns a route, or `None` only after an exhaustive
  search finds none; bounded failure raises `TopologyError("search_limit")`.
- Queries require explicit pinned references. Missing endpoints refuse, including
  start-to-self queries. Missing or excluded imported definitions appear in
  `index.missing`; routes never substitute a same-named definition from another
  source. Missing/changed source graphs and malformed/unknown overlays refuse
  instead of silently restoring source roads.

Bounds are 256 places, 1,024 paths, 64 aliases/condition tokens per value, and
1,024 retained campaign operations. Query defaults are depth 32 and 4,096 edge
steps, with maxima 256 and 4,096. Exhausted operation capacity refuses new writes;
receipts are not evicted. Traversal order is deterministic by path ID. Unknown
payload fields/schemas refuse interpretation and preserve bytes; unknown native
envelope metadata survives supported updates.

## Consumer boundaries and evidence

The four-place save/reopen, blocked-road, same-name imported-town, dependency,
authority and retry proofs are in `test_topology.py`, `test_topologystore.py`,
`test_topology_chat.py` and `test_topologystore_recovery.py`. Recovery tests inject
process crashes after journal publication and record replacement, plus a record
replacement I/O failure, then reopen and retry. They do not simulate power loss.

Generic native sync already transfers definition/instance bytes and preserves
conflicts. This slice adds no semantic topology merge, dependency repair or
source rebase. A receiver with a mismatched graph pin/etag fails closed.

Maps (097), travel adjudication, campaign clocks and Wave 8 UI exit (115) consume
these contracts separately. Player-safe map projection requires its own knowledge
policy. No UI, character movement, elapsed time, evidence or scene history is
written here. Weighted routes and hazards remain the later third milestone;
this first slice uses shortest hop count only. Removing this implementation
requires no migration: canonical JSON remains on disk, and older clients should
leave these unfamiliar records untouched.
