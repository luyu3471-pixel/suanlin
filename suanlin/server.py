import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from .inference import DEMO_EVENTS
from .store import Store, InputError

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web"
MAX_BODY = 1024 * 1024

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler(BaseHTTPRequestHandler):
    server_version = "Suanlin/1.0"

    def log_message(self, format, *args):
        # Access tokens and event payloads are never written to logs.
        pass

    @property
    def store(self):
        return self.server.store

    def send(self, status, data, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer ") and secrets.compare_digest(bearer[7:], self.store.token):
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        value = cookies.get("suanlin_session")
        return bool(value and secrets.compare_digest(value.value, self.server.browser_session))

    def same_origin(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin:
            return origin in ("http://" + host, "https://" + host)
        return self.headers.get("Sec-Fetch-Site") not in ("cross-site",)

    def valid_host(self):
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host in ("localhost", "127.0.0.1"):
            return True
        try:
            return ipaddress.ip_address(host).is_private and self.server.allow_lan
        except ValueError:
            return False

    def cookie(self):
        return "suanlin_session=" + self.server.browser_session + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"

    def body(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise InputError("请求应使用 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise InputError("无效的请求长度") from exc
        if not 0 < length <= MAX_BODY:
            raise InputError("请求应在 1 MB 以内")
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise InputError("请求内容应为 JSON 对象")
        return result

    def do_GET(self):
        try:
            if not self.valid_host():
                return self.send(403, {"error": "主机地址不受支持"})
            path = urlparse(self.path).path
            if path == "/api/health":
                return self.send(200, {"ok": True, "service": "suanlin", "version": "1.0.0"})
            if path.startswith("/api/"):
                if not self.authorized():
                    return self.send(401, {"error": "请使用接入令牌登录"})
                if path == "/api/status":
                    return self.send(200, self.store.snapshot())
                if path == "/api/tasks/export.csv":
                    return self.send(200, self.store.export_csv(), "text/csv; charset=utf-8", {"Content-Disposition": 'attachment; filename="suanlin-tasks.csv"'})
                if path.startswith("/api/tasks/"):
                    return self.send(200, self.store.task(path.rsplit("/", 1)[-1]))
                if path == "/api/experiments":
                    f = ROOT / "results" / "simulation_summary.json"
                    return self.send(200, json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"available": False})
                return self.send(404, {"error": "接口不存在"})
            filenames = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/guide": "guide.html"}
            filename = filenames.get(path)
            if not filename:
                return self.send(404, "页面不存在", "text/plain; charset=utf-8")
            headers = {}
            if path == "/" and self.client_address[0] in ("127.0.0.1", "::1"):
                headers["Set-Cookie"] = self.cookie()
            return self.send(200, (STATIC / filename).read_bytes(), mimetypes.guess_type(filename)[0] + ("; charset=utf-8" if filename.endswith((".html", ".css", ".js")) else ""), headers)
        except (ValueError, InputError) as exc:
            self.send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send(500, {"error": "服务处理失败，请检查本地数据目录和服务日志"})

    def do_POST(self):
        try:
            if not self.valid_host() or not self.same_origin():
                return self.send(403, {"error": "请求来源不受支持"})
            path = urlparse(self.path).path
            if path == "/api/login":
                # Bounded attempts per source, a long random token is required.
                peer = self.client_address[0]
                now = time.time()
                with self.server.login_lock:
                    attempts = [t for t in self.server.login_attempts.get(peer, []) if now - t < 60]
                    if len(attempts) >= 10:
                        return self.send(429, {"error": "尝试过于频繁，请一分钟后重试"})
                    self.server.login_attempts[peer] = attempts + [now]
                data = self.body()
                token = data.get("token", "")
                if not isinstance(token, str) or not secrets.compare_digest(token, self.store.token):
                    return self.send(401, {"error": "接入令牌不正确"})
                return self.send(200, {"ok": True}, headers={"Set-Cookie": self.cookie()})
            if not self.authorized():
                return self.send(401, {"error": "未授权"})
            data = self.body()
            if path == "/api/tasks":
                return self.send(201, self.store.submit(data))
            if path == "/api/demo":
                from .store import number
                count = number(data.get("count", 12), "任务数量", 1, 30)
                if not isinstance(count, int):
                    raise InputError("任务数量应为整数")
                policy = data.get("policy", "smart")
                if policy not in ("smart", "fixed", "round_robin", "random"):
                    raise InputError("无效调度策略")
                tasks = [self.store.submit({"name": f"合成巡检事件 {i + 1:02d}", "events": [DEMO_EVENTS[i % len(DEMO_EVENTS)]], "compute_loops": 250000 + (i % 3) * 100000, "deadline_ms": 5000,
                                            "policy": policy, "privacy": "local_only"}) for i in range(count)]
                return self.send(201, {"tasks": tasks, "source": "synthetic text, actual process execution"})
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                return self.send(200, self.store.cancel(path.split("/")[3]))
            if path.startswith("/api/nodes/"):
                return self.send(200, self.store.configure_worker(path.rsplit("/", 1)[-1], data))
            actions = {"/api/worker/register": lambda d: self.store.register(d, self.client_address[0]),
                       "/api/worker/heartbeat": self.store.heartbeat, "/api/worker/poll": self.store.poll, "/api/worker/result": self.store.complete}
            if path in actions:
                return self.send(200, actions[path](data))
            return self.send(404, {"error": "接口不存在"})
        except (ValueError, InputError, TypeError) as exc:
            self.send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send(500, {"error": "服务处理失败"})

def create_server(host="127.0.0.1", port=8765, directory=None):
    server = Server((host, port), Handler)
    server.store = Store(directory or ROOT / ".local")
    server.browser_session = secrets.token_urlsafe(32)
    server.allow_lan = host != "127.0.0.1"
    server.login_lock = threading.Lock()
    server.login_attempts = {}
    return server

def main():
    parser = argparse.ArgumentParser(description="协调服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.data_dir)
    print(f"协调服务 http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.db.close()

if __name__ == "__main__":
    main()
