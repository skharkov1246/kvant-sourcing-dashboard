// glbs.io — достать API-ключ: (1) window-глобалы, (2) AJAX при клике «Мой ключ» → слайд-панель.
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);
const mask = k => k ? `len=${k.length} ${k.slice(0, 3)}…${k.slice(-2)}` : 'нет';
const isTok = s => /^[A-Za-z0-9_\-]{16,64}$/.test((s || '').trim());

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
const ajax = [];
page.on('response', async r => {
  try {
    const u = r.url(); if (!/glbs\.io/.test(u)) return;
    const ct = r.headers()['content-type'] || '';
    if (!/json|text\/html|text\/plain/.test(ct)) return;
    if (/\.css|\.js|\/assets\//.test(u)) return;
    const t = await r.text();
    if (t.length < 6000 && /[A-Za-z0-9_\-]{20,64}/.test(t)) ajax.push({ url: u.slice(0, 130), body: t.slice(0, 600) });
  } catch {}
});

try {
  if (!await login(page)) { P('LOGIN_OK', false); throw new Error('login'); }
  P('LOGIN_OK', true);
  await page.goto('https://glbs.io/api/', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(2000);

  // (1) window-глобалы: строки-токены и объекты с полем key/apikey/token
  const globals = await page.evaluate(() => {
    const out = { strings: [], objects: [] };
    for (const k of Object.keys(window)) {
      let v; try { v = window[k]; } catch { continue; }
      if (typeof v === 'string' && /^[A-Za-z0-9_\-]{16,64}$/.test(v)) out.strings.push({ k, v });
      else if (v && typeof v === 'object') {
        try { for (const kk of Object.keys(v)) {
          if (/key|token|api/i.test(kk) && typeof v[kk] === 'string' && /^[A-Za-z0-9_\-]{16,64}$/.test(v[kk])) out.objects.push({ path: `${k}.${kk}`, v: v[kk] });
        } } catch {}
      }
    }
    return out;
  });
  P('WINDOW_GLOBALS', { strings: globals.strings.map(x => ({ k: x.k, mask: mask(x.v) })), objects: globals.objects.map(x => ({ path: x.path, mask: mask(x.v) })) });
  let key = (globals.strings.find(x => isTok(x.v)) || {}).v || (globals.objects.find(x => isTok(x.v)) || {}).v || '';

  // (2) ссылка «Мой ключ»: outerHTML + клик → панель
  const linkHtml = await page.evaluate(() => {
    const el = [...document.querySelectorAll('a,button,[data-target],[data-href]')].find(e => /Мой ключ|My key/i.test(e.textContent || '') || /key/i.test(e.getAttribute('data-target') || e.getAttribute('href') || ''));
    return el ? el.outerHTML.slice(0, 300) : null;
  });
  P('MYKEY_LINK', linkHtml);
  for (const sel of ['a:has-text("Мой ключ")', 'button:has-text("Мой ключ")', 'text=Мой ключ', '[data-target*=key]', '[href*=key]']) {
    const el = await page.$(sel); if (el) { await el.click().catch(() => {}); await page.waitForTimeout(2500); break; }
  }
  await page.waitForTimeout(2500);
  // панель / появившиеся токены
  const panel = await page.evaluate(() => {
    const p = document.querySelector('.cd-panel.is-visible, .cd-panel, .modal.show, [class*=panel][class*=visible]');
    const ptext = p ? (p.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 400) : '';
    const cand = [];
    for (const e of document.querySelectorAll('input,textarea,code,pre,span,td,div')) {
      const v = (e.value || e.textContent || '').trim();
      if (/^[A-Za-z0-9_\-]{16,64}$/.test(v) && !v.includes('{')) cand.push(v);
    }
    return { ptext, tokens: [...new Set(cand)].slice(0, 6) };
  });
  P('PANEL', { text: panel.text, tokens: panel.tokens.map(mask) });
  if (!key) key = panel.tokens.find(isTok) || '';

  P('AJAX_TOKENISH', ajax.slice(0, 6));
  P('KEY_FOUND', mask(key));

  if (key) {
    const info = await page.evaluate(async (k) => {
      try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json(); if (j?.meta){delete j.meta.key;delete j.meta.ip;} return { s: r.status, j }; }
      catch (e) { return { err: e.message }; }
    }, key.trim());
    P('API_INFO', info);
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
