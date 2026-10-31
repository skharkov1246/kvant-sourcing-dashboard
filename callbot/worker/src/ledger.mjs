// Журнал звонилки — один Durable Object на весь сервис: состояние меняется атомарно, без гонок
// между вебхуками (KV «последняя запись побеждает» потерял бы попытки и «не звонить»).
// Чистая часть (apply*) тестируется без Cloudflare; класс Ledger — обёртка над хранилищем DO.

import { emptyState, MAX_ATTEMPTS } from "./plan.mjs";

export function reserve(st, job, now) {
  if (st.active[job.job_id]) return false;
  if (Object.values(st.active).some((a) => a.company_id === job.company_id)) return false;
  const day = now.toISOString().slice(0, 10);
  st.active[job.job_id] = { ...job, started: now.toISOString(), tools: [] };
  st.dials[day] = st.dials[day] || {};
  st.dials[day][job.arm] = (st.dials[day][job.arm] || 0) + 1;
  // старые дни счётчиков не копим
  for (const d of Object.keys(st.dials)) if (d < day) delete st.dials[d];
  return true;
}

// Перевод звонка Telnyx на OpenAI: какой job ждёт вызова с этого нашего номера.
export function setPending(st, fromNumber, jobId, mode, now) {
  st.pending[fromNumber] = { job_id: jobId, mode, at: now.getTime() };
}

export function takePending(st, { jobHeader, fromNumber }, now, maxAgeMs = 90_000) {
  if (jobHeader && st.active[jobHeader]) {
    let mode = "human";
    for (const [k, v] of Object.entries(st.pending)) {
      if (v.job_id === jobHeader) {
        mode = v.mode || mode;
        delete st.pending[k];
      }
    }
    return { job: st.active[jobHeader], mode };
  }
  const p = st.pending[fromNumber];
  if (!p || now.getTime() - p.at > maxAgeMs || !st.active[p.job_id]) return null;
  delete st.pending[fromNumber];
  return { job: st.active[p.job_id], mode: p.mode };
}

export function applyToolState(st, job, changes) {
  for (const c of changes || []) {
    if (c.type === "promise") st.cards[c.card] = { ...(st.cards[c.card] || {}), promise: c.date };
    if (c.type === "declined") st.cards[c.card] = { ...(st.cards[c.card] || {}), declined: true };
    if (c.type === "resend") st.cards[c.card] = { ...(st.cards[c.card] || {}), resend: true };
    if (c.type === "dnc") st.companies[c.company_id] = { ...(st.companies[c.company_id] || {}), dnc: true };
  }
}

// Завершение: попытка +1 каждой карточке звонка, «сегодня звонили» компании, звонок снят с активных.
export function finish(st, jobId, localDay) {
  const job = st.active[jobId];
  if (!job) return null;
  for (const r of job.requests || []) {
    const c = (st.cards[r.card] = st.cards[r.card] || {});
    c.attempts = Math.min(MAX_ATTEMPTS, (c.attempts || 0) + 1);
  }
  st.companies[job.company_id] = { ...(st.companies[job.company_id] || {}), last_day: localDay };
  delete st.active[jobId];
  for (const [k, v] of Object.entries(st.byConv)) if (v === jobId) delete st.byConv[k];
  return job;
}

// Звонок, по которому так и не пришло завершение (оборвался вебхук), снимается через 30 минут.
export function sweep(st, now, maxAgeMs = 30 * 60_000) {
  const stale = [];
  for (const [id, a] of Object.entries(st.active)) if (now.getTime() - Date.parse(a.started) > maxAgeMs) stale.push(id);
  return stale;
}

export class Ledger {
  constructor(state, env) {
    this.storage = state.storage;
    this.env = env;
  }

  async load() {
    return { ...emptyState(), ...((await this.storage.get("s")) || {}) };
  }

  async save(st) {
    await this.storage.put("s", st);
  }

  // Одна точка входа: {op, ...}. DO исполняет запросы по одному — изменения не перемешиваются.
  async fetch(request) {
    const msg = await request.json();
    const now = new Date(msg.now || Date.now());
    const st = await this.load();
    let out = null;
    switch (msg.op) {
      case "state":
        out = st;
        break;
      case "reserve":
        out = { ok: reserve(st, msg.job, now) };
        break;
      case "pending":
        setPending(st, msg.from, msg.job_id, msg.mode, now);
        if (st.active[msg.job_id]) st.active[msg.job_id].transferred = true; // конец звонка отдаёт CallSession
        out = { ok: true };
        break;
      case "take_pending":
        out = takePending(st, msg, now);
        break;
      case "map_conv":
        st.byConv[msg.conversation_id] = msg.job_id;
        if (st.active[msg.job_id]) st.active[msg.job_id].conversation_id = msg.conversation_id;
        out = { ok: true };
        break;
      case "job":
        out = st.active[msg.job_id || st.byConv[msg.conversation_id]] || null;
        break;
      case "tool": {
        const job = st.active[msg.job_id];
        if (job) {
          job.tools.push({ name: msg.name, args: msg.args });
          applyToolState(st, job, msg.state);
        }
        out = { ok: Boolean(job) };
        break;
      }
      case "finish":
        out = finish(st, msg.job_id, msg.local_day);
        break;
      case "pause":
        st.paused = Boolean(msg.paused);
        out = { paused: st.paused };
        break;
      default:
        return new Response("bad op", { status: 400 });
    }
    await this.save(st);
    return Response.json(out);
  }
}
