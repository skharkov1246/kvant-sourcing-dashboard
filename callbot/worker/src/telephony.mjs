// Набор и соединение. Разбор API 31.10.2026:
//  группа gpt — OpenAI Realtime по SIP принимает только входящие звонки. Поэтому Telnyx набирает
//    поставщика (POST /v2/calls, автоответчик — premium AMD), а когда ответил человек (или после
//    сигнала автоответчика), переводит звонок на sip:<project>@sip.api.openai.com (transfer, TLS + SRTP).
//    OpenAI шлёт realtime.call.incoming → accept с настройками сессии → Durable Object CallSession
//    держит WebSocket звонка (инструменты, расшифровка). Схему «набор Telnyx → перевод на OpenAI»
//    в документации целиком не описывают: проверить на одном тестовом звонке до пилота.
//  группа eleven — ElevenLabs звонит сам через SIP-транк Telnyx: POST /v1/convai/sip-trunk/outbound-call.

import tools from "../../agent/tools.json" with { type: "json" };

export function e164(phone) {
  const s = String(phone || "").replace(/[^\d+]/g, "");
  return /^\+\d{8,15}$/.test(s) ? s : "";
}

// Номера-отправители по пулам: переменная CALL_NUMBERS — JSON {"HK": "+852…", "SG": "+65…", "GB": "+44…"}.
export function fromNumber(env, pool) {
  let map = {};
  try { map = JSON.parse(env.CALL_NUMBERS || "{}"); } catch { map = {}; }
  return e164(map[pool]) || e164(map.GB) || "";
}

export function b64json(obj) {
  return btoa(unescape(encodeURIComponent(JSON.stringify(obj))));
}

export function unb64json(s) {
  try { return JSON.parse(decodeURIComponent(escape(atob(String(s || ""))))); } catch { return null; }
}

// ── Telnyx ────────────────────────────────────────────────────────────────────

export function telnyxDialBody(env, job) {
  return {
    connection_id: env.TELNYX_CONNECTION_ID,
    to: job.phone,
    from: fromNumber(env, job.from_pool),
    answering_machine_detection: "premium",
    webhook_url: `${String(env.PUBLIC_URL || "").replace(/\/+$/, "")}/telnyx`,
    client_state: b64json({ job_id: job.job_id }),
    timeout_secs: 40,
  };
}

export function telnyxTransferBody(env, job, mode) {
  return {
    to: `sip:${env.OPENAI_PROJECT_ID}@sip.api.openai.com;transport=tls`,
    sip_transport_protocol: "TLS",
    media_encryption: "SRTP",
    custom_headers: [
      { name: "X-Job-Id", value: job.job_id },
      { name: "X-Mode", value: mode },
    ],
    client_state: b64json({ job_id: job.job_id, mode }),
  };
}

async function telnyx(env, path, body, fetchFn = fetch) {
  const res = await fetchFn(`https://api.telnyx.com/v2${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.TELNYX_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`telnyx ${path}: ${res.status}`);
  return data.data || data;
}

export const telnyxDial = (env, job, f) => telnyx(env, "/calls", telnyxDialBody(env, job), f);
export const telnyxTransfer = (env, ccid, job, mode, f) => telnyx(env, `/calls/${encodeURIComponent(ccid)}/actions/transfer`, telnyxTransferBody(env, job, mode), f);
export const telnyxHangup = (env, ccid, f) => telnyx(env, `/calls/${encodeURIComponent(ccid)}/actions/hangup`, {}, f);

// Итог автоответчика premium AMD: человек — переводим сразу; автоответчик — ждём сигнала и переводим
// в режиме voicemail; тишина, факс, «не уверен» — кладём трубку (попытка засчитана).
export function amdDecision(eventType, result) {
  const r = String(result || "");
  if (eventType === "call.machine.premium.detection.ended") {
    if (r.startsWith("human")) return "human";
    if (r === "machine") return "wait_beep";
    return "hangup";
  }
  if (eventType === "call.machine.premium.greeting.ended") {
    return r === "beep_detected" || r === "no_beep_detected" ? "voicemail" : "hangup";
  }
  return "none";
}

// ── OpenAI Realtime ───────────────────────────────────────────────────────────

export function realtimeTools() {
  return tools.tools.map((t) => ({ type: "function", name: t.name, description: t.description, parameters: t.parameters }));
}

export function acceptBody(env, job, mode) {
  const instructions =
    mode === "voicemail"
      ? `You reached a voicemail. Say exactly the following message, slowly and clearly, then call the remind_email tool and stop speaking.\n\n${job.voicemail}`
      : job.prompt;
  return {
    type: "realtime",
    model: env.OPENAI_MODEL || "gpt-realtime-2",
    instructions,
    audio: {
      input: {
        turn_detection: { type: "semantic_vad" },
        transcription: { model: env.OPENAI_TRANSCRIBE_MODEL || "gpt-4o-transcribe" },
      },
      output: { voice: env.OPENAI_VOICE || "marin" },
    },
    tools: realtimeTools(),
    tool_choice: "auto",
  };
}

export async function openaiCall(env, callId, action, body, fetchFn = fetch) {
  const res = await fetchFn(`https://api.openai.com/v1/realtime/calls/${encodeURIComponent(callId)}/${action}`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.OPENAI_API_KEY}`, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`openai ${action}: ${res.status}`);
  return res.status === 204 ? {} : res.json().catch(() => ({}));
}

export function sipHeader(event, name) {
  const h = ((event && event.data && event.data.sip_headers) || []).find((x) => String(x.name).toLowerCase() === name.toLowerCase());
  return h ? String(h.value) : "";
}

export function numberFromSip(value) {
  const m = /(?:sip:|tel:)?(\+?\d{8,15})@?/.exec(String(value || ""));
  return m ? e164(m[1].startsWith("+") ? m[1] : `+${m[1]}`) : "";
}

// ── ElevenLabs ────────────────────────────────────────────────────────────────

export function firstMessage(job) {
  const v = job.agent_vars || {};
  const who = v.our_company_intro || job.our_company || "our company";
  return `Good day, this is ${job.agent_name || "the purchasing team"} from ${who}, purchasing department. May I speak with ${v.contact_name || "the person responsible for quotations"}?`;
}

export function elevenOutboundBody(env, job) {
  let phones = {};
  try { phones = JSON.parse(env.ELEVEN_PHONE_IDS || "{}"); } catch { phones = {}; }
  return {
    agent_id: env.ELEVEN_AGENT_ID,
    agent_phone_number_id: phones[job.from_pool] || phones.GB,
    to_number: job.phone,
    conversation_initiation_client_data: {
      dynamic_variables: { job_id: job.job_id },
      conversation_config_override: {
        agent: { prompt: { prompt: job.prompt }, first_message: firstMessage(job), language: "en" },
      },
    },
  };
}

export async function elevenOutbound(env, job, fetchFn = fetch) {
  const res = await fetchFn("https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call", {
    method: "POST",
    headers: { "xi-api-key": env.ELEVENLABS_API_KEY, "content-type": "application/json" },
    body: JSON.stringify(elevenOutboundBody(env, job)),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false) throw new Error(`elevenlabs outbound: ${res.status}`);
  return data; // {success, message, conversation_id, sip_call_id}
}
