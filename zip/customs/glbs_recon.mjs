// Разведка glbs.io (Глобус-ВЭД) — запускается в GitHub Actions (там есть сеть).
// Логинится по секретам GLBS_USER / GLBS_PASS и печатает структуру страниц ПРЯМО В ЛОГ
// (артефакт из сессии не скачать, а логи читаются напрямую). По этому выводу пишется
// точный парсер таможенной статистики. Пароль в код НЕ пишется — только из env.
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';

const USER = process.env.GLBS_USER;
const PASS = process.env.GLBS_PASS;
const OUT = 'zip/customs/recon';
mkdirSync(OUT, { recursive: true });

const P = (tag, obj) => console.log(`\n===${tag}===\n` + (typeof obj === 'string' ? obj : JSON.stringify(obj, null, 1)) + `\n===/${tag}===`);

async function snap(page) {
  return await page.evaluate(() => {
    const forms = [...document.forms].map(f => ({
      action: f.action, method: f.method, id: f.id, cls: f.className,
      fields: [...f.elements].map(el => ({ tag: el.tagName, type: el.type, name: el.name, id: el.id, ph: el.placeholder })),
    }));
    const buttons = [...document.querySelectorAll('button, input[type=submit], a.btn, [role=button]')]
      .slice(0, 30).map(b => ({ tag: b.tagName, type: b.type, text: (b.innerText || b.value || '').trim().slice(0, 40) }));
    const links = [...document.querySelectorAll('a[href]')].slice(0, 60)
      .map(a => ({ text: a.textContent.trim().slice(0, 40), href: a.href }));
    const hasPass = !!document.querySelector('input[type=password]');
    const text = (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 700);
    return { url: location.href, title: document.title, hasPass, forms, buttons, links, text };
  });
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const reqs = [];
page.on('request', r => { const u = r.url(); if (/glbs\.io|api|json|graphql|rest|search|query|customs|tnved|declaration/i.test(u)) reqs.push(`${r.method()} ${u}`.slice(0, 200)); });

try {
  await page.goto('https://glbs.io/', { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(1500);
  const s1 = await snap(page);
  P('LANDING', s1);
  try { await page.screenshot({ path: `${OUT}/01-landing.png`, fullPage: true }); writeFileSync(`${OUT}/01-landing.html`, await page.content()); } catch {}

  if (s1.hasPass) {
    const userSel = 'input[type=email], input[type=text], input[name*=login i], input[name*=user i], input[name*=email i], input[name*=phone i]';
    if (USER && await page.$(userSel)) await page.fill(userSel, USER);
    if (PASS) await page.fill('input[type=password]', PASS);
    console.log('поля логина заполнены; ищу кнопку отправки');
    // пробуем кнопку submit, иначе Enter
    const btn = await page.$('button[type=submit], input[type=submit], form button');
    await Promise.all([
      page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {}),
      btn ? btn.click().catch(() => {}) : page.keyboard.press('Enter'),
    ]);
    await page.waitForTimeout(4000);
    const s2 = await snap(page);
    P('AFTER_LOGIN', s2);
    console.log(s2.hasPass ? 'ВНИМАНИЕ: поле пароля ещё на странице — логин, вероятно, НЕ прошёл' : 'поле пароля исчезло — логин, похоже, прошёл');
    try { await page.screenshot({ path: `${OUT}/02-after-login.png`, fullPage: true }); writeFileSync(`${OUT}/02-after-login.html`, await page.content()); } catch {}

    // если залогинились — попробуем найти раздел поиска/статистики и ткнуться в один HS-код
    if (!s2.hasPass) {
      const navHints = s2.links.filter(l => /поиск|стат|деклар|импорт|экспорт|тнвэд|search|dashboard|данные|отчёт/i.test(l.text + l.href));
      P('NAV_HINTS', navHints);
      const target = navHints[0];
      if (target) {
        await page.goto(target.href, { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
        await page.waitForTimeout(3000);
        P('SECTION', await snap(page));
        try { await page.screenshot({ path: `${OUT}/03-section.png`, fullPage: true }); } catch {}
      }
    }
  } else {
    P('NOTE', 'на лендинге нет поля пароля — логин, вероятно, за кнопкой/на отдельной странице (см. links выше)');
  }

  P('NETWORK', reqs.length ? reqs : 'ни одного подходящего запроса не перехвачено');
} catch (e) {
  P('ERROR', e.message + '\n' + (e.stack || ''));
} finally {
  await browser.close();
}
