// glbs.io — раскрыть API-ключ (в лог НЕ печатаем, только маску), проверить /api/info/
// и метод supplies-search. Логин по секретам. Пароль/ключ в лог не попадают.
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
  if (!await login(page)) { P('LOGIN_OK', false); throw new Error('login failed'); }
  P('LOGIN_OK', true);

  await page.goto('https://glbs.io/api/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(2000);
  // клик по «Мой ключ» / «My key»
  for (const t of ['Мой ключ', 'My key', 'ключ', 'key']) {
    const el = await page.$(`text=${t}`);
    if (el) { await el.click().catch(() => {}); await page.waitForTimeout(1500); break; }
  }
  await page.waitForTimeout(1500);
  // извлекаем ключ: длинный токен без плейсхолдеров {}
  const key = await page.evaluate(() => {
    const cand = [];
    for (const e of document.querySelectorAll('input,textarea,code,pre,span,div')) {
      const v = (e.value || e.textContent || '').trim();
      if (/^[A-Za-z0-9_\-]{16,64}$/.test(v) && !v.includes('{')) cand.push(v);
    }
    return cand.sort((a, b) => b.length - a.length)[0] || '';
  });
  P('API_KEY_MASK', mask(key));
  if (!key) { P('NOTE', 'ключ не раскрылся кликом — нужен ручной (glbs.io → API → «Мой ключ»)'); }
  else {
    // /api/info/ — лимиты и режим (ключ вырезаем из вывода)
    const info = await page.evaluate(async (k) => {
      try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json();
        if (j?.meta) delete j.meta.key; if (j?.meta) delete j.meta.ip; return { status: r.status, json: j };
      } catch (e) { return { err: e.message }; }
    }, key);
    P('API_INFO', info);

    // документация метода supplies-search (параметры)
    await page.goto('https://glbs.io/api/methods/supplies-search/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(2000);
    P('METHOD_DOC', await page.evaluate(() => (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 2600)));

    // пробный вызов supplies-search по EPIROC (структуру печатаем, ключ — нет)
    const sample = await page.evaluate(async (k) => {
      const tries = [
        `/api/methods/supplies-search/?api-key=${k}&query=EPIROC&limit=2`,
        `/api/methods/supplies-search/?api-key=${k}&goods=EPIROC&country=ru&limit=2`,
        `/api/methods/supplies-search/?api-key=${k}&text=EPIROC&napr=import&limit=2`,
      ];
      const out = [];
      for (const u of tries) {
        try { const r = await fetch(u); const t = await r.text();
          out.push({ url: u.replace(/api-key=[^&]+/, 'api-key=***'), status: r.status, body: t.slice(0, 900) });
        } catch (e) { out.push({ err: e.message }); }
      }
      return out;
    }, key);
    P('API_SAMPLE', sample);
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
