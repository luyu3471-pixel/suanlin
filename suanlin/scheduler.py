"""Shared policy selection. Deadlines are server-clock absolute timestamps."""
import random

POLICIES = {"smart": "联合调度", "fixed": "固定单节点", "round_robin": "轮询分配", "random": "随机分配"}

def text_units(payload):
    return max(1.0, sum(len(x) for x in payload['events']) / 32)


def service_ms(task, node):
    payload = task["payload"]
    compute = (node.get('text_ms_per_unit', .2) * text_units(payload)
               + node.get('hash_ms_per_unit', 25) * payload.get('compute_loops', 0) / 20000)
    if 'capacity' not in node and 'text_ms_per_unit' not in node:
        # Preserve the original interface for external simulation profiles.
        units = max(1, len(payload['events'])) + payload.get('compute_loops', 0) / 20000
        compute = max(1.0, node.get('ms_per_unit', 20) * units)
    transfer = task.get("payload_bytes", 0) * 8 / max(0.1, node.get("bandwidth_mbps", 100)) / 1000
    overhead = max(node.get('overhead_ms', 0), node.get('rtt_ms', 1))
    return max(1.0, compute * 1.15 + overhead + transfer)


def estimate_ms(task, node, now):
    ready = min(node['_slots']) if node.get('_slots') else node.get('busy_until', now)
    return max(0.0, ready - now) * 1000 + service_ms(task, node)


def reserve(task, node, now):
    """Reserve a virtual slot for each earlier queued task, without leasing it."""
    index = min(range(len(node['_slots'])), key=node['_slots'].__getitem__)
    node['_slots'][index] = max(now, node['_slots'][index]) + service_ms(task, node) / 1000

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
