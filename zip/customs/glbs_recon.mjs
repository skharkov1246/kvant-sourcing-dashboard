// glbs.io — ключ рендерится JS-ом в карточку <h3> под «Мой ключ» (через socket.io).
// Ждём наполнения карточки и читаем ключ (в лог — только маска), подтверждаем /api/info/.
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

// читает текст карточки-ключа под заголовком «Мой ключ»
const READ = () => {
  const h1 = [...document.querySelectorAll('h1,h2,h3,h4')].find(e => /^\s*(Мой ключ|My key)\s*$/i.test(e.textContent || ''));
  let card = h1 && h1.nextElementSibling;
  // если следующий не карточка — поищем ближайший h3.text-nowrap
  if (!card || !/[A-Za-z0-9]/.test(card.innerText || '')) card = document.querySelector('h3.text-nowrap');
  const txt = (card ? card.innerText : '').replace(/\s+/g, '').trim();
  return txt;
};

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
try {
  if (!await login(page)) { P('LOGIN_OK', false); throw new Error('login'); }
  P('LOGIN_OK', true);
  await page.goto('https://glbs.io/api/', { waitUntil: 'networkidle', timeout: 45000 });

  // ждём до ~30с, пока карточка наполнится токеном
  let key = '';
  for (let i = 0; i < 30; i++) {
    const v = await page.evaluate(READ);
    if (/^[A-Za-z0-9_\-]{16,64}$/.test(v)) { key = v; break; }
    await page.waitForTimeout(1000);
  }
  P('KEY_FOUND', mask(key));
  if (!key) {
    const dbg = await page.evaluate(() => {
      const h3 = document.querySelector('h3.text-nowrap');
      return { h3html: h3 ? h3.outerHTML.replace(/\s+/g, ' ').slice(0, 300) : 'нет h3.text-nowrap', h3text: h3 ? (h3.innerText || '').trim() : '' };
    });
    P('KEY_DEBUG', dbg);
  } else {
    const info = await page.evaluate(async (k) => {
      try { const r = await fetch(`/api/info/?api-key=${k}`); const j = await r.json(); if (j?.meta){delete j.meta.key;delete j.meta.ip;} return { s: r.status, j }; }
      catch (e) { return { err: e.message }; }
    }, key);
    P('API_INFO', info);
  }
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
