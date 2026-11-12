"""Shared executable queue and independent plan replay. All times are server times."""
from .scheduler import eligible, service_ms


def queue_key(task):
    decision = task.get('scheduler_decision') or {}
    if decision.get('plan_revision') is not None and decision.get('worker_id'):
        return (0, decision['plan_revision'], decision.get('order', 0), task['id'])
    return (1, task['deadline_at'], task['created_at'], task['id'])


def normalize(assignments, tasks):
    """Internal compatibility for callers supplying legacy task->node mappings."""
    return {t['id']: dict(value) if isinstance(value, dict) else
            {'worker_id': value, 'order': i}
            for i, t in enumerate(tasks)
            if (value := assignments.get(t['id'])) is not None}


def replay(tasks, nodes, now, assignments):
    slots = {n['id']: list(n['_slots']) for n in nodes}
    by_id = {n['id']: n for n in nodes}
    assignments = normalize(assignments, tasks)
    timeline, violations = {}, []
    ordered = sorted(tasks, key=lambda t: (assignments.get(t['id'], {}).get('order', 10**9), t['created_at'], t['id']))
    for task in ordered:
        choice = assignments.get(task['id'], {})
        node = by_id.get(choice.get('worker_id'))
        if not node or not eligible(task, node) or not slots[node['id']]:
            violations.append({'task_id': task['id'], 'type': 'ineligible_node'})
            continue
        lane = min(range(len(slots[node['id']])), key=slots[node['id']].__getitem__)
        start = max(now, slots[node['id']][lane])
        finish = start + service_ms(task, node) / 1000
        slots[node['id']][lane] = finish
        timeline[task['id']] = {'worker_id': node['id'], 'start_at': start, 'finish_at': finish}
        if finish > task['deadline_at']:
            violations.append({'task_id': task['id'], 'type': 'deadline_miss',
                               'predicted_lateness_ms': round((finish-task['deadline_at'])*1000, 2)})
    return timeline, violations


def repair(tasks, nodes, now, assignments):
    """Change only conflicting assignments when a feasible improvement exists."""
    choices = normalize(assignments, tasks)
    by_id = {t['id']: t for t in tasks}
    def score(timeline, violations):
        return (sum(v['type']=='ineligible_node' for v in violations),
                sum(v['type']=='deadline_miss' for v in violations),
                sum(v.get('predicted_lateness_ms', 0) for v in violations))
    timeline, violations = replay(tasks, nodes, now, choices)
    for problem in list(violations):
        task = by_id[problem['task_id']]
        best_score = score(timeline, violations)
        for node in nodes:
            if not eligible(task, node):
                continue
            trial = dict(choices)
            trial[task['id']] = dict(choices.get(task['id'], {'order': len(choices)}), worker_id=node['id'])
            candidate, issues = replay(tasks, nodes, now, trial)
            if score(candidate, issues) < best_score:
                choices, timeline, violations, best_score = trial, candidate, issues, score(candidate, issues)
    return choices, timeline, violations
