# Super Admin system monitoring

The browser dashboard is available at `/admin?view=system`. Django authorizes its API with the
server-controlled `superadmin` role; recruiter and applicant sessions receive HTTP 403.

## VM agent

The agent is outbound-only and opens no listening port. Configure the same long random secret in
the Django service and the agent service, then run `scripts/monitoring_agent.py` on the VM.

Required environment:

- `MONITORING_API_URL=https://api.example.com/api`
- `MONITORING_AGENT_SECRET=<long-random-value>`

Optional environment:

- `MONITORING_AGENT_ID=primary-vm`
- `MONITORING_API_SERVICE=talentmatch-api`
- `MONITORING_WORKER_SERVICE=talentmatch-worker`
- `MONITORING_INTERVAL_SECONDS=60`

Use a dedicated unprivileged systemd account. The account only needs permission to read `/proc`,
inspect the two named systemd units, and make HTTPS requests to the Django API. Rotate the agent
secret if it is exposed; it grants telemetry submission only and cannot read monitoring data.

Apply migrations before enabling the agent:

```text
python manage.py migrate
```

Provider tiles deliberately show **Telemetry unavailable** until a provider collector is connected.
Missing credentials never produce a healthy status.
