// Durable Object CallSession — один на звонок группы gpt: держит WebSocket к OpenAI Realtime
// (wss://api.openai.com/v1/realtime?call_id=…), исполняет инструменты агента, копит расшифровку
// и по закрытию сокета завершает звонок через общий finalize (core.mjs).
// Исходящий WebSocket не засыпает: объект живёт, пока открыт сокет (документация Cloudflare).

import { runTool } from "./tools.mjs";
import { finalize, handleTool } from "./core.mjs";

export class SessionCore {
  constructor({ job, send, onTool, onFinish, stageMoves }) {
    this.job = job;
    this.send = send;
    this.onTool = onTool;
    this.onFinish = onFinish;
    this.stageMoves = stageMoves;
    this.turns = [];
    this.tools = [];
    this.started = Date.now();
    this.done = false;
  }

  start() {
    this.send({ type: "response.create" }); // исходящий звонок: агент здоровается первым
  }

  async onEvent(ev) {
    if (!ev || typeof ev !== "object") return;
    if (ev.type === "conversation.item.input_audio_transcription.completed" && ev.transcript) {
      this.turns.push({ role: "callee", text: String(ev.transcript) });
    } else if (ev.type === "response.output_audio_transcript.done" && ev.transcript) {
      this.turns.push({ role: "agent", text: String(ev.transcript) });
    } else if (ev.type === "response.function_call_arguments.done") {
      let args = {};
      try { args = JSON.parse(ev.arguments || "{}"); } catch { args = {}; }
      const r = runTool(ev.name, args, this.job, { stageMoves: this.stageMoves });
      if (!r.error) this.tools.push({ name: ev.name, args });
      await this.onTool(ev.name, args, r);
      this.send({
        type: "conversation.item.create",
        item: { type: "function_call_output", call_id: ev.call_id, output: JSON.stringify({ result: r.say }) },
      });
      this.send({ type: "response.create" });
    }
  }

  async close() {
    if (this.done) return;
    this.done = true;
    await this.onFinish({ turns: this.turns, tools: this.tools, duration_s: (Date.now() - this.started) / 1000 });
  }
}

export class CallSession {
  constructor(state, env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request) {
    const { call_id, job } = await request.json();
    const env = this.env;
    const resp = await fetch(`https://api.openai.com/v1/realtime?call_id=${encodeURIComponent(call_id)}`, {
      headers: { Upgrade: "websocket", Authorization: `Bearer ${env.OPENAI_API_KEY}` },
    });
    const ws = resp.webSocket;
    if (!ws) return new Response("no websocket", { status: 502 });
    ws.accept();
    const core = new SessionCore({
      job,
      stageMoves: env.STAGE_MOVES === "1",
      send: (m) => ws.send(JSON.stringify(m)),
      onTool: (name, args, r) => handleTool(env, job, name, args, r),
      onFinish: (res) => finalize(env, job.job_id, res),
    });
    ws.addEventListener("message", (e) => {
      let ev = null;
      try { ev = JSON.parse(e.data); } catch { ev = null; }
      core.onEvent(ev).catch(() => {});
    });
    ws.addEventListener("close", () => core.close().catch(() => {}));
    ws.addEventListener("error", () => core.close().catch(() => {}));
    core.start();
    return new Response("ok");
  }
}
