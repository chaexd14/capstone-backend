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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='Read-only endpoint, such as https://api.example/api/ready/')
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=10)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--max-error-rate', type=float, default=0.01)
    parser.add_argument('--max-p95-ms', type=float, default=750)
    args = parser.parse_args()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(request_once, args.url, args.timeout) for _ in range(args.requests)]
        results = [future.result() for future in as_completed(futures)]

    latencies = [latency for _, latency in results]
    failures = sum(1 for status, _ in results if not 200 <= status < 400)
    error_rate = failures / len(results)
    p95 = percentile(latencies, 0.95)
    print(
        f'requests={len(results)} failures={failures} error_rate={error_rate:.3%} '
        f'p50_ms={statistics.median(latencies):.1f} p95_ms={p95:.1f}'
    )
    if error_rate > args.max_error_rate or p95 > args.max_p95_ms:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
