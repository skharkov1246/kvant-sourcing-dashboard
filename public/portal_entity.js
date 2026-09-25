// КАРТОЧКИ ПОРТАЛА — страница /p: код, бренд, поставщик (шаг 2 «одной
// стартовой страницы», 25.09.2026), машина и узел (шаг 3).
//
// АДРЕС. /p#code=<ключ кода>, /p#brand=<ключ бренда>, /p#supplier=<KV-S…>,
// /p#model=<ключ машины>, /p#unit=<ключ узла>. Адрес
// после «#» — единственный вход: страница слушает его смену и перерисовывает
// карточку, поэтому ссылка карточки на другую карточку не перезагружает
// страницу, а «назад» браузера возвращает прежнюю.
//
// МАШИНА И УЗЕЛ — данные библиотеки, и открываются они по её праву (сайт
// knowledge). Поэтому ссылка на машину или узел с карточек кода и бренда
// ставится, только когда воркер сказал library: true; без права — имя текстом
// и пометка «библиотека закрыта правом». Узлы машины показываются двумя
// связями порознь: по деталям каталога (измерено) и типовым деревом
// направления (ГТУ, ГПУ) — см. portal_entity_schema.sql, раздел 11.
//
// ПРАВИЛА НОМЕНКЛАТУРЫ НА ЭКРАНЕ (PDF владельцу 24.09.2026):
//   · код и бренд — всегда два соседних столбца «Код» и «Бренд»; заголовок
//     карточки кода — «Код · Бренд»;
//   · бренд реестра — ссылкой на его карточку; слово, которого в реестре нет, —
//     приглушённым словом с пометкой «нет в реестре брендов», а не брендом;
//     источник бренда подписан, «спорно» — пометкой рядом и списком того, кто
//     что называет;
//   · аналоги — отдельной таблицей от оригинала, с причиной; строка оригинала,
//     где бренд в КП не назван или написан не брендом реестра, — с пометкой
//     «оригинал не подтверждён»;
//   · только человеческие имена: номера справочника и сжатые ключи сюда не
//     приходят (их отсекает база и закрытый список полей воркера); нет имени —
//     «имя не известно», а не ключ; номер карточки Битрикса — только в адресе
//     ссылки «карточка в Битриксе ↗», не в тексте;
//   · количество, которое не читается, — «не знаем», а не число; сумма при
//     нём — тоже «не знаем»;
//   · условие КП без значения — с причиной: «в КП не указано» (можно спросить
//     поставщика) и «разбор не дошёл» (наш недочёт) — разные вещи, как на
//     /nomenclature.
//
// ПРЕЖНИЕ СТРАНИЦЫ НЕ ЗАМЕНЯЮТСЯ: у каждой карточки есть ссылки «в прежнем
// разделе» — /nomenclature#k=, /brands#b=, /brands#c=, /suppliers#e=, у машины
// и узла — /library#segment= и /library#section=component.
//
// Всё выводится через textContent: строка из базы разметкой не становится.
(function () {
  "use strict";
  if (typeof document === "undefined") return;
  var место = document.getElementById("card");
  if (!место) return;

  var BXLINK = "https://kvantpro.bitrix24.ru/crm/company/details/";
  // Карточка запроса поставщику — смарт-процесс «Запросы поставщикам» (СП-166).
  var BXRFQ = "https://kvantpro.bitrix24.ru/crm/type/166/details/";
  var API = { code: "/api/portal/code?k=", brand: "/api/portal/brand?b=", supplier: "/api/portal/supplier?s=",
              model: "/api/portal/model?id=", unit: "/api/portal/unit?id=" };
  var ИСТОЧНИК_ИМЕНИ = {
    "bitrix:title": "карточка компании в Битриксе", "bitrix:requisite": "реквизиты в Битриксе",
    "написание": "из справочника поставщиков", "реестр": "справочник поставщиков", "домен": "домен сайта"
  };
  var ЧАСТИ = {
    "предложения": "предложения", "аналоги": "аналоги", "машины": "машины и узлы", "кто делает": "кто делает деталь",
    "кому ещё писать": "кому ещё писать", "поставщики": "поставщиков", "бренды": "бренды", "коды": "коды",
    "узлы": "узлы", "детали": "детали", "парк": "парк", "ведомость": "ведомость", "признаки": "признаки",
    "дефекты": "дефекты", "ремонт": "ремонтные операции"
  };
  // Вид машины из реестра машин (dict/machine.json) — словами.
  var ВИД_МАШИНЫ = {
    "turbine": "газовая турбина", "gas_engine": "газопоршневой двигатель", "mining_machine": "горная машина",
    "other_machine": "прочая машина"
  };
  var НАПРАВЛЕНИЕ = { "gtu": "ГТУ", "gpu": "ГПУ" };
  // Критичность узла одной шкалой (library/equipment.CRIT_WORDS).
  var КРИТ = { "A": "A — останавливает машину", "B": "B — плановая замена", "C": "C — расходник" };
  // Почему дефект стоит на карточке машины.
  var ПУТЬ_ДЕФЕКТА = { "деталь": "по детали этой машины", "машина": "записан для этой машины", "узел": "по узлу машины" };
  var СЕМЕЙСТВО = {
    "ansaldo": "Ansaldo Energia (V-машины)", "sgt": "Siemens SGT-100…400", "finspong": "Siemens SGT-500…800",
    "solar": "Solar Turbines", "heavy": "тяжёлые ГТУ", "gpu": "ГПУ"
  };
  // Откуда дата квотации (lib_prices.price_date_src) — словами для сорсера.
  var ДАТА = {
    "документ": "дата в КП", "письмо": "дата письма", "карточка: создана": "не раньше даты запроса",
    "нет": "в КП и на карточке даты нет"
  };
  // Условие КП без значения — почему его нет (library/offer_terms.py).
  var ПРИЧИНА = {
    "строка": { текст: null, подпись: "из строки предложения" },
    "файл": { текст: null, подпись: "из общих условий КП — к этой строке могло не относиться" },
    "нет": { текст: "в КП не указано", подпись: "проверено: условия в предложении нет — можно спросить поставщика" },
    "несколько": { текст: "в КП несколько разных", подпись: "в предложении разные значения, выбрать нельзя" },
    "не проверено": { текст: "разбор не дошёл", подпись: "наш недочёт разбора, а не молчание поставщика" }
  };
  // Проверка наличия у продавца: слово проверки — словами для сорсера.
  var ПРОВЕРКА = {
    "in_stock": "есть на складе", "available_lead": "под заказ, со сроком",
    "pn_found_no_stock": "номер есть, на складе нет", "oem_only": "только у изготовителя",
    "pn_not_found": "номер не найден", "dead_link": "страница продавца недоступна",
    "price_differs": "цена отличается от записанной", "not_a_seller": "не продавец"
  };
  var СКЛАД = { "yes": "есть", "no": "нет", "conditional": "при условии", "unknown": "не знаем" };
  // Что совпало у компании реестра исполнителей и справочника поставщиков
  // (шаг 4) — словами. Служебного слова «сведение» на экране нет.
  var СОВПАЛ = { "инн": "совпал ИНН", "vat": "совпал налоговый номер", "домен сайта": "совпал домен сайта",
                 "домен почты": "совпал домен почты" };

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
  function наружу(href, text) {
    var a = ссылка(href, text);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    return a;
  }
  function число(n) {
    if (typeof n !== "number" || !isFinite(n)) return "";
    var s = String(Math.round(n * 100) / 100).split(".");
    return s[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (s[1] ? "," + s[1] : "");
  }
  function склонение(n, одна, две, много) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return много;
    if (b === 1) return одна;
    if (b >= 2 && b <= 4) return две;
    return много;
  }
  function к(v) { return encodeURIComponent(String(v)); }
  function адрес_кода(code) { return "/p#code=" + к(code); }
  function адрес_бренда(key) { return "/p#brand=" + к(key); }
  function адрес_поставщика(id) { return "/p#supplier=" + к(id); }
  function адрес_машины(id) { return "/p#model=" + к(id); }
  function адрес_узла(id) { return "/p#unit=" + к(id); }
  function вид_машины(k) { return k ? (ВИД_МАШИНЫ[k] || k) : null; }

  // Код: ключ есть — ссылка на карточку; ключа нет (код отвергнут правилом
  // правдоподобия) — написание и видимая пометка: подсказки при наведении на
  // телефоне нет.
  function код(code, written) {
    if (code) return ссылка(адрес_кода(code), written || code);
    var s = узел("span", null, written || "—");
    if (written) s.appendChild(узел("span", "src", "не код детали: марка или стандарт"));
    return s;
  }
  function бренд(b) {
    if (!b || !b.name) return узел("span", "none", "не назван");
    if (b.key) return ссылка(адрес_бренда(b.key), b.name);
    var w = узел("span");
    w.appendChild(узел("span", "word", b.name));
    w.appendChild(узел("span", "src", "нет в реестре брендов"));
    return w;
  }
  // Компания: из справочника — ссылка на карточку; нет в справочнике —
  // ссылка в карточку Битрикса (номер только в адресе), чтобы цену было куда
  // отправить; не указана вовсе — так и сказано.
  function компания(c, bx, unresolved) {
    if (c && c.id) {
      var box = узел("span");
      box.appendChild(ссылка(адрес_поставщика(c.id), c.name || "имя не известно"));
      if (c.number) box.appendChild(узел("span", "src", c.number));
      return box;
    }
    if (bx) {
      var b = узел("span");
      b.appendChild(наружу(BXLINK + к(bx) + "/", "карточка компании в Битриксе ↗"));
      b.appendChild(узел("span", "src", "нет в справочнике поставщиков"));
      return b;
    }
    return узел("span", "none", unresolved ? "компании нет в справочнике поставщиков" : "компания не указана");
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
  function сумма(o) {
    if (typeof o.total === "number") return деньги(o.total, o.currency);
    if (o.total_hidden) {
      var s = узел("span", "warn", "не знаем");
      s.appendChild(узел("span", "src", "количество не читается — сумму проверить нечем"));
      return s;
    }
    return узел("span", "none", "—");
  }
  function месяц(m, src, rfq) {
    var box = узел("span");
    box.appendChild(m ? узел("span", "num", m) : узел("span", "none", "не знаем"));
    if (src) box.appendChild(узел("span", "src", ДАТА[src] || src));
    if (rfq) {
      var p = узел("span", "src");
      p.appendChild(наружу(BXRFQ + к(rfq) + "/", "карточка запроса ↗"));
      box.appendChild(p);
    }
    return box;
  }
  // Условие КП: значение с источником или причина, почему значения нет.
  function условие(v, src, суффикс) {
    var п = ПРИЧИНА[src || "не проверено"] || ПРИЧИНА["не проверено"];
    var box = узел("span");
    if (v === null || v === undefined || v === "") {
      box.appendChild(узел("span", (src || "не проверено") === "не проверено" ? "warn" : "none", п.текст || "—"));
    } else {
      box.appendChild(узел("span", typeof v === "number" ? "num" : null,
        (typeof v === "number" ? число(v) : String(v)) + (суффикс || "")));
      if (src === "файл") box.appendChild(узел("span", "src", "из общих условий КП"));
    }
    box.setAttribute("title", п.подпись);
    return box;
  }
  function оплата(o) {
    var box = условие(o.pay_terms, o.pay_src);
    if (o.pay_terms && typeof o.pay_advance_pct === "number") {
      box.appendChild(узел("span", "src", "аванс " + число(o.pay_advance_pct) + " %"));
    }
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
  function шапка(вид, h1, строки) {
    var h = узел("header", "hero");
    h.appendChild(узел("p", "eyebrow", вид));
    h.appendChild(h1);
    (строки || []).forEach(function (x) { if (x) h.appendChild(x); });
    return h;
  }
  function не_успели(v) {
    if (!v.partial || !v.partial.length) return null;
    return узел("p", "note warn", "Не успели посчитать: " + v.partial.map(function (x) { return ЧАСТИ[x] || x; }).join(", ")
      + ". Обновите страницу — второй запрос обычно быстрее.");
  }
  function пусто(текст) { return узел("p", "note", текст); }
  function показаны(показано, всего, что) {
    return всего > показано ? пусто("Показаны " + число(показано) + " из " + число(всего) + (что || "") + ".") : null;
  }
  // Машина: имя — ссылкой на её карточку /p#model=, если есть право на
  // библиотеку; без права — текстом, и это сказано. «под» — своя подпись
  // вызывающего (у карточки узла — сколько деталей машины в узле).
  function машина(m, library, под) {
    var chip = узел("span", "chip");
    chip.appendChild(library && m.id ? ссылка(адрес_машины(m.id), m.name) : узел("span", null, m.name));
    var подпись = под !== undefined ? под
      : [вид_машины(m.kind), typeof m.parts === "number" ? "деталей " + число(m.parts) : null].filter(Boolean).join(" · ");
    if (подпись) chip.appendChild(узел("span", "src", подпись));
    if (m.brand && m.brand.name) {
      var бр = узел("span", "src");
      бр.appendChild(бренд(m.brand));
      chip.appendChild(бр);
    }
    if (!library) chip.appendChild(узел("span", "src", "библиотека закрыта правом"));
    return chip;
  }
  // Узел — ссылкой на его карточку /p#unit=; без права на библиотеку — текстом.
  function к_узлу(u, library, нет) {
    if (!u || !u.name) return узел("span", "none", нет || "—");
    return library && u.id ? ссылка(адрес_узла(u.id), u.name) : узел("span", null, u.name);
  }
  function не_посчитано(v, часть) { return (v.partial || []).indexOf(часть) >= 0; }
  var НЕ_УСПЕЛИ = "Не успели посчитать — обновите страницу.";

  // ── карточка кода ──────────────────────────────────────────────────────────
  function предложения(список, аналоги) {
    var колонки = [
      ["Компания", function (o) { return компания(o.company, o.bx, o.unresolved); }],
      ["Код", function (o) { return узел("span", null, o.written || "—"); }],
      ["Бренд", function (o) {
        var b = бренд(o.brand);
        if (!аналоги && o.unconfirmed) {
          var box = узел("span");
          box.appendChild(b);
          box.appendChild(узел("span", "src warn", "оригинал не подтверждён: " + o.unconfirmed));
          return box;
        }
        return b;
      }],
      ["Цена", function (o) { return деньги(o.price, o.currency); }],
      ["Кол-во", function (o) { return количество(o.qty, o.unit, o.qty_hidden); }],
      ["Сумма", сумма],
      ["Базис", function (o) { return условие(o.basis, o.basis_src); }],
      ["Оплата", оплата],
      ["Изготовл., дн.", function (o) { return условие(o.make_days, o.make_src); }],
      ["Поставка, дн.", function (o) { return условие(o.lead_days, o.lead_src); }],
      ["Месяц квотации", function (o) { return месяц(o.month, o.month_src, o.rfq); }]
    ];
    if (аналоги) колонки.push(["Почему аналог", function (o) { return o.why; }]);
    return таблица(колонки, список);
  }

  // Ориентир цены по списку: по каждой валюте — последняя, разброс, сколько
  // компаний. Пересчёта по курсу нет намеренно, и это сказано.
  function ориентир(цены, группа) {
    var мои = (цены || []).filter(function (x) { return x.group === группа; });
    if (!мои.length) return null;
    var box = узел("div", "prices");
    мои.forEach(function (x) {
      var p = узел("p");
      p.appendChild(узел("strong", null, (x.currency || "валюта не указана") + ": "));
      p.appendChild(узел("span", null, "последняя " + число(x.last && x.last.price) + (x.last && x.last.month ? " (" + x.last.month + ", " : " (")));
      var l = x.last || {};
      if (l.company && l.company.id) p.appendChild(ссылка(адрес_поставщика(l.company.id), l.company.name || "имя не известно"));
      else p.appendChild(узел("span", null, l.bx ? "компании нет в справочнике поставщиков" : "компания не указана"));
      p.appendChild(узел("span", null, ")"));
      p.appendChild(узел("span", "src", (x.min === x.max ? "одна цена" : "от " + число(x.min) + " до " + число(x.max))
        + " · " + число(x.companies) + " " + склонение(x.companies, "компания", "компании", "компаний")
        + " · " + число(x.rows) + " " + склонение(x.rows, "строка", "строки", "строк")));
      box.appendChild(p);
    });
    var оговорки = [];
    if (мои.length > 1) {
      оговорки.push("Цены в разных валютах. Пересчёта по курсу здесь нет намеренно: курс на дату котировки мы не храним, "
        + "а сегодняшний превратил бы прошлогоднее КП в сегодняшнее предложение.");
    } else if ((мои[0].companies || 0) <= 1) {
      оговорки.push("Сравнивать не с чем: цену дала одна компания. Это справка о цене, а не выбор.");
    }
    оговорки.forEach(function (t) { box.appendChild(узел("p", "note", t)); });
    return box;
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
    // Код сам указан в каталоге аналогов — это видно в шапке, а не только внизу.
    var к_чему = null;
    if (v.analog_of && v.analog_of.length) {
      к_чему = узел("p", "lead");
      к_чему.appendChild(узел("span", null, "По каталогу аналогов этот код — "));
      v.analog_of.slice(0, 3).forEach(function (a, i) {
        if (i) к_чему.appendChild(узел("span", null, "; "));
        к_чему.appendChild(узел("span", null, (a.kind || "аналог") + " к "));
        к_чему.appendChild(код(a.code, a.written));
        к_чему.appendChild(узел("span", "sep", " · "));
        к_чему.appendChild(бренд(a.brand));
      });
      if (v.analog_of.length > 3) к_чему.appendChild(узел("span", null, "; и ещё " + число(v.analog_of.length - 3) + " — ниже"));
    }
    out.push(шапка("Код", h1, [lead, к_чему]));
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
        return b.name + (b.key ? "" : " (нет в реестре брендов)") + " — " + (b.sources || []).join(", ");
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
    спрос.appendChild(totals);
    var пояснения = [];
    if (d.qty_hidden) пояснения.push("строк с нечитаемым количеством (больше миллиона): " + число(d.qty_hidden) + " — в сумму не вошли");
    // Заказчиков не знаем по устройству базы — одной сноской, а не плиткой.
    пояснения.push("заказчиков не знаем: связи «сделка → заказчик» в базе нет");
    спрос.appendChild(пусто(пояснения.join("; ") + "."));
    out.push(спрос);

    var o = v.offers || {};
    // Строки делятся по бренду только когда бренд позиции установлен.
    var делим = o.brand_judged !== false;
    var ор = раздел(делим ? "Предложения оригинала" : "Предложения", o.original_n ? число(o.original_n) : "");
    if (!делим && (o.rows || 0) > 0) {
      ор.appendChild(пусто(o.brand_disputed
        ? "Бренд позиции не установлен: его называют только поставщики, и называют разные. Оригинал голосованием не выбирается — "
          + "строки не делятся по бренду; аналогом считается только строка, где поставщик сам пишет «аналог»."
        : "Бренд позиции не определён по реестру: строки делятся на оригинал и аналог только по слову поставщика."));
    }
    if (делим && o.unconfirmed_n) {
      ор.appendChild(пусто("Из них " + число(o.unconfirmed_n) + " — оригинал не подтверждён: бренд в КП не назван "
        + "или написан не брендом реестра."));
    }
    var цены_о = ориентир(o.prices, "original");
    if (цены_о) ор.appendChild(цены_о);
    ор.appendChild(o.original && o.original.length ? предложения(o.original, false)
      : пусто(делим ? "Предложений оригинала нет." : "Предложений нет."));
    var ещё_о = показаны((o.original || []).length, o.original_n || 0, ", самые свежие");
    if (ещё_о) ор.appendChild(ещё_о);
    if (o.capped) ор.appendChild(пусто("Строк КП по коду больше 2 000: посчитаны последние 2 000."));
    out.push(ор);
    var ан = раздел("Предложения аналогов", o.analog_n ? число(o.analog_n) : "");
    var цены_а = ориентир(o.prices, "analog");
    if (цены_а) ан.appendChild(цены_а);
    ан.appendChild(o.analog && o.analog.length ? предложения(o.analog, true) : пусто("Предложений аналогов нет."));
    var ещё_а = показаны((o.analog || []).length, o.analog_n || 0, ", самые свежие");
    if (ещё_а) ан.appendChild(ещё_а);
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
        chip.appendChild(к_узлу(u, v.library));
        var под = [u.parent ? "в узле «" + u.parent + "»" : null, u.crit ? "критичность " + u.crit : null].filter(Boolean).join(" · ");
        if (под) chip.appendChild(узел("span", "src", под));
        if (!v.library) chip.appendChild(узел("span", "src", "библиотека закрыта правом"));
        chips.appendChild(chip);
      });
      мш.appendChild(chips);
    } else {
      мш.appendChild(пусто(v.catalog ? "У детали в каталоге не указаны ни машина, ни узел." : "Детали нет в каталоге — машина и узел не известны."));
    }
    out.push(мш);

    var кому = раздел("Кому ещё писать");
    // Сначала — знают именно эту деталь (реестр исполнителей): адрес по детали,
    // а не по классу. Потом — давали цену по бренду позиции на другие коды.
    кому.appendChild(узел("h3", null, "Знают эту деталь — реестр исполнителей"
      + (v.makers_n ? " · " + число(v.makers_n) : "")));
    if (v.makers && v.makers.length) {
      кому.appendChild(таблица([
        // Та же компания есть в справочнике поставщиков (совпал ИНН или
        // домен) — ссылка на её карточку; нет — имя разведки, как прежде.
        ["Компания", function (m) {
          if (!m.company || !m.company.id) return m.name;
          var box = компания(m.company);
          if (m.name && m.name !== m.company.name) box.appendChild(узел("span", "src", "в разведке — " + m.name));
          if (m.link) box.appendChild(узел("span", "src", СОВПАЛ[m.link] || m.link));
          return box;
        }],
        ["Роль", function (m) { return m.role; }],
        ["Страна", function (m) { return m.country; }],
        ["Наличие у продавца", function (m) {
          if (!m.verdict && !m.in_stock) return узел("span", "none", "не проверяли");
          var box = узел("span", null, m.verdict ? (ПРОВЕРКА[m.verdict] || m.verdict) : "проверка без вывода");
          if (m.in_stock) box.appendChild(узел("span", "src", "на складе: " + (СКЛАД[m.in_stock] || m.in_stock)
            + (m.stock_qty ? " · " + m.stock_qty : "")));
          return box;
        }],
        ["Цена продавца", function (m) { return typeof m.price === "number" ? деньги(m.price, m.currency) : узел("span", "none", "—"); }],
        ["Срок", function (m) { return m.lead_time; }],
        ["Что делает", function (m) { return m.makes; }]
      ], v.makers));
      var ещё_и = показаны(v.makers.length, v.makers_n || 0);
      if (ещё_и) кому.appendChild(ещё_и);
      var в_справочнике = v.makers.filter(function (m) { return m.company && m.company.id; }).length;
      кому.appendChild(пусто("Наличие, цена и срок — запись прошлой проверки у продавца, сейчас не перепроверены; дата проверки "
        + "не хранится. " + (в_справочнике
          ? "Компания со ссылкой есть в справочнике поставщиков: совпал ИНН или домен сайта или почты. Без ссылки — "
            + "такого совпадения нет; по одному имени связь не ставится."
          : "В справочнике поставщиков этих компаний по ИНН и домену не нашлось — карточек у них нет.")));
    } else {
      кому.appendChild(пусто(v.catalog ? "В реестре исполнителей эта деталь ни за кем не записана."
        : "Детали нет в каталоге — реестр исполнителей по ней не ведётся."));
    }
    var бр_имя = v.brand && v.brand.name ? v.brand.name : "";
    кому.appendChild(узел("h3", null, "Давали цену по бренду позиции на другие коды"
      + (v.write_to && v.write_to.length ? " · " + число(v.write_to.length) : "")));
    if (v.brand && v.brand.key && делим) {
      if (v.write_to && v.write_to.length) {
        кому.appendChild(пусто("Давали цену оригинала по бренду «" + бр_имя + "» на другие коды, а на этот — нет. "
          + "Это адрес по бренду, а не подтверждение, что у них есть эта деталь."));
        кому.appendChild(таблица([
          ["Компания", function (w) { return компания(w.company); }],
          ["Кодов бренда", function (w) { return узел("span", "num", число(w.codes)); }],
          ["Строк КП", function (w) { return узел("span", "num", число(w.rows)); }],
          ["Последний месяц", function (w) { return месяц(w.last_month); }]
        ], v.write_to));
      } else {
        кому.appendChild(пусто("Других компаний из справочника поставщиков, дававших цену оригинала по бренду «" + бр_имя
          + "», в базе нет."));
      }
    } else if (v.brand && v.brand.key && o.brand_disputed) {
      кому.appendChild(пусто("Бренд позиции не установлен: поставщики называют разные — подбирать по бренду не по чему."));
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
    out.push(шапка("Бренд", узел("h1", null, v.name), [подпись ? узел("p", "lead", подпись) : null]));
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
    totals.appendChild(итог(число(d.registry_rows || 0), "строк спроса, включая ячейки из нескольких брендов"));
    спрос.appendChild(totals);
    спрос.appendChild(пусто("Первые три числа — по дословным написаниям бренда: ячейка вида «SKF, FAG» в них не входит, "
      + "поэтому «не меньше». Последнее посчитано, когда написания заводили в реестр брендов, и такие ячейки учитывает."));
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
    прав.appendChild(узел("p", "note", "по предложениям оригинала"));
    прав.appendChild(v.codes_offers && v.codes_offers.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function () { return узел("span", null, v.name); }],
      ["Строк КП", function (c) { return узел("span", "num", число(c.rows)); }],
      ["Поставщиков", function (c) { return узел("span", "num", число(c.suppliers)); }],
      ["Последний месяц", function (c) { return месяц(c.last_month); }]
    ], v.codes_offers) : пусто("Предложений оригинала по бренду нет."));
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
    var пс = раздел("Поставщики, дававшие цену оригинала", o.suppliers ? число(o.suppliers) : "");
    пс.appendChild(v.suppliers && v.suppliers.length ? таблица([
      ["Компания", function (s) { return компания(s.company); }],
      ["Кодов", function (s) { return узел("span", "num", число(s.codes)); }],
      ["Строк КП", function (s) { return узел("span", "num", число(s.rows)); }],
      ["Последний месяц", function (s) { return месяц(s.last_month); }]
    ], v.suppliers) : пусто("Цен оригинала по бренду от компаний из справочника поставщиков нет."));
    if (o.rows_unresolved) пс.appendChild(пусто("Ещё строк КП от компаний, которых нет в справочнике поставщиков: " + число(o.rows_unresolved) + "."));
    if (o.analog_rows) {
      пс.appendChild(пусто("Ещё " + число(o.analog_rows) + " " + склонение(o.analog_rows, "строка", "строки", "строк")
        + " КП — ответы аналогом на спрос по бренду: поставщик пишет «аналог» или называет другой бренд. В счёт выше не входят."));
    }
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
    out.push(шапка("Поставщик", h1, [lead]));
    // Карточки Битрикса — ссылками без номера в тексте: номер человеку ничего
    // не говорит, а адрес ссылки его несёт.
    var bx = узел("span");
    var карточек = (v.bitrix || []).length;
    (v.bitrix || []).forEach(function (id, i) {
      if (i) bx.appendChild(узел("span", null, ", "));
      bx.appendChild(наружу(BXLINK + к(id) + "/", карточек > 1 ? "карточка " + (i + 1) + " ↗" : "карточка ↗"));
    });
    out.push(факты([
      ["Номер KV-S", v.number || "номер не выдан"],
      ["ИНН", v.inn && v.inn.length ? v.inn.join(", ") : "—"],
      ["Домен", v.domains && v.domains.length ? v.domains.join(", ") : "—"],
      ["Страна, город", [v.country, v.city].filter(Boolean).join(", ")],
      ["Карточка в Битриксе", карточек ? bx : "—"],
      ["Имя взято из", v.name_src ? (ИСТОЧНИК_ИМЕНИ[v.name_src] || v.name_src) : "—"],
      ["В прежнем разделе", прежние([["/suppliers#e=" + к(v.id), "поставщики"]])]
    ]));
    var q = v.quotes || {}, r = v.rfq;
    var зп = раздел("Запросы и КП");
    var totals = узел("div", "totals");
    if (r) {
      totals.appendChild(итог(число(r.sent || 0), "запросов отправлено"));
      // Доля — только от трёх запросов: «50 %» из двух — не показатель
      // (тот же порог, что у раздела «Поставщики»).
      totals.appendChild(итог(число(r.answered || 0) + (r.sent >= 3 ? " · " + Math.round(100 * (r.answered || 0) / r.sent) + " %" : ""),
        "ответили"));
      totals.appendChild(итог(число(r.quoted || 0), "дали КП"));
      totals.appendChild(итог(число(r.silent || 0), "молчали"));
      totals.appendChild(итог(число(r.no_outcome || 0), "без исхода"));
    }
    totals.appendChild(итог(число(q.rows || 0) + (q.capped ? "+" : ""), "строк цены в КП"));
    totals.appendChild(итог(число(q.cards || 0), "запросов с разобранной ценой"));
    totals.appendChild(итог(число(q.codes || 0), "кодов с ценой"));
    totals.appendChild(итог(q.last_month || "—", "последний месяц квотации"));
    зп.appendChild(totals);
    if (r) {
      зп.appendChild(пусто("«Молчали» — в карточке запроса отмечено, что ответа нет. «Без исхода» — ни ответа, ни отказа, "
        + "ни такой отметки: карточка стоит открытой. Доля ответивших — от отправленных, если их не меньше трёх."));
    } else {
      зп.appendChild(пусто("Отзывчивость по запросам ещё не посчитана."));
    }
    out.push(зп);

    var бр = раздел("Бренды", v.brands && v.brands.length ? число(v.brands.length) : "");
    бр.appendChild(v.brands && v.brands.length ? таблица([
      ["Бренд", function (b) { return бренд(b.brand); }],
      ["Кодов: бренд назвал сам", function (b) { return узел("span", "num", число(b.named_codes || 0)); }],
      ["Кодов: бренд не назван — по запросу или каталогу", function (b) { return узел("span", "num", число(b.asked_codes || 0)); }],
      ["Строк КП", function (b) { return узел("span", "num", число(b.rows)); }],
      ["Последний месяц", function (b) { return месяц(b.last_month); }]
    ], v.brands) : пусто("Бренд в предложениях не назван и по запросам не известен."));
    if (v.brands && v.brands.length) {
      бр.appendChild(пусто("«Назвал сам» — бренд, который поставщик написал в своём КП. Где он бренд не написал, строка "
        + "отнесена к бренду, который мы спрашивали, или к бренду детали по каталогу: это не утверждение поставщика."));
    }
    out.push(бр);
    var кд = раздел("Коды с ценами", v.codes && v.codes.length ? число(v.codes.length) : "");
    кд.appendChild(v.codes && v.codes.length ? таблица([
      ["Код", function (c) { return код(c.code, c.written); }],
      ["Бренд", function (c) {
        var box = узел("span");
        box.appendChild(бренд(c.brand));
        if (c.brand_src) box.appendChild(узел("span", "src", c.brand_src));
        return box;
      }],
      ["Оригинал или аналог", function (c) {
        if (!c.verdict) return узел("span", "none", "не судим");
        var box = узел("span", c.verdict === "аналог" ? "warn" : null, c.verdict);
        if (c.why) box.appendChild(узел("span", "src", c.why));
        return box;
      }],
      ["Последняя цена", function (c) { return деньги(c.price, c.currency); }],
      ["Кол-во", function (c) { return количество(c.qty, c.unit, false); }],
      ["Месяц квотации", function (c) { return месяц(c.month); }],
      ["Предложений", function (c) { return узел("span", "num", число(c.offers)); }]
    ], v.codes) : пусто("Разобранных цен от этой компании нет."));
    if (v.codes && v.codes.length) {
      кд.appendChild(пусто("«Оригинал или аналог» — по бренду, который назвал поставщик, против бренда запроса или каталога. "
        + "Если поставщик бренд не назвал или спросить было не про что — не судим."));
    }
    out.push(кд);
    // Кто эта компания в реестре разведки (шаг 4): поставщики, у которых с ней
    // совпал ИНН или домен. research_n = null — связь реестров не посчитана.
    var рз = раздел("В реестре исполнителей", v.research_n ? число(v.research_n) : "");
    if (v.research_n === null || v.research_n === undefined) {
      рз.appendChild(пусто("Связь с реестром исполнителей (разведкой) ещё не посчитана."));
    } else if (!(v.research || []).length) {
      рз.appendChild(пусто("В реестре исполнителей эта компания по ИНН и домену не найдена."));
    } else {
      рз.appendChild(таблица([
        ["Компания в разведке", function (x) { return x.name; }],
        ["Роль", function (x) { return x.role; }],
        ["Страна", function (x) { return x.country; }],
        ["Связь", function (x) { return x.rule ? (СОВПАЛ[x.rule] || x.rule) : "—"; }],
        ["Деталей", function (x) { return узел("span", "num", число(x.parts || 0)); }],
        ["С проверкой наличия", function (x) { return узел("span", "num", число(x.checked || 0)); }]
      ], v.research));
      var ещё_р = показаны(v.research.length, v.research_n || 0);
      if (ещё_р) рз.appendChild(ещё_р);
      рз.appendChild(пусто("Связь — по совпавшему ИНН или домену сайта и почты; по одному имени связь не ставится. "
        + "«Деталей» — строк реестра исполнителей за этим поставщиком разведки."));
    }
    out.push(рз);
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: (v.name || "Имя не известно") + " · поставщик", nodes: out };
  }

  // ── машина и узел: общие разделы ───────────────────────────────────────────
  // Детали: «Код» и «Бренд» — соседние колонки, как везде в портале.
  function таблица_деталей(список, library) {
    return таблица([
      ["Код", function (p) { return код(p.code, p.written); }],
      ["Бренд", function (p) { return бренд(p.brand); }],
      ["Наименование", function (p) { return p.name; }],
      ["Узел", function (p) { return к_узлу(p.unit, library, "не определён"); }],
      ["Наш номер KV", function (p) { return p.kv_no || "—"; }]
    ], список);
  }
  function раздел_деталей(v, заголовок, пусто_текст, пояснение) {
    var p = v.parts || {};
    var s = раздел(заголовок, p.total ? число(p.total) : "");
    if (не_посчитано(v, "детали")) {
      s.appendChild(пусто(НЕ_УСПЕЛИ + (p.total ? " Деталей всего: " + число(p.total) + "." : "")));
      return s;
    }
    if (!(p.list || []).length) { s.appendChild(пусто(пусто_текст)); return s; }
    s.appendChild(таблица_деталей(p.list, v.library));
    var ещё = показаны(p.list.length, p.total || 0, ": сначала с нашим номером KV, потом по потребности из сводки партномеров");
    if (ещё) s.appendChild(ещё);
    if (пояснение) s.appendChild(пусто(пояснение));
    return s;
  }
  function раздел_признаков(v, нет) {
    var x = v.symptoms || {};
    var s = раздел("Признаки", x.n ? число(x.n) : "");
    if (не_посчитано(v, "признаки")) { s.appendChild(пусто(НЕ_УСПЕЛИ)); return s; }
    if (!(x.list || []).length) { s.appendChild(пусто(нет)); return s; }
    s.appendChild(таблица([
      ["Признак", function (r) {
        var box = узел("span", null, r.name);
        if (r.basis) box.appendChild(узел("span", "src", "основание: " + r.basis));
        return box;
      }],
      ["Узел", function (r) { return к_узлу(r.unit, v.library); }],
      ["Что меряют", function (r) { return r.measure; }],
      ["Что обычно значит", function (r) {
        var box = узел("span", null, r.defect || "—");
        if (r.defects && r.defects.length) {
          box.appendChild(узел("span", "src", "дефекты справочника: " + r.defects.map(function (d) { return d.name; }).join("; ")));
        }
        return box;
      }],
      ["Чем подтвердить", function (r) {
        var box = узел("span", null, r.confirm || "—");
        if (r.ops && r.ops.length) {
          box.appendChild(узел("span", "src", "операции: " + r.ops.map(function (o) { return o.name; }).join("; ")));
        }
        return box;
      }]
    ], x.list));
    var ещё = показаны(x.list.length, x.n || 0);
    if (ещё) s.appendChild(ещё);
    if (x.list.some(function (r) { return r.confidence === "low"; })) {
      s.appendChild(пусто("Признаки — заготовка по общей практике диагностики и брошюрам изготовителей, а не наши "
        + "измерения: уверенность низкая, пока инженер сервиса их не подтвердил. Уставок по машинам парка нет."));
    }
    return s;
  }
  function раздел_дефектов(v, машины_карточка, нет) {
    var x = v.defects || {};
    var s = раздел("Дефекты и ремонтные решения", x.n ? число(x.n) : "");
    if (не_посчитано(v, "дефекты")) { s.appendChild(пусто(НЕ_УСПЕЛИ)); return s; }
    if (!(x.list || []).length) { s.appendChild(пусто(нет)); return s; }
    var колонки = [
      ["Дефект", function (r) {
        var box = узел("span", null, r.name);
        if (r.model) box.appendChild(узел("span", "src", "записан для: " + r.model));
        if (r.source) box.appendChild(узел("span", "src", r.source));
        return box;
      }],
      ["Узел", function (r) { return к_узлу(r.unit, v.library); }],
      // Номер детали дефекта — парой «код + бренд»; без номера — прочерк в обеих.
      ["Код", function (r) { return r.written ? код(r.code, r.written) : узел("span", "none", "—"); }],
      ["Бренд", function (r) { return r.written ? бренд(r.brand) : узел("span", "none", "—"); }],
      ["Причина и последствие", function (r) {
        var box = узел("span", null, r.consequence || "—");
        if (r.cause) box.appendChild(узел("span", "src", "причина: " + r.cause));
        return box;
      }],
      ["Решение", function (r) {
        var box = узел("span", null, r.fix || (r.ops && r.ops.length ? "" : "—"));
        if (r.ops && r.ops.length) {
          box.appendChild(узел("span", "src", "операции: " + r.ops.map(function (o) { return o.name; }).join("; ")));
        }
        return box;
      }]
    ];
    if (машины_карточка) колонки.push(["Почему здесь", function (r) { return ПУТЬ_ДЕФЕКТА[r.via] || r.via; }]);
    s.appendChild(таблица(колонки, x.list));
    var ещё = показаны(x.list.length, x.n || 0);
    if (ещё) s.appendChild(ещё);
    return s;
  }
  function раздел_ремонта(v, нет) {
    var x = v.procedures || {};
    var s = раздел("Ремонтные операции", x.n ? число(x.n) : "");
    if (не_посчитано(v, "ремонт")) { s.appendChild(пусто(НЕ_УСПЕЛИ)); return s; }
    if (!(x.list || []).length) { s.appendChild(пусто(нет)); return s; }
    s.appendChild(таблица([
      ["Вид", function (r) { return r.kind; }],
      ["Операция", function (r) {
        var box = узел("span", null, r.name);
        if (r.family) box.appendChild(узел("span", "src", "для семейства: " + (СЕМЕЙСТВО[r.family] || r.family)));
        if (r.source) box.appendChild(узел("span", "src", r.source));
        return box;
      }],
      ["Узел", function (r) { return к_узлу(r.unit, v.library, "вся машина"); }],
      ["Что делают", function (r) { return r.scope; }],
      ["Срок", function (r) { return r.duration; }],
      ["Исполнитель", function (r) { return r.performer; }]
    ], x.list));
    var ещё = показаны(x.list.length, x.n || 0);
    if (ещё) s.appendChild(ещё);
    s.appendChild(пусто("Сроков и стоимости ремонта в библиотеке нет; исполнитель назван, только если он известен."));
    return s;
  }

  // ── карточка машины ────────────────────────────────────────────────────────
  function карточка_машины(v) {
    var out = [];
    var lead = узел("p", "lead");
    if (v.makers && v.makers.length) {
      lead.appendChild(узел("span", null, "Изготовитель: "));
      v.makers.forEach(function (b, i) {
        if (i) lead.appendChild(узел("span", null, ", "));
        // В строке шапки — в строку: пометка «нет в реестре» в скобках, а не
        // отдельной строкой, иначе шапка рвётся посередине.
        if (b.key) lead.appendChild(бренд(b));
        else {
          lead.appendChild(узел("span", "word", b.name));
          lead.appendChild(узел("span", null, " (нет в реестре брендов)"));
        }
      });
    } else {
      lead.appendChild(узел("span", null, "Изготовитель в справочнике машин не указан"));
    }
    var вид = [вид_машины(v.kind), v.segment_name, v.legacy ? "прежнее имя " + v.legacy : null].filter(Boolean);
    if (вид.length) lead.appendChild(узел("span", null, " · " + вид.join(" · ")));
    out.push(шапка("Машина", узел("h1", null, v.name), [lead]));

    // Ячейка изготовителя справочника — только когда она не совпадает с
    // единственным брендом: иначе в ней несведённая часть («… / Выдумлит»).
    var ячейка = v.maker_cell && !(v.makers && v.makers.length === 1 && v.makers[0].name === v.maker_cell);
    var факт = [
      ["Изготовитель в справочнике машин", ячейка ? v.maker_cell : null],
      ["Прежнее имя", v.legacy],
      ["Написания", v.aliases && v.aliases.length ? v.aliases.join(", ") : null],
      ["Вид", вид_машины(v.kind)],
      ["Сегмент", v.segment_name || v.segment],
      ["Семейство", v.family],
      ["Мощность", v.power],
      ["КПД", v.efficiency],
      [v.shafts_label || "Валы", v.shafts],
      ["Применение", v.use_case],
      ["Примечание", v.note],
      ["Откуда в справочнике", v.source]
    ].filter(function (p) { return p[1] !== null && p[1] !== undefined && p[1] !== ""; });
    факт.push(["Типовое дерево узлов", v.dir ? НАПРАВЛЕНИЕ[v.dir] + (v.dir_via ? " — по " + (v.dir_via === "сегмент" ? "сегменту" : "семейству") + " машины" : "")
      : "нет: для этого направления в библиотеке его не заведено"]);
    факт.push(["В прежнем разделе", прежние([[v.segment ? "/library#segment=" + к(v.segment) : "/library", "библиотека"]])]);
    out.push(факты(факт));

    var p = v.parts || {}, f = v.fleet || {}, b = v.bom || {};
    var totals = узел("div", "totals");
    totals.appendChild(итог(число(p.total || 0), "деталей в каталоге"));
    totals.appendChild(итог(число(p.with_unit || 0), "с определённым узлом"));
    totals.appendChild(итог(число(f.n || 0), "площадок в парке"));
    totals.appendChild(итог(число(b.n || 0), "строк ведомости"));
    out.push(totals);

    // Узлы: по деталям — измерено; типовое дерево — общее для направления.
    var уз = раздел("Узлы машины", v.units && v.units.length ? число(v.units.length) : "");
    if (не_посчитано(v, "узлы")) {
      уз.appendChild(пусто(НЕ_УСПЕЛИ));
    } else {
      уз.appendChild(узел("h3", null, "По деталям каталога"));
      if (v.units && v.units.length) {
        уз.appendChild(таблица([
          ["Узел", function (u) { return к_узлу(u, v.library); }],
          ["Входит в", function (u) { return к_узлу(u.parent, v.library, "корень дерева"); }],
          ["Критичность", function (u) { return u.crit ? (КРИТ[u.crit] || u.crit) : "—"; }],
          ["Деталей машины", function (u) { return узел("span", "num", число(u.parts)); }]
        ], v.units));
      } else {
        уз.appendChild(пусто(p.total ? "Ни у одной детали машины узел не определён." : "Деталей машины в каталоге нет — узлы по деталям не известны."));
      }
      if (p.no_unit) {
        уз.appendChild(пусто("Ещё " + число(p.no_unit) + " " + склонение(p.no_unit, "деталь", "детали", "деталей")
          + " без узла: по описанию узел не определился."));
      }
      var t = v.tree;
      var систем = t && t.systems ? t.systems.length : 0;
      уз.appendChild(узел("h3", null, t ? "Типовое дерево узлов " + НАПРАВЛЕНИЕ[t.dir]
        + (систем ? " · " + число(систем) + " " + склонение(систем, "система", "системы", "систем") : "")
        : "Типовое дерево узлов"));
      if (t && t.systems && t.systems.length) {
        var chips = узел("div", "chips");
        t.systems.forEach(function (s) {
          var chip = узел("span", "chip");
          chip.appendChild(к_узлу(s, v.library));
          chip.appendChild(узел("span", "src", [s.crit ? "критичность " + s.crit : null,
            s.children ? "вложенных " + число(s.children) : null,
            s.parts ? "деталей машины " + число(s.parts) : "деталей машины нет"].filter(Boolean).join(" · ")));
          chips.appendChild(chip);
        });
        уз.appendChild(chips);
        уз.appendChild(пусто("Дерево одно на все машины направления: узлы " + НАПРАВЛЕНИЕ[t.dir]
          + " общие для разных изготовителей. Это состав типовой машины, а не ведомость этой."));
      } else {
        уз.appendChild(пусто(v.dir ? "Узлов этого направления в справочнике нет."
          : "Направление машины не определено или для него типового дерева в библиотеке нет (есть для ГТУ и ГПУ)."));
      }
    }
    out.push(уз);

    out.push(раздел_деталей(v, "Детали каталога", "Деталей этой машины в каталоге нет."));
    out.push(раздел_признаков(v, v.dir || (v.units && v.units.length)
      ? "У узлов этой машины признаков в справочнике нет." : "Узлы машины не известны — признаков не к чему привязать."));
    out.push(раздел_дефектов(v, true, "Дефектов по узлам и деталям этой машины в справочнике нет."));
    out.push(раздел_ремонта(v, "Ремонтных операций по узлам этой машины в справочнике нет."));

    var вд = раздел("Ведомость", b.n ? число(b.n) : "");
    if (не_посчитано(v, "ведомость")) {
      вд.appendChild(пусто(НЕ_УСПЕЛИ));
    } else if (b.list && b.list.length) {
      вд.appendChild(таблица([
        ["Узел ведомости", function (r) { return r.node; }],
        ["Позиция", function (r) { return r.position; }],
        ["Код", function (r) { return код(r.code, r.written); }],
        ["Бренд", function (r) { return бренд(r.brand); }],
        ["Наименование", function (r) { return r.name; }],
        ["Кол-во", function (r) { return r.qty; }]
      ], b.list));
      var ещё_в = показаны(b.list.length, b.n || 0);
      if (ещё_в) вд.appendChild(ещё_в);
      вд.appendChild(пусто("Узел ведомости — как он назван в самой ведомости, а не в дереве узлов."));
    } else {
      вд.appendChild(пусто("Ведомости состава на эту машину в библиотеке нет."));
    }
    out.push(вд);

    var пк = раздел("Парк", f.n ? число(f.n) : "");
    if (не_посчитано(v, "парк")) {
      пк.appendChild(пусто(НЕ_УСПЕЛИ));
    } else if (f.list && f.list.length) {
      пк.appendChild(таблица([
        ["Площадка", function (r) { return r.site; }],
        ["Владелец", function (r) { return r.owner; }],
        ["Машин", function (r) { return r.units; }],
        ["Год", function (r) { return r.year; }],
        ["Как записана машина", function (r) { return r.written; }],
        ["Примечание", function (r) { return r.note; }]
      ], f.list));
      var ещё_п = показаны(f.list.length, f.n || 0);
      if (ещё_п) пк.appendChild(ещё_п);
    } else {
      пк.appendChild(пусто("Площадок с этой машиной в справочнике парка нет."));
    }
    out.push(пк);
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: v.name + " · машина", nodes: out };
  }

  // ── карточка узла ──────────────────────────────────────────────────────────
  function карточка_узла(v) {
    var out = [];
    var lead = узел("p", "lead");
    lead.appendChild(узел("span", null, "Дерево " + (НАПРАВЛЕНИЕ[v.dir] || "узлов") + ": "));
    (v.path || []).forEach(function (u) {
      lead.appendChild(к_узлу(u, v.library));
      lead.appendChild(узел("span", "sep", " › "));
    });
    lead.appendChild(узел("span", null, v.name));
    if (v.name_en) lead.appendChild(узел("span", null, " · " + v.name_en));
    out.push(шапка("Узел", узел("h1", null, v.name), [lead]));
    var родитель = v.path && v.path.length ? v.path[v.path.length - 1] : null;
    var факт = [
      ["Английское имя", v.name_en],
      ["Критичность", v.crit ? (КРИТ[v.crit] || v.crit) : null],
      ["Доступность помимо изготовителя", v.aftermarket],
      ["Примечание", v.note],
      ["Откуда в справочнике", v.source]
    ].filter(function (p) { return p[1] !== null && p[1] !== undefined && p[1] !== ""; });
    факт.unshift(["Входит в", родитель ? к_узлу(родитель, v.library) : "корень дерева " + (НАПРАВЛЕНИЕ[v.dir] || "")]);
    факт.push(["В прежнем разделе", прежние([["/library#section=component", "библиотека: узлы"]])]);
    out.push(факты(факт));

    var p = v.parts || {}, м = v.machines || {};
    var totals = узел("div", "totals");
    totals.appendChild(итог(число(p.total || 0), "деталей в узле и вложенных"));
    totals.appendChild(итог(не_посчитано(v, "машины") ? "—" : число(м.with_parts || 0), "машин с деталями в узле"));
    totals.appendChild(итог(не_посчитано(v, "машины") ? "—" : число(м.typical_n || 0),
      "машин " + (НАПРАВЛЕНИЕ[v.dir] || "направления") + " — узел типовой"));
    totals.appendChild(итог(число((v.children || []).length), "вложенных узлов"));
    out.push(totals);

    if (v.children && v.children.length) {
      var вл = раздел("Вложенные узлы", число(v.children.length));
      вл.appendChild(таблица([
        ["Узел", function (u) { return к_узлу(u, v.library); }],
        ["Критичность", function (u) { return u.crit ? (КРИТ[u.crit] || u.crit) : "—"; }],
        ["Деталей", function (u) { return узел("span", "num", число(u.parts || 0)); }],
        ["Вложенных", function (u) { return узел("span", "num", число(u.children || 0)); }]
      ], v.children));
      out.push(вл);
    }

    var мш = раздел("Машины", м.n ? число(м.n) : "");
    if (не_посчитано(v, "машины")) {
      мш.appendChild(пусто(НЕ_УСПЕЛИ));
    } else if (м.list && м.list.length) {
      var chips = узел("div", "chips");
      м.list.forEach(function (x) {
        chips.appendChild(машина(x, v.library, [вид_машины(x.kind),
          x.parts ? "деталей в узле " + число(x.parts) : "деталей в узле нет",
          x.typical ? "типово" : null].filter(Boolean).join(" · ")));
      });
      мш.appendChild(chips);
      var ещё_м = показаны(м.list.length, м.n || 0, ": сначала с деталями в узле");
      if (ещё_м) мш.appendChild(ещё_м);
      мш.appendChild(пусто("«Типово» — машина того же направления (" + (НАПРАВЛЕНИЕ[v.dir] || "—")
        + "): дерево узлов одно на все её машины. «Деталей в узле» — детали каталога машины, размеченные этим узлом "
        + "или вложенным; это измерено, а не выведено."));
    } else {
      мш.appendChild(пусто("Машин с деталями в этом узле нет, и машин направления в справочнике нет."));
    }
    out.push(мш);

    out.push(раздел_деталей(v, "Детали узла", "Деталей с этим узлом в каталоге нет.",
      p.here !== undefined && p.total > p.here ? "Из них в самом узле — " + число(p.here || 0) + ", остальные — во вложенных." : null));
    out.push(раздел_признаков(v, "У этого узла, вложенных и объемлющих признаков в справочнике нет."));
    out.push(раздел_дефектов(v, false, "Дефектов этого узла в справочнике нет."));
    out.push(раздел_ремонта(v, "Ремонтных операций по этому узлу в справочнике нет."));
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: v.name + " · узел", nodes: out };
  }

  // ── отказ словами ──────────────────────────────────────────────────────────
  function отказ(status, v, что) {
    var err = (v && v.error) || "";
    var h = "Карточка не открылась", p = "База не ответила. Попробуйте ещё раз.";
    if (status === 403) {
      h = "Нет доступа";
      p = v && v.need === "knowledge"
        ? "Машины и узлы — данные библиотеки: они открываются по праву «Библиотека оборудования и знаний» — его выдаёт владелец."
        : "Карточки открываются по праву «Поставщики» — его выдаёт владелец.";
    }
    else if (err === "not_a_code") { h = "Это не код детали"; p = "Так пишут марку материала, размер или стандарт — карточки у такого «кода» нет."; }
    else if (err === "not_found") {
      h = "Не нашлось";
      p = что === "code" ? "Этого кода нет ни в каталоге, ни в спросе, ни в КП."
        : что === "brand" ? "Такого бренда нет в реестре брендов."
        : что === "model" ? "Такой машины нет в справочнике машин."
        : что === "unit" ? "Такого узла нет в справочнике узлов." : "Такого поставщика нет в справочнике поставщиков.";
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
    var m = h.match(/^#(code|brand|supplier|model|unit)=(.+)$/);
    if (!m) {
      // Без адреса карточки (или «#» стёрли) — подсказка, а не прежняя карточка.
      ++номер;
      var тихо = узел("div", "status");
      тихо.appendChild(узел("h2", null, "Карточка портала"));
      тихо.appendChild(узел("p", null, "Код, бренд, поставщик, машина или узел открываются из строки поиска сверху; "
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
        var к_ = что === "code" ? карточка_кода(x.v) : что === "brand" ? карточка_бренда(x.v)
          : что === "model" ? карточка_машины(x.v) : что === "unit" ? карточка_узла(x.v) : карточка_поставщика(x.v);
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
