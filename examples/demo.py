"""Submit synthetic reports and complete one report's workflow over HTTP."""

import json
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:8000"


def call(method, path, payload=None):
    request = Request(
        BASE_URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    reports = []
    for title, category, severity, latitude in [
        ("Temporary lane closure", "roadworks", "high", 53.4808),
        ("Pothole near station", "pothole", "medium", 53.4770),
        ("Uneven road surface", "damaged_surface", "low", 53.4880),
    ]:
        reports.append(call("POST", "/reports", {
            "title": title, "description": "Synthetic demonstration report, not a real road incident.",
            "location": "Example location, Manchester", "latitude": latitude,
            "longitude": -2.2426, "category": category, "severity": severity,
        }))

    report_id = reports[0]["id"]
    call("PATCH", f"/reports/{report_id}/status", {
        "expected_status": "reported", "status": "in_progress", "note": "Demo crew assigned.",
    })
    call("PATCH", f"/reports/{report_id}/status", {
        "expected_status": "in_progress", "status": "resolved", "note": "Demo work completed and checked.",
    })
    print(json.dumps({
        "created_report_ids": [report["id"] for report in reports],
        "completed_report_history": call("GET", f"/reports/{report_id}/history"),
        "nearby_unresolved": call("GET", "/reports/nearby?latitude=53.4808&longitude=-2.2426&radius_km=5"),
        "summary": call("GET", "/summary"),
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except URLError as error:
        raise SystemExit(f"Demo failed: {error}. Check that RoadWatch is running.") from error
