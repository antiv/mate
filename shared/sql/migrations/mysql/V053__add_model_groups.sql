-- Migration: model groups
-- Version: V053
-- Database: MySQL
--
-- model_groups: a named, ordered list of models that agents will name as group/<name>
-- (shared/utils/model_groups.py). models is a JSON list of {"model", "confirmed"}, as a
-- list is always read and written whole; confirmed names what an administrator says of
-- the model where LiteLLM doesn't know it.
--
-- Six groups to start from, without models. They are rows like any other: one that is
-- deleted doesn't come back.

CREATE TABLE IF NOT EXISTS model_groups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(40) NOT NULL,
    label VARCHAR(100) NOT NULL,
    description VARCHAR(500),
    needs_tools BOOLEAN NOT NULL DEFAULT FALSE,
    needs_images BOOLEAN NOT NULL DEFAULT FALSE,
    own_servers_only BOOLEAN NOT NULL DEFAULT FALSE,
    models TEXT NOT NULL,
    updated_by VARCHAR(255),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_model_groups_name UNIQUE (name)
);

INSERT IGNORE INTO model_groups (name, label, description, needs_tools, needs_images, own_servers_only, models)
VALUES
    ('standard', 'Standard', 'Most agents: conversation and tools', TRUE, FALSE, FALSE, '[]'),
    ('fast', 'Fast', 'Routing, classification, short answers and many calls', TRUE, FALSE, FALSE, '[]'),
    ('reasoning', 'Reasoning', 'Planning, analysis and many steps', TRUE, FALSE, FALSE, '[]'),
    ('coding', 'Coding', 'Code, SQL and structured output', TRUE, FALSE, FALSE, '[]'),
    ('vision', 'Vision', 'Images and scanned documents', TRUE, TRUE, FALSE, '[]'),
    ('local', 'Local', 'Data that must not leave our servers', TRUE, FALSE, TRUE, '[]');
