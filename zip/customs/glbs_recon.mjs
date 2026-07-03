// glbs.io — точный HTML вокруг «Мой ключ» + все ссылки/кнопки на /api/ (найти механизм ключа).
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);

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
  await page.goto('https://glbs.io/api/', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(2500);

  const info = await page.evaluate(() => {
    // элемент, чей СОБСТВЕННЫЙ текст = «Мой ключ»
    const all = [...document.querySelectorAll('*')];
    const label = all.find(e => {
      const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
      return /^Мой ключ$|^My key$/i.test(own);
    });
    let around = '';
    if (label) {
      const parts = [label.outerHTML];
      let s = label.nextElementSibling, n = 0;
      while (s && n < 4) { parts.push(s.outerHTML); s = s.nextElementSibling; n++; }
      // если сиблингов мало — берём outerHTML родителя
      around = (parts.join('\n').length < 200 && label.parentElement ? label.parentElement.outerHTML : parts.join('\n'));
    }
    around = (around || '(«Мой ключ» как отдельный текст не найден)').replace(/\s+/g, ' ').slice(0, 2500);

    // все ссылки и кнопки, где в href/тексте/атрибутах есть key/api/generate/показать
    const acts = [];
    for (const e of document.querySelectorAll('a,button,[onclick],[data-url],[data-href],[data-target],[data-toggle]')) {
      const attrs = [...e.attributes].map(a => `${a.name}=${a.value}`).join(' ');
      if (/key|api|generat|показ|получ|создать|token/i.test((e.textContent || '') + ' ' + attrs))
        acts.push({ tag: e.tagName, text: (e.textContent || '').trim().slice(0, 25), attrs: attrs.slice(0, 160) });
    }
    // список inline-скриптов с упоминанием key/api-key + короткие фрагменты
    const scripts = [];
    for (const s of document.querySelectorAll('script:not([src])')) {
      const c = s.textContent || '';
      const m = c.match(/.{0,40}(api[_-]?key|apiKey|['"]key['"]).{0,60}/i);
      if (m) scripts.push(m[0].replace(/\s+/g, ' ').slice(0, 120));
    }
    return { around, acts: acts.slice(0, 20), scripts: scripts.slice(0, 8) };
  });
  P('AROUND_MYKEY', info.around);
  P('ACTIONS', info.acts);
  P('INLINE_SCRIPTS', info.scripts);
} catch (e) { P('ERROR', e.message); }
finally { await browser.close(); }
