import {test} from 'node:test';
import assert from 'node:assert/strict';
import {savedFilters, writeFilters, makePager} from '../../src/grimoire/app/static/pages/chats/workspace.js';
test('saved selections roundtrip and storage failures are visible', () => {
  let data = null;
  const storage = {getItem: () => data, setItem: (_, value) => data = value};
  const values = [{name:'Pinned revision', selection:{source:'card:a/character/a@r1', match:'derived', q:'door', group:'none', sort:'name'}}];
  assert.equal(writeFilters(storage, values), null);
  assert.deepEqual(savedFilters(storage).values, values);
  assert.match(writeFilters(storage, Array(21).fill(values[0])), /20/);
  assert.match(savedFilters({getItem: () => '{broken'}).error, /unavailable/);
  assert.match(writeFilters({setItem: () => {throw Error('quota');}}, values), /not saved/);
});
test('latest request wins, bounded and exact by default; stale refusal is exposed', async () => {
  const pending = [];
  const pager = makePager((op, args) => new Promise((resolve, reject) => pending.push({op,args,resolve,reject})));
  const first = pager({q:'old'}), second = pager({q:'new'});
  assert.equal(pending[0].args.limit, 50);
  assert.equal(pending[0].args.match, 'exact');
  pending[1].resolve({chats:[]});
  assert.deepEqual(await second, {result:{chats:[]}});
  pending[0].resolve({chats:['stale']});
  assert.equal(await first, null);
  const third = pager({offset:50,snapshot:'x'});
  pending[2].reject({reason:'changed'});
  assert.equal((await third).error.reason, 'changed');
});

import {install, byText} from './fakedom.mjs';
import * as ui from '../../src/grimoire/app/static/shell/ui.js';
import {href} from '../../src/grimoire/app/static/shell/router.js';
import {mount} from '../../src/grimoire/app/static/pages/chats/page.js';

async function workspacePage(query, pagedSources = false) {
  install('http://localhost');
  let navigation, refuse = false;
  const requests = [], drafts = new Map([['chat:alpha/one:composer', 'Kept composer draft']]);
  const ctx = {ui, href, query:new URLSearchParams(query), styles(){}, layout(){}, setTitle(){}, setCrumb(){},
    has(){return false;}, registered(){return false;}, setCount(){}, navigate(value){navigation=value;},
    draft(key){return {get:()=>drafts.get(key)||'',set:value=>drafts.set(key,value)};},
    api:async(op,args)=> {
      requests.push(args);
      if (refuse) throw {reason:'changed'};
      return {chats:[{chat:'chat:alpha/one',label:'Old row',sources:[],launch:null,scope:null}],total:51,next:50,
        snapshot:'old',sources:[],source_next:pagedSources && args.source_offset < 100 ? args.source_offset + 50 : null,comparisons:[],comparison_next:pagedSources && args.comparison_offset < 100 ? args.comparison_offset + 50 : null,diagnostics:[]};
    }};
  const root=ui.el('div',{}); document.body.append(root); await mount(root,ctx);
  return {root, requests, drafts, get navigation(){return navigation;}, refuse(value){refuse=value;}};
}

test('changing scope filters clears hidden campaign/world/source restrictions', async () => {
  for (const query of ['filter=campaign&campaign=alpha', 'filter=world&world=world%3Aalpha']) {
    const page = await workspacePage(query);
    byText(page.root,'button','All')[0].click();
    assert.equal(page.navigation,'#/chats');
    byText(page.root,'button','By character')[0].click();
    assert.equal(page.navigation,'#/chats?filter=character');
    byText(page.root,'button',query.startsWith('filter=campaign') ? 'World' : 'Campaign')[0].click();
    assert.equal(page.navigation, query.startsWith('filter=campaign') ? '#/chats?filter=world' : '#/chats?filter=campaign');
  }
});

test('stale continuation cannot resurrect cached rows through grouping; restart recovers with focus and drafts', async () => {
  const page = await workspacePage('');
  page.refuse(true);
  byText(page.root,'button','Next chats')[0].click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(page.root.querySelectorAll('a.chats-row').length,0);
  byText(page.root,'button','None')[0].click();
  assert.equal(page.root.querySelectorAll('a.chats-row').length,0);
  assert.match(page.root.querySelector('.chats-status').textContent,/Navigation changed/);
  assert.equal(document.activeElement.textContent,'Restart browsing');
  page.refuse(false);
  byText(page.root,'button','Restart browsing')[0].click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(page.requests.at(-1).offset,0);
  assert.equal(page.requests.at(-1).snapshot,undefined);
  assert.equal(page.root.querySelectorAll('a.chats-row').length,1);
  assert.equal(document.activeElement.getAttribute('href'),'#/chat/chat%3Aalpha%2Fone');
  assert.equal(page.drafts.get('chat:alpha/one:composer'),'Kept composer draft');
});

test('mounted source and comparison paging retains useful focus forward and back, including boundaries', async () => {
  const page = await workspacePage('filter=character&source=card:a/character/a@r1', true);
  for (const kind of ['sources', 'comparisons']) {
    for (const [direction, expected] of [['Next','Next'], ['Next','Previous'], ['Previous','Previous'], ['Previous','Next']]) {
      const button = byText(page.root,'button',`${direction} ${kind}`)[0];
      button.focus(); button.click();
      await new Promise(resolve=>setTimeout(resolve,0));
      assert.equal(document.activeElement.textContent, `${expected} ${kind}`);
      assert.ok(page.root.querySelectorAll("button").includes(document.activeElement));
      assert.equal(page.root.querySelectorAll('a.chats-row').length,1);
      assert.equal(page.drafts.get('chat:alpha/one:composer'),'Kept composer draft');
    }
  }
  page.refuse(true);
  const next = byText(page.root,'button','Next sources')[0]; next.focus(); next.click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(document.activeElement.textContent,'Restart browsing');
  assert.equal(page.root.querySelectorAll('a.chats-row').length,0);
});
