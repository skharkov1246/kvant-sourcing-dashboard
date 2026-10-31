// Тесты обработчика звонилки (node --test). Корпус придуман (CLAUDE.md, правило 18): номера,
// компании и адреса — вымышленные; сеть не трогаем — Telnyx, OpenAI, ElevenLabs и Битрикс подменены.
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";

import { applyEffects, bitrixCall, effectToCall } from "../src/bitrix.mjs";
import { dispatchNow, finalize, handleTool } from "../src/core.mjs";
import worker from "../src/index.mjs";
import { Ledger, finish, reserve, setPending, takePending } from "../src/ledger.mjs";
import { emptyState, inWindow, pickCalls } from "../src/plan.mjs";
import { evalRecord, fromElevenPostcall } from "../src/record.mjs";
import { SessionCore } from "../src/session.mjs";
import {
  acceptBody, amdDecision, b64json, elevenOutboundBody, numberFromSip, realtimeTools, telnyxDialBody,
  telnyxTransferBody, unb64json,
} from "../src/telephony.mjs";
import { finalComment, outcomeOf, runTool } from "../src/tools.mjs";
import { bytesToB64, verifyEleven, verifyOpenAI, verifyTelnyx } from "../src/verify.mjs";

// 10:00 по Шанхаю в понедельник 02.11.2026 = 02:00 UTC
const MON_10_SHANGHAI = new Date("2026-11-02T02:00:00Z");

function call(id, arm, extra = {}) {
  return {
    company_id: String(id), arm, phone: "+8613800138000", country: "CN", tz: "Asia/Shanghai", from_pool: "HK",
    company: "Example Pumps Co", contact_name: "",
    requests: [{ card: String(id * 10), rfq_no: `900-${id * 10}`, subject: "Pump spares", email_date: "20 October 2026",
      email_from: "buy@example.ae", sourcer: "77" }],
    agent_vars: { disclosure: "end", contact_name: "Mr. Li" }, prompt: "PROMPT", voicemail: "VOICEMAIL", ...extra,
  };
}

function memStorage() {
  const m = new Map();
  return { get: async (k) => structuredClone(m.get(k)), put: async (k, v) => void m.set(k, structuredClone(v)) };
}

function fakeNamespace(Cls, env) {
  const objs = new Map();
  return {
    idFromName: (n) => n,
    get: (id) => {
      if (!objs.has(id)) objs.set(id, new Cls({ storage: memStorage() }, env));
      const o = objs.get(id);
      return { fetch: (url, init) => o.fetch(new Request(url, init)) };
    },
  };
}

function fakeEnv(extra = {}) {
  const kv = new Map();
  const env = {
    KV: { get: async (k, t) => (t === "json" ? JSON.parse(kv.get(k) || "null") : kv.get(k)), put: async (k, v) => void kv.set(k, v), _m: kv },
    CALL_ARMS_ACTIVE: "gpt,eleven", MAX_CONCURRENT: "4", DAILY_PER_ARM: "60", BITRIX_WRITE_WEBHOOK_URL: "https://b24.example/rest/1/x",
    ...extra,
  };
  env.LEDGER = fakeNamespace(Ledger, env);
  return env;
}

// ───── подписи ─────

test("OpenAI: Standard Webhooks HMAC, свежесть, подмена тела", async () => {
  const key = Buffer.from("test-secret-key-0123456789");
  const secret = "whsec_" + key.toString("base64");
  const body = '{"type":"realtime.call.incoming"}';
  const ts = "1790000000";
  const sig = createHmac("sha256", key).update(`msg_1.${ts}.${body}`).digest("base64");
  const h = new Headers({ "webhook-id": "msg_1", "webhook-timestamp": ts, "webhook-signature": `v1,${sig}` });
  assert.equal(await verifyOpenAI(secret, h, body, 1790000010), true);
  assert.equal(await verifyOpenAI(secret, h, body + " ", 1790000010), false);
  assert.equal(await verifyOpenAI(secret, h, body, 1790009999), false);       // старое
  assert.equal(await verifyOpenAI("", h, body, 1790000010), false);
});

test("ElevenLabs: t=…,v0=hex над «t.body», ключ — текст", async () => {
  const body = '{"type":"post_call_transcription"}';
  const t = "1790000000";
  const v0 = createHmac("sha256", "wsec_example").update(`${t}.${body}`).digest("hex");
  assert.equal(await verifyEleven("wsec_example", `t=${t},v0=${v0}`, body, 1790000100), true);
  assert.equal(await verifyEleven("other", `t=${t},v0=${v0}`, body, 1790000100), false);
  assert.equal(await verifyEleven("wsec_example", `t=${t},v0=${v0}`, body, 1790009999), false);
});

test("Telnyx: Ed25519 над «timestamp|body»", async () => {
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const pub = bytesToB64(new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey)));
  const body = '{"data":{"event_type":"call.answered"}}';
  const ts = "1790000000";
  const sig = bytesToB64(new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, kp.privateKey, new TextEncoder().encode(`${ts}|${body}`))));
  assert.equal(await verifyTelnyx(pub, sig, ts, body, 1790000005), true);
  assert.equal(await verifyTelnyx(pub, sig, ts, body.replace("answered", "hangup"), 1790000005), false);
  assert.equal(await verifyTelnyx(pub, sig, ts, body, 1790001000), false);
  assert.equal(await verifyTelnyx(pub, "bad", ts, body, 1790000005), false);
});

// ───── план звонков ─────

test("окна звонка по местному времени и будни", () => {
  assert.equal(inWindow("Asia/Shanghai", MON_10_SHANGHAI), true);
  assert.equal(inWindow("Asia/Shanghai", new Date("2026-11-02T04:00:00Z")), false);   // 12:00 — перерыв
  assert.equal(inWindow("Asia/Shanghai", new Date("2026-11-01T02:00:00Z")), false);   // воскресенье
  assert.equal(inWindow("Asia/Dubai", new Date("2026-11-02T06:00:00Z")), true);       // 10:00 в Дубае
  assert.equal(inWindow("Not/AZone", MON_10_SHANGHAI), false);
});

test("pickCalls: контроль, «не звонить», сегодня звонили, попытки, окно, линии, чередование групп", () => {
  const q = { calls: [call(1, "gpt"), call(2, "gpt"), call(3, "eleven"), call(4, "control"), call(5, "gpt"),
    call(6, "eleven", { tz: "Asia/Dubai" }), call(7, "gpt"), call(8, "gpt", { prompt: "" })] };
  const st = { ...emptyState(), companies: { 5: { dnc: true }, 7: { last_day: "2026-11-02" } }, cards: { 20: { attempts: 3 } } };
  const r = pickCalls(q, st, MON_10_SHANGHAI, { arms: ["gpt", "eleven"], maxConcurrent: 2, dailyPerArm: 60 });
  assert.deepEqual(r.picked.map((c) => c.company_id), ["1", "3"]);                 // gpt, eleven по очереди
  assert.equal(r.counters["контроль — не звоним"], 1);
  assert.equal(r.counters["компания: не звонить"], 1);
  assert.equal(r.counters["сегодня уже звонили"], 1);
  assert.equal(r.counters["попытки исчерпаны"], 1);
  assert.equal(r.counters["вне окна звонка"], 1);                                   // Дубай в 6:00
  assert.equal(r.counters["нет текстов агента"], 1);
  const paused = pickCalls(q, { ...st, paused: true }, MON_10_SHANGHAI, { arms: ["gpt"], maxConcurrent: 2 });
  assert.equal(paused.picked.length, 0);
  const capped = pickCalls(q, { ...emptyState(), dials: { "2026-11-02": { gpt: 60 } } }, MON_10_SHANGHAI, { arms: ["gpt"], maxConcurrent: 3, dailyPerArm: 60 });
  assert.equal(capped.picked.length, 0);
});

// ───── действия агента ─────

test("runTool: дата КП, неверная дата, чужой RFQ, повторная отправка — задача сорсеру, отказ", () => {
  const job = { job_id: "j1", company_id: "1", requests: call(1, "gpt").requests };
  const ok = runTool("set_quote_date", { rfq_no: "900-10", date: "2026-11-05" }, job);
  assert.equal(ok.effects[0].type, "comment");
  assert.deepEqual(ok.state, [{ type: "promise", card: "10", date: "2026-11-05" }]);
  assert.equal(runTool("set_quote_date", { rfq_no: "900-10", date: "2026-02-30" }, job).error, "date");
  const two = { ...job, requests: [...job.requests, { ...job.requests[0], card: "11", rfq_no: "900-11" }] };
  assert.equal(runTool("remind_email", { rfq_no: "999-1" }, two).error, "rfq");
  const resend = runTool("resend_rfq", { rfq_no: "900-10", email: "sales@example.cn" }, job);
  assert.ok(resend.say.includes("will re-send") && !resend.say.includes("have just"));       // не обещаем то, чего не было
  assert.deepEqual(resend.effects.map((e) => e.type), ["comment", "task"]);
  assert.equal(resend.effects[1].responsible, 77);
  assert.deepEqual(runTool("mark_declined", { rfq_no: "900-10", reason: "no stock" }, job).effects.map((e) => e.type), ["comment", "task"]);
  assert.deepEqual(runTool("mark_declined", { rfq_no: "900-10", reason: "x" }, job, { stageMoves: true }).effects.map((e) => e.stage || e.type),
    ["comment", "DT166_24:2"]);
  assert.deepEqual(runTool("do_not_call", {}, two).state, [{ type: "dnc", company_id: "1" }]);
  assert.equal(runTool("do_not_call", {}, two).effects.length, 2);
});

test("исход звонка по действиям и комментарий", () => {
  assert.equal(outcomeOf([{ name: "resend_rfq" }, { name: "set_quote_date" }], []), "quote_date_set");
  assert.equal(outcomeOf([{ name: "set_quote_date" }, { name: "do_not_call" }], []), "do_not_call");
  assert.equal(outcomeOf([], [{ role: "callee", text: "hello?" }]), "no_outcome");
  assert.equal(outcomeOf([], []), "no_answer");
  const c = finalComment({}, "quote_date_set", { when: "2026-11-02 10:05 по местному", attempt: 1, duration_s: 95 });
  assert.ok(c.includes("обещана дата КП") && c.includes("Попытка 1 из 3") && c.includes("95 с"));
});

// ───── журнал ─────

test("журнал: одна компания — один звонок, перевод по заголовку и по номеру, попытки до 3", () => {
  const st = emptyState();
  const job = { job_id: "j1", company_id: "1", arm: "gpt", requests: [{ card: "10" }] };
  assert.equal(reserve(st, job, MON_10_SHANGHAI), true);
  assert.equal(reserve(st, { ...job, job_id: "j2" }, MON_10_SHANGHAI), false);
  assert.equal(st.dials["2026-11-02"].gpt, 1);
  setPending(st, "+85230000000", "j1", "voicemail", MON_10_SHANGHAI);
  assert.equal(takePending(st, { jobHeader: "j1" }, MON_10_SHANGHAI).mode, "voicemail");
  setPending(st, "+85230000000", "j1", "human", MON_10_SHANGHAI);
  assert.equal(takePending(st, { fromNumber: "+85230000000" }, new Date(MON_10_SHANGHAI.getTime() + 200_000)), null); // протух
  setPending(st, "+85230000000", "j1", "human", MON_10_SHANGHAI);
  assert.equal(takePending(st, { fromNumber: "+85230000000" }, MON_10_SHANGHAI).job.job_id, "j1");
  st.cards["10"] = { attempts: 2 };
  finish(st, "j1", "2026-11-02");
  finish(st, "j1", "2026-11-02");                                                   // повтор вебхука — без двойного счёта
  assert.equal(st.cards["10"].attempts, 3);
  assert.equal(st.companies["1"].last_day, "2026-11-02");
  assert.deepEqual(st.active, {});
});

// ───── Битрикс ─────

test("Битрикс: формы вызовов и повтор после 429", async () => {
  assert.deepEqual(effectToCall({ type: "comment", card: "10", text: "x" }),
    ["crm.timeline.comment.add", { fields: { ENTITY_ID: 10, ENTITY_TYPE: "dynamic_166", COMMENT: "x" } }]);
  assert.equal(effectToCall({ type: "stage", card: "10", stage: "DT166_24:2" })[1].fields.stageId, "DT166_24:2");
  assert.deepEqual(effectToCall({ type: "task", card: "10", responsible: 77, title: "t", text: "d" })[1].fields.UF_CRM_TASK, ["Ta6_10"]);
  let n = 0;
  const fetchFn = async () => (++n === 1 ? new Response("", { status: 429 }) : Response.json({ result: 5 }));
  assert.equal(await bitrixCall("https://b24.example/rest/1/x", "crm.item.update", {}, { fetchFn, sleep: async () => {} }), 5);
  const bad = async () => Response.json({ error: "ACCESS_DENIED" }, { status: 401 });
  const r = await applyEffects("https://b24.example/rest/1/x", [{ type: "comment", card: "1", text: "x" }], { fetchFn: bad, sleep: async () => {} });
  assert.equal(r.failed.length, 1);
});

// ───── запись разговора и ElevenLabs ─────

test("итог ElevenLabs → реплики и действия; запись — в формате оценщика", () => {
  const data = { transcript: [
    { role: "agent", message: "Good morning", tool_calls: [] },
    { role: "user", message: "Yes, received" },
    { role: "agent", message: "Saved", tool_calls: [{ tool_name: "set_quote_date", params_as_json: '{"rfq_no":"900-10","date":"2026-11-05"}' },
      { tool_name: "end_call", params_as_json: "{}" }] }],
  metadata: { call_duration_secs: 64 }, analysis: { transcript_summary: "Quote by 5 Nov" } };
  const r = fromElevenPostcall(data);
  assert.deepEqual(r.turns.map((t) => t.role), ["agent", "callee", "agent"]);
  assert.deepEqual(r.tools, [{ name: "set_quote_date", args: { rfq_no: "900-10", date: "2026-11-05" } }]);
  assert.equal(r.duration_s, 64);
  const rec = evalRecord({ job_id: "j1", arm: "eleven", agent_vars: { disclosure: "end" }, requests: call(1, "eleven").requests }, r.turns, r.tools);
  assert.deepEqual(Object.keys(rec).sort(), ["arm", "call_id", "country", "disclosure", "requests", "tools", "turns"]);
  assert.equal(rec.requests[0].rfq_no, "900-10");
});

// ───── набор и соединение ─────

test("Telnyx и OpenAI: тела запросов, решение по автоответчику, номер из SIP", () => {
  const env = { TELNYX_CONNECTION_ID: "c1", PUBLIC_URL: "https://cb.example/", CALL_NUMBERS: '{"HK":"+85230000000","GB":"+442000000000"}', OPENAI_PROJECT_ID: "proj_x" };
  const job = { job_id: "j1", phone: "+8613800138000", from_pool: "HK", prompt: "P", voicemail: "VM" };
  const d = telnyxDialBody(env, job);
  assert.equal(d.from, "+85230000000");
  assert.equal(d.webhook_url, "https://cb.example/telnyx");
  assert.deepEqual(unb64json(d.client_state), { job_id: "j1" });
  assert.equal(telnyxDialBody(env, { ...job, from_pool: "SG" }).from, "+442000000000");
  const t = telnyxTransferBody(env, job, "human");
  assert.equal(t.to, "sip:proj_x@sip.api.openai.com;transport=tls");
  assert.equal(amdDecision("call.machine.premium.detection.ended", "human_business"), "human");
  assert.equal(amdDecision("call.machine.premium.detection.ended", "machine"), "wait_beep");
  assert.equal(amdDecision("call.machine.premium.detection.ended", "silence"), "hangup");
  assert.equal(amdDecision("call.machine.premium.greeting.ended", "beep_detected"), "voicemail");
  assert.equal(acceptBody({}, job, "human").instructions, "P");
  assert.ok(acceptBody({}, job, "voicemail").instructions.endsWith("VM"));
  assert.equal(acceptBody({}, job, "human").model, "gpt-realtime-2");
  assert.equal(realtimeTools().length, 7);
  assert.equal(numberFromSip("<sip:+85230000000@sip.telnyx.com>"), "+85230000000");
  assert.equal(unb64json(b64json({ a: "Ж" })).a, "Ж");
});

test("ElevenLabs: тело исходящего звонка с промптом звонка и job_id", () => {
  const env = { ELEVEN_AGENT_ID: "agent_x", ELEVEN_PHONE_IDS: '{"HK":"ph_hk","GB":"ph_gb"}' };
  const b = elevenOutboundBody(env, { job_id: "j1", phone: "+8613800138000", from_pool: "HK", prompt: "P", agent_vars: { contact_name: "Mr. Li" } });
  assert.equal(b.agent_phone_number_id, "ph_hk");
  assert.equal(b.conversation_initiation_client_data.dynamic_variables.job_id, "j1");
  assert.equal(b.conversation_initiation_client_data.conversation_config_override.agent.prompt.prompt, "P");
  assert.ok(b.conversation_initiation_client_data.conversation_config_override.agent.first_message.includes("Mr. Li"));
  // наша компания — та, с чьего адреса ушло письмо (из переменных агента), без «Dubai» по умолчанию
  const hk = elevenOutboundBody(env, { job_id: "j2", phone: "+85221234567", from_pool: "HK", prompt: "P",
    agent_vars: { our_company_intro: "Example HK Ltd" } });
  const fm = hk.conversation_initiation_client_data.conversation_config_override.agent.first_message;
  assert.ok(fm.includes("from Example HK Ltd, purchasing department") && !fm.includes("Dubai"));
});

// ───── сессия GPT ─────

test("сессия: вызов инструмента → результат агенту, расшифровка, завершение один раз", async () => {
  const sent = [];
  const toolsSeen = [];
  let finished = 0;
  const core = new SessionCore({
    job: { job_id: "j1", company_id: "1", requests: call(1, "gpt").requests },
    send: (m) => sent.push(m),
    onTool: async (name) => toolsSeen.push(name),
    onFinish: async (res) => { finished++; assert.equal(res.turns.length, 2); assert.equal(res.tools.length, 1); },
  });
  core.start();
  await core.onEvent({ type: "response.output_audio_transcript.done", transcript: "Good morning" });
  await core.onEvent({ type: "conversation.item.input_audio_transcription.completed", transcript: "Yes" });
  await core.onEvent({ type: "response.function_call_arguments.done", call_id: "fc1", name: "set_quote_date",
    arguments: '{"rfq_no":"900-10","date":"2026-11-05"}' });
  assert.deepEqual(sent.map((m) => m.type), ["response.create", "conversation.item.create", "response.create"]);
  assert.equal(sent[1].item.call_id, "fc1");
  assert.deepEqual(toolsSeen, ["set_quote_date"]);
  await core.close();
  await core.close();
  assert.equal(finished, 1);
});

// ───── набор порции и завершение (журнал — настоящий класс, хранилище в памяти) ─────

test("dispatchNow → finalize: набор по группам, комментарии в карточки, запись разговора, попытки", async () => {
  const env = fakeEnv({ MAX_CONCURRENT: "3" });
  const dialed = [];
  const effects = [];
  const deps = {
    now: MON_10_SHANGHAI,
    queue: { calls: [call(1, "gpt"), call(2, "eleven"), call(3, "control")] },
    telnyxDial: async (e, job) => dialed.push(["gpt", job.job_id]),
    elevenOutbound: async (e, job) => (dialed.push(["eleven", job.job_id]), { conversation_id: "conv_1" }),
    applyEffects: async (w, eff) => (effects.push(...eff), { done: eff.map((x) => x.type), failed: [] }),
  };
  const r = await dispatchNow(env, deps);
  assert.equal(r.placed, 2);
  assert.deepEqual(dialed.map((d) => d[0]), ["gpt", "eleven"]);
  const st = await (await env.LEDGER.get("main").fetch("https://l/op", { method: "POST", body: JSON.stringify({ op: "state" }) })).json();
  assert.equal(Object.keys(st.active).length, 2);
  assert.equal(st.byConv.conv_1, "2026-11-02-2");
  // действие агента и завершение группы eleven
  const job = st.active["2026-11-02-2"];
  await handleTool(env, job, "set_quote_date", {}, runTool("set_quote_date", { rfq_no: "900-20", date: "2026-11-06" }, job), deps);
  const fin = await finalize(env, job.job_id, { turns: [{ role: "callee", text: "ok" }], tools: [], duration_s: 50 }, deps);
  assert.equal(fin.outcome, "quote_date_set");                                      // действие из журнала
  assert.ok(effects.some((e) => e.type === "comment" && e.text.includes("обещана дата КП")));
  const rec = JSON.parse(env.KV._m.get("callr:2026-11-02:2026-11-02-2"));
  assert.equal(rec.arm, "eleven") ;
  assert.equal(rec.outcome, "quote_date_set");
  const st2 = await (await env.LEDGER.get("main").fetch("https://l/op", { method: "POST", body: JSON.stringify({ op: "state" }) })).json();
  assert.equal(st2.cards["20"].attempts, 1);
  assert.equal(st2.cards["20"].promise, "2026-11-06");
  assert.equal(await finalize(env, job.job_id, { turns: [], tools: [] }, deps), null);   // повтор — пусто
});

test("набор не состоялся — звонок снят, попытка засчитана", async () => {
  const env = fakeEnv();
  const deps = { now: MON_10_SHANGHAI, queue: { calls: [call(1, "gpt")] },
    telnyxDial: async () => { throw new Error("telnyx /calls: 403"); }, applyEffects: async () => ({ done: [], failed: [] }) };
  const r = await dispatchNow(env, deps);
  assert.equal(r.placed, 0);
  assert.equal(r.counters["сбой набора"], 1);
  const st = await (await env.LEDGER.get("main").fetch("https://l/op", { method: "POST", body: JSON.stringify({ op: "state" }) })).json();
  assert.deepEqual(st.active, {});
  assert.equal(st.cards["10"].attempts, 1);
});

// ───── маршруты ─────

test("маршруты: здоровье, отказ без подписи и секрета, расписание закрыто без CALLS_ENABLED", async () => {
  const env = fakeEnv({ CALLS_ENABLED: "0", ELEVEN_TOOL_SECRET: "s3cret", OPENAI_WEBHOOK_SECRET: "whsec_" + Buffer.from("k").toString("base64") });
  assert.deepEqual(await (await worker.fetch(new Request("https://cb.example/health"), env)).json(), { ok: true, enabled: false });
  assert.equal((await worker.fetch(new Request("https://cb.example/openai", { method: "POST", body: "{}" }), env)).status, 403);
  assert.equal((await worker.fetch(new Request("https://cb.example/eleven/tool/set_quote_date", { method: "POST", body: "{}" }), env)).status, 403);
  assert.equal((await worker.fetch(new Request("https://cb.example/telnyx", { method: "POST", body: "{}" }), env)).status, 403);
  let ran = false;
  await worker.scheduled({}, env, { waitUntil: () => { ran = true; } });
  assert.equal(ran, false);
  const res = await worker.fetch(new Request("https://cb.example/eleven/tool/set_quote_date",
    { method: "POST", headers: { "x-callbot-secret": "s3cret" }, body: '{"job_id":"none"}' }), env);
  assert.ok((await res.json()).result.includes("not found"));
});
