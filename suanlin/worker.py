"""One node, bounded CPU processes, individual renewable leases."""
import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, Future
import tempfile
from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from .inference import run_task
from .resources import Monitor
from .business import KINDS
from .execution import execute

OFFLINE_REJOIN_SECONDS = 11

class Worker:
    def __init__(self, url, token, name=None, node_id=None, bandwidth=100, stop=None, concurrency=1):
        if not 1 <= concurrency <= 32:
            raise ValueError('concurrency must be 1..32')
        self.url, self.token = url.rstrip('/'), token
        self.name, self.id = name or socket.gethostname(), node_id or f'node-{uuid.uuid4().hex[:10]}'
        self.bandwidth, self.capacity = bandwidth, concurrency
        self.stop = stop or threading.Event()
        self.monitor = Monitor(self.stop)
        self.session = None
        self.rtt_ms = 1
        self.active = {}
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path, data):
        request = urllib.request.Request(self.url + path, json.dumps(data, ensure_ascii=False).encode(),
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        before = time.perf_counter()
        with self.opener.open(request, timeout=8) as response:
            result = json.load(response)
        if path != '/api/worker/poll':  # Long-poll waiting is not network RTT.
            self.rtt_ms = .7 * self.rtt_ms + .3 * (time.perf_counter() - before) * 1000
        return result

    def identity(self):
        return {'id': self.id, 'session': self.session}

    def heartbeat(self):
        while not self.stop.wait(2):
            if self.session:
                with self.lock:
                    items = [{'id': k, 'lease': v[1]['lease']} for k, v in self.active.items()]
                for item in items:
                    try:
                        item['progress']=json.loads((self.control_dir / (item['lease']+'.json')).read_text(encoding='utf-8'))
                    except (OSError,ValueError): pass
                message = dict(self.identity(), rtt_ms=self.rtt_ms, active_tasks=items, resources=self.monitor.data, resource_sequence=self.monitor.sequence)
                if len(items) == 1:
                    message.update(active_task=items[0]['id'], lease=items[0]['lease'])
                try:
                    response=self.post('/api/worker/heartbeat', message)
                    for item in response.get('cancel_tasks',[]):
                        if any(x['id']==item.get('id') and x['lease']==item.get('lease') for x in items):
                            (self.control_dir / (item['lease']+'.cancel')).touch()
                except (OSError, ValueError):
                    pass

    def run(self):
        threading.Thread(target=self.monitor.run, daemon=True, name="resource-monitor").start()
        executor = ProcessPoolExecutor(max_workers=self.capacity) if self.capacity > 1 else ThreadPoolExecutor(max_workers=1)
        with tempfile.TemporaryDirectory(prefix='suanlin-worker-') as control, executor as pool:
            self.control_dir=Path(control)
            heart = threading.Thread(target=self.heartbeat, daemon=True)
            heart.start()
            try:
                while not self.stop.is_set():
                    try:
                        if not self.session:
                            result = self.post('/api/worker/register', dict(id=self.id, name=self.name,
                                host=socket.gethostname(), bandwidth_mbps=self.bandwidth, capacity=self.capacity, resources=self.monitor.data, workload_kinds=list(KINDS), file_workloads=True))
                            if self.capacity > result.get('capacity', 1):
                                raise RuntimeError('Upgrade coordinator A before enabling concurrent workers')
                            self.session = result['session']
                        with self.lock:
                            pending = list(self.active.items())
                        for task_id, (future, message) in pending:
                            if not future.done():
                                continue
                            if 'result' not in message and 'error' not in message:
                                try:
                                    message['result'] = future.result()
                                except Exception as exc:
                                    message['error'] = str(exc)[:1000]
                            self.post('/api/worker/result', message)
                            with self.lock:
                                self.active.pop(task_id, None)
                            for suffix in ('.json','.cancel'):
                                (self.control_dir / (message['lease']+suffix)).unlink(missing_ok=True)
                        with self.lock:
                            count = len(self.active)
                        if count < self.capacity:
                            claim = self.post('/api/worker/poll', dict(self.identity(), wait_seconds=0 if count else 1)).get('task')
                            if claim:
                                message = dict(self.identity(), task_id=claim['id'], lease=claim['lease'])
                                future = pool.submit(execute, claim['payload'],
                                    str(self.control_dir / (claim['lease']+'.json')),
                                    str(self.control_dir / (claim['lease']+'.cancel')))
                                with self.lock:
                                    self.active[claim['id']] = (future, message)
                            else:
                                self.stop.wait(.05)
                        else:
                            self.stop.wait(.005)
                    except urllib.error.HTTPError as exc:
                        if exc.code == 400:
                            with self.lock:
                                old = list(self.active.values())
                            while not self.stop.is_set() and any(not f.done() for f, _ in old):
                                self.stop.wait(.05)
                            with self.lock:
                                self.active.clear()
                            self.session = None
                            self.stop.wait(OFFLINE_REJOIN_SECONDS)
                        else:
                            self.stop.wait(1)
                    except (OSError, ValueError):
                        self.stop.wait(1)
            finally:
                self.stop.set()
                with self.lock:
                    for _, message in self.active.values():
                        (self.control_dir / (message['lease']+'.cancel')).touch()
                heart.join(timeout=10)

def main():
    parser = argparse.ArgumentParser(description='并行任务执行节点')
    parser.add_argument('--coordinator', required=True)
    parser.add_argument('--name')
    parser.add_argument('--id')
    parser.add_argument('--token-file')
    parser.add_argument('--token-env', default='SUANLIN_TOKEN')
    parser.add_argument('--bandwidth', type=float, default=100)
    parser.add_argument('--concurrency', type=int, choices=range(1, 33), default=1)
    parser.add_argument('--stop-file', help='Exit gracefully when this local file appears')
    args = parser.parse_args()
    if not args.coordinator.startswith(('http://', 'https://')):
        parser.error('coordinator must be an HTTP(S) URL')
    token = Path(args.token_file).read_text(encoding='utf-8-sig').strip() if args.token_file else os.environ.get(args.token_env, '')
    if not token:
        parser.error('Missing token file or environment variable')
    worker = Worker(args.coordinator, token, args.name, args.id, args.bandwidth, concurrency=args.concurrency)
    if args.stop_file:
        def watch_stop():
            while not worker.stop.wait(.2):
                if Path(args.stop_file).exists():
                    worker.stop.set()
        threading.Thread(target=watch_stop, daemon=True).start()
    try:
        worker.run()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
