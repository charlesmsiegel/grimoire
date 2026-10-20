"""Per-file picture, crop and caption overlay, including shadowed world images."""

from __future__ import annotations

import filecmp
from pathlib import Path

from .. import assets, characters, image_descriptions, image_refs, image_store, locks
from . import _actors, _identity, _ledgers, _record_paths, _views


def list_images(cid: str, aid: str, vid: str, base: str = "characters",
                *, v: _views.View | None = None) -> list[dict]:
    mine, inherited = _image_halves(cid, aid, vid, base, _views._view(cid, v))
    return sorted(mine + inherited, key=lambda i: i["name"])


def _image_halves(cid: str, aid: str, vid: str, base: str,
                  v: _views.View) -> tuple[list[dict], list[dict]]:
    """`list_images`' two halves, unsorted: the campaign's own rows, and the
    world rows it inherits (none when the record is detached -- the world's
    id-mate is a stranger -- and never a tombstoned one).

    Split for the description reads, which answer the two halves by different
    rules and must not list (or scan the placements of) either side twice."""
    mine = assets.list_images(v.croot, aid, vid, base)
    if _record_paths.flat_ref(base, aid) in v.off:
        return mine, []
    have = {i["name"] for i in mine}
    inherited = [i for i in assets.list_images(v.wroot, aid, vid, base)
                 if i["name"] not in have and _record_paths.asset_ref(base, aid, vid, i["name"]) not in v.gone]
    return mine, inherited


def _row_ids(rows: list[dict]) -> dict[str, str]:
    """name -> image id for the image-bearing placements among listing rows."""
    return {i["name"]: i["image_id"] for i in rows if i.get("image_id")}


def image_root(cid: str, aid: str, vid: str, name: str, base: str = "characters",
               *, v: _views.View | None = None) -> Path:
    v = _views._view(cid, v)
    croot = v.croot
    if assets.image_path(croot, aid, vid, name, base) is not None:
        return croot
    # A per-asset tombstone or a whole-record tombstone (the record was deleted
    # campaign-side; only its <base>/<aid> ref is written) both hide the image:
    # return croot so the serve route 404s instead of falling through to the world.
    if (_record_paths.asset_ref(base, aid, vid, name) in v.gone or _record_paths.flat_ref(base, aid) in v.gone
            or _record_paths.flat_ref(base, aid) in v.off):
        return croot
    return v.wroot


def avatar_v(cid: str, aid: str, vid: str, base: str = "characters",
             *, v: _views.View | None = None) -> str | None:
    """The `?v=` token of the avatar bytes the campaign's image route serves
    for this actor version, or None when it serves none.

    `image_root`'s rule, read off a listing rather than a path probe: the
    campaign's own avatar when it holds one; else nothing when the campaign
    has tombstoned the image or the record, or detached the record; else the
    world's. A token taken from the other side would name bytes the route does
    not return, and `?v=` is served immutable -- a year of the wrong portrait.

    Characters read each side's memoized `characters.version_facts`, so the
    appearance roster -- every actor a campaign has ever seen -- costs a few
    stats per row once warm instead of a folder scan per root. Asked with the
    `crop` each side's listing asks with (`_patch_char_item`), so the roster
    and the character list warm one entry apiece rather than two. PCs list
    the folder: a roster holds a handful of them.
    """
    v = _views._view(cid, v)

    def token(root: Path, crop: bool) -> str | None:
        if base == "characters":
            try:
                return characters.version_facts(root, aid, vid, crop=crop)["avatar_v"]
            except characters.CharacterNotFound:    # an id no resolver accepts
                return None
        return next((i["v"] for i in assets.list_images(root, aid, vid, base)
                     if i["name"] == assets.AVATAR), None)

    mine = token(v.croot, True)
    if mine is not None:
        return mine
    if (_record_paths.asset_ref(base, aid, vid, assets.AVATAR) in v.gone or _record_paths.flat_ref(base, aid) in v.gone
            or _record_paths.flat_ref(base, aid) in v.off):
        return None
    return token(v.wroot, False)


def read_focus(cid: str, aid: str, vid: str, base: str = "characters",
               *, v: _views.View | None = None) -> int | None:
    """The avatar crop, campaign-first: the campaign answers whenever it owns
    an avatar, a crop record of its own (`assets.owns_focus`: an avatar
    placement -- including the image-less occurrence override set on an
    inherited avatar -- or a `focus.json`), a tombstone over the world's
    avatar, or is detached; otherwise the world does. `_patch_char_item`
    restates this rule over memoized facts and must agree with it."""
    v = _views._view(cid, v)
    croot = v.croot
    if (assets.image_path(croot, aid, vid, assets.AVATAR, base) is not None
            or assets.owns_focus(croot, aid, vid, base)
            or _record_paths.asset_ref(base, aid, vid, assets.AVATAR) in v.gone
            or _record_paths.flat_ref(base, aid) in v.off):
        return assets.read_focus(croot, aid, vid, base)
    return assets.read_focus(v.wroot, aid, vid, base)


def read_description(cid: str, aid: str, vid: str, name: str,
                     base: str = "characters", *, v: _views.View | None = None) -> str:
    """One image's description, resolved campaign-first.

    The rule is per IMAGE, where `read_focus`' is per folder, because that is
    the granularity of the thing: a description is a claim about particular
    bytes. Within one directory, `image_descriptions`' R1 answers: a string
    legacy key there wins, else the text on the image object behind the
    placement.

    - **The campaign holds the image.** R1 in the campaign's directory, and
      there is NO fallback to the world's. A campaign-side `gallery_1` is
      different art from the world's `gallery_1`, so inheriting the world's
      sentence about it would caption one picture with a description of
      another -- which, since the caption becomes alt text in a transcript, is
      worse than saying nothing.
    - **The image is inherited.** The campaign's own legacy key answers if it
      holds a string (written before stage 2, it masks the shared text until
      migration), and R1 in the world's directory otherwise -- which is where a
      campaign edit of inherited art now lands (`set_description`, D1).
    - **Tombstoned or detached.** Campaign-side only, matching `read_focus` and
      `image_root`: the world's id-mate is a stranger. Such a name is not in
      the union, and a name the union does not show has no description.

    The ids come from the overlay listing, so no placement is read twice.
    """
    v = _views._view(cid, v)
    mine, inherited = _image_halves(cid, aid, vid, base, v)
    own = next((i for i in mine if i["name"] == name), None)
    if own is not None:
        return image_descriptions.text_in(assets.version_dir(v.croot, aid, vid, base), name,
                                          known_id=own.get("image_id")) or ""
    theirs = next((i for i in inherited if i["name"] == name), None)
    if theirs is None:
        return ""           # not in the union (an unsafe id lists nothing either)
    cdir = assets.version_dir(v.croot, aid, vid, base)
    legacy = image_descriptions.legacy_text_in(cdir, name)
    if legacy is not None:
        return legacy
    return image_descriptions.text_in(assets.version_dir(v.wroot, aid, vid, base), name,
                                      known_id=theirs.get("image_id")) or ""


def read_descriptions(cid: str, aid: str, vid: str, base: str = "characters",
                      *, v: _views.View | None = None) -> dict[str, str]:
    """`read_descriptions_listed`' descriptions, for a caller that wants no rows."""
    return read_descriptions_listed(cid, aid, vid, base, v=v)[0]


def read_descriptions_listed(cid: str, aid: str, vid: str, base: str = "characters",
                             *, v: _views.View | None = None) -> tuple[dict[str, str], list[dict]]:
    """Every visible image's description for one version, by `read_description`'s
    per-image rule — but resolving the whole version in one pass.

    Written as a sweep rather than a loop over `read_description` because the
    loop was quadratic and it showed: each call re-listed the version's images
    and re-read both sidecars, so a thirty-image character cost ~44ms of pure
    directory scanning. This runs once per cast member per TURN, on the
    synchronous path that blocks the event loop, so that was the difference
    between a rounding error and a fifth of a second of stat() calls per reply.
    So it lists each side once and hands the listing's ids along: no placement
    is read that the listing did not already read, and no blob is resolved.

    One entry per image the overlay lists; an image nobody has reviewed is
    ABSENT rather than empty, so a caller can still tell "not reviewed" from
    "reviewed, nothing to say".

    Returns the merged listing rows beside the text -- exactly what
    `list_images` returns, off the same two halves -- so a caller that wants
    both (the per-turn art catalogue, `base_versions`) reads each placement
    once instead of listing the version and then having this list it again.
    """
    v = _views._view(cid, v)
    mine, inherited = _image_halves(cid, aid, vid, base, v)
    union = {i["name"] for i in mine} | {i["name"] for i in inherited}
    # One read of the campaign sidecar answers both halves: the campaign's own
    # images by R1 with their ids, and the inherited ones by their legacy key
    # ONLY -- no id is passed for them, so no object is read on their behalf
    # here. A non-string value is dropped by the typed read, so a malformed
    # campaign entry never masks the world's text.
    out = image_descriptions.read_all(v.croot, aid, vid, base, names=union, ids=_row_ids(mine))
    # Only read the world side if some image falls through to it -- a fully
    # diverged (or detached) version never touches it.
    fall = {i["name"] for i in inherited if i["name"] not in out}
    if fall:
        out.update(image_descriptions.read_all(v.wroot, aid, vid, base, names=fall,
                                               ids=_row_ids(inherited)))
    return dict(sorted(out.items())), sorted(mine + inherited, key=lambda i: i["name"])


def set_description(cid: str, aid: str, vid: str, name: str, text: str,
                    base: str = "characters", **guards) -> None:
    """Write one image's description, as this campaign sees the image.

    - **Inherited art whose world placement resolves** (D1, R3): the text goes
      on the shared image object behind it -- the picture every world and
      campaign holding it shows -- and the name's legacy key is cleared in the
      world's directory and in this campaign's (`image_descriptions.set_in`'s
      `also_clear`), so neither masks the edit. Another campaign's key is not
      this edit's to clear: it keeps masking the shared text until migration.
      A write the image store does not confirm falls back to THIS campaign's
      legacy key (`fallback_dir`), never the world's.
    - **Anything else** -- the campaign's own art, or inherited art the world
      holds as a legacy file with no object to share -- lands campaign-side,
      as it always has: on the object behind a campaign placement, or as the
      campaign's legacy key, which `read_description` then treats as
      authoritative for that image.

    The existence gate is the overlay UNION, not this campaign's own directory:
    a thin campaign reaches most of its art through the world, and describing
    an inherited picture must not require diverging the picture.

    UNDER `campaign_lock`, unlike almost everything else in this module.
    `overlay` sits in `locks.UNREVIEWED` — the frozen backlog — so the guard
    does not require it, but riding that grandfather would be taking a licence
    granted to code that predates the lock domain and spending it on new code.
    `store.covers` and `store.campaign_images` are the precedent for what a new
    campaign-scoped mutator does, and both take it.

    It earns it on the merits too: a description sidecar is read-modify-written
    whole, so two unlocked writers lose one of the two — and what is lost here
    is a sentence somebody sat and wrote, not a derived value that regenerates.
    Two browser tabs, or the describe queue saving while an editor saves, is all
    it takes. Reentrant, so a caller already holding it pays nothing.

    The WORLD-side write (`image_descriptions.set_description` straight onto a
    world root) is still unlocked, and that is not an oversight this closes:
    worlds have no lock domain at all, and `focus.json` and `subjects.json` race
    there in exactly the same way. Naming it here rather than leaving the
    asymmetry to be discovered.
    """
    with locks.campaign_lock(cid):
        # The union is resolved INSIDE the lock, and `set_in` re-checks it inside
        # the sidecar lock as well. Computed outside, it was a check-then-act:
        # a slot could be promoted away between the check and the write.
        v = _views._view(cid, None)
        mine, inherited = _image_halves(cid, aid, vid, base, v)
        union = {i["name"] for i in mine} | {i["name"] for i in inherited}
        if any(i["name"] == name for i in inherited):
            wdir = assets.version_dir(v.wroot, aid, vid, base)
            cdir = assets.version_dir(v.croot, aid, vid, base)
            if image_descriptions.set_inherited(wdir, cdir, name, text, names=union, **guards):
                return
            # Legacy inherited art has no shared object to update. Its binding
            # still participates in the guard, and campaign/world key order
            # must match what this editor displayed before Save.
            guards = {**guards, "source_dir": wdir, "effective_dirs": (cdir, wdir)}
        image_descriptions.set_description(v.croot, aid, vid, name, text, base,
                                           names=union, **guards)


def delete_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    with locks.campaign_lock(cid):
        _ledgers.require_identity_ledgers(cid)
        assets.delete_image(_record_paths.croot_of(cid), aid, vid, name, base)   # no-op when absent
        if assets.image_path(_record_paths.wroot_of(cid), aid, vid, name, base) is not None:
            _ledgers.add_deleted(cid, _record_paths.asset_ref(base, aid, vid, name))


def promote_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    """Copy-up the named image and the current avatar, then swap campaign-side.

    The copy-up is a LINK: the campaign's slot is a placement naming the
    world's image, so promoting inherited art writes no blob.

    UNDER `campaign_lock`, for `set_description`'s reason and then one of its
    own. The sidecar lock serializes each *write* to the file, which is not
    enough here: this reads the world's legacy keys and carries them up a few
    statements later, so a save landing in that gap was read past and then
    masked by the stale carried key -- losing text somebody had just written
    (PR review). A read-modify-write needs the lock the other writer takes, and
    for campaign-scoped state that is this one.
    """
    with locks.campaign_lock(cid):
        # Image publication selects the current binding with its target (spec 07):
        # a campaign recreated while waiting cannot carry its former World art.
        croot, wroot = _record_paths.croot_of(cid), _record_paths.wroot_of(cid)
        severed, gone = _ledgers.require_identity_ledgers(cid)
        inherits = not _identity.record_is_marked(cid, base, aid, severed, roots=(croot, wroot))
        cdir = assets.version_dir(croot, aid, vid, base)
        wdir = assets.version_dir(wroot, aid, vid, base)
        _check_promote_arrived(cid, aid, vid, name, base, inherits, cdir, wdir)
        for n in (name, assets.AVATAR):
            if not (inherits and assets.image_path(croot, aid, vid, n, base) is None
                    and _record_paths.asset_ref(base, aid, vid, n) not in gone):
                continue
            # Belt and braces for the check above: never link over an
            # image-bearing campaign placement, arrived or not. An image-less
            # one (a crop over the inherited avatar) is still filled, under its
            # crop.
            held = image_refs.read(cdir, n)
            if held is not None and held.image is not None:
                continue
            src = assets.image_path(wroot, aid, vid, n, base)
            if src is None:
                continue
            # The DESCRIPTION first, then the bytes (R5). Copying the bytes up
            # is what makes this campaign hold the picture, and from then on
            # its slot answers by its own key or the object -- no longer by the
            # world's key. So the one text that would be lost is a string
            # WORLD legacy key with no campaign key over it, and that alone is
            # carried, as a campaign legacy key: text read off the object is
            # still the object's after the link, and a promote never writes
            # the shared object. Description first because describing an image
            # whose bytes are still inherited is an ordinary state of this
            # store, so a failure between the two leaves something coherent;
            # the other order leaves the picture campaign-side with the world's
            # sentence masked, and a retry cannot even see that it has to fix
            # it -- `image_path` is non-null by then, so the carry-up is
            # skipped forever (PR review).
            #
            # Key presence, not truthiness: `""` is "reviewed, nothing to say"
            # and has to travel too, or the promoted image walks back into the
            # describe queue somebody has already answered for.
            if image_descriptions.legacy_text_in(cdir, n) is None:
                carried = image_descriptions.legacy_text_in(wdir, n)
                if carried is not None:
                    image_descriptions.carry_legacy(cdir, n, carried)
            # By REFERENCE (spec section 8): the campaign slot names the very
            # image the world's does, so no blob is written. A legacy world
            # file is ingested once -- the store keeps its blob -- and the
            # world's folder is left as it was.
            placed = assets.resolve(wdir, n)
            image_id = (placed.image_id if placed is not None
                        else image_store.ingest(src.read_bytes(), src.suffix[1:]).id)
            assets.link_in(cdir, n, image_id)
        assets.promote_image(croot, aid, vid, name, base)
        # When there was no avatar to swap into the promoted slot, the swap
        # leaves no campaign file at `name`, so the inherited image there would
        # still show through the overlay next to the new avatar. Tombstone it so
        # promotion moves the image out of the gallery instead of duplicating it.
        if (inherits and assets.image_path(croot, aid, vid, name, base) is None
                and assets.image_path(wroot, aid, vid, name, base) is not None):
            _ledgers.add_deleted(cid, _record_paths.asset_ref(base, aid, vid, name))


def _check_promote_arrived(cid: str, aid: str, vid: str, name: str, base: str,
                           inherits: bool, cdir: Path, wdir: Path) -> None:
    """Refuse a campaign promotion whose slots have not arrived, before
    `promote_image` copies anything up -- a copy-up landed before a refusal
    would leave the campaign holding a slot it no longer inherits.

    A slot's picture is the campaign's own placement, or -- for a slot it
    inherits (no image of its own, not tombstoned) -- the WORLD's, so it is
    that placement whose arrival is asked: the campaign's own unarrived pick
    is not a gap to copy over, and an inherited one the world has not received
    refuses here as the world-scope promotion does. `_check_promotable`'s
    order: the promoted slot first, the avatar only once that slot exists at
    all, so a slot that is missing -- or tombstoned here -- is still "not
    found" whatever the avatar's state."""
    gone = _ledgers.deleted(cid)

    def inherited(n: str) -> bool:
        if not inherits or _record_paths.asset_ref(base, aid, vid, n) in gone:
            return False
        held = image_refs.read(cdir, n)
        return (held is None or held.image is None) and assets.path_in(cdir, n) is None

    def check(n: str) -> bool:
        """Raise if `n` has not arrived; whether `n` holds a picture here."""
        assets.check_arrived(cdir, n)
        if inherited(n):
            assets.check_arrived(wdir, n)
            return assets.path_in(wdir, n) is not None
        return assets.path_in(cdir, n) is not None

    if check(name):
        check(assets.AVATAR)


def base_versions(cid: str, char_id: str, *, v: _views.View | None = None) -> list[dict]:
    """The world's versions of a character this campaign no longer holds, as
    art it may look at beside the version it shows -- the pass-through.

    `list_images` unions the two roots for ONE version id, which is right for
    the version it names and blind to every other: a pick purges every sibling
    version from the campaign (`appearances.versions._lock`), and from then on
    the union cannot reach a version id the campaign does not have -- yet the
    character's art is filed per version, and the pictures the player wants are
    as likely under a purged sibling as under the locked one. This is the read
    that fills that gap, and it is a READ: nothing here is writable
    campaign-side.

    Each version is read through the UNION, not off the world root, because the
    union is what the campaign route serves and a `?v=` URL is cached immutable:
    a pick unlinks a sibling's card and leaves its assets folder behind, so a
    campaign-side file can still sit under a version the campaign no longer
    holds, and `image_root` will serve it first. The token and the caption have
    to name those bytes, not the world's -- `list_images` and
    `read_descriptions` already answer for exactly that case.

    Every world version the campaign lacks, the world's default first and the
    rest by id -- one order, so a consumer that has to break a name tie
    (`context.art`, whose handles carry no version) breaks it the same way the
    shelf lists them. A version the campaign still holds is not listed: the
    union already shows it. A version with no art is not listed either -- an
    empty pass-through is nothing to show.

    The same three doors close it that close the union. A detached record's
    world id-mate is a stranger; a tombstoned record has no world; and a
    per-image tombstone hides that picture here too, so what the campaign
    removed stays removed. Empty when any of them is shut, or when the world no
    longer holds the character.
    """
    v = _views._view(cid, v)
    ref = _record_paths.flat_ref("characters", char_id)
    if ref in v.gone or ref in v.off:
        return []
    wroot = v.wroot
    theirs = characters.version_ids(wroot, char_id)
    if not theirs:
        return []
    held = set(characters.version_ids(_actors.char_root(cid, char_id, v=v), char_id))
    default = characters.default_version(wroot, char_id)
    ordered = ([default] if default in theirs else []) + [x for x in theirs if x != default]
    out = []
    for vid in ordered:
        if vid in held:
            continue
        # Tombstones applied, campaign file first; one listing serves both.
        described, images = read_descriptions_listed(cid, char_id, vid, v=v)
        if not images:
            continue
        out.append({
            "id": vid,
            "name": characters.version_label(wroot, char_id, vid),
            "images": [i["name"] for i in images],
            "image_v": {i["name"]: i["v"] for i in images},
            "image_ids": {i["name"]: i["image_id"] for i in images if i.get("image_id")},
            "image_descriptions": described,
        })
    return out


def shadowed_images(cid: str, char_id: str, vid: str, *, base: str = "characters",
                    v: _views.View | None = None) -> list[dict]:
    """The world's copies of one version's pictures that the union HIDES: same
    version, same name, and a campaign file of its own under that name.

    `list_images` shows one picture per name and the campaign's wins, which is
    the right rule for a shelf and the wrong one for a reader who wants the
    picture the campaign replaced -- the world's `gallery_1` is not gone, it is
    unreachable. This is the read that reaches it. Read-only, like
    `base_images`: the world's bytes, the world's cache token, the world's
    description. Serve it from the WORLD route -- the campaign route resolves
    that name to the campaign's file, by construction.

    Same bytes are not a second picture. A demote copies the campaign's assets
    up and a promote copies the world's down (and swaps), so same name + same
    bytes -- under that name or any other the campaign holds in the version --
    is the common case and is one picture, already on the shelf. A world copy
    is listed only when NO campaign file in the version has its bytes. Byte
    comparison rather than the cache token, which is mtime+size and differs
    for every copy ever made -- bucketed by size first, so a version holding
    many pictures reads only the pairs that could match.

    The record doors close it as they close the union (detached, tombstoned),
    and so does a per-image tombstone, though a name the campaign holds a
    file under cannot normally carry one.
    """
    v = _views._view(cid, v)
    ref = _record_paths.flat_ref(base, char_id)
    gone = v.gone
    if ref in gone or ref in v.off:
        return []
    croot = v.croot
    # Held NAMES come from the listing, not from file stems: a placement's
    # path is a blob named by its hash.
    mine_rows = assets.list_images(croot, char_id, vid, base)
    held = {i["name"] for i in mine_rows}
    if not held:
        return []
    wroot = v.wroot
    # What the campaign holds, compared the cheapest sound way. Two placements
    # are the same picture iff they name the same image id, so no bytes are
    # read for that pair. Anything involving a legacy file has no identity to
    # compare and falls to bytes: a legacy row's file, and -- for a legacy
    # world row -- a placement's blob.
    mine_ids, legacy_files, all_files = _held_pictures(croot, char_id, vid, mine_rows, base)
    out = []
    for i in assets.list_images(wroot, char_id, vid, base):
        if i["name"] not in held or _record_paths.asset_ref(base, char_id, vid, i["name"]) in gone:
            continue
        if i.get("image_id") and i["image_id"] in mine_ids:
            continue   # the same picture, however it is named or stored
        compare = legacy_files if i.get("image_id") else all_files
        if _novel_bytes(assets.image_path(wroot, char_id, vid, i["name"], base), compare):
            out.append(i)
    if not out:
        return []
    # The world's R1 for each row, off the world listing's own ids: no
    # placement is read twice, and a placement-backed row shows the text on
    # its (shared) image object.
    described = image_descriptions.read_all(wroot, char_id, vid, base,
                                            names={i["name"] for i in out}, ids=_row_ids(out))
    return [{"name": i["name"], "v": i["v"],
             **({"image_id": i["image_id"]} if i.get("image_id") else {}),
             **({"description": described[i["name"]]} if i["name"] in described else {})}
            for i in out]


def _held_pictures(root: Path, char_id: str, vid: str,
                   rows: list[dict], base: str = "characters") -> tuple[set[str], list[Path], list[Path]]:
    """What a version's listing `rows` hold, as `shadowed_images` compares it:
    the placement image ids, the files of rows with no identity (legacy), and
    every row's resolved file."""
    ids = {i["image_id"] for i in rows if i.get("image_id")}
    legacy: list[Path] = []
    every: list[Path] = []
    for i in rows:
        p = assets.image_path(root, char_id, vid, i["name"], base)
        if p is None:
            continue
        every.append(p)
        if not i.get("image_id"):
            legacy.append(p)
    return ids, legacy, every


def _novel_bytes(theirs: Path | None, mine: list[Path]) -> bool:
    """True when `theirs` is a real file and none of `mine` holds its bytes.
    Size first, so only the pairs that could match are read. `mine` holds
    resolved paths -- a placement's blob, a legacy file -- so one comparison
    serves a placement against a legacy file and two legacy files alike."""
    if theirs is None:
        return False
    size = theirs.stat().st_size
    return not any(p.stat().st_size == size and filecmp.cmp(theirs, p, shallow=False) for p in mine)
