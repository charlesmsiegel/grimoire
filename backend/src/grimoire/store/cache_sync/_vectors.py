"""Shared vector projections, one client and the bounded vector warm-up hook."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from ... import embeddings
from ...llm_errors import LLMError
from .. import compiled, embed_space, semsearch, vectors
from ..context import art, layout, semantic
from ..inference import embed
from . import _contracts, _paths, _signals

# -- the `vectors` hook: the three existing producers (section 6.4) ------------


@dataclass(frozen=True)
class Projection:
    """One producer's embedded projection: the embed task it spends under,
    whether write-through may spend on it, the producer's own text function
    (`texts(path, data, instance)`, unit-indexed, None when the row no longer
    serves anyone), its lazy limit per path, and whether it embeds a file in
    units (one row per unit, `{"unit": n}`)."""

    name: str
    task: str
    on_write: Literal["quiet", "explicit"]
    texts: Callable[[Path, bytes, object], list[str] | None]
    limit: int
    units: bool = False
    #: Whether the producer is switched on now. Write-through spends only
    #: where the next ordinary read would embed the same text anyway, so a
    #: producer switched off is deferred in `write` mode; an explicit sync is
    #: the user's own request and asks nothing of it.
    enabled: Callable[[], bool] = lambda: True


def _recall_on() -> bool:
    return semantic.settings() is not None


def _art_on() -> bool:
    """The catalog spends only where a turn would build it: a depth above
    zero and the `available_art` section switched on in the prompt layout,
    which is the layer's off switch (`assemble._section_on`)."""
    return art.settings()["depth"] > 0 and bool(layout.enabled_ids(("available_art",)))


#: The producers' projections. Recall and art run on every turn of a campaign
#: that has them on and embed any candidate whose text is missing, so warming
#: them after a quiet period moves spend earlier and adds none (`quiet`).
#: Library search spends only when a reader searches, so only an explicit sync
#: rebuilds it (`explicit`).
VECTOR_PROJECTIONS: dict[str, Projection] = {
    semantic.PROJECTION: Projection(semantic.PROJECTION, "semantic-recall", "quiet",
                                    semantic.projection_texts, semantic.WARM_LIMIT,
                                    enabled=_recall_on),
    art.PROJECTION: Projection(art.PROJECTION, "art-catalog", "quiet",
                               art.projection_texts, embeddings.BATCH - 1, enabled=_art_on),
    semsearch.PROJECTION: Projection(semsearch.PROJECTION, "semantic-search", "explicit",
                                     semsearch.projection_texts, semsearch.WARM_LIMIT, units=True),
}

#: One process-wide sync client, for its connection pool -- the producers'
#: own pattern.
_CLIENT = embeddings.EmbeddingsClient()


def _enabled(projection: Projection) -> bool:
    try:
        return projection.enabled()
    except Exception:                                     # noqa: BLE001 -- unreadable settings spend nothing
        return False


def _unit_order(row: compiled.Materialized) -> tuple:
    """Rows in unit order: `{"unit": 10}` after `{"unit": 9}`."""
    try:
        unit = json.loads(row.instance).get("unit") if row.instance else None
    except (ValueError, AttributeError):
        unit = None
    return (row.kind, unit if type(unit) is int else -1, row.instance)


def _vector_kind(kind: str) -> tuple[Projection, str] | None:
    """`(projection, space digest)` for a `vector:<projection>:<digest>` kind."""
    head, _, rest = kind.partition(":")
    name, _, digest = rest.partition(":")
    projection = VECTOR_PROJECTIONS.get(name)
    if head != "vector" or projection is None or not digest:
        return None
    return projection, digest


@dataclass(frozen=True)
class _VectorSource:
    """One hot row against the bytes this batch read, before any projection.

    The path comes from the pinned root, not the old history row's path (a
    stated rename may have copied it). Keep this snapshot through the reply:
    a later edit must never vouch for text made from these earlier bytes.
    """

    rel: str
    path: Path
    row: compiled.Materialized
    projection: Projection
    digest: str
    data: bytes


@dataclass
class _Wanted:
    """One text the batch would embed: who pays (one campaign, or "" for
    shared or library-wide text), the rows it replaces, and the bytes their
    `materialized` rows are rewritten from once it lands."""

    task: str
    claims: set[str] = field(default_factory=set)
    rows: list[tuple[_VectorSource, object]] = field(default_factory=list)
    #: The projection whose task this text is sent under (`_wanted`).
    projection: str = ""


def _wanted(plan: dict[str, _Wanted], text: str, projection: Projection) -> _Wanted:
    """`plan`'s entry for `text`. The vector cache is shared, so a text two
    producers want is sent once, as the lazy path would (whichever producer
    read first paid, the other then hit); it is sent under the task of the
    producer first in `VECTOR_PROJECTIONS`, so the attribution never depends
    on the order the batch met its paths."""
    wanted = plan.setdefault(text, _Wanted(projection.task, projection=projection.name))
    order = list(VECTOR_PROJECTIONS)
    if order.index(projection.name) < order.index(wanted.projection or projection.name):
        wanted.task, wanted.projection = projection.task, projection.name
    return wanted


class _VectorsHook:
    """Re-embed, for each hot vector row, the next version of the unit it
    names -- only in the batch's space, only text the vector cache lacks, and
    no more units per path than the producer would embed lazily."""

    name = "vectors"
    on_write: Literal["quiet", "explicit"] = "quiet"

    def owns(self, kind: str) -> bool:
        return _vector_kind(kind) is not None

    def remap(self, kind: str, instance: str, old: str, new: str) -> object:
        """A stated rename: recall's instance names its entity as
        `[kind, id]`, which are the file's directory and stem
        (`<root>/<kind>/<id>.md`), and a campaign-layer file's campaign is in
        its path, so all three follow the move; a unit position is
        independent of the path."""
        value = json.loads(instance) if instance else None
        if not isinstance(value, dict):
            return instance
        entity = value.get("entity")
        before, after = PurePosixPath(old), PurePosixPath(new)
        if not (isinstance(entity, list) and len(entity) == 2
                and entity == [before.parent.name, before.stem]):
            return value
        moved = {**value, "entity": [after.parent.name, after.stem]}
        # A campaign-layer file is read by its own campaign alone, so a move
        # into one (from a world, which every campaign of it reads, or from
        # another campaign) names the destination; the copy coalesces the
        # rows that then coincide.
        if (destination := _paths._campaign_named(after)) is not None:
            moved["campaign"] = destination
        return moved

    def local(self, ctx: _contracts.WarmContext,
              hot: Mapping[str, tuple[compiled.Materialized, ...]]) -> _contracts.LocalResult:
        result = _contracts.LocalResult()
        plan: dict[str, _Wanted] = {}
        for rel, rows in hot.items():
            if ctx.halted():
                break
            try:
                data = ctx.path(rel).read_bytes()
            except OSError as exc:
                for row in rows:
                    result.outcomes[(rel, row.kind)] = f"failed:{type(exc).__name__}"
                continue
            self._path(ctx, rel, rows, data, plan, result)
        result.plan = plan or None
        return result

    def _path(self, ctx: _contracts.WarmContext, rel: str, rows: tuple[compiled.Materialized, ...],
              data: bytes, plan: dict[str, _Wanted], result: _contracts.LocalResult) -> None:
        path = ctx.path(rel)
        spent: dict[str, int] = {}
        for row in sorted(rows, key=_unit_order):
            parsed = _vector_kind(row.kind)
            assert parsed is not None
            projection, digest = parsed
            source = _VectorSource(rel, path, row, projection, digest, data)
            outcome = self._row(ctx, source, plan, spent, result)
            result.outcomes[(rel, row.kind)] = _contracts._worse(result.outcomes.get((rel, row.kind)), outcome) \
                if (rel, row.kind) in result.outcomes else outcome

    def _row(self, ctx: _contracts.WarmContext, source: _VectorSource, plan: dict[str, _Wanted],
             spent: dict[str, int], result: _contracts.LocalResult) -> str:
        projection = source.projection
        if ctx.mode == "write" and (projection.on_write == "explicit" or not _enabled(projection)):
            return "deferred"
        if (problem := self._space_problem(ctx, source.digest)) is not None:
            result.embedding[problem] = result.embedding.get(problem, 0) + 1
            return "skipped"
        try:
            instance = _contracts._instance(source.row)
            texts = projection.texts(source.path, source.data, instance)
        except Exception as exc:                          # noqa: BLE001 -- the reader's failure
            return f"failed:{type(exc).__name__}"
        text = self._unit(projection, texts, instance)
        if text is None:
            return "skipped"
        assert ctx.space is not None                      # `_space_problem` said so
        space_id = ctx.space["space"]
        written = compiled.WrittenInput("source", source.path, source.data)
        if vectors.load(space_id, [text]):
            if not ctx.dry_run:
                # Held already: only its row moves to the current bytes, so a
                # scoped sync can prove this file current next time.
                vectors.load(space_id, [text], sources={text: written}, projection=projection.name,
                             instances={text: instance})
            result.embedding["held"] = result.embedding.get("held", 0) + 1
            return "hit"
        # The producer's own lazy limit, per path, counted in texts it would
        # SEND: a rebuild never sends more for one file than one ordinary read
        # could, and a held text costs nothing.
        if not self._within_limit(projection, text, plan, spent):
            return "lazy"
        return self._plan(ctx, source, text, instance, plan, result)

    @staticmethod
    def _plan(ctx: _contracts.WarmContext, source: _VectorSource, text: str, instance: object,
              plan: dict[str, _Wanted], result: _contracts.LocalResult) -> str:
        """A text the cache lacks, within the limit: deferred with embeddings
        off, counted once in a dry run, otherwise planned with its claim."""
        if not ctx.embedding:                             # a dry run previews this too
            result.embedding["deferred"] = result.embedding.get("deferred", 0) + 1
            return "deferred"
        if ctx.dry_run:
            # Coalesced as a real run would: a text two rows want counts once.
            if text not in plan:
                _wanted(plan, text, source.projection)
                result.embedding["would_send"] = result.embedding.get("would_send", 0) + 1
            return "planned"
        wanted = _wanted(plan, text, source.projection)
        campaign = instance.get("campaign", "") if isinstance(instance, dict) else ""
        wanted.claims.add(campaign if isinstance(campaign, str) else "")
        wanted.rows.append((source, instance))
        return "planned"

    @staticmethod
    def _within_limit(projection: Projection, text: str, plan: Mapping[str, _Wanted],
                      spent: dict[str, int]) -> bool:
        """Whether one more distinct text may be planned for this path under
        the producer's lazy limit; a text already planned costs nothing more."""
        if text in plan:
            return True
        if spent.get(projection.name, 0) >= projection.limit:
            return False
        spent[projection.name] = spent.get(projection.name, 0) + 1
        return True

    @staticmethod
    def _space_problem(ctx: _contracts.WarmContext, digest: str) -> str | None:
        """Section 6.5 step 1: a row embedded in a space the role has moved
        away from is `stale_space` -- re-embedding it would be embedding
        content never embedded there -- and with embedding off it is
        `embedding_off`. Either way nothing is sent."""
        if ctx.space is None:
            return "embedding_off"
        if digest != ctx.space_digest:
            return "stale_space"
        return None

    @staticmethod
    def _unit(projection: Projection, texts: list[str] | None, instance: object) -> str | None:
        """The text that replaces the one unit this row says was embedded."""
        if not texts:
            return None
        if not projection.units:
            return texts[0]
        unit = instance.get("unit") if isinstance(instance, dict) else None
        if type(unit) is not int or not 0 <= unit < len(texts):
            return None
        return texts[unit]

    def network(self, ctx: _contracts.WarmContext, plan: object) -> _contracts.NetworkResult:
        result = _contracts.NetworkResult()
        assert isinstance(plan, dict) and ctx.space is not None
        space = ctx.space
        # The role may have moved since the local phase compared rows with
        # the batch's space: sending now would put texts into a space nothing
        # was ever embedded in, which is the move `confirm_embedding` asks
        # about. The texts go with the batch's space, never the re-read one.
        if self._role_moved(ctx):
            self._settle(result, plan, plan, "skipped")
            result.embedding["stale_space"] = len(plan)
            return result
        by_task: dict[str, list[tuple[str, str]]] = {}
        for text, wanted in plan.items():
            claims = wanted.claims if len(wanted.claims) == 1 else {""}
            by_task.setdefault(wanted.task, []).extend((c, text) for c in sorted(claims))
        failure = ""
        for task, claims in by_task.items():
            for group in embed.attribute(claims):
                for start in range(0, len(group.texts), embeddings.BATCH):
                    chunk = list(group.texts[start:start + embeddings.BATCH])
                    chunk = self._ready(ctx, space["space"], chunk, plan, result, failure)
                    if chunk:
                        got, went = _embed_chunk(task, embed.EmbedGroup(group.campaign, texts=tuple(chunk)),
                                                 space)
                        failure = self._after(ctx, space["space"], chunk, got, went, plan, result)
        result.failure = "" if failure == _signals._MOVED else failure
        if ctx.verify:
            self._verify(space["space"], plan, result)
        return result

    def _ready(self, ctx: _contracts.WarmContext, space_id: str, chunk: list[str], plan: dict[str, _Wanted],
               result: _contracts.NetworkResult, failure: str) -> list[str]:
        """The part of one chunk to send: nothing after a failure or a stop
        (counted unsent -- a failure names its kind on every row it stopped,
        a stop leaves them `planned`, which the batch reports `unsynced`), and
        never a text that arrived meanwhile."""
        if failure or ctx.halted():
            result.embedding["not_sent"] = result.embedding.get("not_sent", 0) + len(chunk)
            if failure and failure != _signals._MOVED:
                self._settle(result, plan, chunk, f"failed:{failure}")
            return []
        return self._arrived(space_id, chunk, plan, result)

    def _after(self, ctx: _contracts.WarmContext, space_id: str, chunk: list[str], got: embed.GroupResult,
               went: bool, plan: dict[str, _Wanted], result: _contracts.NetworkResult) -> str:
        """A sent chunk's reply. The root may have moved while the request was
        out: `vectors.save` resolves the root again, so a reply saved now
        would land in the other library. Dropped; its rows stay `planned`,
        so `unsynced`. Returns the failure that stops the rest, or ""."""
        if _signals._root_moved(ctx.root):
            return _signals._MOVED
        return self._landed(space_id, chunk, got, went, plan, result)

    @staticmethod
    def _role_moved(ctx: _contracts.WarmContext) -> bool:
        """Whether the Embedding role names a different space now than the
        one the batch read -- or none, or cannot be read."""
        try:
            now = embed_space.endpoint()
        except Exception:                                 # noqa: BLE001 -- unreadable is not "the same"
            return True
        return now is None or compiled.space_digest(now["space"]) != ctx.space_digest

    def _verify(self, space_id: str, plan: dict[str, _Wanted], result: _contracts.NetworkResult) -> None:
        """Section 7.7 for a vector kind: every text this batch reports built
        now has a vector."""
        built = [text for text, wanted in plan.items()
                 if any(result.outcomes.get((source.rel, source.row.kind)) == "built"
                        for source, _instance in wanted.rows)]
        held = vectors.load(space_id, built) if built else {}
        for text in built:
            if text not in held:
                result.verify_failed.update(source.rel for source, _instance in plan[text].rows)
                _signals._log.warning("cache sync verify: a built vector is missing")

    def _arrived(self, space_id: str, chunk: list[str], plan: dict[str, _Wanted],
                 result: _contracts.NetworkResult) -> list[str]:
        """Checked again just before sending: texts that arrived meanwhile
        (another process, a turn) are recorded and dropped from the chunk."""
        arrived = vectors.load(space_id, chunk)
        for text in arrived:
            self._save(space_id, text, None, plan[text])
            self._settle(result, plan, [text], "hit")
        return [text for text in chunk if text not in arrived]

    def _landed(self, space_id: str, chunk: list[str], got: embed.GroupResult, went: bool,
                plan: dict[str, _Wanted], result: _contracts.NetworkResult) -> str:
        """Save a chunk's vectors as they land. Returns the failure that stops
        the run, or "". `went` says whether a request went out, which is what
        `requests` and `sent` count -- a failed one included, since it filed a
        ledger row and may have cost money."""
        if went:
            result.embedding["requests"] = result.embedding.get("requests", 0) + 1
            result.embedding["sent"] = result.embedding.get("sent", 0) + len(chunk)
        else:
            result.embedding["not_sent"] = result.embedding.get("not_sent", 0) + len(chunk)
        if got.vectors is None or len(got.vectors) != len(chunk):
            kind = got.error or "bad_response"
            self._settle(result, plan, chunk, f"failed:{kind}")
            # A failure of the input (`bad_response`) costs this chunk; any
            # other -- auth, a missing key, a rate limit, the network, the
            # deadline -- is the endpoint's, and stops the rest.
            return "" if kind == "bad_response" else kind
        for text, raw in zip(chunk, got.vectors, strict=True):
            self._save(space_id, text, raw, plan[text])
        # `vectors.save` drops a write it cannot make (a full or read-only
        # cache) rather than raise: a text is `built` only once it reads back.
        held = vectors.load(space_id, chunk)
        for text in chunk:
            self._settle(result, plan, [text], "built" if text in held else "failed:not_saved")
        return ""

    @staticmethod
    def _save(space_id: str, text: str, raw: list[float] | None, wanted: _Wanted) -> None:
        """Store the vector, and rewrite each row it replaces against the
        bytes it was made from (`built_from`)."""
        for source, instance in wanted.rows:
            written = compiled.WrittenInput("source", source.path, source.data)
            if raw is not None:
                vectors.save(space_id, text, raw, source=written, projection=source.projection.name,
                             instance=instance)
            else:
                vectors.load(space_id, [text], sources={text: written}, projection=source.projection.name,
                             instances={text: instance})

    @staticmethod
    def _settle(result: _contracts.NetworkResult, plan: Mapping[str, _Wanted], texts: Iterable[str],
                outcome: str) -> None:
        for text in texts:
            for source, _instance in plan[text].rows:
                key = (source.rel, source.row.kind)
                result.outcomes[key] = _contracts._worse(result.outcomes.get(key), outcome) \
                    if key in result.outcomes else outcome


def _embed_chunk(task: str, group: embed.EmbedGroup, space: dict) -> tuple[embed.GroupResult, bool]:
    """One chunk through the grouped door (01h-C5): one ledger row, charged
    to the group's campaign, never a request spanning campaigns, documents
    only, on the client's own per-request timeout. The task is spelled as a
    literal per producer, so the operation guard can read it.

    Also answers whether a request went out: the door says `not_sent` for a
    group it never sent, and every other result it returns, failed or not,
    is a call that sent and filed its row. A failure the door did not turn
    into a result (an error raised before it sent) is the chunk's failure,
    by kind, and sent nothing."""
    try:
        got = _embed_chunk_literal(task, group, space)
    except LLMError as exc:
        return embed.GroupResult(None, exc.kind), False
    except OSError:
        return embed.GroupResult(None, "network"), False
    return got, got.error != embed.GROUP_NOT_SENT


def _embed_chunk_literal(task: str, group: embed.EmbedGroup, space: dict) -> embed.GroupResult:
    if task == "semantic-recall":
        (got,) = embed.embed_groups_sync("semantic-recall", [group], space=space, client=_CLIENT)
    elif task == "art-catalog":
        (got,) = embed.embed_groups_sync("art-catalog", [group], space=space, client=_CLIENT)
    elif task == "semantic-search":
        (got,) = embed.embed_groups_sync("semantic-search", [group], space=space, client=_CLIENT)
    else:
        raise ValueError(f"{task!r} is no vector producer's task")
    return got


VECTORS = _VectorsHook()
