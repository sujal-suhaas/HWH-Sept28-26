#!/usr/bin/env python
"""Generate the fictional NimbusPay incident dataset.

Deterministic: same ``--seed`` produces byte-identical output.

The dataset is deliberately *not* a repeating answer key. Each recurring pattern
gets harder to guess from the alert text and easier to diagnose only because
later incidents in that pattern carry a confirmed root cause and a validated
runbook - which is exactly the signal Hindsight recall is meant to surface.

Hidden evaluation labels live in a separate file so the agent can never read
them by accident.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

COMPANY = "NimbusPay"
DATA_START = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)  # 8 weeks before the demo
WEEKS = 8

# --------------------------------------------------------------------------
# Service catalog
# --------------------------------------------------------------------------
SERVICES: list[dict[str, Any]] = [
    {
        "name": "checkout-api",
        "tier": 1,
        "owner": "Checkout",
        "dependencies": ["payments-ledger", "fraud-scorer", "redis-cache"],
        "slo": "p99 < 800ms",
    },
    {
        "name": "payments-ledger",
        "tier": 1,
        "owner": "Money Movement",
        "dependencies": ["kafka-bus", "redis-cache"],
        "slo": "p99 < 500ms",
    },
    {
        "name": "auth-service",
        "tier": 1,
        "owner": "Identity",
        "dependencies": ["redis-cache"],
        "slo": "availability > 99.95%",
    },
    {
        "name": "fraud-scorer",
        "tier": 2,
        "owner": "Risk",
        "dependencies": ["kafka-bus"],
        "slo": "p99 < 250ms",
    },
    {
        "name": "kafka-bus",
        "tier": 1,
        "owner": "Platform",
        "dependencies": [],
        "slo": "consumer lag < 5k",
    },
    {
        "name": "redis-cache",
        "tier": 1,
        "owner": "Platform",
        "dependencies": [],
        "slo": "availability > 99.99%",
    },
    {
        "name": "webhook-dispatcher",
        "tier": 2,
        "owner": "Merchant Integrations",
        "dependencies": ["payments-ledger", "kafka-bus"],
        "slo": "delivery success > 99%",
    },
]

# --------------------------------------------------------------------------
# Root causes and runbooks
# --------------------------------------------------------------------------
ROOT_CAUSES: list[dict[str, str]] = [
    {
        "id": "RC-001",
        "name": "kafka_consumer_lag",
        "summary": "Kafka consumer lag on payments-ledger after a deploy or nightly batch",
        "detail": (
            "A payments-ledger deploy or the nightly settlement batch pushed far more events "
            "through the payments topic than the consumer group could drain. Lag crossed the "
            "alerting threshold and checkout requests that wait on ledger confirmation timed out."
        ),
    },
    {
        "id": "RC-002",
        "name": "redis_failover_flap",
        "summary": "Redis sentinel failover flap caused connection churn",
        "detail": (
            "A redis-cache sentinel failover flapped between replicas. Clients reconnected "
            "repeatedly, so connection setup cost dominated latency and cache hit rate collapsed."
        ),
    },
    {
        "id": "RC-003",
        "name": "tls_cert_expiry",
        "summary": "Expired internal TLS certificate on auth-service",
        "detail": (
            "The internal TLS certificate used between the edge and auth-service expired. "
            "Handshakes failed and token validation returned 5xx for a share of requests."
        ),
    },
    {
        "id": "RC-004",
        "name": "deploy_regression_latency",
        "summary": "payments-ledger v1.8.3 added a synchronous ledger write on the checkout path",
        "detail": (
            "payments-ledger v1.8.3 introduced a synchronous write to the ledger audit table "
            "inside the checkout request path. The write serialised behind a hot row lock and "
            "inflated p99 latency without any infrastructure saturation."
        ),
    },
    {
        "id": "RC-005",
        "name": "connection_pool_exhaustion",
        "summary": "checkout-api database connection pool exhausted under burst traffic",
        "detail": (
            "A traffic burst plus slow ledger calls held every connection in the checkout-api "
            "pool. New requests queued for a connection instead of failing fast."
        ),
    },
    {
        "id": "RC-006",
        "name": "webhook_retry_storm",
        "summary": "webhook-dispatcher retry storm amplified downstream load",
        "detail": (
            "webhook-dispatcher retried failed deliveries immediately and without a cap, "
            "multiplying load on payments-ledger until the ledger degraded for real traffic."
        ),
    },
    {
        "id": "RC-007",
        "name": "fraud_scorer_model_timeout",
        "summary": "fraud-scorer model inference exceeded its timeout budget",
        "detail": (
            "A newly promoted fraud model exceeded the inference timeout budget. Requests fell "
            "back to the slow rule engine, which pushed checkout p99 over SLO."
        ),
    },
    {
        "id": "RC-008",
        "name": "cache_stampede",
        "summary": "Redis cache stampede after bulk key eviction",
        "detail": (
            "A memory-pressure eviction dropped a hot key set at once. Every in-flight request "
            "rebuilt the same entries concurrently, saturating payments-ledger."
        ),
    },
]

RUNBOOKS: list[dict[str, str]] = [
    {
        "id": "RB-005",
        "title": "Raise checkout-api connection pool and add a ledger circuit breaker",
        "root_cause_id": "RC-005",
        "steps": [
            "Raise DB_POOL_SIZE on checkout-api from 20 to 60 and roll the deployment.",
            "Enable the ledger circuit breaker so pool waiters fail fast instead of queueing.",
            "Confirm pool wait time is under 5ms for 10 minutes.",
        ],
        "verified": True,
    },
    {
        "id": "RB-007",
        "title": "Rotate the internal TLS certificate and reload auth-service",
        "root_cause_id": "RC-003",
        "steps": [
            "Issue the replacement internal certificate from the platform CA.",
            "Roll auth-service with the new secret; do not restart the edge first.",
            "Verify handshake error rate returns to zero and add a 21-day expiry alert.",
        ],
        "verified": True,
    },
    {
        "id": "RB-014",
        "title": "Scale the payments-ledger consumer group and replay the affected partition",
        "root_cause_id": "RC-001",
        "steps": [
            "Scale the payments-ledger consumer group from 4 to 12 members.",
            "Confirm lag is draining at a sustained rate, not just bouncing.",
            "Replay the affected partition range for the incident window.",
            "Verify ledger reconciliation reports zero unmatched entries.",
        ],
        "verified": True,
    },
    {
        "id": "RB-018",
        "title": "Restore the previous fraud model and warm the inference cache",
        "root_cause_id": "RC-007",
        "steps": [
            "Roll fraud-scorer back to the previous promoted model.",
            "Warm the inference cache with the last 24h of feature vectors.",
            "Confirm p99 is back under the 250ms budget before re-promoting anything.",
        ],
        "verified": True,
    },
    {
        "id": "RB-021",
        "title": "Pin the Redis primary and widen the sentinel failover window",
        "root_cause_id": "RC-002",
        "steps": [
            "Pin redis-cache to the healthy primary and stop the flapping sentinel.",
            "Raise down-after-milliseconds so a slow replica is not promoted prematurely.",
            "Drain stale client connections and confirm hit rate recovers.",
        ],
        "verified": True,
    },
    {
        "id": "RB-025",
        "title": "Enable jittered TTLs and single-flight cache fill",
        "root_cause_id": "RC-008",
        "steps": [
            "Apply TTL jitter so hot keys do not expire together.",
            "Enable single-flight so only one request rebuilds a missing key.",
            "Confirm payments-ledger query rate returns to baseline.",
        ],
        "verified": True,
    },
    {
        "id": "RB-032",
        "title": "Roll payments-ledger back to v1.8.2 and move the ledger write off the request path",
        "root_cause_id": "RC-004",
        "steps": [
            "Roll payments-ledger back to v1.8.2 to stop the bleeding.",
            "Move the audit write to an async publisher before re-attempting the release.",
            "Verify checkout p99 is under 800ms for 15 minutes.",
        ],
        "verified": True,
    },
    {
        "id": "RB-040",
        "title": "Cap webhook retry concurrency and add exponential backoff",
        "root_cause_id": "RC-006",
        "steps": [
            "Cap webhook-dispatcher retry concurrency at 50.",
            "Enable exponential backoff with jitter on delivery retries.",
            "Drain the retry queue and confirm ledger latency is unaffected.",
        ],
        "verified": True,
    },
]

# --------------------------------------------------------------------------
# Recurring patterns: service + incident_type + symptom signature -> root cause
# --------------------------------------------------------------------------
PATTERNS: list[dict[str, Any]] = [
    {
        "key": "ledger_deploy_checkout_latency",
        "service": "checkout-api",
        "severity": "p1",
        "incident_type": "latency",
        "root_cause_id": "RC-001",
        "runbook_id": "RB-014",
        "weight": 4,
        "alert_title": "checkout-api p99 latency above SLO",
        "alert_summary": (
            "checkout-api p99 latency 4.2s (SLO 800ms). Elevated ledger confirmation timeouts "
            "starting shortly after the payments-ledger deploy."
        ),
        "errors": [
            "checkout.ledger.confirm timeout after 3000ms (svc=payments-ledger)",
            "WARN LedgerConfirmationClient: 412 in-flight confirmations exceeded budget",
            "payments_ledger_consumer_lag_seconds 1840",
        ],
        "decoy_hypotheses": [
            "checkout-api database connection pool exhaustion",
            "redis-cache failover churn",
        ],
    },
    {
        "key": "nightly_batch_kafka_lag",
        "service": "kafka-bus",
        "severity": "p2",
        "incident_type": "saturation",
        "root_cause_id": "RC-001",
        "runbook_id": "RB-014",
        "weight": 3,
        "alert_title": "Kafka consumer lag on payments topic",
        "alert_summary": (
            "payments-ledger consumer group lag 96k and climbing, coinciding with the nightly "
            "settlement batch."
        ),
        "errors": [
            "kafka_consumer_lag{group=payments-ledger} 96412",
            "WARN SettlementBatchJob: batch volume 3.4x hourly median",
        ],
        "decoy_hypotheses": ["broker disk saturation", "checkout-api traffic spike"],
    },
    {
        "key": "redis_failover_flap",
        "service": "redis-cache",
        "severity": "p2",
        "incident_type": "availability",
        "root_cause_id": "RC-002",
        "runbook_id": "RB-021",
        "weight": 3,
        "alert_title": "redis-cache failover flap",
        "alert_summary": "redis-cache sentinel reported 6 role changes in 10 minutes; hit rate 41%.",
        "errors": [
            "redis_sentinel_role_changes_total 6",
            "redis_connection_errors_total 12480",
            "cache_hit_ratio 0.41",
        ],
        "decoy_hypotheses": ["cache stampede after eviction", "network partition"],
    },
    {
        "key": "tls_rotation_auth_errors",
        "service": "auth-service",
        "severity": "p1",
        "incident_type": "error_rate",
        "root_cause_id": "RC-003",
        "runbook_id": "RB-007",
        "weight": 2,
        "alert_title": "auth-service 5xx rate above 2%",
        "alert_summary": (
            "auth-service 5xx rate 6.8%. Handshake failures spiking after the scheduled "
            "certificate rotation window."
        ),
        "errors": [
            "tls_handshake_failures_total 8123",
            "ERROR x509: certificate has expired or is not yet valid",
            "auth_token_validation_errors_total 5410",
        ],
        "decoy_hypotheses": ["upstream identity provider outage", "redis-cache failover"],
    },
    {
        "key": "ledger_release_regression",
        "service": "payments-ledger",
        "severity": "p1",
        "incident_type": "latency",
        "root_cause_id": "RC-004",
        "runbook_id": "RB-032",
        "weight": 2,
        "alert_title": "payments-ledger p99 latency spike after release",
        "alert_summary": (
            "payments-ledger p99 2.9s with no CPU, memory, or queue saturation. Started within "
            "minutes of the v1.8.3 rollout."
        ),
        "errors": [
            "pg_stat_activity: 38 sessions waiting on lock: ledger_audit row",
            "ledger_write_wait_ms p99 2400",
        ],
        "decoy_hypotheses": ["kafka consumer lag", "database failover"],
    },
    {
        "key": "checkout_burst_pool",
        "service": "checkout-api",
        "severity": "p2",
        "incident_type": "saturation",
        "root_cause_id": "RC-005",
        "runbook_id": "RB-005",
        "weight": 2,
        "alert_title": "checkout-api connection pool saturated",
        "alert_summary": "checkout-api DB pool at 100% utilisation with 900 queued requests.",
        "errors": [
            "db_pool_utilisation 1.0",
            "db_pool_waiters 900",
            "WARN HikariPool: connection acquisition 4210ms",
        ],
        "decoy_hypotheses": ["payments-ledger deploy regression", "traffic attack"],
    },
    {
        "key": "webhook_retry_storm",
        "service": "webhook-dispatcher",
        "severity": "p2",
        "incident_type": "saturation",
        "root_cause_id": "RC-006",
        "runbook_id": "RB-040",
        "weight": 2,
        "alert_title": "webhook-dispatcher retry queue growth",
        "alert_summary": "webhook retry queue 240k and growing; ledger latency degrading.",
        "errors": [
            "webhook_retry_queue_depth 241887",
            "webhook_retry_immediate_total 1.9e6",
        ],
        "decoy_hypotheses": ["merchant endpoint outage", "kafka consumer lag"],
    },
    {
        "key": "fraud_model_timeout",
        "service": "fraud-scorer",
        "severity": "p2",
        "incident_type": "latency",
        "root_cause_id": "RC-007",
        "runbook_id": "RB-018",
        "weight": 2,
        "alert_title": "fraud-scorer inference latency above budget",
        "alert_summary": "fraud-scorer p99 1.8s against a 250ms budget after a model promotion.",
        "errors": [
            "model_inference_timeout_total 3120",
            "WARN RuleEngineFallback: engaged for 22% of requests",
        ],
        "decoy_hypotheses": ["kafka consumer lag", "CPU throttling"],
    },
    {
        "key": "cache_stampede",
        "service": "redis-cache",
        "severity": "p2",
        "incident_type": "saturation",
        "root_cause_id": "RC-008",
        "runbook_id": "RB-025",
        "weight": 2,
        "alert_title": "redis-cache eviction storm",
        "alert_summary": "redis-cache evicted 1.2M keys in 4 minutes; payments-ledger query rate 9x.",
        "errors": [
            "redis_evicted_keys_total 1214000",
            "ledger_query_rate 9.1x baseline",
        ],
        "decoy_hypotheses": ["redis failover flap", "memory leak"],
    },
    {
        "key": "auth_redis_dependency",
        "service": "auth-service",
        "severity": "p3",
        "incident_type": "latency",
        "root_cause_id": "RC-002",
        "runbook_id": "RB-021",
        "weight": 1,
        "alert_title": "auth-service token validation latency elevated",
        "alert_summary": "auth-service token validation p99 900ms; redis-cache dependency slow.",
        "errors": ["auth_redis_latency_ms p99 880", "cache_hit_ratio 0.52"],
        "decoy_hypotheses": ["identity provider slowdown"],
    },
]

NOISE_PATTERNS: list[dict[str, Any]] = [
    {
        "service": "fraud-scorer",
        "severity": "p3",
        "incident_type": "error_rate",
        "alert_title": "fraud-scorer rule engine 4xx spike",
        "alert_summary": "fraud-scorer returned 4xx for 3.1% of requests for 6 minutes.",
        "errors": ["fraud_rule_validation_4xx_total 4210"],
        "resolution": "Merchant sent a malformed payload; schema validation tightened and no infra change was needed.",
        "root_cause_id": "RC-NOISE",
        "runbook_id": None,
    },
    {
        "service": "webhook-dispatcher",
        "severity": "p3",
        "incident_type": "error_rate",
        "alert_title": "webhook delivery failures for a single merchant",
        "alert_summary": "One merchant endpoint returned 500 for all deliveries.",
        "errors": ["webhook_delivery_failed_total 1204 merchant=merchant_8842"],
        "resolution": "Merchant endpoint was down. Deliveries replayed successfully after they recovered.",
        "root_cause_id": "RC-NOISE",
        "runbook_id": None,
    },
    {
        "service": "checkout-api",
        "severity": "p3",
        "incident_type": "latency",
        "alert_title": "checkout-api p95 elevated on one region",
        "alert_summary": "checkout-api p95 1.1s in eu-west-1 only, lasted 9 minutes.",
        "errors": ["checkout_p95_ms{region=eu-west-1} 1120"],
        "resolution": "Regional network path degradation upstream of our infrastructure; self-resolved.",
        "root_cause_id": "RC-NOISE",
        "runbook_id": None,
    },
]

OPERATORS = ["d.rao", "m.iyer", "k.novak", "s.okafor", "j.lindqvist"]
CHANNELS = ["#payments-oncall", "#platform-oncall", "#identity-oncall"]


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _timeline(
    opened: datetime,
    rng: random.Random,
    *,
    service: str,
    acknowledged_min: int,
    diagnosed_min: int,
    mitigated_min: int,
    resolved_min: int,
    hypothesis: str,
    fix: str,
    outcome: str,
) -> list[dict[str, str]]:
    events = [
        (0, "pager", "alert_fired", f"{service} alert fired"),
        (acknowledged_min, "operator", "acknowledged", "Incident acknowledged, triage started"),
        (
            acknowledged_min + 1,
            "agent",
            "recall",
            "Recalled prior incidents and validated runbooks for this service and symptom",
        ),
        (diagnosed_min, "agent", "proposed_diagnosis", hypothesis),
    ]
    if outcome == "rejected":
        events.append(
            (
                diagnosed_min + 3,
                "operator",
                "diagnosis_rejected",
                "Proposal rejected; evidence did not match the observed saturation profile",
            )
        )
    events.append((mitigated_min, "operator", "mitigation_applied", fix))
    if outcome == "confirmed":
        events.append(
            (mitigated_min + 4, "operator", "resolution_confirmed", "Fix verified, metrics back in SLO")
        )
    elif outcome == "inconclusive":
        events.append(
            (mitigated_min + 4, "operator", "inconclusive", "Symptoms cleared without a confirmed cause")
        )
    events.append((resolved_min, "operator", "resolved", "Incident closed"))
    return [
        {"at": _iso(opened + timedelta(minutes=offset)), "actor": actor, "event": event, "detail": detail}
        for offset, actor, event, detail in sorted(events, key=lambda e: e[0])
    ]


def build_dataset(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)

    pattern_pool: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        pattern_pool.extend([pattern] * pattern["weight"])

    incidents: list[dict[str, Any]] = []
    labels: dict[str, dict[str, Any]] = {}
    postmortems: list[dict[str, Any]] = []
    occurrence: dict[str, int] = {}

    total = 52
    cursor = DATA_START
    noise_every = 7

    for index in range(total):
        # Spread incidents across 8 weeks with realistic clustering.
        cursor += timedelta(hours=rng.randint(3, 34))
        opened = cursor + timedelta(minutes=rng.randint(0, 59))

        use_noise = (index + 1) % noise_every == 0
        if use_noise:
            template = rng.choice(NOISE_PATTERNS)
            incident_id = f"INC-{1001 + index}"
            operator = rng.choice(OPERATORS)
            resolved_min = rng.randint(12, 40)
            incidents.append(
                {
                    "incident_id": incident_id,
                    "service": template["service"],
                    "severity": template["severity"],
                    "incident_type": template["incident_type"],
                    "environment": "prod",
                    "opened_at": _iso(opened),
                    "channel": rng.choice(CHANNELS),
                    "operator": operator,
                    "alert": {
                        "title": template["alert_title"],
                        "summary": template["alert_summary"],
                        "source": "pagerduty-sim",
                        "fired_at": _iso(opened),
                        "signals": {"error_samples": template["errors"]},
                    },
                    "error_messages": template["errors"],
                    "timeline": _timeline(
                        opened,
                        rng,
                        service=template["service"],
                        acknowledged_min=rng.randint(1, 6),
                        diagnosed_min=rng.randint(6, 14),
                        mitigated_min=resolved_min - 5,
                        resolved_min=resolved_min,
                        hypothesis="Investigated upstream dependencies and traffic shape",
                        fix=template["resolution"],
                        outcome="inconclusive",
                    ),
                    "operator_notes": [template["resolution"]],
                    "diagnosis": {
                        "proposed_by": "agent",
                        "hypothesis": "Investigated upstream dependencies and traffic shape",
                        "outcome": "inconclusive",
                        "actual_root_cause_id": None,
                    },
                    "resolution": {
                        "fix": template["resolution"],
                        "verified": False,
                        "time_to_resolve_minutes": resolved_min,
                        "validated_runbook_id": None,
                    },
                }
            )
            labels[incident_id] = {
                "root_cause_id": None,
                "validated_runbook_id": None,
                "service": template["service"],
                "severity": template["severity"],
                "incident_type": template["incident_type"],
                "is_noise": True,
            }
            continue

        pattern = rng.choice(pattern_pool)
        key = pattern["key"]
        occurrence[key] = occurrence.get(key, 0) + 1
        nth = occurrence[key]

        # The learning story: the first time a pattern appears the diagnosis is
        # inconclusive, the second time it is confirmed but no runbook has been
        # promoted yet, and from the third time on a validated runbook exists.
        if nth == 1:
            outcome = "inconclusive"
            runbook_id = None
            hypothesis = f"Initial hypothesis: {pattern['decoy_hypotheses'][0]}"
        elif nth == 2:
            outcome = "rejected"
            runbook_id = None
            hypothesis = f"Initial hypothesis: {pattern['decoy_hypotheses'][-1]}"
        else:
            outcome = "confirmed"
            runbook_id = pattern["runbook_id"]
            hypothesis = (
                f"Confirmed cause {pattern['root_cause_id']} "
                f"({next(rc['name'] for rc in ROOT_CAUSES if rc['id'] == pattern['root_cause_id'])}) "
                f"based on matching prior incidents"
            )

        incident_id = f"INC-{1001 + index}"
        operator = rng.choice(OPERATORS)
        acknowledged = rng.randint(1, 7)
        diagnosed = acknowledged + rng.randint(4, 12)
        resolved_min = diagnosed + rng.randint(15, 70)
        mitigated = max(diagnosed + 2, resolved_min - rng.randint(5, 12))

        runbook = next((rb for rb in RUNBOOKS if rb["id"] == runbook_id), None)
        fix = (
            runbook["title"]
            if runbook
            else f"Manual mitigation applied for {pattern['root_cause_id']}; runbook not yet promoted"
        )

        incidents.append(
            {
                "incident_id": incident_id,
                "service": pattern["service"],
                "severity": pattern["severity"],
                "incident_type": pattern["incident_type"],
                "environment": "prod",
                "opened_at": _iso(opened),
                "channel": rng.choice(CHANNELS),
                "operator": operator,
                "pattern_key": key,
                "pattern_occurrence": nth,
                "alert": {
                    "title": pattern["alert_title"],
                    "summary": pattern["alert_summary"],
                    "source": "pagerduty-sim",
                    "fired_at": _iso(opened),
                    "signals": {"error_samples": pattern["errors"]},
                },
                "error_messages": pattern["errors"],
                "timeline": _timeline(
                    opened,
                    rng,
                    service=pattern["service"],
                    acknowledged_min=acknowledged,
                    diagnosed_min=diagnosed,
                    mitigated_min=mitigated,
                    resolved_min=resolved_min,
                    hypothesis=hypothesis,
                    fix=fix,
                    outcome=outcome,
                ),
                "operator_notes": [
                    f"Alert looked like {pattern['decoy_hypotheses'][0]} at first glance.",
                    f"Confirmed root cause: {pattern['root_cause_id']}."
                    if outcome == "confirmed"
                    else "Cause not confirmed before symptoms cleared.",
                ],
                "diagnosis": {
                    "proposed_by": "agent",
                    "hypothesis": hypothesis,
                    "outcome": outcome,
                    "actual_root_cause_id": pattern["root_cause_id"]
                    if outcome in {"confirmed", "rejected"}
                    else None,
                },
                "resolution": {
                    "fix": fix,
                    "verified": outcome == "confirmed",
                    "time_to_resolve_minutes": resolved_min,
                    "validated_runbook_id": runbook_id,
                },
            }
        )

        labels[incident_id] = {
            "root_cause_id": pattern["root_cause_id"],
            "validated_runbook_id": pattern["runbook_id"],
            "service": pattern["service"],
            "severity": pattern["severity"],
            "incident_type": pattern["incident_type"],
            "is_noise": False,
        }

        # Postmortems for the confirmed, later occurrences of a pattern.
        if outcome == "confirmed" and len(postmortems) < 8:
            root_cause = next(rc for rc in ROOT_CAUSES if rc["id"] == pattern["root_cause_id"])
            postmortems.append(
                {
                    "postmortem_id": f"PM-{len(postmortems) + 1:03d}",
                    "incident_id": incident_id,
                    "root_cause_id": root_cause["id"],
                    "service": pattern["service"],
                    "root_cause": root_cause["summary"],
                    "detail": root_cause["detail"],
                    "contributing_factors": [
                        "Alert thresholds did not distinguish this pattern from unrelated latency",
                        "No automated guardrail caught the change before it reached production",
                    ],
                    "prevention": [
                        f"Promote {pattern['runbook_id']} into the on-call runbook index",
                        "Add a targeted pre-deploy check for this failure mode",
                    ],
                    "time_to_resolve_minutes": resolved_min,
                }
            )

    demo = build_demo_incident()
    return {
        "company": COMPANY,
        "services": SERVICES,
        "root_causes": ROOT_CAUSES,
        "runbooks": RUNBOOKS,
        "incidents": incidents,
        "postmortems": postmortems,
        "labels": labels,
        "demo": demo,
    }


def build_demo_incident() -> dict[str, Any]:
    """A novel incident used for the teach-then-replay learning demonstration.

    Deliberately *not* one of the seeded patterns: the symptom is signature
    verification failure, which no historical incident shares. A correct recall
    should therefore find nothing relevant until an operator teaches the outcome.
    """
    opened = DATA_START + timedelta(weeks=WEEKS, hours=6)
    return {
        "incident_id": "DEMO-001",
        "service": "webhook-dispatcher",
        "severity": "p1",
        "incident_type": "data_corruption",
        "environment": "prod",
        "opened_at": _iso(opened),
        "channel": "#platform-oncall",
        "operator": "m.iyer",
        "is_demo": True,
        "alert": {
            "title": "webhook signature verification failing for in-flight deliveries",
            "summary": (
                "webhook-dispatcher delivery failure rate 38%. Every failure is a signature "
                "verification error, and only for deliveries queued before the key rotation."
            ),
            "source": "pagerduty-sim",
            "fired_at": _iso(opened),
            "signals": {
                "error_samples": [
                    "webhook_signature_verification_failed_total 18420",
                    "ERROR SignatureMismatch: payload signature does not match any active key",
                    "webhook_delivery_failure_rate 0.38",
                ]
            },
        },
        "error_messages": [
            "webhook_signature_verification_failed_total 18420",
            "ERROR SignatureMismatch: payload signature does not match any active key",
            "webhook_delivery_failure_rate 0.38",
        ],
        "timeline": [
            {"at": _iso(opened), "actor": "pager", "event": "alert_fired", "detail": "webhook-dispatcher alert fired"},
            {
                "at": _iso(opened + timedelta(minutes=3)),
                "actor": "operator",
                "event": "acknowledged",
                "detail": "Incident acknowledged, triage started",
            },
            {
                "at": _iso(opened + timedelta(minutes=4)),
                "actor": "agent",
                "event": "recall",
                "detail": "Recalled prior incidents for webhook-dispatcher",
            },
        ],
        "operator_notes": [
            "Signature verification is the dominant error, not a retry storm.",
            "The signing key was rotated without a dual-key overlap window.",
        ],
        "diagnosis": {
            "proposed_by": "agent",
            "hypothesis": "Uncertain: no matching historical incident for signature verification failures",
            "outcome": "pending",
            "actual_root_cause_id": "RC-009",
        },
        "resolution": {
            "fix": "Restore the previous signing key alongside the new key for a 24h overlap window, then drain the retry queue",
            "verified": True,
            "time_to_resolve_minutes": 47,
            "validated_runbook_id": "RB-051",
        },
        "hidden_ground_truth": {
            "root_cause_id": "RC-009",
            "root_cause": "webhook signing key rotated without a dual-key overlap window",
            "validated_runbook_id": "RB-051",
            "validated_fix": (
                "Restore the previous signing key alongside the new key for a 24h overlap "
                "window, then drain the retry queue"
            ),
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the NimbusPay incident dataset.")
    parser.add_argument("--seed", type=int, default=1337, help="RNG seed for deterministic output")
    parser.add_argument("--out", type=Path, default=Path("data/seed"), help="seed output directory")
    parser.add_argument("--demo-out", type=Path, default=Path("data/demo"), help="demo output directory")
    args = parser.parse_args()

    dataset = build_dataset(args.seed)
    incidents = dataset["incidents"]
    postmortems = dataset["postmortems"]
    demo = dataset["demo"]

    write_json(args.out / "services.json", {"company": COMPANY, "services": dataset["services"]})
    write_json(args.out / "root_causes.json", {"root_causes": dataset["root_causes"]})
    write_json(args.out / "runbooks.json", {"runbooks": dataset["runbooks"]})
    write_json(args.out / "incidents.json", {"company": COMPANY, "incidents": incidents})
    write_json(args.out / "postmortems.json", {"postmortems": postmortems})
    write_json(args.out / "labels.json", {"labels": dataset["labels"]})
    write_json(args.demo_out / "novel_incident.json", demo)

    service_counts: dict[str, int] = {}
    for incident in incidents:
        service_counts[incident["service"]] = service_counts.get(incident["service"], 0) + 1
    confirmed = sum(1 for i in incidents if i["diagnosis"]["outcome"] == "confirmed")

    write_json(
        args.out / "manifest.json",
        {
            "company": COMPANY,
            "seed": args.seed,
            "generated_by": "scripts/generate_data.py",
            "incident_count": len(incidents),
            "postmortem_count": len(postmortems),
            "confirmed_count": confirmed,
            "inconclusive_count": len(incidents) - confirmed,
            "service_counts": service_counts,
            "window_start": incidents[0]["opened_at"],
            "window_end": incidents[-1]["opened_at"],
            "demo_incident_id": demo["incident_id"],
        },
    )

    print(f"wrote {len(incidents)} incidents ({confirmed} confirmed) to {args.out}")
    print(f"wrote {len(postmortems)} postmortems and 1 demo incident to {args.demo_out}")


if __name__ == "__main__":
    main()
