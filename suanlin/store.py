import csv
import io
import json
import random
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .scheduler import choose, estimate_ms, POLICIES

OFFLINE_SECONDS = 10
LEASE_SECONDS = 25
MAX_ATTEMPTS = 3

class InputError(ValueError):
    pass

def number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise InputError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "suanlin.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, kind TEXT, message TEXT);
        """)
        self.tasks = {r["id"]: json.loads(r["body"]) for r in self.db.execute("SELECT * FROM tasks")}
        self.workers = {r["id"]: json.loads(r["body"]) for r in self.db.execute("SELECT * FROM workers")}
        self.turn = 0
        self.rng = random.Random(2030)
        # Running work cannot retain leases across a coordinator restart.
        for n in self.workers.values():
            n.update(last_seen=0, busy_task=None, busy_until=0)
            self.save("workers", n)
        for t in self.tasks.values():
            if t["status"] == "running":
                t.update(status="queued", lease=None, worker_id=None)
                self.save("tasks", t)
        token_file = self.directory / "access_token"
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        self.token = token_file.read_text(encoding="utf-8").strip()

    def save(self, table, item):
        self.db.execute(f"INSERT OR REPLACE INTO {table}(id,body) VALUES(?,?)", (item["id"], json.dumps(item, ensure_ascii=False)))
        self.db.commit()

    def event(self, kind, message):
        self.db.execute("INSERT INTO events(at,kind,message) VALUES(?,?,?)", (time.time(), kind, message))
        self.db.commit()

    def submit(self, data):
        with self.lock:
            if len(self.tasks) >= 20000:
                raise InputError("任务存储达到 20000 条上限，请使用新数据目录")
            name = data.get("name", "办公巡检事件分类")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise InputError("任务名称长度应为 1 到 80 字")
            events = data.get("events")
            if not isinstance(events, list) or not 1 <= len(events) <= 128 or any(not isinstance(x, str) or not x.strip() or len(x) > 2000 for x in events):
                raise InputError("请提交 1 到 128 条非空文本，每条不超过 2000 字")
            policy = data.get("policy", "smart")
            privacy = data.get("privacy", "local_only")
            if policy not in POLICIES or privacy not in ("local_only", "any"):
                raise InputError("无效的调度策略或数据范围")
            loops = number(data.get("compute_loops", 0), "负载循环", 0, 2000000)
            if not isinstance(loops, int):
                raise InputError("负载循环应为整数")
            deadline = number(data.get("deadline_ms", 5000), "时限毫秒", 100, 300000)
            fixed_worker = data.get("fixed_worker_id")
            if fixed_worker is not None and (not isinstance(fixed_worker, str) or fixed_worker not in self.workers):
                raise InputError("指定的固定节点不存在")
            now = time.time()
            payload = {"events": events, "compute_loops": loops}
            t = {"id": uuid.uuid4().hex[:12], "name": name.strip(), "created_at": now, "deadline_at": now + deadline / 1000,
                 "deadline_ms": deadline, "policy": policy, "privacy": privacy, "payload": payload,
                 "payload_bytes": len(json.dumps(payload, ensure_ascii=False).encode()), "status": "queued", "attempts": 0,
                 "worker_id": None, "lease": None, "result": None, "error": None, "fixed_worker_id": fixed_worker}
            self.tasks[t["id"]] = t
            self.save("tasks", t)
            self.event("task", f"任务 {t['id']} 已入队，{POLICIES[policy]}")
            return self.public_task(t, True)

    def register(self, data, peer):
        with self.lock:
            node_id = data.get("id")
            if not isinstance(node_id, str) or not 1 <= len(node_id) <= 64 or not all(c.isalnum() or c in "-_" for c in node_id):
                raise InputError("无效的节点 ID")
            name = data.get("name", node_id)
            if not isinstance(name, str) or not 1 <= len(name) <= 80:
                raise InputError("节点名称长度应为 1 到 80 字")
            host = data.get("host", "unknown")
            if not isinstance(host, str) or len(host) > 120:
                raise InputError("无效的主机名称")
            previous = self.workers.get(node_id)
            if previous and time.time() - previous["last_seen"] < OFFLINE_SECONDS:
                raise InputError("节点 ID 已在线，请为此进程设置不同的 --id")
            if len(self.workers) >= 64 and not previous:
                raise InputError("原型最多保存 64 个节点")
            n = {"id": node_id, "name": name, "host": host, "peer": peer, "last_seen": time.time(), "busy_task": None,
                 "busy_until": 0, "ms_per_unit": number(data.get("ms_per_unit", 25), "初始单位耗时", 0.001, 100000),
                 "rtt_ms": 1.0, "bandwidth_mbps": number(data.get("bandwidth_mbps", 100), "带宽配置", 0.1, 10000),
                 "trusted": previous.get("trusted", peer in ("127.0.0.1", "::1")) if previous else peer in ("127.0.0.1", "::1"),
                 "draining": previous.get("draining", False) if previous else False, "completed": previous.get("completed", 0) if previous else 0,
                 "session": secrets.token_urlsafe(24), "mode": "same_host" if peer in ("127.0.0.1", "::1") else "remote_host"}
            self.workers[node_id] = n
            self.save("workers", n)
            self.event("node", f"节点 {name} 已连接（{'同机进程' if n['mode'] == 'same_host' else '远程终端'}）")
            return {"id": node_id, "session": n["session"], "heartbeat_seconds": 2, "lease_seconds": LEASE_SECONDS}

    def worker(self, data):
        node_id = data.get("id", "")
        n = self.workers.get(node_id) if isinstance(node_id, str) else None
        session = data.get("session", "")
        if not n or not isinstance(session, str) or not secrets.compare_digest(n["session"], session):
            raise InputError("节点会话无效，请重新注册")
        return n

    def heartbeat(self, data):
        with self.lock:
            n = self.worker(data)
            n["rtt_ms"] = number(data.get("rtt_ms", 1), "HTTP 往返耗时", 0, 120000)
            n["last_seen"] = time.time()
            # A live worker renews its lease even for a slow inference.
            t = self.tasks.get(n.get("busy_task"))
            if t and t["status"] == "running" and t.get("worker_id") == n["id"] and data.get("active_task") == t["id"] and data.get("lease") == t["lease"]:
                t["lease_until"] = time.time() + LEASE_SECONDS
                self.save("tasks", t)
            self.save("workers", n)
            return {"ok": True, "draining": n["draining"]}

    def recover(self, now):
        for t in self.tasks.values():
            if t["status"] != "running":
                continue
            n = self.workers.get(t["worker_id"])
            if now > t.get("lease_until", 0):
                if n and n.get("busy_task") == t["id"]:
                    n.update(busy_task=None, busy_until=0)
                    self.save("workers", n)
                t.update(status="queued" if t["attempts"] < MAX_ATTEMPTS else "failed", lease=None, worker_id=None,
                         error="节点租约到期，任务已重排" if t["attempts"] < MAX_ATTEMPTS else "重试次数已耗尽", finished_at=now if t["attempts"] >= MAX_ATTEMPTS else None)
                self.save("tasks", t)
                self.event("recovery", f"任务 {t['id']} 租约到期，状态 {t['status']}")

    def poll(self, data):
        with self.lock:
            n = self.worker(data)
            now = time.time()
            n["last_seen"] = now
            self.recover(now)
            self.save("workers", n)
            if n["draining"] or n["busy_task"]:
                return {"task": None}
            candidates = [dict(x, online=now - x["last_seen"] < OFFLINE_SECONDS) for x in self.workers.values()]
            queued = sorted((t for t in self.tasks.values() if t["status"] == "queued"), key=lambda t: (t["deadline_at"], t["created_at"]))
            for t in queued:
                # Persist the random/round-robin choice while waiting for that
                # worker. Polling frequency must not bias either baseline.
                desired_id = t.get("desired_worker")
                desired = next((x for x in candidates if x["id"] == desired_id and x["online"] and not x["draining"] and (t["privacy"] != "local_only" or x["trusted"])), None)
                if not desired or t["policy"] == "smart":
                    desired = choose(t, candidates, now, t["policy"], self.turn, self.rng)
                    if desired and t["policy"] in ("random", "round_robin"):
                        self.turn += 1
                        t["desired_worker"] = desired["id"]
                        self.save("tasks", t)
                if not desired or desired["id"] != n["id"]:
                    continue
                predicted = estimate_ms(t, n, now)
                t.update(status="running", started_at=now, worker_id=n["id"], lease=secrets.token_urlsafe(24),
                         lease_until=now + LEASE_SECONDS, attempts=t["attempts"] + 1, predicted_ms=predicted, error=None)
                n.update(busy_task=t["id"], busy_until=now + predicted / 1000)
                self.save("tasks", t)
                self.save("workers", n)
                self.event("dispatch", f"任务 {t['id']} 分配至 {n['name']}")
                return {"task": {k: t[k] for k in ("id", "lease", "payload")}}
            return {"task": None}

    def complete(self, data):
        with self.lock:
            n = self.worker(data)
            t = self.tasks.get(data.get("task_id"))
            if not t or not isinstance(data.get("lease"), str) or t.get("worker_id") != n["id"] or not secrets.compare_digest(t.get("lease") or "", data["lease"]):
                return {"accepted": False, "reason": "stale_lease"}
            if t["status"] == "cancelled":
                n.update(last_seen=time.time(), busy_task=None, busy_until=0)
                self.save("workers", n)
                return {"accepted": False, "reason": "cancelled"}
            if t["status"] != "running":
                return {"accepted": False, "reason": "already_finished"}
            result = data.get("result")
            error = data.get("error")
            if error:
                if not isinstance(error, str) or len(error) > 1000:
                    raise InputError("无效的错误消息")
                t.update(status="queued" if t["attempts"] < MAX_ATTEMPTS else "failed", error=error,
                         lease=None, worker_id=None, desired_worker=None, finished_at=time.time() if t["attempts"] >= MAX_ATTEMPTS else None)
            else:
                if not isinstance(result, dict) or not isinstance(result.get("predictions"), list) or len(result["predictions"]) != len(t["payload"]["events"]):
                    raise InputError("推理结果不完整")
                compute_ms = number(result.get("compute_ms"), "计算耗时", 0, 3600000)
                units = max(1, len(t["payload"]["events"])) + t["payload"].get("compute_loops", 0) / 20000
                n["ms_per_unit"] = max(0.001, n["ms_per_unit"] * .65 + compute_ms / units * .35)
                n["completed"] += 1
                now = time.time()
                t.update(status="completed", result=result, finished_at=now, latency_ms=round((now - t["created_at"]) * 1000, 3),
                         deadline_met=now <= t["deadline_at"], lease=None)
            n.update(last_seen=time.time(), busy_task=None, busy_until=0)
            self.save("tasks", t)
            self.save("workers", n)
            self.event("complete", f"任务 {t['id']} {t['status']}")
            return {"accepted": True}

    def cancel(self, task_id):
        with self.lock:
            t = self.tasks.get(task_id)
            if not t:
                raise InputError("任务不存在")
            if t["status"] in ("queued", "running"):
                t.update(status="cancelled", finished_at=time.time())
                self.save("tasks", t)
                self.event("cancel", f"任务 {task_id} 已取消")
            return self.public_task(t, True)

    def configure_worker(self, node_id, data):
        with self.lock:
            n = self.workers.get(node_id)
            if not n:
                raise InputError("节点不存在")
            for key in ("draining", "trusted"):
                if key in data:
                    if not isinstance(data[key], bool):
                        raise InputError(f"{key} 应为布尔值")
                    n[key] = data[key]
            if "bandwidth_mbps" in data:
                n["bandwidth_mbps"] = number(data["bandwidth_mbps"], "配置带宽", .1, 10000)
            self.save("workers", n)
            self.event("config", f"节点 {n['name']} 配置已更新")
            return {"ok": True}

    @staticmethod
    def public_task(t, detail=False):
        excluded = {"lease", "lease_until", "desired_worker"}
        if not detail:
            excluded.update(("payload", "result"))
        return {k: v for k, v in t.items() if k not in excluded}

    def snapshot(self):
        with self.lock:
            now = time.time()
            self.recover(now)
            nodes = [{k: v for k, v in x.items() if k != "session"} | {"online": now - x["last_seen"] < OFFLINE_SECONDS} for x in self.workers.values()]
            tasks = sorted(self.tasks.values(), key=lambda x: x["created_at"], reverse=True)
            done = [t for t in tasks if t["status"] == "completed"]
            latencies = sorted(t["latency_ms"] for t in done)
            return {"server_time": now, "version": "1.0.0", "nodes": nodes, "tasks": [self.public_task(t) for t in tasks[:200]],
                    "counts": {s: sum(t["status"] == s for t in tasks) for s in ("queued", "running", "completed", "failed", "cancelled")},
                    "metrics": {"total": len(tasks), "completed": len(done), "deadline_rate": sum(t.get("deadline_met", False) for t in done) / len(tasks) if tasks else None,
                                "mean_ms": sum(latencies) / len(latencies) if latencies else None,
                                "p95_ms": latencies[max(0, __import__('math').ceil(len(latencies) * .95) - 1)] if latencies else None,
                                "payload_bytes": sum(t["payload_bytes"] * t["attempts"] for t in tasks)},
                    "events": [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")],
                    "measurement": "application_loopback" if all(n["mode"] == "same_host" for n in nodes) else "mixed_hosts",
                    "policies": POLICIES}

    def task(self, task_id):
        with self.lock:
            t = self.tasks.get(task_id)
            if not t:
                raise InputError("任务不存在")
            return self.public_task(t, True)

    def export_csv(self):
        with self.lock:
            output = io.StringIO()
            fields = ["id", "name", "policy", "privacy", "status", "worker_id", "created_at", "started_at", "finished_at", "deadline_ms", "latency_ms", "deadline_met", "attempts", "payload_bytes", "predicted_ms"]
            writer = csv.DictWriter(output, fields, extrasaction="ignore")
            writer.writeheader()
            for t in self.tasks.values():
                row = {k: t.get(k, "") for k in fields}
                # Guard CSV formula injection in user-authored cells.
                for k, v in row.items():
                    if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")):
                        row[k] = "'" + v
                writer.writerow(row)
            return "\ufeff" + output.getvalue()
