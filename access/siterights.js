// ПРАВА НА САЙТ — канонический экземпляр. Блок между маркерами BEGIN/END siteRights
// вшит в гейты пяти сайтов (ГПУ, ОВЭ-75, ЗИП, гидрометаллургия, ГОК); совпадение копий
// проверяет access/test/siterights.test.mjs.
//
// Периметр — Cloudflare Access: до этой проверки доходят только сотрудники с
// действительным входом. Здесь решается более узкий вопрос: выдан ли этому человеку
// именно ЭТОТ сайт. Список прав держит один узел — портал; гейт спрашивает его,
// переслав подпись входа сотрудника (общих секретов между проектами нет).
//
// НАМЕРЕННО НЕ ЗАПИРАЕМ ПРИ СБОЕ. Если портал недоступен или ответил невнятно,
// сайт открывается: перекрыть работу всей компании из-за недоступности одного узла
// хуже, чем на время потерять разграничение внутри уже закрытого периметра.

import { readCookie } from "./cfaccess.js";   // в гейтах readCookie уже есть — из блока accessOk

// BEGIN siteRights
const RIGHTS_URL = "https://kvant-sourcing-f122.pages.dev/api/rights";
const RIGHTS_TTL = 60 * 1000;              // память изолята: не дёргать портал на каждый файл
const rightsCache = new Map();

// Что стоит журнала: страницы и выгружаемые файлы. Разметка, картинки и шрифты —
// часть страницы, а не действие человека, и в журнал не идут.
const AUDIT_SKIP = /\.(css|js|mjs|map|woff2?|ttf|png|jpe?g|gif|svg|webp|ico|avif)$/i;

async function siteAllowed(request, env, site) {
  if (env && env.SITE_RIGHTS === "off") return true;
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion") || readCookie(request, "CF_Authorization");
  if (!jwt) return true;                   // сюда приходят только вошедшие; подпись проверена выше
  const path = new URL(request.url).pathname;
  const worth = !AUDIT_SKIP.test(path);
  const now = Date.now();
  const hit = rightsCache.get(jwt);
  // Права берём из памяти изолята, но о самом действии портал должен узнать: журналу
  // нужны все обращения, а не первое в минуту. Запись идёт отдельным запросом и
  // ответа не ждёт — задержать отдачу страницы она не может.
  if (hit && now - hit.t < RIGHTS_TTL) {
    if (worth) tell(jwt, site, path);
    return hit.sites === null || hit.sites.includes(site);
  }
  let sites = null;                        // null — «портал не ответил», пускаем
  try {
    const r = await fetch(rightsUrl(site, worth ? path : ""), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    if (r.ok) {
      const d = await r.json();
      if (d && Array.isArray(d.sites)) sites = d.sites;
    }
  } catch { /* сеть между проектами не должна запирать сайт */ }
  if (rightsCache.size > 500) rightsCache.clear();
  rightsCache.set(jwt, { t: now, sites });
  return sites === null || sites.includes(site);
}

function rightsUrl(site, path) {
  const u = new URL(RIGHTS_URL);
  if (path) { u.searchParams.set("site", site); u.searchParams.set("at", path); }
  return u.toString();
}

function tell(jwt, site, path) {
  try {
    const p = fetch(rightsUrl(site, path), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch { /* журнал не должен мешать работе сайта */ }
}
// END siteRights

export { siteAllowed, RIGHTS_URL, rightsCache };
