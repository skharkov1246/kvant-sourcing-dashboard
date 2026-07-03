// glbs.io — устойчивый логин + полный дамп страницы /api/ (ищем API-ключ/доступ).
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);

async function login(page) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    await page.goto('https://glbs.io/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(1500);
    if (!(await page.$('input[type=password]'))) return true; // уже залогинены (кука)
    await page.fill('input[type=email],input[name*=login i],input[type=text]', USER).catch(() => {});
    await page.fill('input[type=password]', PASS).catch(() => {});
    await page.click('button[type=submit],input[type=submit]').catch(() => {});
    // ждём исчезновения поля пароля до 20с
    try { await page.waitForFunction(() => !document.querySelector('input[type=password]'), { timeout: 20000 }); } catch {}
    await page.waitForTimeout(2500);
    if (!(await page.$('input[type=password]'))) { P('LOGIN_ATTEMPT', `успех с попытки ${attempt}`); return true; }
  }
  return false;
}

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
try {
  const ok = await login(page);
  P('LOGIN_OK', ok);
  if (!ok) { P('ABORT', 'логин не удался после 3 попыток'); }
  else {
    for (const url of ['https://glbs.io/api/', 'https://glbs.io/account/profile/', 'https://glbs.io/my/']) {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await page.waitForTimeout(2500);
      const d = await page.evaluate(() => {
        const txt = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
        const inputs = [...document.querySelectorAll('input,textarea')].map(e => ({ name: e.name, id: e.id, type: e.type, val: (e.value || '').slice(0, 60) })).filter(x => x.name || x.id);
        const buttons = [...document.querySelectorAll('button,a.btn,[role=button]')].map(b => (b.textContent || '').trim().slice(0, 35)).filter(Boolean).slice(0, 25);
        const apiLinks = [...document.querySelectorAll('a[href]')].map(a => ({ t: a.textContent.trim().slice(0, 30), h: a.href })).filter(x => /api|key|token|doc|swagger|integration/i.test(x.t + x.h)).slice(0, 20);
        const keyish = [...document.querySelectorAll('code,pre,input,textarea,span')].map(e => (e.value || e.textContent || '').trim()).filter(s => /[A-Za-z0-9_\-]{24,}/.test(s) && !/cookie|font|css|\.js/i.test(s)).slice(0, 6);
        return { url: location.href, textLen: txt.length, text: txt.slice(0, 2600), inputs: inputs.slice(0, 25), buttons, apiLinks, keyish };
      });
      P('PAGE_' + url.replace(/[^a-z]/gi, '').slice(-10), d);
    }
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
