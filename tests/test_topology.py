import json
import tempfile
import time
import unittest

from suanlin.store import Store


class TopologySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.worker = self.store.register({'id': 'topology-a'}, '127.0.0.1')

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def submit(self):
        return self.store.submit({'events': ['不应出现在拓扑摘要中的原始文本'],
                                  'policy': 'fixed', 'fixed_worker_id': 'topology-a',
                                  'deadline_ms': 300000})

    def test_running_task_survives_table_limit_without_payload_or_lease(self):
        task = self.submit()
        self.store.poll(self.worker)
        self.store.tasks[task['id']]['progress'] = {
            'done': 10, 'total': 100, 'stage': 'log_analysis', 'at': time.time()}
        for _ in range(205):
            self.submit()
        snapshot = self.store.snapshot()
        self.assertNotIn(task['id'], {t['id'] for t in snapshot['tasks']})
        running = snapshot['topology']['node_tasks']['topology-a']['running']
        self.assertEqual([t['id'] for t in running], [task['id']])
        self.assertEqual(running[0]['progress']['done'], 10)
        self.assertFalse({'payload', 'lease', 'lease_until', 'result'} & running[0].keys())
        self.assertNotIn('不应出现在拓扑摘要中的原始文本', json.dumps(running, ensure_ascii=False))
        running[0]['progress']['done'] = 90
        self.assertEqual(self.store.tasks[task['id']]['progress']['done'], 10)

    def test_recent_results_use_finish_order_and_stay_bounded(self):
        tasks = [self.submit() for _ in range(4)]
        for task, finished in zip(tasks, [400, 100, 300, 200]):
            self.store.tasks[task['id']].update(
                status='completed', worker_id='topology-a', finished_at=finished,
                latency_ms=10, result={'private': 'business result'})
        for _ in range(205):
            self.submit()
        recent = self.store.snapshot()['topology']['node_tasks']['topology-a']['recent']
        self.assertEqual([t['id'] for t in recent], [tasks[i]['id'] for i in [0, 2, 3]])
        self.assertTrue(all('result' not in task for task in recent))

    def test_offline_node_keeps_running_record_until_lease_expires(self):
        task = self.submit()
        self.store.poll(self.worker)
        self.store.workers['topology-a']['last_seen'] = time.time() - 20
        snapshot = self.store.snapshot()
        self.assertFalse(snapshot['nodes'][0]['online'])
        self.assertEqual(snapshot['topology']['node_tasks']['topology-a']['running'][0]['id'], task['id'])
