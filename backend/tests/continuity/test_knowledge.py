"""Knowledge-state tests (who knows what)."""

from __future__ import annotations

import pytest

from grimoire.continuity import FactNotFoundError


async def test_default_knows_is_false(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="x"), source="x")
    assert await service.knows("julian", fid) is False


async def test_reveal_sets_knows_true(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="x"), source="x")
    await service.reveal(fid, to=["julian", "winifred"], in_post="p", source="told by mira")
    assert await service.knows("julian", fid) is True
    assert await service.knows("winifred", fid) is True
    assert await service.knows("kell", fid) is False


async def test_reveal_unknown_fact_raises(service):
    with pytest.raises(FactNotFoundError):
        await service.reveal("missing", to=["a"], in_post="p", source="x")


async def test_secrets_of_returns_only_secret_known_facts(service, fact_factory):
    public_fid = await service.add_fact(
        fact_factory(text="public news", tags=["public"]), source="x"
    )
    secret_fid = await service.add_fact(fact_factory(text="forbidden", tags=["secret"]), source="x")
    await service.reveal(public_fid, to=["julian"], in_post="p", source="witnessed")
    await service.reveal(secret_fid, to=["julian"], in_post="p", source="told by mira")
    secrets = await service.secrets_of("julian")
    ids = {f.id for f in secrets}
    assert ids == {secret_fid}


async def test_secrets_of_excludes_retired(service, fact_factory):
    fid = await service.add_fact(fact_factory(text="forbidden", tags=["secret"]), source="x")
    await service.reveal(fid, to=["julian"], in_post="p", source="x")
    await service.retire_fact(fid, in_post="p", reason="refuted")
    assert await service.secrets_of("julian") == []
