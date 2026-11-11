import tempfile
import threading
import time
import unittest
from suanlin.store import Store
from suanlin.inference import run_task

class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def node(self, name, capacity=1):
        return self.store.register({'id': name, 'capacity': capacity}, '127.0.0.1')

    def submit(self, **extra):
        return self.store.submit(dict(events=['办公室巡检无异常'], **extra))

    def test_capacity_and_independent_completion(self):
        worker = self.node('a', 2)
        for _ in range(3): self.submit()
        first = self.store.poll(worker)['task']
        second = self.store.poll(worker)['task']
        self.assertNotEqual(first['id'], second['id'])
        self.assertIsNone(self.store.poll(worker)['task'])
        self.store.complete(dict(worker, task_id=second['id'], lease=second['lease'], result=run_task(second['payload'])))
        self.assertIn(first['id'], self.store.workers['a']['active_tasks'])
        self.assertIsNotNone(self.store.poll(worker)['task'])

    def test_backlog_assigns_work_to_slower_idle_node(self):
        self.node('fast')
        slow = self.node('slow')
        self.store.workers['fast'].update(text_ms_per_unit=10, rtt_ms=1)
        self.store.workers['slow'].update(text_ms_per_unit=30, rtt_ms=1)
        for _ in range(12): self.submit()
        claim = self.store.poll(slow)['task']
        self.assertIsNotNone(claim, 'The slow node must not starve while all queued jobs prefer the fast node')

    def test_light_single_task_keeps_cheaper_node(self):
        fast = self.node('fast')
        slow = self.node('slow')
        self.store.workers['slow']['rtt_ms'] = 100
        self.submit()
        self.assertIsNone(self.store.poll(slow)['task'])
        self.assertIsNotNone(self.store.poll(fast)['task'])

    def test_multi_lease_heartbeat_cancel_and_recovery(self):
        worker = self.node('a', 2)
        for _ in range(2): self.submit()
        claims = [self.store.poll(worker)['task'] for _ in range(2)]
        for c in claims: self.store.tasks[c['id']]['lease_until'] = 0
        self.store.heartbeat(dict(worker, active_tasks=[{'id':c['id'], 'lease':c['lease']} for c in claims]))
        self.assertTrue(all(self.store.tasks[c['id']]['lease_until'] > time.time() for c in claims))
        self.store.cancel(claims[0]['id'])
        self.store.complete(dict(worker, task_id=claims[0]['id'], lease=claims[0]['lease'], result=run_task(claims[0]['payload'])))
        self.assertIn(claims[1]['id'], self.store.workers['a']['active_tasks'])
        self.store.tasks[claims[1]['id']]['lease_until'] = 0
        with self.store.lock: self.store.recover(time.time())
        retry = self.store.poll(worker)['task']
        self.assertNotEqual(retry['lease'], claims[1]['lease'])

    def test_hash_training_does_not_poison_text_estimate(self):
        worker = self.node('a')
        self.submit(compute_loops=1000000)
        claim = self.store.poll(worker)['task']
        result = run_task({'events':claim['payload']['events'], 'compute_loops':0})
        result.update(compute_ms=1000.2, classification_ms=.2, load_ms=1000)
        self.store.complete(dict(worker, task_id=claim['id'], lease=claim['lease'], result=result))
        self.assertLess(self.store.workers['a']['text_ms_per_unit'], 1)

    def test_long_poll_wakes_on_submission(self):
        worker = self.node('a')
        received = []
        thread = threading.Thread(target=lambda: received.append(self.store.poll(dict(worker, wait_seconds=1))))
        thread.start()
        time.sleep(.05)
        task = self.submit()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received[0]['task']['id'], task['id'])

    def test_invalid_capacity_and_privacy(self):
        for capacity in (True, 0, 33, 1.5):
            with self.assertRaises(ValueError): self.node('bad', capacity)
        worker = self.store.register({'id':'remote', 'capacity':4}, '192.168.5.43')
        for _ in range(20): self.submit()
        self.assertIsNone(self.store.poll(worker)['task'])

    def test_lease_expiry_does_not_resurrect_offline_node(self):
        worker = self.node('dead')
        self.submit()
        claim = self.store.poll(worker)['task']
        self.store.workers['dead']['last_seen'] = 0
        self.store.tasks[claim['id']]['lease_until'] = 0
        with self.store.lock: self.store.recover(time.time())
        self.assertEqual(self.store.workers['dead']['last_seen'], 0)
        self.assertFalse(self.store.snapshot()['nodes'][0]['online'])

if __name__ == '__main__': unittest.main()
