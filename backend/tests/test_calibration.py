"""Calibration must not turn missing evidence into a clean verdict."""
import json
import subprocess

import pytest

from grimoire import calibration
from grimoire.calibration import classify, summarize, verify_checkout


@pytest.mark.parametrize('stdout,stderr,code,parsed', [
    ('[]', 'skipped parse', 0, False), ('[]', '', 1, True),
    ('{}', '', 0, True), ('null', '', 0, True), ('[{}]', '', 0, True),
    ('not json', '', 0, True), ('[]', 'detector failed', 0, True),
])
def test_missing_or_invalid_analysis_is_unknown(stdout, stderr, code, parsed):
    assert classify(stdout, stderr, code, parsed, 'unawaited_coroutine')['state'] == 'unknown'


def test_findings_are_candidates_not_confirmed_defects():
    raw = json.dumps([{'smell_type': 'unawaited_coroutine', 'line': 5}])
    result = classify(raw, '', 0, True, 'unawaited_coroutine')
    assert result == {'state': 'candidate', 'lines': [5]}


def test_structural_accuracy_separates_unknown_and_repeatability():
    clear = {'state': 'clear', 'lines': []}
    flag = {'state': 'candidate', 'lines': [5]}
    unknown = {'state': 'unknown', 'lines': []}
    cases = [
        {'expected': True, 'trials': [flag, flag, flag]},
        {'expected': False, 'trials': [flag, flag, flag]},
        {'expected': True, 'trials': [clear, clear, clear]},
        {'expected': False, 'trials': [clear, clear, clear]},
        {'expected': None, 'trials': [unknown, unknown, unknown]},
        {'expected': True, 'trials': [flag, clear, flag]},
    ]
    result = summarize(cases)
    assert result['confusion'] == {'tp': 1, 'fp': 1, 'fn': 1, 'tn': 1}
    assert result['unknown'] == 1
    assert result['inconsistent'] == 1
    assert result['structural_accuracy'] == 0.5
    assert result['repeatable'] == 5
    assert summarize([])['structural_accuracy'] is None
    assert summarize([{'expected': None, 'trials': [clear]*3}])['structural_accuracy'] is None


def test_external_checkout_must_match_pin(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    with pytest.raises(ValueError, match='revision'):
        verify_checkout(tmp_path)


def test_pipeline_tracks_skipped_analysis_and_rejects_dirty_tool(tmp_path, monkeypatch):
    tool = tmp_path / 'tool'
    scripts = tool / 'skills/python-code-doctor/scripts'
    scripts.mkdir(parents=True)
    script = scripts / 'find_unawaited_coroutines.py'
    script.write_text('import ast, pathlib, sys\n'
                      'try: ast.parse(pathlib.Path(sys.argv[1]).read_text())\n'
                      'except SyntaxError: print("skipped", file=sys.stderr)\n'
                      'print("[]")\n')
    subprocess.run(['git', 'init', str(tool)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tool), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tool), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-m', 'synthetic tool'],
                   check=True, capture_output=True)
    revision = subprocess.check_output(['git', '-C', str(tool), 'rev-parse', 'HEAD'], text=True).strip()
    monkeypatch.setattr(calibration, 'REVISION', revision)
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    (corpus / 'bad.txt').write_text('def broken(\n')
    (corpus / 'manifest.json').write_text(json.dumps([{
        'id': 'bad', 'file': 'bad.txt', 'specialist': 'coroutines',
        'finding': 'unawaited_coroutine', 'structural_expected': None}]))
    report = calibration.calibrate(tool, corpus)
    assert report['cases'][0]['syntax_parsed'] is False
    assert report['by_specialist']['coroutines']['unknown'] == 1
    assert report['by_specialist']['coroutines']['structural_accuracy'] is None
    assert all(t['state'] == 'unknown' for t in report['cases'][0]['trials'])
    for malformed in [
        {'specialist': []},
        {'specialist': 'coroutines', 'omit_expected': True},
    ]:
        case = {'id': 'bad', 'file': 'bad.txt', 'specialist': malformed['specialist'],
                'finding': 'unawaited_coroutine', 'structural_expected': None}
        if malformed.get('omit_expected'):
            del case['structural_expected']
        (corpus / 'manifest.json').write_text(json.dumps([case]))
        with pytest.raises(ValueError, match='invalid corpus'):
            calibration.calibrate(tool, corpus)
    script.write_text('print("[]")\n')
    with pytest.raises(ValueError, match='clean'):
        calibration.calibrate(tool, corpus)
