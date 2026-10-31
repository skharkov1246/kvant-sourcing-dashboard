// Кому звонить сейчас: окна местного времени, попытки, «не звонить», один звонок в день на компанию,
// дневной предел на группу и предел одновременных звонков. Чистые функции — без сети и хранилища.

// Окна звонка по местному времени поставщика (алгоритм звонилки, п. 5): будни 9:30–11:30 и 14:00–17:00.
export const WINDOWS = [
  [9 * 60 + 30, 11 * 60 + 30],
  [14 * 60, 17 * 60],
];
export const WORKDAYS = new Set(["Mon", "Tue", "Wed", "Thu", "Fri"]);
export const MAX_ATTEMPTS = 3; // как ПРАВИЛА["попыток_макс"] в scripts/call_queue.py

export function localParts(tz, now) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: tz || "UTC",
      weekday: "short",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .map((p) => [p.type, p.value]),
  );
  return {
    day: `${parts.year}-${parts.month}-${parts.day}`,
    weekday: parts.weekday,
    minutes: Number(parts.hour) * 60 + Number(parts.minute),
  };
}

export function inWindow(tz, now) {
  let p;
  try {
    p = localParts(tz, now);
  } catch {
    return false; // неизвестный пояс — не звоним
  }
  return WORKDAYS.has(p.weekday) && WINDOWS.some(([a, b]) => p.minutes >= a && p.minutes < b);
}

export function emptyState() {
  return { v: 1, companies: {}, cards: {}, active: {}, dials: {}, byConv: {}, pending: {}, paused: false };
}

// Звонки к набору сейчас. opts: {arms: ["gpt","eleven"], maxConcurrent, dailyPerArm}
export function pickCalls(queue, state, now, opts) {
  const st = { ...emptyState(), ...(state || {}) };
  const arms = new Set(opts.arms || []);
  const counters = {};
  const skip = (why) => (counters[why] = (counters[why] || 0) + 1);
  const today = now.toISOString().slice(0, 10);
  const dialsToday = { ...((st.dials || {})[today] || {}) };
  let free = Math.max(0, (opts.maxConcurrent || 2) - Object.keys(st.active || {}).length);
  const activeCompanies = new Set(Object.values(st.active || {}).map((a) => a.company_id));
  const byArm = {};
  if (st.paused) return { picked: [], counters: { "пауза": 1 } };
  for (const call of (queue && queue.calls) || []) {
    if (!arms.has(call.arm)) {
      skip(call.arm === "control" ? "контроль — не звоним" : "группа выключена");
      continue;
    }
    const co = (st.companies || {})[call.company_id] || {};
    if (co.dnc) { skip("компания: не звонить"); continue; }
    if (activeCompanies.has(call.company_id)) { skip("уже идёт звонок"); continue; }
    let local;
    try { local = localParts(call.tz, now); } catch { skip("неизвестный пояс"); continue; }
    if (co.last_day === local.day) { skip("сегодня уже звонили"); continue; }
    if (!call.prompt || !call.voicemail) { skip("нет текстов агента"); continue; }
    const requests = (call.requests || []).filter((r) => (((st.cards || {})[r.card] || {}).attempts || 0) < MAX_ATTEMPTS);
    if (!requests.length) { skip("попытки исчерпаны"); continue; }
    if (!inWindow(call.tz, now)) { skip("вне окна звонка"); continue; }
    (byArm[call.arm] = byArm[call.arm] || []).push({ ...call, requests });
  }
  // группы по очереди: ни одна не забирает все свободные линии
  const picked = [];
  const order = [...arms].filter((a) => byArm[a]);
  while (free > 0 && order.some((a) => byArm[a].length)) {
    for (const arm of order) {
      if (free <= 0) break;
      const next = byArm[arm].shift();
      if (!next) continue;
      if ((dialsToday[arm] || 0) >= (opts.dailyPerArm || 60)) { skip(`дневной предел ${arm}`); byArm[arm] = []; continue; }
      dialsToday[arm] = (dialsToday[arm] || 0) + 1;
      picked.push(next);
      free--;
    }
  }
  for (const arm of order) if (byArm[arm].length) counters["ждут свободной линии"] = (counters["ждут свободной линии"] || 0) + byArm[arm].length;
  return { picked, counters };
}

export function jobId(call, now) {
  return `${now.toISOString().slice(0, 10)}-${call.company_id}`.replace(/[^0-9A-Za-z:_-]/g, "_");
}
