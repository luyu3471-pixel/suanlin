"""Shared policy selection. Deadlines are server-clock absolute timestamps."""
import random

POLICIES = {"smart": "联合调度", "fixed": "固定单节点", "round_robin": "轮询分配", "random": "随机分配"}

def estimate_ms(task, node, now):
    payload = task["payload"]
    units = max(1, len(payload["events"])) + payload.get("compute_loops", 0) / 20000
    compute = max(1.0, node.get("ms_per_unit", 20.0) * units)
    remaining = max(0.0, node.get("busy_until", now) - now) * 1000
    transfer = task.get("payload_bytes", 0) * 8 / max(0.1, node.get("bandwidth_mbps", 100)) / 1000
    return remaining + node.get("rtt_ms", 1) + transfer + compute * 1.15

def choose(task, nodes, now, policy="smart", turn=0, rng=None):
    eligible = [n for n in nodes if not n.get("draining") and n.get("online", True) and (task.get("privacy") != "local_only" or n.get("trusted", False))]
    if not eligible:
        return None
    eligible.sort(key=lambda n: n["id"])
    if policy == "fixed":
        requested = task.get("fixed_worker_id")
        return next((n for n in eligible if n["id"] == requested), None) if requested else eligible[0]
    if policy == "round_robin":
        return eligible[turn % len(eligible)]
    if policy == "random":
        return (rng or random).choice(eligible)
    deadline_ms = max(1, (task["deadline_at"] - now) * 1000)
    def cost(n):
        finish = estimate_ms(task, n, now)
        return finish + 2 * max(0, finish - deadline_ms)
    return min(eligible, key=lambda n: (cost(n), n["id"]))
