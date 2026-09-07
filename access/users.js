// ИМЕННОЙ ВХОД ПО КОРПОРАТИВНОЙ ПОЧТЕ — канонический экземпляр помощника.
// Ровно этот же блок (между маркерами BEGIN/END) вшит в каждый гейт:
//   public/_worker.js · gpu/public/_worker.js · ove/public/_worker.js · zip/site/_worker.js.example
// Тест: node --test access/test/users.test.mjs. Сверка копий: node access/test/users.test.mjs --sync
//
// Секрет BASIC_AUTH_USERS — текст, по строке на сотрудника:
//     email  sha256hex  сайты
// где хэш = sha256("email:пароль") в hex, а сайты — через запятую (sourcing,zip,gpu,ove) или * для всех.
// Строки, начинающиеся с #, и пустые — пропускаются. Логин при входе — почта, регистр не важен.
//
// Общий пароль (BASIC_AUTH_USER / BASIC_AUTH_PASS) остаётся резервным входом владельца.
// Если почта найдена в таблице, а пароль не подошёл — отказ без перехода к резервному:
// иначе общий пароль работал бы под любой почтой.

// BEGIN userOk
async function userOk(request, env, site) {
  const c = parseBasic(request.headers.get("Authorization"));
  if (!c) return false;
  const login = c.user.trim().toLowerCase();
  const table = env.BASIC_AUTH_USERS || "";
  if (table) {
    for (const raw of table.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const [email, hash, ...rest] = line.split(/\s+/);
      const sites = rest.join("") || "*";
      if (!email || !hash || email.toLowerCase() !== login) continue;
      const got = await sha256Hex(`${login}:${c.pass}`);
      if (!safeEqual(got, hash.toLowerCase())) return false;
      return sites === "*" || sites.split(",").map((s) => s.trim()).includes(site);
    }
  }
  const user = env.BASIC_AUTH_USER || "kvant";
  const pass = env.BASIC_AUTH_PASS;
  return !!pass && safeEqual(c.user, user) && safeEqual(c.pass, pass);
}
async function sha256Hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
// END userOk

// Разбор заголовка Basic без btoa: пароль может содержать любые символы UTF-8.
function parseBasic(header) {
  if (!header || !header.startsWith("Basic ")) return null;
  try {
    const bin = atob(header.slice(6));
    const bytes = Uint8Array.from(bin, (ch) => ch.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    const i = text.indexOf(":");
    if (i < 0) return null;
    return { user: text.slice(0, i), pass: text.slice(i + 1) };
  } catch {
    return null;
  }
}
// сравнение за постоянное время
function safeEqual(a, b) {
  a = String(a); b = String(b);
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export { userOk, sha256Hex, parseBasic, safeEqual };
