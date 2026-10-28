-- ============================================================
-- Multi-User System Schema Additions
-- Run this AFTER data.sql is already seeded
-- ============================================================

-- ── User accounts (hardcoded in auth/config.py but tracked here) ──
CREATE TABLE IF NOT EXISTS users (
    user_id     VARCHAR(20)  PRIMARY KEY,
    email       VARCHAR(100) UNIQUE NOT NULL,
    name        VARCHAR(100),
    role        VARCHAR(20)  NOT NULL CHECK (role IN ('admin','reviewer1','reviewer2')),
    db_schema   VARCHAR(50)  NOT NULL,
    is_active   BOOLEAN      DEFAULT TRUE,
    created_at  TIMESTAMP    DEFAULT NOW()
);

INSERT INTO users VALUES
('USR_001', 'admin@verixa.com',     'Verixa Admin',      'admin',     'admin',     TRUE, NOW()),
('USR_002', 'reviewer1@verixa.com', 'Priya Investigator','reviewer1', 'reviewer1', TRUE, NOW()),
('USR_003', 'reviewer2@verixa.com', 'Rahul Analyst',     'reviewer2', 'reviewer2', TRUE, NOW())
ON CONFLICT (user_id) DO NOTHING;

-- ── Review sessions — one row per investigation per user ──
CREATE TABLE IF NOT EXISTS review_sessions (
    session_id    SERIAL       PRIMARY KEY,
    user_id       VARCHAR(20)  REFERENCES users(user_id),
    entity_id     VARCHAR(20)  NOT NULL,
    scope         VARCHAR(20)  DEFAULT 'full',
    status        VARCHAR(20)  DEFAULT 'pending'
                               CHECK (status IN ('pending','in_progress','completed','approved','escalated')),
    risk_score    DECIMAL(4,3),
    risk_level    VARCHAR(20),
    agents_run    TEXT[],
    pdf_path      VARCHAR(500),
    findings_json JSONB,
    notes         TEXT,
    started_at    TIMESTAMP    DEFAULT NOW(),
    completed_at  TIMESTAMP,
    approved_by   VARCHAR(20)  REFERENCES users(user_id),
    approved_at   TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_session_user     ON review_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_session_entity   ON review_sessions(entity_id);
CREATE INDEX IF NOT EXISTS idx_session_status   ON review_sessions(status);

-- ── Audit trail — every action logged ─────────────────────
CREATE TABLE IF NOT EXISTS audit_trail (
    audit_id    SERIAL       PRIMARY KEY,
    user_id     VARCHAR(20)  REFERENCES users(user_id),
    session_id  INTEGER      REFERENCES review_sessions(session_id),
    entity_id   VARCHAR(20),
    action      VARCHAR(50)  NOT NULL,
    details     JSONB,
    ip_address  VARCHAR(50),
    created_at  TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user   ON audit_trail(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_trail(entity_id);

-- ── Per-user findings storage ──────────────────────────────
-- reviewer1 gets linkedin + itr findings
-- reviewer2 gets youtube + income findings
-- admin gets everything
CREATE TABLE IF NOT EXISTS linkedin_findings (
    id           SERIAL      PRIMARY KEY,
    session_id   INTEGER     REFERENCES review_sessions(session_id),
    entity_id    VARCHAR(20) NOT NULL,
    user_id      VARCHAR(20) REFERENCES users(user_id),
    found        BOOLEAN     DEFAULT FALSE,
    profile_url  TEXT,
    headline     TEXT,
    connections  INTEGER,
    employment_count INTEGER,
    risk_flags   TEXT[],
    income_signals TEXT[],
    raw_data     JSONB,
    created_at   TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS youtube_findings (
    id                    SERIAL      PRIMARY KEY,
    session_id            INTEGER     REFERENCES review_sessions(session_id),
    entity_id             VARCHAR(20) NOT NULL,
    user_id               VARCHAR(20) REFERENCES users(user_id),
    found                 BOOLEAN     DEFAULT FALSE,
    channel_name          TEXT,
    subscribers           INTEGER,
    total_videos          INTEGER,
    est_monthly_income_inr DECIMAL(12,2),
    monetized             BOOLEAN     DEFAULT FALSE,
    risk_flags            TEXT[],
    income_signals        TEXT[],
    raw_data              JSONB,
    created_at            TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS itr_credit_findings (
    id                   SERIAL      PRIMARY KEY,
    session_id           INTEGER     REFERENCES review_sessions(session_id),
    entity_id            VARCHAR(20) NOT NULL,
    user_id              VARCHAR(20) REFERENCES users(user_id),
    declared_income      DECIMAL(12,2),
    txn_volume           DECIMAL(14,2),
    risk_flags           TEXT[],
    income_signals       TEXT[],
    five_year_assessment TEXT[],
    raw_data             JSONB,
    created_at           TIMESTAMP   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS income_findings (
    id                       SERIAL      PRIMARY KEY,
    session_id               INTEGER     REFERENCES review_sessions(session_id),
    entity_id                VARCHAR(20) NOT NULL,
    user_id                  VARCHAR(20) REFERENCES users(user_id),
    declared_monthly_income  DECIMAL(12,2),
    good_credentials         TEXT[],
    lifestyle_signals        TEXT[],
    risk_flags               TEXT[],
    raw_data                 JSONB,
    created_at               TIMESTAMP   DEFAULT NOW()
);

-- ── PDF storage tracker ────────────────────────────────────
CREATE TABLE IF NOT EXISTS pdf_reports (
    pdf_id       SERIAL       PRIMARY KEY,
    session_id   INTEGER      REFERENCES review_sessions(session_id),
    user_id      VARCHAR(20)  REFERENCES users(user_id),
    entity_id    VARCHAR(20)  NOT NULL,
    filename     VARCHAR(500) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    file_size_kb INTEGER,
    sections     TEXT[],
    created_at   TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pdf_user   ON pdf_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_pdf_entity ON pdf_reports(entity_id);

-- ── Verification ──────────────────────────────────────────
SELECT 'users' AS table_name, COUNT(*) AS rows FROM users
UNION ALL SELECT 'review_sessions', COUNT(*) FROM review_sessions
UNION ALL SELECT 'audit_trail', COUNT(*) FROM audit_trail
UNION ALL SELECT 'pdf_reports', COUNT(*) FROM pdf_reports;