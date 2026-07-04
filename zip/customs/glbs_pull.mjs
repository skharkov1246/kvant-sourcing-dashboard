// Клиент официального API glbs.io (Глобус-ВЭД): выгрузка таможенных деклараций
// по нашим HS-кодам через метод /api/supplies-search/. Результаты пишутся в
// zip/customs/out/*.json (их коммитит workflow — я читаю из репозитория).
//
// Ключ — только из секрета GLBS_API_KEY (в код/лог не попадает).
// Метод:
//   search — бесплатно, НО цены/веса скрыты (для разведки объёма);
//   save   — показывает цены/веса, СПИСЫВАЕТ месячный лимит (для реальных цен).
//
// env:
//   GLBS_API_KEY   (обяз.)  — ключ API
//   GLBS_METHOD    search|save (по умолч. search)
//   GLBS_COUNTRY   ru|kz|uz|am|... (по умолч. ru)
//   GLBS_HS        список 4-значных префиксов через запятую (по умолч. — из positions.json)
//   GLBS_PERIOD_START / GLBS_PERIOD_FINISH  YYYY-MM-DD (по умолч. 2023-01-01 .. 2026-03-31)
//   GLBS_MAX_HS    ограничить число HS-групп за прогон (беречь лимит; по умолч. 3)
import { writeFileSync, mkdirSync, readFileSync } from 'fs';

const KEY = process.env.GLBS_API_KEY;
if (!KEY) { console.error('::error::нет секрета GLBS_API_KEY'); process.exit(1); }
const METHOD = process.env.GLBS_METHOD || 'search';
const COUNTRY = process.env.GLBS_COUNTRY || 'ru';
const P1 = process.env.GLBS_PERIOD_START || '2023-01-01';
const P2 = process.env.GLBS_PERIOD_FINISH || '2026-03-31';
const MAX_HS = parseInt(process.env.GLBS_MAX_HS || '3', 10);
const OUT = 'zip/customs/out';
mkdirSync(OUT, { recursive: true });

// HS-префиксы: из env или топ по нашей базе
let hsList = (process.env.GLBS_HS || '').split(',').map(s => s.trim()).filter(Boolean);
if (!hsList.length) {
  const pos = JSON.parse(readFileSync('zip/data/positions.json', 'utf8'));
  const cnt = {};
  for (const p of pos) {
    const m = String(p.hs_code || '').match(/(\d{4})/);
    if (m) cnt[m[1]] = (cnt[m[1]] || 0) + 1;
  }
  hsList = Object.entries(cnt).sort((a, b) => b[1] - a[1]).map(([h]) => h);
}
hsList = hsList.slice(0, MAX_HS);

// поля ответа: пусто => все поля (нужно, чтобы поймать точные имена ценовых полей)
const FIELDS = (process.env.GLBS_FIELDS || '').split(',').map(s => s.trim()).filter(Boolean);

function buildUrl(hs) {
  const p = new URLSearchParams();
  p.set('api-key', KEY);
  p.set('method', METHOD);
  p.set('country', COUNTRY);
  p.set('period_start', P1);
  p.set('period_finish', P2);
  p.set('format', 'json');
  p.set('search[direction]', 'ИМ');
  p.set('search[hs_code][0]', hs + '*');
  FIELDS.forEach((f, i) => p.set(`fields_view[${i}]`, f));
  return `https://glbs.io/api/supplies-search/?${p.toString()}`;
}
const redact = u => u.replace(/api-key=[^&]+/, 'api-key=***');

const summary = [];
for (const hs of hsList) {
  const url = buildUrl(hs);
  try {
    const r = await fetch(url, { headers: { 'User-Agent': 'kvant-zip-bot' } });
    const text = await r.text();
    let json = null; try { json = JSON.parse(text); } catch {}
    writeFileSync(`${OUT}/hs_${hs}.json`, json ? JSON.stringify(json, null, 1) : text);
    // аккуратно вытащим счётчик/структуру, не зная точной схемы
    let count = null, keys = [];
    if (json) {
      const data = json.data || json.supplies || json.result || json.items || json.fea || json;
      if (Array.isArray(data)) { count = data.length; if (data[0]) keys = Object.keys(data[0]); }
      else if (data && typeof data === 'object') { keys = Object.keys(data); }
      if (json.meta) { delete json.meta.key; delete json.meta.ip; }
    }
    summary.push({ hs, status: r.status, bytes: text.length, count, topKeys: keys.slice(0, 25), url: redact(url) });
    console.log(`[hs ${hs}] status=${r.status} bytes=${text.length} count=${count ?? '?'}`);
  } catch (e) {
    summary.push({ hs, err: e.message, url: redact(url) });
    console.log(`[hs ${hs}] ОШИБКА: ${e.message}`);
  }
}
writeFileSync(`${OUT}/_summary.json`, JSON.stringify({ method: METHOD, country: COUNTRY, period: [P1, P2], hs: hsList, results: summary }, null, 1));
console.log('\n===PULL_SUMMARY===\n' + JSON.stringify(summary, null, 1) + '\n===/PULL_SUMMARY===');
