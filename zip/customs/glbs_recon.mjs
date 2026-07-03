// glbs.io — прицельный дамп страницы /api/key/ (там ключ). Маскируем ключ в логе.
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

  for (const url of ['https://glbs.io/api/key/', 'https://glbs.io/api/my-key/']) {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(2500);
    // клик по возможной кнопке «показать/сгенерировать»
    for (const s of ['text=Показать', 'text=Show', 'text=Сгенерировать', 'text=Generate', 'text=Получить', 'button', '.btn']) {
      const el = await page.$(s); if (el) { await el.click().catch(() => {}); await page.waitForTimeout(800); }
    }
    const d = await page.evaluate(() => {
      const inputs = [...document.querySelectorAll('input,textarea')].map(e => ({ name: e.name, id: e.id, type: e.type, ro: e.readOnly, value: (e.value || '') }));
      const codes = [...document.querySelectorAll('code,pre,kbd,samp')].map(e => (e.textContent || '').trim()).filter(Boolean);
      const text = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
      return { url: location.href, text: text.slice(0, 1400), inputs, codes };
    });
    // маскируем длинные значения (потенциальные ключи), не печатаем сырьём
    const inputsMasked = d.inputs.map(i => ({ name: i.name, id: i.id, type: i.type, ro: i.ro, len: i.value.length, mask: i.value.length >= 16 ? mask(i.value) : i.value }));
    const codesMasked = d.codes.map(c => c.length >= 16 ? mask(c) : c).slice(0, 8);
    // выберем самый вероятный ключ: readonly input или code длиной 16..64 без пробелов
    const key = (d.inputs.map(i => i.value).concat(d.codes)).find(v => /^[A-Za-z0-9_\-]{16,64}$/.test((v || '').trim()));
    P('KEYPAGE_' + url.replace(/[^a-z]/gi, '').slice(-8), { url: d.url, text: d.text, inputs: inputsMasked, codes: codesMasked, keyMask: mask(key) });

    if (key) {
      const info = await page.evaluate(async (k) => {
        try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json(); if (j?.meta){delete j.meta.key;delete j.meta.ip;} return { s: r.status, j }; }
        catch (e) { return { err: e.message }; }
      }, key.trim());
      P('API_INFO', info);
      break;
    }
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
