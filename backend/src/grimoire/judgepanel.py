"""Independent judge-panel calibration for 069: report-only, never a blocking grade.

Three pinned judges answer every labelled synthetic case in at least three fresh trials. A judge's
answer counts only when it proves it read the listing: each listing carries one unnumbered
coverage canary that the answer must echo. Missing, unparseable, refused, wrongly-served or
uncovered answers are ``unknown``; they never become ``clear`` and never earn a score. A panel
defect verdict is a *candidate*; a corpus label is ``confirmed`` only by an executed behavioral
check in the test suite, never by a judge.

Model calls are billed. ``capture`` refuses to run without ``--confirm-billing``, a stated spend
cap and a call ceiling covering the plan; ``plan`` and ``score`` make no call. See
``docs/CALIBRATION.md``.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import uuid

from grimoire.calibration import REVISION as RUBRIC_REVISION, verify_checkout

FORMAT = 'grimoire.judge-panel/1'
DEFECT_SCHEMA = 'grimoire.defect-verdict/1'
THEORY_SCHEMA = 'theory-verdict/1'
PANEL_SIZE = 3
MIN_TRIALS = 3
TASKS = ('defect', 'theory')
DIMENSIONS = ('absorption', 'world_mapping', 'abstraction', 'justification', 'honest_limits')
LADDER = (0.0, 0.25, 0.5, 1.0)
SPEND_CAP = re.compile(r'(0|[1-9]\d{0,5})(\.\d{1,2})? [A-Z]{3}')
_SNAPSHOT = re.compile(r'-(\d{8}|\d{4}-\d{2}-\d{2})$')

DEFECT_PROMPT = """You are one of three independent code reviewers. You see no other reviewer's work.
Report only actionable defects: code that, given its stated intent, behaves incorrectly when run.
Structure, style, size or a missing abstraction is not a defect. A deliberate, documented choice
is not a defect. If you cannot tell, answer cannot_assess rather than guessing.

Each listing line starts with its line number. One unnumbered line holds a coverage canary;
copy its token exactly. Answer with one JSON object and nothing else:
{"schema": "grimoire.defect-verdict/1",
 "files": [{"file": "<file name>", "canary": "<token>"}],
 "verdict": "defect" | "no_defect" | "cannot_assess",
 "findings": [{"line": <line number>, "explanation": "<what goes wrong when it runs>"}]}
"findings" is empty unless the verdict is "defect"."""

THEORY_PROMPT = """You are one of three independent judges. You see no other judge's work.
Judge whether this code expresses a coherent theory of its problem, in Naur's sense.
Grade five dimensions on the ladder 1.0 holds, 0.5 partial, 0.25 strained, 0.0 absent:
absorption (do 3-5 plausible next requirements land as extensions or patches?),
world_mapping (can each part be mapped to the problem and back?),
abstraction (domain names, unwritten cases expressible, less to hold in mind?),
justification (is it recoverable why each part is what it is?),
honest_limits (are unpinned assumptions and duplication named honestly?).
A step below 1.0 must cite evidence as "<file>:<line>". Give at least three rehearsals.

Each listing line starts with its line number. One unnumbered line holds a coverage canary;
copy its token exactly. Answer with one JSON object and nothing else:
{"schema": "theory-verdict/1", "unit": "<unit>",
 "files": [{"file": "<file name>", "canary": "<token>"}],
 "theory": "<at most 3 sentences>", "instead_of": "<the other reading and why not>",
 "trivial": false, "trivial_reason": "",
 "dimensions": {"<dimension>": {"step": <step>, "rationale": "...", "evidence": ["<file>:<line>"]}},
 "rehearsals": [{"requirement": "...", "verdict": "extension" | "patch", "why": "..."}]}"""

PROMPT_VERSION = hashlib.sha256((DEFECT_PROMPT + '\0' + THEORY_PROMPT).encode()).hexdigest()

# Sent once, in the same conversation, to a theory judge whose answer had only these faults: a step
# off the ladder, or a lowered step none of whose citations is a listed line. It names the dimensions
# and restates the rule, never which rung or line to pick. The text is in each re-ask record's prompt
# digest, so a capture made with other wording is refused as stale. A re-ask for the ladder alone is
# word for word what parser version 3 sent, so its captures still replay.
REASK_LADDER = """Your answer gave {dimensions} a step that is not on the ladder. Every step must be
exactly one of 1.0 (holds), 0.5 (partial), 0.25 (strained) or 0.0 (absent); there is no step between
them."""
REASK_CITATION = """Your answer gave {dimensions} a step below 1.0 with no citation of a line the listing
has. Each citation must be exactly "{file}:<line>", where <line> is one line number the listing shows."""
REASK_MALFORMED = f'Your answer held no JSON object with "schema": "{THEORY_SCHEMA}" that could be read.'
REASK_END = 'Answer again with one complete JSON object in the same format, and nothing else.'
REASK_PROMPT = REASK_LADDER + ' ' + REASK_END


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class JudgePin:
    """Everything that decides what a judge is. A served model other than ``model`` voids an answer."""

    judge_id: str
    provider_id: str
    base_url: str
    model: str
    temperature: float | None
    max_output_tokens: int
    attempts: int
    max_tokens_field: str = 'max_tokens'

    def record(self) -> dict:
        return {'judge_id': self.judge_id, 'provider_id': self.provider_id, 'base_url': self.base_url,
                'model': self.model, 'temperature': self.temperature,
                'max_output_tokens': self.max_output_tokens, 'attempts': self.attempts,
                'max_tokens_field': self.max_tokens_field, 'prompt_version': PROMPT_VERSION}

    @property
    def sha256(self) -> str:
        return _digest(self.record())

    def serves(self, served: str | None) -> bool:
        """The pinned model, or a dated snapshot of it; nothing else."""
        if not served:
            return False
        return served == self.model or (served.startswith(self.model + '-') and
                                        _SNAPSHOT.fullmatch(served[len(self.model):]) is not None)


def load_panel(path: Path) -> tuple[list[JudgePin], int, dict]:
    """``{"trials": n, "judges": [{"judge_id", "temperature", "model_config": {...}}] * 3}``."""
    from grimoire.app.config import ConfigError, parse_model_config

    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or set(raw) - {'trials', 'judges'}:
        raise ValueError('panel must be an object with trials and judges')
    trials = raw.get('trials')
    if type(trials) is not int or not MIN_TRIALS <= trials <= 10:
        raise ValueError(f'trials must be a whole number from {MIN_TRIALS} to 10')
    judges = raw.get('judges')
    if not isinstance(judges, list) or len(judges) != PANEL_SIZE:
        raise ValueError(f'a panel is exactly {PANEL_SIZE} judges')
    pins, configs = [], {}
    for judge in judges:
        if not isinstance(judge, dict) or set(judge) - {'judge_id', 'temperature', 'model_config'}:
            raise ValueError('each judge has judge_id, temperature and model_config only')
        judge_id = judge.get('judge_id')
        if (not isinstance(judge_id, str) or not judge_id.isascii() or not judge_id.isidentifier()
                or judge_id in configs):
            raise ValueError('judge ids must be distinct ASCII identifiers')
        temperature = judge.get('temperature')
        if temperature is not None and (type(temperature) not in (int, float) or not 0 <= temperature <= 2):
            raise ValueError('temperature must be null or a number from 0 to 2')
        try:
            config = parse_model_config(judge.get('model_config'))
        except ConfigError as error:
            raise ValueError(f'{judge_id}: {error}') from None
        if config.fallbacks:
            raise ValueError(f'{judge_id}: a pinned judge has no fallback models')
        configs[judge_id] = config
        pins.append(JudgePin(judge_id, config.endpoint.provider_id, config.endpoint.base_url, config.model,
                             None if temperature is None else float(temperature),
                             config.max_output_tokens, config.attempts, config.endpoint.max_tokens_field))
    return pins, trials, configs


def load_corpus(corpus: Path) -> list[dict]:
    """Read ``cases.json``: labelled synthetic cases, one source file each."""
    cases = json.loads((corpus / 'cases.json').read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not cases:
        raise ValueError('corpus must contain cases')
    seen = set()
    out = []
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'].isascii()
                or not case['id'].isidentifier() or case['id'] in seen or case.get('task') not in TASKS
                or not isinstance(case.get('file'), str) or Path(case['file']).name != case['file']
                or '\\' in case['file']):
            raise ValueError('invalid corpus case')
        seen.add(case['id'])
        if case['task'] == 'defect':
            label = case.get('actionable_defect', 'missing')
            lines = case.get('defect_lines', [])
            if type(label) not in (bool, type(None)) or not isinstance(lines, list) or \
                    not all(type(n) is int and n >= 1 for n in lines) or bool(lines) != (label is True):
                raise ValueError(f'{case["id"]}: a defect label needs exactly its defect_lines')
        path = corpus / case['file']
        if path.is_symlink():
            raise ValueError('corpus sources must be ordinary files')
        source_bytes = path.read_bytes()
        if any(n > len(source_bytes.decode('utf-8').splitlines()) for n in case.get('defect_lines', [])):
            raise ValueError(f'{case["id"]}: a defect line is past the end of its file')
        out.append({**case, 'source': source_bytes.decode('utf-8'),
                    'source_sha256': hashlib.sha256(source_bytes).hexdigest()})
    for case in out:
        if case.get('scores_above') is not None and not any(
                other['id'] == case['scores_above'] and other['task'] == 'theory' for other in out):
            raise ValueError(f'{case["id"]}: scores_above must name another theory case')
    return out


def canary(salt: str, judge_id: str, case_id: str, trial: int) -> str:
    return hashlib.sha256(f'{salt}:{judge_id}:{case_id}:{trial}'.encode()).hexdigest()[:16]


def alias(salt: str, case_id: str) -> str:
    """The file name a judge sees. Corpus ids and names describe the label, so they are never sent."""
    return 'unit_' + hashlib.sha256(f'{salt}:alias:{case_id}'.encode()).hexdigest()[:10] + '.py'


def listing(source: str, token: str) -> str:
    """Number every line and put the canary, unnumbered, in the middle, so cited lines stay true."""
    lines = source.splitlines()
    middle = len(lines) // 2
    out = [f'{n:>4} | {line}' for n, line in enumerate(lines, 1)]
    out.insert(middle, f'     | # coverage-canary: {token}')
    return '\n'.join(out)


def messages_for(case: dict, token: str, salt: str) -> list[tuple[str, str]]:
    system = DEFECT_PROMPT if case['task'] == 'defect' else THEORY_PROMPT
    name = alias(salt, case['id'])
    user = f'Unit: {name[:-3]}\nFile: {name}\n\n' + listing(case['source'], token)
    return [('system', system), ('user', user)]


def prompt_sha256(messages: list[tuple[str, str]]) -> str:
    return _digest(messages)


# Version 2 reads an answer's one schema-tagged object out of any text around it; version 1 needed the
# whole answer (or a fence holding it) to be that object. The prompts did not change, so a capture
# made under version 1 replays and is re-read under version 2. Version 3 re-asks a theory judge once
# when its answer puts a step off the ladder; a capture made before it has no re-asks and is read
# with those answers ``unknown`` (``off_ladder``), as version 2 read them. Version 4 also re-asks a
# covered answer whose lowered step kept no listed line, and reports the shape of each dropped
# citation; a version 3 capture has no such re-ask and is read with that answer ``citation``.
# Version 5 also re-asks a theory answer that held no readable object at all; a version 4 capture
# has no such re-ask and is read with that answer ``malformed``.
PARSER_VERSION = 5


def _decoded(text: str) -> dict | None:
    body = text.strip()
    if body.startswith('```'):
        body = body.split('\n', 1)[1] if '\n' in body else ''
        body = body[:body.rfind('```')] if body.rstrip().endswith('```') else body
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def extract(text: object, schema: str) -> tuple[dict | None, str | None, bool]:
    """The answer's one object tagged ``schema``: ``(object, reason it is unusable, prose before it)``.

    Prose before the object is tolerated and reported, never read, so a verdict stated in prose is
    never scored. Nothing but a closing fence may follow the object, because text after it can
    retract it. Any other mention of the schema (a second object, even a broken one, or the object
    inside another, even one left unclosed) means the judge may have changed its mind or is quoting
    an example: ``ambiguous``. So does a bracket left open before it. No object: ``malformed``.
    """
    if not isinstance(text, str):
        return None, 'malformed', False
    whole = _decoded(text)
    if whole is not None:
        return (whole, None, False) if whole.get('schema') == schema else (None, 'malformed', False)
    decoder, found, at = json.JSONDecoder(), [], text.find('{')
    while at != -1:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find('{', at + 1)
            continue
        if isinstance(value, dict) and value.get('schema') == schema:
            found.append((value, at, end))
        elif schema in text[at:end]:
            return None, 'ambiguous', True  # the answer only nested inside another object
        at = text.find('{', end)  # never inside an object already read
    if not found:
        return None, 'malformed', False
    value, start, end = found[-1]
    after, before = text[end:].strip(), text[:start]
    if len(found) > 1 or after not in ('', '```') or schema in before or _opens(before):
        return None, 'ambiguous', True
    return value, None, True


def _opens(text: str) -> bool:
    """Whether ``text`` leaves a bracket open: the object after it may sit inside a broken wrapper."""
    depth = 0
    for char in text:
        if char in '{[':
            depth += 1
        elif char in '}]' and depth:
            depth -= 1
    return depth > 0


def covered(data: dict, name: str, token: str) -> bool:
    files = data.get('files')
    return (isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)
            and files[0].get('file') == name and files[0].get('canary') == token)


def parse_defect(text: object, case: dict, token: str, name: str) -> dict:
    """``defect``/``clear`` only for a covered, self-consistent answer; anything else is unknown."""
    data, failure, outside = extract(text, DEFECT_SCHEMA)

    def unknown(reason: str) -> dict:
        return {'state': 'unknown', 'lines': [], 'reason': reason, 'text_outside_json': outside,
                'ambiguous': failure == 'ambiguous'}
    if data is None:
        return unknown(failure or 'malformed')
    if not covered(data, name, token):
        return unknown('coverage')
    verdict, findings = data.get('verdict'), data.get('findings')
    count = len(case['source'].splitlines())
    if verdict not in ('defect', 'no_defect', 'cannot_assess') or not isinstance(findings, list):
        return unknown('malformed')
    for finding in findings:
        if (not isinstance(finding, dict) or type(finding.get('line')) is not int
                or not 1 <= finding['line'] <= count or not isinstance(finding.get('explanation'), str)
                or not finding['explanation'].strip()):
            return unknown('malformed')
    if verdict == 'cannot_assess':
        return unknown('cannot_assess')
    if (verdict == 'defect') != bool(findings):
        return unknown('contradiction')
    return {'state': 'defect' if findings else 'clear',
            'lines': sorted({finding['line'] for finding in findings}), 'reason': None,
            'text_outside_json': outside, 'ambiguous': False}


def _in_listing(line: str, count: int) -> bool:
    """Whether ASCII digits ``line`` name a line of a ``count``-line listing. Never turns a number
    longer than the listing's into an int, which a judge can make long enough to raise."""
    digits = line.lstrip('0') or '0'
    return len(digits) <= len(str(count)) and 1 <= int(digits) <= count


def _listed(cite: object, name: str, count: int) -> bool:
    """``<file>:<line>`` on a line the listing has."""
    file, _, line = cite.rpartition(':') if isinstance(cite, str) else ('', '', '')
    return file == name and line.isascii() and line.isdigit() and _in_listing(line, count)


_RANGE = re.compile(r'\d+\s*-\s*\d+')


def citation_shape(cite: object, name: str, count: int) -> str:
    """What a dropped citation looks like, for the report: never its text, only its kind."""
    if not isinstance(cite, str):
        return 'not_text'
    file, colon, line = cite.rpartition(':')
    if not colon:
        return 'no_line'
    if file.strip() != name:
        return 'other_file'
    bare = line.strip()
    if bare.isascii() and _RANGE.fullmatch(bare):
        return 'range'
    if not (bare.isascii() and bare.isdigit()):
        return 'malformed'
    if not _in_listing(bare, count):
        return 'unlisted_line'
    # A listed line of the right file, kept from the listing only by a space around one of them.
    return 'spacing'


def _curable(key: str, entry: dict) -> bool:
    """Whether an uncited step is one a re-ask may cure: a rubric dimension, graded on a number
    below 1.0, whose evidence holds something the pinned rubric would count as cited."""
    step = entry.get('step')
    return (key in DIMENSIONS and type(step) in (int, float) and step < 1.0
            and any(str(cite).strip() for cite in entry.get('evidence', [])))


def _grounded(dimensions: dict, name: str, count: int) -> tuple[dict | None, int, list[str], bool, dict]:
    """Each dimension with only its citations of listed lines, how many were dropped, which steps
    kept none, whether a re-ask may cure every one of them, and the shape of each dropped citation.

    A citation of a line the listing lacks supports nothing, so it is dropped, never repaired. The
    judge's step is kept only if it still stands: a step below 1.0 must keep at least one listed
    line, as the pinned rubric requires of any lowered step. The dimensions are ``None`` when one
    does not; the counts are kept either way, so the report shows what was dropped. Evidence that is
    not a list of citations names no step and is not curable, because nothing in it was a citation.
    """
    out: dict = {}
    uncited: list[str] = []
    shapes: dict[str, int] = {}
    dropped, curable = 0, True
    for key, entry in dimensions.items():
        if not isinstance(entry, dict) or not isinstance(entry.get('evidence', []), list):
            return None, dropped, [], False, shapes
        evidence = entry.get('evidence', [])
        kept = [cite for cite in evidence if _listed(cite, name, count)]
        for cite in evidence:
            if not _listed(cite, name, count):
                shape = citation_shape(cite, name, count)
                shapes[shape] = shapes.get(shape, 0) + 1
        dropped += len(evidence) - len(kept)
        if kept != evidence and not kept and entry.get('step') != 1.0:
            curable = curable and _curable(key, entry)
            if key in DIMENSIONS:  # another key is the judge's own text, never echoed back to it
                uncited.append(key)
        else:
            out[key] = {**entry, 'evidence': kept} if 'evidence' in entry else dict(entry)
    if out.keys() == dimensions.keys():
        return out, dropped, [], False, shapes
    order = {key: n for n, key in enumerate(DIMENSIONS)}
    uncited.sort(key=order.__getitem__)
    return None, dropped, uncited, curable and bool(uncited), shapes


def off_ladder(dimensions: object) -> list[str]:
    """The dimensions whose step the pinned rubric would refuse as off its ladder, in rubric order."""
    if not isinstance(dimensions, dict):
        return []
    return [key for key in DIMENSIONS
            if isinstance(dimensions.get(key), dict) and dimensions[key].get('step') not in LADDER]


def laddered(verdict: dict) -> dict:
    """``verdict`` with each off-ladder step read as the rung the rubric checks most: 0.5, a lowered
    step that still needs evidence, or 1.0 for a number at or above it. Only to ask the pinned
    rubric what else it would refuse in a first answer; never scored, never shown as a grade."""
    dimensions = {key: dict(entry) if isinstance(entry, dict) else entry
                  for key, entry in verdict['dimensions'].items()}
    for key in off_ladder(dimensions):
        step = dimensions[key].get('step')
        number = type(step) in (int, float) and step >= 1.0
        dimensions[key]['step'] = 1.0 if number else 0.5
    return {**verdict, 'dimensions': dimensions}


def reask_text(off: list[str], uncited: list[str], file: str, malformed: bool = False) -> str:
    """The rule restated for each fault found, and the ask to answer again."""
    parts = ([REASK_MALFORMED] if malformed else []) + (
        [REASK_LADDER.format(dimensions=', '.join(off))] if off else []) + (
        [REASK_CITATION.format(dimensions=', '.join(uncited), file=file)] if uncited else [])
    return ' '.join([*parts, REASK_END])


def reask_messages(messages: list[tuple[str, str]], reply: str, off: list[str],
                   uncited: list[str] | None = None, file: str = '',
                   malformed: bool = False) -> list[tuple[str, str]]:
    """The first conversation, the judge's own answer, and the rules it broke restated."""
    return [*messages, ('assistant', reply), ('user', reask_text(off, uncited or [], file, malformed))]


def parse_theory(text: object, case: dict, token: str, name: str) -> dict:
    """The verdict without its coverage block. Upstream validation happens at scoring."""
    data, failure, outside = extract(text, THEORY_SCHEMA)

    def result(state: str, reason: str | None, verdict: dict | None, dropped: int = 0,
               shapes: dict | None = None) -> dict:
        return {'state': state, 'reason': reason, 'verdict': verdict, 'text_outside_json': outside,
                'ambiguous': failure == 'ambiguous', 'citations_dropped': dropped,
                'dropped_shapes': shapes or {}}
    if data is None and failure != 'ambiguous':
        # No object could be read, so nothing in it was judged: a fault of form alone. Re-askable
        # only with a reply to show the judge, since a re-ask carries the judge's own answer back.
        fault = {'malformed': True, 'reaskable': True} if isinstance(text, str) and text.strip() else {
            'malformed': True}
        return {**result('unknown', 'malformed', None), **fault}
    if data is None:
        return result('unknown', 'ambiguous', None)
    verdict = {key: value for key, value in data.items() if key != 'files'}
    if data.get('unit') != name[:-3]:
        return result('unknown', 'unit', None)
    verdict['unit'] = case['id']
    count = len(case['source'].splitlines())
    dimensions = data.get('dimensions')
    # Checked, never rounded onto the ladder: that would be the harness choosing the judge's step.
    off = {'off_ladder': off_ladder(dimensions)} if off_ladder(dimensions) else {}
    grounded, dropped, uncited, curable, shapes = (
        _grounded(dimensions, name, count) if isinstance(dimensions, dict) else (None, 0, [], False, {}))
    if grounded is None:
        # Re-askable only when the canary was copied too, so a re-ask never gives an unread answer
        # a second draw. With the ladder, an uncited lowered step is the only fault a re-ask cures.
        fault = {'uncited': uncited} if uncited else {}
        if curable and covered(data, name, token):
            fault['reaskable'] = True
        return {**result('unknown', 'citation', verdict, dropped, shapes), **off, **fault}
    verdict['dimensions'] = grounded
    if not covered(data, name, token):
        # Kept, so the report can show what an uncovered verdict would have been awarded.
        return {**result('unknown', 'coverage', verdict, dropped, shapes), **off}
    if off:
        # The only fault found here, so the one a re-ask may cure. The verdict is kept: scoring asks
        # the pinned rubric whether anything else in it would have been refused.
        return {**result('unknown', 'off_ladder', verdict, dropped, shapes), **off}
    return result('verdict', None, verdict, dropped, shapes)


# -- judges ------------------------------------------------------------------------------------

class ReplayJudge:
    """Answers from a capture directory, refusing a capture made for other prompts or pins."""

    def __init__(self, capture: Path) -> None:
        self.capture = Path(capture)
        manifest = json.loads((self.capture / 'capture.json').read_text(encoding='utf-8'))
        if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
                or not isinstance(manifest.get('salt'), str) or not isinstance(manifest.get('pins'), dict)):
            raise ValueError('not a judge-panel capture')
        self.salt = manifest['salt']
        self.pins = manifest['pins']
        # The parser the capture was taken to be read by. A capture taken before a parser change
        # replays under the new one, but it shaped that change, so it can never establish the proof.
        version = manifest.get('parser_version')
        self.parser_version = version if type(version) is int else None

    def respond(self, pin: JudgePin, case: dict, trial: int, messages: list[tuple[str, str]], *,
                reask: bool = False) -> dict | None:
        if self.pins.get(pin.judge_id) != pin.sha256:
            raise ValueError(f'capture was made with a different pin for {pin.judge_id}')
        path = self.capture / 'calls' / pin.judge_id / case['id'] / record_name(trial, reask)
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or not isinstance(record.get('attempts', []), list) or not all(
                isinstance(attempt, dict) for attempt in record.get('attempts', [])):
            raise ValueError(f'malformed capture record {pin.judge_id}/{case["id"]}/{trial}')
        if record.get('prompt_sha256') != prompt_sha256(messages):
            raise ValueError(f'capture is stale for {pin.judge_id}/{case["id"]}/{trial}')
        return record


class LiveJudge:
    """Billed calls through the 013 adapter, each recorded before it is used. Refuses past the ceiling."""

    def __init__(self, pins: list[JudgePin], configs: dict, out: Path, salt: str, max_calls: int, *,
                 spend_cap: str, environ=os.environ, transports: dict | None = None) -> None:  # type: ignore[no-untyped-def]
        from grimoire.app.config import build_access

        self.out, self.max_calls, self.calls = Path(out), max_calls, 0
        self.parser_version = PARSER_VERSION
        self.configs = configs
        self.access = {pin.judge_id: build_access(configs[pin.judge_id], 'human:calibration-operator', environ=environ,
                                                  transport=(transports or {}).get(pin.judge_id))
                       for pin in pins}
        self.out.mkdir(parents=True, exist_ok=False)
        _write(self.out / 'capture.json', {'format': FORMAT, 'salt': salt, 'spend_cap': spend_cap,
                                           'parser_version': PARSER_VERSION,
                                           'max_calls': max_calls,
                                           'pins': {pin.judge_id: pin.sha256 for pin in pins},
                                           'pin_records': [pin.record() for pin in pins]})

    def respond(self, pin: JudgePin, case: dict, trial: int, messages: list[tuple[str, str]], *,
                reask: bool = False) -> dict:
        from grimoire.app.config import policy_for
        from grimoire.core.inference import TurnUsageLedger, plan_turn
        from grimoire.core.inference.usage import TOKEN_FIELDS
        from grimoire.providers.errors import CallRefused
        from grimoire.providers.meter import TurnMeter
        from grimoire.providers.request import ChatMessage, ModelRequest

        if self.calls >= self.max_calls:
            raise RuntimeError('call ceiling reached')
        self.calls += 1
        config, access = self.configs[pin.judge_id], self.access[pin.judge_id]
        plan = plan_turn('calibration-panel', 'judge-' + uuid.uuid4().hex, present_actor_ids=[pin.judge_id],
                         speaking_npc_ids=[pin.judge_id], policy=policy_for(config))
        call = plan.calls[0]
        meter = TurnMeter(TurnUsageLedger(plan))
        request = ModelRequest(tuple(ChatMessage(role, content) for role, content in messages),
                               max_output_tokens=config.max_output_tokens, temperature=pin.temperature)
        record: dict = {'prompt_sha256': prompt_sha256(messages), 'pin_sha256': pin.sha256,
                        'status': 'interrupted', 'reply': None, 'finish_reason': None, 'refusal_reason': None}
        try:
            result = access.adapter.execute(meter, call, request, access.routes, access.handle)
            record.update(status=result.status, reply=result.reply, finish_reason=result.finish_reason,
                          refusal_reason=result.refusal_reason)
        except CallRefused as error:
            record.update(status='refused', refusal_reason=error.reason)
        finally:
            # Written even when an interrupt or crash escapes: a sent attempt may be billed.
            reports = meter.reports(call.call_id)
            # The reply is the final attempt's, so only that attempt's model may vouch for it.
            record['served_model'] = (reports[-1].served_model or None) if reports else None
            record['attempts'] = [{'outcome': r.outcome, 'measurement': r.measurement.value,
                                   'served_model': r.served_model or None,
                                   'tokens': {name: getattr(r.usage, name) for name in TOKEN_FIELDS}}
                                  for r in reports]
            _write(self.out / 'calls' / pin.judge_id / case['id'] / record_name(trial, reask), record)
        return record


def record_name(trial: int, reask: bool) -> str:
    return f'{trial}.reask.json' if reask else f'{trial}.json'


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


# -- running and scoring -------------------------------------------------------------------------

def _parses(case: dict) -> bool:
    try:
        ast.parse(case['source'])
        return True
    except (SyntaxError, ValueError):
        return False


def planned(cases: list[dict], trials: int) -> int:
    return sum(PANEL_SIZE * trials for case in cases if _parses(case))


def reasks_at_most(cases: list[dict], trials: int) -> int:
    """One re-ask per theory answer, if every one of them had a fault a re-ask may cure."""
    return sum(PANEL_SIZE * trials for case in cases if case['task'] == 'theory' and _parses(case))


def trial_result(record: dict | None, pin: JudgePin, case: dict, token: str, name: str) -> dict:
    parse = parse_defect if case['task'] == 'defect' else parse_theory
    base = {'state': 'unknown', 'lines': [], 'verdict': None}
    if record is None:
        return {**base, 'reason': 'not_captured', 'measurement': None}
    attempts = record.get('attempts', [])
    measurement = sorted({a.get('measurement') for a in attempts if a.get('outcome') != 'not_sent'})
    measured = measurement[0] if len(measurement) == 1 else ('mixed' if measurement else None)
    served = record.get('served_model')
    # Every model any attempt reported, kept apart from the reply's own, so drift sees them all.
    observed = sorted({m for m in [served, *(a.get('served_model') for a in attempts)]
                       if isinstance(m, str) and m})
    if record.get('status') != 'accepted':
        return {**base, 'reason': f'call_{record.get("status")}', 'measurement': measured,
                'observed_models': observed}
    if not isinstance(served, str) or not pin.serves(served):
        return {**base, 'reason': 'model_mismatch', 'measurement': measured,
                'served_model': served if isinstance(served, str) else None, 'observed_models': observed}
    return {**base, **parse(record.get('reply'), case, token, name), 'measurement': measured,
            'served_model': served, 'observed_models': observed}


# What ``parsing`` counts for each answer read, the first of a re-asked trial included.
ANSWER_COUNTS = ('text_outside_json', 'ambiguous', 'citations_dropped', 'dropped_shapes')


def _read(result: dict) -> bool:
    """Whether an answer reached the parser: an accepted call, served by the pinned model."""
    return 'text_outside_json' in result


def _measured(first: str | None, second: str | None) -> str | None:
    """One trial's measurement across its two calls: both must agree, as within one call."""
    kinds = {kind for kind in (first, second) if kind is not None}
    return kinds.pop() if len(kinds) == 1 else ('mixed' if kinds else None)


def collect(cases: list[dict], pins: list[JudgePin], trials: int, judge, salt: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """Ask every judge every case ``trials`` times; an unparseable defect case is never sent."""
    out = []
    for case in cases:
        entry = {key: case[key] for key in case if key != 'source'}
        if not _parses(case):
            entry.update(analysis='unknown', reason='syntax', judges={})
            out.append(entry)
            continue
        entry['judges'] = {}
        for pin in pins:
            results = []
            for trial in range(trials):
                token, name = canary(salt, pin.judge_id, case['id'], trial), alias(salt, case['id'])
                messages = messages_for(case, token, salt)
                record = judge.respond(pin, case, trial, messages)
                result = trial_result(record, pin, case, token, name)
                if result.get('reason') == 'off_ladder' or result.pop('reaskable', False):
                    # Once, never again: the re-asked answer is read on its own and stands as it is.
                    off, uncited = result.get('off_ladder', []), result.get('uncited', [])
                    malformed = bool(result.get('malformed'))
                    again = judge.respond(pin, case, trial, reask_messages(messages, record['reply'], off,
                                                                           uncited, name, malformed), reask=True)
                    if again is not None:
                        first = result
                        result = trial_result(again, pin, case, token, name)
                        result.pop('reaskable', None)  # only a gate for the first answer; kept out of the report
                        order = {key: n for n, key in enumerate(DIMENSIONS)}
                        # What the re-ask restated: the dimensions named, or the whole answer.
                        result.update(reasked=['malformed'] if malformed else sorted(
                                          {*off, *uncited}, key=lambda k: order.get(k, len(order))),
                                      first_answer={**{key: first[key] for key in ANSWER_COUNTS},
                                                    'verdict': first['verdict']},
                                      observed_models=sorted({*first.get('observed_models', []),
                                                              *result.get('observed_models', [])}),
                                      measurement=_measured(first['measurement'], result['measurement']))
                        if not _read(result):
                            # No second answer was read, so the trial's only answer keeps its faults.
                            result.update({key: first[key] for key in ('off_ladder', 'uncited', 'malformed')
                                           if key in first})
                results.append(result)
            entry['judges'][pin.judge_id] = results
        out.append(entry)
    return out


def void_drift(cases: list[dict]) -> dict:
    """A judge served by two models (an alias moved mid-capture) has no repeatable answers at all."""
    served: dict[str, set] = {}
    for case in cases:
        for judge_id, results in case.get('judges', {}).items():
            served.setdefault(judge_id, set()).update(m for r in results for m in r.get('observed_models', []))
    for case in cases:
        for judge_id, results in case.get('judges', {}).items():
            if len(served[judge_id]) > 1:
                for result in results:
                    result.update(state='unknown', lines=[], verdict=None, reason='model_drift')
    return {judge_id: sorted(models) for judge_id, models in served.items()}


def parsing(cases: list[dict]) -> dict:
    """Per judge, how many answers needed reading out of surrounding text, or lost citations."""
    out: dict[str, dict] = {}
    for case in cases:
        for judge_id, results in case.get('judges', {}).items():
            row = out.setdefault(judge_id, {'answers': 0, 'text_outside_json': 0, 'ambiguous': 0,
                                              'citations_dropped': 0, 'dropped_shapes': {}, 'reasked': 0,
                                              'off_ladder': 0, 'uncited': 0, 'malformed': 0})
            for result in results:
                for answer in (result.get('first_answer'), result):
                    if answer is None:
                        continue
                    row['answers'] += 1
                    row['text_outside_json'] += bool(answer.get('text_outside_json'))
                    row['ambiguous'] += bool(answer.get('ambiguous'))  # kept apart: drift rewrites reason
                    row['citations_dropped'] += answer.get('citations_dropped', 0)
                    for shape, n in answer.get('dropped_shapes', {}).items():
                        row['dropped_shapes'][shape] = row['dropped_shapes'].get(shape, 0) + n
                row['reasked'] += bool(result.get('reasked'))
                row['off_ladder'] += bool(result.get('off_ladder'))  # still off after any re-ask
                row['uncited'] += bool(result.get('uncited'))  # still uncited after any re-ask
                row['malformed'] += bool(result.get('malformed'))  # still unreadable after any re-ask
    return out


def _settled(trials: list[dict], defect_lines: list[int]) -> dict:
    """A judge's verdict over its trials: unknown if any is, inconsistent if they differ."""
    if not trials or any(t['state'] == 'unknown' for t in trials):
        return {'state': 'unknown', 'located': False}
    if len({t['state'] for t in trials}) != 1:
        return {'state': 'inconsistent', 'located': False}
    state = trials[0]['state']
    located = state == 'defect' and all(set(t['lines']) & set(defect_lines) for t in trials)
    return {'state': state, 'located': located}


def _panel_trials(case: dict, trials: int) -> list[dict]:
    """Per trial, the panel says what at least two judges said; otherwise unknown."""
    lines = case.get('defect_lines', [])
    out = []
    for trial in range(trials):
        votes = [results[trial] for results in case['judges'].values()]
        defect = [v for v in votes if v['state'] == 'defect']
        clear = [v for v in votes if v['state'] == 'clear']
        if len(defect) >= 2:
            located = [v for v in defect if set(v['lines']) & set(lines)]
            out.append({'state': 'defect', 'lines': sorted(lines) if len(located) >= 2 else []})
        elif len(clear) >= 2:
            out.append({'state': 'clear', 'lines': []})
        else:
            out.append({'state': 'unknown', 'lines': []})
    return out


def _outcome(label: bool | None, settled: dict) -> str | None:
    if label is None or settled['state'] in ('unknown', 'inconsistent'):
        return None
    if label:
        return 'tp' if settled['located'] else 'fn'
    return 'fp' if settled['state'] == 'defect' else 'tn'


def _rates(settled: list[tuple[bool | None, dict]]) -> dict:
    counts = dict(tp=0, fp=0, fn=0, tn=0)
    unknown = inconsistent = 0
    for label, verdict in settled:
        outcome = _outcome(label, verdict)
        if verdict['state'] == 'inconsistent':
            inconsistent += 1
        elif outcome is None:
            unknown += 1
        else:
            counts[outcome] += 1
    negatives, positives = counts['fp'] + counts['tn'], counts['tp'] + counts['fn']
    return {'confusion': counts, 'unknown': unknown, 'inconsistent': inconsistent,
            'false_positive_rate': counts['fp'] / negatives if negatives else None,
            'false_negative_rate': counts['fn'] / positives if positives else None}


def score_defects(cases: list[dict], trials: int, judge_ids: list[str]) -> dict:
    judged = [c for c in cases if c['task'] == 'defect' and c.get('analysis') != 'unknown']
    per_judge = {}
    for judge_id in judge_ids:
        settled = [(c['actionable_defect'], _settled(c['judges'][judge_id], c.get('defect_lines', [])))
                   for c in judged]
        repeatable = sum(1 for c in judged
                         if len({t['state'] for t in c['judges'][judge_id]}) == 1
                         and c['judges'][judge_id][0]['state'] != 'unknown')
        per_judge[judge_id] = {**_rates(settled), 'repeatable': repeatable, 'judged': len(judged)}
    panel = []
    for case in judged:
        case['panel_trials'] = _panel_trials(case, trials)
        case['panel'] = _settled(case['panel_trials'], case.get('defect_lines', []))
        verdicts = {_settled(results, case.get('defect_lines', []))['state'] for results in case['judges'].values()}
        case['judges_agree'] = len(verdicts) == 1 and verdicts <= {'defect', 'clear'}
        panel.append((case['actionable_defect'], case['panel']))
    return {'judges': per_judge, 'panel': {**_rates(panel), 'judged': len(judged),
                                           'all_judges_agree': sum(1 for c in judged if c['judges_agree'])}}


UPSTREAM = """
import json, sys
sys.path.insert(0, sys.argv[1])
import theory_rubric as tr
data = json.load(sys.stdin)
try:
    r = tr.score_panel(data['verdicts'], data['size'], allow_exemption=False)
except tr.VerdictError as error:
    print(json.dumps({'error': str(error)}))
else:
    print(json.dumps({'score': r['score'], 'grade': r['grade'], 'disputed': r['disputed'],
                      'steps': {row['key']: row['steps'] for row in r['dimensions']}}))
"""


def upstream_score(checkout: Path, verdicts: list[dict], size: dict) -> dict:
    """The pinned rubric's own panel score, run in an isolated subprocess (it is external code)."""
    scripts = Path(checkout) / 'skills/code-overview/scripts'
    with tempfile.TemporaryDirectory(prefix='grimoire-rubric-cache-') as cache:
        # A fresh bytecode cache: a stale or planted .pyc beside the pinned source is never loaded.
        run = subprocess.run([sys.executable, '-I', '-B', '-X', f'pycache_prefix={cache}', '-c', UPSTREAM,
                              str(scripts)], input=json.dumps({'verdicts': verdicts, 'size': size}),
                             capture_output=True, text=True, timeout=60)
    if run.returncode or run.stderr:
        return {'error': 'upstream rubric failed'}
    try:
        result = json.loads(run.stdout)
    except ValueError:
        return {'error': 'upstream rubric output malformed'}
    return result if isinstance(result, dict) else {'error': 'upstream rubric output malformed'}


def perfect_uncovered_verdict(unit: str) -> dict:
    """A verdict claiming every dimension holds, from a judge that never echoed the canary."""
    return {'schema': THEORY_SCHEMA, 'unit': unit, 'theory': 'It models the stated problem.',
            'instead_of': 'No other reading was considered necessary.', 'trivial': False, 'trivial_reason': '',
            'dimensions': {key: {'step': 1.0, 'rationale': 'holds', 'evidence': []} for key in DIMENSIONS},
            'rehearsals': [{'requirement': f'requirement {n}', 'verdict': 'extension', 'why': 'fits'}
                           for n in range(3)]}


def _score_trial(results: list[dict], size: dict, checkout: Path | None) -> dict:
    """Profile rule: a panel is scored only when all three judges are covered and valid.

    ``upstream_score`` is what the pinned rubric alone would award the same verdicts, coverage aside.
    A re-asked answer counts only if its judge's first answer had no fault but the ones a re-ask
    may cure: the pinned rubric must accept that first answer once its off-ladder steps are read as
    rungs. The rubric sees the first answer's citations as given, so only its own faults count.
    """
    verdicts = [r['verdict'] for r in results]
    upstream = upstream_score(checkout, verdicts, size) if checkout is not None and all(verdicts) else None
    reason = next((r['reason'] for r in results if r['state'] != 'verdict'), None)
    if reason is None and upstream is None:
        reason = 'no_pinned_rubric'
    elif reason is None and 'error' in upstream:
        reason = 'invalid_verdict'
    elif reason is None and checkout is not None and any(
            'error' in upstream_score(checkout, [laddered(r['first_answer']['verdict'])] * PANEL_SIZE, size)
            # A first answer with no readable object holds no verdict for the rubric to refuse.
            for r in results if r.get('first_answer') and r['first_answer']['verdict'] is not None):
        reason = 'first_answer_invalid'
    scored = reason is None
    return {'profile_score': upstream['score'] if scored else None,
            'upstream_score': (upstream or {}).get('score'),
            'grade': upstream['grade'] if scored else None,
            'disputed': upstream['disputed'] if scored else None, 'reason': reason}


def score_theory(cases: list[dict], trials: int, checkout: Path | None) -> dict:
    units = {}
    for case in (c for c in cases if c['task'] == 'theory'):
        if case.get('analysis') == 'unknown':
            units[case['id']] = {'trials': [], 'scored': False, 'score_range': None, 'grades_repeat': False,
                                 'reason': case['reason']}
            continue
        size = {'files': 1, 'loc': len(case['source_lines'])}
        trial_scores = [{**_score_trial([results[trial] for results in case['judges'].values()], size, checkout),
                         # Whose answer in this trial came from a re-ask, so the audit can name it.
                         'reasked': sorted(judge_id for judge_id, results in case['judges'].items()
                                           if results[trial].get('reasked') and _read(results[trial]))}
                        for trial in range(trials)]
        scores = [t['profile_score'] for t in trial_scores]
        known = None not in scores
        units[case['id']] = {'trials': trial_scores, 'scored': known,
                             'score_range': (max(scores) - min(scores)) if known else None,
                             'grades_repeat': known and len({t['grade'] for t in trial_scores}) == 1}
    for case in (c for c in cases if c['task'] == 'theory' and c.get('scores_above')):
        high, low = units[case['id']], units[case['scores_above']]
        units[case['id']]['discriminates'] = (
            min(t['profile_score'] for t in high['trials']) > max(t['profile_score'] for t in low['trials'])
            if high['scored'] and low['scored'] else None)
    # Three judges claim every dimension holds without echoing the canary: what is awarded?
    probe = [{'state': 'unknown', 'reason': 'coverage', 'verdict': perfect_uncovered_verdict('probe')}] * PANEL_SIZE
    return {'units': units, 'missing_analysis_probe': _score_trial(probe, {'files': 1, 'loc': 40}, checkout)}


def proof(cases: list[dict], defects: dict, theory: dict, capture_parser_version: int | None = None) -> dict:
    """The sprint's proving scenario, read off the report.

    ``established`` also needs live evidence, read by the parser the capture was taken for: a capture
    from before a parser change shaped that change, so re-reading it proves nothing.
    """
    by_role = {c.get('role'): c for c in cases if c.get('role')}
    facade, planted = by_role.get('compatibility_facade'), by_role.get('planted_defect')
    facade_ok = bool(facade and facade.get('panel', {}).get('state') == 'clear')
    planted_ok = bool(planted and planted.get('panel', {}).get('located'))
    # Computed through the same scoring path as the captured trials, never asserted.
    unread_scored = any(t['profile_score'] is not None and t['reason'] is not None
                        for unit in theory['units'].values() for t in unit['trials'])
    missing_ok = (theory['missing_analysis_probe']['profile_score'] is None and not unread_scored
                  and not any(c.get('judges') or 'panel' in c for c in cases if c.get('analysis') == 'unknown'))
    panel = defects['panel']
    complete = panel['unknown'] == 0 and panel['inconsistent'] == 0 and panel['judged'] > 0
    measured = {t.get('measurement') for c in cases for results in c.get('judges', {}).values() for t in results}
    live = measured == {'provider_reported'}
    theory_ok = bool(theory['units']) and all(unit['scored'] for unit in theory['units'].values()) and all(
        unit['discriminates'] is True for unit in theory['units'].values() if 'discriminates' in unit)
    current = capture_parser_version == PARSER_VERSION
    return {'compatibility_facade_not_penalized': facade_ok, 'planted_defect_found': planted_ok,
            'missing_analysis_not_perfect': missing_ok, 'defect_panel_complete': complete,
            'theory_panel_scored_and_discriminates': theory_ok, 'provider_reported_only': live,
            'captured_for_this_parser': current,
            'established': facade_ok and planted_ok and missing_ok and complete and theory_ok and live and current}


def run(corpus: Path, pins: list[JudgePin], trials: int, judge, salt: str,  # type: ignore[no-untyped-def]
        checkout: Path | None = None) -> dict:
    if checkout is not None:
        checkout = Path(checkout).resolve()
        verify_checkout(checkout)
    cases = load_corpus(Path(corpus))
    captured_for = getattr(judge, 'parser_version', None)
    collected = collect(cases, pins, trials, judge, salt)
    served = void_drift(collected)
    for entry, case in zip(collected, cases):
        entry['source_lines'] = case['source'].splitlines()
    defects = score_defects(collected, trials, [pin.judge_id for pin in pins])
    theory = score_theory(collected, trials, checkout)
    for entry in collected:
        entry.pop('source_lines')
    if checkout is not None:
        verify_checkout(checkout)
    return {'format': FORMAT, 'blocking': False, 'prompt_version': PROMPT_VERSION,
            'parser_version': PARSER_VERSION, 'capture_parser_version': captured_for,
            'parsing': parsing(collected), 'trials': trials,
            'pins': [pin.record() for pin in pins], 'distinct_models': len({(p.provider_id, p.model) for p in pins}),
            'rubric_revision': None if checkout is None else RUBRIC_REVISION,
            'served_models': served, 'cases': collected, 'defects': defects, 'theory': theory,
            'proof': proof(collected, defects, theory, captured_for),
            'confirmed_defects': [], 'note': 'Panel defect verdicts are candidates; labels are confirmed '
                                             'only by the executed checks in the test suite.'}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'capture', 'score'):
        command = sub.add_parser(name)
        command.add_argument('--corpus', type=Path, required=True)
        command.add_argument('--panel', type=Path, required=True)
    sub.choices['capture'].add_argument('--out', type=Path, required=True)
    sub.choices['capture'].add_argument('--confirm-billing', action='store_true')
    sub.choices['capture'].add_argument('--spend-cap', default=None,
                                        help='the approved maximum spend, recorded (e.g. "5 USD"); not enforced')
    sub.choices['capture'].add_argument('--max-calls', type=int, default=0)
    sub.choices['score'].add_argument('--capture', type=Path, required=True)
    sub.choices['score'].add_argument('--coding-skills', type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        pins, trials, configs = load_panel(args.panel)
        cases = load_corpus(args.corpus)
        calls = planned(cases, trials) + reasks_at_most(cases, trials)
        if args.command == 'plan':
            chars = sum(len(c) for case in cases if _parses(case) for _, c in messages_for(case, '0' * 16, ''))
            print(json.dumps({'planned_calls': planned(cases, trials),
                              'reasks_at_most': reasks_at_most(cases, trials), 'max_calls': calls,
                              'request_ceiling': sum(calls // PANEL_SIZE * pin.attempts for pin in pins),
                              'prompt_characters_per_judge_trial': chars,
                              'max_output_tokens': {p.judge_id: p.max_output_tokens for p in pins}}, indent=2))
            return
        if args.command == 'capture':
            if (not args.confirm_billing or not SPEND_CAP.fullmatch(args.spend_cap or '')
                    or args.max_calls < calls):
                parser.exit(2, f'capture refused: needs --confirm-billing, --spend-cap "<amount> <CUR>" and '
                               f'--max-calls of at least {calls}\n')
            salt = secrets.token_hex(16)
            judge = LiveJudge(pins, configs, args.out, salt, args.max_calls, spend_cap=args.spend_cap)
            collect(cases, pins, trials, judge, salt)
            print(json.dumps({'captured_calls': judge.calls, 'capture': str(args.out)}))
            return
        judge = ReplayJudge(args.capture)
        report = run(args.corpus, pins, trials, judge, judge.salt, args.coding_skills)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, f'judge panel unavailable: {error}\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
