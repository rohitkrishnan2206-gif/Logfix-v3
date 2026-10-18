"""Starter runbooks so retrieval works on day one. Replace/extend from the Knowledge page."""
from __future__ import annotations

RUNBOOKS: list[tuple[str, str]] = [
    ("Runbook: Database connection pool exhausted", """# Database connection pool exhausted
## Symptoms
Errors like "Connection pool exhausted", "timeout waiting for connection", "too many connections". Requests time out (504) and upstream services cascade.
## Likely causes
- A recent deploy lowered the pool size (for example db.pool.max) or raised concurrency
- Connection leak: connections not returned after errors
- Slow queries or locks holding connections longer than normal
## Remediation
- Check the last deployment and roll back if the pool or config changed
- Raise the pool size temporarily and confirm the database max_connections allows it
- Find leaked or long-running connections in the database (pg_stat_activity or SHOW PROCESSLIST) and fix the code path that skips close()
- Add a connection acquisition timeout alert so this is caught before a cascade
"""),
    ("Runbook: Out of memory / OOMKilled", """# Out of memory (OOM) and restart loops
## Symptoms
"java.lang.OutOfMemoryError", "OOMKilled", exit code 137, rising heap usage, long GC pauses, container restarts.
## Likely causes
- Memory leak (caches, listeners or collections that only grow)
- Memory limit set lower than the real working set
- Traffic or payload size spike
## Remediation
- Roll back to the last known good version if the leak started after a deploy
- Raise the memory limit as a short-term mitigation and restart the service
- Capture a heap dump before restart and look for the largest retained objects
- Add a memory usage alert at 80 percent and cap cache sizes
"""),
    ("Runbook: Disk full / no space left on device", """# Disk full
## Symptoms
"No space left on device", ENOSPC, failed writes, database WAL or log write errors, disk usage above 90 percent.
## Likely causes
- Log files or core dumps growing without rotation
- Database WAL or temp files accumulating
- A backup or batch job filling the volume
## Remediation
- Find the largest directories with du -xh --max-depth=2 / and remove or compress old logs
- Enable log rotation with size limits and retention
- Expand the volume if growth is legitimate, then add a disk usage alert at 80 percent
- For databases, check for stuck replication slots holding WAL
"""),
    ("Runbook: High CPU and latency", """# High CPU and slow responses
## Symptoms
Rising latency, timeouts, CPU above 85 percent, queue build-up.
## Likely causes
- Traffic spike or retry storm
- Inefficient code path or a missing database index after a release
- Noisy neighbour or undersized instance
## Remediation
- Compare the start of the problem with deploys and traffic changes
- Scale out or up as a short-term mitigation
- Profile the hot endpoint and check slow query logs
- Add rate limiting and exponential backoff for retries
"""),
    ("Runbook: Database replication lag", """# Database replication lag
## Symptoms
Replica behind primary, stale reads, "replication lag" warnings.
## Likely causes
- Long-running transactions or large batch writes on the primary
- Replica under-provisioned or saturated on I/O
- Network issues between primary and replica
## Remediation
- Check replica CPU and I/O and the longest running transactions on the primary
- Pause or throttle batch jobs
- Route read traffic back to the primary until lag recovers
"""),
    ("Runbook: API throttling and rate limits", """# Throttling (429 / ThrottlingException)
## Symptoms
HTTP 429, "ThrottlingException", "Rate exceeded", "TooManyRequests".
## Likely causes
- Request volume above the service quota
- Retry storm without backoff
## Remediation
- Add exponential backoff with jitter to clients
- Request a quota increase or spread requests over time
- Cache results of repeated identical calls
"""),
    ("Runbook: Authentication and token failures", """# Authentication failures and timeouts
## Symptoms
401/403 spikes, "token expired", "invalid signature", identity provider timeouts.
## Likely causes
- Expired or rotated credentials or signing keys
- Clock skew between services
- Identity provider outage or slow response
## Remediation
- Check credential expiry and recent rotations
- Verify NTP time sync on affected hosts
- Check identity provider status and add a short-lived token cache with graceful failure
"""),
    ("Runbook: Deployment rollback", """# Rolling back a bad deployment
## When to use
Errors start within minutes of a deploy and the affected service matches the deployed one.
## Steps
- Confirm the deploy time against the first error time (error timeline)
- Roll back to the previous version using your deployment tool
- Verify the error rate falls within 5 to 10 minutes
- Open a follow-up to find the root cause before redeploying
"""),
]


def seed_kb(kb) -> int:
    if kb.list_docs():
        return 0
    for title, content in RUNBOOKS:
        kb.add_doc(title, content, source="seed")
    return len(RUNBOOKS)
