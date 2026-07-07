// Клиент официального API glbs.io (Глобус-ВЭД): выгрузка таможенных деклараций
// по нашим HS-кодам через /api/supplies-search/. Сырой ответ огромный (десятки МБ),
// поэтому в git коммитим ТОЛЬКО компактный отфильтрованный экстракт: декларации,
// где в описании/участниках встречается наш каталожный номер, OEM или слова
// «перфоратор/буровой/drifter…». Сырьё в git не попадает (лимит GitHub 100 МБ).
//
// Ключ — из env GLBS_API_KEY (в код/лог не пишется; маскируется в workflow).
// Метод:
//   search — бесплатно, цены/веса скрыты (участники, объёмы, даты, описания);
//   save   — показывает цены/веса, СПИСЫВАЕТ лимит (реальные цены).
//
// env:
//   GLBS_API_KEY   (обяз.)
//   GLBS_METHOD    search|save (по умолч. search)
//   GLBS_COUNTRY   ru|kz|uz|am|... (по умолч. ru)
//   GLBS_HS        4-знач. префиксы через запятую (по умолч. — из positions.json)
//   GLBS_PERIOD_START / GLBS_PERIOD_FINISH  YYYY-MM-DD (по умолч. 2023-01-01..2026-03-31)
//   GLBS_MAX_HS    сколько HS-групп за прогон (по умолч. 3)
//   GLBS_FIELDS    список полей ответа через запятую (по умолч. — все)
//   GLBS_MAX_MATCH сколько совпавших деклараций хранить на HS (по умолч. 4000)
import { writeFileSync, mkdirSync, readFileSync } from 'fs';

const KEY = process.env.GLBS_API_KEY;
if (!KEY) { console.error('::error::нет ключа GLBS_API_KEY'); process.exit(1); }
const METHOD = process.env.GLBS_METHOD || 'search';
const COUNTRY = process.env.GLBS_COUNTRY || 'ru';
const P1 = process.env.GLBS_PERIOD_START || '2023-01-01';
const P2 = process.env.GLBS_PERIOD_FINISH || '2026-03-31';
const MAX_HS = parseInt(process.env.GLBS_MAX_HS || '3', 10);
const MAX_MATCH = parseInt(process.env.GLBS_MAX_MATCH || '20000', 10);
const OUT = 'zip/customs/out';
mkdirSync(OUT, { recursive: true });

// HS-префиксы: из env или топ по нашей базе
let hsList = (process.env.GLBS_HS || '').split(',').map(s => s.trim()).filter(Boolean);
const pos = JSON.parse(readFileSync('zip/data/positions.json', 'utf8'));
if (!hsList.length) {
  const cnt = {};
  for (const p of pos) {
    const m = String(p.hs_code || '').match(/(\d{4})/);
    if (m) cnt[m[1]] = (cnt[m[1]] || 0) + 1;
  }
  hsList = Object.entries(cnt).sort((a, b) => b[1] - a[1]).map(([h]) => h);
}
hsList = hsList.slice(0, MAX_HS);

// наши каталожные номера/алиасы (токены >=4 символов) для сильного матчинга
const tokenSet = new Set();
const norm = s => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
for (const p of pos) {
  for (const raw of [p.catalog_no, p.aliases]) {
    for (const t of String(raw || '').split(/[\s,;/]+/)) {
      const n = norm(t);
      if (n.length >= 5) tokenSet.add(n);
    }
  }
}
const OEM_RE = /перфоратор|перфоратора|буров|бурильн|drifter|rock ?drill|epiroc|atlas ?copco|sandvik|tamrock|montabert|furukawa|коронк|хвостовик|shank|гидромолот|гидроударн|COP\s?\d|HLX|HL\d{3}|RD\d{3}/i;

// поля ответа
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

// самый большой массив объектов в ответе (схема заранее не известна)
function recordsOf(json) {
  let best = [];
  const visit = (o, d) => {
    if (d > 5 || !o || typeof o !== 'object') return;
    if (Array.isArray(o)) {
      if (o.length > best.length && o[0] && typeof o[0] === 'object') best = o;
      return;
    }
    for (const k in o) visit(o[k], d + 1);
  };
  visit(json, 0);
  return best;
}
// скелет ответа для отладки: имена ключей + длины массивов, без объёма
function skeleton(o, d = 0) {
  if (d > 3 || o == null) return typeof o;
  if (Array.isArray(o)) return `[${o.length}]` + (o[0] && typeof o[0] === 'object' ? ' of ' + skeleton(o[0], d + 1) : '');
  if (typeof o === 'object') { const r = {}; for (const k of Object.keys(o).slice(0, 40)) r[k] = skeleton(o[k], d + 1); return r; }
  return typeof o;
}
// вычистить ключ/ip из меты, чтобы не попал в git
function cleanMeta(m) {
  if (!m || typeof m !== 'object') return m || null;
  const c = JSON.parse(JSON.stringify(m));
  for (const k of ['auth_key', 'key', 'api_key', 'apikey', 'ip', 'user_ip']) delete c[k];
  return c;
}
const hay = rec => norm(JSON.stringify(rec));      // для матчинга по PN
const hayRaw = rec => JSON.stringify(rec);          // для матчинга по словам (OEM)

const summary = [];
for (const hs of hsList) {
  const url = buildUrl(hs);
  try {
    const r = await fetch(url, { headers: { 'User-Agent': 'kvant-zip-bot' } });
    const text = await r.text();
    let json = null; try { json = JSON.parse(text); } catch {}
    if (r.status !== 200 || !json) {
      // покажем тело ошибки (без ключа) — понять причину 400
      console.log(`[hs ${hs}] status=${r.status} bytes=${text.length} ERROR_BODY=${text.slice(0, 300)}`);
      summary.push({ hs, status: r.status, bytes: text.length, error: text.slice(0, 300), url: redact(url) });
      continue;
    }
    const recs = recordsOf(json);
    const sampleKeys = recs[0] ? Object.keys(recs[0]) : Object.keys(json);
    const skel = skeleton(json);
    if (!recs.length) console.log(`[hs ${hs}] SKELETON=${JSON.stringify(skel)}`);
    // компактная проекция декларации ГТД (G-коды → человекочитаемые поля)
    const cut = (s, n) => String(s || '').slice(0, n);
    const compact = (rec, tag, pn) => ({
      tag, pn: pn || undefined,
      date: rec.G072 || rec.GD1 || rec.G230 || '',
      importer: cut(rec.G082, 120), inn: rec.G081 || '',
      exporter: cut(rec.G31_11 || rec.G022, 120), brand: cut(rec.G31_12, 40),
      origin: rec.G34 || '', dispatch: rec.G15A || '',
      cur: rec.G221 || '', incoterms: rec.G202 || '', place: cut(rec.G2021, 40),
      hs10: rec.G33 || '', desc: cut(rec.G31_1, 220),
      net_kg: rec.G38 || '', gross_kg: rec.G35 || '', usd_kg: rec.USDKG || '',
      val: rec.G42 || '', custval: rec.G45 || '', statval: rec.G46 || '',
    });
    // фильтр: сильный (наш PN) + слабый (OEM/перфоратор-слова)
    const strong = [], weak = [];
    for (const rec of recs) {
      const h = hay(rec);
      let pn = null;
      for (const t of tokenSet) { if (t.length >= 6 && h.includes(t)) { pn = t; break; } }
      if (pn) strong.push(compact(rec, 'strong', pn));
      else if (OEM_RE.test(hayRaw(rec))) weak.push(compact(rec, 'weak'));
    }
    const matched = strong.concat(weak).slice(0, MAX_MATCH);
    writeFileSync(`${OUT}/customs_${hs}.json`, JSON.stringify({
      hs, method: METHOD, country: COUNTRY, period: [P1, P2],
      total_records: recs.length, strong: strong.length, weak: weak.length,
      fields: sampleKeys, meta: cleanMeta(json.meta), skeleton: recs.length ? undefined : skel,
      matched,
    }, null, 1));
    console.log(`[hs ${hs}] status=200 total=${recs.length} strong=${strong.length} weak=${weak.length} fields=${JSON.stringify(sampleKeys.slice(0, 30))}`);
    summary.push({ hs, status: 200, total_records: recs.length, strong: strong.length, weak: weak.length, fields: sampleKeys.slice(0, 30), url: redact(url) });
  } catch (e) {
    console.log(`[hs ${hs}] ОШИБКА: ${e.message}`);
    summary.push({ hs, err: e.message, url: redact(url) });
  }
}
writeFileSync(`${OUT}/_summary.json`, JSON.stringify({ method: METHOD, country: COUNTRY, period: [P1, P2], hs: hsList, results: summary }, null, 1));
console.log('\n===PULL_SUMMARY===\n' + JSON.stringify(summary, null, 1) + '\n===/PULL_SUMMARY===');
