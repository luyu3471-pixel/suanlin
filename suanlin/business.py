"""Deterministic synthetic inputs, real business algorithms, no artificial hash loops."""
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
import random
import re
import time
import zlib

KINDS = ('log_analysis', 'document_search', 'archive')
LIMITS = {'log_analysis': (1000, 2000000), 'document_search': (100, 100000), 'archive': (1000, 500000)}
SEED_TEXTS = (
    '机房设备巡检发现电源温度偏高请检查散热风扇和线路',
    '消防通道巡查确认纸箱货物已经移走出口畅通',
    '办公室门禁授权记录需要核实陌生人员夜间访问',
    '网络链路延迟增加请求超时需要检查交换设备',
    '服务器磁盘空间告警应清理过期备份并检查数据库',
    '仓库烟感触发后现场发现配电柜冒烟需人工复核',
    '人员正常通行设备运行稳定例行维护结束',
    '视频监控设备断线请检查供电和摄像头连接',
)

def validate(spec):
    if not isinstance(spec, dict) or set(spec)-{'kind','units','seed'}:
        raise ValueError('workload needs only kind, units and seed')
    kind=spec.get('kind'); units=spec.get('units'); seed=spec.get('seed',2030)
    if kind not in KINDS: raise ValueError('Unknown business workload')
    low,high=LIMITS[kind]
    if isinstance(units,bool) or not isinstance(units,int) or not low<=units<=high:
        raise ValueError(f'{kind} units must be {low}..{high}')
    if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<=2147483647:
        raise ValueError('seed must be a nonnegative 32-bit integer')
    return {'kind':kind,'units':units,'seed':seed}

def features(spec):
    spec=validate(spec); kind=spec['kind']; units=spec['units']
    # Conservative budgeting, not a measured resident-set guarantee.
    return {'kind':kind,'units':units,'estimated_memory_mib':round(128+(units*.008 if kind=='document_search' else units*.0002),1),
            'cpu_only':True,'input_source':'seeded_synthetic','streaming':kind=='archive'}

def generate_records(spec):
    rng=random.Random(spec['seed'])
    for i in range(spec['units']):
        yield {'timestamp':1700000000+i//20,'device':f'edge-{rng.randrange(512):03d}',
               'site':f'site-{rng.randrange(24):02d}','status':rng.choices([200,400,500],[90,7,3])[0],
               'latency_ms':round(rng.lognormvariate(4,.7),2),'user':f'user-{rng.randrange(30000):05d}',
               'message':SEED_TEXTS[rng.randrange(len(SEED_TEXTS))], 'ticket':f'{i:08d}-{rng.randrange(1000000):06d}'}

def log_analysis(spec):
    devices=defaultdict(lambda: {'count':0,'errors':0,'latency':0.0})
    counts=Counter();latencies=[];alerts=Counter();bytes_in=0
    pattern=re.compile('超时|告警|冒烟|断线|温度偏高')
    for original in generate_records(spec):
        # Exercise actual JSONL parsing, validation, aggregation and exact quantile sorting.
        line=json.dumps(original,ensure_ascii=False);bytes_in+=len(line.encode())
        row=json.loads(line);device=devices[row['device']]
        device['count']+=1;device['errors']+=int(row['status']>=400);device['latency']+=row['latency_ms']
        counts[str(row['status'])]+=1;latencies.append(row['latency_ms'])
        alerts.update(pattern.findall(row['message']))
    latencies.sort()
    top=sorted(devices.items(),key=lambda p:(-p[1]['errors'],-p[1]['count'],p[0]))[:10]
    return {'records':spec['units'],'input_bytes':bytes_in,'status_counts':dict(counts),'alert_terms':dict(alerts),
            'p95_latency_ms':latencies[math.ceil(len(latencies)*.95)-1], 'top_error_devices':top}

def document_search(spec):
    documents=[];df=Counter();rng=random.Random(spec['seed'])
    for i in range(spec['units']):
        # Independent document records with authored text, tags and unique ticket numbers.
        text='。'.join(SEED_TEXTS[rng.randrange(8)] for _ in range(12)) + f' 工单{i} 站点{rng.randrange(512)}'
        terms=Counter(text[j:j+2] for j in range(len(text)-1))
        documents.append((terms,1+sum(terms.values())/200));df.update(terms.keys())
    rankings=[]
    queries=['配电柜冒烟','门禁夜间访问','网络请求超时','清理磁盘备份','消防出口畅通','检查摄像头供电','机房线路散热','巡查仓库设备']
    for query in queries:
        q=Counter(query[j:j+2] for j in range(len(query)-1));scored=[]
        for i,(doc,length_norm) in enumerate(documents):
            score=sum(count*math.log(1+spec['units']/(1+df[t]))*doc.get(t,0)/length_norm for t,count in q.items())
            scored.append((round(score,6),i))
        scored.sort(key=lambda p:(-p[0],p[1]))
        rankings.append({'query':query,'top10':scored[:10]})
    return {'documents':len(documents),'vocabulary':len(df),'queries':len(queries),'rankings':rankings}

def archive(spec):
    compressor=zlib.compressobj(9);digest=hashlib.sha256();raw_bytes=compressed_bytes=0
    for row in generate_records(spec):
        row['user']='user-redacted'
        raw=(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode()
        raw_bytes+=len(raw);block=compressor.compress(raw);compressed_bytes+=len(block);digest.update(block)
    block=compressor.flush();compressed_bytes+=len(block);digest.update(block)
    return {'records':spec['units'],'uncompressed_bytes':raw_bytes,'compressed_bytes':compressed_bytes,
            'compression_ratio':round(compressed_bytes/raw_bytes,6),'archive_sha256':digest.hexdigest(),
            'artifact_retained':False,'purpose':'redact + compress + integrity validation; benchmark discards archive bytes'}

def run(spec):
    spec=validate(spec);start=time.perf_counter()
    summary={'log_analysis':log_analysis,'document_search':document_search,'archive':archive}[spec['kind']](spec)
    elapsed=round((time.perf_counter()-start)*1000,3)
    digest=hashlib.sha256(json.dumps(summary,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'predictions':[],'model':'business-algorithms-v1','workload':spec,'summary':summary,'digest':digest,
            'compute_ms':elapsed,'classification_ms':0,'load_ms':0,'load_loops':0,
            'input_source':'seeded_synthetic; business algorithms actually executed'}
