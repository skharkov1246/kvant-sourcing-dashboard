// Обработчик звонилки — отдельный Cloudflare Worker, вне Cloudflare Access портала: вебхуки Telnyx,
// OpenAI и ElevenLabs приходят из интернета, поэтому каждый запрос проверяется подписью.
// Маршруты:
//   POST /telnyx            — события звонка группы gpt (ответ, автоответчик, конец)
//   POST /openai            — realtime.call.incoming: принять звонок, запустить CallSession
//   POST /eleven/tool/<имя> — действие агента ElevenLabs (заголовок X-Callbot-Secret)
//   POST /eleven/postcall   — итог разговора ElevenLabs
//   GET  /health            — жив ли воркер (без данных)
// По расписанию — порция набора, только при CALLS_ENABLED=1 (расписание включает владелец).
// В журнал воркера — только коды событий и счётчики: ни номеров, ни названий, ни расшифровок.

import { dispatchNow, finalize, handleTool, ledgerCall } from "./core.mjs";
import { fromElevenPostcall } from "./record.mjs";
import { acceptBody, amdDecision, numberFromSip, openaiCall, sipHeader, telnyxHangup, telnyxTransfer, unb64json } from "./telephony.mjs";
import { runTool } from "./tools.mjs";
import { verifyEleven, verifyOpenAI, verifyTelnyx } from "./verify.mjs";

export { Ledger } from "./ledger.mjs";
export { CallSession } from "./session.mjs";

const ok = (body = { ok: true }) => Response.json(body);
const deny = () => new Response("forbidden", { status: 403 });

async function onTelnyx(request, env, body) {
  const valid = await verifyTelnyx(env.TELNYX_PUBLIC_KEY, request.headers.get("telnyx-signature-ed25519"),
    request.headers.get("telnyx-timestamp"), body);
  if (!valid) return deny();
  const ev = JSON.parse(body).data || {};
  const p = ev.payload || {};
  const cs = unb64json(p.client_state) || {};
  if (!cs.job_id) return ok();
  const job = await ledgerCall(env, { op: "job", job_id: cs.job_id });
  if (!job) return ok();
  const decision = amdDecision(ev.event_type, p.result);
  if (decision === "human" || decision === "voicemail") {
    await ledgerCall(env, { op: "pending", from: p.from, job_id: job.job_id, mode: decision });
    await telnyxTransfer(env, p.call_control_id, job, decision);
  } else if (decision === "hangup") {
    await telnyxHangup(env, p.call_control_id);
  } else if (ev.event_type === "call.hangup" && !cs.mode && !job.transferred) {
    // разговор так и не перевели на OpenAI (не ответили, занято, автоответчик без сигнала)
    await finalize(env, job.job_id, { turns: [], tools: [], duration_s: 0 });
  }
  return ok();
}

async function onOpenAI(request, env, body) {
  if (!(await verifyOpenAI(env.OPENAI_WEBHOOK_SECRET, request.headers, body))) return deny();
  const ev = JSON.parse(body);
  if (ev.type !== "realtime.call.incoming") return ok();
  const callId = ev.data && ev.data.call_id;
  const hit = await ledgerCall(env, {
    op: "take_pending",
    jobHeader: sipHeader(ev, "X-Job-Id"),
    fromNumber: numberFromSip(sipHeader(ev, "From")),
  });
  if (!hit || !hit.job) {
    await openaiCall(env, callId, "reject", { status_code: 603 });
    return ok({ ok: false });
  }
  await openaiCall(env, callId, "accept", acceptBody(env, hit.job, hit.mode));
  const stub = env.CALL_SESSION.get(env.CALL_SESSION.idFromName(callId));
  await stub.fetch("https://session/start", { method: "POST", body: JSON.stringify({ call_id: callId, job: hit.job }) });
  return ok();
}

async function onElevenTool(request, env, body, name) {
  const secret = env.ELEVEN_TOOL_SECRET || "";
  if (!secret || request.headers.get("x-callbot-secret") !== secret) return deny();
  let args = {};
  try { args = JSON.parse(body || "{}"); } catch { args = {}; }
  const job = await ledgerCall(env, { op: "job", job_id: args.job_id, conversation_id: args.conversation_id });
  if (!job) return ok({ result: "The call record was not found. Continue the conversation and promise an email follow-up." });
  const r = runTool(name, args, job, { stageMoves: env.STAGE_MOVES === "1" });
  await handleTool(env, job, name, args, r);
  return ok({ result: r.say });
}

async function onElevenPostcall(request, env, body) {
  if (!(await verifyEleven(env.ELEVEN_WEBHOOK_SECRET, request.headers.get("elevenlabs-signature"), body))) return deny();
  const ev = JSON.parse(body);
  const data = ev.data || {};
  const jobIdValue = (data.conversation_initiation_client_data && data.conversation_initiation_client_data.dynamic_variables
    && data.conversation_initiation_client_data.dynamic_variables.job_id) || null;
  const job = await ledgerCall(env, { op: "job", job_id: jobIdValue, conversation_id: data.conversation_id });
  if (!job) return ok();
  if (ev.type === "post_call_transcription") {
    await finalize(env, job.job_id, fromElevenPostcall(data));
  } else if (ev.type === "call_initiation_failure") {
    await finalize(env, job.job_id, { turns: [], tools: [], duration_s: 0 });
  }
  return ok();
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return ok({ ok: true, enabled: env.CALLS_ENABLED === "1" });
    if (request.method !== "POST") return new Response("not found", { status: 404 });
    const body = await request.text();
    try {
      if (url.pathname === "/telnyx") return await onTelnyx(request, env, body);
      if (url.pathname === "/openai") return await onOpenAI(request, env, body);
      if (url.pathname === "/eleven/postcall") return await onElevenPostcall(request, env, body);
      const m = /^\/eleven\/tool\/([a-z_]+)$/.exec(url.pathname);
      if (m) return await onElevenTool(request, env, body, m[1]);
    } catch (err) {
      console.log(`сбой ${url.pathname}: ${String((err && err.name) || "Error")}`); // без данных звонка
      return new Response("error", { status: 500 });
    }
    return new Response("not found", { status: 404 });
  },

  async scheduled(event, env, ctx) {
    if (env.CALLS_ENABLED !== "1") return;
    ctx.waitUntil(
      dispatchNow(env).then((r) => console.log(`набор: ${r.placed}; ${JSON.stringify(r.counters)}`)),
    );
  },
};
