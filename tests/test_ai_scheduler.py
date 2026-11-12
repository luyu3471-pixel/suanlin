import io
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from suanlin.store import Store
from suanlin.ai_scheduler import AIScheduler, plan
from suanlin.resources import sanitize, memory
from suanlin.scheduler import service_ms, simulate_plan, plan_is_worse, optimize_plan

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.a = self.store.register({'id':'a', 'capacity':2}, '127.0.0.1')
        self.b = self.store.register({'id':'b'}, '192.168.5.40')
        self.store.ai = AIScheduler(self.store)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def submit(self):
        return self.store.submit({'events':['通道正常'], 'policy':'ai'})

    def test_model_call_outside_lock_and_no_raw_event(self):
        t = self.submit()
        observed = []
        def fake(rows, nodes, **kwargs):
            def heartbeat():
                self.store.heartbeat(self.a)
                observed.append(True)
            thread = threading.Thread(target=heartbeat)
            thread.start(); thread.join(timeout=1)
            self.assertTrue(observed)
            self.assertNotIn('通道正常', json.dumps(rows,ensure_ascii=False))
            self.assertEqual([c['id'] for c in rows[0]['candidates']], ['a'])
            return {t['id']:'a'}
        with patch('suanlin.ai_scheduler.plan', side_effect=fake): self.store.ai.cycle()
        claim = self.store.poll(self.a)['task']
        self.assertEqual(claim['id'], t['id'])
        self.assertEqual(self.store.task(t['id'])['scheduler_decision']['source'], 'qwen')

    def test_timeout_uses_rules_without_failing_task(self):
        t = self.submit()
        with patch('suanlin.ai_scheduler.plan', side_effect=TimeoutError): self.store.ai.cycle()
        self.assertEqual(self.store.poll(self.a)['task']['id'], t['id'])
        self.assertEqual(self.store.task(t['id'])['scheduler_decision']['source'], 'rules_fallback')

    def test_revoked_trust_is_rechecked(self):
        self.store.configure_worker('b', {'trusted':True})
        t = self.submit()
        with patch('suanlin.ai_scheduler.plan', return_value={t['id']:'b'}): self.store.ai.cycle()
        self.store.configure_worker('b', {'trusted':False})
        self.assertIsNone(self.store.poll(self.b)['task'])
        with patch('suanlin.ai_scheduler.plan', return_value={t['id']:'a'}): self.store.ai.cycle()
        self.assertEqual(self.store.poll(self.a)['task']['id'], t['id'])
        self.assertEqual(self.store.task(t['id'])['scheduler_decision']['worker_id'], 'a')

    def test_cancelled_task_not_rewritten_by_model(self):
        t = self.submit()
        def cancel(rows,nodes, **kwargs):
            self.store.cancel(t['id'])
            return {t['id']:'a'}
        with patch('suanlin.ai_scheduler.plan',side_effect=cancel): self.store.ai.cycle()
        self.assertEqual(self.store.task(t['id'])['status'],'cancelled')
        self.assertNotIn('scheduler_decision',self.store.task(t['id']))

    def test_wait_budget_falls_back(self):
        t = self.submit()
        self.store.tasks[t['id']]['created_at'] -= 31
        self.assertEqual(self.store.poll(self.a)['task']['id'],t['id'])
        self.assertEqual(self.store.task(t['id'])['scheduler_decision']['reason'],'decision_wait_budget')

    def test_heavy_queue_has_bounded_time_for_multiple_model_batches(self):
        self.store.workers['a']['text_ms_per_unit']=2000
        t=self.store.submit({'events':['x'*32], 'policy':'ai','deadline_ms':300000})
        self.store.tasks[t['id']]['created_at']-=1
        self.assertIsNone(self.store.poll(self.a)['task'])
        self.store.tasks[t['id']]['created_at']-=31
        self.assertEqual(self.store.poll(self.a)['task']['id'],t['id'])
        self.assertEqual(self.store.task(t['id'])['scheduler_decision']['reason'],'decision_wait_budget')

    def test_model_request_batches_stay_within_eight_tasks(self):
        for _ in range(20):self.store.submit({'events':['normal'],'policy':'ai','deadline_ms':30000})
        def fake(rows,nodes, **kwargs):
            self.assertEqual(len(rows),8)
            return {r['task_id']:'a' for r in rows}
        with patch('suanlin.ai_scheduler.plan',side_effect=fake):self.store.ai.cycle()
        self.assertEqual(sum(bool(t.get('scheduler_decision')) for t in self.store.tasks.values()),8)

    def test_resources_are_bounded_and_legacy_optional(self):
        self.assertEqual(self.store.workers['b']['resources'],{})
        self.store.heartbeat(dict(self.a, resources={'cpu_percent':float('nan'),'memory_total_gib':32,'secret':'x','gpus':[{'name':'GPU','memory_total_mib':22000}]}))
        r=self.store.workers['a']['resources']
        self.assertNotIn('secret',r); self.assertNotIn('cpu_percent',r)
        self.assertEqual(r['memory_total_gib'],32)
        self.assertTrue(self.store.workers['a']['resources_at'])

    def test_repeated_sample_does_not_claim_fresh_measurement(self):
        packet=dict(self.a, resources={'cpu_percent':10}, resource_sequence=1)
        self.store.heartbeat(packet)
        self.store.workers['a']['resources_at']=100
        self.store.heartbeat(packet)
        self.assertEqual(self.store.workers['a']['resources_at'],100)
        packet['resource_sequence']=2
        self.store.heartbeat(packet)
        self.assertGreater(self.store.workers['a']['resources_at'],100)

    def test_stale_load_does_not_penalize(self):
        t=self.submit()
        n=dict(self.store.workers['a'],resources={'cpu_percent':100},resources_at=time.time())
        pressured=service_ms(t,n)
        n['resources_at']=time.time()-30
        self.assertLess(service_ms(t,n),pressured)

    def test_simulation_cannot_masquerade_as_ai(self):
        import experiments
        from suanlin.scheduler import choose
        self.assertNotIn('ai',experiments.POLICIES)
        with self.assertRaises(ValueError): choose({},[],time.time(),'ai')

    def test_invalid_model_assignments_rejected(self):
        rows=[{'task_id':'t','candidates':[{'id':'a'}]}]
        for assignments in [['unknown'],[],['a','a']]:
            opener=Mock()
            opener.open.return_value=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'workers':assignments})}}]}).encode())
            with patch('urllib.request.build_opener',return_value=opener), self.assertRaises(ValueError): plan(rows,[])

    def test_feasible_model_plan_is_preserved_without_blanket_replacement(self):
        self.store.configure_worker('b', {'trusted': True})
        self.store.workers['a']['text_ms_per_unit'] = 1000
        self.store.workers['b']['text_ms_per_unit'] = 700
        tasks = [self.store.submit({'events':['x'*32], 'policy':'ai', 'deadline_ms':300000}) for _ in range(6)]
        def overload(rows, nodes, **kwargs):
            # No hypothetical previous assignment is baked into candidate times.
            first = {c['id']:c['service_ms'] for c in rows[0]['candidates']}
            for row in rows:
                self.assertEqual(first, {c['id']:c['service_ms'] for c in row['candidates']})
                self.assertEqual({c['id']:c['finish_ms'] for c in rows[0]['candidates']}, {c['id']:c['finish_ms'] for c in row['candidates']})
            self.assertEqual(len(next(n for n in nodes if n['id']=='a')['slots_ready_ms']),2)
            return {t['id']:'b' for t in tasks}
        with patch('suanlin.ai_scheduler.plan', side_effect=overload): self.store.ai.cycle()
        decisions=[self.store.tasks[t['id']]['scheduler_decision'] for t in tasks]
        self.assertTrue(all(d['source']=='qwen' and d['model_worker_id']=='b' for d in decisions))
        self.assertEqual({d['worker_id'] for d in decisions},{'b'})
        self.assertEqual(self.store.ai.status['accepted'],6)
        self.assertEqual(self.store.ai.status['guarded'],0)
        self.assertIsNotNone(self.store.poll(self.b)['task'])

    def test_dispatch_rebalances_busy_node_to_idle_peer(self):
        self.store.configure_worker('b', {'trusted': True})
        self.store.workers['a']['active_tasks']={'busy1':time.time()+60,'busy2':time.time()+60}
        task=self.submit()
        self.store.tasks[task['id']]['scheduler_decision']={'source':'qwen','worker_id':'a','model_worker_id':'a','model_ms':10}
        claimed=self.store.poll(self.b)['task']
        self.assertEqual(claimed['id'],task['id'])
        decision=self.store.task(task['id'])['scheduler_decision']
        self.assertEqual(decision['source'],'qwen_guarded')
        self.assertEqual(decision['reason'],'busy_node_rebalanced')
        self.assertEqual(decision['selected_worker'],'b')

    def test_plan_replay_does_not_mutate_slots_or_ignore_capacity(self):
        self.store.configure_worker('b', {'trusted':True})
        self.store.workers['a']['text_ms_per_unit']=1000
        self.store.workers['b']['text_ms_per_unit']=1000
        tasks=[self.submit() for _ in range(6)]
        now=time.time();nodes=self.store.candidates(now)
        slots=[list(n['_slots']) for n in nodes]
        _, balanced=simulate_plan(tasks,nodes,now)
        _, overloaded=simulate_plan(tasks,nodes,now,{t['id']:'b' for t in tasks})
        self.assertTrue(plan_is_worse(overloaded,balanced))
        self.assertEqual(slots,[n['_slots'] for n in nodes])
        _, invalid=simulate_plan(tasks,nodes,now,{t['id']:'unknown' for t in tasks})
        self.assertEqual(invalid['unscheduled'],6)

    def test_rejoin_preserves_calibration_only_for_same_capacity(self):
        self.store.workers['a'].update(last_seen=0,business_ms_per_unit={'archive':.02})
        self.store.register({'id':'a','capacity':2},'127.0.0.1')
        self.assertEqual(self.store.workers['a']['business_ms_per_unit']['archive'],.02)
        self.store.workers['a']['last_seen']=0
        self.store.register({'id':'a','capacity':4},'127.0.0.1')
        self.assertNotIn('business_ms_per_unit',self.store.workers['a'])

    def test_overdue_active_slot_does_not_appear_almost_idle(self):
        task=self.submit()
        self.store.tasks[task['id']]['predicted_ms']=20000
        self.store.workers['a']['active_tasks']={task['id']:time.time()-10}
        now=time.time();a=next(n for n in self.store.candidates(now) if n['id']=='a')
        self.assertGreaterEqual(max(a['_slots'])-now,3)

    def test_whole_batch_avoids_blocking_long_job_with_short_job(self):
        self.store.workers['a']['capacity']=1
        self.store.configure_worker('b', {'trusted':True})
        for n in self.store.workers.values():n['workload_kinds']=['document_search','log_analysis']
        self.store.workers['a']['business_ms_per_unit']={'document_search':.1,'log_analysis':.1}
        self.store.workers['b']['business_ms_per_unit']={'document_search':.2,'log_analysis':1}
        tasks=[self.store.submit({'policy':'ai','workload':{'kind':k,'units':u,'seed':1},'deadline_ms':300000}) for k,u in [('document_search',10000),('log_analysis',100000)]]
        now=time.time();nodes=self.store.candidates(now)
        _,greedy=simulate_plan(tasks,nodes,now)
        assignments,optimized=optimize_plan(tasks,nodes,now)
        self.assertEqual(assignments[tasks[0]['id']],'b')
        self.assertEqual(assignments[tasks[1]['id']],'a')
        self.assertLess(optimized['makespan_ms'],greedy['makespan_ms'])

if __name__=='__main__': unittest.main()
