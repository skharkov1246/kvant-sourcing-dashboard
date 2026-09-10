-- База знаний КВАНТ: схема для PostgreSQL / Supabase.
-- Создана export_kb.py. Загрузка данных — \copy из kb_*.csv.gz.

DROP TABLE IF EXISTS kb_files;
CREATE TABLE kb_files (
  sha1           text,
  fid            text,
  filename       text,
  ext            text,
  bytes          integer,
  pages          integer,
  chars          integer,
  kind           text,
  side           text,
  field          text,
  title          text,
  lang           text,
  copies         integer,
  deals          integer,
  deal_id        integer,
  rfq_id         integer,
  supplier       text,
  company        text,
  won            integer,
  deal_sum       double precision,
  seg            text,
  positions      integer,
  priced         integer,
  brands         text,
  items          text,
  currency       text,
  date_min       text,
  date_max       text,
  inn            text,
  orgs           text,
  blank          integer,
  created        text,
  delivery_days  double precision,
  prepay_pct     double precision,
  defer_days     double precision,
  warranty_mo    double precision,
  penalty_pct    double precision,
  nmck           double precision
);
COMMENT ON TABLE kb_files IS 'Карточка документа: род, сторона, язык, даты, ИНН, номенклатура. Ключ — sha1 содержимого';
DROP TABLE IF EXISTS kb_catalog;
CREATE TABLE kb_catalog (
  pn_key         text,
  pn             text,
  brand          text,
  name           text,
  seg            text,
  mentions       integer,
  docs           integer,
  deals          integer,
  won            integer,
  lost           integer,
  first_seen     text,
  last_seen      text,
  cur            text,
  price_min      double precision,
  price_med      double precision,
  price_max      double precision,
  price_n        integer,
  sup_med        double precision,
  our_med        double precision,
  markup         double precision,
  qty_total      double precision,
  suppliers      text,
  customers      text
);
COMMENT ON TABLE kb_catalog IS 'Справочник оборудования: артикул → марка, цены, поставщики, исходы сделок';
DROP TABLE IF EXISTS kb_supplier_prices;
CREATE TABLE kb_supplier_prices (
  pn_key         text,
  pn             text,
  brand          text,
  supplier       text,
  cur            text,
  price_med      double precision,
  price_min      double precision,
  price_n        integer,
  first_seen     text,
  last_seen      text,
  deals          integer,
  won            integer
);
COMMENT ON TABLE kb_supplier_prices IS 'Цены поставщиков по артикулам: у кого брали и почём';
DROP TABLE IF EXISTS kb_positions;
CREATE TABLE kb_positions (
  id             integer,
  deal_id        integer,
  fid            text,
  seg            text,
  part_number    text,
  manufacturer   text,
  name           text,
  qty            double precision,
  unit           text,
  price          double precision,
  currency       text,
  price_total    double precision,
  source         text,
  company        text,
  won            integer,
  closed         text,
  deal_date      text,
  side           text,
  doc_kind       text
);
COMMENT ON TABLE kb_positions IS 'Номенклатура из документов: артикул, марка, количество, цена + исход сделки';
DROP TABLE IF EXISTS kb_deals;
CREATE TABLE kb_deals (
  id             integer,
  title          text,
  category       text,
  origin_cat     text,
  stage          text,
  semantic       text,
  won            integer,
  won_date       text,
  date_create    text,
  closedate      text,
  closed         text,
  age_days       integer,
  sum_eur        double precision,
  currency       text,
  company_id     integer,
  company        text,
  assigned       text,
  seg            text,
  item           text,
  brand          text
);
COMMENT ON TABLE kb_deals IS 'Сделки: воронка, стадия, исход, сумма, заказчик, ответственный, сегмент';
DROP TABLE IF EXISTS kb_rfq;
CREATE TABLE kb_rfq (
  id             integer,
  deal_id        integer,
  title          text,
  stage          text,
  supplier_id    integer,
  supplier       text,
  company_id     integer,
  currency       text,
  amount         double precision,
  created        text,
  updated        text,
  closed         text,
  chosen         integer,
  files          integer
);
COMMENT ON TABLE kb_rfq IS 'Запросы поставщикам (смарт-процесс 166): кому, на что, чем кончилось';
DROP TABLE IF EXISTS kb_price_pairs;
CREATE TABLE kb_price_pairs (
  deal_id        integer,
  key            text,
  kind           text,
  name_sup       text,
  name_our       text,
  price_sup      double precision,
  price_our      double precision,
  qty            double precision,
  ratio          double precision,
  currency       text,
  cur_sup        text,
  cur_our        text,
  price_sup_eur  double precision,
  price_our_eur  double precision
);
COMMENT ON TABLE kb_price_pairs IS 'Пары «цена поставщика ↔ наша цена» по одной позиции: наценка';
DROP TABLE IF EXISTS kb_brands;
CREATE TABLE kb_brands (
  id             integer,
  title          text,
  company_id     integer,
  created        text
);
COMMENT ON TABLE kb_brands IS 'Справочник марок из портала (смарт-процесс 176)';
DROP TABLE IF EXISTS kb_companies;
CREATE TABLE kb_companies (
  id             integer,
  title          text,
  industry       text
);
COMMENT ON TABLE kb_companies IS 'Контрагенты портала';

-- ключи: у карточки документа это содержимое, у справочника — артикул
ALTER TABLE kb_files ADD PRIMARY KEY (sha1);
ALTER TABLE kb_catalog ADD PRIMARY KEY (pn_key);

-- индексы под обычные вопросы к базе
CREATE INDEX IF NOT EXISTS ix_files_kind ON kb_files(kind);
CREATE INDEX IF NOT EXISTS ix_files_deal ON kb_files(deal_id);
CREATE INDEX IF NOT EXISTS ix_cat_brand ON kb_catalog(brand);
CREATE INDEX IF NOT EXISTS ix_cat_pn ON kb_catalog(pn);
CREATE INDEX IF NOT EXISTS ix_pos_pn ON kb_positions(part_number);
CREATE INDEX IF NOT EXISTS ix_pos_oem ON kb_positions(manufacturer);
CREATE INDEX IF NOT EXISTS ix_pos_deal ON kb_positions(deal_id);
CREATE INDEX IF NOT EXISTS ix_rfq_sup ON kb_rfq(supplier);

-- загрузка (запускать из каталога с выгрузкой):
-- \copy kb_files FROM PROGRAM 'zcat kb_files.csv.gz' CSV HEADER
-- \copy kb_catalog FROM PROGRAM 'zcat kb_catalog.csv.gz' CSV HEADER
-- \copy kb_supplier_prices FROM PROGRAM 'zcat kb_supplier_prices.csv.gz' CSV HEADER
-- \copy kb_positions FROM PROGRAM 'zcat kb_positions.csv.gz' CSV HEADER
-- \copy kb_deals FROM PROGRAM 'zcat kb_deals.csv.gz' CSV HEADER
-- \copy kb_rfq FROM PROGRAM 'zcat kb_rfq.csv.gz' CSV HEADER
-- \copy kb_price_pairs FROM PROGRAM 'zcat kb_price_pairs.csv.gz' CSV HEADER
-- \copy kb_brands FROM PROGRAM 'zcat kb_brands.csv.gz' CSV HEADER
-- \copy kb_companies FROM PROGRAM 'zcat kb_companies.csv.gz' CSV HEADER
