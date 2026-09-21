import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { archiveApi } from '../../public/archive_search.js';

const modern = 'sb_secret_SYNTHETIC_CONFIGURATION_ONLY';
const legacy = 'synthetic.fixture.jwt';
const endpoint = 'https://synthetic.invalid/api/library/archive/status';
const status = {
  scope: 'imported_archive_text', all_versions: true, full_archive: false,
  counts: Object.fromEntries(['objects', 'object_versions', 'extraction_versions', 'text_units', 'logical_blobs', 'attachment_observations', 'import_batches', 'ingestion_runs'].map((key) => [key, '0'])),
  last_run: null, storage_files_outside_import_not_searched: true,
  semantic_understanding_measured: false,
};

async function checkConfiguration(value, expectedCode, expectedKey) {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls++;
    assert.equal(url, 'https://vpjliavuuxjcvtxbthlp.supabase.co/rest/v1/rpc/archive_search_status');
    assert.equal(init.method, 'POST');
    assert.equal(init.redirect, 'manual');
    assert.equal(init.headers.apikey, expectedKey);
    assert.equal(init.headers.Authorization, expectedKey.startsWith('sb_secret_') ? undefined : 'Bearer ' + expectedKey);
    return new Response(JSON.stringify(status), { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const env = { SUPABASE_SERVICE_KEY: value };
    const response = await archiveApi(new Request(endpoint), env, 'status');
    const body = await response.json();
    assert.equal(response.status, expectedCode ? 503 : 200);
    assert.equal(calls, expectedCode ? 0 : 1);
    assert.equal(env.SUPABASE_SERVICE_KEY, value);
    assert.ok(response.headers.get('Cache-Control').includes('no-store'));
    if (expectedCode) assert.deepEqual(body, { error: expectedCode });
    else assert.equal(body.scope, 'imported_archive_text');
    assert.equal(JSON.stringify(body).includes('SYNTHETIC_CONFIGURATION_ONLY'), false);
  } finally { globalThis.fetch = original; }
}

for (const [name, value] of [
  ['undefined', undefined], ['null', null], ['empty', ''],
  ['spaces', ' \t\r\n '], ['Unicode surrounding whitespace only', '\u00a0\ufeff'],
]) test('missing configuration: ' + name, () => checkConfiguration(value, 'archive_key_missing'));

for (const [name, value] of [
  ['number', 42], ['boolean', false], ['object', {}], ['array', []],
  ['publishable', 'sb_publishable_SYNTHETIC'], ['short secret', 'sb_secret_'],
  ['quoted', '"' + modern + '"'], ['internal space', modern.slice(0, 15) + ' ' + modern.slice(15)],
  ['internal newline', modern.slice(0, 15) + '\n' + modern.slice(15)],
  ['Bearer prefix', 'Bearer ' + legacy], ['zero-width interior', modern + '\u200b'],
]) test('invalid configuration: ' + name, () => checkConfiguration(value, 'archive_key_invalid'));

for (const [name, value, key] of [
  ['modern unchanged', modern, modern], ['modern outer whitespace', ' \r\n' + modern + '\t ', modern],
  ['modern Unicode outer whitespace', '\u00a0\ufeff' + modern + '\u00a0', modern],
  ['legacy unchanged', legacy, legacy], ['legacy outer whitespace', '\n' + legacy + '\r', legacy],
]) test('accepted configuration: ' + name, () => checkConfiguration(value, null, key));

test('configuration messages explain correction without exposing configuration values', () => {
  const html = fs.readFileSync(new URL('../../public/archive.html', import.meta.url), 'utf8');
  const start = html.indexOf('  function errorMessage(status, code) {');
  const end = html.indexOf('  async function request(', start);
  assert.ok(start >= 0 && end > start);
  const message = vm.runInNewContext('(' + html.slice(start, end).trim() + ')');
  const missing = message(503, 'archive_key_missing');
  const invalid = message(503, 'archive_key_invalid');
  const rejected = message(503, 'archive_key_rejected');
  assert.notEqual(missing, invalid);
  assert.match(missing, /не получило серверный ключ/);
  assert.match(invalid, /неверном формате/);
  assert.match(missing, /Администратору/);
  assert.match(invalid, /Администратору/);
  assert.match(rejected, /не разрешила доступ/);
  assert.match(rejected, /права доступа/);
  assert.equal(html.includes(modern), false);
});

for (const upstreamStatus of [401, 403]) for (const route of ['status', 'search', 'unit']) {
  test(`upstream ${upstreamStatus} becomes a safe connection rejection on ${route}`, async () => {
    const original = globalThis.fetch;
    let calls = 0, cancelled = false;
    globalThis.fetch = async () => {
      calls++;
      const body = new ReadableStream({ cancel() { cancelled = true; } });
      return new Response(body, { status: upstreamStatus, headers: { 'X-Private': modern, 'WWW-Authenticate': modern } });
    };
    try {
      const suffix = route === 'search' ? '?q=AB' : route === 'unit' ? '?extraction_id=12&unit_key=body%3A0' : '';
      const response = await archiveApi(new Request('https://synthetic.invalid/api/library/archive/' + route + suffix), { SUPABASE_SERVICE_KEY: modern }, route);
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: 'archive_key_rejected' });
      assert.equal(response.headers.get('X-Private'), null);
      assert.equal(response.headers.get('WWW-Authenticate'), null);
      assert.equal(calls, 1);
      assert.equal(cancelled, true);
    } finally { globalThis.fetch = original; }
  });
}

for (const [upstreamStatus, code, statusCode] of [[400, 'invalid_archive_query', 400], [409, 'archive_not_ready', 409], [500, 'archive_unavailable', 503]]) {
  test(`upstream ${upstreamStatus} keeps the previous non-key error classification`, async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response('SYNTHETIC_PRIVATE_BODY', { status: upstreamStatus });
    try {
      const response = await archiveApi(new Request(endpoint), { SUPABASE_SERVICE_KEY: modern }, 'status');
      assert.equal(response.status, statusCode);
      assert.deepEqual(await response.json(), { error: code });
    } finally { globalThis.fetch = original; }
  });
}
