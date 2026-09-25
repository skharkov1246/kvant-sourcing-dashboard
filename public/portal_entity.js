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
// разделе» — /nomenclature#k=, /brands#b=, /brands#c=, /suppliers#e=.
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
  var API = { code: "/api/portal/code?k=", brand: "/api/portal/brand?b=", supplier: "/api/portal/supplier?s=" };
  var ИСТОЧНИК_ИМЕНИ = {
    "bitrix:title": "карточка компании в Битриксе", "bitrix:requisite": "реквизиты в Битриксе",
    "написание": "из справочника поставщиков", "реестр": "справочник поставщиков", "домен": "домен сайта"
  };
  var ЧАСТИ = {
    "предложения": "предложения", "аналоги": "аналоги", "машины": "машины и узлы", "кто делает": "кто делает деталь",
    "кому ещё писать": "кому ещё писать", "поставщики": "поставщиков", "бренды": "бренды", "коды": "коды"
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
  // Машина: имя — текстом (у библиотеки нет адреса машины), рядом — ссылка на
  // её раздел, подписанная именно как раздел.
  function машина(m, library) {
    var chip = узел("span", "chip");
    chip.appendChild(узел("span", null, m.name));
    var под = [m.kind, typeof m.parts === "number" ? "деталей " + число(m.parts) : null].filter(Boolean).join(" · ");
    if (под) chip.appendChild(узел("span", "src", под));
    if (m.brand && m.brand.name) {
      var бр = узел("span", "src");
      бр.appendChild(бренд(m.brand));
      chip.appendChild(бр);
    }
    var раз = узел("span", "src");
    if (library) {
      раз.appendChild(ссылка(m.segment ? "/library#segment=" + к(m.segment) : "/library",
        m.segment ? "раздел «" + (m.segment_name || m.segment) + "» в библиотеке →" : "библиотека →"));
    } else {
      раз.textContent = "библиотека закрыта правом";
    }
    chip.appendChild(раз);
    return chip;
  }

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

    var кому = раздел("Кому ещё писать");
    // Сначала — знают именно эту деталь (реестр исполнителей): адрес по детали,
    // а не по классу. Потом — давали цену по бренду позиции на другие коды.
    кому.appendChild(узел("h3", null, "Знают эту деталь — реестр исполнителей"
      + (v.makers_n ? " · " + число(v.makers_n) : "")));
    if (v.makers && v.makers.length) {
      кому.appendChild(таблица([
        ["Компания", function (m) { return m.name; }],
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
      кому.appendChild(пусто("Наличие, цена и срок — запись прошлой проверки у продавца, сейчас не перепроверены; дата проверки "
        + "не хранится. Реестр исполнителей со справочником поставщиков не связан — карточек у этих компаний нет."));
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
    var н = не_успели(v);
    if (н) out.push(н);
    return { title: (v.name || "Имя не известно") + " · поставщик", nodes: out };
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
        : что === "brand" ? "Такого бренда нет в реестре брендов." : "Такого поставщика нет в справочнике поставщиков.";
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
