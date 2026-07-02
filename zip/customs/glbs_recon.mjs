// Разведка glbs.io (Глобус-ВЭД) — запускается в GitHub Actions, где есть сеть.
// Логинится по секретам GLBS_USER / GLBS_PASS и сохраняет структуру страниц как
// артефакты (screenshot + HTML + список форм/полей/ссылок), чтобы по ним написать
// точный парсер таможенной статистики. Пароль в код НЕ пишется — только из env.
//
// Локально из этой сессии не работает (сеть закрыта). Гоняется workflow zip-customs.yml.
import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';

const USER = process.env.GLBS_USER;
const PASS = process.env.GLBS_PASS;
const OUT = 'zip/customs/recon';
mkdirSync(OUT, { recursive: true });

const log = [];
const note = (m) => { log.push(m); console.log(m); };

async function dump(page, tag) {
  try { await page.screenshot({ path: `${OUT}/${tag}.png`, fullPage: true }); } catch (e) { note(`shot ${tag}: ${e.message}`); }
  try { writeFileSync(`${OUT}/${tag}.html`, await page.content()); } catch (e) {}
  const forms = await page.evaluate(() => [...document.forms].map(f => ({
    action: f.action, method: f.method,
    fields: [...f.elements].map(el => ({ tag: el.tagName, type: el.type, name: el.name, id: el.id, ph: el.placeholder })),
  })));
  const links = await page.evaluate(() => [...document.querySelectorAll('a[href]')].slice(0, 80)
    .map(a => ({ text: a.textContent.trim().slice(0, 40), href: a.href })));
  writeFileSync(`${OUT}/${tag}.json`, JSON.stringify({ url: page.url(), title: await page.title(), forms, links }, null, 1));
  note(`[${tag}] ${page.url()} — форм: ${forms.length}, ссылок: ${links.length}`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const apiCalls = [];
page.on('request', r => { const u = r.url(); if (/\/api\/|\.json|graphql|rest/i.test(u)) apiCalls.push(`${r.method()} ${u}`); });

try {
  await page.goto('https://glbs.io/', { waitUntil: 'networkidle', timeout: 60000 });
  await dump(page, '01-landing');

  // эвристический логин: ищем поле пароля и парный текст/email
  const passSel = 'input[type=password]';
  if (await page.$(passSel)) {
    const userSel = 'input[type=email], input[type=text], input[name*=login i], input[name*=user i], input[name*=email i]';
    if (USER && await page.$(userSel)) await page.fill(userSel, USER);
    if (PASS) await page.fill(passSel, PASS);
    note('поля логина заполнены, отправляю форму');
    await Promise.all([
      page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {}),
      page.keyboard.press('Enter'),
    ]);
    await page.waitForTimeout(3000);
    await dump(page, '02-after-login');
  } else {
    note('поле пароля на лендинге не найдено — ищи кнопку «Войти» в 01-landing.json (links)');
  }

  writeFileSync(`${OUT}/_apicalls.txt`, apiCalls.join('\n'));
  note(`перехвачено API-запросов: ${apiCalls.length} (см. _apicalls.txt)`);
} catch (e) {
  note('ОШИБКА: ' + e.message);
} finally {
  writeFileSync(`${OUT}/_log.txt`, log.join('\n'));
  await browser.close();
}
