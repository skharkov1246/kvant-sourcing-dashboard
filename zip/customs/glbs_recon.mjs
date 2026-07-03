// glbs.io — найти, КАК отдаётся API-ключ: DOM-элементы key/api + сетевые ответы при
// клике «Мой ключ» + кандидатные эндпоинты. Ключ печатаем только маской.
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);
const mask = k => k ? `len=${k.length} ${k.slice(0, 3)}…${k.slice(-2)}` : 'нет';
const isTok = s => /^[A-Za-z0-9_\-]{16,64}$/.test(s || '');

async function login(page) {
  for (let a = 1; a <= 3; a++) {
    await page.goto('https://glbs.io/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(1500);
    if (!(await page.$('input[type=password]'))) return true;
    await page.fill('input[type=email],input[name*=login i],input[type=text]', USER).catch(() => {});
    await page.fill('input[type=password]', PASS).catch(() => {});
    await page.click('button[type=submit],input[type=submit]').catch(() => {});
    try { await page.waitForFunction(() => !document.querySelector('input[type=password]'), { timeout: 20000 }); } catch {}
    await page.waitForTimeout(2000);
    if (!(await page.$('input[type=password]'))) return true;
  }
  return false;
}

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
const bodies = [];
page.on('response', async r => {
  try {
    const u = r.url(); if (!/glbs\.io/.test(u)) return;
    const ct = r.headers()['content-type'] || '';
    if (!/json|text|javascript/.test(ct)) return;
    const t = await r.text();
    if (/key|token/i.test(u) || (t.length < 4000 && /[A-Za-z0-9_\-]{20,}/.test(t) && /key/i.test(t)))
      bodies.push({ url: u.slice(0, 120), snippet: t.slice(0, 500) });
  } catch {}
});

try {
  if (!await login(page)) { P('LOGIN_OK', false); throw new Error('login'); }
  P('LOGIN_OK', true);

  await page.goto('https://glbs.io/api/', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(2500);

  // 1) DOM: элементы с id/class про key/api/token — их текст
  const dom = await page.evaluate(() => {
    const out = [];
    for (const e of document.querySelectorAll('[id*=key i],[class*=key i],[id*=api i],[class*=apikey i],[id*=token i],[data-key],[data-api-key]')) {
      const txt = (e.value || e.textContent || '').trim().slice(0, 80);
      out.push({ tag: e.tagName, id: e.id, cls: (e.className || '').toString().slice(0, 40), txt });
    }
    return out.slice(0, 30);
  });
  P('DOM_KEYISH', dom);

  // 2) клик «Мой ключ» и повторный разбор
  for (const sel of ['text=Мой ключ', 'text=My key', 'a:has-text("ключ")', 'button:has-text("ключ")', '[href*=key]']) {
    const el = await page.$(sel); if (el) { await el.click().catch(() => {}); await page.waitForTimeout(1500); }
  }
  await page.waitForTimeout(1500);
  const afterClick = await page.evaluate(() => {
    const cand = [];
    for (const e of document.querySelectorAll('input,textarea,code,pre,span,td,div,a')) {
      const v = (e.value || e.textContent || '').trim();
      if (/^[A-Za-z0-9_\-]{16,64}$/.test(v) && !v.includes('{')) cand.push(v);
    }
    return [...new Set(cand)].slice(0, 5);
  });
  P('AFTER_CLICK_TOKENS', afterClick.map(mask));

  // 3) кандидатные эндпоинты (могут вернуть ключ в JSON)
  const eps = ['/api/key/', '/account/api/', '/my/api/', '/api/my-key/', '/account/api-key/', '/ajax/api-key/'];
  const epRes = [];
  for (const ep of eps) {
    try {
      const r = await page.evaluate(async (u) => { const x = await fetch(u); return { s: x.status, t: (await x.text()).slice(0, 300) }; }, ep);
      const tok = (r.t.match(/[A-Za-z0-9_\-]{20,64}/) || [])[0];
      epRes.push({ ep, status: r.s, tokenMask: mask(isTok(tok) ? tok : ''), sample: r.t.slice(0, 120) });
    } catch (e) { epRes.push({ ep, err: e.message }); }
  }
  P('ENDPOINTS', epRes);

  // 4) сетевые ответы с ключом
  P('NET_BODIES', bodies.slice(0, 8));

  // 5) если где-то нашёлся токен — подтвердим через /api/info/
  const found = afterClick.find(isTok) || epRes.map(e => e.tokenMask).find(() => false);
  const anyTok = afterClick.find(isTok);
  if (anyTok) {
    const info = await page.evaluate(async (k) => {
      try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json(); if (j?.meta){delete j.meta.key;delete j.meta.ip;} return { s: r.status, j }; }
      catch (e) { return { err: e.message }; }
    }, anyTok);
    P('API_INFO', info);
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
