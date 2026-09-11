/* Правки инженеров в библиотеке ГТУ: заметки, статусы, оценки — общие и подписанные.
 *
 * ЧТО СЛУЧИЛОСЬ. Заметка привязывалась к ключу nrm(имя компании) и жила только в
 * localStorage браузера. Схлопывание дублей (#142 17.08.2026, 1625 -> 1285 компаний;
 * #148 21.08.2026, склейка по базовому имени) и фильтры «нет сайта» / «конкуренты РФ»
 * выбрасывают карточку из выдачи через map.delete(k) — и заметка остаётся в хранилище
 * привязанной к ключу, которого в списке больше нет. Инженер видит: «комментарии
 * пропали». Ничего не стёрлось — стало недостижимым. Этот модуль их достаёт.
 *
 * КАК УСТРОЕНО.
 *   1. Поиск по НЕСКОЛЬКИМ ключам: текущий + все altNames склеенной карточки + сырое
 *      имя. Одного ключа мало — именно поэтому всё и потерялось.
 *   2. Осиротевшие (ни к чему не привязались) не исчезают молча: их собирает
 *      orphans() и страница показывает отдельным списком с переносом на живую карточку.
 *   3. Общее хранилище: запись уходит на сервер (таблица gt_notes через прокси /db
 *      того же сайта — ключ Supabase живёт в воркере, в браузер не попадает).
 *      Поэтому правка видна коллегам и переживает смену браузера.
 *   4. Автор не спрашивается и не вводится руками: воркер знает вошедшего по подписи
 *      Cloudflare Access и проставляет автора сам. Подделать нельзя.
 *   5. localStorage остаётся — как кэш для офлайна и как аварийная копия. Старые
 *      ключи НИКОГДА не удаляются: это последний рубеж, если сервер недоступен.
 *   6. Неотправленное складывается в очередь и досылается при следующей загрузке —
 *      обрыв сети не теряет заметку.
 */
(function (global) {
  'use strict';

  var API = '/db/rest/v1/gt_notes';       // прокси к Supabase на этом же origin
  var ME = '/api/me';                     // кто вошёл — отвечает воркер сайта
  var CACHE = 'gt_notes_cache_v1';        // зеркало серверных строк
  var OUTBOX = 'gt_notes_outbox_v1';      // неотправленное
  var SWEPT = 'gt_notes_swept_v1';        // какие старые правки уже перенесены

  // Старые браузерные хранилища библиотеки. Перечислены ЯВНО, но сверх списка
  // sweepLegacy() проходит по всем ключам, начинающимся на gt_ и kvant_pn —
  // чтобы забрать и то, что заводили до этого модуля и о чём здесь не знают.
  var LEGACY = {
    gt_edits_v2: 'index', gt_rfq_v1: 'rfq', gt_hot_v1: 'hot', gt_solar_v1: 'solar',
    gt_cummins_v1: 'cummins', gt_lm6000_v1: 'lm6000', gt_4000f_v1: 'f4000',
    gt_v643a_v1: 'v643a', gt_6b_v1: 'ms6001b', kvant_pn_v2: 'wizard'
  };

  function jget(k, dflt) {
    try { var v = JSON.parse(localStorage.getItem(k)); return v == null ? dflt : v; }
    catch (e) { return dflt; }
  }
  function jset(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  /* Ключ правки. Совпадает с nrm() страницы подбора: правки, заведённые до этого
     модуля, должны находиться по тому же ключу, иначе перенос бессмыслен. */
  function norm(s) {
    return String(s || '').toLowerCase().replace(/[^a-zа-я0-9]/g, '')
      .replace(/(coltd|colimited|ltd|llc|inc|gmbh|srl|spa|bv|sa|sadecv|corp|company|limited|group)+$/, '');
  }

  var state = { me: null, rows: jget(CACHE, []), live: false, scope: '' };

  function rowKey(r) { return [r.scope, r.key, r.kind].join(' '); }

  function merge(rows) {
    var by = {};
    (state.rows || []).concat(rows || []).forEach(function (r) {
      if (!r || !r.key) return;
      var k = rowKey(r), old = by[k];
      // выигрывает более поздняя запись: строки приходят и с сервера, и из очереди
      if (!old || String(r.at || '') >= String(old.at || '')) by[k] = r;
    });
    state.rows = Object.keys(by).map(function (k) { return by[k]; });
    jset(CACHE, state.rows);
  }

  /* Пометить строки как уже перенесённые: их не подметёт sweepLegacy. */
  function markSwept(rows) {
    var swept = jget(SWEPT, {});
    [].concat(rows || []).forEach(function (r) {
      if (r && r.key && r.text) swept[rowKey(r) + ' ' + r.text] = 1;
    });
    jset(SWEPT, swept);
  }

  function me() {
    if (state.me) return Promise.resolve(state.me);
    return fetch(ME, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { state.me = (d && d.email) ? d : { email: '' }; return state.me; })
      .catch(function () { state.me = { email: '' }; return state.me; });
  }

  /* Загрузка всех правок раздела. Сервер недоступен — работаем на кэше:
     страница не должна падать из-за базы. */
  function load(scope) {
    state.scope = scope || state.scope;
    return fetch(API + '?scope=eq.' + encodeURIComponent(state.scope) +
                 '&removed=is.false&select=*&order=at.asc', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('http ' + r.status)); })
      .then(function (rows) {
        state.live = true;
        merge(rows);
        // Пришедшее с сервера помечаем перенесённым. Иначе при следующей загрузке
        // sweepLegacy увидит заметку КОЛЛЕГИ, которую страница положила в своё
        // локальное хранилище, примет её за «старую местную» и отправит заново —
        // уже с почтой текущего пользователя. Подпись перевешивалась бы на чужого.
        markSwept(rows);
        return flushOutbox().then(function () { return state.rows; });
      })
      .catch(function () { state.live = false; return state.rows; });
  }

  /* Запись правки. Автор проставляется воркером по подписи входа; здесь он нужен
     только для мгновенного показа до ответа сервера. */
  function save(rec) {
    var row = {
      scope: rec.scope || state.scope,
      key: rec.key,
      names: rec.names || [],
      kind: rec.kind || 'note',
      text: rec.text == null ? '' : String(rec.text),
      author: (state.me && state.me.email) || '',
      at: rec.at || new Date().toISOString(),
      removed: !!rec.removed
    };
    merge([row]);                                  // сначала показать, потом отправить
    return post(row).catch(function () {
      var box = jget(OUTBOX, []);
      box.push(row);
      jset(OUTBOX, box);                           // не отправилось — не потеряно
      return row;
    });
  }

  function post(row) {
    return fetch(API, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Prefer': 'return=representation' },
      body: JSON.stringify(row)
    }).then(function (r) {
      if (!r.ok) throw new Error('http ' + r.status);
      return r.json().then(function (d) { merge(d); return d; });
    });
  }

  function flushOutbox() {
    var box = jget(OUTBOX, []);
    if (!box.length) return Promise.resolve(0);
    jset(OUTBOX, []);
    var left = [], n = 0;
    return box.reduce(function (p, row) {
      return p.then(function () {
        return post(row).then(function () { n++; }).catch(function () { left.push(row); });
      });
    }, Promise.resolve()).then(function () {
      if (left.length) jset(OUTBOX, left);
      return n;
    });
  }

  /* Правка по карточке. Ищем по ВСЕМ известным именам, а не по одному ключу:
     карточка могла приехать из склейки под чужим именем, и заметка висит на нём. */
  function get(kind, candidates) {
    var keys = keysOf(candidates), best = null;
    for (var i = 0; i < state.rows.length; i++) {
      var r = state.rows[i];
      if (r.kind !== kind || r.removed) continue;
      if (keys.indexOf(r.key) < 0) continue;
      if (!best || String(r.at || '') >= String(best.at || '')) best = r;
    }
    return best;
  }

  function keysOf(candidates) {
    var out = [];
    [].concat(candidates || []).forEach(function (c) {
      if (!c) return;
      var k = norm(c);
      if (k && out.indexOf(k) < 0) out.push(k);
      var raw = String(c);
      if (raw && out.indexOf(raw) < 0) out.push(raw);   // ключ мог прийти уже нормализованным
    });
    return out;
  }

  /* Осиротевшие: правки, чьи ключи не совпали ни с одной живой карточкой.
     Их нельзя прятать — именно они и есть «пропавшие комментарии». */
  function orphans(liveKeys) {
    var live = {};
    [].concat(liveKeys || []).forEach(function (k) { live[k] = 1; });
    return state.rows.filter(function (r) {
      return !r.removed && r.text && !live[r.key];
    });
  }

  /* Перенос правки на живую карточку — когда компания переименовалась или
     склеилась с другой. Старая строка помечается снятой, новая пишется с тем же
     текстом; автор новой — тот, кто переносил, поэтому исходный указывается в тексте. */
  function reattach(row, newKey, newNames) {
    var text = row.text + (row.author ? ' (перенесено, автор: ' + row.author + ')' : '');
    return save({ scope: row.scope, key: newKey, names: newNames || [], kind: row.kind, text: text })
      .then(function () {
        return save({ scope: row.scope, key: row.key, kind: row.kind, text: '', removed: true });
      });
  }

  /* Сбор старых браузерных хранилищ в общую базу. Идемпотентно: что уже перенесено,
     записано в SWEPT и второй раз не отправляется. Сами старые ключи остаются на
     месте — на случай, если сервер недоступен и переносить пока некуда. */
  function sweepLegacy() {
    var swept = jget(SWEPT, {}), found = [], scanned = [];
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (!k || !(LEGACY[k] || /^gt[_-]/.test(k) || k === 'kvant_pn_v2')) continue;
      if (k === CACHE || k === OUTBOX || k === SWEPT) continue;
      if (k === 'gt_edits_before_import') continue;   // копия перед импортом, не хранилище
      scanned.push(k);
      var scope = LEGACY[k] || k.replace(/^gt[_-]/, '').replace(/_v\d+$/, '');
      var data = jget(k, null);
      if (!data) continue;
      rowsFromLegacy(scope, data).forEach(function (row) {
        var sig = rowKey(row) + ' ' + row.text;
        if (swept[sig]) return;
        swept[sig] = 1;
        found.push(row);
      });
    }
    jset(SWEPT, swept);
    return { rows: found, keys: scanned };
  }

  /* Раскладка старых форматов в общие строки. Форматов два и они несовместимы:
     на подборе заметка — плоская строка на компанию, в базе PN — массив
     {t, d} с историей. Оба сводятся к одной строке на правку. */
  function rowsFromLegacy(scope, d) {
    var out = [];
    function push(kind, key, text, at) {
      if (!key || text == null || text === '') return;
      out.push({ scope: scope, key: String(key), names: [], kind: kind,
                 text: String(text), author: '', at: at || '', removed: false });
    }
    ['note', 'st', 'ex'].forEach(function (kind) {
      var box = d && d[kind];
      if (!box || typeof box !== 'object') return;
      Object.keys(box).forEach(function (key) { push(kind, key, box[key]); });
    });
    ['why', 'exwhy'].forEach(function (kind) {
      var box = d && d[kind];
      if (!box || typeof box !== 'object') return;
      Object.keys(box).forEach(function (key) {
        var v = box[key] || {};
        push(kind, key, v.why || '', v.at || '');
      });
    });
    if (d && d.pn) {
      ['st', 'note'].forEach(function (kind) {
        var box = d.pn[kind];
        if (!box || typeof box !== 'object') return;
        Object.keys(box).forEach(function (pn) {
          var v = box[pn];
          if (Array.isArray(v)) v.forEach(function (x) { push('pn_' + kind, pn, x && x.t, x && x.d); });
          else push('pn_' + kind, pn, v);
        });
      });
    }
    return out;
  }

  /* Разовый перенос старых правок в общую базу. Возвращает, сколько нашли и
     сколько отправили — страница показывает это инженеру, а не молчит. */
  function migrate() {
    var swept = sweepLegacy();
    if (!swept.rows.length) return Promise.resolve({ found: 0, sent: 0, keys: swept.keys });
    var sent = 0;
    return swept.rows.reduce(function (p, row) {
      return p.then(function () {
        return save(row).then(function () { sent++; }, function () {});
      });
    }, Promise.resolve()).then(function () {
      return { found: swept.rows.length, sent: sent, keys: swept.keys };
    });
  }

  /* Синхронизация простого постраничного хранилища {st:{}, note:{}} с общей базой.
     Страницы машин пишут правки десятками мест, и обвешивать каждое вызовом save()
     — верный способ однажды пропустить одно. Поэтому сравниваем со снимком
     прошлой синхронизации и отправляем только изменившееся. */
  var snaps = {};
  function syncStore(scope, obj, fields) {
    var prev = snaps[scope] || (snaps[scope] = {});
    var n = 0;
    (fields || ['st', 'note']).forEach(function (f) {
      var box = (obj && obj[f]) || {};
      var was = prev[f] || (prev[f] = {});
      Object.keys(box).forEach(function (k) {
        if (was[k] === box[k]) return;
        was[k] = box[k];
        n++;
        save({ scope: scope, key: k, kind: f, text: box[k] });
      });
      Object.keys(was).forEach(function (k) {
        if (box[k] !== undefined) return;
        delete was[k];
        n++;
        save({ scope: scope, key: k, kind: f, text: '', removed: true });   // снято, но не стёрто
      });
    });
    return n;
  }

  /* Снимок текущего состояния страницы — чтобы первая же синхронизация не
     отправила заново всё, что уже лежит в базе. */
  function seedStore(scope, obj, fields) {
    var prev = snaps[scope] = {};
    (fields || ['st', 'note']).forEach(function (f) {
      prev[f] = {};
      var box = (obj && obj[f]) || {};
      Object.keys(box).forEach(function (k) { prev[f][k] = box[k]; });
    });
  }

  /* Подпись под заметкой. Второй аргумент — текст, который реально показан на
     странице: если он разошёлся с тем, что лежит в базе (правили и локально, и
     у коллеги), подпись не ставится. Чужая фамилия под своим текстом хуже, чем
     отсутствие подписи. */
  function signature(row, shownText) {
    if (!row) return '';
    if (shownText != null && String(shownText).trim() !== String(row.text || '').trim()) return '';
    var who = row.author || 'без подписи';
    var when = String(row.at || '').slice(0, 10);
    return who + (when ? ' · ' + when : '');
  }

  global.KVN = {
    norm: norm, keysOf: keysOf, me: me, load: load, save: save, get: get,
    orphans: orphans, reattach: reattach, sweepLegacy: sweepLegacy, migrate: migrate,
    syncStore: syncStore, seedStore: seedStore, markSwept: markSwept,
    signature: signature, rowsFromLegacy: rowsFromLegacy,
    rows: function () { return state.rows; },
    live: function () { return state.live; }
  };
})(typeof window !== 'undefined' ? window : this);
