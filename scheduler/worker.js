// Планировщик дашборда КВАНТ — отдельный Cloudflare Cron Worker (НЕ Pages).
// Делает две вещи по расписанию:
//   1) каждые 2 часа — триггерит пересборку дашборда (GitHub repository_dispatch),
//      чтобы данные обновлялись САМИ, даже когда никто не заходит на сайт;
//   2) раз в день (00:05 МСК) — шлёт на почту отчёт: сколько уникальных IP заходило за прошедший день.
//
// Визиты пишет Pages-воркер (public/_worker.js) в KV namespace VISITS: ключ v:{дата}:{ip} = число заходов.
//
// Биндинги/секреты (см. АВТООБНОВЛЕНИЕ.md):
//   KV     VISITS            — ТОТ ЖЕ namespace, что привязан к Pages-проекту
//   secret GH_DISPATCH_TOKEN — GitHub PAT этого репо (Contents: read/write) для пересборки
//   secret RESEND_API_KEY    — ключ Resend для отправки письма
//   var    REPORT_TO         — кому слать (по умолчанию skharkov@gmail.com)
//   var    REPORT_FROM       — от кого (адрес на верифицированном в Resend домене)
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const REPORT_CRON = "5 21 * * *"; // 00:05 МСК — отчёт за прошедший день (всё остальное — пересборка)

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === REPORT_CRON) ctx.waitUntil(sendDailyReport(env));
    else ctx.waitUntil(triggerRebuild(env));
  },
};

// пересборка дашборда через GitHub repository_dispatch
async function triggerRebuild(env) {
  if (!env.GH_DISPATCH_TOKEN) return;
  await fetch(`https://api.github.com/repos/${GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "kvant-dashboard-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "rebuild" }),
  });
}

const mskDay = (back) =>
  new Date(Date.now() + 3 * 3600 * 1000 - back * 24 * 3600 * 1000).toISOString().slice(0, 10); // МСК = UTC+3
const fmtRu = (iso) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ежедневный отчёт: уникальные IP за вчерашний МСК-день
async function sendDailyReport(env) {
  if (!env.VISITS || !env.RESEND_API_KEY) return;
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
  <p style="margin:14px 0 0;color:#999;font-size:12px">Внутренний дашборд КВАНТ · отчёт сформирован планировщиком Cloudflare. Уникальные IP — по заходам на страницу (поллинг свежести не учитывается).</p>
</div>`;

  await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { "Authorization": `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: env.REPORT_FROM || "Дашборд КВАНТ <onboarding@resend.dev>",
      to: [env.REPORT_TO || "skharkov@gmail.com"],
      subject: `КВАНТ · дашборд: ${distinct} уникальных IP за ${fmtRu(day)}`,
      html,
    }),
  });
}
