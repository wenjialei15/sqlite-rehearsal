-- Synthetic readings on the AgriPulse v0.1.0 database schema.
CREATE TABLE readings (
    id INTEGER PRIMARY KEY, zone_id TEXT NOT NULL, device_id TEXT NOT NULL,
    temperature REAL NOT NULL, humidity REAL NOT NULL, soil_moisture REAL NOT NULL,
    light REAL NOT NULL, battery REAL NOT NULL, source TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX readings_zone_time ON readings(zone_id, recorded_at, id);
CREATE INDEX readings_device ON readings(device_id, id);
CREATE TABLE thresholds (zone_id TEXT PRIMARY KEY, moisture_min REAL NOT NULL);
INSERT INTO thresholds VALUES ('greenhouse-a', 42), ('field-c', 38);
INSERT INTO readings VALUES
    (101, 'greenhouse-a', 'sensor-a', 25, 60, 30, 1000, 90, 'device', '2025-01-14T09:00:00+00:00'),
    (102, 'greenhouse-a', 'sensor-a', 26, 59, 29, 1100, 89, 'device', '2025-01-14T10:00:00+00:00'),
    (103, 'field-c', 'sensor-b', 24, 64, 32, 1050, 94, 'device', '2025-01-14T10:00:00+00:00');
