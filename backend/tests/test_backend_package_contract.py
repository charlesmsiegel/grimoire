"""Interfaces captured at a7ebe3961 before the larger package extractions.

The fixture came from the original modules, independently under Pydantic 1/2.
It pins the old surface rather than learning the answer from the new leaves.
"""

import copy
import hashlib
import importlib
import json
import logging
from pathlib import Path
from types import ModuleType

import pydantic
import pytest

from grimoire.main import create_app
from grimoire.store import logs, overlay

CONTRACT = json.loads((Path(__file__).parent / 'fixtures' /
                       'backend_package_contract.json').read_text(encoding='utf-8'))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize('name', sorted(CONTRACT['modules']))
def test_original_bindings_and_star_surface_remain(name):
    module = importlib.import_module(name)
    expected = CONTRACT['modules'][name]
    assert not [n for n in expected['exports'] if not hasattr(module, n)]
    assert not [n for n in expected['module_bindings']
                if not isinstance(getattr(module, n), ModuleType)]
    exported = getattr(module, '__all__', [n for n in vars(module) if not n.startswith('_')])
    assert _digest(sorted(exported)) == expected['star_sha256']
    assert {n: getattr(module, n).name for n in expected['loggers']} == expected['loggers']
    assert getattr(module, '__annotations__', {}) == expected['module_annotations']


@pytest.mark.parametrize('name', [n for n, row in CONTRACT['modules'].items() if 'routes' in row])
def test_original_route_table_remains(name):
    module = importlib.import_module(name)
    rows = [{'methods': sorted(r.methods), 'path': r.path, 'name': r.name,
             'status_code': r.status_code,
             'markers': sorted(n for n in vars(r.endpoint) if n.startswith('grimoire_'))}
            for r in module.router.routes]
    assert rows == CONTRACT['modules'][name]['routes']
    for route in module.router.routes:
        owner = importlib.import_module(route.endpoint.__module__)
        assert owner.router is module.router
        assert route.endpoint is getattr(module, route.name)


# FastAPI publishes endpoint docstrings. These two source pointers moved with
# their modules; the independently captured schema differs only in those exact
# literals. Keep its original hashes, require the corrected pointers, and undo
# only those spellings on a copy before checking the entire schema.
_DESCRIPTION_SOURCE_PATHS = (
    ('/api/campaigns/{cid}/ledger', 'get',
     'routes/ledger.py', 'routes/ledger/_policy.py'),
    ('/api/campaigns/{cid}/scenes/{sid}/messages/{index}', 'delete',
     'store/locks.py', 'store/locks/__init__.py'),
)


def _assert_openapi_contract(schema):
    original = copy.deepcopy(schema)
    for path, method, before, after in _DESCRIPTION_SOURCE_PATHS:
        operation = original['paths'][path][method]
        description = operation['description']
        assert description.count(after) == 1
        assert before not in description
        operation['description'] = description.replace(after, before)
    assert _digest(original) == CONTRACT['openapi_sha256'][pydantic.VERSION.split('.')[0]]


def test_complete_openapi_remains():
    schema = create_app().openapi()
    before = _digest(schema)
    _assert_openapi_contract(schema)
    assert _digest(schema) == before


@pytest.mark.parametrize('change', ['endpoint', 'parameter', 'description', 'old_pointer'])
def test_openapi_source_pointer_allowance_rejects_other_changes(change):
    schema = create_app().openapi()
    path, method, old_pointer, new_pointer = _DESCRIPTION_SOURCE_PATHS[0]
    operation = schema['paths'][path][method]
    if change == 'endpoint':
        schema['paths']['/api/probe'] = {'get': {'responses': {'200': {'description': 'Probe'}}}}
    elif change == 'parameter':
        operation['parameters'][0]['required'] = not operation['parameters'][0]['required']
    elif change == 'description':
        # This is within an allowed field: ignoring its whole text would hide
        # an unrelated policy change along with the approved source pointer.
        operation['description'] += '\nAdditional policy.'
    else:
        operation['description'] = operation['description'].replace(new_pointer, old_pointer)
    with pytest.raises(AssertionError):
        _assert_openapi_contract(schema)


def test_original_log_label_is_persisted_and_filterable(monkeypatch, tmp_path):
    monkeypatch.setenv('GRIMOIRE_HOME', str(tmp_path))
    logs.install()
    monkeypatch.setattr(overlay.log, 'level', logging.WARNING)
    overlay.log.warning('Package modularization probe')
    rows = logs.read(module='store.overlay', contains='Package modularization probe')['rows']
    assert [(row['module'], row['message']) for row in rows] == [
        ('store.overlay', 'Package modularization probe')]
