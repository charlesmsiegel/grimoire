from grimoire.llm_errors import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"


from grimoire.llm import LLMClient


class FakeProvider:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    async def stream(self, messages, *args, **kwargs):
        self.calls.append((args, kwargs))
        yield self.tag


def _conn(kind, **fields):
    return {"kind": kind, "model": "m", "api_key": "k", "base_url": "", "post_process": "none", **fields}


async def test_dispatches_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openrouter", model="or-model", api_key="sk-or-x")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["or"]
    assert op.calls == [(("or-model", "sk-or-x"), {})]
    assert cl.calls == [] and oc.calls == []


async def test_dispatches_to_claude():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="opus")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["cl"]
    assert cl.calls == [(("opus",), {})]
    assert op.calls == [] and oc.calls == []


async def test_claude_missing_model_defaults_to_opus():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="")
    [c async for c in client.stream([], conn)]
    assert cl.calls == [(("opus",), {})]


async def test_dispatches_to_openai_compatible_with_strict_flag():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", model="glm-4.6", api_key="sk-z",
                 base_url="https://api.z.ai/v4", post_process="strict")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["oc"]
    assert oc.calls == [(("glm-4.6", "sk-z", "https://api.z.ai/v4"), {"strict": True})]


async def test_openai_compatible_none_post_process_is_not_strict():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", post_process="none")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1] == {"strict": False}


async def test_missing_kind_defaults_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = {"model": "or-model", "api_key": "sk-or-x"}  # defensive: no kind key at all
    assert [c async for c in client.stream([], conn)] == ["or"]


import ast
from pathlib import Path

import grimoire

# The LLM gateway: the facade plus the three providers it dispatches to, and the
# error module they all share.
GATEWAY_MODULES = ("llm", "llm_errors", "openrouter", "claude_agent", "openai_compatible")


def _sibling_imports(name: str) -> set[str]:
    """Names in the grimoire package that `name` imports.

    Deliberately counts function-scope imports too: deferring an import into a
    function is how an import cycle gets worked around, so a check that ignored
    them would call the workaround "acyclic" and let the cycle back in.
    """
    src = Path(grimoire.__file__).with_name(f"{name}.py").read_text(encoding="utf-8")
    found: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                found.add(node.module)
            else:  # `from . import x`
                found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".", 1)[1] for a in node.names
                         if a.name.startswith("grimoire."))
    return found


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    done: set[str] = set()

    def walk(node: str, path: list[str]) -> list[str] | None:
        if node in path:
            return path[path.index(node):] + [node]
        if node in done:
            return None
        for nxt in sorted(graph.get(node, ())):
            if cycle := walk(nxt, path + [node]):
                return cycle
        done.add(node)
        return None

    for start in graph:
        if cycle := walk(start, []):
            return cycle
    return None


def test_llm_gateway_imports_are_acyclic():
    """Regression for #239: LLMError lives in its own leaf module so that
    llm.py can import the providers at module scope."""
    graph = {m: _sibling_imports(m) & set(GATEWAY_MODULES) for m in GATEWAY_MODULES}
    cycle = _find_cycle(graph)
    assert cycle is None, "import cycle: " + " -> ".join(cycle or [])
