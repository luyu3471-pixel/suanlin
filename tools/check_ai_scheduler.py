"""Real Qwen scheduling smoke test with two local workers (not two computers)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from suanlin.server import create_server
from suanlin.worker import Worker

def main():
    os.environ['SUANLIN_AI_SCHEDULER']='1'
    with tempfile.TemporaryDirectory() as directory:
        server=create_server('127.0.0.1',0,directory)
        service=threading.Thread(target=server.serve_forever,daemon=True)
        service.start()
        workers=[Worker('http://127.0.0.1:'+str(server.server_address[1]),server.store.token,node_id=n) for n in ('test-a','test-b')]
        threads=[threading.Thread(target=w.run,daemon=True) for w in workers]
        for t in threads:t.start()
        try:
            limit=time.monotonic()+15
            while len(server.store.workers)<2 and time.monotonic()<limit:time.sleep(.05)
            tasks=[server.store.submit({'events':['配电箱发现烟雾'], 'compute_loops':100000,'policy':'ai','deadline_ms':30000}) for _ in range(8)]
            end=time.monotonic()+35
            while time.monotonic()<end:
                rows=[server.store.task(t['id']) for t in tasks]
                if all(r['status'] in ('completed','failed') for r in rows):break
                time.sleep(.1)
            result={'scope':'two local worker processes/threads, not two hosts','counts':dict(Counter(r['status'] for r in rows)), 'decisions':[r.get('scheduler_decision') for r in rows], 'workers':dict(Counter(r['worker_id'] for r in rows)), 'execution_models':list({r.get('result',{}).get('model') for r in rows if r.get('result')}),'ai_status':dict(server.store.ai.status)}
            Path('.local').mkdir(exist_ok=True)
            Path('.local/ai-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False),flush=True)
            assert all(r['status']=='completed' for r in rows)
            assert any(r.get('scheduler_decision',{}).get('source')=='qwen' for r in rows), 'All tasks fell back; inspect timing'
            assert result['execution_models']==['char-bigram-naive-bayes-v1']
        finally:
            for w in workers:w.stop.set()
            for t in threads:t.join(timeout=10)
            server.shutdown(); service.join(timeout=5)
            server.server_close(); server.store.db.close()

if __name__=='__main__':main()
