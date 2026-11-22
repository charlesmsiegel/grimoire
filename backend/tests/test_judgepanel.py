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
        'state': 'defect', 'lines': [2], 'reason': None, 'text_outside_json': False, 'ambiguous': False}
    assert jp.parse_defect(answer('no_defect'), CASE, 'tok', 'c.py.txt')['state'] == 'clear'


def test_judges_never_see_corpus_ids_or_file_names():
    for case in jp.load_corpus(CORPUS):
        text = jp.messages_for(case, 'tok', 'salt')[1][1]  # the system prompt is the same for every case
        stems = {case['id'], case['file'], case['file'].split('.')[0]}
        assert not any(stem in text for stem in stems), case['id']
        assert jp.alias('salt', case['id']) in text and jp.alias('other', case['id']) not in text


def theory_answer(verdict, unit='f', token='tok'):
    return json.dumps({**verdict, 'unit': unit, 'files': [{'file': 'f.py', 'canary': token}]})


def test_a_citation_of_an_unlisted_line_is_dropped_and_a_lowered_step_must_keep_a_real_one():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\nb = 2\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    absorption = verdict['dimensions']['absorption']
    # A step of 1.0 needs no evidence, so a bad citation on it is dropped and the verdict stands.
    for evidence, kept in ((['f.py:2'], ['f.py:2']), (['f.py:3'], []), (['other.py:1'], []), (['f.py'], []),
                           ([7], []), (['f.py:0', 'f.py:1'], ['f.py:1'])):
        absorption['evidence'] = evidence
        result = jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')
        assert result['state'] == 'verdict', evidence
        assert result['verdict']['dimensions']['absorption']['evidence'] == kept
        assert result['citations_dropped'] == len(evidence) - len(kept)
    # A lowered step stands only on a line the listing has.
    absorption['step'] = 0.25
    for evidence, state in ((['f.py:3', 'f.py:1'], 'verdict'), (['f.py:3'], 'unknown'),
                            (['f.py:9', 'g.py:1'], 'unknown')):
        absorption['evidence'] = evidence
        result = jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')
        assert result['state'] == state, evidence
        if state == 'unknown':
            assert result['reason'] == 'citation'
    # Evidence that is not a list of citations at all is not repaired.
    absorption['evidence'] = 'f.py:1'
    assert jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')['reason'] == 'citation'
    absorption['evidence'] = []
    for unit in ('u', None, 'g'):  # another unit's analysis is not this unit's
        assert jp.parse_theory(theory_answer(verdict, unit), case, 'tok', 'f.py')['reason'] == 'unit'


def test_an_uncited_lowered_step_is_left_for_the_pinned_rubric_to_refuse():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    verdict['dimensions']['absorption'].update(step=0.5, evidence=[])
    result = jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')
    assert result['state'] == 'verdict' and result['citations_dropped'] == 0  # nothing invented or dropped


PROSE = 'Line 2 assigns the wrong name, so the caller reads a stale value.\n\n'


@pytest.mark.parametrize('wrap', [
    lambda body: PROSE + body,                                   # prose, then the bare object
    lambda body: PROSE + '```json\n' + body + '\n```\n',         # prose, then a fenced object
    lambda body: 'Example: {"line": 1}. ' + body,                # a stray object without the schema
    lambda body: PROSE + '{not json} ' + body,                   # a brace that never parses
])
def test_prose_before_one_tagged_object_is_read_and_reported(wrap):
    result = jp.parse_defect(wrap(answer()), CASE, 'tok', 'c.py.txt')
    assert result == {'state': 'defect', 'lines': [2], 'reason': None, 'text_outside_json': True,
                      'ambiguous': False}


@pytest.mark.parametrize('text,reason', [
    (PROSE + answer() + '\nOn reflection:\n' + answer('no_defect'), 'ambiguous'),  # it changed its mind
    (PROSE + answer() + answer(), 'ambiguous'),                                      # even the same answer
    (answer() + '\n\nWait, line 2 is correct. There is no defect.', 'ambiguous'),     # text after can retract
    (PROSE + answer() + '\nCorrected:\n' + answer('no_defect')[:-1] + ',}', 'ambiguous'),  # a broken second try
    (PROSE + answer('no_defect')[:-1] + ',}\nCorrected:\n' + answer(), 'ambiguous'),     # a broken first try
    ('Answer: {"result": ' + answer() + ', }', 'ambiguous'),                          # inside a broken wrapper
    ('Example: {"example": ' + answer('no_defect'), 'ambiguous'),                     # inside an unclosed wrapper
    ('[\n  ' + answer(), 'ambiguous'),                                                # inside an unclosed list
    ('Per the set {a, b we have: ' + answer(), 'ambiguous'),                          # a bracket left open
    (PROSE + answer()[:-1] + ',}', 'malformed'),                                     # one object, not JSON
    (PROSE + json.dumps({'answer': json.loads(answer())}), 'ambiguous'),             # only nested inside another
    (json.dumps([json.loads(answer())]), 'ambiguous'),                                # inside a list too
    (PROSE + answer(schema='other'), 'malformed'),
    ('The code is correct; no defect.', 'malformed'),                               # a verdict in prose only
    (PROSE + answer(token='wrong'), 'coverage'),                                     # the canary still decides
])
def test_text_around_an_answer_never_supplies_or_repairs_it(text, reason):
    result = jp.parse_defect(text, CASE, 'tok', 'c.py.txt')
    assert result['state'] == 'unknown' and result['reason'] == reason


def test_the_report_counts_answers_read_out_of_prose_and_ambiguous_ones_even_after_drift():
    cases = [{'judges': {'a': [{'text_outside_json': True, 'ambiguous': False},
                               {'reason': 'model_drift', 'ambiguous': True, 'citations_dropped': 2,
                                'dropped_shapes': {'range': 1, 'other_file': 1}}]}}]
    assert jp.parsing(cases) == {'a': {'answers': 2, 'text_outside_json': 1, 'ambiguous': 1,
                                       'citations_dropped': 2, 'dropped_shapes': {'range': 1, 'other_file': 1},
                                       'reasked': 0, 'off_ladder': 0, 'uncited': 0}}


def test_a_superscript_line_number_is_a_dropped_citation_not_a_crash():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\nb = 2\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    verdict['dimensions']['absorption']['evidence'] = ['f.py:\u00b2']
    assert jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')['citations_dropped'] == 1


def test_a_voided_verdict_still_reports_the_citations_it_lost():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\nb = 2\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    verdict['dimensions']['absorption'].update(step=0.5, evidence=['f.py:9'])
    result = jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')
    assert result['reason'] == 'citation' and result['citations_dropped'] == 1


def test_theory_answers_with_prose_before_them_are_read_too():
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\n'}
    result = jp.parse_theory(PROSE + theory_answer(jp.perfect_uncovered_verdict('u')), case, 'tok', 'f.py')
    assert result['state'] == 'verdict' and result['text_outside_json'] is True


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

    def respond(self, pin, case, trial, messages, reask=False):
        self.calls.append((pin.judge_id, case['id'], trial) + (('reask',) if reask else ()))
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


class LikeTheLiveRun(Scripted):
    """The shapes the 2026-11-20 capture showed: judge ``b`` puts prose before every defect answer,
    and judge ``c`` cites a line past the end of the cohesive unit, beside a real one, in two trials."""

    def respond(self, pin, case, trial, messages, reask=False):
        record = super().respond(pin, case, trial, messages, reask)
        if pin.judge_id == 'b' and case['task'] == 'defect':
            record['reply'] = 'The listing reads correctly from the top.\n\n```json\n' + record['reply'] + '\n```'
        if pin.judge_id == 'c' and case['id'] == 'ledger_cohesive' and trial < 2:
            reply = json.loads(record['reply'])
            name, count = jp.alias('salt', case['id']), len(case['source'].splitlines())
            reply['dimensions']['honest_limits'].update(step=0.5, evidence=[f'{name}:{count + 3}', f'{name}:1'])
            record['reply'] = json.dumps(reply)
        return record


def test_answers_shaped_like_the_live_run_now_complete_the_panel(tmp_path, stub_checkout):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    result = jp.run(CORPUS, pins, trials, LikeTheLiveRun(accurate), 'salt', stub_checkout)
    assert result['parser_version'] == jp.PARSER_VERSION
    assert result['parsing']['b']['text_outside_json'] == 10 * 3   # every defect answer, read and reported
    assert result['parsing']['c']['citations_dropped'] == 2
    assert result['defects']['judges']['b']['unknown'] == 0
    proof = result['proof']
    assert proof['defect_panel_complete'] and proof['theory_panel_scored_and_discriminates']
    assert not proof['established']  # still synthetic


class OffTheLadder(Scripted):
    """The shape the 2026-11-22 capture showed: judge ``b`` grades the cohesive unit's honest_limits
    0.75, between two rungs, in two trials. Asked again, it answers ``fixed`` (``None``: off again)."""

    def __init__(self, decide, fixed=0.5, trials=(0, 1)):
        super().__init__(decide)
        self.fixed, self.trials, self.reasks = fixed, trials, []

    def respond(self, pin, case, trial, messages, reask=False):
        record = super().respond(pin, case, trial, messages, reask)
        if pin.judge_id == 'b' and case['id'] == 'ledger_cohesive' and trial in self.trials:
            reply = json.loads(record['reply'])
            name = jp.alias('salt', case['id'])
            step = 0.75 if not reask or self.fixed is None else self.fixed
            reply['dimensions']['honest_limits'].update(step=step, evidence=[f'{name}:2'])
            record['reply'] = json.dumps(reply)
            if reask:
                self.reasks.append(messages)
            record['prompt_sha256'] = jp.prompt_sha256(messages)
        return record


def test_off_ladder_steps_are_found_as_the_pinned_rubric_would_refuse_them():
    entry = {'rationale': 'r', 'evidence': ['u.py:1']}
    assert jp.off_ladder({'honest_limits': {**entry, 'step': 0.75}, 'absorption': {**entry, 'step': 1.0},
                          'abstraction': {**entry, 'step': '0.5'}, 'justification': {**entry, 'step': None},
                          'world_mapping': {**entry, 'step': float('nan')}}) == [
        'world_mapping', 'abstraction', 'justification', 'honest_limits']
    # On the ladder by the rubric's own test (``step in LADDER``), so never re-asked.
    assert jp.off_ladder({key: {**entry, 'step': step} for key, step in
                          zip(jp.DIMENSIONS, (0, 0.25, 0.5, 1, 0.0))}) == []
    # A missing or malformed dimension is the rubric's to refuse, not a reason to ask again.
    assert jp.off_ladder({'absorption': 'holds'}) == [] and jp.off_ladder(None) == []


def test_an_off_ladder_step_is_asked_about_once_and_the_new_answer_stands(tmp_path, stub_checkout):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    judge = OffTheLadder(accurate)
    result = jp.run(CORPUS, pins, trials, judge, 'salt', stub_checkout)
    reasked = [call for call in judge.calls if call[-1] == 'reask']
    assert reasked == [('b', 'ledger_cohesive', 0, 'reask'), ('b', 'ledger_cohesive', 1, 'reask')]
    assert len(judge.calls) == jp.planned(jp.load_corpus(CORPUS), trials) + 2
    # The re-ask is the first conversation, the judge's own answer, and the ladder restated.
    first = judge.reasks[0]
    cohesive = next(c for c in jp.load_corpus(CORPUS) if c['id'] == 'ledger_cohesive')
    assert first[:2] == jp.messages_for(cohesive, jp.canary('salt', 'b', 'ledger_cohesive', 0), 'salt')
    assert first[2][0] == 'assistant' and '0.75' in first[2][1]
    assert first[3] == ('user', jp.REASK_PROMPT.format(dimensions='honest_limits'))
    assert result['parsing']['b']['reasked'] == 2 and result['parsing']['b']['off_ladder'] == 0
    trial = result['cases'][[c['id'] for c in result['cases']].index('ledger_cohesive')]['judges']['b'][0]
    assert trial['state'] == 'verdict' and trial['reasked'] == ['honest_limits']
    assert trial['verdict']['dimensions']['honest_limits']['step'] == 0.5
    assert result['proof']['theory_panel_scored_and_discriminates']
    assert not result['proof']['established']  # still synthetic


@pytest.mark.skipif(not os.environ.get('GRIMOIRE_CODING_SKILLS'),
                    reason='set GRIMOIRE_CODING_SKILLS to a clean checkout at the calibration pin')
def test_the_pinned_rubric_scores_the_panel_once_the_off_ladder_judge_is_reasked(tmp_path):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    checkout = Path(os.environ['GRIMOIRE_CODING_SKILLS'])
    without = jp.run(CORPUS, pins, trials, OffTheLadder(accurate, fixed=None), 'salt', checkout)
    assert not without['theory']['units']['ledger_cohesive']['scored']
    result = jp.run(CORPUS, pins, trials, OffTheLadder(accurate), 'salt', checkout)
    unit = result['theory']['units']['ledger_cohesive']
    assert unit['scored'] and unit['discriminates'] is True
    assert result['proof']['theory_panel_scored_and_discriminates']


def test_a_step_still_off_the_ladder_after_one_reask_is_unknown_and_never_asked_again(tmp_path, stub_checkout):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    judge = OffTheLadder(accurate, fixed=None)
    result = jp.run(CORPUS, pins, trials, judge, 'salt', stub_checkout)
    assert sum(call[-1] == 'reask' for call in judge.calls) == 2
    trial = result['cases'][[c['id'] for c in result['cases']].index('ledger_cohesive')]['judges']['b'][0]
    assert trial['state'] == 'unknown' and trial['reason'] == 'off_ladder'
    assert trial['verdict']['dimensions']['honest_limits']['step'] == 0.75  # kept as answered, never rounded
    assert result['parsing']['b'] == {**result['parsing']['b'], 'reasked': 2, 'off_ladder': 2}
    unit = result['theory']['units']['ledger_cohesive']
    assert not unit['scored'] and unit['trials'][0]['reason'] == 'off_ladder'
    assert not result['proof']['theory_panel_scored_and_discriminates']


class Reasked(Scripted):
    """Judge ``b`` answers the cohesive unit in trial 0 with honest_limits at 0.75, shaped by ``first``;
    its re-ask is the accurate answer, shaped by ``again`` (a function of the record)."""

    def __init__(self, decide, first=None, again=None, measurement='synthetic'):
        super().__init__(decide)
        self.first, self.again, self.measurement, self.asked = first, again, measurement, []

    def respond(self, pin, case, trial, messages, reask=False):
        record = super().respond(pin, case, trial, messages, reask)
        record['attempts'] = [{'outcome': 'ok', 'measurement': self.measurement}]
        if pin.judge_id != 'b' or case['id'] != 'ledger_cohesive' or trial != 0:
            return record
        if reask:
            self.asked.append(messages)
            return self.again(record) if self.again else record
        reply = json.loads(record['reply'])
        reply['dimensions']['honest_limits'].update(step=0.75, evidence=[jp.alias('salt', case['id']) + ':2'])
        record['reply'] = self.first(reply) if self.first else json.dumps(reply)
        return record


def cohesive_b(result):
    return next(c for c in result['cases'] if c['id'] == 'ledger_cohesive')['judges']['b'][0]


def run_reasked(tmp_path, judge, checkout=None):
    pins, trials, _ = jp.load_panel(panel_file(tmp_path))
    return jp.run(CORPUS, pins, trials, judge, 'salt', checkout)


def test_the_reask_names_the_dimensions_and_restates_the_ladder_without_choosing_a_rung():
    assert jp.REASK_PROMPT.format(dimensions='abstraction, honest_limits') == (
        'Your answer gave abstraction, honest_limits a step that is not on the ladder. Every step must be\n'
        'exactly one of 1.0 (holds), 0.5 (partial), 0.25 (strained) or 0.0 (absent); there is no step between\n'
        'them. Answer again with one complete JSON object in the same format, and nothing else.')


def uncovered(reply):
    return {**reply, 'files': [{**reply['files'][0], 'canary': 'skipped'}]}


def uncited_abstraction(reply):
    return {**reply, 'dimensions': {**reply['dimensions'], 'abstraction': {
        'step': 0.5, 'rationale': 'r', 'evidence': ['elsewhere.py:2']}}}


@pytest.mark.parametrize('fault, reason', [
    (uncovered, 'coverage'),
    # A lowered step with no listed line is re-asked only in an answer that copied the canary.
    (lambda reply: uncovered(uncited_abstraction(reply)), 'citation'),
    # Evidence that is not a list of citations is not a citation to restate.
    (lambda reply: {**reply, 'dimensions': {**reply['dimensions'], 'abstraction': {
        'step': 0.5, 'rationale': 'r', 'evidence': 'elsewhere.py:2'}}}, 'citation'),
])
def test_an_answer_with_another_fault_is_never_reasked_even_off_the_ladder(tmp_path, fault, reason):
    judge = Reasked(accurate, first=lambda reply: json.dumps(fault(reply)))
    result = run_reasked(tmp_path, judge)
    assert not any(call[-1] == 'reask' for call in judge.calls)
    trial = cohesive_b(result)
    assert trial['reason'] == reason and trial['off_ladder'] == ['honest_limits'] and 'reasked' not in trial
    assert 'uncited' not in trial
    assert result['parsing']['b']['off_ladder'] == 1 and result['parsing']['b']['reasked'] == 0


def test_dropped_citations_are_reported_by_shape_never_by_text():
    name, count = 'unit_0123456789.py', 20
    shapes = {cite: jp.citation_shape(cite, name, count) for cite in (
        f'{name}:21', f'{name}:0', 'unit_0123456789:3', f'src/{name}:3', 'other.py:3', f'{name}:3-5',
        f'{name}: 4 - 6', f'{name}:L3', f'{name}:3,4', f'{name}:', 'line 3', name, 3, None)}
    assert list(shapes.values()) == [
        'unlisted_line', 'unlisted_line', 'other_file', 'other_file', 'other_file', 'range', 'range',
        'malformed', 'malformed', 'malformed', 'no_line', 'no_line', 'not_text', 'not_text']
    case = {'id': 'u', 'task': 'theory', 'source': 'a = 1\nb = 2\n'}
    verdict = jp.perfect_uncovered_verdict('u')
    verdict['dimensions']['absorption']['evidence'] = ['f.py:9', 'f.py:1-2', 'f.py:1']
    result = jp.parse_theory(theory_answer(verdict), case, 'tok', 'f.py')
    assert result['citations_dropped'] == 2 and result['dropped_shapes'] == {'unlisted_line': 1, 'range': 1}


def test_a_ladder_only_reask_is_word_for_word_what_parser_version_3_sent():
    assert jp.reask_text(['honest_limits'], [], 'f.py') == jp.REASK_PROMPT.format(dimensions='honest_limits')


def unlisted(step):
    def first(reply):
        count = len(next(c for c in jp.load_corpus(CORPUS) if c['id'] == 'ledger_cohesive')['source'].splitlines())
        name = jp.alias('salt', 'ledger_cohesive')
        reply['dimensions']['honest_limits'].update(step=step, evidence=[f'{name}:{count + 4}', f'{name}:2-3'])
        return json.dumps(reply)
    return first


def test_a_lowered_step_with_no_listed_line_is_reasked_once_and_the_new_answer_stands(tmp_path, stub_checkout):
    judge = Reasked(accurate, first=unlisted(0.5))
    result = run_reasked(tmp_path, judge, stub_checkout)
    assert [call for call in judge.calls if call[-1] == 'reask'] == [('b', 'ledger_cohesive', 0, 'reask')]
    name = jp.alias('salt', 'ledger_cohesive')
    assert judge.asked[0][-1] == ('user', (
        'Your answer gave honest_limits a step below 1.0 with no citation of a line the listing\n'
        f'has. Each citation must be exactly "{name}:<line>", where <line> is one line number the listing shows. '
        'Answer again with one complete JSON object in the same format, and nothing else.'))
    trial = cohesive_b(result)
    assert trial['state'] == 'verdict' and trial['reasked'] == ['honest_limits']
    assert trial['first_answer']['citations_dropped'] == 2
    assert trial['first_answer']['dropped_shapes'] == {'unlisted_line': 1, 'range': 1}
    row = result['parsing']['b']
    assert row['reasked'] == 1 and row['uncited'] == 0 and row['dropped_shapes'] == {'unlisted_line': 1, 'range': 1}
    assert result['theory']['units']['ledger_cohesive']['trials'][0]['reasked'] == ['b']
    assert result['proof']['theory_panel_scored_and_discriminates']


def test_an_off_ladder_and_uncited_step_gets_one_reask_naming_both(tmp_path, stub_checkout):
    judge = Reasked(accurate, first=unlisted(0.75))
    result = run_reasked(tmp_path, judge, stub_checkout)
    assert sum(call[-1] == 'reask' for call in judge.calls) == 1
    text = judge.asked[0][-1][1]
    assert text == jp.reask_text(['honest_limits'], ['honest_limits'], jp.alias('salt', 'ledger_cohesive'))
    assert text.startswith(jp.REASK_LADDER.format(dimensions='honest_limits') + ' Your answer gave honest_limits a step below')
    trial = cohesive_b(result)
    assert trial['state'] == 'verdict' and trial['reasked'] == ['honest_limits']
    assert result['proof']['theory_panel_scored_and_discriminates']


def test_a_step_still_uncited_after_its_reask_is_unknown_and_never_asked_again(tmp_path, stub_checkout):
    def still(record):
        record['reply'] = unlisted(0.5)(json.loads(record['reply']))
        return record
    judge = Reasked(accurate, first=unlisted(0.5), again=still)
    result = run_reasked(tmp_path, judge, stub_checkout)
    assert sum(call[-1] == 'reask' for call in judge.calls) == 1
    trial = cohesive_b(result)
    assert trial['reason'] == 'citation' and trial['uncited'] == ['honest_limits']
    assert result['parsing']['b']['uncited'] == 1 and result['parsing']['b']['citations_dropped'] == 4
    assert not result['proof']['theory_panel_scored_and_discriminates']


def test_a_refused_reask_keeps_the_first_answers_uncited_step(tmp_path, stub_checkout):
    def refused(record):
        return {**record, 'status': 'refused', 'reply': None}
    result = run_reasked(tmp_path, Reasked(accurate, first=unlisted(0.5), again=refused), stub_checkout)
    trial = cohesive_b(result)
    assert trial['reason'] == 'call_refused' and trial['uncited'] == ['honest_limits'] and 'off_ladder' not in trial
    assert result['parsing']['b']['uncited'] == 1


def test_a_reasked_trial_still_reports_what_its_first_answer_needed(tmp_path):
    def messy(reply):
        reply['dimensions']['absorption']['evidence'] = ['elsewhere.py:1']  # dropped; 1.0 needs none
        return 'Reading it through first.\n' + json.dumps(reply)
    result = run_reasked(tmp_path, Reasked(accurate, first=messy))
    trial = cohesive_b(result)
    assert trial['state'] == 'verdict' and trial['reasked'] == ['honest_limits']
    assert {k: v for k, v in trial['first_answer'].items() if k != 'verdict'} == {
        'text_outside_json': True, 'ambiguous': False, 'citations_dropped': 1,
        'dropped_shapes': {'other_file': 1}}
    assert trial['first_answer']['verdict']['dimensions']['honest_limits']['step'] == 0.75
    row = result['parsing']['b']
    assert row['answers'] == 37 and row['text_outside_json'] == 1 and row['citations_dropped'] == 1
    assert result['theory']['units']['ledger_cohesive']['trials'][0]['reasked'] == ['b']
    assert result['theory']['units']['ledger_cohesive']['trials'][1]['reasked'] == []


def test_a_reask_with_no_answer_leaves_the_trial_off_the_ladder(tmp_path):
    def refused(record):
        return {**record, 'status': 'refused', 'reply': None}
    result = run_reasked(tmp_path, Reasked(accurate, again=refused))
    trial = cohesive_b(result)
    assert trial['reason'] == 'call_refused' and trial['off_ladder'] == ['honest_limits']
    assert result['parsing']['b']['reasked'] == 1 and result['parsing']['b']['off_ladder'] == 1
    assert result['parsing']['b']['answers'] == 37  # the first answer was read; the re-ask gave none
    # Asked again, but no answer came from the re-ask, so the trial names no re-asked judge.
    assert result['theory']['units']['ledger_cohesive']['trials'][0]['reasked'] == []


def test_off_ladder_steps_are_read_as_the_rung_the_rubric_checks_most():
    entry = {'rationale': 'r', 'evidence': []}
    verdict = {'unit': 'u', 'dimensions': {'absorption': {**entry, 'step': 0.75}, 'world_mapping': {**entry, 'step': 1.5},
                                           'abstraction': {**entry, 'step': '1.0'},
                                           'justification': {**entry, 'step': float('nan')},
                                           'honest_limits': {**entry, 'step': 0.25}}}
    steps = {key: e['step'] for key, e in jp.laddered(verdict)['dimensions'].items()}
    assert steps == {'absorption': 0.5, 'world_mapping': 1.0, 'abstraction': 0.5, 'justification': 0.5,
                     'honest_limits': 0.25}
    assert verdict['dimensions']['absorption']['step'] == 0.75  # the answer itself is never changed


def test_a_reask_counts_only_if_the_pinned_rubric_accepts_the_first_answer(tmp_path, stub_checkout, monkeypatch):
    real, seen = jp.upstream_score, []

    def refuses_two_rehearsals(checkout, verdicts, size):
        seen.append(verdicts)
        if any(len(v.get('rehearsals', [])) < 3 for v in verdicts):
            return {'error': 'absorption needs at least 3 rehearsal(s)'}
        return real(checkout, verdicts, size)
    monkeypatch.setattr(jp, 'upstream_score', refuses_two_rehearsals)

    def two_rehearsals(reply):
        return json.dumps({**reply, 'rehearsals': reply['rehearsals'][:2]})
    result = run_reasked(tmp_path, Reasked(accurate, first=two_rehearsals), stub_checkout)
    assert cohesive_b(result)['state'] == 'verdict'  # the re-asked answer itself is fine
    trial = result['theory']['units']['ledger_cohesive']['trials'][0]
    assert trial['reason'] == 'first_answer_invalid' and trial['profile_score'] is None
    assert not result['proof']['theory_panel_scored_and_discriminates']
    checked = [v for v in seen if v[0].get('rehearsals') and len(v[0]['rehearsals']) == 2]
    assert checked and checked[0][0]['dimensions']['honest_limits']['step'] == 0.5  # read as a rung

    clean = run_reasked(tmp_path, Reasked(accurate), stub_checkout)
    assert clean['theory']['units']['ledger_cohesive']['trials'][0]['reason'] is None


@pytest.mark.skipif(not os.environ.get('GRIMOIRE_CODING_SKILLS'),
                    reason='set GRIMOIRE_CODING_SKILLS to a clean checkout at the calibration pin')
@pytest.mark.parametrize('fault', [
    lambda reply: {**reply, 'dimensions': {**reply['dimensions'], 'abstraction': {
        'step': 0.5, 'rationale': 'r', 'evidence': []}}},
    lambda reply: {**reply, 'rehearsals': reply['rehearsals'][:2]},
    lambda reply: {**reply, 'theory': ''},
])
def test_the_pinned_rubric_refuses_a_reask_whose_first_answer_had_another_fault(tmp_path, fault):
    checkout = Path(os.environ['GRIMOIRE_CODING_SKILLS'])
    result = run_reasked(tmp_path, Reasked(accurate, first=lambda reply: json.dumps(fault(reply))), checkout)
    assert result['theory']['units']['ledger_cohesive']['trials'][0]['reason'] == 'first_answer_invalid'
    assert not result['proof']['theory_panel_scored_and_discriminates']


def test_a_reask_is_measured_and_watched_for_drift_with_its_trial(tmp_path):
    reported = run_reasked(tmp_path, Reasked(accurate, measurement='provider_reported'))
    assert cohesive_b(reported)['measurement'] == 'provider_reported'
    assert reported['proof']['provider_reported_only']

    class Estimated(Reasked):
        def respond(self, pin, case, trial, messages, reask=False):
            record = super().respond(pin, case, trial, messages, reask)
            if reask:
                record['attempts'] = [{'outcome': 'ok', 'measurement': 'estimated'}]
            return record
    estimated = run_reasked(tmp_path, Estimated(accurate, measurement='provider_reported'))
    assert cohesive_b(estimated)['measurement'] == 'mixed'
    assert not estimated['proof']['provider_reported_only']

    class Moved(Reasked):
        """The first answer alone was served by another snapshot; the re-ask by the usual one."""
        def respond(self, pin, case, trial, messages, reask=False):
            record = super().respond(pin, case, trial, messages, reask)
            if (pin.judge_id, case['id'], trial, reask) == ('b', 'ledger_cohesive', 0, False):
                record['served_model'] = 'judge-model-2026-01-01'
            return record
    for again in (None, lambda r: {**r, 'served_model': 'judge-model-2026-01-01'}):
        moved = run_reasked(tmp_path, (Moved if again is None else Reasked)(accurate, again=again))
        assert moved['served_models']['b'] == ['judge-model', 'judge-model-2026-01-01']
        assert all(t['reason'] == 'model_drift' for c in moved['cases'] for t in c.get('judges', {}).get('b', []))


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
    assert replayed['capture_parser_version'] == jp.PARSER_VERSION
    assert replayed['proof']['captured_for_this_parser']
    # A capture taken before the parser changed replays, but it shaped the change: it proves nothing.
    manifest = json.loads((capture / 'capture.json').read_text())
    for older in ({k: v for k, v in manifest.items() if k != 'parser_version'}, {**manifest, 'parser_version': 1}):
        (capture / 'capture.json').write_text(json.dumps(older))
        reread = jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt')
        assert reread['defects'] == replayed['defects']
        assert not reread['proof']['captured_for_this_parser'] and not reread['proof']['established']
    (capture / 'capture.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='ceiling'):
        live.respond(pins[0], cases[0], 0, jp.messages_for(cases[0], 'x', 'salt'))

    record = capture / 'calls/a/facade/0.json'
    record.write_text(record.read_text().replace('"prompt_sha256": "', '"prompt_sha256": "0'))
    with pytest.raises(ValueError, match='stale'):
        jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt')
    other = panel_file(tmp_path, model='judge-model-2')
    with pytest.raises(ValueError, match='different pin'):
        jp.run(CORPUS, jp.load_panel(other)[0], trials, jp.ReplayJudge(capture), 'salt')


def test_a_reask_is_captured_counted_against_the_ceiling_and_replayed(tmp_path, monkeypatch, stub_checkout):
    from grimoire.testing.providers import Reply, ScriptedTransport

    monkeypatch.setenv('SYNTHETIC_JUDGE_KEY', 'sk-synthetic')
    pins, trials, configs = jp.load_panel(panel_file(tmp_path))
    cases = jp.load_corpus(CORPUS)
    # The same answers, call by call, as a provider would stream them.
    replies: dict[str, list] = {}

    class Recording(OffTheLadder):
        def respond(self, pin, case, trial, messages, reask=False):
            record = super().respond(pin, case, trial, messages, reask)
            replies.setdefault(pin.judge_id, []).append(Reply(deltas=(record['reply'],), model='judge-model'))
            return record
    jp.collect(cases, pins, trials, Recording(accurate), 'salt')
    base = jp.planned(cases, trials)

    def live(out, ceiling):
        transports = {judge_id: ScriptedTransport(list(steps)) for judge_id, steps in replies.items()}
        return jp.LiveJudge(pins, configs, tmp_path / out, 'salt', ceiling, spend_cap='0 USD',
                            transports=transports), transports
    # The plan's own ceiling is never reached by the base calls alone: a re-ask is a billed call.
    short, _ = live('short', base)
    with pytest.raises(RuntimeError, match='ceiling'):
        jp.collect(cases, pins, trials, short, 'salt')
    judge, transports = live('capture', base + jp.reasks_at_most(cases, trials))
    jp.collect(cases, pins, trials, judge, 'salt')
    assert judge.calls == base + 2 and all(t.remaining == 0 for t in transports.values())
    capture = tmp_path / 'capture'
    assert sorted(p.name for p in (capture / 'calls/b/ledger_cohesive').iterdir()) == [
        '0.json', '0.reask.json', '1.json', '1.reask.json', '2.json']
    replayed = jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt', stub_checkout)
    assert replayed['parsing']['b']['reasked'] == 2 and replayed['parsing']['b']['off_ladder'] == 0
    assert replayed['proof']['theory_panel_scored_and_discriminates']
    # A capture without the re-asks (one taken before them) reads those answers as unknown.
    (capture / 'calls/b/ledger_cohesive/0.reask.json').unlink()
    older = jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt', stub_checkout)
    trial = next(c for c in older['cases'] if c['id'] == 'ledger_cohesive')['judges']['b'][0]
    assert trial['reason'] == 'off_ladder' and 'reasked' not in trial
    assert not older['proof']['theory_panel_scored_and_discriminates']
    # A re-ask recorded for other words, or for another first answer, is stale.
    record = capture / 'calls/b/ledger_cohesive/1.reask.json'
    record.write_text(record.read_text().replace('"prompt_sha256": "', '"prompt_sha256": "0'))
    with pytest.raises(ValueError, match='stale'):
        jp.run(CORPUS, pins, trials, jp.ReplayJudge(capture), 'salt')


@pytest.mark.parametrize('extra', [[], ['--confirm-billing'], ['--confirm-billing', '--spend-cap', '5 USD'],
                                   ['--confirm-billing', '--spend-cap', ' ', '--max-calls', '108'],
                                   ['--confirm-billing', '--spend-cap', 'lots', '--max-calls', '108'],
                                   ['--confirm-billing', '--spend-cap', '5 USD', '--max-calls', '125']])
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
    # 12 cases, 3 judges, 3 trials; each of the 18 theory answers may be re-asked once.
    assert plan['planned_calls'] == 108 and plan['reasks_at_most'] == 18
    assert plan['max_calls'] == 126 and plan['request_ceiling'] == 126
    assert plan['max_output_tokens'] == {'a': 512, 'b': 512, 'c': 512}


@pytest.mark.skipif(not os.environ.get('GRIMOIRE_CODING_SKILLS'),
                    reason='set GRIMOIRE_CODING_SKILLS to a clean checkout at the calibration pin')
def test_pinned_upstream_rubric_awards_a_perfect_score_to_unread_verdicts(tmp_path):
    """Reproduces the upstream inaccuracy the profile gate corrects (docs/CALIBRATION.md)."""
    result, _ = report(tmp_path, accurate, checkout=Path(os.environ['GRIMOIRE_CODING_SKILLS']))
    assert result['theory']['missing_analysis_probe']['upstream_score'] == 100.0
    assert result['theory']['missing_analysis_probe']['profile_score'] is None
    assert result['theory']['units']['ledger_cohesive']['discriminates'] is True
