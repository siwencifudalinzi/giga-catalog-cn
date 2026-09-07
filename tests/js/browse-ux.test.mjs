import test from 'node:test';
import assert from 'node:assert/strict';
import { createTagIndex } from '../../public/js/tags.js';
import * as navigation from '../../public/js/series-navigation.js';

test('series preview uses latest real cover and does not mutate records', () => {
  const videos = [{series:'ABC',code:'ABC-1',releaseDate:'2025-01-01',cover:'https://example.com/1.jpg'},
    {series:'ABC',code:'ABC-2',releaseDate:'2025-02-01',cover:'https://example.com/2.jpg'},
    {series:'ABC',code:'ABC-3',releaseDate:'2025-03-01',cover:''}];
  assert.equal(navigation.buildSeriesPreviews(videos).get('ABC').code, 'ABC-2');
  assert.equal(videos[0].code, 'ABC-1');
});

test('series query accepts case and fullwidth codes and stays in supplied order', () => {
  const series = [{code:'SPSF'}, {code:'THPA'}, {code:'SPSA'}];
  assert.deepEqual(navigation.filterSeries(series, ' ｓｐｓ ').map(x=>x.code), ['SPSF','SPSA']);
  assert.deepEqual(navigation.filterSeries(series, 'missing'), []);
});

test('uncertain proper name uses original Japanese and old translation stays searchable', () => {
  const index = createTagIndex([{id:1149,group:'character',nameJa:'サムライジャー',nameZh:'武士罐子',count:4}]);
  assert.equal(index.get(1149).nameZh, 'サムライジャー');
  assert.equal(index.search('武士罐子')[0].id, 1149);
  assert.equal(index.search('サムライ')[0].id, 1149);
});

test('reviewed general word keeps old and Japanese names as aliases', () => {
  const index = createTagIndex([{id:68,group:'character',nameJa:'金髪',nameZh:'金发女郎',count:4}]);
  assert.equal(index.get(68).nameZh, '金发');
  assert.equal(index.search('金发女郎')[0].id, 68);
  assert.equal(index.search('金髪')[0].id, 68);
});
