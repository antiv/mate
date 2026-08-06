-- Migration: Separate admin key for widget management endpoints
-- Version: V025
-- Database: SQLite
--
-- The embeddable api_key is public by design; it must not also authorise the
-- /widget/api admin routes. Existing rows get a generated admin key.

ALTER TABLE widget_api_keys ADD COLUMN admin_key TEXT;

UPDATE widget_api_keys
SET admin_key = 'wak_' || lower(hex(randomblob(24)))
WHERE admin_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ix_widget_api_keys_admin_key
    ON widget_api_keys (admin_key);
