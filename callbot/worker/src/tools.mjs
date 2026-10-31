// Действия агента во время звонка → изменения состояния и записи в Битрикс.
// Без доработки Битрикса (решение владельца 31.10.2026): только существующие методы REST —
// комментарий в ленту карточки, задача сорсеру, стадия «Отказ в КП» (только при STAGE_MOVES=1).
// Письма через REST не уходят (навык supplier-rfq): повторную отправку делает сорсер по задаче,
// поэтому агент говорит «перешлём сегодня», а не «уже переслал».

export const REFUSED_STAGE = "DT166_24:2"; // stages.py: refused — «Отказ в КП»

function cardOf(job, rfqNo) {
  const reqs = job.requests || [];
  const hit = reqs.find((r) => String(r.rfq_no).trim() === String(rfqNo || "").trim());
  return hit || (reqs.length === 1 ? reqs[0] : null);
}

function isoDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || "").trim());
  if (!m) return "";
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCMonth() === +m[2] - 1 ? `${m[1]}-${m[2]}-${m[3]}` : "";
}

const clip = (s, n = 300) => String(s || "").replace(/\s+/g, " ").trim().slice(0, n);

// → {say, effects:[…], state:[…]}: say — что агент скажет (результат инструмента);
// effects — записи в Битрикс; state — изменения состояния звонилки.
export function runTool(name, args, job, opts = {}) {
  const a = args && typeof args === "object" ? args : {};
  const req = ["do_not_call"].includes(name) ? null : cardOf(job, a.rfq_no);
  if (name !== "do_not_call" && !req) {
    return { say: "Unknown RFQ number. Use one of the RFQ numbers from your list.", effects: [], state: [], error: "rfq" };
  }
  const card = req && String(req.card);
  const sourcer = req && Number(req.sourcer) > 0 ? Number(req.sourcer) : null;
  const rfq = req && req.rfq_no;
  const task = (title, text) => (sourcer ? [{ type: "task", card, responsible: sourcer, title, text }] : []);
  switch (name) {
    case "set_quote_date": {
      const date = isoDate(a.date);
      if (!date) return { say: "The date is not valid. Ask for the date again and use the format YYYY-MM-DD.", effects: [], state: [], error: "date" };
      return {
        say: `Saved: quotation for RFQ ${rfq} expected on ${date}.`,
        effects: [{ type: "comment", card, text: `Звонилка: поставщик обещал КП к ${date}.` }],
        state: [{ type: "promise", card, date }],
      };
    }
    case "resend_rfq": {
      const email = clip(a.email, 120);
      return {
        say: "Noted. Our purchasing team will re-send the request today" + (email ? ` to ${email}.` : "."),
        effects: [
          { type: "comment", card, text: `Звонилка: поставщик не получил письмо по RFQ ${rfq}. ` + (email ? `Просит отправить на ${email}.` : "Адрес прежний.") },
          ...task(`Переслать запрос RFQ ${rfq}`, `Поставщик по звонку не получил письмо. ${email ? `Адрес: ${email}.` : "Адрес прежний."} Переслать сегодня из карточки.`),
        ],
        state: [{ type: "resend", card }],
      };
    }
    case "remind_email":
      return {
        say: "Noted.",
        effects: [{ type: "comment", card, text: `Звонилка: напомнили о письме по RFQ ${rfq} (сообщение на автоответчике или устно).` }],
        state: [],
      };
    case "add_contact": {
      const who = [clip(a.name, 80), clip(a.position, 80), clip(a.email, 120), clip(a.phone, 40)].filter(Boolean).join(", ");
      return {
        say: "Saved. Our team will send the request to this contact today.",
        effects: [
          { type: "comment", card, text: `Звонилка: запросами занимается другой человек: ${who}.` },
          ...task(`Новый контакт поставщика по RFQ ${rfq}`, `По звонку: ${who}. Завести контакт в карточке и переслать запрос.`),
        ],
        state: [{ type: "resend", card }],
      };
    }
    case "mark_declined": {
      const reason = clip(a.reason);
      const alt = clip(a.alternative_supplier, 200);
      return {
        say: "Thank you, noted.",
        effects: [
          { type: "comment", card, text: `Звонилка: поставщик отказался от КП по RFQ ${rfq}. Причина: ${reason || "не названа"}.` + (alt ? ` Советует: ${alt}.` : "") },
          ...(opts.stageMoves ? [{ type: "stage", card, stage: REFUSED_STAGE }] : task(`Отказ поставщика по RFQ ${rfq}`, `По звонку: отказ. Причина: ${reason || "не названа"}. Перевести карточку в «Отказ в КП».`)),
        ],
        state: [{ type: "declined", card }],
      };
    }
    case "escalate": {
      const q = clip(a.question, 500);
      return {
        say: "I have passed your question to our sourcing manager; you will receive an answer by email today.",
        effects: [
          { type: "comment", card, text: `Звонилка: вопрос поставщика по RFQ ${rfq}: ${q}` },
          ...task(`Вопрос поставщика по RFQ ${rfq}`, `По звонку: ${q}. Ответить письмом сегодня.`),
        ],
        state: [],
      };
    }
    case "do_not_call":
      return {
        say: "Understood. We will contact you by email only.",
        effects: (job.requests || []).map((r) => ({ type: "comment", card: String(r.card), text: "Звонилка: поставщик попросил больше не звонить — только письма." })),
        state: [{ type: "dnc", company_id: job.company_id }],
      };
    default:
      return { say: "This action is not available.", effects: [], state: [], error: "tool" };
  }
}

// Исход звонка — из действий агента, одинаково для обеих групп (сравнение честное).
export function outcomeOf(tools, turns) {
  const names = new Set((tools || []).map((t) => t.name));
  if (names.has("do_not_call")) return "do_not_call";
  if (names.has("mark_declined")) return "declined";
  if (names.has("set_quote_date")) return "quote_date_set";
  if (names.has("add_contact")) return "wrong_person_new_contact";
  if (names.has("resend_rfq")) return "resent";
  if (names.has("escalate")) return "escalated";
  if (names.has("remind_email")) return "voicemail";
  const callee = (turns || []).filter((t) => t.role === "callee" && String(t.text || "").trim());
  return callee.length ? "no_outcome" : "no_answer";
}

const OUTCOME_RU = {
  do_not_call: "просили не звонить",
  declined: "отказ от КП",
  quote_date_set: "обещана дата КП",
  wrong_person_new_contact: "дали другой контакт",
  resent: "письмо не получено — перешлём",
  escalated: "вопрос передан сорсеру",
  voicemail: "автоответчик, оставлено сообщение",
  no_outcome: "разговор без результата",
  no_answer: "не дозвонились",
};

// Комментарий в каждую карточку звонка после завершения.
export function finalComment(job, outcome, meta = {}) {
  const parts = [`Звонилка: звонок ${meta.when || ""}`.trim() + ".", `Итог: ${OUTCOME_RU[outcome] || outcome}.`];
  if (meta.attempt) parts.push(`Попытка ${meta.attempt} из 3.`);
  if (meta.duration_s) parts.push(`Длительность ${Math.round(meta.duration_s)} с.`);
  if (meta.summary) parts.push(`Кратко: ${clip(meta.summary, 400)}`);
  return parts.join(" ");
}
