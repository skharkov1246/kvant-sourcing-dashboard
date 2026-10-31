// Запись разговора для робота-оценщика (scripts/call_eval.py, формат «ЗАПИСЬ ЗВОНКА»): одинаковая
// для обеих групп — из событий OpenAI Realtime или из итогового вебхука ElevenLabs.

export function evalRecord(job, turns, tools, extra = {}) {
  return {
    call_id: job.job_id,
    arm: job.arm,
    country: job.country,
    disclosure: (job.agent_vars && job.agent_vars.disclosure) || "off",
    requests: (job.requests || []).map((r) => ({
      card: String(r.card),
      rfq_no: r.rfq_no,
      email_date: r.email_date,
      subject: r.subject,
      email_from: r.email_from,
    })),
    turns: (turns || []).filter((t) => String(t.text || "").trim()),
    tools: tools || [],
    ...extra,
  };
}

// Итог ElevenLabs (post_call_transcription) → реплики и действия агента. Поля вызова инструмента
// в расшифровке документация называет по-разному — берём оба написания.
export function fromElevenPostcall(data) {
  const turns = [];
  const tools = [];
  for (const t of (data && data.transcript) || []) {
    const role = t.role === "agent" ? "agent" : "callee";
    if (t.message) turns.push({ role, text: String(t.message) });
    for (const c of t.tool_calls || []) {
      const name = c.tool_name || c.name;
      let args = c.params_as_json ?? c.parameters ?? c.params ?? {};
      if (typeof args === "string") {
        try { args = JSON.parse(args); } catch { args = {}; }
      }
      if (name && !String(name).startsWith("system_") && name !== "voicemail_detection" && name !== "end_call") {
        tools.push({ name, args });
      }
    }
  }
  const dur = data && data.metadata && (data.metadata.call_duration_secs ?? data.metadata.duration_secs);
  const summary = data && data.analysis && data.analysis.transcript_summary;
  return { turns, tools, duration_s: Number(dur) || 0, summary: summary || "" };
}

export function recordKey(job, day) {
  return `callr:${day}:${job.job_id}`;
}
