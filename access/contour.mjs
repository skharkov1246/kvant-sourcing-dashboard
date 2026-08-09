// Подпорки тестового контура: KV на файле и ASSETS на локальной папке.
// Никаких зависимостей — только стандартный Node 18+ (WebCrypto, fetch, Request/Response).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const DIR = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(DIR, "..");

// ---------- KV ----------
// Файловый (для devserver, чтобы состояние переживало перезапуск) или в памяти (для тестов).
export function makeKV(file = null) {
  let db = {};
  if (file && fs.existsSync(file)) {
    try { db = JSON.parse(fs.readFileSync(file, "utf8")); } catch { db = {}; }
  }
  const save = () => { if (file) fs.writeFileSync(file, JSON.stringify(db, null, 1)); };
  const alive = (k) => {
    const rec = db[k];
    if (!rec) return null;
    if (rec.exp && rec.exp < Date.now()) { delete db[k]; return null; }
    return rec;
  };
  return {
    async get(k) { const r = alive(k); return r ? r.v : null; },
    async put(k, v, opts = {}) {
      db[k] = { v: String(v), exp: opts.expirationTtl ? Date.now() + opts.expirationTtl * 1000 : null };
      save();
    },
    async delete(k) { delete db[k]; save(); },
    async list({ prefix = "", cursor } = {}) {
      const keys = Object.keys(db).filter((k) => k.startsWith(prefix) && alive(k)).sort().map((name) => ({ name }));
      return { keys, list_complete: true, cursor: null };
    },
    _dump: () => db,
  };
}

// ---------- ASSETS ----------
// Отдаёт файлы из папки; «/» → index.html. Ровно то, что делает env.ASSETS в Pages.
export function makeAssets(dir) {
  return {
    async fetch(request) {
      const url = new URL(typeof request === "string" ? request : request.url);
      let rel = decodeURIComponent(url.pathname);
      if (rel.endsWith("/")) rel += "index.html";
      const file = path.join(dir, rel.replace(/^\/+/, ""));
      if (!file.startsWith(dir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        return new Response("Not found", { status: 404 });
      }
      const ext = path.extname(file).toLowerCase();
      const type = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
        ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml" }[ext] || "application/octet-stream";
      return new Response(fs.readFileSync(file), { status: 200, headers: { "Content-Type": type } });
    },
  };
}

// ---------- index.html для контура ----------
// Если есть настоящая сборка (public/index.html) — берём её: так резка ACL проверяется
// на реальном объёме данных. Иначе собираем шаблон с моковыми данными.
export function buildIndex(outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  const real = path.join(ROOT, "public", "index.html");
  const out = path.join(outDir, "index.html");
  if (fs.existsSync(real)) {
    fs.copyFileSync(real, out);
    return { out, source: "public/index.html" };
  }
  const tpl = fs.readFileSync(path.join(ROOT, "templates", "dashboard_core.html"), "utf8");
  const J = (o) => JSON.stringify(o).replace(/<\//g, "<\\/");
  const gen = new Date().toISOString();
  const html = tpl
    .replace("__TITLE__", "Анализ работы сорсеров · КВАНТ · тестовый контур")
    .replace("__DATA_JSON__", J(MOCK.data))
    .replace("__INSIGHTS_JSON__", J(MOCK.insights))
    .replace("__COMPANY_JSON__", J(MOCK.company))
    .replace("__KAM_JSON__", J(MOCK.dir))
    .replace("__ENG_JSON__", J(MOCK.dir))
    .replace("__PRODUCT_JSON__", J(MOCK.dir))
    .replace("__CONTRACTS_JSON__", J(MOCK.contracts))
    .replace("__REPS_JSON__", J(MOCK.reps))
    .replace("__ADVISOR_JSON__", J(MOCK.advisor))
    .replace("__GENERATED_AT__", gen)
    .replace("__NEXT_UPDATE__", gen);
  fs.writeFileSync(out, html);
  return { out, source: "моковая сборка из templates/dashboard_core.html" };
}

// Моки: не полные, но с маркерами MOCK-<TAB>, по которым тест проверяет,
// что данные чужой вкладки физически не попали в отданный HTML.
export const MOCK = {
  data: { marker: "MOCK-DATA", label: "тестовый контур", sourcers: [], rows: [], kpis: [] },
  insights: { marker: "MOCK-INSIGHTS", items: [] },
  company: { marker: "MOCK-COMPANY", label: "тест", kpis: [], cohorts: [], silent: [], ttrRFQ: null },
  dir: { marker: "MOCK-DIR", label: "тест", kpis: [], rows: [] },
  contracts: { marker: "MOCK-CONTRACTS", label: "тест", kpis: [], rows: [],
    suppliers: { marker: "MOCK-SUPPLIERS", label: "тест", kpis: [], rows: [] } },
  reps: { marker: "MOCK-REPS", label: "тест", kpis: [], rows: [] },
  advisor: { marker: "MOCK-ADVISOR", label: "тест", hygiene: [], silent: [] },
};

// ---------- окружение контура ----------
export function testEnv({ kvFile = null, assetsDir } = {}) {
  return {
    ACL: makeKV(kvFile),
    ASSETS: makeAssets(assetsDir),
    CONTOUR: "test",
    OWNER_EMAIL: "skharkov@gmail.com",
    SIGN_KEY: "contour-signing-key-not-for-prod",
    MAIL_PROVIDER: "log",
    TEST_OUTBOX_OPEN: "1",
    BASIC_AUTH_USER: "kvant",
    BASIC_AUTH_PASS: "contour",
  };
}
