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


def _object(text: object) -> dict | None:
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith('```'):
        body = body.split('\n', 1)[1] if '\n' in body else ''
        body = body[:body.rfind('```')] if body.rstrip().endswith('```') else body
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def covered(data: dict, name: str, token: str) -> bool:
    files = data.get('files')
    return (isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)
            and files[0].get('file') == name and files[0].get('canary') == token)


def parse_defect(text: object, case: dict, token: str, name: str) -> dict:
    """``defect``/``clear`` only for a covered, self-consistent answer; anything else is unknown."""
    def unknown(reason: str) -> dict:
        return {'state': 'unknown', 'lines': [], 'reason': reason}
    data = _object(text)
    if data is None or data.get('schema') != DEFECT_SCHEMA:
        return unknown('malformed')
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
            'lines': sorted({finding['line'] for finding in findings}), 'reason': None}


def _cites(evidence: object, name: str, count: int) -> bool:
    """Every citation is ``<file>:<line>`` on a line the listing has."""
    if not isinstance(evidence, list):
        return False
    for cite in evidence:
        file, _, line = cite.rpartition(':') if isinstance(cite, str) else ('', '', '')
        if file != name or not line.isdigit() or not 1 <= int(line) <= count:
            return False
    return True


def parse_theory(text: object, case: dict, token: str, name: str) -> dict:
    """The verdict without its coverage block. Upstream validation happens at scoring."""
    data = _object(text)
    if data is None or data.get('schema') != THEORY_SCHEMA:
        return {'state': 'unknown', 'reason': 'malformed', 'verdict': None}
    verdict = {key: value for key, value in data.items() if key != 'files'}
    if data.get('unit') != name[:-3]:
        return {'state': 'unknown', 'reason': 'unit', 'verdict': None}
    verdict['unit'] = case['id']
    count = len(case['source'].splitlines())
    dimensions = data.get('dimensions')
    if not isinstance(dimensions, dict) or not all(
            isinstance(entry, dict) and _cites(entry.get('evidence', []), name, count)
            for entry in dimensions.values()):
        return {'state': 'unknown', 'reason': 'citation', 'verdict': verdict}
    if not covered(data, name, token):
        # Kept, so the report can show what an uncovered verdict would have been awarded.
        return {'state': 'unknown', 'reason': 'coverage', 'verdict': verdict}
    return {'state': 'verdict', 'reason': None, 'verdict': verdict}


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

    def respond(self, pin: JudgePin, case: dict, trial: int, messages: list[tuple[str, str]]) -> dict | None:
        if self.pins.get(pin.judge_id) != pin.sha256:
            raise ValueError(f'capture was made with a different pin for {pin.judge_id}')
        path = self.capture / 'calls' / pin.judge_id / case['id'] / f'{trial}.json'
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
        self.configs = configs
        self.access = {pin.judge_id: build_access(configs[pin.judge_id], 'human:calibration-operator', environ=environ,
                                                  transport=(transports or {}).get(pin.judge_id))
                       for pin in pins}
        self.out.mkdir(parents=True, exist_ok=False)
        _write(self.out / 'capture.json', {'format': FORMAT, 'salt': salt, 'spend_cap': spend_cap,
                                           'max_calls': max_calls,
                                           'pins': {pin.judge_id: pin.sha256 for pin in pins},
                                           'pin_records': [pin.record() for pin in pins]})

    def respond(self, pin: JudgePin, case: dict, trial: int, messages: list[tuple[str, str]]) -> dict:
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
            record['served_model'] = next((r.served_model for r in reversed(reports) if r.served_model), None)
            record['attempts'] = [{'outcome': r.outcome, 'measurement': r.measurement.value,
                                   'tokens': {name: getattr(r.usage, name) for name in TOKEN_FIELDS}}
                                  for r in reports]
            _write(self.out / 'calls' / pin.judge_id / case['id'] / f'{trial}.json', record)
        return record


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


def trial_result(record: dict | None, pin: JudgePin, case: dict, token: str, name: str) -> dict:
    parse = parse_defect if case['task'] == 'defect' else parse_theory
    base = {'state': 'unknown', 'lines': [], 'verdict': None}
    if record is None:
        return {**base, 'reason': 'not_captured', 'measurement': None}
    measurement = sorted({a.get('measurement') for a in record.get('attempts', []) if a.get('outcome') != 'not_sent'})
    measured = measurement[0] if len(measurement) == 1 else ('mixed' if measurement else None)
    if record.get('status') != 'accepted':
        return {**base, 'reason': f'call_{record.get("status")}', 'measurement': measured}
    if not pin.serves(record.get('served_model')):
        # Kept, so drift detection sees every model that answered for this judge.
        return {**base, 'reason': 'model_mismatch', 'measurement': measured,
                'served_model': record.get('served_model')}
    return {**base, **parse(record.get('reply'), case, token, name), 'measurement': measured,
            'served_model': record.get('served_model')}


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
                token = canary(salt, pin.judge_id, case['id'], trial)
                messages = messages_for(case, token, salt)
                results.append(trial_result(judge.respond(pin, case, trial, messages), pin, case, token,
                                            alias(salt, case['id'])))
            entry['judges'][pin.judge_id] = results
        out.append(entry)
    return out


def void_drift(cases: list[dict]) -> dict:
    """A judge served by two models (an alias moved mid-capture) has no repeatable answers at all."""
    served: dict[str, set] = {}
    for case in cases:
        for judge_id, results in case.get('judges', {}).items():
            served.setdefault(judge_id, set()).update(r['served_model'] for r in results if r.get('served_model'))
    for case in cases:
        for judge_id, results in case.get('judges', {}).items():
            if len(served[judge_id]) > 1:
                for result in results:
                    result.update(state='unknown', lines=[], verdict=None, reason='model_drift')
    return {judge_id: sorted(models) for judge_id, models in served.items()}


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
    """
    verdicts = [r['verdict'] for r in results]
    upstream = upstream_score(checkout, verdicts, size) if checkout is not None and all(verdicts) else None
    reason = next((r['reason'] for r in results if r['state'] != 'verdict'), None)
    if reason is None and upstream is None:
        reason = 'no_pinned_rubric'
    elif reason is None and 'error' in upstream:
        reason = 'invalid_verdict'
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
        trial_scores = [_score_trial([results[trial] for results in case['judges'].values()], size, checkout)
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


def proof(cases: list[dict], defects: dict, theory: dict) -> dict:
    """The sprint's proving scenario, read off the report; ``established`` also needs live evidence."""
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
    return {'compatibility_facade_not_penalized': facade_ok, 'planted_defect_found': planted_ok,
            'missing_analysis_not_perfect': missing_ok, 'defect_panel_complete': complete,
            'theory_panel_scored_and_discriminates': theory_ok, 'provider_reported_only': live,
            'established': facade_ok and planted_ok and missing_ok and complete and theory_ok and live}


def run(corpus: Path, pins: list[JudgePin], trials: int, judge, salt: str,  # type: ignore[no-untyped-def]
        checkout: Path | None = None) -> dict:
    if checkout is not None:
        checkout = Path(checkout).resolve()
        verify_checkout(checkout)
    cases = load_corpus(Path(corpus))
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
    return {'format': FORMAT, 'blocking': False, 'prompt_version': PROMPT_VERSION, 'trials': trials,
            'pins': [pin.record() for pin in pins], 'distinct_models': len({(p.provider_id, p.model) for p in pins}),
            'rubric_revision': None if checkout is None else RUBRIC_REVISION,
            'served_models': served, 'cases': collected, 'defects': defects, 'theory': theory,
            'proof': proof(collected, defects, theory),
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
        calls = planned(cases, trials)
        if args.command == 'plan':
            chars = sum(len(c) for case in cases if _parses(case) for _, c in messages_for(case, '0' * 16, ''))
            print(json.dumps({'planned_calls': calls,
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
