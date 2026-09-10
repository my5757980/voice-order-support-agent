-- Simulated commerce backend.
--
-- Shaped like the real order service it will one day be replaced by, so swapping it
-- is an adapter change rather than a rewrite (spec TC-008).
--
-- The load-bearing line in this file is the UNIQUE constraint on
-- return_requests.idempotency_key. It makes duplicate prevention a database guarantee
-- rather than an application check, so FR-029 holds even if the calling logic is wrong.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    attributes  TEXT NOT NULL DEFAULT '{}',   -- JSON; merchant text, treated as DATA
    care_text   TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id     TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    placed_at    TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
                    'placed','processing','partially_shipped','shipped','delivered','cancelled')),
    total_cents  INTEGER NOT NULL CHECK (total_cents >= 0),
    currency     TEXT NOT NULL DEFAULT 'USD'
);

-- Every lookup filters by customer. Indexed because it is on the critical path and
-- must stay inside the 300 ms fast-tool budget.
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, placed_at DESC);

CREATE TABLE IF NOT EXISTS line_items (
    line_item_id            TEXT PRIMARY KEY,
    order_id                TEXT NOT NULL REFERENCES orders(order_id),
    product_id              TEXT NOT NULL REFERENCES products(product_id),
    quantity                INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price_cents        INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    -- Return eligibility is PER LINE ITEM, not per order: an order can be half
    -- returnable and the agent must be able to say so.
    return_state            TEXT NOT NULL CHECK (return_state IN (
                                'eligible','window_closed','returned','not_returnable')),
    return_window_closes_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_line_items_order ON line_items(order_id);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id          TEXT PRIMARY KEY,
    order_id             TEXT NOT NULL REFERENCES orders(order_id),
    carrier              TEXT NOT NULL,
    tracking_reference   TEXT NOT NULL,
    latest_scan_location TEXT,
    latest_scan_at       TEXT,
    expected_delivery    TEXT
);

-- One order may have several: split shipments are a required edge case, not an anomaly.
CREATE INDEX IF NOT EXISTS idx_shipments_order ON shipments(order_id);

CREATE TABLE IF NOT EXISTS policies (
    topic  TEXT PRIMARY KEY,
    body   TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS return_requests (
    return_id            TEXT PRIMARY KEY,
    order_id             TEXT NOT NULL REFERENCES orders(order_id),
    line_item_ids        TEXT NOT NULL,          -- JSON array
    reason               TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN (
                            'created','label_issued','in_transit','refunded','failed')),
    refund_estimate_days INTEGER NOT NULL,
    -- Derived from turn_id. UNIQUE is what makes FR-029 a guarantee.
    idempotency_key      TEXT NOT NULL UNIQUE,
    created_by_turn_id   TEXT NOT NULL,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalation_tickets (
    ticket_id               TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    summary                 TEXT NOT NULL,
    order_refs              TEXT NOT NULL DEFAULT '[]',
    actions_taken           TEXT NOT NULL DEFAULT '[]',
    transcript_ref          TEXT,
    expected_response_hours INTEGER NOT NULL,
    created_at              TEXT NOT NULL
);

-- Audit trail. Arguments are redacted BEFORE they are written (FR-064), and `actor`
-- keeps agent-taken actions distinguishable from human ones (FR-065).
CREATE TABLE IF NOT EXISTS tool_audit (
    audit_id           TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    turn_id            TEXT NOT NULL,
    tool_name          TEXT NOT NULL,
    arguments_redacted TEXT NOT NULL,
    outcome            TEXT NOT NULL CHECK (outcome IN (
                          'success','validation_failed','authz_denied','error','deduplicated')),
    duration_ms        INTEGER NOT NULL,
    actor              TEXT NOT NULL DEFAULT 'agent' CHECK (actor IN ('agent','human')),
    created_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_session ON tool_audit(session_id, created_at);
