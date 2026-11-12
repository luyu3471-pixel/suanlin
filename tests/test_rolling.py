import io,json,tempfile,time,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch,Mock
from suanlin.store import Store,InputError
from suanlin.ai_scheduler import AIScheduler,plan
from suanlin.planning import replay
from suanlin.execution import execute
from concurrent.futures import ProcessPoolExecutor

class RollingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=Store(self.temp.name)
  self.a=self.s.register({'id':'a','capacity':1},'127.0.0.1');self.s.ai=AIScheduler(self.s)
 def tearDown(self):self.s.db.close();self.temp.cleanup()
 def task(self,**kw):return self.s.submit(dict(events=['x'*32],policy='ai',deadline_ms=30000,**kw))
 def cycle(self,fn=None):
  with patch('suanlin.ai_scheduler.plan',side_effect=fn or (lambda rows,nodes,**kw:{r['task_id']:{'worker_id':'a','order':i} for i,r in enumerate(rows)})):self.s.ai.cycle()
 def test_blocked_prefix_does_not_hide_executable_task(self):
  for _ in range(8):self.s.submit({'policy':'ai','workload':{'kind':'archive','units':1000},'deadline_ms':10000})
  t=self.task();self.cycle();self.assertEqual(self.s.task(t['id'])['scheduler_decision']['source'],'qwen')
 def test_execution_order_matches_model_and_replay(self):
  t1,t2=self.task(),self.task()
  self.cycle(lambda rows,nodes,**kw:{t1['id']:{'worker_id':'a','order':1},t2['id']:{'worker_id':'a','order':0}})
  self.assertEqual(self.s.poll(self.a)['task']['id'],t2['id'])
 def test_expired_plan_replans_but_frozen_does_not(self):
  t=self.task();self.cycle();task=self.s.tasks[t['id']];d=task['scheduler_decision']
  d.update(at=time.time()-20,valid_until=0,freeze_until=time.time()+10)
  self.assertEqual(self.s.ai.select_tasks(time.time()),[])
  d['freeze_until']=0;self.cycle();self.assertEqual(self.s.tasks[t['id']]['replan_count'],1)
 def test_rejoin_during_model_call_rejects_old_plan(self):
  t=self.task()
  def model(rows,nodes,**kw):
   self.s.workers['a']['last_seen']=0;self.s.register({'id':'a'},'127.0.0.1');return {t['id']:'a'}
  self.cycle(model);self.assertNotIn('scheduler_decision',self.s.tasks[t['id']])
 def test_new_urgent_arrival_invalidates_snapshot(self):
  t=self.task()
  def model(rows,nodes,**kw):
   self.s.submit({'events':['urgent'],'policy':'ai','deadline_ms':1000});return {t['id']:'a'}
  with patch('time.time',return_value=time.time()):self.cycle(model)
  self.assertNotIn('scheduler_decision',self.s.tasks[t['id']])
 def test_new_urgent_arrival_invalidates_snapshot_after_clock_rollback(self):
  t=self.task();now=time.time()
  def model(rows,nodes,**kw):
   with patch('time.time',return_value=now-.1):
    self.s.submit({'events':['urgent'],'policy':'ai','deadline_ms':1000})
   return {t['id']:'a'}
  with patch('time.time',return_value=now):self.cycle(model)
  self.assertNotIn('scheduler_decision',self.s.tasks[t['id']])
 def test_atomic_commit_rolls_back_on_database_error(self):
  a,b=self.task(),self.task()
  self.s.db.execute("CREATE TRIGGER reject_plan BEFORE INSERT ON tasks WHEN NEW.id='"+b['id']+"' BEGIN SELECT RAISE(ABORT,'test'); END;")
  with self.assertRaises(sqlite3.IntegrityError):self.cycle()
  self.assertNotIn('scheduler_decision',self.s.tasks[a['id']])
  row=self.s.db.execute('SELECT body FROM tasks WHERE id=?',(a['id'],)).fetchone()
  self.assertNotIn('scheduler_decision',json.loads(row[0]))
 def test_node_credentials_not_exposed(self):
  t=self.task();self.cycle();self.assertNotIn(self.a['session'],json.dumps(self.s.task(t['id'])))
 def test_cooperative_cancel_and_progress(self):
  path=Path(self.temp.name);cancel=path/'cancel';cancel.touch()
  with self.assertRaisesRegex(RuntimeError,'task_cancelled'):execute({'workload':{'kind':'archive','units':1000}},str(path/'progress'),str(cancel))
  cancel.unlink();execute({'workload':{'kind':'archive','units':1000}},str(path/'progress'),str(cancel))
  self.assertEqual(json.loads((path/'progress').read_text())['stage'],'complete')
 def test_process_worker_stops_running_job_on_cancel(self):
  path=Path(self.temp.name);progress=path/'process-progress';cancel=path/'process-cancel'
  with ProcessPoolExecutor(max_workers=1) as pool:
   future=pool.submit(execute,{'workload':{'kind':'archive','units':500000}},str(progress),str(cancel))
   until=time.monotonic()+15
   while not progress.exists() and not future.done() and time.monotonic()<until:time.sleep(.02)
   self.assertTrue(progress.exists())
   cancel.touch()
   with self.assertRaisesRegex(RuntimeError,'task_cancelled'):future.result(timeout=10)
 def test_heartbeat_cancellation_requires_matching_worker_lease(self):
  t=self.task();self.cycle();claim=self.s.poll(self.a)['task'];self.s.cancel(t['id'])
  r=self.s.heartbeat(dict(self.a,active_tasks=[{'id':t['id'],'lease':claim['lease']}]))
  self.assertEqual(r['cancel_tasks'][0]['lease'],claim['lease'])
 def test_schema_rejects_duplicate_refs_and_wrong_snapshot(self):
  rows=[{'task_id':'x','candidates':[{'id':'a'}]},{'task_id':'y','candidates':[{'id':'b'}]}]
  cases=[{'snapshot_id':'wrong','assignments':[]},{'snapshot_id':'s','assignments':[{'task_ref':'t0','worker_id':'a','order':0},{'task_ref':'t0','worker_id':'a','order':1}]}]
  for data in cases:
   opener=Mock();opener.open.return_value=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}]}).encode())
   with patch('urllib.request.build_opener',return_value=opener),self.assertRaises(ValueError):plan(rows,[],snapshot_id='s')
 def test_schema_valid_response_preserves_explicit_order(self):
  rows=[{'task_id':'private-id','candidates':[{'id':'a'}]}]
  data={'snapshot_id':'s','assignments':[{'task_ref':'t0','worker_id':'a','order':0}]}
  opener=Mock();opener.open.return_value=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}]}).encode())
  with patch('urllib.request.build_opener',return_value=opener):actual=plan(rows,[],snapshot_id='s')
  self.assertEqual(actual,{'private-id':{'worker_id':'a','order':0}})
  request=opener.open.call_args.args[0]
  self.assertNotIn('private-id',request.data.decode())

if __name__=='__main__':unittest.main()
