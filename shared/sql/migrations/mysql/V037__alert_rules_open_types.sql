-- Migration: Alert rules accept new condition and destination types
-- Version: V037
-- Database: MySQL

-- On SQLite and PostgreSQL this drops the CHECK constraints V026 put on
-- condition_type and destination_type. V026 never added them on MySQL, so there is
-- nothing to change; the file keeps the version numbering in step across dialects.

SELECT 1;
