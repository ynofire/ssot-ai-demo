-- SSOT AI 컨설팅 프로토타입 — SQLite 스키마
-- 대시보드·채팅·입력폼이 모두 이 DB만 읽고 쓴다.
-- 금액 단위: 원(정수). 날짜: 'YYYY-MM-DD' 텍스트.
PRAGMA foreign_keys = ON;

CREATE TABLE suppliers (
    supplier_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0)
);

-- list_price/cost_price 는 "현재" 기준 단가. 과거 단가는 sales·purchases 행에 남는다.
CREATE TABLE products (
    product_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    category    TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('goods', 'service')),
    unit        TEXT NOT NULL,
    list_price  INTEGER NOT NULL CHECK (list_price >= 0),
    cost_price  INTEGER NOT NULL CHECK (cost_price >= 0)
);

CREATE TABLE clients (
    client_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    status      TEXT NOT NULL CHECK (status IN ('active', 'churned')),
    joined_date TEXT NOT NULL CHECK (joined_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    churn_date  TEXT CHECK (churn_date IS NULL OR churn_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    CHECK ((status = 'churned') = (churn_date IS NOT NULL))
);

-- 매출: sell_price / cost_price 는 판매 시점의 개당 단가. 수금은 판매 당월로 간주.
CREATE TABLE sales (
    sale_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    client_id   TEXT    NOT NULL REFERENCES clients(client_id),
    product_id  TEXT    NOT NULL REFERENCES products(product_id),
    qty         INTEGER NOT NULL CHECK (qty > 0),
    sell_price  INTEGER NOT NULL CHECK (sell_price >= 0),
    cost_price  INTEGER NOT NULL CHECK (cost_price >= 0)
);

-- 매입: 상품(goods) 입고 기준. 지급은 입고 당월로 간주.
CREATE TABLE purchases (
    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    supplier_id TEXT    NOT NULL REFERENCES suppliers(supplier_id),
    product_id  TEXT    NOT NULL REFERENCES products(product_id),
    qty         INTEGER NOT NULL CHECK (qty > 0),
    unit_cost   INTEGER NOT NULL CHECK (unit_cost >= 0)
);

-- 운영비: 임대료·인건비·공과금 등 (순현금흐름 계산용)
CREATE TABLE expenses (
    expense_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    category    TEXT    NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount >= 0)
);

CREATE TABLE inventory (
    product_id    TEXT PRIMARY KEY REFERENCES products(product_id),
    qty           INTEGER NOT NULL CHECK (qty >= 0),
    reorder_level INTEGER NOT NULL CHECK (reorder_level >= 0),
    updated_at    TEXT NOT NULL
);

-- 변경이력: 전원 열람 가능. 폼 제출 시 저장과 동시에 자동 기록한다.
CREATE TABLE change_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('대표', '구매부', '영업부', '경리')),
    table_name  TEXT NOT NULL,
    record_id   TEXT,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
);

CREATE INDEX idx_sales_date        ON sales(date);
CREATE INDEX idx_sales_client_date ON sales(client_id, date);
CREATE INDEX idx_sales_product     ON sales(product_id, date);
CREATE INDEX idx_purchases_date    ON purchases(date);
CREATE INDEX idx_expenses_date     ON expenses(date);
CREATE INDEX idx_change_log_ts     ON change_log(timestamp);
