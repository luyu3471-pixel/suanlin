import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from suanlin.inference import MODEL, run_task
from suanlin.scheduler import choose
from suanlin.server import create_server
from suanlin.store import Store, InputError, LEASE_SECONDS
from suanlin.worker import Worker
from experiments import workload, simulate, PROFILES

TEST_TMP = Path(__file__).resolve().parents[1] / "tmp" / "tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.store = Store(self.temp.name)
        self.credentials = self.store.register({"id": "edge", "name": "Edge"}, "127.0.0.1")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def submit(self, **kwargs):
        return self.store.submit(dict(events=["配电箱出现浓烟"], **kwargs))

    def test_real_inference(self):
        self.assertEqual(MODEL.predict("配电箱浓烟明火")['label'], "烟雾风险")
        result = run_task({"events": ["消防通道有纸箱堵塞"], "compute_loops": 100})
        self.assertEqual(result["predictions"][0]["label"], "通道占用")
        self.assertEqual(len(result["digest"]), 64)

    def test_completion_persists_and_does_not_expose_lease(self):
        task = self.submit()
        claim = self.store.poll(self.credentials)["task"]
        result = self.store.complete(dict(self.credentials, task_id=task["id"], lease=claim["lease"], result=run_task(claim["payload"])))
        self.assertTrue(result["accepted"])
        self.assertNotIn("lease", self.store.task(task["id"]))
        self.assertNotIn("session", self.store.snapshot()["nodes"][0])
        self.assertEqual(self.store.task(task["id"])["status"], "completed")

    def test_expired_lease_rejects_late_result(self):
        task = self.submit()
        claim = self.store.poll(self.credentials)["task"]
        self.store.tasks[task["id"]]["lease_until"] = time.time() - 1
        self.store.recover(time.time())
        new_claim = self.store.poll(self.credentials)["task"]
        result = self.store.complete(dict(self.credentials, task_id=task["id"], lease=claim["lease"], result=run_task(claim["payload"])))
        self.assertFalse(result["accepted"])
        self.assertNotEqual(claim["lease"], new_claim["lease"])

    def test_cancellation_cannot_be_overwritten(self):
        task = self.submit()
        claim = self.store.poll(self.credentials)["task"]
        self.store.cancel(task["id"])
        self.store.complete(dict(self.credentials, task_id=task["id"], lease=claim["lease"], result=run_task(claim["payload"])))
        self.assertEqual(self.store.task(task["id"])["status"], "cancelled")
        self.assertIsNone(self.store.workers['edge']["busy_task"])

    def test_local_only_does_not_use_untrusted_remote(self):
        self.store.configure_worker("edge", {"draining": True})
        remote = self.store.register({"id": "remote"}, "192.168.1.10")
        self.submit()
        self.assertIsNone(self.store.poll(remote)["task"])
        self.store.configure_worker("remote", {"trusted": True})
        self.assertIsNotNone(self.store.poll(remote)["task"])

    def test_max_attempts_and_heartbeat_renewal(self):
        task = self.submit()
        for _ in range(3):
            self.store.poll(self.credentials)
            t = self.store.tasks[task["id"]]
            before = t["lease_until"]
            self.store.heartbeat(dict(self.credentials, rtt_ms=1, active_task=t["id"], lease=t["lease"]))
            self.assertGreaterEqual(t["lease_until"], before)
            t["lease_until"] = 0
            self.store.recover(time.time())
        self.assertEqual(t["status"], "failed")

    def test_validation_and_csv_formula_escape(self):
        for value in ([], [""], [1], "text"):
            with self.assertRaises(InputError):
                self.store.submit({"events": value})
        for value in (True, float('nan'), -1):
            with self.assertRaises(InputError):
                self.submit(deadline_ms=value)
        self.submit(name="=1+1")
        rows = list(csv.DictReader(io.StringIO(self.store.export_csv().lstrip('\ufeff'))))
        self.assertEqual(rows[0]["name"], "'=1+1")

    def test_duplicate_live_id_rejected(self):
        with self.assertRaises(InputError):
            self.store.register({"id": "edge"}, "127.0.0.1")

    def test_idle_heartbeat_does_not_renew_undelivered_result(self):
        task = self.submit()
        self.store.poll(self.credentials)
        t = self.store.tasks[task["id"]]
        t["lease_until"] = time.time() + 1
        before = t["lease_until"]
        self.store.heartbeat(dict(self.credentials, rtt_ms=1))
        self.assertEqual(t["lease_until"], before)

    def test_invalid_result_preserves_running_slot(self):
        task = self.submit()
        claim = self.store.poll(self.credentials)["task"]
        with self.assertRaises(InputError):
            self.store.complete(dict(self.credentials, task_id=task["id"], lease=claim["lease"], result={"predictions": [], "compute_ms": 1}))
        self.assertEqual(self.store.workers['edge']["busy_task"], task['id'])
        self.assertEqual(self.store.tasks[task['id']]['status'], 'running')

    def test_seeded_random_choice_not_changed_by_polls(self):
        self.store.register({"id": "another"}, "127.0.0.1")
        task = self.submit(policy="random")
        self.store.poll(self.credentials)
        desired = self.store.tasks[task["id"]].get("desired_worker")
        for _ in range(5):
            self.store.poll(self.credentials)
            self.assertEqual(self.store.tasks[task["id"]].get("desired_worker"), desired)

    def test_restart_recovers_running_task(self):
        task = self.submit()
        self.store.poll(self.credentials)
        self.store.db.close()
        self.store = Store(self.temp.name)
        self.assertEqual(self.store.task(task["id"])["status"], "queued")
        self.assertFalse(self.store.snapshot()["nodes"][0]["online"])

class IntegrationTests(unittest.TestCase):
    def test_http_workers_execute_with_auth_and_reject_cross_origin(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            server = create_server(port=0, directory=temp)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stop = threading.Event()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            worker = threading.Thread(target=Worker(base, server.store.token, "test", "integration", stop=stop).run, daemon=True)
            worker.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(base + "/api/status")
                self.assertEqual(denied.exception.code, 401)
                request = urllib.request.Request(base + "/api/tasks", json.dumps({"events": ["出口被纸箱堵塞"]}).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + server.store.token})
                with opener.open(request) as response:
                    task = json.load(response)
                limit = time.time() + 10
                while time.time() < limit and server.store.task(task["id"])["status"] != "completed":
                    time.sleep(.05)
                actual = server.store.task(task["id"])
                self.assertEqual(actual["status"], "completed")
                self.assertEqual(actual["result"]["predictions"][0]["label"], "通道占用")
                request = urllib.request.Request(base + "/api/demo", b'{}', {"Content-Type": "application/json", "Authorization": "Bearer " + server.store.token, "Origin": "http://evil.invalid"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(request)
                self.assertEqual(denied.exception.code, 403)
            finally:
                stop.set()
                worker.join(timeout=3)
                server.shutdown()
                server.server_close()
                server.store.db.close()

    def test_simulation_is_reproducible_and_privacy_constrained(self):
        rows = workload(2030, "normal", 40)
        self.assertEqual(rows, workload(2030, "normal", 40))
        for policy in ("smart", "random", "round_robin", "fixed"):
            first, records = simulate(rows, PROFILES["normal"], policy, 1)
            second, again = simulate(rows, PROFILES["normal"], policy, 1)
            self.assertEqual(first, second)
            self.assertEqual(records, again)
            self.assertTrue(all(r["worker_id"] != "c-cloud" for r in records if r["privacy"] == "local_only"))

if __name__ == "__main__":
    unittest.main()
