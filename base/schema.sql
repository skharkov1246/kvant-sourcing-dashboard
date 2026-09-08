-- Фундамент КВАНТ: единая база по всему, что есть в Bitrix24.
-- Одна файловая БД, ноль инфраструктуры, полнотекстовый поиск по вложениям.
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS deals (
  id            INTEGER PRIMARY KEY,
  title         TEXT,
  category_id   TEXT,      -- воронка на сейчас
  category      TEXT,
  origin_cat_id TEXT,      -- воронка, где сделка ЗАВЕДЕНА (из истории стадий)
  origin_cat    TEXT,
  stage_id      TEXT,
  stage         TEXT,
  semantic      TEXT,      -- P в работе / S успех / F провал
  won           INTEGER,   -- дошла ли до воронки реализации (кат. 0)
  won_date      TEXT,
  date_create   TEXT,
  date_modify   TEXT,
  closedate     TEXT,
  closed        TEXT,
  age_days      INTEGER,
  sum_eur       REAL,      -- сумма в БАЗОВОЙ валюте портала (евро, BASE=Y у EUR)
  sum_orig      REAL,
  currency      TEXT,
  company_id    TEXT,
  company       TEXT,
  assigned_id   TEXT,
  assigned      TEXT,
  seg           TEXT,      -- сегмент оборудования (заполняет классификатор)
  item          TEXT,      -- что закупалось, нормализованно
  brand         TEXT
);
CREATE INDEX IF NOT EXISTS ix_deals_cat  ON deals(origin_cat_id);
CREATE INDEX IF NOT EXISTS ix_deals_seg  ON deals(seg);
CREATE INDEX IF NOT EXISTS ix_deals_won  ON deals(won);
CREATE INDEX IF NOT EXISTS ix_deals_date ON deals(date_create);

CREATE TABLE IF NOT EXISTS stage_events (
  deal_id     INTEGER,
  category_id TEXT,
  stage_id    TEXT,
  stage       TEXT,
  semantic    TEXT,
  at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_se_deal ON stage_events(deal_id);
CREATE INDEX IF NOT EXISTS ix_se_at   ON stage_events(at);

CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, title TEXT, industry TEXT);
CREATE TABLE IF NOT EXISTS users     (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS stages    (stage_id TEXT PRIMARY KEY, name TEXT, semantic TEXT, sort INTEGER, category_id TEXT);
CREATE TABLE IF NOT EXISTS categories(id TEXT PRIMARY KEY, name TEXT);

-- вложения сделок
CREATE TABLE IF NOT EXISTS files (
  fid        TEXT PRIMARY KEY,
  deal_id    INTEGER,
  field      TEXT,
  field_name TEXT,
  filename   TEXT,
  ext        TEXT,
  bytes      INTEGER,
  sha1       TEXT,
  url        TEXT,
  path       TEXT,
  status     TEXT,     -- new / downloaded / parsed / error
  err        TEXT,
  pages      INTEGER,
  chars      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_files_deal ON files(deal_id);
CREATE INDEX IF NOT EXISTS ix_files_ext  ON files(ext);
CREATE INDEX IF NOT EXISTS ix_files_st   ON files(status);

-- извлечённый текст вложений
CREATE TABLE IF NOT EXISTS file_text (fid TEXT, part INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS ix_ft_fid ON file_text(fid);

-- переписка и активности сделок
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY, deal_id INTEGER, type TEXT, direction TEXT,
  subject TEXT, created TEXT, body TEXT, n_files INTEGER
);
CREATE INDEX IF NOT EXISTS ix_act_deal ON activities(deal_id);

-- номенклатура, вытащенная из вложений и названий
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id INTEGER, fid TEXT, seg TEXT,
  raw TEXT, part_number TEXT, manufacturer TEXT, name TEXT,
  qty REAL, unit TEXT, price REAL, currency TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS ix_pos_pn   ON positions(part_number);
CREATE INDEX IF NOT EXISTS ix_pos_deal ON positions(deal_id);
CREATE INDEX IF NOT EXISTS ix_pos_seg  ON positions(seg);

CREATE TABLE IF NOT EXISTS segments (code TEXT PRIMARY KEY, name TEXT, scope TEXT, markers TEXT);

-- полнотекст: одним запросом ищем по вложениям, письмам и названиям сделок
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
  body, kind UNINDEXED, ref UNINDEXED, deal_id UNINDEXED, tokenize='unicode61 remove_diacritics 2'
);
