// Запись в Битрикс: комментарий в ленту карточки СП-166, задача сорсеру, смена стадии.
// Вебхук записи — секрет воркера BITRIX_WRITE_WEBHOOK_URL (заводит владелец; у агента его нет).
// На 429/503 ждём и повторяем (CLAUDE.md, «Битрикс не перегружать»), не больше трёх раз.

const ENTITY_TYPE = "dynamic_166"; // crm.timeline.comment.add: ENTITY_TYPE смарт-процесса
const SPA_ID = 166;

export function effectToCall(effect) {
  if (effect.type === "comment") {
    return ["crm.timeline.comment.add", { fields: { ENTITY_ID: Number(effect.card), ENTITY_TYPE, COMMENT: effect.text } }];
  }
  if (effect.type === "stage") {
    return ["crm.item.update", { entityTypeId: SPA_ID, id: Number(effect.card), fields: { stageId: effect.stage } }];
  }
  if (effect.type === "task") {
    const deadline = new Date(Date.now() + 8 * 3600_000).toISOString();
    return [
      "tasks.task.add",
      {
        fields: {
          TITLE: effect.title,
          DESCRIPTION: effect.text,
          RESPONSIBLE_ID: effect.responsible,
          DEADLINE: deadline,
          UF_CRM_TASK: [`T${SPA_ID.toString(16)}_${effect.card}`],
        },
      },
    ];
  }
  return null;
}

export async function bitrixCall(webhook, method, params, { fetchFn = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  const base = String(webhook || "").replace(/\/+$/, "");
  if (!base) throw new Error("BITRIX_WRITE_WEBHOOK_URL не задан");
  for (const pause of [2000, 5000, 15000, null]) {
    const res = await fetchFn(`${base}/${method}.json`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
    });
    if ((res.status === 429 || res.status === 503) && pause) {
      await sleep(pause);
      continue;
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok || body.error) throw new Error(`bitrix ${method}: ${res.status} ${body.error || ""}`.trim());
    return body.result;
  }
  throw new Error(`bitrix ${method}: перегрузка`);
}

// Все записи звонка по очереди (не параллельно: ≤ 2 запросов в секунду на портал).
export async function applyEffects(webhook, effects, opts = {}) {
  const done = [];
  const failed = [];
  for (const e of effects || []) {
    const call = effectToCall(e);
    if (!call) continue;
    try {
      await bitrixCall(webhook, call[0], call[1], opts);
      done.push(e.type);
    } catch (err) {
      failed.push(`${e.type}: ${String(err.message || err).slice(0, 120)}`);
    }
    if (opts.sleep !== undefined || opts.fetchFn) continue; // в тестах без пауз
    await new Promise((r) => setTimeout(r, 600));
  }
  return { done, failed };
}
