import unittest
from tools.performance_suite import generate, metrics

class PerformanceSuiteTests(unittest.TestCase):
    def test_inputs_reproducible_and_valid_for_server_limits(self):
        dataset = generate(16, 2030)
        self.assertEqual(dataset, generate(16, 2030))
        self.assertNotEqual(dataset, generate(16, 2031))
        for rows in dataset['cases'].values():
            self.assertEqual(len(rows), 16)
            for row in rows:
                self.assertLessEqual(len(row['events']), 128)
                self.assertTrue(all(0 < len(text) <= 2000 for text in row['events']))
                self.assertLessEqual(row['compute_loops'], 2000000)
        self.assertTrue(all(row['compute_loops']==0 for row in dataset['cases']['text_batch']))
        self.assertTrue(all(row['compute_loops']==0 for row in dataset['cases']['tiny_control']))

    def test_metrics_retain_failures_and_measure_parallel_overlap(self):
        tasks = [dict(status='completed', worker_id='a', created_at=0, started_at=1,
                      finished_at=end, latency_ms=end*1000, deadline_met=True,
                      result={'compute_ms':100}) for end in (2,3)]
        tasks.append(dict(status='failed', created_at=0, finished_at=4, deadline_met=False))
        result=metrics(tasks,[{'id':'a','capacity':2}])
        self.assertEqual(result['failed'],1)
        self.assertEqual(result['deadline_rate'],2/3)
        self.assertEqual(result['throughput_tasks_s'],.5)
        self.assertEqual(result['nodes']['a']['peak_inflight'],2)
        self.assertEqual(result['p95_latency_ms'],3000)

if __name__=='__main__': unittest.main()
