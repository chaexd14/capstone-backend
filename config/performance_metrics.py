import math
import threading
import time
from collections import defaultdict, deque


_WINDOW_SECONDS = 600
_MAX_SAMPLES = 5000
_samples = deque(maxlen=_MAX_SAMPLES)
_lock = threading.Lock()


def record_api_request(*, path, status, duration_ms, db_query_count, db_duration_ms):
    sample = {
        'at': time.monotonic(), 'path': path, 'status': status,
        'duration_ms': duration_ms, 'db_query_count': db_query_count,
        'db_duration_ms': db_duration_ms,
    }
    with _lock:
        _samples.append(sample)


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 1)


def api_performance_snapshot():
    cutoff = time.monotonic() - _WINDOW_SECONDS
    with _lock:
        recent = [sample.copy() for sample in _samples if sample['at'] >= cutoff]
    grouped = defaultdict(list)
    for sample in recent:
        grouped[sample['path']].append(sample)
    routes = []
    for path, samples in grouped.items():
        durations = [sample['duration_ms'] for sample in samples]
        routes.append({
            'path': path,
            'requests': len(samples),
            'error_rate': round(sum(sample['status'] >= 500 for sample in samples) / len(samples), 4),
            'p50_ms': _percentile(durations, 0.50),
            'p95_ms': _percentile(durations, 0.95),
            'p99_ms': _percentile(durations, 0.99),
            'average_db_queries': round(sum(sample['db_query_count'] for sample in samples) / len(samples), 1),
            'average_db_ms': round(sum(sample['db_duration_ms'] for sample in samples) / len(samples), 1),
        })
    return {
        'window_seconds': _WINDOW_SECONDS,
        'sample_count': len(recent),
        'routes': sorted(routes, key=lambda route: (-route['requests'], route['path']))[:20],
        'scope': 'current_api_process',
    }
