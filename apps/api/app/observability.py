"""Bounded process metrics. No raw questions, tokens, database URLs or student records."""
from collections import Counter
from threading import Lock

_lock = Lock()
_counts = Counter()
_buckets = (0.01, 0.05, 0.1, 0.3, 0.5, 1, 3, 10, float('inf'))
_histogram = Counter()
_sum = 0.0


def increment(name, amount=1):
    with _lock:
        _counts[name] += amount


def observe(seconds):
    global _sum
    with _lock:
        _sum += seconds
        for bucket in _buckets:
            if seconds <= bucket:
                _histogram[bucket] += 1


def snapshot():
    with _lock:
        return {'counters': dict(_counts), 'latency_buckets_seconds': {
            str(key): value for key, value in _histogram.items()}, 'latency_sum_seconds': _sum,
            'scope': 'single-process', 'ai_enabled': bool(_counts['query_llm_calls']), 'llm_calls': _counts['query_llm_calls'],
            'ai_scope': 'student-query', 'design_llm_calls': _counts['design_llm_calls']}


def prometheus():
    with _lock:
        lines = ['# TYPE campus_events_total counter']
        for name, value in sorted(_counts.items()):
            lines.append(f'campus_events_total{{event="{name}"}} {value}')
        lines.append('# TYPE campus_request_seconds histogram')
        for bucket in _buckets:
            label = '+Inf' if bucket == float('inf') else str(bucket)
            lines.append(f'campus_request_seconds_bucket{{le="{label}"}} {_histogram[bucket]}')
        lines += [f'campus_request_seconds_count {_histogram[float("inf")]}',
                  f'campus_request_seconds_sum {_sum}']
        return '\n'.join(lines) + '\n'
