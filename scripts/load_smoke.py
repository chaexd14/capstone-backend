"""Read-only API load smoke test for a production-equivalent staging environment."""

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def request_once(url, timeout):
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, headers={'Accept': 'application/json'})
        return response.status_code, (time.perf_counter() - started) * 1000
    except requests.RequestException:
        return 0, (time.perf_counter() - started) * 1000


def percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return ordered[index]


def run_stage(url, *, requests_count, concurrency, timeout):
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(request_once, url, timeout) for _ in range(requests_count)]
        results = [future.result() for future in as_completed(futures)]
    latencies = [latency for _, latency in results]
    failures = sum(1 for status, _ in results if not 200 <= status < 400)
    return {
        'requests': len(results), 'failures': failures,
        'error_rate': failures / len(results),
        'p50': statistics.median(latencies), 'p95': percentile(latencies, 0.95),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='Read-only endpoint, such as https://api.example/api/ready/')
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=10)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--max-error-rate', type=float, default=0.01)
    parser.add_argument('--max-p95-ms', type=float, default=750)
    parser.add_argument(
        '--stages', default='',
        help='Comma-separated concurrency ramp, for example 5,10,25. Requests are split across stages.',
    )
    parser.add_argument(
        '--stop-p95-ms', type=float, default=2000,
        help='In staged mode, stop after two consecutive stages exceed this p95.',
    )
    args = parser.parse_args()

    stages = [args.concurrency]
    if args.stages:
        stages = [int(value.strip()) for value in args.stages.split(',') if value.strip()]
        if not stages or any(value <= 0 for value in stages):
            parser.error('--stages must contain positive integers')

    remaining = args.requests
    consecutive_slow_stages = 0
    failed = False
    for index, concurrency in enumerate(stages):
        stage_count = remaining if index == len(stages) - 1 else max(1, args.requests // len(stages))
        remaining -= stage_count
        result = run_stage(
            args.url, requests_count=stage_count,
            concurrency=concurrency, timeout=args.timeout,
        )
        print(
            f'stage={index + 1} concurrency={concurrency} requests={result["requests"]} '
            f'failures={result["failures"]} error_rate={result["error_rate"]:.3%} '
            f'p50_ms={result["p50"]:.1f} p95_ms={result["p95"]:.1f}'
        )
        if result['error_rate'] > args.max_error_rate:
            print('Stopping: stage error rate exceeded the safety threshold.')
            failed = True
            break
        consecutive_slow_stages = consecutive_slow_stages + 1 if result['p95'] > args.stop_p95_ms else 0
        if consecutive_slow_stages >= 2:
            print('Stopping: p95 exceeded the safety threshold in two consecutive stages.')
            failed = True
            break
        failed = failed or result['p95'] > args.max_p95_ms

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
