"""Integration tests with isolated, temporary databases."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from roadwatch.geo import distance_km
from roadwatch.main import create_app


class RoadWatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "nested" / "roadwatch.db"
        self.client = self.enterContext(TestClient(create_app(self.db_path)))

    def report(self, **overrides):
        payload = {
            "title": "Pothole near junction", "description": "A large pothole in the left lane.",
            "location": "Example Street, Manchester", "latitude": 53.4808,
            "longitude": -2.2426, "category": "pothole", "severity": "medium",
        } | overrides
        response = self.client.post("/reports", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def change(self, item, target, expected=None):
        return self.client.patch(f"/reports/{item['id']}/status", json={
            "expected_status": expected or item["status"], "status": target,
            "note": "Updated after a site inspection.",
        })

    def history(self, item):
        return self.client.get(f"/reports/{item['id']}/history").json()

    def nearby(self, **overrides):
        response = self.client.get("/reports/nearby", params={
            "latitude": 53.4808, "longitude": -2.2426, "radius_km": 5,
        } | overrides)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_documentation_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("/reports/nearby", self.client.get("/openapi.json").json()["paths"])

    def test_create_and_read_report(self):
        item = self.report(title="  Pothole near junction  ")
        self.assertEqual(item["title"], "Pothole near junction")
        self.assertEqual(item["status"], "reported")
        self.assertIsNone(item["resolved_at"])
        self.assertEqual(item["created_at"], item["updated_at"])
        self.assertEqual(self.client.get(f"/reports/{item['id']}").json(), item)
        event = self.history(item)[0]
        self.assertIsNone(event["from_status"])
        self.assertEqual(event["to_status"], "reported")

    def test_invalid_reports_are_rejected(self):
        valid = self.report()
        for key in ["id", "status", "created_at", "updated_at", "resolved_at"]:
            valid.pop(key)
        cases = [
            {"title": "  "}, {"description": "short"}, {"location": ""},
            {"latitude": 91}, {"longitude": -181}, {"latitude": "nan"},
            {"severity": "extreme"}, {"category": "unknown"},
            {"unexpected": "value"}, {"status": "resolved"},
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/reports", json=valid | invalid).status_code, 422)
        self.assertEqual(self.client.get("/summary").json()["total_reports"], 1)

    def test_missing_resources(self):
        self.assertEqual(self.client.get("/reports/999").status_code, 404)
        self.assertEqual(self.client.get("/reports/999/history").status_code, 404)
        self.assertEqual(self.change({"id": 999, "status": "reported"}, "resolved").status_code, 404)

    def test_report_filters_combine(self):
        matching = self.report(severity="high", category="roadworks")
        self.report(severity="high", category="pothole")
        self.report(severity="low", category="roadworks")
        result = self.client.get("/reports?category=roadworks&severity=high&status=reported").json()
        self.assertEqual([row["id"] for row in result], [matching["id"]])

    def test_pagination(self):
        first = self.report()
        second = self.report()
        self.assertEqual(self.client.get("/reports?limit=1").json()[0]["id"], second["id"])
        self.assertEqual(self.client.get("/reports?limit=1&offset=1").json()[0]["id"], first["id"])
        self.assertEqual(self.client.get("/reports?limit=101").status_code, 422)
        self.assertEqual(self.client.get("/reports?offset=-1").status_code, 422)

    def test_complete_report_lifecycle(self):
        original = self.report()
        response = self.change(original, "in_progress")
        self.assertEqual(response.status_code, 200)
        progress = response.json()
        response = self.change(progress, "resolved")
        self.assertEqual(response.status_code, 200)
        resolved = response.json()
        self.assertIsNotNone(resolved["resolved_at"])
        self.assertEqual(resolved["created_at"], original["created_at"])
        self.assertEqual(resolved["resolved_at"], resolved["updated_at"])
        events = self.history(original)
        self.assertEqual([event["to_status"] for event in events], ["reported", "in_progress", "resolved"])
        self.assertEqual(events[-1]["note"], "Updated after a site inspection.")

    def test_direct_resolution(self):
        item = self.report()
        self.assertEqual(self.change(item, "resolved").status_code, 200)

    def test_reopen_preserves_history_and_clears_resolution_time(self):
        item = self.report()
        resolved = self.change(item, "resolved").json()
        reopened = self.change(resolved, "reported")
        self.assertEqual(reopened.status_code, 200)
        self.assertIsNone(reopened.json()["resolved_at"])
        self.assertEqual(len(self.history(item)), 3)
        self.assertEqual(len(self.client.get("/reports/queue").json()), 1)

    def test_invalid_transition_does_not_change_history(self):
        item = self.change(self.report(), "in_progress").json()
        self.assertEqual(self.change(item, "reported").status_code, 409)
        self.assertEqual(len(self.history(item)), 2)

    def test_no_op_transition_rejected(self):
        item = self.report()
        self.assertEqual(self.change(item, "reported").status_code, 409)
        self.assertEqual(len(self.history(item)), 1)

    def test_stale_update_rejected(self):
        item = self.report()
        self.change(item, "in_progress")
        self.assertEqual(self.change(item, "resolved").status_code, 409)
        self.assertEqual(self.client.get(f"/reports/{item['id']}").json()["status"], "in_progress")
        self.assertEqual(len(self.history(item)), 2)

    def test_status_update_requires_note_and_expected_status(self):
        item = self.report()
        for payload in [
            {"status": "resolved", "note": "Checked on site"},
            {"expected_status": "reported", "status": "resolved", "note": "  "},
            {"expected_status": "reported", "status": "closed", "note": "Checked on site"},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.patch(f"/reports/{item['id']}/status", json=payload).status_code, 422)

    def test_status_and_history_roll_back_together(self):
        item = self.report()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("""CREATE TRIGGER reject_event BEFORE INSERT ON status_events
                BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.change(item, "resolved")
        self.assertEqual(self.client.get(f"/reports/{item['id']}").json(), item)
        self.assertEqual(len(self.history(item)), 1)

    def test_initial_report_rolls_back_if_history_fails(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("""CREATE TRIGGER reject_event BEFORE INSERT ON status_events
                BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.report()
        self.assertEqual(self.client.get("/reports").json(), [])

    def test_concurrent_updates_do_not_overwrite_each_other(self):
        item = self.report()
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.change(item, "in_progress").status_code, range(2)))
        self.assertEqual(sorted(codes), [200, 409])
        self.assertEqual(len(self.history(item)), 2)

    def test_priority_queue_orders_severity_then_age(self):
        low = self.report(severity="low")
        high_first = self.report(severity="high")
        medium = self.report(severity="medium")
        high_second = self.report(severity="high")
        resolved = self.report(severity="high")
        self.change(resolved, "resolved")
        queue = self.client.get("/reports/queue").json()
        self.assertEqual([item["id"] for item in queue],
                         [high_first["id"], high_second["id"], medium["id"], low["id"]])

    def test_nearby_radius_and_order(self):
        far = self.report(latitude=53.49)
        close = self.report()
        self.report(latitude=54.0)
        result = self.nearby()
        self.assertEqual([item["report"]["id"] for item in result], [close["id"], far["id"]])
        self.assertEqual(result[0]["distance_km"], 0)
        self.assertGreater(result[1]["distance_km"], 1)
        self.assertEqual(len(self.nearby(limit=1)), 1)

    def test_nearby_hides_resolved_by_default(self):
        item = self.report()
        self.change(item, "resolved")
        self.assertEqual(self.nearby(), [])
        self.assertEqual(len(self.nearby(include_resolved=True)), 1)

    def test_nearby_handles_international_date_line(self):
        item = self.report(latitude=0, longitude=-179.9)
        result = self.nearby(latitude=0, longitude=179.9, radius_km=30)
        self.assertEqual(result[0]["report"]["id"], item["id"])
        self.assertAlmostEqual(result[0]["distance_km"], 22.239, places=3)

    def test_nearby_validates_coordinates_and_radius(self):
        for invalid in [{"latitude": 91}, {"longitude": "nan"}, {"radius_km": 0}, {"radius_km": 101}]:
            with self.subTest(invalid=invalid):
                response = self.client.get("/reports/nearby", params={"latitude": 0, "longitude": 0} | invalid)
                self.assertEqual(response.status_code, 422)

    def test_summary_counts_and_zero_categories(self):
        self.report(severity="high")
        resolved = self.report(severity="high", category="roadworks")
        self.change(resolved, "resolved")
        self.report(category="obstruction")
        summary = self.client.get("/summary").json()
        self.assertEqual(summary["total_reports"], 3)
        self.assertEqual(summary["by_status"], {"reported": 2, "in_progress": 0, "resolved": 1})
        self.assertEqual(summary["by_category"], {"roadworks": 1, "pothole": 1, "damaged_surface": 0, "obstruction": 1})
        self.assertEqual(summary["high_severity_unresolved"], 1)

    def test_empty_service(self):
        self.assertEqual(self.client.get("/summary").json()["total_reports"], 0)
        self.assertEqual(self.client.get("/reports/queue").json(), [])
        self.assertEqual(self.nearby(), [])

    def test_persistence_across_app_instances(self):
        item = self.report()
        with TestClient(create_app(self.db_path)) as client:
            self.assertEqual(client.get(f"/reports/{item['id']}").json(), item)
            self.assertEqual(len(client.get(f"/reports/{item['id']}/history").json()), 1)

    def test_sql_like_content_is_data(self):
        item = self.report(title="'); DROP TABLE reports; --")
        self.assertEqual(self.client.get(f"/reports/{item['id']}").json()["title"], item["title"])
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_known_distances(self):
        self.assertEqual(distance_km(0, 0, 0, 0), 0)
        self.assertAlmostEqual(distance_km(0, 0, 0, 1), 111.195, places=3)
        self.assertAlmostEqual(distance_km(0, 0, 0, 180), 20015.114, places=3)


if __name__ == "__main__":
    unittest.main()
