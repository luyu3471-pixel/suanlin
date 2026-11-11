"""Summarize completed suite raw records; never silently omit failed or late tasks."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics

LABELS={'tiny_control':'短文本对照','text_batch':'批量文本','cpu_heavy':'人工重计算','mixed':'混合任务'}
POLICIES={'fixed':'固定A','round_robin':'轮询','smart':'联合调度','random':'随机'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    summary=json.loads((args.run/'summary.json').read_text(encoding='utf-8'))
    assert summary['complete']
    meta=json.loads((args.run/'metadata.json').read_text(encoding='utf-8'))
    trials=summary['trials']
    groups=defaultdict(list)
    unique=set()
    all_tasks=[]
    output=[]
    for trial in trials:
        path=args.run/f"{trial['case']}-r{trial['round']}-{trial['policy']}-results.json"
        tasks=json.loads(path.read_text(encoding='utf-8'))
        assert len(tasks)==trial['submitted']
        for task in tasks:
            assert task['id'] not in unique
            unique.add(task['id'])
            if trial['policy']=='fixed' and task['status']=='completed':
                assert task['worker_id']=='local-1'
        groups[(trial['case'],trial['policy'])].extend(tasks)
        all_tasks.extend(tasks)
    expected=meta['rounds']*len(meta['cases'])*len(meta['policies'])
    assert len(trials)==expected
    for (case,policy),tasks in groups.items():
        sample=[t for t in trials if t['case']==case and t['policy']==policy]
        done=[t for t in tasks if t['status']=='completed']
        values=sorted(t['latency_ms'] for t in done)
        row={'case':case,'policy':policy,'submitted':len(tasks),'completed':len(done),
             'failed':sum(t['status']=='failed' for t in tasks),
             'cancelled':sum(t['status']=='cancelled' for t in tasks),
             'late_completed':sum(not t.get('deadline_met',False) for t in done),
             'deadline_rate':sum(t.get('deadline_met',False) for t in tasks)/len(tasks),
             'throughput_tasks_s':len(done)/sum(t['server_makespan_s'] for t in sample),
             'mean_makespan_s':statistics.mean(t['server_makespan_s'] for t in sample),
             'mean_latency_ms':statistics.mean(values) if values else None,
             'pooled_p95_ms':values[math.ceil(len(values)*.95)-1] if values else None,
             'workers':dict(Counter(t['worker_id'] for t in done)),
             'mean_compute_ms':statistics.mean(t['result']['compute_ms'] for t in done) if done else None,
             'mean_queue_ms':statistics.mean((t['started_at']-t['created_at'])*1000 for t in done) if done else None,
             'peak_inflight':{node:max(t['nodes'][node]['peak_inflight'] for t in sample) for node in sample[0]['nodes']}}
        output.append(row)
    warm=json.loads((args.run/'warmup-results.json').read_text(encoding='utf-8'))
    report={'formal_tasks':len(all_tasks),'warmup_tasks':len(warm),'status_counts':dict(Counter(t['status'] for t in all_tasks)),
            'deadline_met':sum(t.get('deadline_met',False) for t in all_tasks),'workers':dict(Counter(t.get('worker_id') for t in all_tasks)),
            'attempts':dict(Counter(t['attempts'] for t in all_tasks)),'groups':output}
    (args.run/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['新A/新B角色交换后性能实测（2026-11-11）','',
        '节点槽数：'+str({n['id']:n.get('capacity',1) for n in meta['nodes']}),
        '四类合成负载，每类每策略3轮、每轮64项；8并发提交。固定A使用全部4槽。',
        '短文本/批量文本时限5秒；带人工CPU循环的任务时限30秒。混合任务按各自类型使用时限。',
        '机器和存储配置请结合该次测试的环境记录；本报告不自动推断硬件。',
        '未使用Qwen或GPU；人工循环仅用于CPU调度压力测试。','',
        f"正式任务{len(all_tasks)}项，预热{len(warm)}项；状态{report['status_counts']}；按时{report['deadline_met']}项。",
        f"执行节点分布：{report['workers']}；派发次数分布：{report['attempts']}。",'',
        '负载 | 策略 | 完成/提交 | 按时率 | 吞吐(项/秒) | 每批总耗时均值(秒) | 平均延迟(ms) | 合并P95(ms) | A/B完成数']
    for row in output:
        lines.append(f"{LABELS[row['case']]} | {POLICIES[row['policy']]} | {row['completed']}/{row['submitted']} | {row['deadline_rate']:.1%} | {row['throughput_tasks_s']:.3f} | {row['mean_makespan_s']:.2f} | {row['mean_latency_ms']:.2f} | {row['pooled_p95_ms']:.2f} | {row['workers'].get('local-1',0)}/{row['workers'].get('office-b',0)}")
    lines+=['','联合调度相对固定A：']
    for case in meta['cases']:
        fixed=next(g for g in output if g['case']==case and g['policy']=='fixed')
        smart=next(g for g in output if g['case']==case and g['policy']=='smart')
        lines.append(f"{LABELS[case]}：吞吐变化{(smart['throughput_tasks_s']/fixed['throughput_tasks_s']-1)*100:+.1f}%；P95变化{(smart['pooled_p95_ms']/fixed['pooled_p95_ms']-1)*100:+.1f}%。")
    lines+=['','统计口径：吞吐为三轮完成数/三轮批次总耗时之和；P95合并每组192项完成任务后取最近秩值。',
            '按时率以全部提交任务为分母；平均延迟/P95仅包含完成任务。完成不等于按时完成。',
            '两台机器、并发配置、提交方式、数据库位置均与角色交换前不同，不能直接据此前后比较推断代码优化收益。',
            '本轮未中途更改节点/数据库/调度代码；所有轮次与不利结果均保留。',
            '高等待时间需进一步区分存储、通信和调度开销；不能仅由吞吐数据断言原因。',
            '', '原始证据目录：'+str(args.run)]
    (args.run/'测试报告.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
