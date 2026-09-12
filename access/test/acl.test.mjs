// Права доступа: роли, точечные добавки, резка дашборда и сверка копии блока в гейте.
// Запуск: node --test access/test/acl.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  SITE_IDS, TAB_IDS, PAYLOADS, ACL_KEY,
  defaultAcl, normalizeAcl, rightsFor, loadAcl, saveAcl, touchUser, loadSeen, cutDashboard,
} from "../acl.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Хранилище-подпорка вместо Cloudflare KV.
function kv() {
  const box = new Map();
  return {
    box,
    get: async (k, o) => { const v = box.get(k); return v == null ? null : (o && o.type === "json" ? JSON.parse(v) : v); },
    put: async (k, v) => { box.set(k, v); },
    list: async ({ prefix = "" } = {}) => ({
      keys: [...box.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
      list_complete: true,
    }),
  };
}

test("роль по умолчанию даёт сотруднику всё, гость не получает ничего", () => {
  const acl = defaultAcl();
  const r = rightsFor(acl, "ivanov@kvantpro.com", {});
  assert.deepEqual(r.sites, SITE_IDS);
  assert.deepEqual(r.tabs, TAB_IDS);
  assert.equal(r.admin, false);

  acl.users["guest@kvantpro.com"] = { role: "guest", sites: [], tabs: [], note: "", seen: 1 };
  const g = rightsFor(acl, "GUEST@kvantpro.com", {});
  assert.deepEqual(g.sites, []);
  assert.deepEqual(g.tabs, []);
});

test("роль сорсинга ограничивает вкладки, точечная добавка расширяет", () => {
  const acl = defaultAcl();
  acl.users["s@kvantpro.com"] = { role: "sourcing", sites: [], tabs: [], note: "", seen: 1 };
  const base = rightsFor(acl, "s@kvantpro.com", {});
  assert.deepEqual(base.tabs, ["sourcing", "contracts", "suppliers"]);
  assert.deepEqual(base.sites, ["dashboard", "zip", "gt", "gpu", "knowledge"]);

  acl.users["s@kvantpro.com"].tabs = ["kam"];
  acl.users["s@kvantpro.com"].sites = ["gok"];
  const wide = rightsFor(acl, "s@kvantpro.com", {});
  assert.deepEqual(wide.tabs, ["sourcing", "kam", "contracts", "suppliers"]);   // порядок — как в TAB_IDS
  assert.deepEqual(wide.sites, ["dashboard", "zip", "gt", "gpu", "knowledge", "gok"]);
});

test("ADMIN_EMAILS даёт полные права даже при пустой роли — страховка от потери хранилища", () => {
  const acl = defaultAcl();
  acl.users["stepan@kvantpro.com"] = { role: "guest", sites: [], tabs: [], note: "", seen: 1 };
  const r = rightsFor(acl, "Stepan@KvantPro.com", {});
  assert.equal(r.admin, true);
  assert.deepEqual(r.sites, SITE_IDS);
  assert.deepEqual(r.tabs, TAB_IDS);

  const other = rightsFor(acl, "stepan@kvantpro.com", { ADMIN_EMAILS: "boss@kvantpro.com" });
  assert.equal(other.admin, false);
  assert.equal(rightsFor(acl, "boss@kvantpro.com", { ADMIN_EMAILS: "boss@kvantpro.com, alt@kvantpro.com" }).admin, true);
});

test("роль owner несёт признак админа", () => {
  const acl = defaultAcl();
  acl.users["o@kvantpro.com"] = { role: "owner", sites: [], tabs: [], note: "", seen: 1 };
  assert.equal(rightsFor(acl, "o@kvantpro.com", {}).admin, true);
  assert.equal(rightsFor(acl, "o@kvantpro.com", {}).roleName, "Владелец");
});

test("испорченный документ из хранилища чинится, мусорные права отбрасываются", () => {
  const a = normalizeAcl(null);
  assert.equal(a.defaultRole, "employee");
  assert.ok(a.roles.owner.admin);

  const b = normalizeAcl({
    defaultRole: "нет-такой-роли",
    roles: { weird: { name: "Странная", sites: ["dashboard", "марс"], tabs: ["kam", "выдумка"] }, broken: null },
    users: { " MIXED@Kvant.Com ": { role: "нет-такой", sites: ["gpu", "х"], tabs: ["сорсинг"], note: "x".repeat(500), seen: "3" },
             bad: 5 },
  });
  assert.equal(b.defaultRole, "employee");
  assert.deepEqual(b.roles.weird.sites, ["dashboard"]);
  assert.deepEqual(b.roles.weird.tabs, ["kam"]);
  assert.equal(b.roles.weird.admin, false);
  assert.equal(b.roles.broken, undefined);
  assert.ok(b.users["mixed@kvant.com"], "почта пользователя приводится к нижнему регистру и обрезается");
  assert.equal(b.users["mixed@kvant.com"].role, "employee", "несуществующая роль заменяется ролью по умолчанию");
  assert.deepEqual(b.users["mixed@kvant.com"].sites, ["gpu"]);
  assert.deepEqual(b.users["mixed@kvant.com"].tabs, []);
  assert.equal(b.users["mixed@kvant.com"].note.length, 200);
  assert.equal(b.users.bad, undefined);
});

test("запись и чтение хранилища, без привязки KV — права по умолчанию", async () => {
  const store = kv();
  const env = { ACL: store };
  const acl = defaultAcl();
  acl.users["a@kvantpro.com"] = { role: "kam", sites: [], tabs: [], note: "", seen: 1 };
  assert.equal(await saveAcl(env, acl), true);
  assert.ok(store.box.has(ACL_KEY));
  assert.equal((await loadAcl(env)).users["a@kvantpro.com"].role, "kam");

  assert.equal(await saveAcl({}, acl), false, "без хранилища запись не делает вид, что удалась");
  assert.deepEqual(await loadAcl({}), defaultAcl());
});

test("хранилище берётся из VISITS, если отдельная привязка ACL не заведена", async () => {
  const store = kv();
  await saveAcl({ VISITS: store }, defaultAcl());
  assert.ok(store.box.has(ACL_KEY));
});

test("вход отмечается отдельным ключом, а не правкой документа прав", async () => {
  const env = { ACL: kv() };
  await touchUser(env, "New@KvantPro.com");
  const seen = await loadSeen(env);
  const u = seen["new@kvantpro.com"];
  assert.ok(u, "вошедший не попал в учёт");
  assert.equal(u.seen, 1);
  assert.ok(u.first && u.last);
  assert.deepEqual((await loadAcl(env)).users, {}, "документ прав учётом входов не трогается");

  await touchUser(env, "");                       // пустая почта ничего не заводит
  assert.equal(Object.keys(await loadSeen(env)).length, 1);
});

test("повторный вход в течение десяти минут не переписывает хранилище", async () => {
  const store = kv();
  const env = { ACL: store };
  await touchUser(env, "a@kvantpro.com");
  const first = store.box.get("seen:a@kvantpro.com");
  await touchUser(env, "a@kvantpro.com");
  assert.equal(store.box.get("seen:a@kvantpro.com"), first, "запись на каждый запрос — лишняя нагрузка");

  // спустя тишину счётчик растёт, дата первого входа сохраняется
  const old = JSON.parse(first);
  store.box.set("seen:a@kvantpro.com", JSON.stringify({ ...old, last: new Date(Date.now() - 20 * 60 * 1000).toISOString() }));
  await touchUser(env, "a@kvantpro.com");
  const now = JSON.parse(store.box.get("seen:a@kvantpro.com"));
  assert.equal(now.seen, 2);
  assert.equal(now.first, old.first);
});

test("без хранилища учёт отвечает «неизвестно», а не «никто не заходил»", async () => {
  await touchUser({}, "a@kvantpro.com");          // не должно бросать
  assert.equal(await loadSeen({}), null, "пустой список и отсутствие хранилища нельзя путать");
  assert.deepEqual(await loadSeen({ ACL: kv() }), {}, "с хранилищем пустой список — это факт");
});

// ── резка дашборда ───────────────────────────────────────────────────────────
// Берём боевой шаблон и подставляем вместо данных метки: если метка осталась
// в ответе — данные вкладки утекли в исходный код страницы.
function page() {
  let html = fs.readFileSync(path.join(ROOT, "templates/dashboard_core.html"), "utf8");
  for (const p of PAYLOADS) html = html.replace(`__${p}_JSON__`, `["MOCK-${p}"]`);
  return html;
}

test("шаблон и модуль согласованы: все вкладки и все массивы данных на месте", () => {
  const html = page();
  for (const t of TAB_IDS) assert.match(html, new RegExp(`data-tab="${t}"`), `в шаблоне нет вкладки ${t}`);
  for (const p of PAYLOADS) assert.match(html, new RegExp(`window\\.__${p}__ *=`), `в шаблоне нет массива ${p}`);
  const inTemplate = [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(inTemplate)].sort(), TAB_IDS.slice().sort(), "набор вкладок разошёлся с шаблоном");
});

test("одна вкладка: чужие кнопки, панели и данные вырезаны", () => {
  const out = cutDashboard(page(), ["kam"]);
  assert.match(out, /data-tab="kam"/);
  for (const t of TAB_IDS) if (t !== "kam") assert.doesNotMatch(out, new RegExp(`data-tab="${t}"`), `осталась кнопка ${t}`);
  assert.match(out, /window\.__KAM__ = \["MOCK-KAM"\]/, "данные своей вкладки должны остаться");
  for (const p of PAYLOADS) if (p !== "KAM") assert.ok(!out.includes(`MOCK-${p}`), `данные ${p} утекли в страницу`);
  for (const p of PAYLOADS) if (p !== "KAM") assert.match(out, new RegExp(`window\\.__${p}__ = __kvNoData\\(\\);`), `${p} не заменён пустышкой`);
  assert.ok(out.includes("function __kvNoData()"), "пустышка не подставлена");
  assert.doesNotMatch(out, /<div id="tab-company" hidden><\/div>/, "осталась панель чужой вкладки");
  assert.match(out, /<div id="tab-sourcing" hidden>/, "панель «Сорсинга» не спрятана");
  assert.match(out, /\.tabs \.tab'\);if\(b\)b\.click\(\)/, "нет открытия первой доступной вкладки");
});

test("полные права: страница не тронута", () => {
  const html = page();
  const out = cutDashboard(html, TAB_IDS);
  assert.equal(out, html, "при полных правах резка не должна ничего менять");
  for (const p of PAYLOADS) assert.ok(out.includes(`MOCK-${p}`));
});

test("пустые права: данных не остаётся вовсе", () => {
  const out = cutDashboard(page(), []);
  for (const p of PAYLOADS) assert.ok(!out.includes(`MOCK-${p}`), `данные ${p} утекли в страницу`);
  assert.doesNotMatch(out, /data-tab="/, "остались кнопки вкладок");
});

test("парные вкладки делят массив: «Поставщики» без «Реализации» сохраняют CONTRACTS", () => {
  const out = cutDashboard(page(), ["suppliers"]);
  assert.ok(out.includes("MOCK-CONTRACTS"), "«Поставщики» читают тот же массив, что и «Реализация»");
  assert.doesNotMatch(out, /data-tab="contracts"/);
  const c = cutDashboard(page(), ["cohorts"]);
  assert.ok(c.includes("MOCK-COMPANY"), "«Когорты» читают тот же массив, что и «Пульс компании»");
});

test("«Сорсинг» в правах — панель остаётся видимой", () => {
  const out = cutDashboard(page(), ["sourcing"]);
  assert.match(out, /<div id="tab-sourcing">/);
  assert.ok(out.includes("MOCK-DATA") && out.includes("MOCK-INSIGHTS"));
  assert.ok(!out.includes("MOCK-KAM"));
});

test("копия блока прав в гейте портала совпадает с каноническим access/acl.js", () => {
  const block = (s) => { const m = s.match(/\/\/ BEGIN aclCore[\s\S]*?\/\/ END aclCore/); return m ? m[0] : null; };
  const want = block(fs.readFileSync(path.join(ROOT, "access/acl.js"), "utf8"));
  assert.ok(want, "в access/acl.js нет маркеров");
  const gate = fs.readFileSync(path.join(ROOT, "public/_worker.js"), "utf8");
  assert.equal(block(gate), want, "public/_worker.js: блок aclCore разошёлся с access/acl.js");
});

test("разделение «Базы ЗИП» на ГШО и ГТУ не отнимает уже выданный доступ", () => {
  // документ версии 1: плитка была одна, идентификатор zip
  const a = normalizeAcl({
    version: 1, defaultRole: "employee",
    roles: { engineer: { name: "Инженер", sites: ["zip", "gpu"], tabs: [] },
             guest: { name: "Гость", sites: [], tabs: [] } },
    users: { "e@kvantpro.com": { role: "engineer", sites: ["zip"], tabs: [] },
             "g@kvantpro.com": { role: "guest", sites: ["gpu"], tabs: [] } },
  });
  assert.equal(a.version, 2);
  assert.deepEqual(a.roles.engineer.sites, ["zip", "gt", "gpu"], "роль с ЗИП должна получить ГТУ");
  assert.deepEqual(a.roles.guest.sites, [], "пустой роли ничего не добавляем");
  assert.deepEqual(a.users["e@kvantpro.com"].sites, ["zip", "gt"]);
  assert.deepEqual(a.users["g@kvantpro.com"].sites, ["gpu"], "без ЗИП добавки нет");

  // документ уже второй версии: владелец мог снять ГТУ сознательно — не возвращаем
  const b = normalizeAcl({
    version: 2, defaultRole: "employee",
    roles: { engineer: { name: "Инженер", sites: ["zip"], tabs: [] } }, users: {},
  });
  assert.deepEqual(b.roles.engineer.sites, ["zip"]);
});

test("порядок сайтов в правах всегда как в справочнике", () => {
  const a = normalizeAcl({ version: 2, roles: { r: { name: "Р", sites: ["gok", "dashboard", "gt"], tabs: [] } }, users: {} });
  assert.deepEqual(a.roles.r.sites, ["dashboard", "gt", "gok"]);
});

test("новая библиотека не расширяет сохранённые роли и точечные права", () => {
  const acl = normalizeAcl({ version: 2, defaultRole: "employee", roles: {
    employee: { name: "Сотрудник", sites: ["dashboard", "zip", "gt", "gpu", "ove", "gidromet", "gok"], tabs: [] },
    custom: { name: "Своя роль", sites: ["gt"], tabs: [] },
  }, users: { "reader@kvantpro.com": { role: "custom", sites: ["zip"], tabs: [] } } });
  assert.ok(!rightsFor(acl, "new@kvantpro.com", {}).sites.includes("knowledge"));
  assert.deepEqual(rightsFor(acl, "reader@kvantpro.com", {}).sites, ["zip", "gt"]);
  assert.ok(rightsFor(acl, "stepan@kvantpro.com", {}).sites.includes("knowledge"));
});

test("строгое чтение прав отказывает при отсутствии, ошибке и порче хранилища", async () => {
  await assert.rejects(loadAcl({}, { strict: true }));
  const failing = { ACL: { get: async () => { throw new Error("offline"); } } };
  await assert.rejects(loadAcl(failing, { strict: true }));
  assert.deepEqual(await loadAcl(failing), defaultAcl(), "поведение прежних маршрутов не меняется");
  const env = { ACL: kv() };
  await env.ACL.put(ACL_KEY, "{}");
  await assert.rejects(loadAcl(env, { strict: true }));
});
