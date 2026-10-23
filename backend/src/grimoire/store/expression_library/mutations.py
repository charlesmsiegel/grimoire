"""Apply an ordered batch to its evolving semantic layer before any publication."""

import copy

from . import contracts, images


def visible(inherited: dict, manifest: dict, slots: dict) -> dict:
    outfits = copy.deepcopy(inherited["outfits"])
    for outfit, meta in manifest["outfits"].items():
        prior = outfits.get(outfit, {})
        outfits[outfit] = {**contracts.merge_metadata(outfit, prior, meta),
                          "expressions": dict(prior.get("expressions", {}))}
    for (outfit, label), image in slots.items():
        outfits[outfit]["expressions"][label] = {"image_id": image}
    for outfit in manifest["hidden_outfits"]:
        outfits.pop(outfit, None)
    for outfit, labels in manifest["hidden_expressions"].items():
        for label in labels:
            if outfit in outfits:
                outfits[outfit]["expressions"].pop(label, None)
    return outfits


def _outfit(manifest: dict, slots: dict, mutation: dict, visible: dict, outfit: str) -> None:
    op, outfits = mutation["op"], manifest["outfits"]
    if op == "create-outfit":
        if outfit in outfits or outfit in visible:
            raise contracts.ExpressionConflictError("Outfit already exists")
        outfits[outfit] = contracts.metadata(mutation.get("metadata", {"label": outfit}))
    elif op == "patch-outfit":
        if outfit not in outfits and outfit not in visible:
            raise contracts.ExpressionInvalidError("Outfit does not exist")
        patch = contracts.metadata(mutation.get("metadata", {}))
        old = outfits.get(outfit, {})
        outfits[outfit] = {**old, **patch}
        if "presentation" in patch:
            outfits[outfit]["presentation"] = {**old.get("presentation", {}), **patch["presentation"]}
    elif op == "remove-outfit":
        _remove_outfit(manifest, slots, outfit)
    else:
        if outfit not in visible:
            raise contracts.ExpressionInvalidError("Visible outfit does not exist")
        manifest["default_outfit"] = outfit


def _remove_outfit(manifest: dict, slots: dict, outfit: str) -> None:
    manifest["outfits"].pop(outfit, None)
    for key in list(slots):
        if key[0] == outfit:
            del slots[key]
    if manifest["default_outfit"] == outfit:
        manifest["default_outfit"] = None


def _duplicate(manifest: dict, slots: dict, mutation: dict, visible: dict, outfit: str) -> None:
    destination = contracts.slug(mutation.get("new_outfit"))
    if destination in manifest["outfits"] or destination in visible:
        raise contracts.ExpressionConflictError("Destination outfit already exists")
    if outfit not in visible:
        raise contracts.ExpressionInvalidError("Source outfit does not exist")
    source = visible[outfit]
    manifest["outfits"][destination] = {k: copy.deepcopy(source[k]) for k in
        ("label", "clothing_aliases", "fallback_expression", "presentation")}
    for label, row in source["expressions"].items():
        slots[destination, label] = row["image_id"]


def _expression(manifest: dict, slots: dict, mutation: dict, visible: dict, outfit: str) -> None:
    if outfit not in manifest["outfits"] and outfit not in visible:
        raise contracts.ExpressionInvalidError("Outfit does not exist")
    label = contracts.slug(mutation.get("expression"))
    if mutation["op"] == "put-expression":
        data = mutation.get("data")
        ext, _ = images.validate(data)
        manifest["outfits"].setdefault(outfit, {})
        slots[outfit, label] = (data, ext)
    else:
        slots.pop((outfit, label), None)


def _visibility(manifest: dict, mutation: dict, outfit: str) -> None:
    op = mutation["op"]
    if op.endswith("outfit"):
        hidden = set(manifest["hidden_outfits"])
        (hidden.add if op.startswith("hide") else hidden.discard)(outfit)
        manifest["hidden_outfits"] = sorted(hidden)
    else:
        label = contracts.slug(mutation.get("expression"))
        hidden = set(manifest["hidden_expressions"].get(outfit, []))
        (hidden.add if op.startswith("hide") else hidden.discard)(label)
        manifest["hidden_expressions"][outfit] = sorted(hidden)


def apply(manifest: dict, slots: dict, mutation: dict, inherited: dict) -> None:
    if not isinstance(mutation, dict):
        raise contracts.ExpressionInvalidError("Invalid mutation")
    op = mutation.get("op")
    outfit = contracts.slug(mutation.get("outfit"))
    current = visible(inherited, manifest, slots)
    if op in ("create-outfit", "patch-outfit", "remove-outfit", "set-default"):
        _outfit(manifest, slots, mutation, current, outfit)
    elif op == "duplicate-outfit":
        _duplicate(manifest, slots, mutation, current, outfit)
    elif op in ("put-expression", "remove-expression"):
        _expression(manifest, slots, mutation, current, outfit)
    elif op in ("hide-outfit", "restore-outfit", "hide-expression", "restore-expression"):
        _visibility(manifest, mutation, outfit)
    else:
        raise contracts.ExpressionInvalidError("Unknown expression mutation")
