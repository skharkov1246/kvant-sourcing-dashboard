// Общие шаги обработчика: обращение к журналу (DO Ledger), исполнение действия агента, завершение
// звонка (комментарии в Битрикс, запись для оценщика), набор очередной порции звонков.

import { applyEffects } from "./bitrix.mjs";
import { sweep } from "./ledger.mjs";
import { jobId, localParts, pickCalls } from "./plan.mjs";
import { evalRecord, recordKey } from "./record.mjs";
import { elevenOutbound, e164, telnyxDial } from "./telephony.mjs";
import { finalComment, outcomeOf } from "./tools.mjs";

export const QUEUE_KEY = "callq:v1";

export async function ledgerCall(env, msg) {
  const stub = env.LEDGER.get(env.LEDGER.idFromName("main"));
  const res = await stub.fetch("https://ledger/op", { method: "POST", body: JSON.stringify(msg) });
  if (!res.ok) throw new Error(`ledger ${msg.op}: ${res.status}`);
  return res.json();
}

// Действие агента: отметка в журнале + записи в Битрикс. Ошибка Битрикса звонок не рвёт.
export async function handleTool(env, job, name, args, r, deps = {}) {
  if (r.error) return { ok: false };
  await ledgerCall(env, { op: "tool", job_id: job.job_id, name, args, state: r.state });
  const res = await (deps.applyEffects || applyEffects)(env.BITRIX_WRITE_WEBHOOK_URL, r.effects);
  return { ok: true, ...res };
}

// Завершение звонка: исход по действиям агента, комментарий в каждую карточку, запись разговора.
export async function finalize(env, jobIdValue, res, deps = {}) {
  const now = deps.now || new Date();
  const active = await ledgerCall(env, { op: "job", job_id: jobIdValue });
  if (!active) return null; // уже завершён (повтор вебхука)
  const tools = res.tools && res.tools.length ? res.tools : active.tools || [];
  const outcome = outcomeOf(tools, res.turns);
  const local = localParts(active.tz, now);
  const job = await ledgerCall(env, { op: "finish", job_id: jobIdValue, local_day: local.day, now: now.toISOString() });
  if (!job) return null;
  const when = `${local.day} ${String(Math.floor(local.minutes / 60)).padStart(2, "0")}:${String(local.minutes % 60).padStart(2, "0")} по местному`;
  const attempt = Math.max(...(job.requests || []).map((r) => (r.attempts || 0) + 1), 1);
  const text = finalComment(job, outcome, { when, attempt, duration_s: res.duration_s, summary: res.summary });
  const effects = (job.requests || []).map((r) => ({ type: "comment", card: String(r.card), text }));
  const written = await (deps.applyEffects || applyEffects)(env.BITRIX_WRITE_WEBHOOK_URL, effects);
  const rec = evalRecord(job, res.turns, tools, { outcome, duration_s: Math.round(res.duration_s || 0) });
  if (env.KV) await env.KV.put(recordKey(job, now.toISOString().slice(0, 10)), JSON.stringify(rec), { expirationTtl: 90 * 86400 });
  return { outcome, written };
}

function settings(env) {
  return {
    arms: String(env.CALL_ARMS_ACTIVE || "gpt,eleven").split(",").map((s) => s.trim()).filter(Boolean),
    maxConcurrent: Math.min(5, Math.max(1, Number(env.MAX_CONCURRENT || 2))),
    dailyPerArm: Math.max(0, Number(env.DAILY_PER_ARM || 60)),
  };
}

// Порция набора (по расписанию воркера, только при CALLS_ENABLED=1).
export async function dispatchNow(env, deps = {}) {
  const now = deps.now || new Date();
  const queue = deps.queue || (await env.KV.get(QUEUE_KEY, "json"));
  if (!queue || !Array.isArray(queue.calls)) return { placed: 0, counters: { "очереди нет": 1 } };
  let state = await ledgerCall(env, { op: "state" });
  // звонки без завершения (потерян вебхук) — снять через 30 минут, иначе займут линии навсегда
  const stale = sweep({ ...state, active: state.active || {} }, now);
  for (const id of stale) await finalize(env, id, { turns: [], tools: [], duration_s: 0 }, deps);
  if (stale.length) state = await ledgerCall(env, { op: "state" });
  const { picked, counters } = pickCalls(queue, state, now, settings(env));
  if (stale.length) counters["снято зависших"] = stale.length;
  let placed = 0;
  for (const call of picked) {
    const job = {
      job_id: jobId(call, now),
      company_id: call.company_id,
      arm: call.arm,
      phone: e164(call.phone),
      from_pool: call.from_pool,
      tz: call.tz,
      country: call.country,
      requests: call.requests.map((r) => ({ ...r, attempts: ((state.cards || {})[r.card] || {}).attempts || 0 })),
      prompt: call.prompt,
      voicemail: call.voicemail,
      agent_vars: call.agent_vars,
      our_company: env.CALL_OUR_COMPANY || "",
      agent_name: env.CALL_AGENT_NAME || "",
    };
    if (!job.phone) { counters["плохой номер"] = (counters["плохой номер"] || 0) + 1; continue; }
    const r = await ledgerCall(env, { op: "reserve", job, now: now.toISOString() });
    if (!r.ok) continue;
    try {
      if (job.arm === "gpt") {
        await (deps.telnyxDial || telnyxDial)(env, job);
      } else if (job.arm === "eleven") {
        const out = await (deps.elevenOutbound || elevenOutbound)(env, job);
        await ledgerCall(env, { op: "map_conv", job_id: job.job_id, conversation_id: out.conversation_id });
      }
      placed++;
    } catch (err) {
      // набор не состоялся — снимаем звонок; попытка засчитывается, чтобы плохой номер не крутился
      await finalize(env, job.job_id, { turns: [], tools: [], duration_s: 0 }, deps);
      counters["сбой набора"] = (counters["сбой набора"] || 0) + 1;
    }
  }
  return { placed, counters };
}
