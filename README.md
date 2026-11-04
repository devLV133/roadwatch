# RoadWatch

A Python API for reporting roadworks and road defects, tracking their progress, and finding nearby unresolved issues.

Built with **FastAPI**, **Pydantic**, and **SQLite**. Includes interactive API documentation, a runnable example, automated tests, and GitHub Actions. No API keys, map subscription, or external database are needed.

## Features

- Submit reports with a description, location, coordinates, category, and severity.
- Filter reports by status, category, and severity.
- Track work from `reported` to `in_progress` to `resolved`, with reopening supported.
- Keep a chronological status history with a note for every change.
- Find reports within a chosen radius, ordered by distance.
- Prioritise unresolved reports by severity, then oldest report first.
- Read summary counts by status and category.

**This is a local prototype. Reports are stored in this application and are not sent to a council or road authority.**

## Run locally

Requires Python 3.11 or newer. Clone the repository and enter its folder:

```bash
git clone https://github.com/devLV133/roadwatch.git
cd roadwatch
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn roadwatch.main:app --reload
```

The repository is private, so cloning requires GitHub access. If using a downloaded ZIP, extract it, open a terminal in its folder, and start with `python -m venv .venv`.

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1` instead.

Open **http://127.0.0.1:8000/docs**. Expand an endpoint, select **Try it out**, enter the request, and select **Execute**. This is the project's interactive interface; there is no separate dashboard.

The database is created automatically at `data/roadwatch.db`, relative to the directory where the server starts. Data survives restarts. Set `ROADWATCH_DB_PATH` to choose another SQLite file. In-memory databases are not supported because requests use separate connections.

## Try the demo

With the server running, open a second terminal in the same virtual environment:

```bash
python examples/demo.py
```

The demo creates three synthetic Manchester reports, moves one through the full workflow, and prints its history, nearby unresolved reports, and service totals. Each run creates three additional reports. Existing reports remain in place and are included in the relevant queries.

## Example workflow

### 1. Submit a roadwork report

```bash
curl -X POST http://127.0.0.1:8000/reports \
  -H "Content-Type: application/json" \
  -d '{"title":"Temporary lane closure","description":"Roadworks are blocking one lane near the junction.","location":"Example Street, Manchester","latitude":53.4808,"longitude":-2.2426,"category":"roadworks","severity":"high"}'
```

Use the returned `id` in subsequent requests. The examples below assume it is `1`.

### 2. Record work starting

```bash
curl -X PATCH http://127.0.0.1:8000/reports/1/status \
  -H "Content-Type: application/json" \
  -d '{"expected_status":"reported","status":"in_progress","note":"Maintenance crew assigned after inspection."}'
```

### 3. Resolve the report

```bash
curl -X PATCH http://127.0.0.1:8000/reports/1/status \
  -H "Content-Type: application/json" \
  -d '{"expected_status":"in_progress","status":"resolved","note":"Work completed and lane reopened."}'
```

### 4. Check history and nearby issues

```bash
curl http://127.0.0.1:8000/reports/1/history
curl "http://127.0.0.1:8000/reports/nearby?latitude=53.4808&longitude=-2.2426&radius_km=5"
curl http://127.0.0.1:8000/summary
```

Resolved reports are excluded from nearby results by default. Add `include_resolved=true` to include them.

## API reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check service and database availability |
| POST | `/reports` | Submit a report |
| GET | `/reports` | List reports, optionally filtering by `status`, `category`, and `severity` |
| GET | `/reports/{report_id}` | Read one report |
| PATCH | `/reports/{report_id}/status` | Change status with an explanatory note |
| GET | `/reports/{report_id}/history` | Read the chronological status history |
| GET | `/reports/queue` | List unresolved reports in priority order |
| GET | `/reports/nearby` | Search by latitude, longitude, and radius |
| GET | `/summary` | Read counts by status and category |

`/reports`, `/reports/queue`, and `/reports/{report_id}/history` accept `limit` (1–100, default 100) and `offset` (default 0). Nearby search accepts `limit`, a radius greater than zero and up to 100 km (default 5), and `include_resolved` (default false). Reports are listed newest first; history is oldest first.

Errors use `404` for missing reports, `409` for conflicting updates or disallowed transitions, and `422` for invalid input.

## Data and workflow rules

| Field | Values |
|---|---|
| Category | `roadworks`, `pothole`, `damaged_surface`, `obstruction` |
| Severity | `low`, `medium`, `high` |
| Status | `reported`, `in_progress`, `resolved` |

Severity records the submitter's assessment and is not independently verified. The queue places `high` before `medium` before `low`, then orders by original report creation time and ID. Both reported and in-progress items remain in the queue.

| Current status | Allowed next status |
|---|---|
| `reported` | `in_progress` or `resolved` |
| `in_progress` | `resolved` |
| `resolved` | `reported` |

Every status change requires a note and `expected_status`, which must match the current status. If another update has already changed it, the API returns `409`; refresh the report before trying again. Concurrent changes are serialised, and report changes and history entries commit together or roll back together.

Resolving sets `resolved_at`. Reopening clears it while retaining the previous resolution in history. A report's original `created_at` remains unchanged. Timestamps are stored in UTC. Repeating the current status is rejected instead of adding a duplicate event.

Titles, descriptions, severity, category, and coordinates are fixed after submission in this version. Reports are not automatically merged or deleted, so similar submissions can coexist. No reporter identity or contact details are collected.

## Distance calculation

Nearby search uses the Haversine formula to estimate great-circle distance. Results are filtered using the unrounded distance, then displayed to three decimal places. Equal distances are ordered by report ID. These are straight-line estimates, not driving routes, travel times, or access instructions.

The SQLite query scans candidate reports to calculate distances. This is suitable for a small prototype; a larger service would benefit from spatial indexing.

## Tests

```bash
python -m unittest discover -s tests -v
```

The 26 tests use isolated temporary databases and cover validation, lifecycle transitions, reopening, stale and concurrent updates, transaction rollback, persistence, filtering, pagination, prioritisation, location search, and summary counts.

GitHub Actions runs the same suite on Python 3.11, 3.12, and 3.13 for pushes and pull requests. See the repository's Actions tab for the current results.

## Project layout

| Path | Purpose |
|---|---|
| `src/roadwatch/main.py` | API endpoints and application factory |
| `src/roadwatch/models.py` | Input validation and response models |
| `src/roadwatch/db.py` | SQLite schema and transactions |
| `src/roadwatch/geo.py` | Distance calculation |
| `examples/demo.py` | Runnable HTTP example with synthetic reports |
| `tests/test_api.py` | Behaviour and integration tests |
| `.github/workflows/tests.yml` | GitHub Actions test matrix |

## Scope

This project demonstrates a working reporting workflow, persistence, validation, geospatial filtering, and automated testing. It does not control traffic lights, dispatch maintenance teams, verify incidents, or connect to council systems. There are no accounts or permissions. Use the default local binding while experimenting; shared deployment would need authentication, access controls, rate limits, backups, and database migrations.

MIT licence. See [LICENSE](LICENSE).
