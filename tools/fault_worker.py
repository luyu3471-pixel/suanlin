"""Explicit controlled fault injector: exits ONLY itself after its first task.

Not for production, performance benchmarking, network or hardware-failure claims.
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import suanlin.worker as worker_module

def main():
    parser = argparse.ArgumentParser(description='受控进程故障端：收到第一项任务后仅退出自身，预期退出码73')
    parser.add_argument('--coordinator', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--id', default='office-b-fault')
    parser.add_argument('--name', default='电脑B受控故障端')
    args = parser.parse_args()
    if not args.coordinator.startswith(('http://', 'https://')):
        parser.error('coordinator 必须为HTTP(S)地址')
    token = Path(args.token_file).read_text(encoding='utf-8-sig').strip()
    if not token:
        parser.error('令牌为空')
    def deliberate_exit(payload):
        print('已领取测试任务：现在按预设退出本进程。仅用于受控进程中断验证，退出码73。', flush=True)
        os._exit(73)
    worker_module.run_task = deliberate_exit
    print('故障注入模式已启动：收到第一项任务后退出自身。不会关闭其他程序、断网或关机。', flush=True)
    try:
        worker_module.Worker(args.coordinator, token, args.name, args.id).run()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__': main()
