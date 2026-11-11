"""Reproducible synthetic workload suite; concurrent submissions, all results retained."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suanlin.inference import DEMO_EVENTS

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def generate(count=64, seed=2030):
    rng = random.Random(seed)
    cases = {}
    for case in ('tiny_control', 'text_batch', 'cpu_heavy', 'mixed'):
        rows = []
        for i in range(count):
            loops, batch, repeat = 0, 1, 1
            if case == 'text_batch': batch, repeat = 128, 16
            if case == 'cpu_heavy': loops = 1000000
            if case == 'mixed':
                loops, batch, repeat = rng.choice([(0,1,1), (0,128,16), (200000,8,1), (1000000,1,1)])
            events = [(f'合成记录{i:03d}-{j:03d}：' + DEMO_EVENTS[(i+j) % len(DEMO_EVENTS)] * repeat) for j in range(batch)]
            rows.append({'name': f'{case}-{i:03d}', 'events': events, 'compute_loops': loops,
                         'deadline_ms': 5000 if loops == 0 else 30000, 'privacy':'local_only'})
        rng.shuffle(rows)
        cases[case] = rows
    return {'schema':1, 'seed':seed, 'tasks_per_case':count, 'source':'authored_synthetic',
            'not_model_accuracy_dataset':True, 'cases':cases}

class Client:
    def __init__(self, url, token_file):
        self.url = url.rstrip('/')
        self.token = Path(token_file).read_text(encoding='utf-8-sig').strip()
    def call(self, path, data=None):
        # An opener per request avoids shared mutable transport state across submit threads.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode()
        request = urllib.request.Request(self.url + path, body, {'Content-Type':'application/json', 'Authorization':'Bearer '+self.token})
        with opener.open(request, timeout=30) as response: return json.load(response)

def wait(client, tasks, timeout):
    end = time.monotonic() + timeout
    while True:
        # Read in parallel; benchmark timing uses server timestamps, not this polling duration.
        with ThreadPoolExecutor(max_workers=8) as pool:
            actual = list(pool.map(lambda t:client.call('/api/tasks/'+t['id']), tasks))
        if all(t['status'] in ('completed','failed','cancelled') for t in actual): return actual
        if time.monotonic() > end: raise TimeoutError('Task wait expired; accepted task IDs are preserved')
        time.sleep(.2)

def metrics(tasks, nodes):
    done = [t for t in tasks if t['status']=='completed']
    values = sorted(t['latency_ms'] for t in done)
    span = max(t['finished_at'] for t in tasks) - min(t['created_at'] for t in tasks)
    by_node = {}
    for node in nodes:
        ts = [t for t in done if t['worker_id']==node['id']]
        marks = sorted([(t['started_at'],1) for t in ts]+[(t['finished_at'],-1) for t in ts])
        active = peak = 0
        for _, change in marks: active += change; peak = max(peak, active)
        by_node[node['id']] = {'completed':len(ts), 'capacity':node.get('capacity',1), 'peak_inflight':peak,
            'mean_compute_ms':statistics.mean(t['result']['compute_ms'] for t in ts) if ts else None,
            'mean_queue_ms':statistics.mean((t['started_at']-t['created_at'])*1000 for t in ts) if ts else None}
    return {'submitted':len(tasks), 'completed':len(done), 'failed':sum(t['status']=='failed' for t in tasks),
            'cancelled':sum(t['status']=='cancelled' for t in tasks), 'deadline_rate':sum(t.get('deadline_met',False) for t in tasks)/len(tasks),
            'server_makespan_s':span, 'throughput_tasks_s':len(done)/max(span,.000001),
            'mean_latency_ms':statistics.mean(values) if values else None,
            'p95_latency_ms':values[math.ceil(.95*len(values))-1] if values else None,
            'nodes':by_node}

def run(args):
    dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
    selected = args.cases or list(dataset['cases'])
    if any(case not in dataset['cases'] for case in selected): raise ValueError('Unknown workload case')
    client = Client(args.url, args.token_file)
    initial = client.call('/api/status')
    if initial['counts']['queued'] or initial['counts']['running']: raise ValueError('Wait for existing work to finish')
    nodes = [n for n in initial['nodes'] if n['online'] and n['trusted'] and not n['draining']]
    if not nodes or args.fixed_worker not in {n['id'] for n in nodes}: raise ValueError('Fixed baseline node is not ready')
    if not args.allow_same_host_test and (len(nodes)<2 or not any(n['mode']=='remote_host' for n in nodes)):
        raise ValueError('LAN suite requires two ready nodes including a remote node')
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    output = args.output_root / (stamp+'-suite-'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    write(output/'dataset.json', dataset)
    write(output/'metadata.json', {'source':'synthetic_actual_execution', 'url':args.url,
        'dataset_sha256':hashlib.sha256(args.dataset.read_bytes()).hexdigest(), 'rounds':args.rounds,
        'submit_concurrency':args.submit_concurrency, 'cases':selected, 'policies':args.policies,
        'same_host_test':args.allow_same_host_test, 'nodes':[{k:n.get(k) for k in ('id','capacity','mode')} for n in nodes],
        'notes':'Compare fixed at full A capacity against all nodes; no CPU utilization or real-business claim.'})
    print('OUTPUT='+str(output), flush=True)
    trials = []
    accepted = []
    def submit_batch(payloads, filename):
        submitted, errors = [], []
        with ThreadPoolExecutor(max_workers=args.submit_concurrency) as pool:
            futures = [pool.submit(client.call, '/api/tasks', body) for body in payloads]
            for future in as_completed(futures):
                try:
                    task = future.result(); submitted.append(task); accepted.append(task)
                    write(output/filename, submitted)
                except Exception as exc: errors.append(str(exc))
        if errors: raise RuntimeError('Submission errors: '+ '; '.join(errors))
        return submitted
    try:
        # Fixed to every node: prestart every CPU slot and learn both feature costs.
        warmup = []
        for node in nodes:
            for _ in range(2*node.get('capacity',1)):
                warmup.append({'name':'性能测试预热', 'events':DEMO_EVENTS*16, 'compute_loops':200000,
                    'policy':'fixed','fixed_worker_id':node['id'],'privacy':'local_only','deadline_ms':300000})
        warm = wait(client, submit_batch(warmup, 'warmup-accepted.json'), args.timeout)
        write(output/'warmup-results.json', warm)
        if any(t['status']!='completed' for t in warm): raise RuntimeError('Warmup failed')
        for case in selected:
            for rnd in range(args.rounds):
                order = args.policies[rnd % len(args.policies):]+args.policies[:rnd % len(args.policies)]
                for policy in order:
                    tag = f'{case}-r{rnd+1}-{policy}'
                    bodies = [dict(row, name=tag+'-'+str(i), policy=policy,
                        fixed_worker_id=args.fixed_worker if policy=='fixed' else None) for i,row in enumerate(dataset['cases'][case])]
                    before = time.monotonic()
                    submitted = submit_batch(bodies, tag+'-accepted.json')
                    submit_elapsed = time.monotonic()-before
                    actual = wait(client, submitted, args.timeout)
                    write(output/(tag+'-results.json'), actual)
                    result = dict(case=case, round=rnd+1, policy=policy, submit_elapsed_s=submit_elapsed,
                                  client_wall_s=time.monotonic()-before, **metrics(actual,nodes))
                    trials.append(result); write(output/'trials.json', trials)
                    print(f"{tag}: {result['completed']}/{result['submitted']}, {result['throughput_tasks_s']:.2f} tasks/s, P95={result['p95_latency_ms']}", flush=True)
        write(output/'summary.json', {'complete':True,'trials':trials})
    except BaseException as exc:
        write(output/'incomplete.json', {'complete':False,'error':str(exc),'accepted_task_ids':[t['id'] for t in accepted]})
        raise
    return output

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    gen=sub.add_parser('generate'); gen.add_argument('--tasks',type=int,default=64); gen.add_argument('--seed',type=int,default=2030)
    gen.add_argument('--output',type=Path,default=ROOT/'benchmarks'/'performance-v2.json')
    runp=sub.add_parser('run')
    runp.add_argument('--dataset',type=Path,default=ROOT/'benchmarks'/'performance-v2.json')
    runp.add_argument('--url',required=True); runp.add_argument('--token-file',required=True)
    runp.add_argument('--rounds',type=int,default=3); runp.add_argument('--submit-concurrency',type=int,default=8)
    runp.add_argument('--fixed-worker',default='local-1'); runp.add_argument('--policies',nargs='+',choices=['fixed','round_robin','random','smart'],default=['fixed','round_robin','smart'])
    runp.add_argument('--cases',nargs='+'); runp.add_argument('--timeout',type=int,default=600)
    runp.add_argument('--output-root',type=Path,default=ROOT/'.local'/'performance-suite')
    runp.add_argument('--allow-same-host-test',action='store_true')
    args=parser.parse_args()
    if args.command=='generate':
        if not 1<=args.tasks<=256: parser.error('tasks must be 1..256')
        args.output.parent.mkdir(parents=True,exist_ok=True); write(args.output,generate(args.tasks,args.seed)); print(args.output)
    else:
        if not 1<=args.rounds<=10 or not 1<=args.submit_concurrency<=32: parser.error('rounds 1..10, submit-concurrency 1..32')
        run(args)

if __name__=='__main__': main()
