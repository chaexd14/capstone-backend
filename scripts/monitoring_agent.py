"""Outbound-only TalentMatch VM metrics agent.

Run under systemd with MONITORING_API_URL and MONITORING_AGENT_SECRET set.
The process reports allowlisted host metrics once per minute and exposes no port.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request


def _cpu_sample():
    def read():
        with open('/proc/stat', encoding='ascii') as source:
            values = [int(value) for value in source.readline().split()[1:]]
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)

    total_a, idle_a = read()
    time.sleep(0.2)
    total_b, idle_b = read()
    elapsed = max(1, total_b - total_a)
    return round(100 * (1 - (idle_b - idle_a) / elapsed), 1)


def _memory_percent():
    values = {}
    with open('/proc/meminfo', encoding='ascii') as source:
        for line in source:
            key, value = line.split(':', 1)
            values[key] = int(value.strip().split()[0])
    return round(100 * (1 - values['MemAvailable'] / values['MemTotal']), 1)


def _uptime_seconds():
    with open('/proc/uptime', encoding='ascii') as source:
        return int(float(source.read().split()[0]))


def _service_up(name):
    result = subprocess.run(
        ['systemctl', 'is-active', '--quiet', name], check=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=5,
    )
    return result.returncode == 0


def report():
    api_url = os.environ['MONITORING_API_URL'].rstrip('/') + '/platform/monitoring/agent/report/'
    secret = os.environ['MONITORING_AGENT_SECRET']
    disk = shutil.disk_usage('/')
    payload = json.dumps({
        'agent_id': os.getenv('MONITORING_AGENT_ID', 'primary-vm'),
        'cpu_percent': _cpu_sample(),
        'memory_percent': _memory_percent(),
        'disk_percent': round(100 * disk.used / disk.total, 1),
        'uptime_seconds': _uptime_seconds(),
        'api_service_up': _service_up(os.getenv('MONITORING_API_SERVICE', 'talentmatch-api')),
        'worker_service_up': _service_up(os.getenv('MONITORING_WORKER_SERVICE', 'talentmatch-worker')),
    }).encode('utf-8')
    request = urllib.request.Request(
        api_url, data=payload, method='POST',
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Monitoring-Agent-Key': secret,
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 202:
            raise RuntimeError(f'Monitoring endpoint returned HTTP {response.status}')


def main():
    interval = max(30, int(os.getenv('MONITORING_INTERVAL_SECONDS', '60')))
    while True:
        try:
            report()
        except Exception as exc:
            print(f'monitoring_agent error={type(exc).__name__}', flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
