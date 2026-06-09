// Планировщик дашборда КВАНТ — отдельный Cloudflare Cron Worker (НЕ Pages).
// По расписанию делает ТРИ вещи:
//   1) каждые 2 часа (:07)      — пересборка дашборда (GitHub repository_dispatch), даже без визитов;
//   2) каждые 2 часа (:37)      — проверка АЛЕРТОВ по свежим данным и письмо, если есть что сообщить;
//   3) раз в день (00:05 МСК)   — отчёт: сколько уникальных IP заходило за прошедший день.
//
// Визиты пишет Pages-воркер (public/_worker.js) в KV namespace VISITS (ключ v:{дата}:{ip}=число).
// Состояние алертов тоже в этом KV (ключи alert:*), чтобы не слать одно и то же дважды.
//
// Биндинги/секреты (см. АВТООБНОВЛЕНИЕ.md):
//   KV     VISITS            — тот же namespace, что у Pages-проекта
//   secret GH_DISPATCH_TOKEN — GitHub PAT этого репо (Contents: read/write) — пересборка
//   secret RESEND_API_KEY    — ключ Resend — отправка писем
//   secret BASIC_AUTH_PASS   — пароль дашборда (чтобы планировщик мог прочитать данные сайта для алертов)
//   var    BASIC_AUTH_USER   — логин дашборда (по умолчанию "kvant")
//   var    SITE_URL          — адрес дашборда (https://kvant-sourcing-f122.pages.dev/)
//   var    REPORT_TO         — кому слать (skharkov@gmail.com)
//   var    REPORT_FROM       — от кого (адрес на верифицированном в Resend домене)
//   var    ALERT_MIN_EUR     — порог «крупной» сделки для алерта о застрявших (по умолчанию 100000)
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const REPORT_CRON = "5 21 * * *";   // 00:05 МСК — дневной отчёт о посетителях
const ALERT_CRON = "37 */2 * * *";  // :37 чётных часов — проверка алертов (на ~30 мин позже пересборки)

const SCAN_CRON = "0 21 * * *";     // 00:00 МСК — суточный скан чатов сделок (тяжёлый, раз в сутки)

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === REPORT_CRON) ctx.waitUntil(sendDailyReport(env));
    else if (event.cron === ALERT_CRON) ctx.waitUntil(checkAlerts(env));
    else if (event.cron === SCAN_CRON) ctx.waitUntil(triggerDispatch(env, "scan"));
    else ctx.waitUntil(triggerDispatch(env, "rebuild"));
  },
};

// ---------- 1. триггер GitHub-воркфлоу (пересборка дашборда / суточный скан) ----------
async function triggerDispatch(env, eventType) {
  if (!env.GH_DISPATCH_TOKEN) return;
  await fetch(`https://api.github.com/repos/${GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "kvant-dashboard-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: eventType }),
  });
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const mskDay = (back) =>
  new Date(Date.now() + 3 * 3600 * 1000 - back * 24 * 3600 * 1000).toISOString().slice(0, 10);
const fmtRu = (iso) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };

async function sendEmail(env, subject, html) {
  if (!env.RESEND_API_KEY) return;
  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { "Authorization": `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: env.REPORT_FROM || "Дашборд КВАНТ <onboarding@resend.dev>",
      to: [env.REPORT_TO || "skharkov@gmail.com"],
      subject, html,
    }),
  });
}

// читает дашборд (за Basic Auth) и достаёт встроенный объект window.__NAME__
async function fetchData(env, name) {
  const url = env.SITE_URL || "https://kvant-sourcing-f122.pages.dev/";
  const auth = "Basic " + btoa(`${env.BASIC_AUTH_USER || "kvant"}:${env.BASIC_AUTH_PASS || ""}`);
  const r = await fetch(url, { headers: { "Authorization": auth, "X-Poll": "1" } });
  const t = await r.text();
  const m = t.match(new RegExp("window\\." + name + " = (.*?);\\n"));
  if (!m) return null;
  try { return JSON.parse(m[1].replace(/<\\\//g, "</")); } catch (e) { return null; }
}

// крупные застрявшие сделки у отслеживаемых коммерсантов (открытые, без движения >30 дн)
function repStuckBig(reps, minEur) {
  const out = [];
  for (const r of (reps && reps.reps) || [])
    for (const d of r.deals || [])
      if (d.stuck && d.cls === "early" && (d.raw || 0) >= minEur)
        out.push({ rep: r.label, id: String(d.id), t: d.t, amt: d.amt, raw: d.raw || 0 });
  return out;
}

// БРОШЕНО в чатах сделок: ждут ответа менеджера дольше суток (зашквар) — алертим только это
const NEGLECT_H = 24;
function repPending(reps) {
  const out = [];
  for (const r of (reps && reps.reps) || [])
    for (const w of (r.react && r.react.waiting) || [])
      if ((w.ageH || 0) >= NEGLECT_H)
        out.push({ rep: r.label, key: String(w.chat), deal: w.deal, t: w.t,
                   ageH: w.ageH, ageDays: w.ageDays, kind: w.kind });
  return out;
}

// ---------- 2. алерты ----------
async function checkAlerts(env) {
  if (!env.VISITS || !env.BASIC_AUTH_PASS) return;
  const contracts = await fetchData(env, "__CONTRACTS__");
  const company = await fetchData(env, "__COMPANY__");
  const reps = await fetchData(env, "__REPS__");
  if (!contracts && !company && !reps) return;

  const minEur = parseInt(env.ALERT_MIN_EUR || "100000", 10);
  const rows = (contracts && contracts.rows) || [];
  const deals = (company && company.deals) || [];
  const blocks = [];

  // первый запуск: засеять состояние и НЕ слать алерты на всё существующее
  const seeded = await env.VISITS.get("alert:init");
  const maxSeq = Math.max(0, ...rows.map((r) => r.seq || 0));
  const overdue = deals.filter((d) => d.ovd && d.cls === "early" && (d.raw || 0) >= minEur);

  const repStuck = repStuckBig(reps, minEur);
  const pending = repPending(reps);
  if (!seeded) {
    await env.VISITS.put("alert:maxseq", String(maxSeq));
    for (const d of overdue) await env.VISITS.put("alert:ovd:" + d.id, "1", { expirationTtl: 60 * 60 * 24 * 60 });
    for (const d of repStuck) await env.VISITS.put("alert:rs:" + d.id, "1", { expirationTtl: 60 * 60 * 24 * 60 });
    for (const p of pending) await env.VISITS.put("alert:pend:" + p.key, "1", { expirationTtl: 60 * 60 * 24 * 45 });
    await env.VISITS.put("alert:init", "1");
    await sendEmail(env, "КВАНТ · дашборд — алерты включены",
      `<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif"><p>Алерты подключены. Дальше буду писать только когда появится новое:</p>
       <ul><li>новые сделки, переведённые в реализацию (по № реализации);</li>
       <li>крупные (≥ ${minEur.toLocaleString("ru-RU")} €) сделки, застрявшие открытыми с прошедшим сроком;</li>
       <li>крупные застрявшие сделки у коммерсантов;</li>
       <li>@упоминания в чатах сделок, висящие без ответа дольше целевого срока.</li></ul>
       <p style="color:#888;font-size:12px">Текущее состояние принято за точку отсчёта (№ до ${maxSeq}, застрявших ${overdue.length}, у коммерсантов ${repStuck.length}, без ответа в чатах ${pending.length}) — повторных писем по ним не будет.</p></div>`);
    return;
  }

  // 2a. новые контракты в реализации (№ реализации больше прежнего максимума)
  const lastMax = parseInt((await env.VISITS.get("alert:maxseq")) || "0", 10);
  const fresh = rows.filter((r) => (r.seq || 0) > lastMax).sort((a, b) => (a.seq || 0) - (b.seq || 0));
  if (fresh.length) {
    blocks.push(`<h3 style="margin:14px 0 6px">🟢 Новые сделки в реализации (${fresh.length})</h3>
      <table style="border-collapse:collapse;font-size:13px">${fresh.map((r) =>
        `<tr><td style="padding:3px 12px 3px 0;font-family:monospace;color:#0a7d34">#${r.seq}</td>
             <td style="padding:3px 12px 3px 0">${esc(r.customer || "—")}</td>
             <td style="padding:3px 0;text-align:right">${esc(r.saleLbl || "")}</td></tr>`).join("")}</table>`);
    await env.VISITS.put("alert:maxseq", String(Math.max(lastMax, maxSeq)));
  }

  // 2b. крупные застрявшие (открытые, срок прошёл), о которых ещё не писали
  const newOvd = [];
  for (const d of overdue) {
    if (!(await env.VISITS.get("alert:ovd:" + d.id))) {
      newOvd.push(d);
      await env.VISITS.put("alert:ovd:" + d.id, "1", { expirationTtl: 60 * 60 * 24 * 60 });
    }
  }
  if (newOvd.length) {
    newOvd.sort((a, b) => (b.raw || 0) - (a.raw || 0));
    blocks.push(`<h3 style="margin:14px 0 6px">🔴 Крупные застрявшие сделки (${newOvd.length})</h3>
      <div style="color:#888;font-size:12px;margin-bottom:4px">открыты, плановый срок закрытия прошёл, сумма ≥ ${minEur.toLocaleString("ru-RU")} €</div>
      <table style="border-collapse:collapse;font-size:13px">${newOvd.map((d) =>
        `<tr><td style="padding:3px 12px 3px 0">${esc(d.t || "")}</td>
             <td style="padding:3px 12px 3px 0;color:#888">${esc(d.o || "")}</td>
             <td style="padding:3px 0;text-align:right;color:#b3261e">${esc(d.amt || "")}</td></tr>`).join("")}</table>`);
  }

  // 2c. крупные застрявшие у коммерсантов (открытые, без движения >30 дн), о которых ещё не писали
  const newRs = [];
  for (const d of repStuck) {
    if (!(await env.VISITS.get("alert:rs:" + d.id))) {
      newRs.push(d);
      await env.VISITS.put("alert:rs:" + d.id, "1", { expirationTtl: 60 * 60 * 24 * 60 });
    }
  }
  if (newRs.length) {
    newRs.sort((a, b) => b.raw - a.raw);
    blocks.push(`<h3 style="margin:14px 0 6px">🟠 Застряли у коммерсантов (${newRs.length})</h3>
      <div style="color:#888;font-size:12px;margin-bottom:4px">открытые сделки менеджеров без движения по стадиям &gt;30 дн, сумма ≥ ${minEur.toLocaleString("ru-RU")} €</div>
      <table style="border-collapse:collapse;font-size:13px">${newRs.map((d) =>
        `<tr><td style="padding:3px 12px 3px 0;font-weight:600">${esc(d.rep)}</td>
             <td style="padding:3px 12px 3px 0">${esc(d.t || "")}</td>
             <td style="padding:3px 0;text-align:right;color:#b3261e">${esc(d.amt || "")}</td></tr>`).join("")}</table>`);
  }

  // 2d. новые «висящие» @упоминания в чатах сделок (без ответа дольше SLA)
  const newPend = [];
  for (const p of pending) {
    if (!(await env.VISITS.get("alert:pend:" + p.key))) {
      newPend.push(p);
      await env.VISITS.put("alert:pend:" + p.key, "1", { expirationTtl: 60 * 60 * 24 * 45 });
    }
  }
  if (newPend.length) {
    newPend.sort((a, b) => (b.ageH || 0) - (a.ageH || 0));
    blocks.push(`<h3 style="margin:14px 0 6px">🔴 Брошены в чатах сделок (${newPend.length})</h3>
      <div style="color:#888;font-size:12px;margin-bottom:4px">ждут ответа менеджера дольше суток (запрос без ответа или его сделка с чужим последним сообщением)</div>
      <table style="border-collapse:collapse;font-size:13px">${newPend.map((p) =>
        `<tr><td style="padding:3px 12px 3px 0;font-weight:600">${esc(p.rep)}</td>
             <td style="padding:3px 12px 3px 0">${esc(p.t || ("Сделка #" + p.deal))}</td>
             <td style="padding:3px 12px 3px 0;color:#888">${esc(p.kind || "")}</td>
             <td style="padding:3px 0;text-align:right;color:#b3261e">${esc(p.ageDays)} дн</td></tr>`).join("")}</table>`);
  }

  if (!blocks.length) return; // ничего нового — молчим
  const n1 = fresh.length, n2 = newOvd.length, n3 = newRs.length, n4 = newPend.length;
  const subj = [n1 ? `${n1} в реализацию` : "", n2 ? `${n2} застрявших` : "",
                n3 ? `${n3} у коммерсантов` : "", n4 ? `${n4} без ответа` : ""].filter(Boolean).join(" · ");
  await sendEmail(env, `КВАНТ · дашборд: ${subj}`,
    `<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a">${blocks.join("")}
     <p style="margin:16px 0 0;color:#999;font-size:12px">Алерт сформирован планировщиком по свежим данным дашборда. Подробности — на дашборде.</p></div>`);
}

// ---------- 3. дневной отчёт о посетителях ----------
async function sendDailyReport(env) {
  if (!env.VISITS) return;
  const day = mskDay(1);
  const prefix = `v:${day}:`;
  const rows = [];
  let cursor;
  do {
    const res = await env.VISITS.list({ prefix, cursor, limit: 1000 });
    for (const k of res.keys) {
      const n = parseInt((await env.VISITS.get(k.name)) || "0", 10);
      rows.push({ ip: k.name.slice(prefix.length), n });
    }
    cursor = res.list_complete ? null : res.cursor;
  } while (cursor);
  rows.sort((a, b) => b.n - a.n);
  const distinct = rows.length;
  const hits = rows.reduce((s, r) => s + r.n, 0);
  const list = rows.length
    ? rows.map((r) => `<tr><td style="padding:3px 14px 3px 0;font-family:monospace">${esc(r.ip)}</td><td style="padding:3px 0;text-align:right;color:#888">${r.n}</td></tr>`).join("")
    : `<tr><td colspan="2" style="color:#888;padding:6px 0">заходов не было</td></tr>`;
  const html = `<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a">
  <h2 style="margin:0 0 4px">Дашборд КВАНТ — посетители за ${fmtRu(day)}</h2>
  <p style="margin:0 0 14px;color:#444">Уникальных IP: <b style="font-size:18px">${distinct}</b> · всего заходов: <b>${hits}</b></p>
  <table style="border-collapse:collapse;font-size:13px"><thead><tr>
    <th style="text-align:left;border-bottom:1px solid #ddd;padding:0 14px 4px 0">IP-адрес</th>
    <th style="text-align:right;border-bottom:1px solid #ddd;padding:0 0 4px">заходов</th>
  </tr></thead><tbody>${list}</tbody></table>
  <p style="margin:14px 0 0;color:#999;font-size:12px">Внутренний дашборд КВАНТ · уникальные IP по заходам на страницу (поллинг свежести не учитывается).</p>
</div>`;
  await sendEmail(env, `КВАНТ · дашборд: ${distinct} уникальных IP за ${fmtRu(day)}`, html);
}
