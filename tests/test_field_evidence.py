import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suanlin.inference import TRAINING
from suanlin.server import create_server
from suanlin.worker import Worker
from tools import field_evidence as evidence

TEMP = ROOT / 'tmp' / 'field-tool-tests'
TEMP.mkdir(parents=True, exist_ok=True)

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=TEMP)
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def dataset(self, rows):
        p = self.root / 'events.csv'
        with p.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, ['id', 'text', 'label', 'source'])
            writer.writeheader(); writer.writerows(rows)
        return p

    def test_blank_and_unknown_labels_are_not_fabricated(self):
        for rows in ([], [{'id': '1', 'text': '示例', 'label': '未知类别', 'source': '自编合成'}]):
            with self.assertRaises(ValueError): evidence.load_rows(self.dataset(rows))

    def test_evaluation_discloses_training_overlap_and_missing_classes(self):
        text = TRAINING['烟雾风险'][0]
        source = self.dataset([{'id': str(i), 'text': text, 'label': '烟雾风险', 'source': '自编合成'} for i in range(2)])
        output = evidence.evaluate(argparse.Namespace(events_csv=source, output_root=self.root))
        report = json.loads((output / 'metrics.json').read_text(encoding='utf-8'))
        self.assertEqual(report['exact_training_overlap_count'], 2)
        self.assertEqual(report['unique_text_count'], 1)
        self.assertEqual(len(report['classes_absent_from_test']), 3)
        self.assertTrue(report['independence_not_automatically_proven'])

    def test_failed_and_unfinished_tasks_remain_in_denominator(self):
        tasks = [{'status': 'completed', 'latency_ms': 10, 'deadline_met': True, 'worker_id': 'a'},
                 {'status': 'failed', 'worker_id': None}, {'status': 'running', 'worker_id': 'b'}]
        metrics = evidence.trial_metrics(tasks)
        self.assertEqual(metrics['deadline_rate'], 1 / 3)
        self.assertEqual(metrics['failed'], 1)
        self.assertEqual(metrics['unfinished'], 1)
        self.assertEqual(metrics['mean_ms_successful'], 10)

    def test_same_host_requires_explicit_test_flag(self):
        class Fake:
            def call(self, path):
                return {'counts': {'queued': 0, 'running': 0}, 'nodes': [dict(id=x, online=True, trusted=True, draining=False, mode='same_host') for x in ('a', 'b')]}
        with self.assertRaises(ValueError): evidence.preflight(Fake(), argparse.Namespace(allow_same_host_test=False))
        _, nodes = evidence.preflight(Fake(), argparse.Namespace(allow_same_host_test=True), ('a',))
        self.assertEqual(len(nodes), 2)

    def test_run_paths_and_csv_export_do_not_overwrite_or_execute_formulas(self):
        first = evidence.new_directory('test', self.root)
        second = evidence.new_directory('test', self.root)
        self.assertNotEqual(first, second)
        evidence.write_csv(first / 'rows.csv', [{'text': '=1+1'}])
        self.assertIn("'=1+1", (first / 'rows.csv').read_text(encoding='utf-8-sig'))

class HTTPFieldTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=TEMP)
        self.root = Path(self.directory.name)
        self.server = create_server(port=0, directory=self.root / 'coordinator')
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=Worker(self.url, self.server.store.token, x, x, stop=self.stop).run, daemon=True) for x in ('local-1', 'office-b')]
        for thread in self.threads: thread.start()
        self.token_file = self.root / 'coordinator' / 'access_token'
        self.wait_node('local-1'); self.wait_node('office-b')

    def wait_node(self, node_id):
        limit = time.monotonic() + 8
        while time.monotonic() < limit:
            if any(n['id'] == node_id and n['online'] for n in self.server.store.snapshot()['nodes']): return
            time.sleep(.05)
        self.fail('worker did not register: ' + node_id)

    def tearDown(self):
        self.stop.set()
        for thread in self.threads: thread.join(3)
        self.server.shutdown(); self.server.server_close(); self.server_thread.join(3)
        self.server.store.db.close(); self.directory.cleanup()

    def test_comparison_records_real_results_and_synthetic_provenance(self):
        args = argparse.Namespace(url=self.url, token_file=self.token_file, fixed_worker='local-1', allow_same_host_test=True,
                                  use_demo=True, events_csv=None, output_root=self.root, rounds=2, tasks=2,
                                  deadline_ms=5000, interval_ms=0, compute_loops=0, note='本机工具测试，不是现场证据')
        output = evidence.run_comparison(args)
        report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertTrue(report['complete'])
        self.assertEqual(report['trial_count'], 8)
        self.assertEqual(report['metadata']['topology_observed'], 'same_host_tool_test')
        self.assertFalse(report['metadata']['physical_devices_verified_by_script'])
        self.assertEqual(report['metadata']['extra_cpu_loops'], 0)
        self.assertEqual(sum(g['submitted'] for g in report['groups']), 16)
        with (output / 'tasks.csv').open(encoding='utf-8-sig') as stream:
            raw = list(csv.DictReader(stream))
        self.assertTrue(all(r['source'] == '自编合成' for r in raw))
        self.assertEqual(len(list(output.glob('round-*-results.json'))), 8)

    def test_normal_completion_is_not_mislabeled_as_fault_recovery(self):
        args = argparse.Namespace(url=self.url, token_file=self.token_file, target='office-b', fallback='local-1',
                                  allow_same_host_test=True, output_root=self.root)
        output, passed = evidence.failure_test(args)
        self.assertFalse(passed)
        report = json.loads((output / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(report['task']['attempts'], 1)
        self.assertTrue(all(not n['draining'] for n in self.server.store.snapshot()['nodes']))

    def test_process_exit_is_recovered_by_another_worker_and_settings_restored(self):
        child = subprocess.Popen([sys.executable, str(ROOT / 'tools/fault_worker.py'), '--coordinator', self.url, '--token-file', str(self.token_file), '--id', 'fault-case'],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.wait_node('fault-case')
            args = argparse.Namespace(url=self.url, token_file=self.token_file, target='fault-case', fallback='local-1',
                                      allow_same_host_test=True, output_root=self.root)
            output, passed = evidence.failure_test(args)
            self.assertTrue(passed)
            self.assertEqual(child.wait(timeout=5), 73)
            report = json.loads((output / 'result.json').read_text(encoding='utf-8'))
            self.assertGreaterEqual(report['task']['attempts'], 2)
            self.assertEqual(report['task']['worker_id'], 'local-1')
            self.assertFalse(report['physical_or_radio_failure_tested'])
            self.assertTrue(all(not n['draining'] for n in self.server.store.snapshot()['nodes']))
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=5)
            child.stdout.close(); child.stderr.close()

if __name__ == '__main__': unittest.main()
