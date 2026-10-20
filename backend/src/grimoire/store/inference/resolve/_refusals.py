"""Pure setup/capability refusal precedence and stable role/route/provider sentences."""

from __future__ import annotations

import dataclasses

from ... import image_drafts, routing
from .. import capabilities, providers
from ..resolved import Attempt, ResolvedInference
from . import _capability_policy, _contracts, _targeting


def unusable(resolved: ResolvedInference) -> _contracts.Refusal | None:
    """Why this resolution cannot send, if it cannot: the 409 `missing_key`.

    Shared by the seam and by the per-call override, whose `model`-only reroll
    drives the standing route and must be refused on exactly the same terms --
    including the routed-connection wording below.
    """
    if not resolved.attempts:
        return 409, {"detail": "No LLM connection selected", "kind": "missing_key"}
    primary = resolved.attempts[0].target
    why = _targeting.target_problem(primary)
    if why is None:
        return None
    # Refused before the facade, so a configured fallback does not rescue it.
    # The facade falls back on `auth`, and the distinction is real: a key the
    # provider rejected is a runtime failure, worth routing around silently,
    # while no key at all is a setup mistake. Quietly serving it from the
    # fallback would leave someone playing for weeks on the wrong connection.
    name = primary.label
    if resolved.legacy:
        # A format-1 store keeps the sentences it always answered (user
        # ruling 2026-10-09): the bare reason, and a ROUTED connection named
        # by the legacy route it was stored under -- reported, not walked
        # past, because the user pointed this task at it.
        if resolved.via == "route":
            return 409, {"detail": f"{why} ({name}, routed for "
                                   f"{routing.label_for(resolved.legacy_route or '').lower()})",
                         "kind": "missing_key"}
        return 409, {"detail": why, "kind": "missing_key"}
    # Spec 12: the provider, and the route or role that chose it. Nearly
    # everything comes through a role, and two providers of one kind would
    # otherwise read the same sentence. A pin is named by its own route (a
    # split route's pin is its own, not its legacy parent's): a ROUTED
    # connection that cannot send is reported, not walked past -- the user
    # pointed this task at it.
    where = (f"routed for {routing.label_for(resolved.route).lower()}"
             if resolved.via == "route"
             else f"the {resolved.role.capitalize()} role" if resolved.role else "")
    detail = f"{why} ({name}, {where})" if where else f"{why} ({name})"
    return 409, {"detail": detail, "kind": "missing_key"}


def incapable(resolved: ResolvedInference) -> _contracts.Refusal | None:
    """The 409 for a primary that is KNOWN unable to do what its route needs.

    Only a known `no` (`resolved.missing`); `unknown` is let through, and a
    fallback's gaps are reported (`fallback_missing`) but never refused on.
    Asked after `unusable` (`refusal`), so a connection with no key says that
    first: it is the fix the reader has to make before anything else matters.

    A wire protocol that cannot carry an image (an `adapter` `no` on vision)
    answers with the sentence the image-description route always answered
    with, in the same body, so nothing that reads it sees a change. A model
    whose facts say "Images: on" is not refused over a catalog's vision `no`:
    that word is a user `yes` the capability resolution already ranks above
    the catalog (a legacy connection's own "Images: on" reaches the facts
    through the planner, `Overlay.facts`). Anything else is `incapable` (`incapable_text`), naming the first missing
    capability in `capabilities.NAMES` order -- with its alternatives, on a
    decide resolution whose primary can neither generate nor decide natively
    (`_cannot`). A name-rule guess is never
    missing (`_GUESSES`), and a failed test call is `unknown`, so neither can
    refuse here.
    """
    if not resolved.missing or not resolved.attempts:
        return None
    primary = resolved.attempts[0]
    vision = primary.capabilities.get("vision")
    if "vision" in resolved.missing and vision is not None and vision.source == "adapter":
        return 409, image_drafts.UNSUPPORTED
    return 409, {"detail": incapable_text(resolved, resolved.missing[0]),
                 "kind": "incapable"}


def refusal(resolved: ResolvedInference) -> _contracts.Refusal | None:
    """The seam's one decision about `resolved`: `(status, body)` when it
    cannot be served, None when it can. Pure -- it reads nothing.

    `routes.common` raises from it, and the settings view reports it, so what
    the screen says is wrong with a row is the seam's own answer rather than a
    copy of it."""
    return unusable(resolved) or incapable(resolved)


def escalation_refusal(resolved: ResolvedInference, task: str) -> _contracts.Refusal | None:
    """Why the escalation role's resolution of `task` cannot serve its hop
    (roadmap 01d §5.2), or None when it can. Pure, like `refusal`.

    `resolved` is `resolve(task, operation="decide", role=...)`, which has no
    route: its `missing` covers only the operation's own needs. A hop answers
    for the BASE route, though, so it is held to that route's `requires`
    too, and never runs on a model the route itself could not. `unusable`
    (no key, nothing selected) comes first, as at the seam; then a known gap,
    as 409 `incapable` with `escalation_text`'s sentence naming the route."""
    found = unusable(resolved)
    if found is not None or not resolved.attempts:
        return found
    route = routing.route(task)
    missing = _capability_policy._missing(resolved.attempts[0], _capability_policy._needs(route, resolved.operation))
    if not missing:
        return None
    held = dataclasses.replace(resolved, route=route.key if route is not None else "",
                               missing=missing)
    return 409, {"detail": escalation_text(held, missing[0]), "kind": "incapable"}


def escalation_text(resolved: ResolvedInference, cap: str) -> str:
    """The `incapable` sentence for an escalation hop: the BASE route it
    escalates (`resolved.route`), the role and the model it would be handed
    to, what that model cannot do (`_cannot`), and the remedy -- another
    model for that role. Not `incapable_text`, whose "or pin this route"
    would not move the hop: a pin moves the route's own calls, never its
    escalation role."""
    role = resolved.role.capitalize()
    subject = (f"The {routing.label_for(resolved.route)} route" if resolved.route
               else "This decision")
    return (f"{subject} escalates to the {role} role ({_on(resolved.attempts[0])}), "
            f"which cannot {_cannot(resolved, cap)} — choose another {role} model.")


def incapable_text(resolved: ResolvedInference, cap: str) -> str:
    """The `incapable` sentence (spec 5.3): the route, the role when one
    supplied the model, the model on its provider, what it cannot do
    (`_cannot`), and what to do about it.

    "The <label> route ..." rather than "<label> runs ...", because half the
    route labels are plural ("Scene turns", "Image descriptions"). The role
    is named only when the model IS the role's: a pin names none, and a
    per-call override moved the call off whatever the role chose. The remedy
    follows: another model for the role, or a pin, where there is a route to
    pin; another model for the route where it is already pinned. The model is
    the one the connection runs (`facts.model_of`, `llm.effective_model`'s
    rule)."""
    subject, where, remedy = _primary_phrases(resolved)
    return f"{subject} {where}, which cannot {_cannot(resolved, cap)} — {remedy}."


def _cannot(resolved: ResolvedInference, cap: str) -> str:
    """What the primary cannot do, as `incapable_text` says it: `cap`'s
    phrase (`capabilities.CANNOT`), or -- when `cap` is one of its
    operation's alternatives (`OPERATION_CAPABILITY`) and the primary is
    missing them all -- every alternative's, joined by "or" in
    `capabilities.NAMES` order ("generate text or make native decisions").
    `CANNOT` stays keyed by capability, so a picker's reasons
    (`capabilities.group_for`) are unchanged."""
    group = _contracts.OPERATION_CAPABILITY.get(resolved.operation, ())
    if len(group) > 1 and cap in group and all(c in resolved.missing for c in group):
        return " or ".join(capabilities.CANNOT.get(c, c) for c in capabilities.NAMES
                           if c in group)
    return capabilities.CANNOT.get(cap, cap)


def _on(attempt: Attempt) -> str:
    """"<model> on <provider>": the model the connection runs, and the
    provider's name (else its preset's label, else its id)."""
    target = attempt.target
    preset = providers.PRESETS.get(attempt.provider_preset)
    provider = target.provider_name or (preset.label if preset is not None
                                        else target.provider_id)
    return f"{target.model} on {provider}"


def _primary_phrases(resolved: ResolvedInference) -> tuple[str, str, str]:
    """`(subject, where, remedy)` for the primary attempt -- what the route
    is, what it runs on, and what to do about it (`incapable_text`)."""
    primary = resolved.attempts[0]
    on = _on(primary)
    standing = resolved.standing
    chosen = standing is not None and (standing.provider, standing.model) == (
        primary.provider_id, primary.model)
    subject = (f"The {routing.label_for(resolved.route)} route" if resolved.route
               else "This decision" if resolved.operation == "decide"
               else "This generation")
    pin = " or pin this route" if resolved.route else ""
    if chosen and resolved.role:
        role = resolved.role.capitalize()
        where = f"runs on the {role} role ({on})"
        remedy = f"choose another {role} model{pin}"
    elif chosen and resolved.via == "route":
        where, remedy = f"is pinned to {on}", "choose another model for this route"
    else:
        where, remedy = f"runs on {on}", f"choose another model{pin}"
    return subject, where, remedy
