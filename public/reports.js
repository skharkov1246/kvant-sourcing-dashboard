/* КОНСТРУКТОР ОТЧЁТОВ БАЗЫ ПОСТАВЩИКОВ.
 *
 * Распоряжение владельца 01.10.2026: «проваливаюсь в базу управления
 * поставщиками — там вся информация, и я могу собрать любой вид отчёта».
 *
 * Отчёт строится в браузере поверх тех же снимков, что читают страницы
 * раздела (/api/suppliers, /api/crossref, /api/brands …): второго источника
 * данных и второй резки прав нет — воркер уже отдал ровно то, что человеку
 * положено. Скрипт хранит только правила разбора; данных в нём нет.
 *
 * Две части: ДВИЖОК (чистые функции, проверяются тестом
 * access/test/reports_ui.test.mjs без браузера) и ЭКРАН (только в браузере).
 */
(function (root) {
  "use strict";

  // ── ДВИЖОК ───────────────────────────────────────────────────────────────
  var пусто = function (v) { return v == null || v === "" || (typeof v === "number" && isNaN(v)); };
  var да = function (v) { return v ? "да" : ""; };
  var список = function (v, n) {
    if (!Array.isArray(v)) return v == null ? "" : String(v);
    return v.slice(0, n || 50).map(function (x) {
      return Array.isArray(x) ? x[0] : (x && typeof x === "object" ? (x.name || x.pn || "") : x);
    }).filter(function (x) { return x != null && x !== ""; }).join(", ");
  };
  var число = function (v) {
    if (typeof v === "number") return v;
    if (пусто(v)) return NaN;
    var n = Number(String(v).replace(/\s/g, "").replace(",", "."));
    return n;
  };

  // Наборы: заголовок, нужные ответы API и разбор в плоские строки.
  // Поле — {id, label}; строка — объект по id полей. Разбор не выдумывает
  // значений: чего нет в снимке, то пусто, а не ноль.
  var НАБОРЫ = {
    suppliers: {
      title: "Поставщики (реестр)",
      fields: [["number", "Номер"], ["name", "Компания"], ["inn", "ИНН"], ["domain", "Домен"],
        ["country", "Страна"], ["status", "Статус"], ["sources", "Источники"], ["wait_inn", "Ждёт ИНН"],
        ["sent", "Запросов"], ["answered", "Ответили"], ["quoted", "С ценой"], ["silent", "Молчат"],
        ["no_outcome", "Без исхода"]],
      разобрать: function (d) {
        return ((d.suppliers && d.suppliers.entities) || []).map(function (e) {
          var r = e.rfq || {};
          return { number: e.number, name: e.name, inn: e.inn, domain: e.domain, country: e.country,
            status: e.status, sources: список(e.sources), wait_inn: да(e.wait_inn),
            sent: r.sent, answered: r.answered, quoted: r.quoted, silent: r.silent, no_outcome: r.no_outcome };
        });
      },
      need: ["suppliers"],
    },
    positions: {
      title: "Позиции (номенклатура)",
      fields: [["n", "Код"], ["bn", "Бренд"], ["name", "Наименование"], ["co", "Компаний"],
        ["offers", "Предложений"], ["cmp", "Цена сравнима"], ["dr", "Прямых предложений"],
        ["cat", "В каталоге"], ["kv", "Номер КВАНТ"], ["deals", "Сделок спроса"], ["drows", "Строк спроса"],
        ["dqty", "Кол-во в спросе"], ["models", "Машины"], ["brands", "Бренды карточек"], ["oem", "Изготовители в файлах"]],
      разобрать: function (d) {
        return ((d.crossref && d.crossref.positions) || []).map(function (p) {
          var dm = p.demand || {};
          return { n: p.n || p.k, bn: p.bn, name: p.name, co: p.co, offers: p.offers, cmp: да(p.cmp),
            dr: p.dr, cat: да(p.cat), kv: p.kv, deals: dm.deals, drows: dm.rows, dqty: dm.qty,
            models: список(p.models), brands: список(p.brands), oem: список(p.oem_file) };
        });
      },
      need: ["crossref"],
    },
    offers: {
      title: "Предложения поставщиков (код × компания)",
      fields: [["n", "Код"], ["bn", "Бренд"], ["name", "Наименование"], ["company", "Компания"],
        ["rows", "Строк"], ["price", "Цена"], ["cur", "Валюта"], ["date", "Дата цены"]],
      разобрать: function (d) {
        var cr = d.crossref || {}, cos = cr.companies || [], out = [];
        (cr.positions || []).forEach(function (p) {
          (p.e || []).forEach(function (e) {
            var c = cos[e[0]] || {};
            out.push({ n: p.n || p.k, bn: p.bn, name: p.name, company: c.name || c.co || "",
              rows: e[1], price: e[2], cur: e[3], date: e[4] });
          });
        });
        return out;
      },
      need: ["crossref"],
    },
    companies: {
      title: "Компании в предложениях",
      fields: [["name", "Компания"], ["rows", "Строк предложений"], ["parts", "Позиций"],
        ["nbrands", "Брендов"], ["brands", "Бренды"], ["oem", "Изготовители"]],
      разобрать: function (d) {
        return ((d.crossref && d.crossref.companies) || []).map(function (c) {
          return { name: c.name || c.co, rows: c.rows, parts: c.parts,
            nbrands: (c.brands || []).length, brands: список(c.brands, 10), oem: список(c.oem, 10) };
        });
      },
      need: ["crossref"],
    },
    brands: {
      title: "Бренды",
      fields: [["name", "Бренд"], ["any", "Кодов"], ["plausible", "Правдоподобных кодов"],
        ["priced", "Кодов с ценой"], ["asked", "Кодов спрашивали"], ["sups", "Пар с поставщиками"],
        ["deals", "Сделок"], ["models", "Машин"], ["dict", "В словаре"]],
      разобрать: function (d) {
        return ((d.brands && d.brands.brands) || []).map(function (b) {
          var c = b.codes || {};
          return { name: b.name || b.k, any: c.any, plausible: c.plausible, priced: b.priced,
            asked: b.asked, sups: b.sups, deals: c.deals, models: b.models_n, dict: да(b.dict) };
        });
      },
      need: ["brands"],
    },
    pairs: {
      title: "Бренд × поставщик",
      fields: [["brand", "Бренд"], ["supplier", "Поставщик"], ["codes", "Кодов"], ["priced", "Кодов с ценой"],
        ["price_rows", "Строк цен"], ["by_customer", "По спросу заказчика"], ["by_other_kp", "По чужому КП"],
        ["by_catalog", "По каталогу"], ["asked_closed", "Спрашивали — закрыто"]],
      разобрать: function (d) {
        var pr = d.pairs || {}, br = d.brands || {};
        var имяБ = {}, имяП = {};
        (br.brands || []).forEach(function (b) { имяБ[b.k] = b.name; });
        (br.suppliers || []).forEach(function (s) { имяП[s.k] = s.name; });
        var поля = pr.pair_fields || [];
        return (pr.pairs || []).map(function (row) {
          var o = {};
          поля.forEach(function (f, i) { o[f] = row[i]; });
          var bk = (pr.brands || [])[o.brand], sk = (pr.suppliers || [])[o.supplier];
          o.brand = имяБ[bk] || bk; o.supplier = имяП[sk] || sk;
          if (o.by_currency && typeof o.by_currency === "object") o.by_currency = JSON.stringify(o.by_currency);
          return o;
        });
      },
      need: ["pairs", "brands"],
    },
  };

  var ИСТОЧНИКИ = {
    suppliers: function (get) { return get("/api/suppliers"); },
    brands: function (get) { return get("/api/brands"); },
    pairs: function (get) { return get("/api/brands/pairs"); },
    crossref: function (get) {
      return get("/api/crossref").then(function (h) {
        var n = Number(h.lists) || 0, ждём = [];
        for (var i = 0; i < n; i++) ждём.push(get("/api/crossref/rows?l=" + (i < 10 ? "0" : "") + i));
        return Promise.all(ждём).then(function (части) {
          var pos = [];
          части.forEach(function (ч) { pos = pos.concat(ч.positions || []); });
          if (!n && Array.isArray(h.positions)) pos = h.positions;
          return Object.assign({}, h, { positions: pos });
        });
      });
    },
  };

  var ОПЕРАЦИИ = {
    contains: "содержит", eq: "=", ne: "≠", gt: ">", ge: "≥", lt: "<", le: "≤", empty: "пусто", filled: "заполнено",
  };

  function проходит(r, f) {
    var v = r[f.col];
    switch (f.op) {
      case "empty": return пусто(v);
      case "filled": return !пусто(v);
      case "contains": return String(v == null ? "" : v).toLowerCase().indexOf(String(f.val || "").toLowerCase()) >= 0;
      case "eq": return String(v == null ? "" : v).toLowerCase() === String(f.val == null ? "" : f.val).toLowerCase();
      case "ne": return String(v == null ? "" : v).toLowerCase() !== String(f.val == null ? "" : f.val).toLowerCase();
    }
    var a = число(v), b = число(f.val);
    if (isNaN(a) || isNaN(b)) return false;
    return f.op === "gt" ? a > b : f.op === "ge" ? a >= b : f.op === "lt" ? a < b : f.op === "le" ? a <= b : true;
  }

  function фильтровать(rows, filters, q) {
    var фф = (filters || []).filter(function (f) { return f && f.col && f.op; });
    var qq = String(q || "").trim().toLowerCase();
    return rows.filter(function (r) {
      for (var i = 0; i < фф.length; i++) if (!проходит(r, фф[i])) return false;
      if (!qq) return true;
      for (var k in r) if (r[k] != null && String(r[k]).toLowerCase().indexOf(qq) >= 0) return true;
      return false;
    });
  }

  // Группировка: строка на значение поля группы; «Строк» — всегда; прочие
  // показатели — по выбору: сумма, среднее, минимум, максимум, уникальных.
  var ФУНКЦИИ = { sum: "сумма", avg: "среднее", min: "минимум", max: "максимум", distinct: "уникальных" };
  function сгруппировать(rows, by, aggs) {
    if (!by) return rows;
    var группы = new Map();
    rows.forEach(function (r) {
      var ключ = пусто(r[by]) ? "(пусто)" : String(r[by]);
      if (!группы.has(ключ)) группы.set(ключ, []);
      группы.get(ключ).push(r);
    });
    var out = [];
    группы.forEach(function (rs, ключ) {
      var o = {}; o[by] = ключ; o.__count = rs.length;
      (aggs || []).forEach(function (a) {
        var id = a.fn + ":" + a.col;
        if (a.fn === "distinct") {
          var s = new Set(); rs.forEach(function (r) { if (!пусто(r[a.col])) s.add(String(r[a.col])); });
          o[id] = s.size; return;
        }
        var nums = rs.map(function (r) { return число(r[a.col]); }).filter(function (x) { return !isNaN(x); });
        if (!nums.length) { o[id] = null; return; }
        var sum = nums.reduce(function (x, y) { return x + y; }, 0);
        o[id] = a.fn === "sum" ? sum : a.fn === "avg" ? sum / nums.length
          : a.fn === "min" ? Math.min.apply(null, nums) : Math.max.apply(null, nums);
      });
      out.push(o);
    });
    return out;
  }

  function сортировать(rows, col, dir) {
    if (!col) return rows;
    var k = dir === "asc" ? 1 : -1;
    return rows.slice().sort(function (a, b) {
      var x = a[col], y = b[col];
      if (пусто(x) && пусто(y)) return 0;
      if (пусто(x)) return 1;
      if (пусто(y)) return -1;
      var nx = число(x), ny = число(y);
      if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * k;
      return String(x).localeCompare(String(y), "ru") * k;
    });
  }

  // CSV для Excel в русской локали: «;» и BOM, кавычки по RFC 4180.
  function вCSV(rows, cols) {
    var поле = function (v) {
      if (v == null) return "";
      var s = typeof v === "number" ? String(v).replace(".", ",") : String(v);
      return /[";\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var head = cols.map(function (c) { return поле(c.label); }).join(";");
    var body = rows.map(function (r) { return cols.map(function (c) { return поле(r[c.id]); }).join(";"); });
    return "﻿" + [head].concat(body).join("\r\n");
  }

  // Столбцы результата: при группировке — поле группы, «Строк» и показатели.
  function столбцы(ds, state) {
    var все = НАБОРЫ[ds].fields.map(function (f) { return { id: f[0], label: f[1] }; });
    var по = {}; все.forEach(function (c) { по[c.id] = c; });
    if (state.group) {
      var out = [{ id: state.group, label: (по[state.group] || {}).label || state.group }, { id: "__count", label: "Строк" }];
      (state.aggs || []).forEach(function (a) {
        out.push({ id: a.fn + ":" + a.col, label: ФУНКЦИИ[a.fn] + ": " + ((по[a.col] || {}).label || a.col) });
      });
      return out;
    }
    var выбор = state.cols && state.cols.length ? state.cols : все.map(function (c) { return c.id; });
    return выбор.filter(function (id) { return по[id]; }).map(function (id) { return по[id]; });
  }

  function построить(ds, rows, state) {
    var r = фильтровать(rows, state.filters, state.q);
    r = сгруппировать(r, state.group, state.aggs);
    r = сортировать(r, state.sort, state.dir);
    return { rows: r, cols: столбцы(ds, state) };
  }

  // Готовые отчёты — те же состояния, что собирает человек; ссылка /reports#preset=…
  var ГОТОВЫЕ = {
    suppliers_rfq: { ds: "suppliers", filters: [{ col: "sent", op: "gt", val: "0" }], sort: "sent", dir: "desc",
      cols: ["number", "name", "inn", "sent", "answered", "quoted", "silent", "no_outcome"] },
    suppliers_no_inn: { ds: "suppliers", filters: [{ col: "inn", op: "empty" }], sort: "sent", dir: "desc",
      cols: ["number", "name", "domain", "country", "wait_inn", "sent"] },
    positions_choice: { ds: "positions", filters: [{ col: "offers", op: "ge", val: "2" }], sort: "deals", dir: "desc",
      cols: ["n", "bn", "name", "co", "offers", "cmp", "deals", "drows"] },
    offers_by_company: { ds: "offers", group: "company", aggs: [{ fn: "distinct", col: "n" }, { fn: "sum", col: "rows" }],
      sort: "__count", dir: "desc" },
    brands_priced: { ds: "brands", sort: "priced", dir: "desc", cols: ["name", "any", "priced", "asked", "sups", "deals"] },
    pairs_top: { ds: "pairs", sort: "priced", dir: "desc" },
  };

  // Состояние ↔ адрес: отчёт пересылается ссылкой.
  function вАдрес(state) { return "#r=" + encodeURIComponent(JSON.stringify(state)); }
  function изАдреса(hash) {
    var h = String(hash || "").replace(/^#/, "");
    var m = /(?:^|&)preset=([a-z_]+)/.exec(h);
    if (m && ГОТОВЫЕ[m[1]]) return JSON.parse(JSON.stringify(ГОТОВЫЕ[m[1]]));
    var r = /(?:^|&)r=([^&]+)/.exec(h);
    if (!r) return null;
    try {
      var s = JSON.parse(decodeURIComponent(r[1]));
      return s && НАБОРЫ[s.ds] ? s : null;
    } catch (e) { return null; }
  }

  var ДВИЖОК = { НАБОРЫ: НАБОРЫ, ИСТОЧНИКИ: ИСТОЧНИКИ, ОПЕРАЦИИ: ОПЕРАЦИИ, ФУНКЦИИ: ФУНКЦИИ, ГОТОВЫЕ: ГОТОВЫЕ,
    фильтровать: фильтровать, сгруппировать: сгруппировать, сортировать: сортировать, вCSV: вCSV,
    столбцы: столбцы, построить: построить, вАдрес: вАдрес, изАдреса: изАдреса };
  root.KVR = ДВИЖОК;

  // ── ЭКРАН ────────────────────────────────────────────────────────────────
  if (typeof document === "undefined" || !document.getElementById("rep")) return;

  var $ = function (id) { return document.getElementById(id); };
  var узел = function (тег, класс, текст) {
    var e = document.createElement(тег);
    if (класс) e.className = класс;
    if (текст != null) e.textContent = текст;
    return e;
  };
  var ШАГ = 500;
  var кэш = {}, данные = {}, строки = [], показано = ШАГ, результат = null;
  var state = изАдреса(location.hash) || { ds: "suppliers" };

  var get = function (u) {
    return fetch(u, { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 403) throw new Error("нет права на раздел «Поставщики»");
      if (!r.ok) throw new Error("не удалось загрузить (" + r.status + ")");
      return r.json();
    });
  };
  function загрузить(ds) {
    var need = НАБОРЫ[ds].need;
    return Promise.all(need.map(function (src) {
      if (!кэш[src]) кэш[src] = ИСТОЧНИКИ[src](get).catch(function (e) { delete кэш[src]; throw e; });
      return кэш[src].then(function (v) { данные[src] = v; });
    })).then(function () { return НАБОРЫ[ds].разобрать(данные); });
  }

  function опции(sel, пары, текущий, пустая) {
    while (sel.firstChild) sel.removeChild(sel.firstChild);
    if (пустая != null) { var o0 = узел("option", null, пустая); o0.value = ""; sel.appendChild(o0); }
    пары.forEach(function (p) {
      var o = узел("option", null, p[1]); o.value = p[0];
      if (p[0] === текущий) o.selected = true;
      sel.appendChild(o);
    });
  }

  function поляНабора() { return НАБОРЫ[state.ds].fields; }

  function рисоватьУправление() {
    опции($("ds"), Object.keys(НАБОРЫ).map(function (k) { return [k, НАБОРЫ[k].title]; }), state.ds);
    // столбцы
    var cols = $("cols"); while (cols.firstChild) cols.removeChild(cols.firstChild);
    var выбраны = state.cols && state.cols.length ? state.cols : поляНабора().map(function (f) { return f[0]; });
    поляНабора().forEach(function (f) {
      var l = узел("label", "chk"), i = узел("input");
      i.type = "checkbox"; i.value = f[0]; i.checked = выбраны.indexOf(f[0]) >= 0;
      i.addEventListener("change", function () {
        state.cols = Array.prototype.slice.call(cols.querySelectorAll("input:checked")).map(function (x) { return x.value; });
        обновить();
      });
      l.appendChild(i); l.appendChild(узел("span", null, f[1])); cols.appendChild(l);
    });
    // фильтры
    var fl = $("filters"); while (fl.firstChild) fl.removeChild(fl.firstChild);
    (state.filters || []).forEach(function (f, idx) {
      var row = узел("div", "frow");
      var c = узел("select"); опции(c, поляНабора(), f.col);
      var o = узел("select"); опции(o, Object.keys(ОПЕРАЦИИ).map(function (k) { return [k, ОПЕРАЦИИ[k]]; }), f.op);
      var v = узел("input"); v.value = f.val == null ? "" : f.val; v.placeholder = "значение";
      v.hidden = f.op === "empty" || f.op === "filled";
      var x = узел("button", "lnk", "убрать"); x.type = "button";
      var upd = function () {
        state.filters[idx] = { col: c.value, op: o.value, val: v.value };
        v.hidden = o.value === "empty" || o.value === "filled"; обновить();
      };
      c.addEventListener("change", upd); o.addEventListener("change", upd); v.addEventListener("input", upd);
      x.addEventListener("click", function () { state.filters.splice(idx, 1); рисоватьУправление(); обновить(); });
      row.appendChild(c); row.appendChild(o); row.appendChild(v); row.appendChild(x); fl.appendChild(row);
    });
    опции($("group"), поляНабора(), state.group, "— без группировки —");
    var ag = $("aggs"); while (ag.firstChild) ag.removeChild(ag.firstChild);
    $("agg-box").hidden = !state.group;
    $("cols-box").hidden = !!state.group;
    (state.aggs || []).forEach(function (a, idx) {
      var row = узел("div", "frow");
      var fn = узел("select"); опции(fn, Object.keys(ФУНКЦИИ).map(function (k) { return [k, ФУНКЦИИ[k]]; }), a.fn);
      var c = узел("select"); опции(c, поляНабора(), a.col);
      var x = узел("button", "lnk", "убрать"); x.type = "button";
      var upd = function () { state.aggs[idx] = { fn: fn.value, col: c.value }; обновить(); };
      fn.addEventListener("change", upd); c.addEventListener("change", upd);
      x.addEventListener("click", function () { state.aggs.splice(idx, 1); рисоватьУправление(); обновить(); });
      row.appendChild(fn); row.appendChild(c); row.appendChild(x); ag.appendChild(row);
    });
    $("q").value = state.q || "";
  }

  function рисоватьТаблицу() {
    var t = $("tbl"); while (t.firstChild) t.removeChild(t.firstChild);
    var head = узел("thead"), tr = узел("tr");
    результат.cols.forEach(function (c) {
      var th = узел("th"), b = узел("button", "sort", c.label); b.type = "button";
      if (state.sort === c.id) { th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending"); b.textContent += state.dir === "asc" ? " ↑" : " ↓"; }
      b.addEventListener("click", function () {
        state.dir = state.sort === c.id && state.dir === "desc" ? "asc" : "desc"; state.sort = c.id; обновить();
      });
      th.appendChild(b); tr.appendChild(th);
    });
    head.appendChild(tr); t.appendChild(head);
    var body = узел("tbody"), fmt = function (v) {
      if (v == null || v === "") return "";
      if (typeof v === "number") return Math.round(v * 100) / 100 === v ? v.toLocaleString("ru-RU") : v.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
      return String(v);
    };
    результат.rows.slice(0, показано).forEach(function (r) {
      var tr2 = узел("tr");
      результат.cols.forEach(function (c) {
        var v = r[c.id], td = узел("td", typeof v === "number" ? "num" : null, fmt(v));
        tr2.appendChild(td);
      });
      body.appendChild(tr2);
    });
    t.appendChild(body);
    var всего = результат.rows.length;
    $("count").textContent = всего.toLocaleString("ru-RU") + " строк" +
      (state.group ? " (групп)" : "") + " из " + строки.length.toLocaleString("ru-RU") + " в наборе";
    $("more").hidden = всего <= показано;
    $("more").textContent = "Показать ещё " + Math.min(ШАГ, всего - показано).toLocaleString("ru-RU") + " из " + всего.toLocaleString("ru-RU");
    $("print-title").textContent = ($("title").value || НАБОРЫ[state.ds].title) + " · " + new Date().toLocaleDateString("ru-RU");
  }

  function обновить() {
    if (!строки) return;
    показано = ШАГ;
    результат = построить(state.ds, строки, state);
    try { history.replaceState(null, "", вАдрес(state)); } catch (e) { /* адрес — удобство */ }
    рисоватьТаблицу();
  }

  function сменитьНабор(ds, сохранить) {
    if (!сохранить) state = { ds: ds };
    $("status").textContent = "Загружаю…"; $("status").hidden = false; $("result").hidden = true;
    рисоватьУправление();
    загрузить(state.ds).then(function (rows) {
      строки = rows;
      $("status").hidden = true; $("result").hidden = false;
      if (!rows.length) { $("status").hidden = false; $("status").textContent = "Нет данных: снимок ещё не опубликован."; }
      обновить();
    }).catch(function (e) {
      $("status").hidden = false; $("status").textContent = "Не удалось загрузить: " + e.message;
    });
  }

  $("ds").addEventListener("change", function () { сменитьНабор($("ds").value); });
  $("q").addEventListener("input", function () { state.q = $("q").value; обновить(); });
  $("add-filter").addEventListener("click", function () {
    (state.filters = state.filters || []).push({ col: поляНабора()[0][0], op: "contains", val: "" });
    рисоватьУправление();
  });
  $("group").addEventListener("change", function () {
    state.group = $("group").value || undefined; state.aggs = state.group ? (state.aggs || []) : [];
    if (state.group) state.sort = "__count", state.dir = "desc";
    рисоватьУправление(); обновить();
  });
  $("add-agg").addEventListener("click", function () {
    (state.aggs = state.aggs || []).push({ fn: "sum", col: поляНабора()[0][0] }); рисоватьУправление(); обновить();
  });
  $("more").addEventListener("click", function () { показано += ШАГ; рисоватьТаблицу(); });
  $("csv").addEventListener("click", function () {
    var blob = new Blob([вCSV(результат.rows, результат.cols)], { type: "text/csv;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = ($("title").value || НАБОРЫ[state.ds].title).replace(/[\\/:*?"<>|]+/g, " ") + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  });
  $("print").addEventListener("click", function () { показано = результат.rows.length; рисоватьТаблицу(); window.print(); });
  $("link").addEventListener("click", function () {
    var u = location.origin + location.pathname + вАдрес(state);
    var done = function () { $("link").textContent = "Ссылка скопирована"; setTimeout(function () { $("link").textContent = "Ссылка на отчёт"; }, 2000); };
    if (navigator.clipboard) navigator.clipboard.writeText(u).then(done, function () { prompt("Ссылка на отчёт", u); });
    else prompt("Ссылка на отчёт", u);
  });
  window.addEventListener("hashchange", function () {
    var s = изАдреса(location.hash);
    if (s && JSON.stringify(s) !== JSON.stringify(state)) { var прежний = state.ds; state = s; сменитьНабор(s.ds, true); if (прежний === s.ds) рисоватьУправление(); }
  });
  сменитьНабор(state.ds, true);
})(typeof window !== "undefined" ? window : globalThis);
