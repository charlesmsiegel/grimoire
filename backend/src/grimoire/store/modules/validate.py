"""Manifest, sheets.json and checks.json validation.

Every helper here appends to an ``errors`` list rather than raising: a pack
with problems still loads, and ``binding.resolve`` is what refuses to bind it.
"""

from __future__ import annotations

import re

from .. import dice, entities, expressions
from .fields import _pool_group_fields, assembled_fields, numeric_names

FIELD_TYPES = ("number", "dots", "track", "resource", "text", "list", "ref")
SHEET_KINDS = ("characters", "items", "locations", "creatures", "groups", "lore")
RESERVED_NAMES = ("difficulty", "modifier", "new")


def _validate_manifest(meta: dict, errors: list[str]) -> None:
    if not meta.get("name"):
        errors.append("module.md: manifest requires a name")
    d = meta.get("dice")
    if d:
        try:
            dice.parse(d)
        except dice.DiceError as e:
            errors.append(f"module.md: bad dice default: {e}")


def _as_list(value, where, what, errors) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{where}: {what} must be a list")
        return []
    return value


def _as_dict(value, where, what, errors) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{where}: {what} must be an object")
        return {}
    return value


def _validate_field(field: dict, where: str, errors: list[str]) -> None:
    if not isinstance(field, dict):
        errors.append(f"{where}: field must be an object")
        return
    key = field.get("key")
    if not key or not isinstance(key, str):
        errors.append(f"{where}: field missing key")
        return
    if key in expressions._FUNCS:
        errors.append(f"{where}.{key}: reserved key (expression function name)")
        return
    if key in RESERVED_NAMES:
        errors.append(f"{where}.{key}: reserved key (ambient expression name)")
        return
    ftype = field.get("type")
    if ftype not in FIELD_TYPES:
        errors.append(f"{where}.{key}: unknown field type {ftype!r}")
        return
    if ftype == "ref":
        ref_kind = field.get("ref_kind")
        if ref_kind not in entities.ENTITY_KINDS:
            errors.append(f"{where}.{key}: ref field requires ref_kind in {entities.ENTITY_KINDS}")
    if ftype in ("dots", "track", "resource"):
        m = field.get("max")
        if not isinstance(m, int) or isinstance(m, bool):
            errors.append(f"{where}.{key}: {ftype} requires an integer max")


def _validate_derived(derived: dict, scope: set[str], where: str,
                      errors: list[str]) -> set[str]:
    """Validate a derived map against a name scope; returns derived names."""
    out: set[str] = set()
    for name, expr in derived.items():
        if name in RESERVED_NAMES or name in expressions._FUNCS:
            errors.append(f"{where}.{name}: reserved derived name")
            continue
        if name in scope:
            errors.append(f"{where}.{name}: derived name collides with a field")
            continue
        if not isinstance(expr, str):
            errors.append(f"{where}.{name}: derived expression must be a string")
            continue
        try:
            unknown = expressions.names(expr) - scope
        except expressions.ExpressionError as e:
            errors.append(f"{where}.{name}: {e}")
            continue
        if unknown:
            errors.append(f"{where}.{name}: unknown names {sorted(unknown)}")
        out.add(name)
    return out


def _validate_creation(st: dict, st_groups: list, groups: dict, where: str,
                       errors: list[str]) -> None:
    creation = st.get("creation")
    if creation is None:
        return
    if not isinstance(creation, dict):
        errors.append(f"{where}.creation: must be an object")
        return
    pools = _as_dict(creation.get("pools"), f"{where}.creation", "pools", errors)
    for pool_id, pool in pools.items():
        pwhere = f"{where}.creation.pools.{pool_id}"
        if not isinstance(pool, dict):
            errors.append(f"{pwhere}: must be an object")
            continue
        if pool_id not in st_groups:
            errors.append(f"{pwhere}: {pool_id!r} is not a group of this sheet type")
            continue
        group_fields = _pool_group_fields(groups.get(pool_id))
        budget = pool.get("budget", 0)
        if isinstance(budget, str):
            try:
                unknown = expressions.names(budget)
            except expressions.ExpressionError as e:
                errors.append(f"{pwhere}.budget: {e}")
            else:
                if unknown:
                    errors.append(f"{pwhere}.budget: must not reference fields, found {sorted(unknown)}")
                else:
                    try:
                        expressions.evaluate(budget, {})
                    except expressions.ExpressionError as e:
                        errors.append(f"{pwhere}.budget: {e}")
        elif not isinstance(budget, int) or isinstance(budget, bool):
            errors.append(f"{pwhere}.budget: must be an int or an expression string")
        costs = _as_dict(pool.get("costs"), pwhere, "costs", errors)
        for field_key, cost in costs.items():
            if field_key not in group_fields:
                errors.append(f"{pwhere}.costs.{field_key}: not a field of group {pool_id!r}")
                continue
            if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
                errors.append(f"{pwhere}.costs.{field_key}: must be a positive integer")


_RAISABLE_TYPES = ("number", "dots")


def _validate_advancement(st: dict, fields: list[dict], scope: set[str],
                          where: str, errors: list[str]) -> None:
    adv = st.get("advancement")
    if adv is None:
        return
    if not isinstance(adv, dict):
        errors.append(f"{where}.advancement: must be an object")
        return
    field_defs = {f["key"]: f for f in fields if isinstance(f, dict) and isinstance(f.get("key"), str)}
    pool = adv.get("pool")
    pool_field = field_defs.get(pool) if isinstance(pool, str) else None
    if not isinstance(pool_field, dict) or pool_field.get("type") != "resource":
        errors.append(f"{where}.advancement.pool: {pool!r} must be a resource field of this sheet type")
    costs = _as_dict(adv.get("costs"), f"{where}.advancement", "costs", errors)
    cost_scope = scope | {"new"}
    for field_key, expr in costs.items():
        fdef = field_defs.get(field_key)
        if not isinstance(fdef, dict) or fdef.get("type") not in _RAISABLE_TYPES:
            errors.append(f"{where}.advancement.costs.{field_key}: must target a number/dots field")
            continue
        if not isinstance(expr, str):
            errors.append(f"{where}.advancement.costs.{field_key}: expression must be a string")
            continue
        try:
            unknown = expressions.names(expr) - cost_scope
        except expressions.ExpressionError as e:
            errors.append(f"{where}.advancement.costs.{field_key}: {e}")
            continue
        if unknown:
            errors.append(f"{where}.advancement.costs.{field_key}: unknown names {sorted(unknown)}")
            continue
        sample = dict.fromkeys(cost_scope, 1)
        try:
            result = expressions.evaluate(expr, sample)
        except expressions.ExpressionError as e:
            errors.append(f"{where}.advancement.costs.{field_key}: {e}")
            continue
        if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
            errors.append(
                f"{where}.advancement.costs.{field_key}: must evaluate to a positive "
                f"integer (sampled {result!r} at every name = 1)")


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

ROLL_SCOPE_NAMES = ("total", "natural", "margin", "successes", "ones", "dice")


def _validate_outcomes(outcomes, where: str, errors: list[str]) -> None:
    if not isinstance(outcomes, list):
        errors.append(f"{where}: outcomes must be a list")
        return
    for i, tier in enumerate(outcomes):
        w = f"{where}.outcomes[{i}]"
        if not isinstance(tier, dict):
            errors.append(f"{w}: must be an object")
            continue
        label = tier.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{w}: label must be a non-empty string")
        when = tier.get("when")
        if not isinstance(when, str):
            errors.append(f"{w}: when must be an expression string")
            continue
        try:
            unknown = expressions.names(when) - set(ROLL_SCOPE_NAMES)
        except expressions.ExpressionError as e:
            errors.append(f"{w}: when: {e}")
            continue
        if unknown:
            errors.append(f"{w}: when references non-roll-scope names {sorted(unknown)}")


def _validate_checks(checks: dict, sheets: dict, rule_ids: set[str],
                     errors: list[str]) -> None:
    groups = sheets.get("groups", {})
    for cid, check in checks.items():
        if cid == "_defaults":
            continue
        if not isinstance(check, dict):
            errors.append(f"checks.{cid}: must be an object")
            continue
        where = f"checks.{cid}"
        if not check.get("label"):
            errors.append(f"{where}: missing label")
        scope: set[str] = set()
        requires = _as_list(check.get("requires"), where, "requires", errors)
        for gid in requires:
            if not isinstance(gid, str):
                errors.append(f"{where}: requires entries must be strings")
                continue
            g = groups.get(gid)
            if not isinstance(g, dict):
                errors.append(f"{where}: unknown required group {gid!r}")
                continue
            scope |= numeric_names(g.get("fields", []))
            derived = g.get("derived", {})
            if isinstance(derived, dict):
                scope |= set(derived)
        scope |= {"difficulty", "modifier"}
        if "difficulty" in check and (
                not isinstance(check["difficulty"], int) or isinstance(check["difficulty"], bool)):
            errors.append(f"{where}: difficulty must be an integer")
        roll = check.get("roll", "")
        if not isinstance(roll, str):
            errors.append(f"{where}: roll must be a string")
        else:
            exprs = _PLACEHOLDER.findall(roll)
            for expr in exprs:
                try:
                    unknown = expressions.names(expr) - scope
                except expressions.ExpressionError as e:
                    errors.append(f"{where}: {e}")
                    continue
                if unknown:
                    errors.append(f"{where}: unknown names {sorted(unknown)}")
            template = _PLACEHOLDER.sub("3", roll)
            try:
                dice.parse(template)
            except dice.DiceError as e:
                errors.append(f"{where}: roll is not dice notation: {e}")
        rule_refs = _as_list(check.get("rules"), where, "rules", errors)
        for rid in rule_refs:
            if not isinstance(rid, str):
                errors.append(f"{where}: rules entries must be strings")
                continue
            if rid not in rule_ids:
                errors.append(f"{where}: unknown rules doc {rid!r}")
        if "outcomes" in check:
            _validate_outcomes(check["outcomes"], where, errors)


def validate_sheet_values(sheets: dict, type_id: str, values: dict) -> list[str]:
    """Validate a sheet's field-value map against a sheet type. Reused by
    campaign sheets in Phase 3."""
    if not isinstance(values, dict):
        return ["fields must be an object"]
    errors: list[str] = []
    fields = {
        f["key"]: f
        for f in assembled_fields(sheets, type_id)
        if isinstance(f.get("key"), str)
    }
    for key, value in values.items():
        f = fields.get(key)
        if f is None:
            errors.append(f"{key}: not a field of sheet type {type_id!r}")
            continue
        t = f.get("type")
        if t == "resource":
            if (not isinstance(value, dict)
                    or not isinstance(value.get("current"), int) or isinstance(value.get("current"), bool)
                    or not isinstance(value.get("max"), int) or isinstance(value.get("max"), bool)):
                errors.append(f"{key}: resource needs a current/max pair")
        elif t in ("number", "dots", "track"):
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{key}: expected an integer")
            else:
                fmax, fmin = f.get("max"), f.get("min")
                fmax_ok = isinstance(fmax, int) and not isinstance(fmax, bool)
                fmin_ok = isinstance(fmin, int) and not isinstance(fmin, bool)
                if t in ("dots", "track"):
                    if not fmax_ok or not 0 <= value <= fmax:
                        errors.append(f"{key}: outside 0..max")
                elif t == "number" and (
                        (fmin_ok and value < fmin)
                        or (fmax_ok and value > fmax)):
                    errors.append(f"{key}: outside min/max")
        elif t == "text":
            if not isinstance(value, str):
                errors.append(f"{key}: expected a string")
        elif t == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{key}: expected a list of strings")
        elif t == "ref":
            ref_kind = f.get("ref_kind")
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(f"{key}: expected a list of strings")
            else:
                for entry in value:
                    parts = entry.split(":")
                    valid_entity_form = len(parts) == 2 and parts[0] == ref_kind
                    valid_module_form = len(parts) == 3 and parts[0] == ref_kind and parts[1] == "module"
                    if not (valid_entity_form or valid_module_form):
                        errors.append(f"{key}: {entry!r} is not a valid ref for kind {ref_kind!r}")
    return errors


def _validate_sheets(sheets: dict, errors: list[str]) -> None:
    groups = sheets.get("groups", {})
    for gid, group in groups.items():
        if not isinstance(group, dict):
            errors.append(f"groups.{gid}: must be an object")
            continue
        seen: set[str] = set()
        fields = _as_list(group.get("fields"), f"groups.{gid}", "fields", errors)
        for f in fields:
            _validate_field(f, f"groups.{gid}", errors)
            if not isinstance(f, dict):
                continue
            k = f.get("key")
            if not isinstance(k, str):
                continue
            if k in seen:
                errors.append(f"groups.{gid}.{k}: duplicate field key")
            seen.add(k)
        gscope = numeric_names(fields)
        derived = _as_dict(group.get("derived"), f"groups.{gid}", "derived", errors)
        _validate_derived(derived, gscope, f"groups.{gid}", errors)
    for tid, st in sheets.get("sheet_types", {}).items():
        where = f"sheet_types.{tid}"
        if not isinstance(st, dict):
            errors.append(f"{where}: must be an object")
            continue
        if st.get("kind") not in SHEET_KINDS:
            errors.append(f"{where}: unknown kind {st.get('kind')!r}")
        st_groups = _as_list(st.get("groups"), where, "groups", errors)
        for gid in st_groups:
            if not isinstance(gid, str):
                errors.append(f"{where}: group ref must be a string")
            elif gid not in groups:
                errors.append(f"{where}: unknown group ref {gid!r}")
        st_fields = _as_list(st.get("fields"), where, "fields", errors)
        for f in st_fields:
            _validate_field(f, where, errors)
        fields = assembled_fields(sheets, tid)
        resource_max = {
            f["key"] + "_max"
            for f in fields
            if isinstance(f, dict) and isinstance(f.get("key"), str)
            and f.get("type") == "resource"
        }
        for f in fields:
            if not isinstance(f, dict):
                continue
            k = f.get("key")
            if isinstance(k, str) and k in resource_max:
                errors.append(
                    f"{where}.{k}: collides with a resource field's implicit _max name")
        keys = [f.get("key") for f in fields if isinstance(f.get("key"), str)]
        for k in {k for k in keys if keys.count(k) > 1}:
            errors.append(f"{where}.{k}: duplicate field key across groups")
        scope = numeric_names(fields)
        for gid in st_groups:
            g = groups.get(gid) if isinstance(gid, str) else None
            if isinstance(g, dict) and isinstance(g.get("derived", {}), dict):
                scope |= set(g.get("derived", {}))
        st_derived = _as_dict(st.get("derived"), where, "derived", errors)
        type_derived_names = _validate_derived(st_derived, scope, where, errors)
        _validate_creation(st, st_groups, groups, where, errors)
        _validate_advancement(st, fields, scope | type_derived_names, where, errors)
