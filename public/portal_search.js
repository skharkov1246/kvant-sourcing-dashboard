// ЕДИНЫЙ ПОИСК ПОРТАЛА — одна строка поиска на стартовой странице и на
// страницах раздела «Поставщики» (поставщики, номенклатура, бренды, счётчики).
//
// ЗАЧЕМ ОДИН ФАЙЛ. Четыре страницы и портал должны искать одинаково: одно
// слово — коды, бренды, поставщики, машины, узлы, и каждая строка ведёт на уже
// существующую страницу. Пять копий разошлись бы с первой правки, поэтому
// страница подключает этот файл одной строкой и больше ничего о поиске не знает.
//
// КУДА ВЕДУТ СТРОКИ (шаг 2, 25.09.2026). Код, бренд и поставщик — на свои
// карточки /p#code=, /p#brand=, /p#supplier= (public/portal_entity.js), где
// каждый код, бренд и компания снова ссылка. Прежние страницы не заменяются:
// у каждой строки вторая ссылка «в прежнем разделе» — /nomenclature#k=,
// /brands#b=, /suppliers#e= — ровно те адреса, что вели сюда до шага 2.
// Шаг 3: машина и узел — на карточки /p#model=, /p#unit=, а прежний раздел
// библиотеки (/library#segment=, /library#section=component) — второй ссылкой.
// Их данные — библиотека, и без её права строка остаётся без ссылок.
//
// ДВА ВИДА. Если на странице есть место <div id="kvps"> (стартовая страница) —
// строка и выдача рисуются в нём, в потоке страницы. Иначе сверху страницы
// встаёт тонкая полоска со строкой поиска, а выдача раскрывается под ней. Свои
// ссылки страниц («← Портал КВАНТ» и прочие) полоска не заменяет.
//
// ДАННЫЕ. /api/portal/search?q=… — агрегаты из базы (library/supabase/
// portal_schema.sql) за правом suppliers. Всё выводится через textContent:
// строка из базы разметкой не становится никогда.
(function () {
  "use strict";
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById("kvps-out")) return;   // подключён дважды — второй раз молчит

  var ВИДЫ = [
    { id: "код", name: "Коды" },
    { id: "бренд", name: "Бренды" },
    { id: "поставщик", name: "Поставщики" },
    { id: "машина", name: "Машины" },
    { id: "узел", name: "Узлы" }
  ];
  var НЕ_УСПЕЛИ = { "код": "коды", "бренд": "бренды", "поставщик": "поставщиков", "машина": "машины", "узел": "узлы" };
  // Страницы, которые сами переходят по смене адреса после «#». Остальные
  // читают его только при загрузке, и переход на них же нужно перезагрузить.
  var СЛУШАЮТ_ХЕШ = ["/brands", "/library", "/p"];
  var ЗАДЕРЖКА = 250;

  // Цвета — переменные страницы, где они есть, иначе те же значения, что у
  // раздела «Поставщики»: полоска выглядит своей и на портале, и в разделе.
  var CSS = [
    ".kvps-bar{position:relative;display:flex;align-items:center;gap:12px;padding:8px 16px;background:#0b0e13;border-bottom:1px solid var(--line,var(--ln,#2a3341));font:14px/1.4 'IBM Plex Sans',-apple-system,'Segoe UI',sans-serif;color:var(--ink,#e7ecf3)}",
    ".kvps-bar .kvps-home{color:var(--accent,var(--a,#73b6ff));text-decoration:none;font-size:12px;letter-spacing:.1em;text-transform:uppercase;white-space:nowrap}",
    ".kvps-field{position:relative;flex:1 1 auto;min-width:0;max-width:760px}",
    ".kvps-field input{width:100%;box-sizing:border-box;background:var(--card,#151a22);color:var(--ink,#e7ecf3);border:1px solid var(--line,var(--ln,#2a3341));border-radius:8px;padding:7px 11px;font:inherit;font-size:14px;min-width:0}",
    ".kvps-field input:focus{outline:2px solid var(--accent,var(--a,#73b6ff));outline-offset:1px}",
    ".kvps-panel{position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:60;max-height:70vh;overflow:auto;background:var(--card,#151a22);border:1px solid var(--line,var(--ln,#2a3341));border-radius:10px;box-shadow:0 12px 32px rgba(0,0,0,.45);padding:6px 0}",
    ".kvps-page{margin:0 0 26px}",
    ".kvps-page .kvps-field{max-width:none}",
    ".kvps-page .kvps-field input{font-size:16px;padding:12px 14px;border-radius:10px}",
    ".kvps-page .kvps-out{margin-top:10px;background:var(--card,#151a22);border:1px solid var(--line,var(--ln,#2a3341));border-radius:12px;padding:6px 0}",
    ".kvps-hint{color:var(--dim,#8b97a8);font-size:12.5px;margin:6px 2px 0}",
    ".kvps-status{padding:8px 14px;color:var(--dim,#a0acbd);font-size:13px}",
    ".kvps-status.kvps-err{color:#e7c488}",
    // Отступы группы — свои: правило «section{margin…}» страницы-хозяина
    // (портала, карточек) иначе ложится и на выдачу пустой полосой.
    "section.kvps-group{margin:0;padding:4px 0 6px}",
    ".kvps-group+.kvps-group{border-top:1px solid var(--line,var(--ln,#2a3341))}",
    ".kvps-gh{display:flex;gap:10px;align-items:baseline;padding:6px 14px 4px;font-size:11.5px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;color:var(--dim,#8b97a8)}",
    ".kvps-gh span{font-weight:400;letter-spacing:0;text-transform:none}",
    ".kvps-cols,.kvps-code{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);column-gap:14px;padding:0 14px}",
    ".kvps-cols{font-size:11.5px;color:var(--dim,#8b97a8);padding-bottom:2px}",
    ".kvps-row{display:block;padding:6px 14px;color:inherit;text-decoration:none;border-left:2px solid transparent}",
    ".kvps-code{padding-top:6px;padding-bottom:6px;border-left:2px solid transparent}",
    ".kvps-row:hover,.kvps-row:focus,.kvps-code:hover,.kvps-code:focus-within{background:rgba(115,182,255,.07);border-left-color:var(--accent,var(--a,#73b6ff));outline:none}",
    ".kvps-t{color:var(--accent,var(--a,#73b6ff));text-decoration:none;font-weight:500;overflow-wrap:anywhere}",
    ".kvps-row .kvps-t{display:block}",
    ".kvps-b{overflow-wrap:anywhere}.kvps-b a{color:var(--ink,#e7ecf3);text-decoration:none;border-bottom:1px dotted var(--dim,#8b97a8)}",
    ".kvps-b .kvps-word{color:var(--dim,#a0acbd)}",
    ".kvps-s{grid-column:1/-1;color:var(--dim,#a0acbd);font-size:12.5px;overflow-wrap:anywhere}",
    ".kvps-s a{color:var(--accent,var(--a,#73b6ff));text-decoration:none;margin-left:6px}",
    ".kvps-row .kvps-s{display:block}",
    ".kvps-ent{display:block;padding:6px 14px;border-left:2px solid transparent}",
    ".kvps-ent:hover,.kvps-ent:focus-within{background:rgba(115,182,255,.07);border-left-color:var(--accent,var(--a,#73b6ff))}",
    ".kvps-ent .kvps-t,.kvps-ent .kvps-s{display:block}",
    // На узком экране выдача встаёт на всю ширину полоски, а не строки поиска.
    "@media(max-width:560px){.kvps-bar{gap:10px}.kvps-bar .kvps-field{position:static}.kvps-field input{font-size:16px}.kvps-bar .kvps-panel{left:8px;right:8px;top:calc(100% - 2px)}}"
  ].join("\n");

  function узел(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = String(text);
    return el;
  }
  function число(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
  function путь(p) { return String(p || "/").replace(/\.html$/, "").replace(/\/+$/, "") || "/"; }

  function адрес(r, library) {
    var k = encodeURIComponent(r.key);
    if (r.kind === "код") return "/p#code=" + k;
    if (r.kind === "бренд") return "/p#brand=" + k;
    if (r.kind === "поставщик") return "/p#supplier=" + k;
    // Машина и узел — данные библиотеки: карточка открывается по её праву.
    if (!library) return null;
    if (r.kind === "машина") return "/p#model=" + k;
    if (r.kind === "узел") return "/p#unit=" + k;
    return null;
  }

  // Подпись чисел: что именно посчитано, словами, а не голыми цифрами.
  function счёт(r) {
    var c = r.counts || {}, out = [];
    if (r.kind === "код") {
      if (c.deals) out.push("сделок " + число(c.deals) + (c.capped ? "+" : ""));
      if (c.offers) out.push("строк КП " + число(c.offers) + (c.suppliers ? " от " + число(c.suppliers) + " пост." : ""));
      if (c.catalog) out.push("в каталоге");
      if (c.analogs) out.push("аналогов " + число(c.analogs));
    } else if (r.kind === "бренд") {
      if (c.spellings) out.push("написаний " + число(c.spellings));
      if (c.rows) out.push("строк данных " + число(c.rows));
    } else if (r.kind === "поставщик") {
      out.push(c.offers ? "строк КП " + число(c.offers) + (c.codes ? " по " + число(c.codes) + " кодам" : "") : "предложений нет");
    } else if (r.kind === "машина") {
      if (c.parts) out.push("деталей " + число(c.parts));
      if (c.fleet) out.push("площадок " + число(c.fleet));
    } else if (r.kind === "узел") {
      if (c.parts) out.push("деталей " + число(c.parts));
      if (c.children) out.push("вложенных узлов " + число(c.children));
    }
    return out.join(" · ");
  }

  // Прежний адрес той же строки — вторая ссылка «в прежнем разделе».
  function прежний(r) {
    var k = encodeURIComponent(r.key);
    if (r.kind === "код") return "/nomenclature#k=" + k;
    if (r.kind === "бренд") return "/brands#b=" + k;
    if (r.kind === "поставщик") return "/suppliers#e=" + k;
    if (r.kind === "машина") return r.segment ? "/library#segment=" + encodeURIComponent(r.segment) : "/library";
    if (r.kind === "узел") return "/library#section=component";
    return null;
  }

  function перейти(e) {
    // Переход на эту же страницу с другим «#»: страницы, которые читают адрес
    // только при загрузке, иначе остались бы на месте.
    var a = e.currentTarget;
    var href = a.getAttribute("href") || "";
    var i = href.indexOf("#");
    if (i < 0 || typeof location === "undefined") return;
    if (путь(href.slice(0, i)) !== путь(location.pathname)) return;
    if (СЛУШАЮТ_ХЕШ.indexOf(путь(location.pathname)) >= 0) return;
    e.preventDefault();
    location.hash = href.slice(i);
    if (location.reload) location.reload();
  }

  function ссылка(href, cls, text) {
    var a = узел("a", cls, text);
    a.setAttribute("href", href);
    a.href = href;
    a.addEventListener("click", перейти);
    return a;
  }

  function строка_кода(r) {
    var row = узел("div", "kvps-code");
    row.appendChild(ссылка(адрес(r), "kvps-t", r.title));
    var b = узел("div", "kvps-b");
    if (r.brand) {
      // Бренд реестра — ссылкой на карточку бренда; слово без ключа реестра —
      // ссылкой на поиск бренда по этому слову и приглушённо: это ещё не бренд.
      if (r.brand_key) b.appendChild(ссылка("/p#brand=" + encodeURIComponent(r.brand_key), null, r.brand));
      else b.appendChild(ссылка("/brands#n=" + encodeURIComponent(r.brand), "kvps-word", r.brand));
      b.setAttribute("title", {
        "каталог": "изготовитель по каталогу", "частота": "самый частый бренд в спросе и КП",
        "аналог": "изготовитель аналога", "написание": "слово спецификации, в реестре брендов не найдено"
      }[r.brand_src] || "");
    } else {
      b.appendChild(узел("span", "kvps-word", "не назван"));
    }
    row.appendChild(b);
    var s = узел("div", "kvps-s", [r.subtitle, счёт(r), r.source].filter(Boolean).join(" · "));
    s.appendChild(ссылка(прежний(r), null, "в прежнем разделе →"));
    s.appendChild(ссылка("/brands#c=" + encodeURIComponent(r.key), null, "код и цены →"));
    row.appendChild(s);
    return row;
  }

  // Бренд, поставщик, машина и узел: заголовок — карточка /p, в подписи — прежний раздел.
  // Две ссылки не вкладываются одна в другую, поэтому строка — не ссылка.
  function строка_карточки(r, library) {
    var row = узел("div", "kvps-ent");
    row.appendChild(ссылка(адрес(r, library), "kvps-t", r.title));
    var s = узел("span", "kvps-s", [r.subtitle, счёт(r)].filter(Boolean).join(" · "));
    s.appendChild(ссылка(прежний(r), null, "в прежнем разделе →"));
    row.appendChild(s);
    return row;
  }

  function строка(r, library) {
    var href = адрес(r, library);
    var row = href ? ссылка(href, "kvps-row", null) : узел("div", "kvps-row");
    row.appendChild(узел("span", "kvps-t", r.title));
    var под = [r.subtitle, счёт(r)].filter(Boolean).join(" · ");
    if (!href && (r.kind === "машина" || r.kind === "узел")) под = [под, "библиотека закрыта правом"].filter(Boolean).join(" · ");
    if (под) row.appendChild(узел("span", "kvps-s", под));
    return row;
  }

  function нарисовать(out, ответ) {
    out.textContent = "";
    var rows = ответ.rows || [];
    var порядок = [];
    var группы = {};
    rows.forEach(function (r) {
      if (!группы[r.kind]) { группы[r.kind] = []; порядок.push(r.kind); }
      группы[r.kind].push(r);
    });
    if (!rows.length) out.appendChild(узел("div", "kvps-status", "Ничего не нашлось по «" + ответ.q + "»."));
    порядок.forEach(function (вид) {
      var имя = (ВИДЫ.filter(function (v) { return v.id === вид; })[0] || { name: вид }).name;
      var g = узел("section", "kvps-group");
      var h = узел("div", "kvps-gh", имя);
      h.appendChild(узел("span", null, String(группы[вид].length)));
      g.appendChild(h);
      if (вид === "код") {
        // «Код» и «Бренд» — две колонки рядом: номер без изготовителя ничего
        // не говорит сорсеру, а изготовитель без номера — не позиция.
        var cols = узел("div", "kvps-cols");
        cols.appendChild(узел("span", null, "Код"));
        cols.appendChild(узел("span", null, "Бренд"));
        g.appendChild(cols);
        группы[вид].forEach(function (r) { g.appendChild(строка_кода(r)); });
      } else if (вид === "бренд" || вид === "поставщик" || ((вид === "машина" || вид === "узел") && ответ.library)) {
        // Машина и узел с правом на библиотеку — как бренд: заголовок ведёт на
        // карточку /p#model= или /p#unit=, прежний раздел библиотеки — второй
        // ссылкой. Без права — строка без ссылок и с пометкой (строка() ниже).
        группы[вид].forEach(function (r) { g.appendChild(строка_карточки(r, !!ответ.library)); });
      } else {
        группы[вид].forEach(function (r) { g.appendChild(строка(r, !!ответ.library)); });
      }
      out.appendChild(g);
    });
    if (ответ.partial && ответ.partial.length) {
      out.appendChild(узел("div", "kvps-status kvps-err", "Не успели посчитать: "
        + ответ.partial.map(function (k) { return НЕ_УСПЕЛИ[k] || k; }).join(", ")
        + ". Уточните запрос — длиннее слово ищется быстрее."));
    }
  }

  function причина(status, error) {
    if (status === 403) return "Поиск закрыт: нет права «Поставщики».";
    if (error === "search_not_installed") return "Поиск ещё не установлен в базе — нужна схема портала (скажите владельцу).";
    if (error === "search_key_missing") return "Поиск не подключён: у портала нет ключа базы.";
    if (error === "invalid_query") return "Запрос от двух до восьмидесяти знаков.";
    return "Поиск не ответил. Попробуйте ещё раз.";
  }

  function собрать(host, вид) {
    var field = узел("div", "kvps-field");
    var input = узел("input");
    input.setAttribute("type", "search");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("aria-label", "Поиск: код, бренд, поставщик, машина или узел");
    input.setAttribute("placeholder", "Код, бренд, поставщик (ИНН, домен, KV-S), машина, узел");
    input.setAttribute("maxlength", "80");
    field.appendChild(input);
    var out = узел("div", вид === "полоска" ? "kvps-panel" : "kvps-out");
    out.setAttribute("id", "kvps-out");
    out.setAttribute("aria-live", "polite");
    out.hidden = true;
    input.setAttribute("aria-controls", "kvps-out");
    field.appendChild(out);
    host.appendChild(field);
    if (вид === "страница") {
      host.appendChild(узел("div", "kvps-hint", "Одно слово: код детали или наш номер KV, бренд, "
        + "поставщик по названию, ИНН, домену или номеру KV-S, машина, узел. Enter — открыть первое."));
    }

    var таймер = null, номер = 0, ждём = null;
    function спросить() {
      var q = String(input.value || "").trim();
      if (ждём && ждём.abort) ждём.abort();
      ждём = null;
      if (Array.from(q).length < 2) { out.hidden = true; out.textContent = ""; return; }
      var мой = ++номер;
      ждём = typeof AbortController !== "undefined" ? new AbortController() : null;
      out.hidden = false;
      out.textContent = "";
      out.appendChild(узел("div", "kvps-status", "Ищем…"));
      fetch("/api/portal/search?q=" + encodeURIComponent(q),
            { headers: { Accept: "application/json" }, signal: ждём ? ждём.signal : undefined })
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (v) { return { status: r.status, v: v }; });
        })
        .then(function (x) {
          if (мой !== номер) return;          // пришёл ответ на прежнюю строку
          if (x.status !== 200 || !x.v || !Array.isArray(x.v.rows)) {
            out.textContent = "";
            out.appendChild(узел("div", "kvps-status kvps-err", причина(x.status, x.v && x.v.error)));
            return;
          }
          нарисовать(out, x.v);
        })
        .catch(function (e) {
          if (мой !== номер || (e && e.name === "AbortError")) return;
          out.textContent = "";
          out.appendChild(узел("div", "kvps-status kvps-err", причина(0, null)));
        });
    }
    input.addEventListener("input", function () {
      if (таймер) clearTimeout(таймер);
      таймер = setTimeout(спросить, ЗАДЕРЖКА);
    });
    function ссылки() { return out.querySelectorAll("a"); }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var a = ссылки()[0];
        if (a) { e.preventDefault(); a.click ? a.click() : (location.href = a.getAttribute("href")); }
      } else if (e.key === "ArrowDown") {
        var первая = ссылки()[0];
        if (первая) { e.preventDefault(); первая.focus(); }
      } else if (e.key === "Escape" && вид === "полоска") {
        out.hidden = true;
      }
    });
    out.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Escape") return;
      e.preventDefault();
      if (e.key === "Escape") { if (вид === "полоска") out.hidden = true; input.focus(); return; }
      var все = Array.prototype.slice.call(ссылки());
      var i = все.indexOf(document.activeElement);
      var j = e.key === "ArrowDown" ? i + 1 : i - 1;
      if (j < 0) input.focus();
      else if (все[j]) все[j].focus();
    });
    if (вид === "полоска") {
      input.addEventListener("focus", function () { if (out.childNodes && out.childNodes.length) out.hidden = false; });
      document.addEventListener("click", function (e) {
        if (!host.contains || !e.target || !host.contains(e.target)) out.hidden = true;
      });
    }
    return input;
  }

  var style = узел("style");
  style.textContent = CSS;
  (document.head || document.body).appendChild(style);

  var место = document.getElementById("kvps");
  if (место) {
    собрать(место, "страница");
  } else {
    var bar = узел("div", "kvps-bar");
    bar.setAttribute("id", "kvps-bar");
    bar.setAttribute("role", "search");
    var home = узел("a", "kvps-home", "КВАНТ");
    home.setAttribute("href", "/");
    home.href = "/";
    home.setAttribute("title", "Портал КВАНТ");
    bar.appendChild(home);
    собрать(bar, "полоска");
    document.body.insertBefore(bar, document.body.firstChild);
  }
})();
