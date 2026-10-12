"""Synthetic-only generation and measurement refuse existing/private inputs."""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.store import paths, search
from grimoire.store.campaigns import read as campaigns_read
from grimoire.store.worlds import read as worlds_read


def _synth():
    assert importlib.util.find_spec('scripts.synth_library') is not None, 'synthetic generator missing'
    return importlib.import_module('scripts.synth_library')


def test_generated_shapes_deterministic_and_publicly_readable(tmp_path):
    module = _synth()
    shape = module.Shape(worlds=2, campaigns_per_world=1, scenes_per_campaign=2, records_per_world=4, body_words=20)
    first, second = tmp_path / 'Realm', tmp_path / 'Saltmarch'
    manifest = module.generate(first, shape)
    assert module.generate(second, shape) == manifest
    left = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob('*') if path.is_file()}
    right = {path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob('*') if path.is_file()}
    assert left == right
    assert json.loads(left['.synth-library.json'])['shape']['worlds'] == 2
    with paths.pin_library(first):
        assert len(worlds_read.list_worlds()) == 2
        assert len(campaigns_read.list_campaigns()) == 2
        assert len(list(search.walk())) >= 12
    assert len(list(first.glob('campaigns/*/scenes/*.md'))) == 4
    assert len(list(first.glob('worlds/*/characters/*/*.json'))) == 4


@pytest.mark.parametrize('existing', ['record', 'marker', 'file'])
def test_generator_refuses_every_nonempty_target_before_writing(tmp_path, existing):
    module = _synth()
    target = tmp_path / 'Realm'
    if existing == 'file':
        target.write_bytes(b'Realm')
        before = target.read_bytes()
    else:
        target.mkdir()
        (target / ('.synth-library.json' if existing == 'marker' else 'Realm.md')).write_bytes(b'Realm')
        before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError):
        module.generate(target, module.Shape())
    assert (target.read_bytes() if existing == 'file' else {p.name: p.read_bytes() for p in target.iterdir()}) == before


@pytest.mark.parametrize('field', ['worlds', 'campaigns_per_world', 'scenes_per_campaign', 'records_per_world', 'body_words'])
def test_negative_shape_refused_before_target_creation(tmp_path, field):
    module = _synth()
    target = tmp_path / 'Realm'
    with pytest.raises(ValueError):
        module.generate(target, module.Shape(**{field: -1}))
    assert not target.exists()


def test_generation_failure_restores_environment_and_never_marks_partial_build(tmp_path, monkeypatch):
    module = _synth()
    original = os.environ.get('GRIMOIRE_HOME')
    monkeypatch.setattr(paths, 'pointer_path', lambda: pytest.fail('generator consulted bootstrap pointer'))
    def refused(*args, **kwargs):
        raise OSError('write refused')
    monkeypatch.setattr(module.atomic, 'write_bytes', refused)
    target = tmp_path / 'Realm'
    with pytest.raises(OSError):
        module.generate(target, module.Shape(worlds=1))
    assert os.environ.get('GRIMOIRE_HOME') == original
    assert not (target / '.synth-library.json').exists()


def test_generator_cli_uses_explicit_shape_and_target(tmp_path):
    _synth()
    target = tmp_path / 'Realm'
    env = {**os.environ, 'PYTHONPATH': str(Path('backend/src').resolve())}
    result = subprocess.run([sys.executable, '-m', 'scripts.synth_library', str(target), '--worlds', '1',
                             '--campaigns', '1', '--scenes', '1', '--records', '2', '--body-words', '5'],
                            cwd='backend', env=env, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads((target / '.synth-library.json').read_text(encoding='utf-8'))['shape']['body_words'] == 5


def test_benchmark_refuses_unmarked_root_without_changes(tmp_path):
    assert importlib.util.find_spec('scripts.benchmark_compiled') is not None, 'synthetic benchmark missing'
    benchmark = importlib.import_module('scripts.benchmark_compiled')
    target = tmp_path / 'Realm'
    target.mkdir()
    (target / 'Realm.md').write_bytes(b'Realm')
    with pytest.raises(ValueError):
        benchmark.measure(target, repeats=1)
    assert list(target.iterdir()) == [target / 'Realm.md']


def test_generated_library_api_pages_and_scene_posts(client, tmp_path, monkeypatch):
    from grimoire.store import scenes
    module = _synth()
    target = tmp_path / 'Realm'
    module.generate(target, module.Shape(worlds=1, campaigns_per_world=1, scenes_per_campaign=1,
                                         records_per_world=2, body_words=20))
    monkeypatch.setenv('GRIMOIRE_HOME', str(target))
    for url in ('/api/worlds', '/api/campaigns', '/api/todo', '/api/shell?campaign=saltmarch-000-000',
                '/api/campaigns/saltmarch-000-000'):
        response = client.get(url)
        assert response.status_code == 200, (url, response.text)
    with paths.pin_library(target):
        assert len(scenes.read_scene('saltmarch-000-000', '0001--realm')['messages']) == 2


def test_benchmark_four_cases_leave_original_bytes_and_environment_unchanged(tmp_path):
    module = _synth()
    target = tmp_path / 'Realm'
    module.generate(target, module.Shape(worlds=1, campaigns_per_world=1, scenes_per_campaign=1,
                                         records_per_world=2, body_words=20))
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    original = os.environ.get('GRIMOIRE_HOME')
    benchmark = importlib.import_module('scripts.benchmark_compiled')
    result = benchmark.measure(target, repeats=1)
    assert result['shape']['worlds'] == 1
    assert set(result['pages']) == {'world_shelf', 'campaign_shelf', 'todo', 'search', 'campaign_hub'}
    for cases in result['pages'].values():
        assert set(cases) == {'cold', 'warm_process', 'warm_restart', 'one_file_edited'}
        assert all(len(sample['samples_ms']) == 1 for sample in cases.values())
        assert cases['cold']['worker_pids'][0] != os.getpid()
        assert cases['warm_restart']['worker_pids'][0] != os.getpid()
        assert cases['cold']['worker_pids'] != cases['warm_restart']['worker_pids']
    assert before == {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
    assert os.environ.get('GRIMOIRE_HOME') == original


def test_fresh_worker_preserves_parent_import_paths_with_only_source_environment(tmp_path, monkeypatch):
    module = _synth()
    target = tmp_path / 'Realm'
    module.generate(target, module.Shape(worlds=1, campaigns_per_world=1, scenes_per_campaign=1,
                                         records_per_world=1, body_words=20))
    benchmark = importlib.import_module('scripts.benchmark_compiled')
    source = Path(benchmark.compiled.__file__).resolve().parents[2]
    # make check sets package src, while pytest alone adds backend/scripts to
    # this parent's sys.path. Fresh workers must inherit both actual imports.
    monkeypatch.setenv('PYTHONPATH', str(source))
    before = os.environ['PYTHONPATH']
    locks = tmp_path / 'Saltmarch'
    locks.mkdir()
    result = benchmark._fresh(target, locks, 'page', 'world_shelf')
    assert result['worker_pid'] != os.getpid()
    assert len(result['samples_ms']) == 1
    assert os.environ['PYTHONPATH'] == before
