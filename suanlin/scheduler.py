"""Shared policy selection. Deadlines are server-clock absolute timestamps."""
import random
import time
from .business import features

POLICIES = {"ai": "Qwen AI 调度", "smart": "联合调度", "fixed": "固定单节点", "round_robin": "轮询分配", "random": "随机分配"}

def cost_key(task, node):
    spec = task['payload'].get('workload')
    return (spec['kind'] if spec else 'text') + ':' + str(node.get('capacity',1))

def cost_profile(task, node):
    cost = service_ms(task,node)
    stats = node.get('cost_stats',{}).get(cost_key(task,node),{})
    deviation = stats.get('relative_error',.35)
    return {'service_ms':round(cost,2),'conservative_ms':round(cost*(1+2*deviation),2),
            'samples':stats.get('samples',0),'capacity':node.get('capacity',1)}

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
    if payload.get('workload'):
        spec=payload['workload']
        defaults={'log_analysis':.02,'document_search':.4,'archive':.08}
        compute=node.get('business_ms_per_unit',{}).get(spec['kind'], defaults[spec['kind']])*spec['units']
    resources = node.get('resources', {})
    if time.time() - (node.get('resources_at') or 0) < 15:
        cpu = resources.get('cpu_percent', 0)
        total, available = resources.get('memory_total_gib', 0), resources.get('memory_available_gib', 0)
        pressure = max(0, cpu - 85) / 100
        if total and available / total < .1: pressure += .5
        compute *= 1 + pressure
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


def simulate_plan(tasks, nodes, now, assignments=None):
    """Replay a whole proposal on independent slot timelines, without live mutation."""
    copies = [dict(n, _slots=list(n['_slots'])) for n in nodes]
    selected, finishes, tardiness = {}, [], []
    unscheduled = 0
    for task in sorted(tasks, key=lambda t: (t['deadline_at'], t['created_at'])):
        if assignments is None:
            node = choose(task, copies, now, 'smart')
        else:
            node = next((n for n in copies if n['id'] == assignments.get(task['id']) and eligible(task, n)), None)
        if node is None:
            unscheduled += 1
            continue
        finish = estimate_ms(task, node, now)
        selected[task['id']] = node['id']
        finishes.append(finish)
        tardiness.append(max(0, finish - (task['deadline_at'] - now) * 1000))
        reserve(task, node, now)
    return selected, {'unscheduled': unscheduled, 'deadline_misses': sum(t > 0 for t in tardiness),
                      'tardiness_ms': sum(tardiness), 'makespan_ms': max(finishes, default=0),
                      'total_finish_ms': sum(finishes)}


def plan_is_worse(proposed, baseline):
    # Prefer deadline feasibility; reject meaningful throughput/latency regressions.
    for key in ('unscheduled', 'deadline_misses'):
        if proposed[key] != baseline[key]:
            return proposed[key] > baseline[key]
    for key, floor, fraction in (('tardiness_ms', 50, .03), ('makespan_ms', 50, .03), ('total_finish_ms', 100, .05)):
        if proposed[key] > baseline[key] + max(floor, baseline[key] * fraction):
            return True
    return False


def optimize_plan(tasks, nodes, now):
    """Bounded whole-batch improvement of greedy placement; never touch live slots."""
    assignments, score = simulate_plan(tasks, nodes, now)
    def rank(value):
        return (value['unscheduled'], value['deadline_misses'], value['tardiness_ms'],
                value['makespan_ms'] + .1 * value['total_finish_ms'] / max(1, len(tasks)))
    # Small batches only: two sweeps, at most four candidate moves per task.
    for _ in range(2):
        improved = False
        for task in tasks:
            options = sorted((n for n in nodes if eligible(task,n)), key=lambda n:service_ms(task,n))[:4]
            for node in options:
                if assignments.get(task['id']) == node['id']: continue
                proposal = dict(assignments, **{task['id']: node['id']})
                _, candidate_score = simulate_plan(tasks, nodes, now, proposal)
                if rank(candidate_score) < rank(score) and candidate_score['total_finish_ms'] <= score['total_finish_ms'] * 1.05 + 100:
                    assignments, score, improved = proposal, candidate_score, True
        if not improved: break
    return assignments, score

def eligible(task, node):
    if node.get('draining') or not node.get('online',True) or (task.get('privacy')=='local_only' and not node.get('trusted',False)):
        return False
    spec=task.get('payload',{}).get('workload')
    if spec:
        if spec['kind'] not in node.get('workload_kinds',[]): return False
        available=node.get('resources',{}).get('memory_available_gib')
        if available is not None and features(spec)['estimated_memory_mib']*max(1,node.get('capacity',1)) > max(0,available-1)*1024:
            return False
    return True


def choose(task, nodes, now, policy="smart", turn=0, rng=None):
    if policy == 'ai':
        raise ValueError('AI policy requires coordinator model decisions; use smart for an explicit fallback')
    eligible_nodes = [n for n in nodes if eligible(task,n)]
    if not eligible_nodes:
        return None
    eligible_nodes.sort(key=lambda n: n["id"])
    if policy == "fixed":
        requested = task.get("fixed_worker_id")
        return next((n for n in eligible_nodes if n["id"] == requested), None) if requested else eligible_nodes[0]
    if policy == "round_robin":
        return eligible_nodes[turn % len(eligible_nodes)]
    if policy == "random":
        return (rng or random).choice(eligible_nodes)
    deadline_ms = max(1, (task["deadline_at"] - now) * 1000)
    def cost(n):
        finish = estimate_ms(task, n, now)
        return finish + 2 * max(0, finish - deadline_ms)
    return min(eligible_nodes, key=lambda n: (cost(n), n["id"]))
