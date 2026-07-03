// Разведка glbs.io (Глобус-ВЭД) — запускается в GitHub Actions (там есть сеть).
// Логин по секретам GLBS_USER / GLBS_PASS, печать структуры страниц + сетевых POST
// ПРЯМО В ЛОГ (артефакт из сессии не скачать). По выводу пишется парсер/клиент API.
// Пароль в код НЕ пишется — только из env.
import { chromium } from 'playwright';

const USER = process.env.GLBS_USER;
const PASS = process.env.GLBS_PASS;
const P = (tag, obj) => console.log(`\n===${tag}===\n` + (typeof obj === 'string' ? obj : JSON.stringify(obj, null, 1)) + `\n===/${tag}===`);

async function snap(page, tag) {
  try {
    await page.waitForSelector('body', { timeout: 10000 }).catch(() => {});
    const s = await page.evaluate(() => {
      const b = document.body;
      const forms = [...document.forms].map(f => ({
        action: f.action, method: f.method, id: f.id,
        fields: [...f.elements].map(el => ({ tag: el.tagName, type: el.type, name: el.name, id: el.id, ph: el.placeholder })),
      }));
      const nav = [...document.querySelectorAll('a[href], [role=menuitem], .nav-link, .menu a')].slice(0, 70)
        .map(a => ({ text: (a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 45), href: a.href || '' }))
        .filter(x => x.text || x.href);
      return {
        url: location.href, title: document.title,
        hasPass: !!document.querySelector('input[type=password]'),
        forms, nav, text: (b ? b.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 900),
      };
    });
    P(tag, s);
    return s;
  } catch (e) { P(tag + '_ERR', e.message); return null; }
}

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();
const posts = [];
page.on('request', r => {
  if (r.method() === 'POST') {
    let pd = ''; try { pd = (r.postData() || '').slice(0, 300); } catch {}
    posts.push(`POST ${r.url().slice(0, 160)}  DATA: ${pd}`);
  }
});
const apis = [];
page.on('response', async r => {
  const u = r.url(), ct = (r.headers()['content-type'] || '');
  if (/json/i.test(ct) && /glbs\.io/.test(u)) apis.push(`${r.status()} ${u.slice(0, 170)}`);
});

try {
  await page.goto('https://glbs.io/', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(2000);
  const s1 = await snap(page, 'LANDING');

  if (s1 && s1.hasPass) {
    await page.fill('input[type=email], input[name*=email i], input[name*=login i]', USER).catch(async () => {
      await page.fill('input[type=text]', USER).catch(() => {});
    });
    await page.fill('input[type=password]', PASS).catch(() => {});
    console.log('логин заполнен, жму Sign In');
    await page.click('button[type=submit], input[type=submit]').catch(() => page.keyboard.press('Enter'));
    await page.waitForLoadState('networkidle', { timeout: 60000 }).catch(() => {});
    await page.waitForTimeout(4000);
    const s2 = await snap(page, 'AFTER_LOGIN');
    console.log(s2 && s2.hasPass ? 'ЛОГИН НЕ ПРОШЁЛ (пароль ещё на странице)' : 'ЛОГИН ПРОШЁЛ (пароля нет)');

    if (s2 && !s2.hasPass) {
      // ищем раздел статистики/поиска
      const hints = (s2.nav || []).filter(l => /поиск|стат|деклар|импорт|экспорт|тнвэд|tnved|search|dashboard|данные|отчёт|analytic|trade|import|export/i.test(l.text + ' ' + l.href));
      P('NAV_HINTS', hints.length ? hints : '(в навигации явных ссылок на поиск не видно — см. nav в AFTER_LOGIN)');
      // пробуем несколько типовых URL раздела поиска
      for (const u of ['https://glbs.io/search/', 'https://glbs.io/statistic/', 'https://glbs.io/trade/', 'https://glbs.io/cabinet/', (hints[0] && hints[0].href)]) {
        if (!u) continue;
        await page.goto(u, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
        await page.waitForTimeout(2500);
        const ss = await snap(page, 'SECTION_' + u.replace(/[^a-z]/gi, '').slice(-12));
        if (ss && (ss.forms.some(f => f.fields.length > 1) || /тнвэд|деклар|импорт|hs|8431|8421/i.test(ss.text))) {
          P('SECTION_LOOKS_LIKE_SEARCH', u);
          break;
        }
      }
    }
  } else {
    P('NOTE', 'нет поля пароля на лендинге');
  }
} catch (e) {
  P('ERROR', e.message + '\n' + (e.stack || '').slice(0, 500));
} finally {
  P('POSTS', posts.length ? posts : 'нет POST-запросов');
  P('JSON_APIS', apis.length ? apis : 'нет JSON-ответов с glbs.io');
  await browser.close();
}
