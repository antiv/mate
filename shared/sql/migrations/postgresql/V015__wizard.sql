-- Migration: Agent Builder Wizard (public iframe wizard + leads)
-- Version: V015
-- Database: PostgreSQL

CREATE TABLE IF NOT EXISTS wizard_sessions (
    id               SERIAL PRIMARY KEY,
    session_token    VARCHAR(64)  UNIQUE NOT NULL,
    tier             VARCHAR(20),
    step_data        TEXT,
    status           VARCHAR(30)  NOT NULL DEFAULT 'started',
    trial_project_id INTEGER      REFERENCES projects(id) ON DELETE SET NULL,
    widget_api_key   VARCHAR(255),
    root_agent_name  VARCHAR(255),
    client_ip        VARCHAR(64),
    origin           VARCHAR(500),
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at       TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wizard_sessions_token   ON wizard_sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_wizard_sessions_status  ON wizard_sessions(status);
CREATE INDEX IF NOT EXISTS idx_wizard_sessions_project ON wizard_sessions(trial_project_id);

CREATE TABLE IF NOT EXISTS wizard_leads (
    id                SERIAL PRIMARY KEY,
    wizard_session_id INTEGER      REFERENCES wizard_sessions(id) ON DELETE SET NULL,
    tier              VARCHAR(20),
    name              VARCHAR(255),
    email             VARCHAR(320) NOT NULL,
    company           VARCHAR(255),
    phone             VARCHAR(64),
    message           TEXT,
    requirements      TEXT,
    estimated_price   VARCHAR(64),
    trial_project_id  INTEGER,
    trial_widget_key  VARCHAR(255),
    status            VARCHAR(30)  NOT NULL DEFAULT 'new',
    client_ip         VARCHAR(64),
    created_at        TIMESTAMP    DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wizard_leads_email   ON wizard_leads(email);
CREATE INDEX IF NOT EXISTS idx_wizard_leads_status  ON wizard_leads(status);
CREATE INDEX IF NOT EXISTS idx_wizard_leads_created ON wizard_leads(created_at);
