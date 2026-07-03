// glbs.io — реальный тестовый поиск (прототип парсера). Логин по секретам, вывод в лог.
import { chromium } from 'playwright';
const USER = process.env.GLBS_USER, PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
const gets = [];
page.on('request', r => { const u = r.url(); if (/\/search\/\?|\/api\/|goods|declaration|supply/i.test(u)) gets.push(`${r.method()} ${u.slice(0, 180)}`); });

try {
  await page.goto('https://glbs.io/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(1200);
  await page.fill('input[type=email],input[name*=login i]', USER).catch(() => {});
  await page.fill('input[type=password]', PASS).catch(() => {});
  await page.click('button[type=submit],input[type=submit]').catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(2500);

  await page.goto('https://glbs.io/search/', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(2500);

  // товарный запрос в мультиселект-поле
  const q = 'EPIROC';
  await page.fill('#G33-input-m', q).catch(e => P('FILL_ERR', e.message));
  await page.waitForTimeout(1800);
  // выбрать подсказку из выпадашки, иначе Enter
  const opt = await page.$('.dropdown-menu:visible li, .multiselect-dropdown li:visible, [class*=dropdown] li:visible, ul[role=listbox] li');
  if (opt) { await opt.click().catch(() => {}); } else { await page.keyboard.press('Enter').catch(() => {}); }
  await page.waitForTimeout(800);
  // широкий период
  await page.fill('#date_1', '2023-01-01').catch(() => {});
  await page.fill('#date_2', '2026-03-31').catch(() => {});
  // значение скрытого goods/query после ввода
  const hid = await page.evaluate(() => ({
    goods: document.querySelector('#searchGoods')?.value || document.querySelector('[name=goods]')?.value || '',
    query: document.querySelector('[name=query]')?.value || '',
    fields: document.querySelector('[name=fields]')?.value || '',
  }));
  P('HIDDEN_AFTER_INPUT', hid);

  await page.click('#submitForm').catch(() => page.keyboard.press('Enter'));
  await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(5000);

  const res = await page.evaluate(() => {
    const t = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
    const tables = [...document.querySelectorAll('table')];
    let best = null, max = 0;
    for (const tb of tables) { const n = tb.querySelectorAll('tr').length; if (n > max) { max = n; best = tb; } }
    const headers = best ? [...best.querySelectorAll('thead th, tr:first-child th, tr:first-child td')].map(c => c.textContent.trim().slice(0, 30)) : [];
    const rows = best ? [...best.querySelectorAll('tbody tr')].slice(0, 4).map(r => [...r.querySelectorAll('td')].map(c => c.textContent.replace(/\s+/g, ' ').trim().slice(0, 40))) : [];
    const cnt = (t.match(/(Найдено|Found|results?|поставок|shipments?|записей)[^0-9]{0,15}([\d  ,\.]{1,12})/i) || [])[0] || '';
    const gate = /недостаточно|пополн|оплат|нет доступа|no access|insufficient|top up|upgrade|заблок/i.test(t);
    return { url: location.href, tableRows: max, headers, rows, countHint: cnt, gate, textHead: t.slice(0, 400), textTail: t.slice(-400) };
  });
  P('REAL_SEARCH', res);
} catch (e) { P('ERROR', e.message); }
finally { P('GETS', gets.slice(0, 25)); await browser.close(); }
