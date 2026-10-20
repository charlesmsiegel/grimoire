"""Contracts captured from the monoliths at d5c4d6777, before import cleanup.

The fixture is deliberately not generated from the split packages. Schema
representations differ between Pydantic majors, so each has its own original
capture; neither is normalized to make a mismatch disappear.
"""

import hashlib
import importlib
import json
from pathlib import Path

import pydantic
import pytest
from fastapi import FastAPI

from grimoire.routes import campaigns, models

CONTRACT = json.loads((Path(__file__).parent / 'fixtures' /
                       'route_package_contract.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('module', [campaigns, models], ids=['campaigns', 'models'])
def test_former_module_bindings_remain_importable(module):
    domain = module.__name__.rsplit('.', 1)[-1]
    assert not [name for name in CONTRACT[f'{domain}_exports'] if not hasattr(module, name)]


def test_campaign_star_import_surface_matches_the_monolith():
    expected = {name for name in CONTRACT['campaigns_exports'] if not name.startswith('_')}
    assert set(campaigns.__all__) == expected


def test_campaign_route_registration_matches_the_monolith():
    rows = [{'methods': sorted(r.methods), 'path': r.path, 'name': r.name,
             'status_code': r.status_code,
             'markers': sorted(n for n in vars(r.endpoint) if n.startswith('grimoire_'))}
            for r in campaigns.router.routes]
    assert rows == CONTRACT['campaign_routes']
    for route in campaigns.router.routes:
        assert route.endpoint is getattr(campaigns, route.name)
        owner = importlib.import_module(route.endpoint.__module__)
        assert owner.router is campaigns.router


@pytest.mark.parametrize('name', sorted(CONTRACT['model_schemas']['2']))
def test_request_schema_matches_the_monolith(name):
    model = getattr(models, name)
    schema = model.model_json_schema() if hasattr(model, 'model_json_schema') else model.schema()
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
    assert digest == CONTRACT['model_schemas'][pydantic.VERSION.split('.')[0]][name]


def test_campaign_annotations_resolve_in_a_fresh_openapi_build():
    app = FastAPI()
    app.include_router(campaigns.router)
    schema = app.openapi()
    for route in CONTRACT['campaign_routes']:
        for method in route['methods']:
            assert schema['paths'][route['path']][method.lower()]['operationId'].startswith(route['name'])
