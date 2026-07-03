// glbs.io — забрать документацию метода supplies-search (ключ не нужен) и попытаться
// найти API-ключ в HTML страницы /api/ (data-атрибут / input / инлайн-скрипт). Маскируем.
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);
const mask = k => k ? `len=${k.length} ${k.slice(0, 3)}…${k.slice(-2)}` : 'нет';

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
try {
  if (!await login(page)) { P('LOGIN_OK', false); throw new Error('login'); }
  P('LOGIN_OK', true);

  // 1) документация метода — параметры запроса
  await page.goto('https://glbs.io/api/methods/supplies-search/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(2500);
  P('METHOD_DOC', await page.evaluate(() => (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 3200)));

  // 2) ищем ключ на /api/: клик «Мой ключ» + разбор HTML/атрибутов/инпутов/скриптов
  await page.goto('https://glbs.io/api/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(2000);
  for (const sel of ['text=Мой ключ', 'text=My key', '[data-target*=key]', 'button:has-text("ключ")', 'a:has-text("ключ")']) {
    const el = await page.$(sel); if (el) { await el.click().catch(() => {}); await page.waitForTimeout(1200); }
  }
  await page.waitForTimeout(1200);
  const found = await page.evaluate(() => {
    const res = { inputs: [], dataAttr: [], tokens: [], keyHtml: '' };
    for (const e of document.querySelectorAll('input,textarea')) {
      const v = (e.value || '').trim();
      res.inputs.push({ name: e.name, id: e.id, type: e.type, len: v.length, tok: /^[A-Za-z0-9_\-]{16,}$/.test(v) });
    }
    for (const e of document.querySelectorAll('*')) {
      for (const a of e.attributes || []) {
        if (/key|token|api/i.test(a.name) && /^[A-Za-z0-9_\-]{16,}$/.test(a.value)) res.dataAttr.push(`${e.tagName}.${a.name}`);
      }
    }
    // элемент рядом с «Мой ключ»
    const walker = [...document.querySelectorAll('*')].find(e => /Мой ключ|My key/i.test(e.textContent || '') && e.children.length < 4);
    if (walker) res.keyHtml = (walker.closest('div,section,li') || walker).outerHTML.replace(/\s+/g, ' ').slice(0, 700);
    // токены в инлайн-скриптах
    for (const s of document.querySelectorAll('script:not([src])')) {
      const m = (s.textContent || '').match(/(api[_-]?key|apikey)["'\s:=]{1,4}([A-Za-z0-9_\-]{16,})/i);
      if (m) res.tokens.push(m[2]);
    }
    return res;
  });
  // маскируем возможный ключ из скриптов
  const scriptKey = found.tokens[0] || '';
  P('KEY_SEARCH', { inputs: found.inputs, dataAttr: found.dataAttr, keyHtml: found.keyHtml, scriptKeyMask: mask(scriptKey) });

  // если нашли ключ в скрипте — быстрый /api/info/ для подтверждения (ключ вырезаем)
  if (scriptKey) {
    const info = await page.evaluate(async (k) => {
      try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json();
        if (j?.meta) { delete j.meta.key; delete j.meta.ip; } return { status: r.status, json: j };
      } catch (e) { return { err: e.message }; }
    }, scriptKey);
    P('API_INFO', info);
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
