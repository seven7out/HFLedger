import datetime
import http.server
import json
import os
import tempfile
import threading
import unittest

from core import production_health


UTC = datetime.timezone.utc


class Clock:
    def __init__(self):
        self.value = datetime.datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += datetime.timedelta(seconds=seconds)


class ProductionHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(os.path.join(self.temp.name, "monitor.json"))
        self.write_config("https://status.example.test/health")

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, endpoint, **extra):
        value = {
            "version": 1,
            "endpoint": endpoint,
            "intervalSeconds": 60,
        }
        value.update(extra)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.write("\n")
        os.chmod(self.path, 0o600)

    def monitor(self, results, clock=None):
        remaining = iter(results)
        return production_health.ProductionHealthMonitor(
            self.path,
            now_fn=clock or Clock(),
            probe_fn=lambda _endpoint: next(remaining),
            autostart=False,
        )

    def test_three_failures_degrade_and_one_success_recovers(self):
        clock = Clock()
        monitor = self.monitor([True, False, False, False, True], clock=clock)

        self.assertEqual(monitor.snapshot()["monitorState"], "starting")
        self.assertTrue(monitor.check_once())
        self.assertEqual(monitor.snapshot()["state"], "healthy")

        for expected_failures in (1, 2):
            clock.advance(60)
            self.assertFalse(monitor.check_once())
            snapshot = monitor.snapshot()
            self.assertEqual(snapshot["state"], "healthy")
            self.assertEqual(snapshot["monitorState"], "retrying")
            self.assertNotIn("consecutive", json.dumps(snapshot).lower())

        clock.advance(60)
        self.assertFalse(monitor.check_once())
        degraded = monitor.snapshot()
        self.assertEqual(degraded["state"], "degraded")
        self.assertEqual(
            degraded["summary"],
            "The live service is not responding to its health check.")

        clock.advance(60)
        self.assertTrue(monitor.check_once())
        recovered = monitor.snapshot()
        self.assertEqual(recovered["state"], "healthy")
        self.assertEqual(recovered["monitorState"], "active")
        self.assertEqual(recovered["lastCheckedAt"], recovered["lastHealthyAt"])

    def test_stale_monitor_is_plain_language_degradation(self):
        clock = Clock()
        monitor = self.monitor([True], clock=clock)
        monitor.check_once()
        clock.advance(181)

        snapshot = monitor.snapshot()

        self.assertEqual(snapshot["state"], "degraded")
        self.assertEqual(snapshot["monitorState"], "stale")
        self.assertEqual(snapshot["summary"], "Production monitoring has stopped updating.")

    def test_snapshot_never_exposes_endpoint_or_response_details(self):
        monitor = self.monitor([False, False, False])
        for _index in range(3):
            monitor.check_once()

        encoded = json.dumps(monitor.snapshot())

        self.assertNotIn("status.example.test", encoded)
        self.assertNotIn("endpoint", encoded.lower())
        self.assertNotIn("failure", encoded.lower())

    def test_config_rejects_unsafe_or_ambiguous_endpoints(self):
        rejected = (
            "http://status.example.test/health",
            "http://127.0.0.1:18181/health",
            "https://person:secret@status.example.test/health",
            "https://status.example.test/health?token=secret",
            "https://status.example.test/health#details",
            "data:text/plain,healthy",
        )
        for endpoint in rejected:
            with self.subTest(endpoint=endpoint):
                self.write_config(endpoint)
                with self.assertRaises(ValueError):
                    production_health.ProductionHealthMonitor(self.path, autostart=False)

    def test_loopback_http_is_available_only_for_local_verification(self):
        self.assertEqual(
            production_health.validate_endpoint(
                "http://127.0.0.1:18181/health", allow_loopback_http=True),
            "http://127.0.0.1:18181/health")
        with self.assertRaises(ValueError):
            production_health.validate_endpoint("http://127.0.0.1:18181/health")

    def test_config_is_closed_bounded_and_symlink_free(self):
        self.write_config("https://status.example.test/health", unexpected=True)
        with self.assertRaises(ValueError):
            production_health.ProductionHealthMonitor(self.path, autostart=False)

        self.write_config("https://status.example.test/health")
        os.chmod(self.path, 0o644)
        with self.assertRaises(ValueError):
            production_health.ProductionHealthMonitor(self.path, autostart=False)

        with open(self.path, "wb") as handle:
            handle.write(b" " * (production_health.CONFIG_MAX_BYTES + 1))
        os.chmod(self.path, 0o600)
        with self.assertRaises(ValueError):
            production_health.ProductionHealthMonitor(self.path, autostart=False)

        self.write_config("https://status.example.test/health")
        target = os.path.join(self.temp.name, "target.json")
        os.replace(self.path, target)
        os.symlink(target, self.path)
        with self.assertRaises(ValueError):
            production_health.ProductionHealthMonitor(self.path, autostart=False)

    def test_real_probe_accepts_success_and_refuses_redirects(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_response(302)
                    self.send_header("Location", "/health")
                    self.end_headers()

            def log_message(self, _format, *_args):
                pass

        service = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            port = service.server_address[1]
            self.write_config("http://127.0.0.1:%d/health" % port)
            healthy = production_health.ProductionHealthMonitor(
                self.path, autostart=False, allow_loopback_http=True)
            self.assertTrue(healthy.check_once())
            self.assertEqual(healthy.snapshot()["state"], "healthy")

            self.write_config("http://127.0.0.1:%d/redirect" % port)
            redirected = production_health.ProductionHealthMonitor(
                self.path, autostart=False, allow_loopback_http=True)
            self.assertFalse(redirected.check_once())
        finally:
            service.shutdown()
            service.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
