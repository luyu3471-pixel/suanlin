import csv
import io
import tempfile
import unittest
from zipfile import ZipFile

from suanlin.business import run
from suanlin.store import Store


class PracticalTaskTests(unittest.TestCase):
    def test_log_analysis_uses_actual_rows_and_reports_locations(self):
        file = {'name':'gateway.csv','text':
                'service,status,latency_ms,message\n'
                'orders,200,60,OK\n'
                'payment,504,900,上游超时\n'
                'payment,502,400,连接失败\n'}
        report = run({'kind':'log_analysis','input':{'files':[file]}})['summary']
        self.assertEqual(report['records'],3)
        self.assertEqual(report['abnormal'],2)
        self.assertEqual(report['p95_latency_ms'],900)
        self.assertEqual(report['affected'],[{'name':'payment','count':2}])
        self.assertEqual([row['line'] for row in report['examples']],[3,4])

    def test_search_returns_evidence_from_uploaded_document(self):
        report = run({'kind':'document_search','input':{'query':'报销 发票','files':[
            {'name':'报销制度.md','text':'差旅规定\n请提交电子发票和行程单。\n'},
            {'name':'门禁.txt','text':'访客请登记。'},
        ]}})['summary']
        hits = report['matches']
        self.assertEqual([(h['file'],h['line']) for h in hits],[('报销制度.md',2)])
        self.assertIn('电子发票和行程单',hits[0]['excerpt'])

    def test_archive_keeps_table_structure_and_masks_sensitive_values(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            try:
                old = store.register({'id':'old','workload_kinds':['archive']},'127.0.0.1')
                new = store.register({'id':'new','workload_kinds':['archive'],'file_workloads':True},'127.0.0.1')
                store.configure_worker('new',{'draining':True})
                workload = {'kind':'archive','input':{'files':[{'name':'customers.csv','text':
                    '姓名,Email,备注\n内部样本,sample@example.com,已回访\n'}],
                    'fields':['姓名','客户编号']}}
                with self.assertRaisesRegex(ValueError,'可信'):
                    store.submit({'workload':workload,'privacy':'any'})
                task = store.submit({'workload':workload})
                task_id = task['id']
                self.assertNotIn('sample@example.com',str(task))
                self.assertEqual(store.task(task_id)['dispatch']['reason'],'unsupported')
                self.assertIsNone(store.poll(old)['task'])
                store.configure_worker('new',{'draining':False})
                self.assertEqual(store.task(task_id)['dispatch']['free'],1)
                claim = store.poll(new)['task']
                self.assertEqual(claim['id'],task_id)
                result = run(claim['payload']['workload'])
                self.assertNotIn('sample@example.com',str(result))
                self.assertTrue(store.complete(dict(new,task_id=task_id,lease=claim['lease'],result=result))['accepted'])
                detail = store.task(task_id)
                self.assertNotIn('artifact_base64',detail['result'])
                self.assertEqual(detail['result']['summary']['unmatched_fields'],['客户编号'])
                self.assertTrue(detail['result']['download_available'])
                with ZipFile(io.BytesIO(store.task_archive(task_id))) as archive:
                    text = archive.read('redacted.csv').decode('utf-8-sig')
                    self.assertEqual(list(csv.reader(io.StringIO(text))),[
                        ['姓名','Email','备注'],['[已脱敏]','[已脱敏]','已回访']])
            finally:
                store.db.close()
