"""Deterministic sprite fallback that exhausts the selected outfit first.

Candidates are semantic choices, never narrative facts. Keeping the ordered
fallback list lets an image-load failure advance without changing resolver rules.
"""

from dataclasses import dataclass

from ..tracker import fields


@dataclass(frozen=True)
class SpriteChoice:
    outfit_id: str | None = None
    expression: str | None = None
    image_id: str | None = None
    token: str | None = None
    fallback: str = "placeholder"


def candidates(collection: dict, outfit_id: str | None, expression: str | None, *,
               avatar: SpriteChoice | None = None, explicit: bool = False) -> tuple[SpriteChoice, ...]:
    mood = expression if explicit or expression in fields.VISIBLE_MOODS else "neutral"
    outfits = collection["outfits"]
    chosen = outfit_id if outfit_id in outfits else collection["default_outfit"]
    order = list(dict.fromkeys([chosen, collection["default_outfit"]]))
    result = []
    for outfit in order:
        if outfit not in outfits:
            continue
        metadata = outfits[outfit]
        sprites = metadata["expressions"]
        labels = dict.fromkeys([mood, metadata["fallback_expression"], "neutral", *sorted(sprites)])
        for label in labels:
            row = sprites.get(label)
            if row is None or not row["available"]:
                continue
            fallback = "default-outfit" if outfit != chosen else "exact" if label == mood else "outfit"
            result.append(SpriteChoice(outfit, label, row["image_id"], row["token"], fallback))
    if avatar is not None:
        result.append(avatar)
    result.append(SpriteChoice())
    return tuple(result)


def sprite(collection: dict, outfit_id: str | None, expression: str | None, *,
           avatar: SpriteChoice | None = None, explicit: bool = False) -> SpriteChoice:
    return candidates(collection, outfit_id, expression, avatar=avatar, explicit=explicit)[0]
