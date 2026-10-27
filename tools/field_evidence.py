"""Field-evidence helpers. No fabricated interviews, physical-device or radio claims.

All newly collected data stays under .local and is excluded from submission ZIPs.
Runtime dependencies: Python standard library only.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import random
import shutil
import statistics
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from suanlin.inference import DEMO_EVENTS, MODEL, TRAINING
from suanlin.scheduler import POLICIES

PRIVATE = ROOT / '.local' / 'field-evidence'
TERMINAL = {'completed', 'failed', 'cancelled'}
CN_TIME = timezone(timedelta(hours=8))

def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def write_csv(path, records):
    if not records:
        return
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, list(records[0]), extrasaction='ignore')
        writer.writeheader()
        for row in records:
            # Exported human-authored strings must not become spreadsheet formulas.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in row.items()})

def new_directory(kind, parent=None):
    base = Path(parent) if parent else PRIVATE / 'runs'
    path = base / (datetime.now(CN_TIME).strftime('%Y%m%d-%H%M%S') + '-' + kind + '-' + uuid.uuid4().hex[:6])
    path.mkdir(parents=True, exist_ok=False)
    return path

class Client:
    def __init__(self, url, token_file):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('协调端应为有效的 HTTP(S) 地址，不要在 URL 中放令牌')
        self.url = url.rstrip('/')
        self.token = Path(token_file).read_text(encoding='utf-8-sig').strip()
        if not self.token:
            raise ValueError('接入令牌文件为空')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, data=None):
        payload = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        request = urllib.request.Request(self.url + path, payload, {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with self.opener.open(request, timeout=10) as response:
            return json.load(response)

    def task(self, task_id):
        return self.call('/api/tasks/' + task_id)

def preflight(client, args, required_ids=()):
    snapshot = client.call('/api/status')
    if snapshot['counts']['queued'] or snapshot['counts']['running']:
        raise ValueError('请先等当前排队和运行中的任务结束，再做独立实验')
    nodes = [n for n in snapshot['nodes'] if n['online'] and n['trusted'] and not n['draining']]
    if len(nodes) < 2:
        raise ValueError('至少需要两个在线、可信、正在接收任务的节点')
    if not getattr(args, 'allow_same_host_test', False) and not any(n['mode'] == 'remote_host' for n in nodes):
        raise ValueError('未观察到远程连接。请先接入第二台电脑；本机工具自测可显式加 --allow-same-host-test，结果会标为同机自测')
    for node_id in required_ids:
        if node_id not in {n['id'] for n in nodes}:
            raise ValueError('指定节点未就绪：' + node_id)
    return snapshot, nodes

def load_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not {'text', 'label', 'source'}.issubset(reader.fieldnames or []):
            raise ValueError('事件 CSV 必须含 text、label、source 三列，建议从 init 生成的表格填写')
        rows = []
        for number, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'第 {number} 行列数与表头不符；请用Excel/WPS另存为CSV UTF-8，含英文逗号的文字需要正确引用')
            if not any((v or '').strip() for v in row.values()):
                continue
            text, label, source = (row.get(k, '').strip() for k in ('text', 'label', 'source'))
            if not text or len(text) > 2000 or label not in TRAINING or not source:
                raise ValueError(f'第 {number} 行缺少有效文本、四类标签或数据来源')
            rows.append({'id': row.get('id', '').strip() or str(number - 1), 'text': text, 'label': label, 'source': source,
                         'observed_at': row.get('observed_at', ''), 'reviewer_a_label': row.get('reviewer_a_label', ''), 'reviewer_b_label': row.get('reviewer_b_label', '')})
    if not rows:
        raise ValueError('事件表没有已填写的数据；程序不会自动填充真实事件')
    return rows

def wait_tasks(client, tasks, timeout=120):
    limit = time.monotonic() + timeout
    while True:
        actual = [client.task(t['id']) for t in tasks]
        if all(t['status'] in TERMINAL for t in actual):
            return actual
        if time.monotonic() >= limit:
            raise TimeoutError('本轮未在等待期限内结束。当前任务已保存在协调端，未自动删除或取消')
        time.sleep(.1)

def trial_metrics(tasks):
    values = sorted(t['latency_ms'] for t in tasks if t['status'] == 'completed')
    return {'submitted': len(tasks), 'completed': len(values), 'failed': sum(t['status'] == 'failed' for t in tasks),
            'cancelled': sum(t['status'] == 'cancelled' for t in tasks), 'unfinished': sum(t['status'] not in TERMINAL for t in tasks),
            'deadline_rate': sum(t.get('deadline_met', False) for t in tasks) / len(tasks) if tasks else None,
            'mean_ms_successful': statistics.mean(values) if values else None,
            'p95_ms_successful': values[math.ceil(len(values) * .95) - 1] if values else None,
            'worker_counts': dict(Counter(t.get('worker_id') or 'unassigned' for t in tasks))}

def run_comparison(args):
    client = Client(args.url, args.token_file)
    before, nodes = preflight(client, args, (args.fixed_worker,))
    if args.use_demo:
        rows = [{'id': str(i), 'text': text, 'label': '', 'source': '自编合成'} for i, text in enumerate(DEMO_EVENTS)]
        source_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    else:
        rows = load_rows(args.events_csv)
        source_hash = hashlib.sha256(Path(args.events_csv).read_bytes()).hexdigest()
    output = new_directory('comparison', args.output_root)
    print('本次数据保存至：' + str(output), flush=True)
    write_json(output / 'nodes-before.json', before)
    write_json(output / 'input-events.json', rows)
    if not args.use_demo:
        shutil.copyfile(args.events_csv, output / 'input-original.csv')
    metadata = {'recorded_at': datetime.now(CN_TIME).isoformat(), 'kind': 'actual_http_task_execution',
                'topology_observed': 'remote_connection_present_physical_devices_require_inventory_and_photos' if any(n['mode'] == 'remote_host' for n in nodes) else 'same_host_tool_test',
                'physical_devices_verified_by_script': False, 'data_sources_user_recorded': dict(Counter(r['source'] for r in rows)),
                'unique_input_texts': len({r['text'] for r in rows}), 'source_sha256': source_hash,
                'scenario_note': args.note, 'rounds': args.rounds, 'tasks_per_trial': args.tasks, 'deadline_ms': args.deadline_ms,
                'submit_interval_ms': args.interval_ms, 'extra_cpu_loops': args.compute_loops,
                'workload_scope': 'includes_explicit_artificial_cpu_load' if args.compute_loops else 'classifier_only_no_artificial_cpu_load',
                'fixed_worker': args.fixed_worker, 'python': platform.python_version(), 'coordinator_url': args.url,
                'metric_notes': ['截止前完成数除以全部提交数；失败不剔除', '均值和P95仅含完成任务，失败与未完成数另报',
                                 'P95采用每轮最近秩法；汇总为逐轮指标均值', '文本可以重复调度，任务次数不等于独立业务事件数',
                                 'HTTP往返与端到端时延不是无线空口时延；配置带宽不是自动测量值']}
    write_json(output / 'run-info.json', metadata)
    raw, trials, submitted = [], [], []
    try:
        warmup = [client.call('/api/tasks', {'name': '现场实验预热', 'events': [rows[i % len(rows)]['text']], 'compute_loops': args.compute_loops,
                                            'deadline_ms': 300000, 'policy': 'round_robin'}) for i in range(4)]
        submitted.extend(warmup)
        write_json(output / 'warmup-results.json', wait_tasks(client, warmup))
        policies = ['fixed', 'round_robin', 'random', 'smart']
        for round_index in range(args.rounds):
            chosen = [rows[i % len(rows)] for i in range(args.tasks)]
            random.Random(2030 + round_index).shuffle(chosen)
            write_json(output / f'round-{round_index + 1:02d}-input.json', chosen)
            order = policies[round_index % 4:] + policies[:round_index % 4]
            for policy in order:
                ids = []
                for i, event in enumerate(chosen):
                    task = client.call('/api/tasks', {'name': f'现场-{output.name[-6:]}-{round_index + 1}-{policy}-{i + 1}',
                                                     'events': [event['text']], 'policy': policy, 'deadline_ms': args.deadline_ms,
                                                     'privacy': 'local_only', 'compute_loops': args.compute_loops,
                                                     'fixed_worker_id': args.fixed_worker if policy == 'fixed' else None})
                    ids.append(task); submitted.append(task)
                    if args.interval_ms:
                        time.sleep(args.interval_ms / 1000)
                actual = wait_tasks(client, ids)
                # Full results are retained separately from the scan-friendly CSV.
                write_json(output / f'round-{round_index + 1:02d}-{policy}-results.json', actual)
                for event, task in zip(chosen, actual):
                    result = task.get('result') or {}
                    raw.append({'round': round_index + 1, 'policy': policy, 'event_id': event['id'], 'source': event['source'],
                                'task_id': task['id'], 'worker_id': task.get('worker_id'), 'status': task['status'],
                                'created_at': task['created_at'], 'finished_at': task.get('finished_at'), 'deadline_ms': task['deadline_ms'],
                                'latency_ms': task.get('latency_ms'), 'deadline_met': task.get('deadline_met', False), 'attempts': task['attempts'],
                                'compute_ms': result.get('compute_ms'), 'extra_load_ms': result.get('load_ms'), 'extra_load_loops': args.compute_loops})
                metrics = trial_metrics(actual)
                trials.append({'round': round_index + 1, 'policy': policy, **metrics})
                write_csv(output / 'tasks.csv', raw)
                write_json(output / 'trials.json', trials)
                print(f"第 {round_index + 1}/{args.rounds} 轮 {POLICIES[policy]}：完成 {metrics['completed']}/{metrics['submitted']}，按时率 {metrics['deadline_rate']:.1%}", flush=True)
        groups = []
        for policy in policies:
            records = [t for t in trials if t['policy'] == policy]
            means = [t['mean_ms_successful'] for t in records if t['mean_ms_successful'] is not None]
            tails = [t['p95_ms_successful'] for t in records if t['p95_ms_successful'] is not None]
            groups.append({'policy': policy, 'label': POLICIES[policy], 'submitted': sum(t['submitted'] for t in records),
                           'completed': sum(t['completed'] for t in records), 'failed': sum(t['failed'] for t in records),
                           'deadline_rate_mean': statistics.mean(t['deadline_rate'] for t in records),
                           'mean_ms_successful_trial_mean': statistics.mean(means) if means else None,
                           'p95_ms_successful_trial_mean': statistics.mean(tails) if tails else None})
        write_json(output / 'summary.json', {'complete': True, 'metadata': metadata, 'groups': groups, 'trial_count': len(trials)})
        lines = ['# 现场任务对比记录', '', '此记录由实际HTTP任务产生。远程连接不独立证明物理设备数量，请附设备表与照片。',
                 f"输入来源：{dict(Counter(r['source'] for r in rows))}。额外CPU循环：{args.compute_loops}。", '',
                 '| 策略 | 完成/提交 | 按时率 | 均值ms | 逐轮P95均值ms |', '|---|---|---|---|---|']
        for g in groups:
            mean = f"{g['mean_ms_successful_trial_mean']:.2f}" if g['mean_ms_successful_trial_mean'] is not None else '无完成任务'
            tail = f"{g['p95_ms_successful_trial_mean']:.2f}" if g['p95_ms_successful_trial_mean'] is not None else '无完成任务'
            lines.append(f"| {g['label']} | {g['completed']}/{g['submitted']} | {g['deadline_rate_mean']:.1%} | {mean} | {tail} |")
        lines += ['', '合成数据或人工负载只能支持相应条件下的功能/性能结论，不能当作真实业务收益。请保留所有轮次，包括无改善或失败结果。']
        (output / 'summary.md').write_text('\n'.join(lines), encoding='utf-8')
        write_json(output / 'nodes-after.json', client.call('/api/status'))
    except BaseException as exc:
        partial = []
        for task in submitted:
            try: partial.append(client.task(task['id']))
            except Exception: partial.append({'id': task['id'], 'status': 'unknown_after_connection_error'})
        write_json(output / 'incomplete.json', {'complete': False, 'error_type': type(exc).__name__, 'message': str(exc), 'tasks': partial})
        raise
    print('测试完成。请人工核实设备与数据来源后再使用报告。', flush=True)
    return output

def evaluate(args):
    rows = load_rows(args.events_csv)
    output = new_directory('model-evaluation', args.output_root)
    shutil.copyfile(args.events_csv, output / 'input-original.csv')
    labels = list(TRAINING)
    confusion = {actual: {predicted: 0 for predicted in labels} for actual in labels}
    predictions = []
    training_texts = {x.strip() for examples in TRAINING.values() for x in examples}
    overlaps = 0
    for row in rows:
        result = MODEL.predict(row['text'])
        confusion[row['label']][result['label']] += 1
        overlap = row['text'] in training_texts
        overlaps += overlap
        predictions.append(dict(row, predicted_label=result['label'], score_uncalibrated=result['confidence'], correct=row['label'] == result['label'], exact_training_overlap=overlap))
    per_class = []
    for label in labels:
        tp = confusion[label][label]
        support = sum(confusion[label].values())
        predicted_count = sum(confusion[actual][label] for actual in labels)
        precision = tp / predicted_count if predicted_count else 0
        recall = tp / support if support else None
        f1 = 2 * tp / (support + predicted_count) if support else None
        per_class.append({'label': label, 'support': support, 'predicted_count': predicted_count, 'precision': precision, 'recall': recall, 'f1': f1})
    supported = [x['f1'] for x in per_class if x['support']]
    missing = [x['label'] for x in per_class if not x['support']]
    report = {'recorded_at': datetime.now(CN_TIME).isoformat(), 'sample_count': len(rows), 'unique_text_count': len({r['text'] for r in rows}),
              'source_counts_user_recorded_not_verified': dict(Counter(r['source'] for r in rows)),
              'accuracy': sum(x['correct'] for x in predictions) / len(rows), 'macro_f1_present_true_classes': statistics.mean(supported),
              'classes_absent_from_test': missing, 'per_class': per_class, 'confusion_matrix_rows_true_columns_predicted': confusion,
              'exact_training_overlap_count': overlaps, 'independence_not_automatically_proven': True,
              'notes': ['真实/历史/模拟/合成来源由填表者负责核实', '缺少的类别没有召回率证据，不能宣传四类均已验证',
                        '与训练集重合或大量重复文本会削弱独立评测效力', '人工标签应在查看模型输出前独立确定，保留分歧', '模型分数不是经过校准的现实正确概率']}
    write_json(output / 'metrics.json', report)
    write_csv(output / 'predictions.csv', predictions)
    write_json(output / 'mistakes.json', [x for x in predictions if not x['correct']])
    print(f"评测完成：{len(rows)}条，准确率 {report['accuracy']:.1%}；数据来源、重复与缺失类别请查看 metrics.json。保存至 {output}", flush=True)
    return output

def initialize():
    destination = PRIVATE / 'records'
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / 'docs' / '证据模板').iterdir():
        target = destination / source.name
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)
    print('空白记录表已准备在：' + str(destination) + '。已有文件未覆盖。')
    return destination

def failure_test(args):
    if args.target == args.fallback:
        raise ValueError('故障端与接替端必须不同')
    client = Client(args.url, args.token_file)
    before, nodes = preflight(client, args, (args.target, args.fallback))
    output = new_directory('process-failure', args.output_root)
    write_json(output / 'nodes-before.json', before)
    print('受控故障测试记录：' + str(output), flush=True)
    print('目标端必须使用 tools/fault_worker.py 启动；它收到任务后会按预设退出。', flush=True)
    original = {n['id']: n['draining'] for n in before['nodes']}
    changed = []
    timeline = []
    task = None
    try:
        for node in nodes:
            if node['id'] != args.target:
                client.call('/api/nodes/' + node['id'], {'draining': True})
                changed.append(node['id'])
        task = client.call('/api/tasks', {'name': '受控执行进程中断验证', 'events': ['办公室巡检无异常'], 'policy': 'smart', 'privacy': 'local_only', 'deadline_ms': 120000})
        limit = time.monotonic() + 90
        observed_target = False
        fallback_enabled = False
        while time.monotonic() < limit:
            snapshot = client.call('/api/status')
            actual = client.task(task['id'])
            observed_target = observed_target or actual.get('worker_id') == args.target
            timeline.append({'observed_at': datetime.now(CN_TIME).isoformat(), 'task': actual,
                             'nodes': [{k: n[k] for k in ('id', 'online', 'draining', 'busy_task', 'last_seen')} for n in snapshot['nodes']],
                             'events': [e for e in snapshot['events'] if task['id'] in e['message']]})
            write_json(output / 'timeline.json', timeline)
            if observed_target and not fallback_enabled:
                client.call('/api/nodes/' + args.fallback, {'draining': False})
                fallback_enabled = True
                print('已观察到目标端领取任务。正在等待租约到期和接替，通常约25秒。', flush=True)
            if actual['status'] in TERMINAL:
                passed = observed_target and actual['status'] == 'completed' and actual['attempts'] >= 2 and actual['worker_id'] == args.fallback
                result = {'passed': passed, 'claim_observed': observed_target, 'task': actual, 'target': args.target, 'fallback': args.fallback,
                          'fault_type': 'deliberately_exit_only_the_fault_worker_process_after_claim',
                          'physical_or_radio_failure_tested': False, 'notes': ['这是受控进程退出，不是无线断网或整机故障实测', '物理终端仍需设备表和照片佐证', '未观测到重排时如实记为未通过，不生成成功结论']}
                write_json(output / 'result.json', result)
                print('观察结果：' + ('进程中断后任务由指定接替端完成' if passed else '本次未形成指定恢复证据，请查 result.json'), flush=True)
                return output, passed
            time.sleep(.5)
        raise TimeoutError('90秒内未观察到指定恢复结果；请保留原始时间线')
    except BaseException as exc:
        write_json(output / 'incomplete.json', {'passed': False, 'message': str(exc), 'task_id': task['id'] if task else None})
        raise
    finally:
        restore_errors = []
        for node_id in changed:
            try: client.call('/api/nodes/' + node_id, {'draining': original[node_id]})
            except Exception as exc: restore_errors.append({'id': node_id, 'message': str(exc)})
        write_json(output / 'node-restore.json', {'restore_errors': restore_errors})
        if restore_errors:
            print('部分节点未能恢复接收设置，请在节点页人工检查。', flush=True)

def main(argv=None):
    parser = argparse.ArgumentParser(description='算邻现场取证工具；所有原始数据保存在 .local，不自动进入参赛压缩包')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='准备空白访谈、设备和标注记录表，不覆盖已有内容')
    run = sub.add_parser('run', help='四种策略的真实HTTP任务对比')
    data = run.add_mutually_exclusive_group(required=True)
    data.add_argument('--use-demo', action='store_true', help='明确使用合成事件，仅支持功能/合成负载结论')
    data.add_argument('--events-csv', type=Path, help='已取得许可并人工标注的事件表')
    run.add_argument('--fixed-worker', default='local-1')
    run.add_argument('--rounds', type=int, default=5)
    run.add_argument('--tasks', type=int, default=20)
    run.add_argument('--deadline-ms', type=int, default=5000)
    run.add_argument('--compute-loops', type=int, default=0, help='默认不加入人为负载')
    run.add_argument('--interval-ms', type=int, default=0, help='提交间隔；0为尽快连续提交，不代表真实业务到达过程')
    run.add_argument('--note', default='请补充本次物理设备、网络与背景负载记录')
    failure = sub.add_parser('failure', help='通过专用故障端观察受控进程退出后的租约恢复')
    failure.add_argument('--target', default='office-b-fault')
    failure.add_argument('--fallback', default='local-1')
    for network in (run, failure):
        network.add_argument('--url', default='http://127.0.0.1:8765')
        network.add_argument('--token-file', default='.local/office-field/access_token')
        network.add_argument('--allow-same-host-test', action='store_true', help='仅供工具本机自测；结果不会声称物理多机验证')
        network.add_argument('--output-root', type=Path)
    evaluation = sub.add_parser('evaluate', help='使用人工标签评测现有模型；不会生成真实事件')
    evaluation.add_argument('--events-csv', type=Path, required=True)
    evaluation.add_argument('--output-root', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'run':
        if not 2 <= args.rounds <= 30 or not 1 <= args.tasks <= 200 or not 100 <= args.deadline_ms <= 300000 or not 0 <= args.compute_loops <= 2000000 or not 0 <= args.interval_ms <= 10000:
            parser.error('rounds 2..30；tasks 1..200；deadline 100..300000；loops 0..2000000；interval 0..10000')
    try:
        if args.command == 'init': initialize()
        elif args.command == 'run': run_comparison(args)
        elif args.command == 'evaluate': evaluate(args)
        elif args.command == 'failure': return 0 if failure_test(args)[1] else 2
    except (ValueError, OSError, TimeoutError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
