"""Measure only marked synthetic libraries, on disposable copies.

The caller's tree is never mutated. Clock aging uses the cache's public seam
and leaves perf_counter real: source eligibility is controlled, timings are not.
Run from backend/: python -m scripts.benchmark_compiled ROOT --output report.json.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.synth_library import MARKER, Shape

from grimoire.routes import campaigns, shell, todo
from grimoire.store import (
    compiled,
    frontmatter,
    maintenance_reports,
    paths,
    proclock,
    search,
    statcache,
    tokens,
)
from grimoire.store.campaigns import read as campaign_read
from grimoire.store.scenes import read as scene_read
from grimoire.store.worlds import read as world_read


def _manifest(target: Path) -> dict:
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('benchmark requires a plain synthetic root')
    try:
        marker = target / MARKER
        if marker.is_symlink() or marker.stat().st_size > 4096:
            raise ValueError('invalid synthetic marker')
        value = json.loads(marker.read_text(encoding='utf-8'))
        shape = Shape(**value['shape'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError('benchmark requires a generator marker') from error
    if value.get('schema') != 1 or value.get('generator') != 'grimoire.synthetic':
        raise ValueError('benchmark requires a generator marker')
    if any(type(size) is not int or size < 0 for size in vars(shape).values()):
        raise ValueError('invalid synthetic shape')
    for member in target.rglob('*'):
        if member.is_symlink() or getattr(member.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('synthetic benchmark refuses linked members')
    return value


def _clear() -> None:
    compiled.close()
    statcache._cache.clear()
    search._POOL.clear()
    search._LATEST.clear()
    scene_read._SCENE_POOL.clear()
    tokens._RECORD_POOL.clear()
    tokens._large_body_digest.cache_clear()


def _samples(call, repeats: int, before=None) -> dict:
    samples = []
    for _ in range(repeats):
        if before is not None:
            before()
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000)
    return {'samples_ms': samples, 'median_ms': statistics.median(samples)}


@contextlib.contextmanager
def _aged(root: Path, locks: Path):
    original_clock = compiled.filesystem_clock
    latest = max((max(p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in root.rglob('*') if p.is_file()), default=0)
    now = latest + compiled.PERSIST_WINDOW_NS + 1_000_000_000
    real_time_ns = time.time_ns
    def aged_now():
        return max(now, real_time_ns() + compiled.PERSIST_WINDOW_NS + 1_000_000_000)
    def clock(candidate):
        observed = original_clock(candidate)
        return replace(observed, now_ns=max(aged_now(), observed.now_ns)) if observed is not None else None
    with patch.object(compiled, 'filesystem_clock', clock), patch.object(statcache.time, 'time_ns', aged_now), \
            patch.object(proclock, 'lock_dir', lambda: locks), \
            patch.object(maintenance_reports, 'device_key', lambda candidate: 'a' * 64), paths.pin_library(root):
        yield


def _probe_file(root: Path, path: Path, label: str, reader, repeats: int) -> dict:
    kind = compiled.Kind('benchmark:' + label, 1, ('stem', 'dirname'), ('stem', 'dirname'), True)
    with patch.dict(compiled.KINDS, {kind.name: kind}):
        direct = _samples(lambda: reader(path), repeats)
        calls = []
        def compute(view, params):
            calls.append(1)
            text = view.files[0].data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
            if label == 'search_markdown':
                return list(search._markdown_text(text, params['stem']))
            if label == 'search_card':
                return list(search._card_text(text, params['stem']))
            parser = world_read._world_row_value if label == 'world_row' else campaign_read._campaign_row_value
            return parser(params['dirname'], text)
        def cached():
            with compiled.batch(root) as request:
                return request.derive(kind, [compiled.Input('body', path)], params={'stem': path.stem, 'dirname': path.parent.name}, compute=compute)
        cached()
        _clear()
        warm = _samples(cached, repeats, _clear)
        process_warm = _samples(cached, repeats)
    return {'direct': direct, 'warm_process': process_warm, 'stat_lookup_decode': warm, 'warm_compute_calls': len(calls) - 1,
            'selected': len(calls) == 1 and warm['median_ms'] < direct['median_ms']}


def _exact_count(body: str):
    value = tokens.count_tokens(body)
    return value if tokens.last_counter() == tokens.ENCODING else compiled.Uncached(value)


def _probes(root: Path, repeats: int) -> dict:
    result = {}
    markdown = next(root.glob('campaigns/*/scenes/*.md'), None)
    card = next(root.glob('worlds/*/characters/*/*.json'), None)
    if markdown:
        result['search_markdown'] = _probe_file(root, markdown, 'search_markdown', search._markdown_doc, repeats)
    if card:
        result['search_card'] = _probe_file(root, card, 'search_card', search._card_doc, repeats)
    for label, pattern, reader in [('world_row', 'worlds/*/world.md', world_read._world_row),
                                   ('campaign_row', 'campaigns/*/campaign.md', campaign_read._campaign_row)]:
        path = next(root.glob(pattern), None)
        if path:
            # Bypass the current outer memo, measuring its original parse.
            def direct(candidate, row_reader=reader):
                statcache._cache.clear()
                return row_reader(candidate.parent, candidate)
            result[label] = _probe_file(root, path, label, direct, repeats)
    if markdown:
        body = frontmatter.parse_frontmatter(markdown.read_text(encoding='utf-8'))[1].strip()
        direct = _samples(lambda: tokens.count_tokens(body), repeats)
        encoder = tokens.last_counter() == tokens.ENCODING
        kind = compiled.Kind('benchmark:tokens', 1, (), ('text_hash', 'encoding'), False)
        params = {'text_hash': hashlib.sha256(body.encode()).hexdigest(), 'encoding': tokens.ENCODING}
        with patch.dict(compiled.KINDS, {kind.name: kind}):
            def cached():
                with compiled.batch(root) as request:
                    return request.derive(kind, [], params=params, compute=lambda view, values: _exact_count(body))
            cached()
            _clear()
            warm = _samples(cached, repeats, _clear)
            process_warm = _samples(cached, repeats)
            wrapper_warm = _samples(lambda: tokens.record_tokens(markdown, body), repeats, _clear)
        result['tokens'] = {'direct': direct, 'stat_lookup_decode': warm,
                            'warm_process': process_warm, 'record_tokens_restart': wrapper_warm,
                            'text_chars': len(body), 'persist_min_chars': tokens.PERSIST_MIN_CHARS,
                            'encoder': encoder, 'selected': encoder and len(body) >= tokens.PERSIST_MIN_CHARS
                            and wrapper_warm['median_ms'] < direct['median_ms']}
    return result


def _pages(root: Path) -> dict:
    cid = next((p.name for p in (root / 'campaigns').iterdir() if p.is_dir()), '')
    return {'world_shelf': world_read.list_worlds, 'campaign_shelf': campaign_read.list_campaigns,
            'todo': todo.get_todo, 'search': lambda: list(search.walk()),
            'campaign_hub': lambda: {'campaign': campaigns.get_campaign(cid), 'shell': shell.get_shell(cid)} if cid else {}}


def _counted_samples(root: Path, call, repeats: int, before=None) -> dict:
    counts = Counter()
    active = False
    def measured():
        nonlocal active
        active = True
        try:
            return call()
        finally:
            active = False
    original_bytes, original_text = Path.read_bytes, Path.read_text
    def read_bytes(candidate):
        if active and candidate.is_relative_to(root) and '.cache' not in candidate.relative_to(root).parts:
            counts['read_bytes'] += 1
        return original_bytes(candidate)
    def read_text(candidate, *args, **kwargs):
        if active and candidate.is_relative_to(root) and '.cache' not in candidate.relative_to(root).parts:
            counts['read_text'] += 1
        return original_text(candidate, *args, **kwargs)
    with patch.object(Path, 'read_bytes', read_bytes), patch.object(Path, 'read_text', read_text), \
            patch.object(search, '_markdown_text', wraps=search._markdown_text) as markdown_calls, \
            patch.object(search, '_card_text', wraps=search._card_text) as card_calls, \
            patch.object(tokens, 'count_tokens', wraps=tokens.count_tokens) as token_calls:
        samples = _samples(measured, repeats, before)
    return {**samples, 'reads': dict(counts), 'calls': {'markdown_extract': markdown_calls.call_count,
            'card_extract': card_calls.call_count, 'count_tokens': token_calls.call_count}}


def _prepare_case(root: Path, case: str, page: str) -> None:
    if case == 'cold':
        _clear()
        compiled.purge_for_delete(root)
    elif case == 'warm_restart':
        _clear()
    elif case == 'one_file_edited':
        pattern = ('worlds/*/world.md' if page == 'world_shelf' else
                   'campaigns/*/campaign.md' if page in {'campaign_shelf', 'campaign_hub'} else
                   'worlds/*/locations/*.md')
        member = next(root.glob(pattern), None)
        if member:
            member.write_bytes(member.read_bytes() + b'\nMara\n')


def _fresh(root: Path, locks: Path, job: str, name: str) -> dict:
    # A subprocess has no warmed BUILD, device classification, dependency
    # fingerprint, encoder, Python memo or SQLite connection. OS caches stay.
    payload = {'root': str(root), 'locks': str(locks), 'job': job, 'name': name}
    started = time.perf_counter()
    worker = 'import json,sys; from scripts.benchmark_compiled import _worker; print(json.dumps(_worker(json.load(sys.stdin))))'
    # pytest and other callers can supply script imports through sys.path
    # without exporting them (make check exports only package src). Preserve
    # the parent's actual code search path, without warming any worker state.
    environment = os.environ.copy()
    environment['PYTHONPATH'] = os.pathsep.join(sys.path)
    process = subprocess.run([sys.executable, '-c', worker], input=json.dumps(payload), text=True,
                             capture_output=True, check=True, timeout=120, env=environment)
    value = json.loads(process.stdout)
    value['process_wall_ms'] = (time.perf_counter() - started) * 1000
    return value


def _worker(payload: dict) -> dict:
    root, locks = Path(payload['root']), Path(payload['locks'])
    _manifest(root)
    with _aged(root, locks):
        if payload['job'] == 'page':
            return {**_counted_samples(root, _pages(root)[payload['name']], 1), 'worker_pid': os.getpid()}
        markdown = next(root.glob('campaigns/*/scenes/*.md'))
        body = frontmatter.parse_frontmatter(markdown.read_text(encoding='utf-8'))[1].strip()
        # Both paths begin with an unloaded encoder. The measured public
        # wrapper includes BUILD/dependency/device/database initialization;
        # source bytes are supplied by its caller in both measurements.
        call = (lambda: tokens.record_tokens(markdown, body)) if payload['name'] == 'cached' else lambda: tokens.count_tokens(body)
        with patch.object(tokens, 'count_tokens', wraps=tokens.count_tokens) as counter:
            result = _samples(call, 1)
        result['calls'] = {'count_tokens': counter.call_count}
        result['worker_pid'] = os.getpid()
        result['encoder'] = tokens.last_counter() == tokens.ENCODING
        return result


def _merge_samples(samples: list[dict]) -> dict:
    durations = [item['samples_ms'][0] for item in samples]
    counts, calls = Counter(), Counter()
    for item in samples:
        counts.update(item.get('reads', {}))
        calls.update(item.get('calls', {}))
    return {'samples_ms': durations, 'median_ms': statistics.median(durations),
            'process_wall_ms': [item['process_wall_ms'] for item in samples],
            'worker_pids': [item['worker_pid'] for item in samples],
            'reads': dict(counts), 'calls': dict(calls)}


def _page_cases(root: Path, locks: Path, repeats: int) -> dict:
    results = {}
    for name, call in _pages(root).items():
        cold = []
        for _ in range(repeats):
            with tempfile.TemporaryDirectory(dir=root.parent, prefix='cold-') as temporary:
                copy = Path(temporary) / 'library'
                shutil.copytree(root, copy, ignore=shutil.ignore_patterns('.cache'))
                cold.append(_fresh(copy, locks, 'page', name))
        _clear()
        call()  # Seed the disposable tree before launching restart workers.
        restart = [_fresh(root, locks, 'page', name) for _ in range(repeats)]
        call()
        cases = {'cold': _merge_samples(cold), 'warm_restart': _merge_samples(restart),
                 'warm_process': _counted_samples(root, call, repeats),
                 'one_file_edited': _counted_samples(root, call, repeats,
                     lambda name=name: _prepare_case(root, 'one_file_edited', name))}
        results[name] = cases
    return results


def _fresh_tokens(root: Path, locks: Path, repeats: int) -> dict:
    if next(root.glob('campaigns/*/scenes/*.md'), None) is None:
        return {}
    # Seed exactly the shipped kind rather than a benchmark-only key.
    _fresh(root, locks, 'tokens', 'cached')
    direct, cached = [], []
    for number in range(repeats):
        # Alternate order so host load and filesystem cache do not favor one
        # contender. Each sample runs in its own newly initialized process.
        order = ('direct', 'cached') if number % 2 == 0 else ('cached', 'direct')
        for name in order:
            value = _fresh(root, locks, 'tokens', name)
            (direct if name == 'direct' else cached).append(value)
    return {'direct': _merge_samples(direct), 'record_tokens': _merge_samples(cached),
            'encoder': all(item['encoder'] for item in direct + cached),
            'includes_initialization': True}


def _warm_search_comparison(root: Path, repeats: int) -> dict:
    # Reproduce the original path/st_mtime/size/inode wrapper, including its
    # version eviction. Extraction and live enumeration are shared unchanged.
    old_pool, old_latest = {}, {}
    def baseline(reader, path):
        signature = statcache.signature(path)
        kind = 'search:' + reader.__name__
        slot = (kind, str(path))
        previous = old_latest.get(slot)
        if previous != signature:
            if previous is not None:
                old_pool.pop((kind, previous), None)
            old_latest[slot] = signature
        return statcache.memo(kind, signature, lambda: reader(path), pool=old_pool,
                              max_entries=search.POOL_ENTRIES)
    def page():
        return list(search.walk())
    expected = page()
    with patch.object(search, '_doc', baseline):
        assert page() == expected
    current, previous, ratios = [], [], []
    for number in range(repeats):
        pair = {}
        for label in (('baseline', 'current') if number % 2 == 0 else ('current', 'baseline')):
            with patch.object(search, '_doc', baseline) if label == 'baseline' else contextlib.nullcontext():
                pair[label] = _samples(page, 1)['samples_ms'][0]
        previous.append(pair['baseline'])
        current.append(pair['current'])
        ratios.append(pair['current'] / pair['baseline'])
    return {'baseline_ms': previous, 'current_ms': current, 'paired_ratios': ratios,
            'median_ratio': statistics.median(ratios), 'outputs_equal': True}


def measure(target: Path, *, repeats: int = 5) -> dict[str, object]:
    target = Path(target).absolute()
    manifest = _manifest(target)
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be positive')
    versions = {}
    for dependency in ('pydantic', 'tiktoken', 'fastapi'):
        try:
            versions[dependency] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            versions[dependency] = 'absent'
    with tempfile.TemporaryDirectory(prefix='grimoire-synthetic-') as temporary:
        sandbox = Path(temporary)
        root, lock_root = sandbox / 'library', sandbox / 'locks'
        shutil.copytree(target, root, ignore=shutil.ignore_patterns('.cache'))
        lock_root.mkdir()
        try:
            with _aged(root, lock_root):
                _clear()
                return {'schema': 1, 'shape': manifest['shape'], 'versions': versions,
                        'source_clock_available': compiled.filesystem_clock(root) is not None,
                        'probes': _probes(root, repeats), 'pages': _page_cases(root, lock_root, repeats),
                        'fresh_process_tokens': _fresh_tokens(root, lock_root, repeats),
                        'warm_search_comparison': _warm_search_comparison(root, repeats),
                        'limitations': 'Fresh Python processes include BUILD/encoder/database initialization; process wall times include imports. OS page cache remains warm; local platform measurements only.'}
        finally:
            _clear()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    try:
        result = measure(options.target, repeats=options.repeats)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    text = json.dumps(result, sort_keys=True, indent=2) + '\n'
    if options.output:
        options.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
