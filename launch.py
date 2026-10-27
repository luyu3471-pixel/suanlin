"""Start a coordinator and two genuine local worker subprocesses."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser
from suanlin.server import create_server

def main():
    parser = argparse.ArgumentParser(description="多终端任务调度系统一键启动")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workers", type=int, choices=range(0, 9), default=2)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    try:
        server = create_server(args.host, args.port, args.data_dir)
    except OSError as exc:
        print(f"启动失败：{exc}。如果端口已占用，请使用 --port 8766。")
        return 1
    root = Path(__file__).resolve().parent
    token_file = server.store.directory.resolve() / "access_token"
    url = f"http://127.0.0.1:{server.server_address[1]}"
    children = []
    print(f"多终端任务调度系统已启动：{url}\n演示模式：{args.workers} 个同机工作进程，非多终端实测。\n按 Ctrl+C 停止服务。", flush=True)
    for i in range(args.workers):
        command = [sys.executable, "-m", "suanlin.worker", "--coordinator", url, "--token-file", str(token_file), "--name", f"本机执行节点 {i + 1}", "--id", f"local-{i + 1}"]
        children.append(subprocess.Popen(command, cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    if not args.no_browser:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        server.server_close()
        server.store.db.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
