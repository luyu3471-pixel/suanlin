"""Reproducible discrete-event simulation, NOT LAN or 6G measurements."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
from suanlin.scheduler import choose, POLICIES

PROFILES = {
    "normal": [("a-edge", 32, 3, 80, True), ("b-laptop", 65, 9, 40, True), ("c-cloud", 12, 80, 25, False)],
    "congested": [("a-edge", 32, 15, 8, True), ("b-laptop", 65, 25, 4, True), ("c-cloud", 12, 180, 1, False)],
    "burst": [("a-edge", 32, 3, 80, True), ("b-laptop", 65, 9, 40, True), ("c-cloud", 12, 80, 25, False)],
}

def workload(seed, scenario, count):
    rng = random.Random(seed)
    arrival = 0
    rows = []
    for i in range(count):
        arrival += rng.expovariate(1 / (.06 if scenario == "burst" else .14))
        units = rng.choice([1, 2, 3, 5, 8])
        rows.append({"id": i, "arrival": arrival, "units": units, "deadline_ms": rng.choice([250, 500, 1000, 2000]),
                     "payload_bytes": rng.choice([4096, 32768, 262144]), "privacy": "local_only" if rng.random() < .25 else "any",
                     # Shared per-job noise for every policy/node, not fresh draws per policy.
                     "noise": rng.uniform(.85, 1.2)})
    return rows

def simulate(rows, profile, policy, seed):
    nodes = [{"id": i, "ms_per_unit": c, "rtt_ms": r, "bandwidth_mbps": b, "trusted": t, "busy_until": 0, "online": True} for i, c, r, b, t in profile]
    rng = random.Random(seed + 90000)
    records = []
    for turn, job in enumerate(rows):
        task = {"payload": {"events": ["x"] * job["units"], "compute_loops": 0}, "payload_bytes": job["payload_bytes"], "privacy": job["privacy"], "deadline_at": job["arrival"] + job["deadline_ms"] / 1000}
        node = choose(task, nodes, job["arrival"], policy, turn, rng)
        transfer_ms = job["payload_bytes"] * 8 / node["bandwidth_mbps"] / 1000
        # Per-node FIFO, communication precedes compute. The node serializes the
        # application requests; no idealized parallel transfer is assumed.
        start = max(job["arrival"], node["busy_until"])
        compute_ms = node["ms_per_unit"] * job["units"] * job["noise"]
        finish = start + (node["rtt_ms"] + transfer_ms + compute_ms) / 1000
        node["busy_until"] = finish
        latency = (finish - job["arrival"]) * 1000
        records.append(dict(job, policy=policy, worker_id=node["id"], latency_ms=round(latency, 5), deadline_met=latency <= job["deadline_ms"],
                            transfer_ms=round(transfer_ms, 5), compute_ms=round(compute_ms, 5)))
    latencies = sorted(x["latency_ms"] for x in records)
    return {"deadline_rate": sum(x["deadline_met"] for x in records) / len(records), "mean_ms": statistics.mean(latencies),
            "p95_ms": latencies[math.ceil(len(latencies) * .95) - 1], "payload_bytes": sum(x["payload_bytes"] for x in records)}, records

def run(output, seeds=30, count=240):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    groups = []
    all_records = []
    trial_records = []
    for scenario, profile in PROFILES.items():
        measurements = {p: [] for p in POLICIES}
        hashes = []
        for seed in range(seeds):
            rows = workload(2030 + seed, scenario, count)
            hashes.append(hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
            for policy in POLICIES:
                metrics, records = simulate(rows, profile, policy, seed)
                measurements[policy].append(metrics)
                trial_records.append(dict(scenario=scenario, seed=2030 + seed, policy=policy, **metrics))
                all_records.extend(dict(scenario=scenario, seed=2030 + seed, **r) for r in records)
        for policy, results in measurements.items():
            group = {"scenario": scenario, "policy": policy, "label": POLICIES[policy], "seeds": seeds, "tasks_per_seed": count}
            for field in ("deadline_rate", "mean_ms", "p95_ms", "payload_bytes"):
                values = [x[field] for x in results]
                mean = statistics.mean(values)
                # Approximate 95% CI of seed-level averages, not per-job pseudo-replication.
                margin = 1.96 * statistics.stdev(values) / math.sqrt(seeds) if seeds > 1 else 0
                group[field] = {"mean": round(mean, 6), "ci95": [round(mean - margin, 6), round(mean + margin, 6)]}
            groups.append(group)
    summary = {"available": True, "evidence_type": "discrete_event_simulation", "description": "离散事件仿真；所有网络与计算配置均为假设，不是实测或 6G 性能", "base_seed": 2030,
               "seeds": seeds, "tasks_per_seed": count, "python": platform.python_version(), "groups": groups, "profiles": PROFILES,
               "workload_hashes": {scenario: [hashlib.sha256(json.dumps(workload(2030 + i, scenario, count), sort_keys=True).encode()).hexdigest() for i in range(seeds)] for scenario in PROFILES},
               "ci_method": "seed-level mean +/- 1.96*sample_std/sqrt(n), approximate", "p95_method": "nearest-rank per seed, then mean over seeds",
               "assumptions": ["独立单任务，节点串行 FIFO，不拆分模型", "可信范围是管理员配置，未提供远程可信执行证明", "三个假设节点，无故障丢包与实测无线信道", "所有策略使用相同任务、隐私约束和作业级噪声", "仿真调度时准确获知队列与名义服务时间，收益可能高于实际", "原型仅发送文本，仿真大载荷用于敏感性分析"],
               "total_simulated_tasks": len(all_records)}
    (output / "simulation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for filename, records in (("simulation_raw.csv", all_records), ("simulation_trials.csv", trial_records)):
        with (output / filename).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, records[0].keys())
            writer.writeheader()
            writer.writerows(records)
    print(f"Simulation complete: {len(all_records)} records, {len(groups)} groups. NOT physical-network measurements.")
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="算邻调度对比仿真")
    parser.add_argument("--output", default="results")
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--tasks", type=int, default=240)
    args = parser.parse_args()
    if not 2 <= args.seeds <= 500 or not 10 <= args.tasks <= 2000:
        parser.error("seeds 应在 2 到 500，tasks 应在 10 到 2000")
    run(args.output, args.seeds, args.tasks)
