"""06-C1: operational skills stay attached to the interfaces they name.

Files own executable truth; the canonical skill owns guidance, and adapters
only enable discovery. These checks validate static references and document
boundaries, not agent judgment, arbitrary dynamic Python/shell programs, real
library privacy, or whether a host actually discovers a directory. Planted
broken fixtures prove a check rejects the defect it claims to detect.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import inspect
import io
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire import cache, main
from grimoire.store import cache_sync, frontmatter
from tests import test_docs_guard as docs_guard
from tests import test_install_scripts as install_scripts

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIR = ROOT / ".agents" / "skills"
ADAPTER_DIRS = (ROOT / ".claude" / "skills",)
# The operational vocabulary below belongs to outside-app store maintenance.
# Discovery and link checks cover every skill; these specialized checks must
# not impose cache-sync commands and result fields on unrelated procedures.
ROUTING_SKILLS = ("grimoire-store-editing",)
ADAPTER_MAX_BODY_LINES = 12


_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCES = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_FLAG = re.compile(r"(?<![\w/.-])(--[a-z][a-z0-9-]*)")
_COMMAND = re.compile(r"(?<!\S)-m\s+(grimoire(?:\.[a-z_]\w*)+)\b(.*)")
_GIT_FLAGS = {"--porcelain", "--untracked-files"}
_VOCABULARIES = {
    "Path status": "PATH_STATUSES", "Refusal reason": "REFUSAL_REASONS",
    "Kind outcome": "KIND_OUTCOMES", "Batch field": "BATCH_FIELDS",
    "Embedding field": "EMBEDDING_FIELDS", "Stop reason": "STOP_REASONS",
}


def _skill_parts(path: Path):
    text = path.read_text(encoding="utf-8")
    meta, body = frontmatter.parse_frontmatter(text)
    header = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
    return meta, body, header.group(1) if header else None


def _adapter_problems(adapter: Path, canonical: Path) -> list[str]:
    if not canonical.is_file():
        return [f"{adapter}: missing canonical {canonical}"]
    problems = []
    meta, body, header = _skill_parts(adapter)
    original, canonical_body, canonical_header = _skill_parts(canonical)
    if meta.get("name") != adapter.parent.name or original.get("name") != canonical.parent.name:
        problems.append(f"{adapter}: name must match its directory name")
    if not header or not canonical_header or not meta.get("description") or header != canonical_header:
        problems.append(f"{adapter}: frontmatter differs from {canonical}")
    if len(body.splitlines()) > ADAPTER_MAX_BODY_LINES:
        problems.append(f"{adapter}: body exceeds {ADAPTER_MAX_BODY_LINES} lines")
    targets = [ref.split("#", 1)[0] for ref in docs_guard._refs(body)
               if not ref.startswith(("https://", "http://", "mailto:", "#"))]
    if not any((adapter.parent / ref).resolve() == canonical.resolve() for ref in targets):
        problems.append(f"{adapter}: missing resolving canonical link")
    if {child.name for child in adapter.parent.iterdir()} != {"SKILL.md"}:
        problems.append(f"{adapter.parent}: may contain only SKILL.md")
    shared, run = docs_guard._longest_shared_run(body, canonical_body)
    if shared > docs_guard.MAX_SHARED_RUN:
        problems.append(f"{adapter}: copies canonical prose ({shared} words): {run}")
    return problems + _link_problems(adapter, body)


def _pair_problems(canonical_dir: Path, adapter_dirs: tuple[Path, ...],
                   routing_skills: tuple[str, ...]) -> list[str]:
    problems = [f"{canonical_dir / name}: missing canonical" for name in routing_skills
                if not (canonical_dir / name / "SKILL.md").is_file()]
    for directory in adapter_dirs:
        problems.extend(f"{directory / name}: missing adapter" for name in routing_skills
                        if not (directory / name / "SKILL.md").is_file())
        for adapter in directory.glob("*/SKILL.md"):
            problems.extend(_adapter_problems(adapter, canonical_dir / adapter.parent.name / "SKILL.md"))
    return problems


def _link_problems(path: Path, text: str) -> list[str]:
    problems = []
    for ref in docs_guard._refs(text):
        if ref.startswith(("https://", "http://", "mailto:")):
            continue
        rel, _, anchor = ref.partition("#")
        target = Path(os.path.abspath(path.parent / rel)) if rel else path
        # Unlike the old docs reader, the new skill has no legitimate reason
        # to read headings outside the checkout. Bound it before opening one.
        if not target.resolve().is_relative_to(docs_guard.ROOT.resolve()):
            problems.append(f"{path}: relative link outside repository: {ref}")
            continue
        if not target.exists():
            problems.append(f"{path}: missing path or case-mismatched target: {ref}")
        elif not docs_guard._spelled_exactly(target):
            problems.append(f"{path}: link case does not match the target: {ref}")
        elif anchor and (not target.is_file() or anchor not in docs_guard._anchors(
                text if target == path else target.read_text(encoding="utf-8"))):
            problems.append(f"{path}: missing anchor: {ref}")
    return problems


def _reference_problems(directory: Path) -> list[str]:
    """Follow the skill's Markdown graph, including conditional deep references.

    A valid link in SKILL.md cannot establish that the next index still works.
    Inspect every page, and require each one to be discoverable from the entry
    point so a moved procedure cannot silently become an orphan. Other skills
    and repo documents keep their own inventories; only traverse this package.
    """
    documents = {path.resolve(): path.read_text(encoding="utf-8") for path in directory.rglob("*.md")}
    problems = [problem for path, text in documents.items() for problem in _link_problems(path, text)]
    pending = [(directory / "SKILL.md").resolve()]
    reached = set()
    while pending:
        path = pending.pop()
        if path in reached or path not in documents:
            continue
        reached.add(path)
        pending.extend((path.parent / ref.split("#", 1)[0]).resolve()
                       for ref in docs_guard._refs(documents[path])
                       if not ref.startswith(("https://", "http://", "mailto:")))
    problems.extend(f"unreachable reference: {path}" for path in sorted(documents.keys() - reached))
    return problems


def _sync_options() -> set[str]:
    parser = cache.build_parser()
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return {option for action in sub.choices["sync"]._actions for option in action.option_strings}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _store_value(spelling: str):
    if spelling == "grimoire.store":
        return importlib.import_module(spelling)
    parts = spelling.removeprefix("grimoire.store.").split(".")
    # The facade exports functions/constants as well as submodules. Resolve
    # that public binding first, then an importable module not exposed there.
    try:
        value = getattr(importlib.import_module("grimoire.store"), parts[0])
    except AttributeError:
        value = importlib.import_module("grimoire.store." + parts[0])
    for part in parts[1:]:
        value = getattr(value, part)
    return value


def _store_bindings(tree: ast.AST) -> dict[str, str]:
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("grimoire.store"):
            for alias in node.names:
                bindings[alias.asname or alias.name] = node.module + "." + alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("grimoire.store") and alias.asname:
                    bindings[alias.asname] = alias.name
    return bindings


def _store_spelling(expression: ast.AST, bindings: dict[str, str], *, inline: bool) -> str:
    spelling = _name(expression)
    first, _, rest = spelling.partition(".")
    if first in bindings:
        return ".".join(part for part in (bindings[first], rest) if part)
    if spelling.startswith("grimoire.store"):
        return spelling
    if inline and "." in spelling and not spelling.endswith((".md", ".txt", ".json", ".sqlite", ".vec")):
        return spelling  # bare inline dotted names identify store interfaces
    return ""  # local variables and non-store calls are outside this check


def _store_import_problems(bindings: dict[str, str]) -> list[str]:
    problems = []
    # An unused import can already be stale; alias calls are not its only
    # evidence. Resolve imports without executing the documented snippet.
    for spelling in bindings.values():
        try:
            _store_value(spelling)
        except (ImportError, AttributeError) as error:
            problems.append(f"missing store reference {spelling}: {type(error).__name__}")
    return problems


def _python_problems(code: str, *, inline: bool = False) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        if inline:
            return []  # inline shell fragments and record paths are not Python
        return [f"invalid Python example: {error.msg}"]
    bindings = _store_bindings(tree)
    problems = _store_import_problems(bindings)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Attribute)):
            continue
        expression = node.func if isinstance(node, ast.Call) else node
        spelling = _store_spelling(expression, bindings, inline=inline)
        if not spelling:
            continue
        try:
            value = _store_value(spelling)
        except (ImportError, AttributeError) as error:
            problems.append(f"missing store reference {spelling}: {type(error).__name__}")
            continue
        if isinstance(node, ast.Call):
            keywords = {kw.arg: None for kw in node.keywords if kw.arg is not None}
            try:
                inspect.signature(value).bind_partial(**keywords)
            except (TypeError, ValueError) as error:
                problems.append(f"{spelling}: invalid call keywords {sorted(keywords)}: {error}")
    return problems


def _command_problems(body: str) -> list[str]:
    problems = []
    # Join continuations before parsing so later flags keep their owner.
    joined = re.sub(r"\\\r?\n\s*", " ", body)
    for line in joined.splitlines():
        match = _COMMAND.search(line)
        if match:
            problems.extend(_command_arguments(*match.groups()))
    return problems


def _command_arguments(module: str, tail: str) -> list[str]:
    try:
        entry = importlib.import_module(module)
    except ImportError:
        return [f"unimportable command module {module}"]
    try:
        args = shlex.split(tail, comments=True)
        if module == "grimoire.where":
            return [f"{module} accepts no arguments: {args}"] if args else []
        if not callable(getattr(entry, "build_parser", None)):
            return [f"no command syntax validator for {module}"]
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            entry.build_parser().parse_args(args)
    except (ValueError, SystemExit) as error:
        if not isinstance(error, SystemExit) or error.code != 0:
            return [f"invalid command {module} {tail.strip()}"]
    return []


def _code_problems(text: str) -> list[str]:
    problems = []
    fragments = _INLINE.findall(text) + [body for _, body in _FENCES.findall(text)]
    options = _sync_options() | _GIT_FLAGS
    for fragment in fragments:
        problems.extend(f"unknown sync flag {flag}" for flag in _FLAG.findall(fragment) if flag not in options)
        problems.extend(_command_problems(fragment))
    for language, body in _FENCES.findall(text):
        if language.strip() == "python":
            problems.extend(_python_problems(body))
    for fragment in _INLINE.findall(text):
        problems.extend(_python_problems(fragment, inline=True))
    references = set(re.findall(r"/api/[\w/{}/.-]+", text))
    if references:
        # FastAPI defers included routers behind wrapper nodes. Its public
        # schema applies their prefixes when enumerating documented paths.
        routes = set(main.create_app().openapi()["paths"])
        problems.extend(f"missing API route {ref}" for ref in sorted(references - routes))
    return list(dict.fromkeys(problems))


def _result_prose_problems(text: str, known: set[str]) -> list[str]:
    result = re.search(r"^## Read the result\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not result:
        return ["missing result section for prose vocabulary validation"]
    problems = []
    for code in _INLINE.findall(result.group(1)):
        if code.startswith("--") and code.split("=", 1)[0] in _sync_options():
            continue
        if code == "cache: off":
            continue  # the renderer's off-state label, rather than a field
        if code not in known:
            problems.append(f"unknown report name in result prose: {code}")
    return problems


def _vocabulary_problems(text: str) -> list[str]:
    vocab = {label: set(getattr(cache_sync, name)) for label, name in _VOCABULARIES.items()}
    seen = set()
    problems = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] not in vocab:
            continue
        names = _INLINE.findall(cells[1])
        problems.extend(f"unknown {cells[0]}: {name}" for name in names if name not in vocab[cells[0]])
        if cells[0] == "Path status":
            seen.update(names)
    problems.extend(f"undocumented path status: {name}" for name in sorted(vocab["Path status"] - seen))
    # Table coverage cannot excuse a misspelled field in nearby prose.
    return problems + _result_prose_problems(text, set().union(*vocab.values()))


def _routing_problems(text: str, others: tuple[tuple[str, str], ...]) -> list[str]:
    problems = []
    for name, other in others:
        shared, run = docs_guard._longest_shared_run(text, other)
        if shared > docs_guard.MAX_SHARED_RUN:
            problems.append(f"copies {name} ({shared} words): {run}")
    return problems


def _diagnose(name, *args):
    # A missing check is a readable RED failure while its tests are written;
    # once implemented, every assertion below examines the actual diagnostic.
    check = globals().get(name)
    assert callable(check), f"{name} must validate the documented boundary"
    return check(*args)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def skill_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_guard, "ROOT", tmp_path)
    canonical_dir = tmp_path / ".agents" / "skills"
    adapter_dir = tmp_path / ".claude" / "skills"
    canonical = canonical_dir / "grimoire-store-editing" / "SKILL.md"
    adapter = adapter_dir / "grimoire-store-editing" / "SKILL.md"
    header = "---\nname: grimoire-store-editing\ndescription: Use when editing records outside the app.\n---\n\n"
    _write(tmp_path / "AGENTS.md", "# Privacy\n\nPrivate content stays outside the repository.\n")
    _write(canonical, header + "# Store editing\n\nRead [privacy](../../../AGENTS.md#privacy).\n")
    _write(adapter, header + "# Store editing\n\nRead [the procedure](../../../.agents/skills/grimoire-store-editing/SKILL.md).\n")
    return canonical_dir, adapter_dir, canonical, adapter


def test_required_skill_files_exist():
    names = [path.name for path in CANONICAL_DIR.iterdir() if path.is_dir()]
    assert names, "there are no authoritative skills"
    for name in names:
        assert (CANONICAL_DIR / name / "SKILL.md").is_file(), f"missing canonical skill: {name}"
        for directory in ADAPTER_DIRS:
            assert (directory / name / "SKILL.md").is_file(), f"missing discovery adapter: {name}"


def test_repository_skills_obey_the_contract():
    names = tuple(path.name for path in CANONICAL_DIR.iterdir() if path.is_dir())
    problems = _diagnose("_pair_problems", CANONICAL_DIR, ADAPTER_DIRS, names)
    for name in names:
        problems += _diagnose("_reference_problems", CANONICAL_DIR / name)
    other_skills = tuple((str(p), p.read_text(encoding="utf-8")) for p in CANONICAL_DIR.glob("*/SKILL.md")
                         if p.parent.name not in ROUTING_SKILLS)
    prose = tuple((str(p), p.read_text(encoding="utf-8")) for p in docs_guard.PROSE)
    for name in ROUTING_SKILLS:
        path = CANONICAL_DIR / name / "SKILL.md"
        if not path.is_file():
            continue
        text = "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(path.parent.rglob("*.md")))
        problems += _diagnose("_code_problems", text)
        problems += _diagnose("_vocabulary_problems", text)
        problems += _diagnose("_routing_problems", text, prose + other_skills)
    assert not problems, "\n".join(problems)


def test_generated_discovery_entries_are_current():
    """The generator is also the exact-content check, so new skills need no
    second hand-maintained inventory and short handwritten workflows cannot
    hide inside otherwise valid discovery entries."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_skills.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_valid_adapter_pair_passes(skill_tree):
    canonical_dir, adapter_dir, _, _ = skill_tree
    assert _diagnose("_pair_problems", canonical_dir, (adapter_dir,), ROUTING_SKILLS) == []


@pytest.mark.parametrize("defect,want", [
    ("description", "frontmatter"),
    ("raw_description", "frontmatter"),
    ("missing_adapter", "missing adapter"),
    ("missing_canonical", "missing canonical"),
    ("name", "directory name"),
    ("long_body", "12 lines"),
    ("bad_pointer", "canonical link"),
    ("extra_resource", "only SKILL.md"),
    ("orphan_adapter", "missing canonical"),
    ("copied_body", "canonical prose"),
])
def test_adapter_defects_are_rejected(skill_tree, defect, want):
    canonical_dir, adapter_dir, canonical, adapter = skill_tree
    text = adapter.read_text(encoding="utf-8")
    replacements = {
        "description": ("records", "recordx"),
        "raw_description": ("outside the app.\n", "outside the app. \n"),
        "name": ("name: grimoire-store-editing", "name: store-editing"),
        "bad_pointer": ("../../../.agents/", "../../.agents/"),
    }
    if defect in replacements:
        adapter.write_text(text.replace(*replacements[defect]), encoding="utf-8")
    elif defect == "missing_adapter":
        adapter.unlink()
    elif defect == "missing_canonical":
        canonical.unlink()
    elif defect == "long_body":
        header = text.split("---", 2)[1]
        adapter.write_text("---" + header + "---\n" + "line\n" * 13, encoding="utf-8")
    elif defect == "extra_resource":
        _write(adapter.parent / "copy.py", "# copied resource\n")
    elif defect == "orphan_adapter":
        _write(adapter_dir / "verify" / "SKILL.md", text.replace("grimoire-store-editing", "verify"))
    elif defect == "copied_body":
        paragraph = "One source defines the procedure so a second maintained copy cannot silently teach a different editing rule."
        canonical.write_text(canonical.read_text(encoding="utf-8") + paragraph, encoding="utf-8")
        adapter.write_text(text + paragraph, encoding="utf-8")
    problems = _diagnose("_pair_problems", canonical_dir, (adapter_dir,), ROUTING_SKILLS)
    assert any(want in p for p in problems), problems


def test_relative_links_and_anchors_pass(skill_tree):
    _, _, canonical, _ = skill_tree
    assert _diagnose("_link_problems", canonical, canonical.read_text(encoding="utf-8")) == []


def test_nested_skill_references_are_reachable_and_checked(skill_tree):
    _, _, canonical, _ = skill_tree
    canonical.write_text(canonical.read_text(encoding="utf-8") + "\n[Choose a task](references/index.md).\n",
                         encoding="utf-8")
    _write(canonical.parent / "references" / "index.md", "# Tasks\n\n[Sync](sync/result.md#result).\n")
    leaf = _write(canonical.parent / "references" / "sync" / "result.md", "# Result\n\nRead the report.\n")
    assert _diagnose("_reference_problems", canonical.parent) == []

    leaf.write_text("# Result\n\n[Missing heading](../index.md#missing).\n", encoding="utf-8")
    problems = _diagnose("_reference_problems", canonical.parent)
    assert any("missing anchor" in problem for problem in problems), problems


def test_unlinked_reference_is_not_hidden_from_discovery(skill_tree):
    _, _, canonical, _ = skill_tree
    _write(canonical.parent / "references" / "forgotten.md", "# Forgotten procedure\n")
    problems = _diagnose("_reference_problems", canonical.parent)
    assert any("unreachable reference" in problem and "forgotten.md" in problem for problem in problems), problems


@pytest.mark.parametrize("target,want", [
    ("../../../missing.md", "missing path"),
    ("../../../AGENTS.md#missing", "missing anchor"),
    ("../../../agents.md#privacy", "case"),
    ("#missing", "missing anchor"),
])
def test_link_defects_are_rejected(skill_tree, target, want):
    _, _, canonical, _ = skill_tree
    problems = _diagnose("_link_problems", canonical, f"Read [the rules]({target}).")
    assert any(want in p for p in problems), problems


def test_supported_code_references_pass():
    text = r"""Use `--dry-run`, `--no-embed` and `--verify`.
```sh
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.where
PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync \
    worlds/realm/lore/tidewatch.md --no-embed
 git -C /scratch status --porcelain=v1 -z --untracked-files=all
```
```python
from grimoire.store import cache_sync as sync, voice_anchors as voices, home, DEFAULT_MODEL, parse_frontmatter
with sync.collecting(embed=False, deleted=[]) as collected:
    voices.write(root, "seraphine", "A placeholder anchor.")
```
`cache_sync.collecting(embed=False, deleted=[])` and `voice_anchors.write`.
The HTTP door is `POST /api/cache/sync`.
"""
    assert _diagnose("_code_problems", text) == []


@pytest.mark.parametrize("text,want", [
    ("Use `--imaginary-option`.", "--imaginary-option"),
    ("```sh\npython -m grimoire.cache sync \\\n  worlds/realm/lore/tidewatch.md --imaginary-option\n```", "--imaginary-option"),
    ("```sh\npython -m grimoire.cache doctor --all\n```", "doctor"),
    ("```sh\npython -m grimoire.where --no-embed\n```", "grimoire.where"),
    ("```sh\npython -m grimoire.missing_module\n```", "grimoire.missing_module"),
    ("Use `python -m grimoire.missing_module`.", "grimoire.missing_module"),
    ("Use `entities.missing_writer`.", "entities.missing_writer"),
    ("```python\nfrom grimoire.store import missing_module\n```", "missing_module"),
    ("```python\nimport grimoire.store as store\nstore.entities.missing_writer()\n```", "missing_writer"),
    ("Use `cache_sync.collecting(unknown=True)`.", "unknown"),
    ("```python\ncache_sync.collecting(unknown=)\n```", "invalid Python example"),
    ("```python\nfrom grimoire.store import cache_sync as sync\nwith sync.collecting(unknown=True):\n    pass\n```", "unknown"),
    ("Use `POST /api/no-such-route`.", "/api/no-such-route"),
])
def test_code_reference_defects_are_rejected(text, want):
    problems = _diagnose("_code_problems", text)
    assert any(want in p for p in problems), problems


RESULTS = """## Read the result

| Vocabulary | Names | Meaning |
|---|---|---|
| Path status | `refreshed`, `current`, `cold`, `deleted`, `missing`, `refused`, `failed`, `verify_failed`, `unsynced` | Interpret every path. |
| Refusal reason | `link` | Unsafe traversal. |
| Kind outcome | `built` | A derived value was rebuilt. |
| Batch field | `tokens_failed`, `stopped`, `truncated` | The batch may be partial. |
| Embedding field | `deferred` | Work left for later. |
| Stop reason | `cancelled` | The caller stopped it. |

Report `tokens_failed` and `stopped`; do not infer completion from successful paths.

## Check one reader
Read back the changed record.
"""


def test_current_report_vocabulary_passes():
    assert _diagnose("_vocabulary_problems", RESULTS) == []


@pytest.mark.parametrize("old,new,want", [
    ("`refreshed`", "`refreshd`", "refreshd"),
    ("`, `unsynced`", "`", "unsynced"),
    ("`link`", "`unknown_reason`", "unknown_reason"),
    ("`built`", "`unknown_outcome`", "unknown_outcome"),
    ("`tokens_failed`,", "`unknown_field`,", "unknown_field"),
    ("`deferred`", "`unknown_embedding`", "unknown_embedding"),
    ("`cancelled`", "`unknown_stop`", "unknown_stop"),
    ("Report `tokens_failed`", "Report `tokens_falied`", "tokens_falied"),
])
def test_report_vocabulary_defects_are_rejected(old, new, want):
    text = RESULTS.replace(old, new, 1)
    problems = _diagnose("_vocabulary_problems", text)
    assert any(want in p for p in problems), problems


def test_long_copied_prose_is_rejected_but_fences_are_not():
    copied = "The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by default. The root"
    other = (("AGENTS.md", (ROOT / "AGENTS.md").read_text(encoding="utf-8")),)
    assert any("AGENTS.md" in p for p in _diagnose("_routing_problems", copied, other))
    assert _diagnose("_routing_problems", "```text\n" + copied + "\n```", other) == []


def test_description_participates_in_the_routing_check():
    copied = "The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by default. The root"
    text = "---\nname: grimoire-store-editing\ndescription: " + copied + "\n---\n\nShort body."
    other = (("AGENTS.md", (ROOT / "AGENTS.md").read_text(encoding="utf-8")),)
    assert _diagnose("_routing_problems", text, other)


def test_installer_rejects_a_unix_only_skill_command(tmp_path):
    doc = _write(tmp_path / "SKILL.md", "Run backend/.venv/bin/python -m grimoire.where.\n")
    with pytest.raises(AssertionError, match="Unix-only"):
        install_scripts.test_venv_paths_are_given_for_both_platforms(doc)
