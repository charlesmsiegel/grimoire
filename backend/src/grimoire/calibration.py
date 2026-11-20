"""Offline, report-only calibration of explicitly supplied synthetic source.

No provider calls, application state, or blocking grade. Structural detector
flags are candidates; they are not independent actionable-defect judgments.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

REVISION = '74f40dbd4bc301e276702b2854478f0e2eb2ac6a'
DETECTORS = {'coroutines': 'find_unawaited_coroutines.py',
             'overengineering': 'find_overengineering.py'}


def verify_checkout(checkout: Path) -> None:
    """Refuse a different or modified tool, before executing any of its code."""
    revision = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=10)
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise ValueError('external tool revision does not match the calibration pin')
    status = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain',
                             '--untracked-files=all'], capture_output=True, text=True, timeout=10)
    if status.returncode or status.stdout:
        raise ValueError('external tool checkout is not clean')


def classify(stdout: str, stderr: str, code: int, parsed: bool, finding: str) -> dict:
    """Fail closed on missing analysis, including successful empty skipped runs."""
    unknown = {'state': 'unknown', 'lines': []}
    if code or stderr or not parsed:
        return unknown
    try:
        rows = json.loads(stdout)
    except (ValueError, TypeError):
        return unknown
    if not isinstance(rows, list):
        return unknown
    for row in rows:
        if (not isinstance(row, dict) or
                not isinstance(row.get('smell_type', row.get('issue_type')), str) or
                type(row.get('line')) is not int or row['line'] < 1):
            return unknown
    lines = sorted({row['line'] for row in rows
                    if row.get('smell_type', row.get('issue_type')) == finding})
    return {'state': 'candidate' if lines else 'clear', 'lines': lines}


def summarize(cases: list[dict]) -> dict:
    """Case-level structural agreement; unknown and inconsistent cases aren't successes."""
    counts = dict(tp=0, fp=0, fn=0, tn=0)
    unknown = inconsistent = repeatable = 0
    for case in cases:
        trials = case['trials']
        if not trials:
            unknown += 1
            continue
        if any(trial != trials[0] for trial in trials[1:]):
            inconsistent += 1
            continue
        repeatable += 1
        if case['expected'] is None or trials[0]['state'] == 'unknown':
            unknown += 1
            continue
        flagged = trials[0]['state'] == 'candidate'
        key = ('tp' if flagged else 'fn') if case['expected'] else ('fp' if flagged else 'tn')
        counts[key] += 1
    total = sum(counts.values())
    return {'confusion': counts, 'unknown': unknown, 'inconsistent': inconsistent,
            'repeatable': repeatable,
            'structural_accuracy': (counts['tp'] + counts['tn']) / total if total else None}


def calibrate(checkout: Path, corpus: Path) -> dict:
    """Run three fresh processes per fixture; capture coverage separately from findings."""
    checkout = checkout.resolve()
    verify_checkout(checkout)
    manifest_bytes = (corpus / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, list) or not manifest:
        raise ValueError('corpus must contain cases')
    cases = []
    seen = set()
    digest = hashlib.sha256(manifest_bytes)
    with tempfile.TemporaryDirectory(prefix='grimoire-calibration-') as temporary:
        for case in manifest:
            if (not isinstance(case, dict) or not isinstance(case.get('id'), str) or
                    not case['id'].isascii() or not case['id'].isidentifier() or case['id'] in seen or
                    not isinstance(case.get('specialist'), str) or case['specialist'] not in DETECTORS or
                    not isinstance(case.get('finding'), str) or
                    'structural_expected' not in case or type(case['structural_expected']) not in (bool, type(None)) or
                    not isinstance(case.get('file'), str) or
                    Path(case['file']).name != case['file'] or '\\' in case['file']):
                raise ValueError('invalid corpus case')
            seen.add(case['id'])
            source_path = corpus / case['file']
            if source_path.is_symlink():
                raise ValueError('corpus sources must be ordinary files')
            source_bytes = source_path.read_bytes()
            digest.update(case['file'].encode())
            digest.update(source_bytes)
            source = source_bytes.decode('utf-8')
            path = Path(temporary) / (case['id'] + '.py')
            path.write_text(source, encoding='utf-8')
            try:
                ast.parse(source)
                parsed = True
            except (SyntaxError, ValueError):
                parsed = False
            trials, diagnostics = [], []
            script = checkout / 'skills/python-code-doctor/scripts' / DETECTORS[case['specialist']]
            for _ in range(3):
                try:
                    run = subprocess.run([sys.executable, '-B', str(script), str(path), '--format', 'json'],
                                         capture_output=True, text=True, timeout=30)
                    trials.append(classify(run.stdout, run.stderr, run.returncode, parsed, case['finding']))
                    diagnostics.append({'exit': run.returncode,
                                        'stderr': run.stderr.replace(str(path), case['file']),
                                        'stdout': run.stdout.replace(str(path), case['file'])})
                except subprocess.TimeoutExpired:
                    trials.append({'state': 'unknown', 'lines': []})
                    diagnostics.append({'error': 'detector timeout'})
            cases.append({'id': case['id'], 'specialist': case['specialist'],
                          'expected': case['structural_expected'],
                          'actionable_defect_label': case.get('actionable_defect'),
                          'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                          'syntax_parsed': parsed, 'trials': trials, 'diagnostics': diagnostics})
    verify_checkout(checkout)
    return {'format': 'grimoire.calibration/1', 'tool_revision': REVISION,
            'python': platform.python_version(), 'corpus_sha256': digest.hexdigest(),
            'blocking': False, 'cases': cases,
            'by_specialist': {name: summarize([case for case in cases if case['specialist'] == name])
                              for name in DETECTORS},
            'confirmed_defects': [],
            'independent_defect_judge': {'state': 'unknown', 'reason': 'no pinned independent judge execution'},
            'theory_judge': {'state': 'unknown', 'reason': 'no pinned independent model and three-judge trials'}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    parser.add_argument('corpus', type=Path)
    args = parser.parse_args()
    try:
        report = calibrate(args.checkout, args.corpus)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f'calibration unavailable: {type(error).__name__}\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
