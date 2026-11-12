"""Create fixed business workload suites; run all policies without cherry-picking."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.performance_suite import Client, wait, metrics
from suanlin.business import KINDS

def dataset():
    profiles={}
    sizes={'smoke':{'log_analysis':10000,'document_search':1000,'archive':10000},
           'standard':{'log_analysis':1000000,'document_search':60000,'archive':400000},
           'heavy':{'log_analysis':2000000,'document_search':100000,'archive':500000}}
    for profile,units in sizes.items():
        rows=[]
        for i,kind in enumerate(['log_analysis','document_search','archive']*2):
            rows.append({'name':f'{profile}-{kind}-{i}','workload':{'kind':kind,'units':units[kind],'seed':2030+i},'deadline_ms':300000,'privacy':'local_only'})
        profiles[profile]=rows
    # Identical mix for every policy: long jobs plus smaller urgent jobs.
    mixed=[]
    for i in range(12):
        kind=KINDS[i%3];small=i%4 in (1,3)
        units=sizes['standard'][kind]//(10 if small else 1)
        mixed.append({'name':f'mixed-{kind}-{i}','workload':{'kind':kind,'units':units,'seed':2100+i},'deadline_ms':30000 if small else 300000,'privacy':'local_only'})
    profiles['mixed_deadlines']=mixed
    profiles['throughput']=[dict(profiles['standard'][i%6],name=f'throughput-{i}',workload=dict(profiles['standard'][i%6]['workload'],seed=2300+i)) for i in range(24)]
    return {'schema':2,'source':'seeded_synthetic_inputs_real_algorithms','input_generation_included_in_timing':True,'network_transfer':'compact seeded specifications, not full log/document files','profiles':profiles}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--generate',action='store_true')
    p.add_argument('--dataset',type=Path,default=Path('benchmarks/business-v1.json'))
    p.add_argument('--url',default='http://127.0.0.1:8765')
    p.add_argument('--token-file',type=Path)
    p.add_argument('--profile',choices=('smoke','standard','heavy','mixed_deadlines','throughput'),default='smoke')
    p.add_argument('--rounds',type=int,choices=range(1,6),default=3)
    p.add_argument('--policies',nargs='+',choices=('fixed_A','fixed_B','smart','ai'),default=['fixed_A','fixed_B','smart','ai'])
    p.add_argument('--a',default='local-1');p.add_argument('--b',default='office-b')
    p.add_argument('--output',type=Path,default=Path('.local/business-tests'))
    args=p.parse_args()
    if args.generate:
        args.dataset.parent.mkdir(parents=True,exist_ok=True)
        args.dataset.write_text(json.dumps(dataset(),ensure_ascii=False,indent=2),encoding='utf-8');print(args.dataset);return
    if not args.token_file:p.error('--token-file required')
    data=json.loads(args.dataset.read_text(encoding='utf-8'))
    if args.profile not in data['profiles']:p.error('Profile absent; use --dataset benchmarks/business-v2.json for throughput')
    payloads=data['profiles'][args.profile]
    client=Client(args.url,args.token_file);before=client.call('/api/status')
    if before.get('version') not in ('2.3.0-business-workloads','2.4.0-slot-aware','2.4.1-slot-aware','2.5.0-qwen-rolling') or not before.get('ai_scheduler',{}).get('enabled'):
        raise ValueError('Start the business-workload coordinator with Qwen scheduling enabled')
    nodes={n['id']:n for n in before['nodes']}
    if before['counts']['queued'] or before['counts']['running']:raise ValueError('Wait for current work to finish')
    for node_id in [args.a,args.b]:
        n=nodes[node_id]
        if not n['online'] or not n['trusted'] or n['draining'] or not set(KINDS)<=set(n.get('workload_kinds',[])):
            raise ValueError('Upgrade and verify worker capabilities: '+node_id)
    if nodes[args.a]['host']==nodes[args.b]['host'] or nodes[args.b]['mode']!='remote_host':raise ValueError('Two physical hosts required')
    out=args.output/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+args.profile);out.mkdir(parents=True)
    def save(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    save('metadata.json',{'coordinator_version':before.get('version'),'policies':args.policies,'dataset':data,'sha256':hashlib.sha256(args.dataset.read_bytes()).hexdigest(),'nodes':[nodes[args.a],nodes[args.b]],'profile':args.profile,'rounds':args.rounds,'warmup':'one smoke task per kind per node','status':'running'})
    accepted=[];trials=[];canonical={};mismatches=[]
    def batch(items,label):
        received=[];errors=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending={pool.submit(client.call,'/api/tasks',body):i for i,body in enumerate(items)}
            for future in as_completed(pending):
                try:
                    task=future.result();task['input_index']=pending[future];received.append(task);accepted.append(task)
                    save('accepted.json',accepted)
                except Exception as exc:errors.append(type(exc).__name__+': '+str(exc))
        if errors:save('submission-errors.json',errors);raise RuntimeError('Submission failed; accepted IDs retained')
        received.sort(key=lambda t:t['input_index'])
        rows=wait(client,received,1800);save(label+'.json',rows);return rows
    try:
        warm=[]
        for node in (args.a,args.b):
            for item in data['profiles']['smoke'][:3]:warm.append(dict(item,policy='fixed',fixed_worker_id=node,name='business-warmup'))
        batch(warm,'warmup')
        policies=list(dict.fromkeys(args.policies))
        for round_no in range(args.rounds):
            for policy in policies[round_no%len(policies):]+policies[:round_no%len(policies)]:
                current=client.call('/api/status');current_nodes={n['id']:n for n in current['nodes']}
                if any(not current_nodes[n]['online'] or current_nodes[n]['capacity']!=nodes[n]['capacity'] for n in (args.a,args.b)):raise RuntimeError('Node topology changed')
                items=[]
                for item in payloads:
                    body=dict(item,policy='fixed' if policy.startswith('fixed') else policy)
                    if policy.startswith('fixed'):body['fixed_worker_id']=args.a if policy=='fixed_A' else args.b
                    items.append(body)
                rows=batch(items,f'{round_no+1}-{policy}')
                for i,t in enumerate(rows):
                    if t['status']!='completed':continue
                    result=t['result'];signature=(result['workload'],result['summary'],result['digest'])
                    if i in canonical and canonical[i]!=signature:mismatches.append({'round':round_no+1,'policy':policy,'index':i})
                    canonical.setdefault(i,signature)
                trial=dict(metrics(rows,[nodes[args.a],nodes[args.b]]),round=round_no+1,policy=policy,ai_sources=dict(Counter(t.get('scheduler_decision',{}).get('source') for t in rows)) if policy=='ai' else {})
                trials.append(trial);save('trials.json',trials);print(json.dumps(trial,ensure_ascii=False),flush=True)
        save('summary.json',{'status':'complete','trials':trials,'consistency_mismatches':mismatches,'notes':['Report every strategy and all failures.','Model overhead is included. No guarantee AI beats rules.','Seeded inputs generated locally; full file transfer is not benchmarked.']})
    except Exception as exc:
        save('incomplete.json',{'error':str(exc),'accepted_count':len(accepted),'trials':trials});raise
    print(out)

if __name__=='__main__':main()
