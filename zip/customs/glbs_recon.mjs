// Разведка glbs.io шаг 2: проверка API-доступа + тестовый поиск (что отдаёт аккаунт).
// Логин по секретам, вывод в лог. Пароль в код НЕ пишется.
import { chromium } from 'playwright';

const USER = process.env.GLBS_USER;
const PASS = process.env.GLBS_PASS;
const P = (t, o) => console.log(`\n===${t}===\n` + (typeof o === 'string' ? o : JSON.stringify(o, null, 1)) + `\n===/${t}===`);

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const net = [];
page.on('request', r => { if (r.method() === 'POST' || /search|api|goods|query|declaration/i.test(r.url())) net.push(`${r.method()} ${r.url().slice(0, 150)}`); });

try {
  // --- логин ---
  await page.goto('https://glbs.io/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(1500);
  await page.fill('input[type=email], input[name*=email i], input[name*=login i]', USER).catch(() => {});
  await page.fill('input[type=password]', PASS).catch(() => {});
  await page.click('button[type=submit], input[type=submit]').catch(() => page.keyboard.press('Enter'));
  await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(3000);
  P('LOGIN_OK', !(await page.$('input[type=password]')));

  // --- страница API ---
  await page.goto('https://glbs.io/api/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(2000);
  const api = await page.evaluate(() => {
    const t = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
    const tokens = [...document.querySelectorAll('input,code,pre')].map(e => (e.value || e.textContent || '').trim()).filter(x => /key|token|[A-Za-z0-9]{20,}/.test(x)).slice(0, 8);
    const links = [...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => /api|doc|swagger|token|key/i.test(h)).slice(0, 15);
    return { url: location.href, text: t.slice(0, 1200), tokens, links };
  });
  P('API_PAGE', api);

  // --- тестовый поиск ---
  await page.goto('https://glbs.io/search/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(2500);
  // дамп ВСЕХ полей ввода (видимых и скрытых) с подписями
  const inputs = await page.evaluate(() => {
    const lab = el => {
      if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) return l.textContent.trim().slice(0, 30); }
      const p = el.closest('.form-group,.field,.input-group,div'); return p ? (p.querySelector('label')?.textContent || '').trim().slice(0, 30) : '';
    };
    return [...document.querySelectorAll('input,select,textarea')].slice(0, 60).map(el => ({
      tag: el.tagName, type: el.type, name: el.name, id: el.id, ph: el.placeholder,
      vis: !!(el.offsetParent), label: lab(el),
    })).filter(x => x.vis || x.name || x.id);
  });
  P('SEARCH_INPUTS', inputs);
  const csrf = await page.$eval('input[name=csrf_token]', e => e.value).catch(() => null);
  P('CSRF_PRESENT', !!csrf);

  // пробуем ввести производителя EPIROC в первое видимое текстовое поле поиска и отправить
  const box = await page.$('input[type=text]:visible, input[type=search]:visible, textarea:visible');
  if (box) {
    await box.fill('EPIROC').catch(() => {});
    await page.waitForTimeout(1200);
    await page.click('#submitForm').catch(() => page.keyboard.press('Enter'));
    await page.waitForLoadState('networkidle', { timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(4000);
    const res = await page.evaluate(() => {
      const t = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
      const rows = document.querySelectorAll('table tr, .result-row, .supply-row, [class*=result] tr').length;
      const pay = /балан|попол|оплат|тариф|доступ|balance|pay|top up|subscrib|недостаточно|no access|limit/i.test(t);
      return { url: location.href, rows, payHint: pay, text: t.slice(0, 1500) };
    });
    P('SEARCH_RESULT', res);
  } else {
    P('SEARCH_RESULT', 'видимое поле ввода не найдено — см. SEARCH_INPUTS');
  }

  // Entry Prices — прямой раздел цен ввоза
  await page.goto('https://glbs.io/entry-prices/', { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(2500);
  P('ENTRY_PRICES', await page.evaluate(() => ({ url: location.href, text: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 900) })));
} catch (e) {
  P('ERROR', e.message);
} finally {
  P('NETWORK', net.slice(0, 40));
  await browser.close();
}
