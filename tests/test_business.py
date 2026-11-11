import tempfile
import unittest
from suanlin.business import run,validate,KINDS
from suanlin.inference import run_task
from suanlin.store import Store

class BusinessTests(unittest.TestCase):
    def test_log_counts_and_quantiles(self):
        spec={'kind':'log_analysis','units':1000,'seed':1}
        a=run(spec);b=run(spec)
        self.assertEqual(a['digest'],b['digest'])
        self.assertEqual(sum(a['summary']['status_counts'].values()),1000)
        self.assertGreater(a['summary']['p95_latency_ms'],0)
        self.assertEqual(a['load_loops'],0)

    def test_retrieval_order_and_repeatability(self):
        spec={'kind':'document_search','units':100,'seed':7}
        a=run(spec);b=run(spec)
        self.assertEqual(a['digest'],b['digest'])
        for ranking in a['summary']['rankings']:
            self.assertEqual(len(ranking['top10']),10)
            self.assertEqual(ranking['top10'], sorted(ranking['top10'],key=lambda p:(-p[0],p[1])))

    def test_archive_does_real_compression(self):
        a=run({'kind':'archive','units':1000,'seed':1})
        self.assertLess(a['summary']['compressed_bytes'],a['summary']['uncompressed_bytes'])
        self.assertFalse(a['summary']['artifact_retained'])

    def test_invalid_unbounded_workloads_rejected(self):
        for spec in [{'kind':'shell','units':1},{'kind':'log_analysis','units':True},{'kind':'archive','units':999999999},{'kind':'archive','units':1000,'path':'private'}]:
            with self.assertRaises(ValueError):validate(spec)

    def test_capability_memory_and_result_validation(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d)
            try:
                old=s.register({'id':'old'},'127.0.0.1')
                new=s.register({'id':'new','workload_kinds':list(KINDS),'resources':{'memory_available_gib':10}},'127.0.0.1')
                t=s.submit({'workload':{'kind':'archive','units':1000},'policy':'smart'})
                self.assertIsNone(s.poll(old)['task'])
                claim=s.poll(new)['task'];self.assertEqual(claim['id'],t['id'])
                result=run_task(claim['payload'])
                self.assertEqual(result['model'],'business-algorithms-v1')
                s.complete(dict(new,task_id=claim['id'],lease=claim['lease'],result=result))
                self.assertEqual(s.task(t['id'])['status'],'completed')
                self.assertIn('archive',s.workers['new']['business_ms_per_unit'])
                s.heartbeat(dict(new,resources={'memory_available_gib':.1},resource_sequence=1))
                s.submit({'workload':{'kind':'document_search','units':100},'policy':'fixed','fixed_worker_id':'new'})
                self.assertIsNone(s.poll(new)['task'])
            finally:s.db.close()

if __name__=='__main__':unittest.main()
