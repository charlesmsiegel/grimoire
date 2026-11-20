"""069 judge panel: labels are executed, missing analysis is never clean, and nothing is billed."""
import asyncio
import os
import json
from pathlib import Path
import subprocess

import pytest

from grimoire import calibration, judgepanel as jp

CORPUS = Path(__file__).parent / 'fixtures/calibration/panel'
BASE = {'provider_id': 'synthetic', 'base_url': 'https://judge.invalid/v1', 'model': 'judge-model',
        'key_env': 'SYNTHETIC_JUDGE_KEY', 'max_output_tokens': 512}


def load(case_id):
    namespace = {}
    exec(compile((CORPUS / f'{case_id}.py.txt').read_text(), case_id, 'exec'), namespace)
    return namespace


# -- the labels themselves are behavior, run here, not judge opinions -----------------------------

class Backend:
    def __init__(self):
        self.data = {}

    def load(self, key):
        return self.data.get(key)

    def save(self, key, value):
        self.data[key] = value


class Failing:
    def write(self, note):
        raise OSError('disk full')


def check(case_id):
    """True when the fixture misbehaves against its own docstring."""
    ns = load(case_id)
    if case_id in ('awaited', 'discarded'):
        events = []
        asyncio.run(ns['caller'](events, 'synthetic'))
        return events != ['synthetic']
    if case_id == 'facade':
        store = ns['LegacyStore'](Backend())
        store.save('k', 'v')
        return store.load('k') != 'v'
    if case_id in ('mutable_default', 'sentinel_default'):
        ns['request_tags']('a')
        return ns['request_tags']('b') != ['b']
    if case_id == 'last_items':
        return ns['newest']([1, 2, 3, 4], 2) != [3, 4] or ns['newest']([1], 2) != [1]
    if case_id == 'late_binding':
        return [callback() for callback in ns['slot_callbacks'](3)] != [0, 1, 2]
    if case_id == 'swallowed_write':
        return ns['save_note'](Failing(), 'note') is not False
    if case_id == 'fail_closed':
        return [ns['classify'](t) for t in ('oops', '{}', '[]', '[1]')] != ['unknown', 'unknown', 'clear',
                                                                              'candidate']
    if case_id == 'exact_int':
        return [ns['valid_line'](v) for v in (True, 0, 1, 2.0)] != [False, False, True, False]
    raise AssertionError(case_id)


@pytest.mark.filterwarnings('ignore::RuntimeWarning')
@pytest.mark.parametrize('case', [c for c in jp.load_corpus(CORPUS)
                                  if c['task'] == 'defect' and c['actionable_defect'] is not None],
                         ids=lambda c: c['id'])
def test_every_defect_label_is_confirmed_by_running_the_fixture(case):
    assert check(case['id']) is case['actionable_defect']


def test_corpus_shape():
    cases = jp.load_corpus(CORPUS)
    roles = {c.get('role') for c in cases}
    assert {'compatibility_facade', 'planted_defect', 'odd_pattern'} <= roles
    defects = [c for c in cases if c['task'] == 'defect' and c['actionable_defect'] is not None]
    assert sum(c['actionable_defect'] for c in defects) == 5 and len(defects) == 10
    missing = next(c for c in cases if c['id'] == 'missing')
    assert missing['actionable_defect'] is None and not jp._parses(missing)
    for case in defects:
        for line in case.get('defect_lines', []):
            assert line <= len(case['source'].splitlines())


# -- parsing: only a covered, consistent answer is a verdict --------------------------------------

CASE = {'id': 'c', 'task': 'defect', 'file': 'c.py.txt', 'source': 'a = 1\nb = 2\n'}


def answer(verdict='defect', findings=None, token='tok', file='c.py.txt', schema=jp.DEFECT_SCHEMA):
    if findings is None:
        findings = [{'line': 2, 'explanation': 'wrong'}] if verdict == 'defect' else []
    return json.dumps({'schema': schema, 'files': [{'file': file, 'canary': token}],
                       'verdict': verdict, 'findings': findings})


@pytest.mark.parametrize('text,reason', [
    (None, 'malformed'), ('not json', 'malformed'), ('[]', 'malformed'),
    (answer(schema='other'), 'malformed'), (answer(token='wrong'), 'coverage'),
    (answer(file='other.py'), 'coverage'),
    (json.dumps({'schema': jp.DEFECT_SCHEMA, 'verdict': 'no_defect', 'findings': []}), 'coverage'),
    (answer('cannot_assess'), 'cannot_assess'), (answer('maybe'), 'malformed'),
    (answer('defect', findings=[]), 'contradiction'),
    (answer('no_defect', findings=[{'line': 1, 'explanation': 'x'}]), 'contradiction'),
    (answer(findings=[{'line': 3, 'explanation': 'x'}]), 'malformed'),
    (answer(findings=[{'line': True, 'explanation': 'x'}]), 'malformed'),
    (answer(findings=[{'line': 1, 'explanation': ' '}]), 'malformed'),
])
def test_missing_or_inconsistent_answers_are_unknown(text, reason):
    result = jp.parse_defect(text, CASE, 'tok', 'c.py.txt')
    assert result['state'] == 'unknown' and result['reason'] == reason


def test_covered_answers_are_verdicts_even_in_a_fence():
    assert jp.parse_defect('```json\n' + answer() + '\n```', CASE, 'tok', 'c.py.txt') == {
        'state': 'defect', 'lines': [2], 'reason': None}
    assert jp.parse_defect(answer('no_defect'), CASE, 'tok', 'c.py.txt')['state'] == 'clear'


def test_judges_never_see_corpus_ids_or_file_names():
    for case in jp.load_corpus(CORPUS):
        text = jp.messages_for(case, 'tok', 'salt')[1][1]  # the system prompt is the same for every case
        stems = {case['id'], case['file'], case['file'].split('.')[0]}
        assert not any(stem in text for stem in stems), case['id']
        assert jp.alias('salt', case['id']) in text and jp.alias('other', case['id']) not in text


def test_theory_citations_must_point_into_the_listing():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\nb = 2\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    for evidence, state in ((['f.py:2'], 'verdict'), (['f.py:3'], 'unknown'), (['other.py:1'], 'unknown'),
                            (['f.py'], 'unknown'), ([7], 'unknown')):
        verdict['dimensions']['absorption']['evidence'] = evidence
        text = json.dumps({**verdict, 'unit': 'f', 'files': [{'file': 'f.py', 'canary': 'tok'}]})
        assert jp.parse_theory(text, case, 'tok', 'f.py')['state'] == state
    verdict['dimensions']['absorption']['evidence'] = []
    for unit in ('u', None, 'g'):  # another unit's analysis is not this unit's
        text = json.dumps({**verdict, 'unit': unit, 'files': [{'file': 'f.py', 'canary': 'tok'}]})
        assert jp.parse_theory(text, case, 'tok', 'f.py')['reason'] == 'unit'


def test_listing_keeps_line_numbers_and_hides_one_canary():
    text = jp.listing('one\ntwo\nthree\nfour\n', 'abc')
    lines = text.splitlines()
    assert lines[2] == '     | # coverage-canary: abc'
    assert [line for line in lines if 'canary' not in line][2] == '   3 | three'


def test_pins_accept_only_the_model_or_its_dated_snapshot():
    pin = jp.JudgePin('a', 'p', 'https://x.invalid', 'judge-model', 0.0, 10, 1)
    assert pin.serves('judge-model') and pin.serves('judge-model-20261101') and pin.serves('judge-model-2026-11-01')
    assert not pin.serves('judge-model-mini') and not pin.serves(None) and not pin.serves('other')


# -- the panel ------------------------------------------------------------------------------------

def panel_file(tmp_path, judges=None, trials=3, **extra):
    judges = judges if judges is not None else [
        {'judge_id': name, 'temperature': 0, 'model_config': {**BASE, **extra}} for name in ('a', 'b', 'c')]
    path = tmp_path / 'panel.json'
    path.write_text(json.dumps({'trials': trials, 'judges': judges}))
    return path


@pytest.mark.parametrize('change', [
    {'trials': 2}, {'judges': 'drop'}, {'judges': 'dupe'}, {'fallbacks': ['other'], 'attempts': 2},
    {'temperature': 'hot'},
])
def test_panel_config_must_pin_three_judges_and_three_trials(tmp_path, change):
    judges = [{'judge_id': n, 'temperature': change.get('temperature', 0),
               'model_config': {**BASE, **{k: v for k, v in change.items() if k in ('fallbacks', 'attempts')}}}
              for n in ('a', 'b', 'c')]
    if change.get('judges') == 'drop':
        judges = judges[:2]
    if change.get('judges') == 'dupe':
        judges[2]['judge_id'] = 'a'
    with pytest.raises(ValueError):
        jp.load_panel(panel_file(tmp_path, judges, change.get('trials', 3)))


class Scripted:
    """Answers like a judge would, from ``decide(judge_id, case, trial) -> (verdict, lines, covered)``."""

    def __init__(self, decide, served='judge-model'):
        self.decide, self.served, self.calls = decide, served, []

    def respond(self, pin, case, trial, messages):
        self.calls.append((pin.judge_id, case['id'], trial))
        token = jp.canary('salt', pin.judge_id, case['id'], trial)
        assert token in messages[1][1]
        verdict, lines, covered = self.decide(pin.judge_id, case, trial)
        name = jp.alias('salt', case['id'])
        files = [{'file': name, 'canary': token if covered else 'skipped'}]
        if case['task'] == 'theory':
            reply = {**jp.perfect_uncovered_verdict(name[:-3]), 'files': files}
            if verdict == 'weak':
                for entry in reply['dimensions'].values():
                    entry.update(step=0.25, evidence=[name + ':1'])
        else:
            reply = {'schema': jp.DEFECT_SCHEMA, 'files': files, 'verdict': verdict,
                     'findings': [{'line': n, 'explanation': 'fails'} for n in lines]}
        return {'status': 'accepted', 'reply': json.dumps(reply), 'served_model': self.served,
                'prompt_sha256': jp.prompt_sha256(messages),
                'attempts': [{'outcome': 'ok', 'measurement': 'synthetic'}]}


def accurate(judge_id, case, trial):
    if case['task'] == 'theory':
        return ('weak' if case['id'] == 'ledger_scrambled' else 'strong'), [], True
    lines = case.get('defect_lines', [])
    return ('defect', lines[:1], True) if case['actionable_defect'] else ('no_defect', [], True)


def report(tmp_path, decide, checkout=None, **kw):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    judge = Scripted(decide, **kw)
    return jp.run(CORPUS, pins, trials, judge, 'salt', checkout), judge


def test_accurate_panel_meets_the_scenario_but_synthetic_answers_establish_nothing(tmp_path):
    result, judge = report(tmp_path, accurate)
    assert not any(case == 'missing' for _, case, _ in judge.calls)  # syntax failure: never sent
    assert len(judge.calls) == jp.planned(jp.load_corpus(CORPUS), 3) == (10 + 2) * 3 * 3
    panel = result['defects']['panel']
    assert panel['confusion'] == {'tp': 5, 'fp': 0, 'fn': 0, 'tn': 5}
    assert panel['false_positive_rate'] == 0.0 and panel['all_judges_agree'] == 10
    assert result['defects']['judges']['a']['repeatable'] == 10
    missing = next(c for c in result['cases'] if c['id'] == 'missing')
    assert missing['analysis'] == 'unknown' and missing['reason'] == 'syntax'
    proof = result['proof']
    assert proof['compatibility_facade_not_penalized'] and proof['planted_defect_found']
    assert proof['missing_analysis_not_perfect']
    assert not proof['theory_panel_scored_and_discriminates']  # no pinned rubric supplied
    assert proof['defect_panel_complete']
    assert not proof['provider_reported_only'] and not proof['established']
    assert result['blocking'] is False and result['confirmed_defects'] == []


def test_a_penalized_facade_is_a_false_positive(tmp_path):
    def flags_facade(judge_id, case, trial):
        if case['id'] == 'facade' and judge_id != 'c':
            return 'defect', [7], True
        return accurate(judge_id, case, trial)
    result, _ = report(tmp_path, flags_facade)
    assert result['defects']['panel']['confusion']['fp'] == 1
    assert result['defects']['panel']['false_positive_rate'] == 0.2
    assert result['defects']['judges']['c']['false_positive_rate'] == 0.0
    assert not result['proof']['compatibility_facade_not_penalized']


def test_a_mislocated_or_unread_planted_defect_is_not_found(tmp_path):
    def wrong_line(judge_id, case, trial):
        return ('defect', [1], True) if case['id'] == 'discarded' else accurate(judge_id, case, trial)

    def unread(judge_id, case, trial):
        verdict, lines, _ = accurate(judge_id, case, trial)
        return verdict, lines, case['id'] != 'discarded'
    for decide in (wrong_line, unread):
        result, _ = report(tmp_path, decide)
        assert not result['proof']['planted_defect_found']
    assert result['defects']['panel']['unknown'] == 1  # uncovered answers are unknown, not clean


def test_trials_that_disagree_are_inconsistent_not_counted(tmp_path):
    def wobbly(judge_id, case, trial):
        if case['id'] == 'last_items' and judge_id == 'a' and trial == 1:
            return 'no_defect', [], True
        return accurate(judge_id, case, trial)
    result, _ = report(tmp_path, wobbly)
    judge_a = result['defects']['judges']['a']
    assert judge_a['inconsistent'] == 1 and judge_a['repeatable'] == 9
    assert result['defects']['panel']['confusion']['tp'] == 5  # two of three still agree each trial


def test_an_incomplete_panel_does_not_establish_the_proof(tmp_path):
    def mostly_unread(judge_id, case, trial):
        verdict, lines, _ = accurate(judge_id, case, trial)
        return verdict, lines, case['id'] in ('facade', 'discarded') or case['task'] == 'theory'
    result, _ = report(tmp_path, mostly_unread)
    assert result['proof']['planted_defect_found'] and result['proof']['compatibility_facade_not_penalized']
    assert not result['proof']['defect_panel_complete'] and result['defects']['panel']['unknown'] == 8


def test_a_model_that_changes_mid_capture_voids_that_judge(tmp_path):
    judge = Scripted(accurate)
    respond = judge.respond

    def drifting(pin, case, trial, messages):
        record = respond(pin, case, trial, messages)
        if pin.judge_id == 'a' and trial == 2:
            record['served_model'] = 'judge-model-20270101'
        return record
    judge.respond = drifting
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    result = jp.run(CORPUS, pins, trials, judge, 'salt')
    assert result['served_models']['a'] == ['judge-model', 'judge-model-20270101']
    assert result['defects']['judges']['a']['unknown'] == 10
    assert result['defects']['judges']['b']['unknown'] == 0
    assert result['defects']['panel']['confusion']['tp'] == 5  # b and c still agree


def test_a_mismatched_model_once_voids_the_whole_judge(tmp_path):
    judge = Scripted(accurate)
    respond = judge.respond

    def once(pin, case, trial, messages):
        record = respond(pin, case, trial, messages)
        if pin.judge_id == 'a' and case['id'] == 'facade' and trial == 1:
            record['served_model'] = 'other-model'
        return record
    judge.respond = once
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    result = jp.run(CORPUS, pins, trials, judge, 'salt')
    assert result['served_models']['a'] == ['judge-model', 'other-model']
    assert result['defects']['judges']['a']['unknown'] == 10


def test_the_token_field_is_part_of_the_pin(tmp_path):
    first = jp.load_panel(panel_file(tmp_path))[0][0]
    second = jp.load_panel(panel_file(tmp_path, max_tokens_field='max_completion_tokens'))[0][0]
    assert first.sha256 != second.sha256


def test_an_interrupted_billed_call_is_still_recorded(tmp_path, monkeypatch):
    from grimoire.testing.providers import Interrupt, ScriptedTransport

    monkeypatch.setenv('SYNTHETIC_JUDGE_KEY', 'sk-synthetic')
    pins, trials, configs = jp.load_panel(panel_file(tmp_path))
    case = jp.load_corpus(CORPUS)[0]
    transports = {pin.judge_id: ScriptedTransport([Interrupt(1)]) for pin in pins}
    live = jp.LiveJudge(pins, configs, tmp_path / 'capture', 'salt', 1, spend_cap='0 USD', transports=transports)
    with pytest.raises(KeyboardInterrupt):
        live.respond(pins[0], case, 0, jp.messages_for(case, 'tok', 'salt'))
    record = json.loads((tmp_path / 'capture/calls/a/facade/0.json').read_text())
    assert record['status'] == 'interrupted' and record['attempts']


def test_a_retry_that_names_no_model_is_not_vouched_for_by_the_failed_attempt(tmp_path, monkeypatch):
    from grimoire.testing.providers import ReadFail, Reply, ScriptedTransport

    monkeypatch.setenv('SYNTHETIC_JUDGE_KEY', 'sk-synthetic')
    pins, trials, configs = jp.load_panel(panel_file(tmp_path, attempts=2))
    case = jp.load_corpus(CORPUS)[0]
    name = jp.alias('salt', case['id'])
    token = jp.canary('salt', 'a', case['id'], 0)
    messages = jp.messages_for(case, token, 'salt')
    reply = Scripted(accurate).respond(pins[0], case, 0, messages)['reply']
    transports = {'a': ScriptedTransport([ReadFail(1, reply=Reply(model='judge-model')),
                                          Reply(deltas=(reply,), model='')])}
    live = jp.LiveJudge(pins, configs, tmp_path / 'capture', 'salt', 1, spend_cap='0 USD', transports=transports)
    record = live.respond(pins[0], case, 0, messages)
    assert record['status'] == 'accepted' and record['served_model'] is None
    result = jp.trial_result(record, pins[0], case, token, name)
    assert result['state'] == 'unknown' and result['reason'] == 'model_mismatch'
    assert result['observed_models'] == ['judge-model']


def test_a_different_served_model_voids_every_answer(tmp_path):
    result, _ = report(tmp_path, accurate, served='judge-model-mini')
    assert result['defects']['panel']['unknown'] == 10
    reasons = {t['reason'] for c in result['cases'] for r in c.get('judges', {}).values() for t in r}
    assert reasons == {'model_mismatch'}


# -- theory: the pinned rubric scores; the profile refuses an unread unit ------------------------

STUB_RUBRIC = '''
class VerdictError(ValueError):
    pass
def score_panel(verdicts, size, *, allow_exemption=True):
    if len(verdicts) != 3:
        raise VerdictError("three")
    steps = [sum(v["dimensions"][k]["step"] for k in v["dimensions"]) / 5 for v in verdicts]
    score = 100.0 * sorted(steps)[1]
    return {"score": score, "grade": "A" if score >= 90 else "D", "disputed": [],
            "dimensions": [{"key": k, "steps": [v["dimensions"][k]["step"] for v in verdicts]}
                           for k in verdicts[0]["dimensions"]]}
'''


@pytest.fixture
def stub_checkout(tmp_path, monkeypatch):
    tool = tmp_path / 'tool'
    scripts = tool / 'skills/code-overview/scripts'
    scripts.mkdir(parents=True)
    (scripts / 'theory_rubric.py').write_text(STUB_RUBRIC)
    for args in (['init'], ['add', '.'], ['-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                                          'commit', '-m', 'stub']):
        subprocess.run(['git', '-C', str(tool), *args], check=True, capture_output=True)
    revision = subprocess.check_output(['git', '-C', str(tool), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setattr(calibration, 'REVISION', revision)
    monkeypatch.setattr(jp, 'RUBRIC_REVISION', revision)
    return tool


def test_theory_scores_only_covered_panels_and_reports_what_upstream_would_award(tmp_path, stub_checkout):
    result, _ = report(tmp_path, accurate, checkout=stub_checkout)
    units = result['theory']['units']
    assert units['ledger_cohesive']['scored'] and units['ledger_cohesive']['score_range'] == 0
    assert units['ledger_cohesive']['discriminates'] is True
    assert result['proof']['theory_panel_scored_and_discriminates']
    probe = result['theory']['missing_analysis_probe']
    assert probe == {'profile_score': None, 'upstream_score': 100.0, 'grade': None, 'disputed': None,
                     'reason': 'coverage'}

    def skips_one(judge_id, case, trial):
        verdict, lines, ok = accurate(judge_id, case, trial)
        return verdict, lines, not (case['id'] == 'ledger_scrambled' and judge_id == 'b')
    result, _ = report(tmp_path, skips_one, checkout=stub_checkout)
    scrambled = result['theory']['units']['ledger_scrambled']
    assert not scrambled['scored'] and {t['reason'] for t in scrambled['trials']} == {'coverage'}
    assert all(t['upstream_score'] is not None for t in scrambled['trials'])
    assert result['theory']['units']['ledger_cohesive']['discriminates'] is None


def test_a_modified_rubric_checkout_is_refused(tmp_path, stub_checkout):
    (stub_checkout / 'skills/code-overview/scripts/theory_rubric.py').write_text(STUB_RUBRIC + '\n#')
    with pytest.raises(ValueError, match='clean'):
        report(tmp_path, accurate, checkout=stub_checkout)


def test_planted_bytecode_beside_the_rubric_is_never_loaded(tmp_path, stub_checkout):
    import py_compile
    scripts = stub_checkout / 'skills/code-overview/scripts'
    planted = tmp_path / 'planted.py'
    planted.write_text(STUB_RUBRIC.replace('score = 100.0 *', 'score = 0.0 *'))
    py_compile.compile(str(planted), cfile=str(scripts / '__pycache__/theory_rubric.cpython-'
                                               f'{__import__("sys").version_info[0]}{__import__("sys").version_info[1]}.pyc'),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    (stub_checkout / '.gitignore').write_text('__pycache__/\n')
    with pytest.raises(ValueError, match='clean'):  # an untracked .gitignore
        report(tmp_path, accurate, checkout=stub_checkout)
    subprocess.run(['git', '-C', str(stub_checkout), 'add', '.gitignore'], check=True)
    subprocess.run(['git', '-C', str(stub_checkout), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                    'commit', '-qm', 'ignore'], check=True)
    revision = subprocess.check_output(['git', '-C', str(stub_checkout), 'rev-parse', 'HEAD'], text=True).strip()
    calibration.REVISION = jp.RUBRIC_REVISION = revision
    with pytest.raises(ValueError, match='clean'):  # an ignored file is still not clean
        report(tmp_path, accurate, checkout=stub_checkout)
    scores = jp.upstream_score(stub_checkout, [jp.perfect_uncovered_verdict('u')] * 3, {'files': 1, 'loc': 3})
    assert scores['score'] == 100.0  # the source ran, not the planted bytecode


def test_malformed_captures_fail_cleanly(tmp_path, capsys):
    capture = tmp_path / 'capture'
    capture.mkdir()
    for manifest in ({'format': jp.FORMAT}, {'format': jp.FORMAT, 'salt': 's', 'pins': []}):
        (capture / 'capture.json').write_text(json.dumps(manifest))
        with pytest.raises(SystemExit) as stop:
            jp.main(['score', '--corpus', str(CORPUS), '--panel', str(panel_file(tmp_path)),
                     '--capture', str(capture)])
        assert stop.value.code == 2 and 'unavailable' in capsys.readouterr().err


# -- capture and replay; billed calls need every guard --------------------------------------------

def test_replay_reproduces_a_capture_and_refuses_a_stale_one(tmp_path, monkeypatch):
    from grimoire.testing.providers import Reply, ScriptedTransport

    monkeypatch.setenv('SYNTHETIC_JUDGE_KEY', 'sk-synthetic')
    pins, trials, configs = jp.load_panel(panel_file(tmp_path))
    cases = jp.load_corpus(CORPUS)
    scripted = Scripted(accurate)

    def transport_for(judge_id):
        steps = []
        for case in cases:
            if case['task'] == 'defect' and not jp._parses(case):
                continue
            for trial in range(trials):
                token = jp.canary('salt', judge_id, case['id'], trial)
                messages = jp.messages_for(case, token, 'salt')
                pin = next(p for p in pins if p.judge_id == judge_id)
                steps.append(Reply(deltas=(scripted.respond(pin, case, trial, messages)['reply'],),
                                   model='judge-model'))
        return ScriptedTransport(steps)

    transports = {pin.judge_id: transport_for(pin.judge_id) for pin in pins}
    capture = tmp_path / 'capture'
    live = jp.LiveJudge(pins, configs, capture, 'salt', jp.planned(cases, trials), spend_cap='0 USD',
                        transports=transports)
    jp.collect(cases, pins, trials, live, 'salt')
    assert all(t.remaining == 0 for t in transports.values())
    assert 'sk-synthetic' not in ''.join(p.read_text() for p in capture.rglob('*.json'))
    replayed = jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt')
    assert replayed['defects']['panel']['confusion'] == {'tp': 5, 'fp': 0, 'fn': 0, 'tn': 5}
    assert not replayed['proof']['provider_reported_only']  # a scripted transport is synthetic
    with pytest.raises(RuntimeError, match='ceiling'):
        live.respond(pins[0], cases[0], 0, jp.messages_for(cases[0], 'x', 'salt'))

    record = capture / 'calls/a/facade/0.json'
    record.write_text(record.read_text().replace('"prompt_sha256": "', '"prompt_sha256": "0'))
    with pytest.raises(ValueError, match='stale'):
        jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt')
    other = panel_file(tmp_path, model='judge-model-2')
    with pytest.raises(ValueError, match='different pin'):
        jp.run(CORPUS, jp.load_panel(other)[0], trials, jp.ReplayJudge(capture), 'salt')


@pytest.mark.parametrize('extra', [[], ['--confirm-billing'], ['--confirm-billing', '--spend-cap', '5 USD'],
                                   ['--confirm-billing', '--spend-cap', ' ', '--max-calls', '108'],
                                   ['--confirm-billing', '--spend-cap', 'lots', '--max-calls', '108'],
                                   ['--confirm-billing', '--spend-cap', '5 USD', '--max-calls', '107']])
def test_capture_is_refused_before_any_call_without_every_guard(tmp_path, extra, capsys):
    out = tmp_path / 'out'
    with pytest.raises(SystemExit) as stop:
        jp.main(['capture', '--corpus', str(CORPUS), '--panel', str(panel_file(tmp_path)), '--out', str(out),
                 *extra])
    assert stop.value.code == 2 and 'capture refused' in capsys.readouterr().err
    assert not out.exists()


def test_plan_names_the_calls_and_bounds(tmp_path, capsys):
    jp.main(['plan', '--corpus', str(CORPUS), '--panel', str(panel_file(tmp_path))])
    plan = json.loads(capsys.readouterr().out)
    assert plan['planned_calls'] == 108 and plan['request_ceiling'] == 108
    assert plan['max_output_tokens'] == {'a': 512, 'b': 512, 'c': 512}


@pytest.mark.skipif(not os.environ.get('GRIMOIRE_CODING_SKILLS'),
                    reason='set GRIMOIRE_CODING_SKILLS to a clean checkout at the calibration pin')
def test_pinned_upstream_rubric_awards_a_perfect_score_to_unread_verdicts(tmp_path):
    """Reproduces the upstream inaccuracy the profile gate corrects (docs/CALIBRATION.md)."""
    result, _ = report(tmp_path, accurate, checkout=Path(os.environ['GRIMOIRE_CODING_SKILLS']))
    assert result['theory']['missing_analysis_probe']['upstream_score'] == 100.0
    assert result['theory']['missing_analysis_probe']['profile_score'] is None
    assert result['theory']['units']['ledger_cohesive']['discriminates'] is True
