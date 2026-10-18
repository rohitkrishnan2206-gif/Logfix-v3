"""Built-in fallback guidance per category. Used when no LLM is configured or the runbook search is empty.
These are general SRE practices, not claims about the specific incident."""
from __future__ import annotations

# (min, max) minutes when there is no history for this category
BASELINE_ETA = {
    "resource_exhaustion": (8, 15), "availability": (10, 25), "auth_failure": (10, 30), "data_store": (15, 45),
    "network": (15, 40), "dependency_failure": (10, 30), "performance_degradation": (20, 60),
    "configuration": (10, 25), "unknown": (20, 60),
}

PLAYBOOKS: dict[str, dict] = {
    "resource_exhaustion": {
        "immediate": [
            ("Confirm the exhausted resource", "Check pool size, memory or disk usage on the affected service against its limit.", "low"),
            ("Find what is holding the resource", "Look for leaked connections, long-running queries, large allocations or runaway processes.", "low"),
            ("Relieve the pressure", "Restart or recycle the unhealthy instances one at a time, or scale out to add capacity.", "medium"),
            ("Throttle the source of load", "Rate-limit or temporarily disable the heaviest caller or batch job.", "medium"),
        ],
        "auto": [("Scale out the affected service by one or two instances", ["Increase replica/instance count by 1-2", "Wait for health checks to pass", "Watch error rate for 5 minutes"], "Scale back to the previous count")],
        "verify": ["Error rate for this pattern returns to baseline", "Resource usage is below 80% of its limit", "No new events for 10 minutes"],
        "rollback": ["Return to the previous instance count or config if errors increase"],
    },
    "availability": {
        "immediate": [
            ("Check health of the failing service", "Look at instance health, restarts and recent crashes.", "low"),
            ("Check the last deployment", "If the failure began right after a release, prepare to roll back.", "low"),
            ("Restart unhealthy instances", "Recycle failing instances one at a time to restore service.", "medium"),
            ("Fail over or route around", "Shift traffic to healthy instances or a standby if available.", "medium"),
        ],
        "auto": [("Restart unhealthy instances", ["Identify instances failing health checks", "Restart them one at a time", "Confirm each passes health checks before the next"], "Stop restarting and fail over to standby")],
        "verify": ["Health checks pass on all instances", "5xx rate returns to baseline"],
        "rollback": ["Redeploy the previous known-good version"],
    },
    "auth_failure": {
        "immediate": [
            ("Check credential and token expiry", "Verify the failing credential, key or certificate has not expired or been rotated.", "low"),
            ("Check recent permission changes", "Review IAM/role/policy changes made shortly before the first error.", "low"),
            ("Restore access", "Rotate or re-issue the credential, or revert the permission change.", "medium"),
        ],
        "auto": [("Re-issue the expired credential", ["Create a new credential in the secret manager", "Roll it out to the service", "Confirm authentication succeeds"], "Restore the previous credential if still valid")],
        "verify": ["401/403 errors stop", "Dependent calls succeed"],
        "rollback": ["Revert the permission or credential change"],
    },
    "data_store": {
        "immediate": [
            ("Check database health", "Look at connections, locks, replication lag and disk space.", "low"),
            ("Find blocking queries", "Identify long-running transactions or deadlocks and the code path that causes them.", "low"),
            ("Relieve locks safely", "Terminate the blocking session only after confirming it is safe to do so.", "high"),
            ("Protect the data", "Confirm recent backups exist before any corrective write.", "low"),
        ],
        "auto": [],
        "verify": ["Write errors stop", "Replication lag is back to normal"],
        "rollback": ["Restore from backup only as a last resort and with approval"],
    },
    "network": {
        "immediate": [
            ("Check DNS and TLS", "Verify DNS resolution and certificate validity for the failing endpoint.", "low"),
            ("Check network rules", "Review security groups, firewall rules and routes changed recently.", "low"),
            ("Retry through an alternate path", "Use another zone, endpoint or region if available.", "medium"),
        ],
        "auto": [],
        "verify": ["Connections succeed", "Handshake errors stop"],
        "rollback": ["Revert the last network or certificate change"],
    },
    "dependency_failure": {
        "immediate": [
            ("Check the upstream dependency", "Look at the status of the service this one calls and its error rate.", "low"),
            ("Enable fallbacks", "Turn on circuit breakers, cached responses or degraded mode if available.", "medium"),
            ("Tune timeouts and retries", "Make sure retries have back-off so they do not amplify the outage.", "medium"),
            ("Contact the owning team", "Page the owner of the upstream dependency with the evidence.", "low"),
        ],
        "auto": [("Open the circuit breaker for the failing dependency", ["Enable the breaker or fallback flag", "Confirm requests return the degraded response", "Close it once the dependency recovers"], "Disable the flag")],
        "verify": ["Timeouts stop", "Success rate on the dependency call recovers"],
        "rollback": ["Disable the fallback flag"],
    },
    "performance_degradation": {
        "immediate": [
            ("Find the slow component", "Compare latency per service and per endpoint to find where the time goes.", "low"),
            ("Check for resource pressure", "Look at CPU, memory, GC and connection pool metrics.", "low"),
            ("Reduce load", "Scale out or shed non-critical traffic while you investigate.", "medium"),
        ],
        "auto": [],
        "verify": ["p95 latency returns to baseline"],
        "rollback": ["Revert the last deployment or config change if latency started after it"],
    },
    "configuration": {
        "immediate": [
            ("Identify the bad setting", "Compare current configuration against the last known-good version.", "low"),
            ("Revert the change", "Restore the previous configuration and redeploy.", "medium"),
        ],
        "auto": [],
        "verify": ["Service starts and passes health checks"],
        "rollback": ["Reapply the previous configuration"],
    },
    "unknown": {
        "immediate": [
            ("Gather more evidence", "Read the sample logs and check what changed shortly before the first error.", "low"),
            ("Check recent deployments", "A release near the start time is the most common cause.", "low"),
            ("Escalate to the service owner", "Share the incident link and evidence with the owning team.", "low"),
        ],
        "auto": [],
        "verify": ["Error pattern stops appearing"],
        "rollback": ["Roll back the most recent change to the affected service"],
    },
}
