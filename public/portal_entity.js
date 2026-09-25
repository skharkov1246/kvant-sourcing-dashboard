// КАРТОЧКИ ПОРТАЛА — страница /p: код, бренд, поставщик (шаг 2 «одной
// стартовой страницы», 25.09.2026).
//
// АДРЕС. /p#code=<ключ кода>, /p#brand=<ключ бренда>, /p#supplier=<KV-S…>. Адрес
// после «#» — единственный вход: страница слушает его смену и перерисовывает
// карточку, поэтому ссылка карточки на другую карточку не перезагружает
// страницу, а «назад» браузера возвращает прежнюю.
//
// ПРАВИЛА НОМЕНКЛАТУРЫ НА ЭКРАНЕ (PDF владельцу 24.09.2026):
//   · код и бренд — всегда два соседних столбца «Код» и «Бренд»; заголовок
//     карточки кода — «Код · Бренд»;
//   · бренд реестра — ссылкой на его карточку; слово, которого в реестре нет, —
//     приглушённым словом, а не брендом; источник бренда подписан, «спорно» —
//     пометкой рядом и списком того, кто что называет;
//   · аналоги — отдельной таблицей от оригинала, с причиной;
//   · только человеческие имена: номера справочника и сжатые ключи сюда не
//     приходят (их отсекает база и закрытый список полей воркера); нет имени —
//     «имя не известно», а не ключ;
//   · количество, которое не читается, — «не знаем», а не число.
//
// ПРЕЖНИЕ СТРАНИЦЫ НЕ ЗАМЕНЯЮТСЯ: у каждой карточки есть ссылки «в прежнем
// разделе» — /nomenclature#k=, /brands#b=, /brands#c=, /suppliers#e=.
//
// Всё выводится через textContent: строка из базы разметкой не становится.
(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var место = document.getElementById("card");
  if (!место) return;

  var BXLINK = "https://kvantpro.bitrix24.ru/crm/company/details/";
  var API = { code: "/api/portal/code?k=", brand: "/api/portal/brand?b=", supplier: "/api/portal/supplier?s=" };
  var ИСТОЧНИК_ИМЕНИ = {
    "bitrix:title": "карточка компании в Битриксе", "bitrix:requisite": "реквизиты в Битриксе",
    "написание": "написание из сведения реестра", "реестр": "реестр поставщиков", "домен": "домен сайта"
  };
  var ЧАСТИ = {
    "предложения": "предложения", "аналоги": "аналоги", "машины": "машины и узлы",
    "кому ещё писать": "кому ещё писать", "поставщики": "поставщиков", "бренды": "бренды", "коды": "коды"
  };

  // ── мелочи ─────────────────────────────────────────────────────────────────
  function узел(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }
  function ссылка(href, text, cls) {
    var a = узел("a", cls, text);
    a.setAttribute("href", href);
    return a;
  }
  function число(n) {
    if (typeof n !== "number" || !isFinite(n)) return "";
    var s = String(Math.round(n * 100) / 100).split(".");
    return s[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (s[1] ? "," + s[1] : "");
  }
  function к(v) { return encodeURIComponent(String(v)); }
  function адрес_кода(code) { return "/p#code=" + к(code); }
  function адрес_бренда(key) { return "/p#brand=" + к(key); }
  function адрес_поставщика(id) { return "/p#supplier=" + к(id); }

  // Код: ключ есть — ссылка на карточку; ключа нет (код отвергнут правилом
  // правдоподобия) — просто написание, без ссылки.
  function код(code, written) {
    if (code) return ссылка(адрес_кода(code), written || code);
    var s = узел("span", null, written || "—");
    if (written) s.setAttribute("title", "не код детали: карточки нет");
    return s;
  }
  function бренд(b) {
    if (!b || !b.name) return узел("span", "none", "не назван");
    if (b.key) return ссылка(адрес_бренда(b.key), b.name);
    var w = узел("span", "word", b.name);
    w.setAttribute("title", "слово из документа; в реестре брендов не найдено");
    return w;
  }
  function компания(c) {
    if (!c || !c.id) return узел("span", "none", "не сведена с реестром");
    var box = узел("span");
    box.appendChild(ссылка(адрес_поставщика(c.id), c.name || "имя не известно"));
    if (c.number) box.appendChild(узел("span", "src", c.number));
    return box;
  }
  function деньги(price, cur) {
    if (typeof price !== "number") return узел("span", "none", "—");
    return узел("span", "num", число(price) + " " + (cur || "(валюта не указана)"));
  }
  function количество(qty, unit, скрыто) {
    if (typeof qty === "number") return узел("span", "num", число(qty) + (unit ? " " + unit : ""));
    if (скрыто) {
      var s = узел("span", "warn", "не знаем");
      s.setAttribute("title", "количество не сходится с ценой и суммой или больше миллиона");
      return s;
    }
    return узел("span", "none", "—");
  }
  function месяц(m, src) {
    var box = узел("span");
    box.appendChild(m ? узел("span", "num", m) : узел("span", "none", "не знаем"));
    if (src) box.appendChild(узел("span", "src", src === "нет" ? "в КП и на карточке даты нет" : src));
    return box;
  }

  // Таблица: колонки [подпись, функция строки → узел или текст]. Подпись идёт в
  // data-l ячейки: на узком экране строка встаёт карточкой, подпись — слева.
  function таблица(колонки, строки) {
    var wrap = узел("div", "table-wrap");
    var t = узел("table", "st");
    var thead = узел("thead");
    var hr = узел("tr");
    колонки.forEach(function (c) { hr.appendChild(узел("th", null, c[0])); });
    thead.appendChild(hr);
    t.appendChild(thead);
    var tb = узел("tbody");
    строки.forEach(function (r) {
      var tr = узел("tr");
      колонки.forEach(function (c) {
        var td = узел("td");
        td.setAttribute("data-l", c[0]);
        var v = c[1](r);
        if (v && typeof v === "object") td.appendChild(v);
        else td.textContent = v === undefined || v === null || v === "" ? "—" : String(v);
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    wrap.appendChild(t);
    return wrap;
  }
  function раздел(заголовок, счёт) {
    var s = узел("section");
    var h = узел("h2", null, заголовок);
    if (счёт !== undefined && счёт !== null && счёт !== "") h.appendChild(узел("span", null, String(счёт)));
    s.appendChild(h);
    return s;
  }
  function итог(значение, подпись) {
    var t = узел("div", "total");
    if (значение && typeof значение === "object") {
      var st = узел("strong");
      st.appendChild(значение);
      t.appendChild(st);
    } else {
      t.appendChild(узел("strong", null, значение === undefined || значение === null || значение === "" ? "—" : значение));
    }
    t.appendChild(узел("span", null, подпись));
    return t;
  }
  function факты(пары) {
    var dl = узел("dl");
    пары.forEach(function (p) {
      if (!p) return;
      var d = узел("div");
      d.appendChild(узел("dt", null, p[0]));
      var dd = узел("dd");
      if (p[1] && typeof p[1] === "object") dd.appendChild(p[1]);
      else dd.textContent = p[1] === undefined || p[1] === null || p[1] === "" ? "—" : String(p[1]);
      d.appendChild(dd);
      dl.appendChild(d);
    });
    return dl;
  }
  function прежние(ссылки) {
    var box = узел("span", "old");
    ссылки.forEach(function (x) { box.appendChild(ссылка(x[0], x[1])); });
    return box;
  }
  function шапка(вид, h1, lead) {
    var h = узел("header", "hero");
    h.appendChild(узел("p", "eyebrow", вид));
    h.appendChild(h1);
    if (lead) h.appendChild(lead);
    return h;
  }
  function не_успели(v) {
    if (!v.partial || !v.partial.length) return null;
    return узел("p", "note warn", "Не успели посчитать: " + v.partial.map(function (x) { return ЧАСТИ[x] || x; }).join(", ")
      + ". Обновите страницу — второй запрос обычно быстрее.");
  }
  function пусто(текст) { return узел("p", "note", текст); }
  function машина(m, library) {
    var chip = узел("span", "chip");
    var href = library ? (m.segment ? "/library#segment=" + к(m.segment) : "/library") : null;
    chip.appendChild(href ? ссылка(href, m.name) : узел("span", null, m.name));
    var под = [m.kind, typeof m.parts === "number" ? "деталей " + число(m.parts) : null].filter(Boolean).join(" · ");
    if (под) chip.appendChild(узел("span", "src", под));
    if (m.brand && m.brand.name) {
      var бр = узел("span", "src");
      бр.appendChild(бренд(m.brand));
      chip.appendChild(бр);
    }
    if (!library) chip.appendChild(узел("span", "src", "библиотека закрыта правом"));
    return chip;
  }

  // ── карточка кода ──────────────────────────────────────────────────────────
  function предложения(список, аналоги) {
    var колонки = [
      ["Компания", function (o) { return компания(o.company); }],
      ["Код", function (o) { return узел("span", null, o.written || "—"); }],
      ["Бренд", function (o) { return бренд(o.brand); }],
      ["Цена", function (o) { return деньги(o.price, o.currency); }],
      ["Кол-во", function (o) { return количество(o.qty, o.unit, o.qty_hidden); }],
      ["Сумма", function (o) { return typeof o.total === "number" ? деньги(o.total, o.currency) : узел("span", "none", "—"); }],
      ["Базис", function (o) { return o.basis; }],
      ["Месяц квотации", function (o) { return месяц(o.month, o.month_src); }]
    ];
    if (аналоги) колонки.push(["Почему аналог", function (o) { return o.why; }]);
    return таблица(колонки, список);
  }

  function карточка_кода(v) {
    var out = [];
    var h1 = узел("h1");
    h1.appendChild(узел("span", null, v.written || v.key));
    h1.appendChild(узел("span", "sep", "·"));
    h1.appendChild(бренд(v.brand));
    var lead = узел("p", "lead");
    if (v.brand && v.brand.name) {
      lead.appendChild(узел("span", null, "Бренд по источнику: " + (v.brand.src || "—")));
      if (v.brand.disputed === true) lead.appendChild(узел("span", "tag", "спорно"));
      if (v.brand.disputed === null && v.brand.key === null) {
        lead.appendChild(узел("span", "tag", "не бренд реестра"));
      }
    } else {
      lead.textContent = "Бренд не назван ни каталогом, ни спецификацией, ни КП.";
    }
    out.push(шапка("Код", h1, lead));
    if (v.brand && v.brand.disputed === true && v.brands && v.brands.length) {
      out.push(узел("p", "note warn", "Источники называют разные бренды: " + v.brands.map(function (b) {
        return b.name + " — " + (b.sources || []).join(", ");
      }).join("; ") + "."));
    }
    var d = v.demand || {};
    out.push(факты([
      ["Наименование", v.name],
      ["Наш номер KV", v.kv_no || "не выдан"],
      ["В каталоге", v.catalog ? "да" : "нет"],
      ["Бренды по источникам", v.brands && v.brands.length ? v.brands.map(function (b) {
        return b.name + (b.key ? "" : " (слово)") + " — " + (b.sources || []).join(", ");
      }).join("; ") : "—"],
      ["В прежнем разделе", прежние([["/nomenclature#k=" + к(v.key), "номенклатура"],
                                    ["/brands#c=" + к(v.key), "бренды: код и цены"]])]
    ]));

    var спрос = раздел("Спрос");
    var totals = узел("div", "totals");
    totals.appendChild(итог(число(d.deals || 0) + (d.capped ? "+" : ""), "сделок спрашивали"));
    totals.appendChild(итог(число(d.rows || 0) + (d.capped ? "+" : ""), "строк спроса"));
    totals.appendChild(итог(typeof d.qty === "number" ? число(d.qty) + (d.unit ? " " + d.unit : "")
      : (d.rows ? (d.units > 1 ? "в разных единицах" : "не знаем") : "—"), "единиц спрошено"));
    totals.appendChild(итог(d.last_month || "—", "последняя строка занесена"));
    totals.appendChild(итог(typeof d.customers === "number" ? число(d.customers) : "не знаем", "заказчиков"));
    спрос.appendChild(totals);
    var пояснения = [];
    if (d.qty_hidden) пояснения.push("строк с нечитаемым количеством (больше миллиона): " + число(d.qty_hidden) + " — в сумму не вошли");
    пояснения.push("заказчиков не знаем: связи «сделка → заказчик» в базе нет");
    спрос.appendChild(пусто(пояснения.join("; ") + "."));
    out.push(спрос);

    var o = v.offers || {};
    var ор = раздел("Предложения оригинала", o.original_n ? число(o.original_n) : "");
    if (o.brand_judged === false && (o.rows || 0) > 0) {
      ор.appendChild(пусто("Бренд позиции не определён по реестру: строки делятся на оригинал и аналог только по слову поставщика."));
    }
    ор.appendChild(o.original && o.original.length ? предложения(o.original, false) : пусто("Предложений оригинала нет."));
    if (o.capped) ор.appendChild(пусто("Показаны последние строки; всего их больше 2 000."));
    out.push(ор);
    var ан = раздел("Предложения аналогов", o.analog_n ? число(o.analog_n) : "");
    ан.appendChild(o.analog && o.analog.length ? предложения(o.analog, true) : пусто("Предложений аналогов нет."));
    out.push(ан);

    var кат = раздел("Аналоги по каталогу", v.analogs && v.analogs.length ? число(v.analogs.length) : "");
    кат.appendChild(v.analogs && v.analogs.length ? таблица([
      ["Код", function (a) { return код(a.code, a.written); }],
      ["Бренд", function (a) { return бренд(a.brand); }],
      ["Вид связи", function (a) { return a.kind; }]
    ], v.analogs) : пусто(v.catalog ? "В каталоге аналогов к этой детали нет." : "Детали нет в каталоге — аналоги из каталога не известны."));
    if (v.analog_of && v.analog_of.length) {
      кат.appendChild(пусто("Этот код сам указан в каталоге как номер или аналог к:"));
      кат.appendChild(таблица([
        ["Код", function (a) { return код(a.code, a.written); }],
        ["Бренд", function (a) { return бренд(a.brand); }],
        ["Вид связи", function (a) { return a.kind; }]
      ], v.analog_of));
    }
    out.push(кат);

    var мш = раздел("Машины и узлы");
    if ((v.machines && v.machines.length) || (v.units && v.units.length)) {
      var chips = узел("div", "chips");
      (v.machines || []).forEach(function (m) { chips.appendChild(машина(m, v.library)); });
      (v.units || []).forEach(function (u) {
        var chip = узел("span", "chip");
        chip.appendChild(узел("span", null, u.name));
        var под = [u.parent ? "в узле «" + u.parent + "»" : null, u.crit ? "критичность " + u.crit : null].filter(Boolean).join(" · ");
        if (под) chip.appendChild(узел("span", "src", под));
        chips.appendChild(chip);
      });
      мш.appendChild(chips);
    } else {
      мш.appendChild(пусто(v.catalog ? "У детали в каталоге не указаны ни машина, ни узел." : "Детали нет в каталоге — машина и узел не известны."));
    }
    out.push(мш);

    var кому = раздел("Кому ещё писать", v.write_to && v.write_to.length ? число(v.write_to.length) : "");
    if (v.brand && v.brand.key) {
      кому.appendChild(пусто("Давали цену по бренду «" + v.brand.name + "» на другие коды, а на этот — нет."));
      кому.appendChild(v.write_to && v.write_to.length ? таблица([
        ["Компания", function (w) { return компания(w.company); }],
        ["Кодов бренда", function (w) { return узел("span", "num", число(w.codes)); }],
        ["Строк КП", function (w) { return узел("span", "num", число(w.rows)); }],
        ["Последний месяц", function (w) { return месяц(w.last_month); }]
      ], v.write_to) : пусто("Других поставщиков по этому бренду в базе нет."));
    } else {
      кому.appendChild(пусто("Бренд позиции не определён по реестру — подобрать поставщиков по бренду нельзя."));
    }
    out.push(кому);
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: (v.written || v.key) + " · " + (v.brand && v.brand.name ? v.brand.name : "бренд не назван"), nodes: out };
  }

  // ── карточка бренда ────────────────────────────────────────────────────────
  function карточка_бренда(v) {
    var out = [];
    var свой = { key: v.key, name: v.name };
    var подпись = [v.owner ? "владелец — " + v.owner : null, v.country,
      v.former_names ? "прежде: " + v.former_names : null].filter(Boolean).join(" · ");
    out.push(шапка("Бренд", узел("h1", null, v.name), подпись ? узел("p", "lead", подпись) : null));
    var d = v.demand || {};
    out.push(факты([
      ["Страна", v.country], ["Владелец", v.owner], ["Прежние имена", v.former_names],
      ["Написания в данных", v.spellings && v.spellings.length ? v.spellings.join(", ") : "—"],
      ["В прежнем разделе", прежние([["/brands#b=" + к(v.key), "бренды и коды"]])]
    ]));
    var спрос = раздел("Спрос по бренду");
    var totals = узел("div", "totals");
    totals.appendChild(итог(число(d.deals || 0) + (d.capped ? "+" : ""), "сделок, не меньше"));
    totals.appendChild(итог(число(d.rows || 0) + (d.capped ? "+" : ""), "строк спроса, не меньше"));
    totals.appendChild(итог(число(d.codes || 0), "кодов спрашивали"));
    totals.appendChild(итог(число(d.registry_rows || 0), "строк спроса по реестру брендов"));
    спрос.appendChild(totals);
    спрос.appendChild(пусто("Счёт — по дословным написаниям бренда; ячейка вида «SKF, FAG» сюда не входит, поэтому «не меньше». "
      + "Строки по реестру посчитаны при засеве и такие ячейки учитывают."));
    out.push(спрос);

    var коды = раздел("Коды бренда");
    var cols = узел("div", "cols");
    var лев = узел("div");
    лев.appendChild(узел("p", "note", "по спросу"));
    лев.appendChild(v.codes_demand && v.codes_demand.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function () { return узел("span", null, v.name); }],
      ["Сделок", function (c) { return узел("span", "num", число(c.deals)); }],
      ["Строк", function (c) { return узел("span", "num", число(c.rows)); }]
    ], v.codes_demand) : пусто("Спроса с этим брендом в строке нет."));
    var прав = узел("div");
    прав.appendChild(узел("p", "note", "по предложениям"));
    прав.appendChild(v.codes_offers && v.codes_offers.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function () { return узел("span", null, v.name); }],
      ["Строк КП", function (c) { return узел("span", "num", число(c.rows)); }],
      ["Поставщиков", function (c) { return узел("span", "num", число(c.suppliers)); }],
      ["Последний месяц", function (c) { return месяц(c.last_month); }]
    ], v.codes_offers) : пусто("Предложений по бренду нет."));
    cols.appendChild(лев);
    cols.appendChild(прав);
    коды.appendChild(cols);
    out.push(коды);

    var cat = v.catalog || {};
    var кат = раздел("В каталоге", cat.parts ? число(cat.parts) + " дет." : "");
    кат.appendChild(cat.list && cat.list.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function () { return узел("span", null, v.name); }],
      ["Наименование", function (c) { return c.name; }],
      ["Наш номер KV", function (c) { return c.kv_no || "—"; }]
    ], cat.list) : пусто("Деталей бренда в каталоге нет."));
    if (cat.parts > (cat.list || []).length) кат.appendChild(пусто("Показаны " + число((cat.list || []).length) + " из " + число(cat.parts) + "."));
    out.push(кат);

    var мш = раздел("Машины бренда", v.machines && v.machines.length ? число(v.machines.length) : "");
    if (v.machines && v.machines.length) {
      var chips = узел("div", "chips");
      v.machines.forEach(function (m) { chips.appendChild(машина(m, v.library)); });
      мш.appendChild(chips);
    } else {
      мш.appendChild(пусто("Машин, чей изготовитель — этот бренд, в справочнике нет."));
    }
    out.push(мш);

    var o = v.offers || {};
    var пс = раздел("Поставщики, дававшие цену", o.suppliers ? число(o.suppliers) : "");
    пс.appendChild(v.suppliers && v.suppliers.length ? таблица([
      ["Компания", function (s) { return компания(s.company); }],
      ["Кодов", function (s) { return узел("span", "num", число(s.codes)); }],
      ["Строк КП", function (s) { return узел("span", "num", число(s.rows)); }],
      ["Последний месяц", function (s) { return месяц(s.last_month); }]
    ], v.suppliers) : пусто("Цен по бренду от сведённых с реестром поставщиков нет."));
    if (o.rows_unresolved) пс.appendChild(пусто("Ещё строк КП от компаний, не сведённых с реестром: " + число(o.rows_unresolved) + "."));
    out.push(пс);

    var ан = раздел("Аналоги к кодам бренда", v.analogs && v.analogs.length ? число(v.analogs.length) : "");
    ан.appendChild(v.analogs && v.analogs.length ? таблица([
      ["Код", function (a) { return код(a.code, a.written); }],
      ["Бренд", function () { return бренд(свой); }],
      ["Аналог", function (a) { return код(a.alt_code, a.alt_written); }],
      ["Бренд аналога", function (a) { return бренд(a.brand); }],
      ["Вид связи", function (a) { return a.kind; }]
    ], v.analogs) : пусто("Аналогов других брендов к кодам бренда в каталоге нет."));
    out.push(ан);
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: v.name + " · бренд", nodes: out };
  }

  // ── карточка поставщика ────────────────────────────────────────────────────
  function карточка_поставщика(v) {
    var out = [];
    var lead = null;
    if (v.merged_from) lead = узел("p", "lead", "Номер " + v.merged_from + " слит в эту компанию — показана она.");
    var h1 = узел("h1", v.name ? null : "none", v.name || "Имя не известно");
    out.push(шапка("Поставщик", h1, lead));
    var bx = узел("span");
    (v.bitrix || []).forEach(function (id) {
      var a = ссылка(BXLINK + к(id) + "/", "№ " + id + " ↗");
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      bx.appendChild(a);
      bx.appendChild(узел("span", null, " "));
    });
    out.push(факты([
      ["Номер KV-S", v.number || "номер не выдан"],
      ["ИНН", v.inn && v.inn.length ? v.inn.join(", ") : "—"],
      ["Домен", v.domains && v.domains.length ? v.domains.join(", ") : "—"],
      ["Страна, город", [v.country, v.city].filter(Boolean).join(", ")],
      ["Карточка в Битриксе", v.bitrix && v.bitrix.length ? bx : "—"],
      ["Имя взято из", v.name_src ? (ИСТОЧНИК_ИМЕНИ[v.name_src] || v.name_src) : "—"],
      ["В прежнем разделе", прежние([["/suppliers#e=" + к(v.id), "поставщики"]])]
    ]));
    var q = v.quotes || {}, r = v.rfq;
    var зп = раздел("Запросы и КП");
    var totals = узел("div", "totals");
    if (r) {
      totals.appendChild(итог(число(r.sent || 0), "запросов отправлено"));
      totals.appendChild(итог(число(r.answered || 0), "ответили"));
      totals.appendChild(итог(число(r.quoted || 0), "дали КП"));
      totals.appendChild(итог(число(r.no_outcome || 0), "без исхода"));
    }
    totals.appendChild(итог(число(q.rows || 0) + (q.capped ? "+" : ""), "строк цены в КП"));
    totals.appendChild(итог(число(q.cards || 0), "запросов с разобранной ценой"));
    totals.appendChild(итог(число(q.codes || 0), "кодов с ценой"));
    totals.appendChild(итог(q.last_month || "—", "последний месяц квотации"));
    зп.appendChild(totals);
    if (!r) зп.appendChild(пусто("Отзывчивость по запросам ещё не посчитана."));
    out.push(зп);

    var бр = раздел("Бренды", v.brands && v.brands.length ? число(v.brands.length) : "");
    бр.appendChild(v.brands && v.brands.length ? таблица([
      ["Бренд", function (b) { return бренд(b.brand); }],
      ["Кодов", function (b) { return узел("span", "num", число(b.codes)); }],
      ["Строк КП", function (b) { return узел("span", "num", число(b.rows)); }],
      ["Последний месяц", function (b) { return месяц(b.last_month); }]
    ], v.brands) : пусто("Бренд в предложениях не назван."));
    out.push(бр);
    var кд = раздел("Коды с ценами", v.codes && v.codes.length ? число(v.codes.length) : "");
    кд.appendChild(v.codes && v.codes.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function (c) { return бренд(c.brand); }],
      ["Последняя цена", function (c) { return деньги(c.price, c.currency); }],
      ["Кол-во", function (c) { return количество(c.qty, c.unit, false); }],
      ["Месяц квотации", function (c) { return месяц(c.month); }],
      ["Предложений", function (c) { return узел("span", "num", число(c.offers)); }]
    ], v.codes) : пусто("Разобранных цен от этой компании нет."));
    out.push(кд);
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: (v.name || v.id) + " · поставщик", nodes: out };
  }

  // ── отказ словами ──────────────────────────────────────────────────────────
  function отказ(status, v, что) {
    var err = (v && v.error) || "";
    var h = "Карточка не открылась", p = "База не ответила. Попробуйте ещё раз.";
    if (status === 403) { h = "Нет доступа"; p = "Карточки открываются по праву «Поставщики» — его выдаёт владелец."; }
    else if (err === "not_a_code") { h = "Это не код детали"; p = "Так пишут марку материала, размер или стандарт — карточки у такого «кода» нет."; }
    else if (err === "not_found") {
      h = "Не нашлось";
      p = что === "code" ? "Этого кода нет ни в каталоге, ни в спросе, ни в КП."
        : что === "brand" ? "Такого бренда нет в реестре брендов." : "Такого поставщика нет в реестре.";
    }
    else if (err === "brands_not_installed") { h = "Реестр брендов не установлен"; p = "Карточка бренда появится, когда в базе будет реестр брендов."; }
    else if (err === "entity_not_installed") { h = "Карточки ещё не установлены в базе"; p = "Нужна схема карточек портала — скажите владельцу."; }
    else if (err === "search_key_missing") { h = "Карточки не подключены"; p = "У портала нет ключа базы."; }
    else if (err === "invalid_key") { h = "Неверный адрес карточки"; p = "Откройте карточку из строки поиска сверху."; }
    var box = узел("div", "status error");
    box.appendChild(узел("h2", null, h));
    box.appendChild(узел("p", null, p));
    return box;
  }

  function заменить(nodes) {
    место.textContent = "";
    nodes.forEach(function (n) { место.appendChild(n); });
  }

  var номер = 0;
  function открыть() {
    var h = typeof location !== "undefined" ? String(location.hash || "") : "";
    var m = h.match(/^#(code|brand|supplier)=(.+)$/);
    if (!m) {
      // Без адреса карточки (или «#» стёрли) — подсказка, а не прежняя карточка.
      ++номер;
      var тихо = узел("div", "status");
      тихо.appendChild(узел("h2", null, "Карточка портала"));
      тихо.appendChild(узел("p", null, "Код, бренд или поставщик открываются из строки поиска сверху; "
        + "каждая ссылка карточки ведёт на карточку того, что в ней названо."));
      заменить([тихо]);
      document.title = "Карточка · КВАНТ";
      return;
    }
    var значение;
    try { значение = decodeURIComponent(m[2]); } catch (e) { значение = m[2]; }
    var что = m[1];
    var мой = ++номер;
    var ждём = узел("div", "status");
    ждём.appendChild(узел("p", null, "Открываем…"));
    заменить([ждём]);
    fetch(API[что] + к(значение), { headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (v) { return { status: r.status, v: v }; });
      })
      .then(function (x) {
        if (мой !== номер) return;       // пришёл ответ на прежний адрес
        if (x.status !== 200 || !x.v || x.v.error) { заменить([отказ(x.status, x.v, что)]); return; }
        var к_ = что === "code" ? карточка_кода(x.v) : что === "brand" ? карточка_бренда(x.v) : карточка_поставщика(x.v);
        заменить(к_.nodes);
        document.title = к_.title + " · КВАНТ";
        if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
      })
      .catch(function () {
        if (мой !== номер) return;
        заменить([отказ(0, null, что)]);
      });
  }

  if (typeof window !== "undefined" && window.addEventListener) window.addEventListener("hashchange", открыть);
  открыть();
})();
