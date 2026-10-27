"""Actual worker process. No arbitrary code execution or remote shell."""
import argparse
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from .inference import run_task

class Worker:
    def __init__(self, url, token, name=None, node_id=None, bandwidth=100, stop=None):
        self.url = url.rstrip("/")
        self.token = token
        self.name = name or socket.gethostname()
        self.id = node_id or f"node-{uuid.uuid4().hex[:10]}"
        self.bandwidth = bandwidth
        self.stop = stop or threading.Event()
        self.session = None
        self.active_task = None
        self.active_lease = None
        self.rtt_ms = 1
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path, data):
        req = urllib.request.Request(self.url + path, json.dumps(data, ensure_ascii=False).encode(), {"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        before = time.perf_counter()
        with self.opener.open(req, timeout=8) as response:
            result = json.load(response)
        self.rtt_ms = (time.perf_counter() - before) * 1000
        return result

    def identity(self):
        return {"id": self.id, "session": self.session}

    def heartbeat(self):
        while not self.stop.wait(2):
            if not self.session:
                continue
            try:
                self.post("/api/worker/heartbeat", dict(self.identity(), rtt_ms=self.rtt_ms, active_task=self.active_task, lease=self.active_lease))
            except (OSError, ValueError):
                pass

    def run(self):
        thread = threading.Thread(target=self.heartbeat, daemon=True)
        thread.start()
        while not self.stop.is_set():
            try:
                if not self.session:
                    result = self.post("/api/worker/register", {"id": self.id, "name": self.name, "host": socket.gethostname(), "bandwidth_mbps": self.bandwidth})
                    self.session = result["session"]
                claim = self.post("/api/worker/poll", self.identity()).get("task")
                if not claim:
                    self.stop.wait(.15)
                    continue
                message = dict(self.identity(), task_id=claim["id"], lease=claim["lease"])
                self.active_task, self.active_lease = claim["id"], claim["lease"]
                try:
                    message["result"] = run_task(claim["payload"])
                except Exception as exc:
                    message["error"] = str(exc)[:1000]
                # Retry delivery of the same result/lease on transient errors.
                for _ in range(5):
                    try:
                        self.post("/api/worker/result", message)
                        break
                    except OSError:
                        if self.stop.wait(1):
                            break
                self.active_task = self.active_lease = None
            except urllib.error.HTTPError as exc:
                if exc.code == 400:
                    # Coordinator may have restarted or replaced this session.
                    self.stop.wait(OFFLINE_REJOIN_SECONDS)
                    self.session = None
                self.stop.wait(1)
            except (OSError, ValueError):
                self.stop.wait(1)

OFFLINE_REJOIN_SECONDS = 11

def main():
    parser = argparse.ArgumentParser(description="算邻执行节点")
    parser.add_argument("--coordinator", required=True)
    parser.add_argument("--name")
    parser.add_argument("--id")
    parser.add_argument("--token-file")
    parser.add_argument("--token-env", default="SUANLIN_TOKEN")
    parser.add_argument("--bandwidth", type=float, default=100)
    args = parser.parse_args()
    if not args.coordinator.startswith(("http://", "https://")):
        parser.error("coordinator 应为 http(s) 地址")
    if args.token_file:
        from pathlib import Path
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = os.environ.get(args.token_env, "")
    if not token:
        parser.error("请通过 --token-file 或 SUANLIN_TOKEN 提供接入令牌")
    try:
        Worker(args.coordinator, token, args.name, args.id, args.bandwidth).run()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
