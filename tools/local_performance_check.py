"""Isolated loopback regression only. Never connects to the production coordinator."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.performance_suite import Client, run

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'.local'/'local-capacity-check')
    args=parser.parse_args()
    args.output = args.output.resolve()
    args.baseline_root = args.baseline_root.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    reports=[]
    for label,source,capacity in [('original-1',args.baseline_root,1),('optimized-1',ROOT,1),('optimized-4',ROOT,4)]:
        folder=args.output/label; folder.mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        worker_process=None
        stop_file=folder/'worker.stop'
        log=(folder/'server.log').open('w',encoding='utf-8')
        server=subprocess.Popen([sys.executable,'-m','suanlin.server','--port',str(port),'--data-dir',str(folder/'state')],
            cwd=source,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            token_file=folder/'state'/'access_token'
            for _ in range(100):
                if token_file.exists(): break
                if server.poll() is not None: raise RuntimeError('Local server failed; see log')
                time.sleep(.1)
            client=Client(url,token_file)
            command=[sys.executable,'-m','suanlin.worker','--coordinator',url,'--token-file',str(token_file),'--id','local-1']
            if not label.startswith('original'):
                command += ['--concurrency',str(capacity),'--stop-file',str(stop_file)]
            worker_process=subprocess.Popen(command,cwd=source,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for _ in range(100):
                if client.call('/api/status')['nodes']: break
                time.sleep(.1)
            print('CONFIGURATION='+label,flush=True)
            result=run(argparse.Namespace(dataset=ROOT/'benchmarks'/'smoke-v2.json',cases=None,url=url,
                token_file=token_file,rounds=2,submit_concurrency=8,fixed_worker='local-1',policies=['fixed'],
                timeout=180,output_root=folder,allow_same_host_test=True))
            reports.append({'configuration':label,'capacity':capacity,'output':str(result),
                'trials':json.loads((result/'summary.json').read_text(encoding='utf-8'))['trials']})
            (args.output/'comparison.json').write_text(json.dumps({'scope':'same_physical_host_loopback_not_LAN', 'reports':reports},ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            if worker_process:
                if label.startswith('original'):
                    worker_process.terminate()
                else:
                    stop_file.touch()
                try:
                    worker_process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill','/PID',str(worker_process.pid),'/T','/F'],capture_output=True)
                    worker_process.wait(timeout=10)
            server.terminate(); server.wait(timeout=10); log.close()

if __name__=='__main__': main()
