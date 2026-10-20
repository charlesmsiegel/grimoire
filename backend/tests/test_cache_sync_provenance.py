"""Warm provenance survives a package split without granting a whole package."""

import ast

import pytest

from tests.test_operation_guard import WARM_MODULE, Provenance


def _package_sources():
    sources = {WARM_MODULE: ("from ._contracts import WarmContext as WarmContext\n", True)}
    for leaf in ("_signals", "_contracts", "_paths", "_files", "_vectors",
                 "_other_hooks", "_engine", "_scopes"):
        sources[f"{WARM_MODULE}.{leaf}"] = ("", False)
    sources[f"{WARM_MODULE}._contracts"] = ("class WarmContext:\n    pass\n", False)
    sources[f"{WARM_MODULE}._files"] = (
        'class _FilesHook:\n    def network(self, ctx, plan):\n        pass\nFILES = _FilesHook()\n', False)
    sources[f"{WARM_MODULE}._other_hooks"] = (
        'class _OverviewHook:\n    def network(self, ctx, plan):\n        pass\nclass _SearchDocsHook:\n    def network(self, ctx, plan):\n        pass\nOVERVIEW = _OverviewHook()\nSEARCHDOCS = _SearchDocsHook()\n', False)
    sources[f"{WARM_MODULE}._vectors"] = (
        "from ..inference import embed\nclass _VectorsHook:\n    def network(self, ctx, plan):\n        embed.embed_groups_sync('semantic-recall', g, space=ctx.space, client=c)\nVECTORS = _VectorsHook()\n", False)
    sources[f"{WARM_MODULE}._engine"] = (
        'from .. import embed_space\nfrom . import _contracts, _files, _other_hooks, _vectors\nHOOKS = (_files.FILES, _other_hooks.OVERVIEW, _other_hooks.SEARCHDOCS, _vectors.VECTORS)\ndef _context(root):\n    return _contracts.WarmContext(root=root, space=embed_space.endpoint())\ndef _network_phases(ctx, plan):\n    for hook in HOOKS:\n        hook.network(ctx, plan)\n', False)
    return sources


def _problems(sources):
    return Provenance({name: (ast.parse(text), package)
                       for name, (text, package) in sources.items()}).problems()


def test_package_warm_context_follows_exact_registered_hook_owners():
    assert _problems(_package_sources()) == []


def test_package_warm_context_follows_the_stable_facade_constructor():
    sources = _package_sources()
    name = f"{WARM_MODULE}._engine"
    text, package = sources[name]
    text = text.replace("from .. import embed_space", "from .. import embed_space\nfrom . import WarmContext")
    sources[name] = (text.replace("_contracts.WarmContext", "WarmContext"), package)
    assert _problems(sources) == []


@pytest.mark.parametrize("leaf, change", [
    ("_vectors", "class _Rogue:\n    def network(self, ctx, plan):\n        embed.embed_sync('semantic-recall', t, space=ctx.space, client=c)\n"),
    ("_paths", "from ..inference import embed\nclass _VectorsHook:\n    def network(self, ctx, plan):\n        embed.embed_sync('semantic-recall', t, space=ctx.space, client=c)\n"),
    ("_engine", "HOOKS = (_vectors.VECTORS,)\n"),
    ("_engine", "_vectors = unknown\n"),
    ("_vectors", "_VectorsHook = unknown\n_VectorsHook = other\n"),
    ("_signals", "def changed(ctx):\n    return replace(ctx, **{'space': {'space': 's'}})\n"),
    ("_vectors", "def changed(ctx):\n    return replace(ctx, **{'space': {'space': 's'}})\n"),
    ("_paths", "from . import _contracts\ndef made(root):\n    return _contracts.WarmContext(root=root, space={'space': 's'})\n"),
    ("_scopes", 'from . import _vectors\ndef elsewhere(ctx, plan):\n    return _vectors.VECTORS.network(ctx, plan)\n'),
])
def test_package_warm_context_rejects_unproved_identity_and_forwarding(leaf, change):
    sources = _package_sources()
    name = f"{WARM_MODULE}.{leaf}"
    text, package = sources[name]
    sources[name] = (text + change, package)
    assert _problems(sources)


@pytest.mark.parametrize("before, after", [
    ("_vectors.VECTORS)", "_files.FILES)"),
    ("space=embed_space.endpoint()", "space={'space': 's'}"),
    ("hook.network(ctx, plan)", "hook.network(SimpleNamespace(space={'space': 's'}), plan)"),
    ("HOOKS = (_files.FILES, _other_hooks.OVERVIEW, _other_hooks.SEARCHDOCS, _vectors.VECTORS)",
     "HOOKS = registered()"),
])
def test_package_warm_context_rejects_wrong_tuple_source_and_argument(before, after):
    sources = _package_sources()
    name = f"{WARM_MODULE}._engine"
    text, package = sources[name]
    assert before in text
    sources[name] = (text.replace(before, after), package)
    assert _problems(sources)


def test_package_warm_context_rejects_missing_owner():
    sources = _package_sources()
    del sources[f"{WARM_MODULE}._files"]
    assert _problems(sources)


def test_package_warm_context_rejects_an_unreviewed_extra_owner():
    sources = _package_sources()
    sources[f"{WARM_MODULE}._rogue"] = ("def changed(ctx):\n    return replace(ctx, **{'space': {'space': 's'}})\n", False)
    assert _problems(sources)


def test_package_warm_context_rejects_aliased_construction_in_another_owner():
    sources = _package_sources()
    sources[f"{WARM_MODULE}._paths"] = ("from ._contracts import WarmContext as Ctx\ndef made(root):\n    return Ctx(root=root, space={'space': 's'})\n", False)
    assert _problems(sources)


def test_package_warm_context_rejects_a_rebound_facade_constructor():
    sources = _package_sources()
    text, package = sources[WARM_MODULE]
    sources[WARM_MODULE] = (text + "WarmContext = unknown\n", package)
    name = f"{WARM_MODULE}._engine"
    text, package = sources[name]
    text = text.replace("from .. import embed_space", "from .. import embed_space\nfrom . import WarmContext")
    sources[name] = (text.replace("_contracts.WarmContext", "WarmContext"), package)
    assert _problems(sources)
