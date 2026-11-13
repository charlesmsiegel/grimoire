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
