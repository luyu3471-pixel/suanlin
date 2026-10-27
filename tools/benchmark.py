"""Execute actual requests. Output is machine-local evidence, not 6G data."""
import argparse
import csv
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suanlin.inference import DEMO_EVENTS

def run(url, token_file, rounds=5, count=16):
    token = Path(token_file).read_text(encoding="utf-8").strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path, data=None):
        req = urllib.request.Request(url.rstrip('/') + path, json.dumps(data, ensure_ascii=False).encode() if data is not None else None,
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + token})
        with opener.open(req, timeout=15) as response:
            return json.load(response)
    state = api('/api/status')
    if state['counts']['queued'] or state['counts']['running']:
        raise RuntimeError('请等待现有任务结束后再运行基准')
    if not any(n['online'] and n['trusted'] and not n['draining'] for n in state['nodes']):
        raise RuntimeError('没有可接收任务的可信节点')
    records = []
    trials = []
    policies = ['fixed', 'round_robin', 'random', 'smart']
    # Calibration tasks precede all policies. Policy ordering rotates by round.
    warmup = [api('/api/tasks', {'name':'基准预热', 'events':[DEMO_EVENTS[0]], 'compute_loops':150000, 'deadline_ms':10000, 'policy':'round_robin'}) for _ in range(8)]
    def wait(ids):
        limit = time.time() + 60
        while time.time() < limit:
            tasks = [api('/api/tasks/' + x['id']) for x in ids]
            if all(t['status'] in ('completed','failed','cancelled') for t in tasks):
                return tasks
            time.sleep(.08)
        raise RuntimeError('实际测试超过60秒，保留现有数据库以便排查')
    wait(warmup)
    started = time.time()
    for seed in range(rounds):
        rng = random.Random(3000 + seed)
        inputs = [(DEMO_EVENTS[i % len(DEMO_EVENTS)], rng.choice([100000,200000,300000])) for i in range(count)]
        order = policies[seed % 4:] + policies[:seed % 4]
        for policy in order:
            ids = [api('/api/tasks', {'name':f'本机基准 {seed+1}-{i+1}', 'events':[event], 'compute_loops':loops, 'deadline_ms':5000, 'policy':policy}) for i,(event,loops) in enumerate(inputs)]
            actual = wait(ids)
            for t in actual:
                records.append({'seed':3000+seed, 'policy':policy, 'id':t['id'], 'status':t['status'], 'worker_id':t['worker_id'], 'deadline_met':t.get('deadline_met',False),
                                'latency_ms':t.get('latency_ms'), 'compute_ms':t.get('result',{}).get('compute_ms'), 'load_ms':t.get('result',{}).get('load_ms'),
                                'load_loops':t['payload']['compute_loops'], 'deadline_ms':t['deadline_ms'], 'payload_bytes':t['payload_bytes'], 'attempts':t['attempts']})
            values = sorted(t['latency_ms'] for t in actual if t['status']=='completed')
            trials.append({'seed':3000+seed,'policy':policy,'count':len(actual),'completed':len(values),'deadline_rate':sum(t.get('deadline_met',False) for t in actual)/len(actual),
                           'mean_ms':statistics.mean(values) if values else None,'p95_ms':values[math.ceil(len(values)*.95)-1] if values else None})
    out=ROOT/'results';out.mkdir(exist_ok=True)
    with (out/'local_benchmark_raw.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,records[0].keys());writer.writeheader();writer.writerows(records)
    summary={'evidence_type':'actual_application_requests', 'topology':'same_host_processes' if all(n['mode']=='same_host' for n in state['nodes']) else 'mixed_connections_needs_physical_confirmation',
             'platform':platform.platform(),'python':platform.python_version(),'started_at':started,'ended_at':time.time(),'rounds':rounds,'tasks_per_trial':count,'total':len(records),
             'nodes':[{k:n[k] for k in ('id','mode','bandwidth_mbps')} for n in state['nodes']], 'extra_load':'100k/200k/300k SHA256 loops, reported separately from classifier',
             'trials':trials,'groups':[],'limitations':['同机工作进程，不是多物理终端实验','无链路损伤注入，不是6G网络测试','人为CPU负载与文本推理混合，单独记录负载时间','均值和P95仅针对成功任务，失败数另行保留','轮询频率与本机后台负载影响实际结果']}
    for policy in policies:
        group=[t for t in trials if t['policy']==policy]
        summary['groups'].append({'policy':policy,'deadline_rate':statistics.mean(t['deadline_rate'] for t in group),'mean_ms':statistics.mean(t['mean_ms'] for t in group if t['mean_ms'] is not None),
                                  'p95_ms':statistics.mean(t['p95_ms'] for t in group if t['p95_ms'] is not None),'completed':sum(t['completed'] for t in group)})
    (out/'local_benchmark_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Actual benchmark complete: {len(records)} tasks. topology={summary["topology"]}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--token-file',required=True);p.add_argument('--rounds',type=int,default=5);p.add_argument('--tasks',type=int,default=16)
    a=p.parse_args()
    if not 2<=a.rounds<=30 or not 4<=a.tasks<=200:p.error('rounds 2..30, tasks 4..200')
    run(a.url,a.token_file,a.rounds,a.tasks)
