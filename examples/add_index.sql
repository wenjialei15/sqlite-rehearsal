BEGIN;
CREATE INDEX readings_source ON readings(source);
PRAGMA user_version = 1;
COMMIT;
