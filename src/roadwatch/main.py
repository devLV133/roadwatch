"""Run with: python -m uvicorn roadwatch.main:app --reload."""

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from .db import connect, initialize
from .models import (
    Category, NearbyReport, Report, ReportCreate, Severity, Status,
    StatusEvent, StatusUpdate, Summary,
)

TRANSITIONS = {
    "reported": {"in_progress", "resolved"},
    "in_progress": {"resolved"},
    "resolved": {"reported"},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database(request: Request):
    with connect(request.app.state.db_path) as connection:
        yield connection


DB = Annotated[sqlite3.Connection, Depends(database, scope="function")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def get_report_row(db: sqlite3.Connection, report_id: int) -> dict:
    row = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Report not found")
    return dict(row)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("ROADWATCH_DB_PATH", "data/roadwatch.db"))
    if str(path) == ":memory:":
        raise ValueError("Use file-backed SQLite; requests use separate connections.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(path)
        yield

    app = FastAPI(
        title="RoadWatch",
        description="A local prototype for roadwork and road defect reports. Reports are not sent to a council.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db_path = path

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health", tags=["Service"])
    def health(db: DB):
        db.execute("SELECT COUNT(*) FROM reports").fetchone()
        return {"status": "ok"}

    @app.post("/reports", response_model=Report, status_code=201, tags=["Reports"])
    def create_report(payload: ReportCreate, db: DB):
        values = payload.model_dump(mode="json") | {"timestamp": now()}
        cursor = db.execute(
            """INSERT INTO reports (title, description, location, latitude, longitude,
            category, severity, status, created_at, updated_at)
            VALUES (:title, :description, :location, :latitude, :longitude,
            :category, :severity, 'reported', :timestamp, :timestamp)""", values,
        )
        report_id = cursor.lastrowid
        db.execute(
            """INSERT INTO status_events (report_id, from_status, to_status, note, created_at)
            VALUES (?, NULL, 'reported', 'Report submitted.', ?)""",
            (report_id, values["timestamp"]),
        )
        return get_report_row(db, report_id)

    @app.get("/reports", response_model=list[Report], tags=["Reports"])
    def list_reports(
        db: DB, status: Status | None = None, category: Category | None = None,
        severity: Severity | None = None, limit: Limit = 100, offset: Offset = 0,
    ):
        rows = db.execute(
            """SELECT * FROM reports WHERE (? IS NULL OR status = ?)
            AND (? IS NULL OR category = ?) AND (? IS NULL OR severity = ?)
            ORDER BY id DESC LIMIT ? OFFSET ?""",
            (status, status, category, category, severity, severity, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    # Static paths must precede the report_id route.
    @app.get("/reports/queue", response_model=list[Report], tags=["Planning"])
    def priority_queue(db: DB, limit: Limit = 100, offset: Offset = 0):
        rows = db.execute(
            """SELECT * FROM reports WHERE status != 'resolved'
            ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            created_at, id LIMIT ? OFFSET ?""", (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    @app.get("/reports/nearby", response_model=list[NearbyReport], tags=["Planning"])
    def nearby_reports(
        db: DB,
        latitude: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
        longitude: Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)],
        radius_km: Annotated[float, Query(gt=0, le=100, allow_inf_nan=False)] = 5,
        include_resolved: bool = False, limit: Limit = 100,
    ):
        rows = db.execute(
            """SELECT * FROM (
                SELECT reports.*, distance_km(?, ?, latitude, longitude) AS distance
                FROM reports WHERE (? OR status != 'resolved')
            ) WHERE distance <= ? ORDER BY distance, id LIMIT ?""",
            (latitude, longitude, include_resolved, radius_km, limit),
        ).fetchall()
        results = []
        for row in rows:
            report = dict(row)
            distance = report.pop("distance")
            results.append({"report": report, "distance_km": round(distance, 3)})
        return results

    @app.get("/reports/{report_id}", response_model=Report, tags=["Reports"])
    def get_report(report_id: int, db: DB):
        return get_report_row(db, report_id)

    @app.patch("/reports/{report_id}/status", response_model=Report, tags=["Reports"])
    def update_status(report_id: int, payload: StatusUpdate, db: DB):
        db.execute("BEGIN IMMEDIATE")
        report = get_report_row(db, report_id)
        previous = report["status"]
        if payload.expected_status != previous:
            raise HTTPException(409, "The report status has changed. Refresh it before updating.")
        if payload.status not in TRANSITIONS[previous]:
            raise HTTPException(409, f"Cannot change status from {previous} to {payload.status.value}")
        timestamp = now()
        db.execute(
            "UPDATE reports SET status = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
            (payload.status, timestamp, timestamp if payload.status == Status.RESOLVED else None, report_id),
        )
        db.execute(
            """INSERT INTO status_events (report_id, from_status, to_status, note, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (report_id, previous, payload.status, payload.note, timestamp),
        )
        return get_report_row(db, report_id)

    @app.get("/reports/{report_id}/history", response_model=list[StatusEvent], tags=["Reports"])
    def history(report_id: int, db: DB, limit: Limit = 100, offset: Offset = 0):
        get_report_row(db, report_id)
        return [dict(row) for row in db.execute(
            "SELECT * FROM status_events WHERE report_id = ? ORDER BY id LIMIT ? OFFSET ?",
            (report_id, limit, offset),
        ).fetchall()]

    @app.get("/summary", response_model=Summary, tags=["Service"])
    def summary(db: DB):
        # One query keeps all totals within the same database snapshot.
        grouped = db.execute(
            "SELECT status, category, severity, COUNT(*) AS count FROM reports GROUP BY status, category, severity"
        ).fetchall()
        statuses = {status.value: 0 for status in Status}
        categories = {category.value: 0 for category in Category}
        urgent = 0
        for row in grouped:
            statuses[row["status"]] += row["count"]
            categories[row["category"]] += row["count"]
            if row["severity"] == "high" and row["status"] != "resolved":
                urgent += row["count"]
        return {
            "total_reports": sum(statuses.values()), "by_status": statuses,
            "by_category": categories, "high_severity_unresolved": urgent,
        }

    return app


app = create_app()
