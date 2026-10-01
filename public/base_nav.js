/* ОБЩАЯ НАВИГАЦИЯ БАЗЫ УПРАВЛЕНИЯ ПОСТАВЩИКАМИ.
 *
 * Распоряжение владельца 01.10.2026: единая точка входа — «Управление компанией»
 * (дашборд) и «Управление поставщиками» (база). Внутри базы человек переходит
 * между разделами, не возвращаясь на портал. До этого у каждой страницы была
 * своя строка ссылок, и на каждой — разный набор (на одной два раздела, на
 * другой четыре).
 *
 * Скрипт заменяет содержимое первой <nav class="topbar"> страницы одной и той
 * же строкой разделов; страницы без такой строки получают её первой в <body>.
 * Данных в скрипте нет — только адреса страниц. Библиотеку показывает, только
 * если у человека есть сайт knowledge (ответ /api/rights); без ответа — прячет:
 * лишняя ссылка, ведущая в 403, хуже недостающей.
 */
(function () {
  "use strict";
  if (typeof document === "undefined" || document.getElementById("kvbase-nav")) return;

  var РАЗДЕЛЫ = [
    { href: "/base", name: "Обзор", paths: ["/base", "/base/", "/base.html"] },
    { href: "/suppliers", name: "Поставщики", paths: ["/suppliers", "/suppliers/", "/suppliers.html"] },
    { href: "/nomenclature", name: "Номенклатура", paths: ["/nomenclature", "/nomenclature/", "/nomenclature.html"] },
    { href: "/brands", name: "Бренды и коды", paths: ["/brands", "/brands/", "/brands.html"] },
    { href: "/counters", name: "Счётчики", paths: ["/counters", "/counters/", "/counters.html"] },
    { href: "/library", name: "Библиотека", paths: ["/library", "/library/", "/library.html"], need: "knowledge" },
    { href: "/reports", name: "Отчёты", paths: ["/reports", "/reports/", "/reports.html"] },
  ];

  var CSS =
    "#kvbase-nav{display:flex;align-items:center;gap:6px 18px;flex-wrap:wrap;font-size:13px;color:#a0acbd}" +
    "#kvbase-nav .kvb-home{color:#a0acbd;text-decoration:none;white-space:nowrap}" +
    "#kvbase-nav .kvb-title{color:#e7ecf3;font-weight:600;white-space:nowrap}" +
    "#kvbase-nav ul{display:flex;flex-wrap:wrap;gap:4px;list-style:none;margin:0;padding:0}" +
    "#kvbase-nav li a{display:inline-block;padding:5px 10px;border-radius:7px;color:#73b6ff;text-decoration:none;border:1px solid transparent}" +
    "#kvbase-nav li a:hover{border-color:#2a3341}" +
    "#kvbase-nav li a[aria-current=page]{background:#1a222e;border-color:#2a3341;color:#e7ecf3}" +
    "#kvbase-nav li a:focus-visible,#kvbase-nav .kvb-home:focus-visible{outline:2px solid #73b6ff;outline-offset:2px}" +
    "@media print{#kvbase-nav{display:none}}";

  function узел(тег, класс, текст) {
    var e = document.createElement(тег);
    if (класс) e.className = класс;
    if (текст != null) e.textContent = текст;
    return e;
  }

  var путь = (location && location.pathname) || "/";
  var nav = document.querySelector("nav.topbar") || null;
  var своя = !nav;
  if (своя) nav = узел("nav", "topbar");
  while (nav.firstChild) nav.removeChild(nav.firstChild);
  nav.id = "kvbase-nav";
  nav.setAttribute("aria-label", "База управления поставщиками");

  var дом = узел("a", "kvb-home", "← Портал");
  дом.setAttribute("href", "/");
  nav.appendChild(дом);
  nav.appendChild(узел("span", "kvb-title", "Управление поставщиками"));
  var ul = узел("ul");
  var ждут = [];
  РАЗДЕЛЫ.forEach(function (р) {
    var li = узел("li");
    var a = узел("a", null, р.name);
    a.setAttribute("href", р.href);
    if (р.paths.indexOf(путь) >= 0) a.setAttribute("aria-current", "page");
    li.appendChild(a);
    if (р.need) { li.hidden = true; ждут.push({ li: li, need: р.need }); }
    ul.appendChild(li);
  });
  nav.appendChild(ul);

  var style = узел("style");
  style.textContent = CSS;
  (document.head || document.body).appendChild(style);
  if (своя) {
    var обёртка = document.querySelector(".wrap") || document.body;
    обёртка.insertBefore(nav, обёртка.firstChild);
  }

  if (ждут.length && typeof fetch === "function") {
    fetch("/api/rights", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var сайты = d.sites || [];
        ждут.forEach(function (w) { if (d.admin || сайты.indexOf(w.need) >= 0) w.li.hidden = false; });
      })
      .catch(function () { /* без ответа ссылка остаётся скрытой */ });
  }
})();
