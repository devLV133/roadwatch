"""SQLite schema and short-lived, transactional connections."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .geo import distance_km

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    location TEXT NOT NULL,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    category TEXT NOT NULL CHECK (category IN ('roadworks', 'pothole', 'damaged_surface', 'obstruction')),
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('reported', 'in_progress', 'resolved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK ((status = 'resolved' AND resolved_at IS NOT NULL)
        OR (status != 'resolved' AND resolved_at IS NULL))
);
CREATE INDEX IF NOT EXISTS report_filters ON reports (status, category, severity);
CREATE TABLE IF NOT EXISTS status_events (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    from_status TEXT CHECK (from_status IN ('reported', 'in_progress', 'resolved')),
    to_status TEXT NOT NULL CHECK (to_status IN ('reported', 'in_progress', 'resolved')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS report_events ON status_events (report_id, id);
"""


@contextmanager
def connect(path: Path):
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function("distance_km", 4, distance_km, deterministic=True)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
