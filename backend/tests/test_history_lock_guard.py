"""The lexical turn-path guard rejects retrieval inside a campaign hold."""

import ast
from pathlib import Path


def violations(source):
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not any(isinstance(i.context_expr, ast.Call) and ((isinstance(i.context_expr.func, ast.Attribute) and i.context_expr.func.attr == "campaign_lock")
                        or (isinstance(i.context_expr.func, ast.Name) and i.context_expr.func.id == "campaign_lock")) for i in node.items):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = child.func.attr if isinstance(child.func, ast.Attribute) else child.func.id if isinstance(child.func, ast.Name) else ""
                if name in {"retrieve", "gather", "gather_sync", "gather_view", "gather_view_sync",
                             "classify", "classify_evidence"}:
                    found.append(child.lineno)
    return found


def test_guard_rejects_retrieval_under_lock():
    assert violations("with store.locks.campaign_lock(cid):\n    await history.retrieve(cid, sid, query)\n") == [2]
    assert not violations("with store.locks.campaign_lock(cid):\n    prepare()\nawait history.retrieve(cid, sid, query)\n")
    # 11-S3's doors: a step's view, and the classification behind it.
    assert violations("with campaign_lock(cid):\n    gather_view_sync(app, cid, sid)\n") == [2]
    assert violations("with campaign_lock(cid):\n    epistemic.classify(p, items)\n") == [2]


def test_routes_never_retrieve_under_campaign_lock():
    root = Path(__file__).parents[1] / "src" / "grimoire" / "routes"
    assert not _violating_routes(root)


def _violating_routes(root):
    return [(p.relative_to(root).as_posix(), found) for p in root.rglob("*.py")
            if (found := violations(p.read_text(encoding="utf-8")))]


def test_route_scan_reaches_new_package_leaves(tmp_path):
    leaf = tmp_path / "scenes" / "_unexpected.py"
    leaf.parent.mkdir()
    leaf.write_text("with locks.campaign_lock(cid):\n    history.retrieve(cid)\n", encoding="utf-8")
    assert _violating_routes(tmp_path) == [("scenes/_unexpected.py", [2])]
