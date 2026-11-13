"""The real CLI composes startup campaign policy with editable inference settings."""
from __future__ import annotations

import json
import os
from urllib.parse import quote

from provider_servers import base_url, sse_lines, sse_server, stream
from test_app_campaign_browser import AGENDA, prepare
from test_app_inference_browser import CHROMIUM, navigate, playwright, pytestmark, running_app, save_models, select_npc
from test_campaignapi import BRIBE, SECRET


def test_startup_policy_survives_model_edits_and_restart_in_real_browser(tmp_path):
    folder, chat, _, mara, tamsin, config = prepare(tmp_path)
    # Both NPCs are present for this composition scenario; all fixtures are synthetic.
    config['stages'][0]['absences'] = []
    config['stages'][0]['placements'].append({'who': str(tamsin), 'area': 'hold'})
    config['agendas'][0]['goals'][0]['approaches'].append(
        {'approach_id': 'watch', 'action': 'Watch the quay.'})
    campaign = tmp_path / 'campaign.json'
    settings = tmp_path / 'model.json'

    def respond(handler):
        request = json.loads(handler.server.requests[-1][2])
        decision = 'Answer with the number of one choice' in request['messages'][-1]['content']
        reply = BRIBE if request['model'] == 'label-model' else ('1' if decision else 'The tide is turning.')
        stream(sse_lines(deltas=(reply,), usage={'prompt_tokens': 12, 'completion_tokens': 8}))(handler)

    with sse_server(respond) as provider, playwright.sync_playwright() as pw:
        config['labelling'] = {'provider_id': 'labels', 'base_url': base_url(provider),
                               'model': 'label-model', 'attempts': 1, 'max_output_tokens': 1200}
        campaign.write_text(json.dumps(config))
        browser = pw.chromium.launch(executable_path=CHROMIUM, headless=True, args=['--no-sandbox'],
                                     env={**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / 'browser-config'),
                                          'XDG_CACHE_HOME': str(tmp_path / 'browser-cache')})
        try:
            page = browser.new_page()
            page.set_default_timeout(15_000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def inspect_startup(url):
                page.goto(url)
                navigate(page, 'providers')
                page.get_by_label('Chat', exact=True).select_option(str(chat))
                select_npc(page, mara)
                region = page.get_by_role('region', name='Current context', exact=True)
                playwright.expect(region).to_contain_text('campaign startup configuration')
                playwright.expect(region).to_contain_text('Wait by the hatch.')
                playwright.expect(region.get_by_label('Goal', exact=True)).to_have_count(0)
                playwright.expect(region.get_by_role('button', name='Save tactics', exact=True)).to_have_count(0)
                assert SECRET not in region.inner_text()
                return region

            with running_app(folder, settings, campaign_config=campaign) as url:
                inspect_startup(url)  # Missing model settings still retain campaign policy.
                assert not provider.requests
                page.get_by_label('Endpoint address', exact=True).fill(base_url(provider))
                page.get_by_role('textbox', name='Model', exact=True).fill('voice-model')
                page.get_by_label('Optional decision calls per turn', exact=True).fill('1')
                save_models(page)
                page.get_by_label('Chat', exact=True).select_option(str(chat))
                select_npc(page, tamsin)
                page.get_by_label('Goal', exact=True).fill('Count the crates')
                page.get_by_label('Candidate actions', exact=True).fill('Check each stamp\nCount the ropes')
                with page.expect_response('**/api/providers.save_agenda') as saved:
                    page.get_by_role('button', name='Save tactics', exact=True).click()
                assert saved.value.ok, saved.value.text()
                select_npc(page, mara)
                playwright.expect(page.get_by_role('region', name='Current context', exact=True)).to_contain_text('Wait by the hatch.')
                assert not provider.requests
                # The separate campaign label route still executes after narration settings changed.
                navigate(page, 'campaign/' + quote(chat.campaign_id) + '?chat=' + quote(str(chat), safe=''))
                page.get_by_role('button', name='Director view', exact=True).click()
                playwright.expect(page.get_by_role('region', name='NPC goals', exact=True)).to_contain_text(AGENDA)
                playwright.expect(page.get_by_role('region', name='Cast and stage', exact=True)).to_contain_text('Cargo hold')
                page.get_by_label('Against', exact=True).select_option('hold')
                page.get_by_role('button', name='Label this action', exact=True).click()
                dialog = page.get_by_role('dialog')
                playwright.expect(dialog).to_contain_text('label-model')
                dialog.get_by_role('button', name='Request labels', exact=True).click()
                playwright.expect(page.get_by_role('region', name='Open action', exact=True)).to_contain_text('proposed')
                assert [json.loads(body)['model'] for _, _, body in provider.requests] == ['label-model']
                assert all(SECRET not in body.decode() for _, _, body in provider.requests)
                navigate(page, 'chat/' + quote(str(chat), safe=''))
                with page.expect_response('**/api/generate') as generated:
                    page.get_by_role('button', name='Ask for a reply', exact=True).click()
                assert generated.value.ok, generated.value.text()
                requests = [json.loads(body) for _, _, body in provider.requests[1:]]
                assert len(requests) == 3  # One bounded decision plus both separate NPC narrations.
                assert all(request['model'] == 'voice-model' for request in requests)
                assert sum('Answer with the number of one choice' in request['messages'][-1]['content']
                           for request in requests) == 1
                assert any('Wait by the hatch.' in json.dumps(request) for request in requests)
                assert any('Check each stamp' in json.dumps(request) for request in requests)

            saved_settings = settings.read_text()
            with running_app(folder, settings, campaign_config=campaign) as url:
                inspect_startup(url)
                select_npc(page, tamsin)
                playwright.expect(page.get_by_label('Goal', exact=True)).to_have_value('Count the crates')
            conflicting = json.loads(saved_settings)
            conflicting['agendas'][0]['actor'] = str(mara)  # Valid model schema, forbidden startup owner collision.
            for content in ('{invalid', json.dumps(conflicting)):
                settings.write_text(content)
                with running_app(folder, settings, campaign_config=campaign) as url:
                    inspect_startup(url)
                    playwright.expect(page.get_by_text('No model configured; inspection still works.', exact=True)).to_be_visible()
                assert settings.read_text() == content  # Invalid user files are never repaired implicitly.
            assert len(provider.requests) == 4
            assert errors == []
        finally:
            browser.close()
