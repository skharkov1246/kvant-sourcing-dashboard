// Мини-DOM для проверки страниц портала, которые рисуются скриптом целиком.
//
// ЗАЧЕМ ОН ЕСТЬ. Ошибка в таком скрипте даёт не кривую вёрстку, а ПУСТОЙ ЭКРАН, и
// увидит его владелец. Правило CLAUDE.md про smoke («вкладка без данных — это
// непроверенная вкладка») действует и здесь, а браузера в гейте нет.
//
// ЗАЧЕМ ОТДЕЛЬНЫМ МОДУЛЕМ. Сначала он жил внутри nomenclature_ui.test.mjs. Второй
// такой же странице (счётчики, 22.09.2026) он понадобился целиком, и выбор был
// между копией и выносом. Копия хуже: узел, который в настоящем DOM ведёт себя
// не так, как здесь, пришлось бы править в двух местах, а расхождение двух
// подделок DOM не заметит никто.
//
// Здесь ровно то, что нужно страницам, и ни узла больше. Каждая поддержанная
// особенность — не удобство, а поведение настоящего DOM, на котором страница уже
// ошибалась бы иначе.
export class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.events = {};
    this.hidden = false;
    this.value = "";
    this._text = "";
    this.className = "";
    this.style = {};
    // Разметка SVG в настоящем DOM разбирается браузером; здесь она хранится
    // строкой. Для проверки этого достаточно и даже лучше: видно, ЧТО страница
    // нарисовала, а не только что не упала.
    this._html = "";
  }
  set textContent(v) { this._text = v == null ? "" : String(v); this.children = []; this._html = ""; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { this._html = v == null ? "" : String(v); this.children = []; this._text = ""; }
  get innerHTML() { return this._html; }
  appendChild(node) {
    // Настоящий DOM разворачивает DocumentFragment при вставке и опустошает его.
    // Без этого список из трёх строк выглядел бы одним узлом.
    if (node instanceof Fragment) { this.children.push(...node.children); node.children = []; return node; }
    this.children.push(node);
    return node;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(k, fn) { (this.events[k] ||= []).push(fn); }
  async fire(k) { for (const fn of this.events[k] || []) await fn({ preventDefault() {} }); }
  querySelectorAll(sel) {
    const теги = sel.split(",").map((t) => t.trim().toUpperCase());
    return потомки(this).filter((e) => теги.includes(e.tagName));
  }
  focus() {}
  scrollIntoView() {}

  // ── таблицы ───────────────────────────────────────────────────────────────
  // tBodies, insertRow и insertCell — интерфейс HTMLTableElement, которым
  // страницы строят строки. Своего tbody здесь не создаётся: страница обязана
  // обращаться к разобранному из разметки, как в браузере.
  get tBodies() { return потомки(this).filter((e) => e.tagName === "TBODY"); }
  insertRow() {
    const tr = new Element("tr");
    this.children.push(tr);
    return tr;
  }
  insertCell() {
    const td = new Element("td");
    this.children.push(td);
    return td;
  }
}

export class Fragment extends Element {
  constructor() { super("#fragment"); }
}

export function потомки(root) { return [root, ...root.children.flatMap(потомки)]; }

/** Идентификаторы и вложенность из разметки страницы (всё до первого <script>).
 *
 *  ВЛОЖЕННОСТЬ ВАЖНА. tBodies ищет tbody СРЕДИ ПОТОМКОВ, поэтому плоский список
 *  узлов дал бы пустой tBodies и страница молча не нарисовала бы ни строки — при
 *  зелёном тесте.
 */
export function разобрать(html) {
  const разметка = html.split("<script>")[0];
  const корень = new Element("#root");
  const стек = [корень];
  const ПУСТЫЕ = new Set(["meta", "link", "br", "hr", "img", "input", "source", "i"]);
  for (const m of разметка.matchAll(/<(\/?)([a-z][a-z0-9]*)\b([^>]*?)(\/?)>/gi)) {
    const [, закрывающий, тег, атрибуты, самозакрытый] = m;
    if (закрывающий) {
      if (стек.length > 1) стек.pop();
      continue;
    }
    const el = new Element(тег);
    const id = атрибуты.match(/\bid="([^"]+)"/);
    if (id) el.id = id[1];
    el.hidden = /\shidden(?:\s|$)/.test(атрибуты);
    стек[стек.length - 1].children.push(el);
    if (!самозакрытый && !ПУСТЫЕ.has(тег.toLowerCase())) стек.push(el);
  }
  const карта = {};
  for (const el of потомки(корень)) if (el.id) карта[el.id] = el;
  return { корень, карта };
}

/** Прогоняет скрипт страницы на мини-DOM и возвращает карту узлов по id. */
// маршруты — для страниц, читающих несколько адресов (бренды: сводка, связи,
// корзины кодов): функция «адрес → снимок». hash — адрес записи после «#», как
// его видит страница при открытии по прямой ссылке.
export async function открыть(html, { снимок = null, ответ = null, маршруты = null, hash = "" } = {}) {
  const vm = await import("node:vm");
  const source = html.split("<script>").slice(1).join("<script>").split("</script>")[0];
  const { карта } = разобрать(html);
  const создано = [];
  const doc = {
    body: new Element("body"),
    getElementById: (id) => карта[id] || null,
    createElement: (tag) => { const e = new Element(tag); создано.push(e); return e; },
    createDocumentFragment: () => new Fragment(),
    createTextNode: (t) => Object.assign(new Element("#text"), { textContent: t }),
  };
  const context = {
    document: doc, console, URL, encodeURIComponent, Math, JSON,
    fetch: (url) => Promise.resolve(ответ || new Response(JSON.stringify(маршруты ? маршруты(String(url)) : снимок),
      { status: 200, headers: { "Content-Type": "application/json" } })),
    location: { hash, pathname: "/", search: "" },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  // Скрипт читает снимок через fetch: даём микрозадачам дойти до отрисовки.
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  return { карта, создано };
}
