"""API tests. The memory store is always replaced with the fake - no network."""

from __future__ import annotations

from src.memory import InMemoryMemoryStore
from tests.agent.fake_llm import FakeLLM
from tests.api.helpers import (
    RC_KAFKA_LAG,
    alert_body,
    investigate_then_propose,
    make_client,
)


def test_health_returns_200_without_touching_memory() -> None:
    with make_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["memory_mode"] == "on"
    assert body["bank_id"] == "dejaops-test"
    assert body["model_primary"] == "openai/gpt-oss-120b"
    assert body["model_fallback"] == "qwen/qwen3.8-27b"


def test_memory_health_reports_success_with_a_working_store() -> None:
    with make_client() as client:
        response = client.get("/health/memory")

    assert response.status_code == 200
    body = response.json()
    assert body["operation"] == "health"
    assert body["success"] is True
    assert body["mode"] == "on"
    assert body["bank_id"] == "dejaops-test"


def test_memory_health_reports_off_mode_explicitly() -> None:
    with make_client(mode="off") as client:
        response = client.get("/health/memory")

    body = response.json()
    assert body["mode"] == "off"
    assert body["success"] is False
    assert body["error_code"] == "memory_off"


def test_memory_traces_endpoint_returns_recorded_traces() -> None:
    with make_client() as client:
        client.get("/health/memory")
        response = client.get("/api/memory/traces")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] >= 2
    # startup records create_bank, then the explicit health probe
    assert [t["operation"] for t in body["traces"]] == ["create_bank", "health"]


def test_memory_traces_limit_is_honoured() -> None:
    with make_client() as client:
        for _ in range(4):
            client.get("/health/memory")
        response = client.get("/api/memory/traces?limit=2")

    assert response.json()["count"] == 2


def test_catalog_exposes_valid_root_cause_and_runbook_ids() -> None:
    with make_client() as client:
        response = client.get("/catalog")

    assert response.status_code == 200
    body = response.json()
    assert body["company"] == "NimbusPay"

    root_causes = {item["id"]: item for item in body["root_causes"]}
    runbooks = {item["id"]: item for item in body["runbooks"]}
    assert root_causes and runbooks

    # Every runbook must point at a root cause the same payload exposes, or the
    # feedback UI could prefill a cause the API then rejects.
    for runbook in runbooks.values():
        assert runbook["root_cause_id"] in root_causes
        assert runbook["title"]

    # And the ids it advertises must be the ones the feedback endpoint accepts.
    with make_client(llm=FakeLLM(investigate_then_propose())) as client:
        incident_id = client.post("/alerts", json=alert_body()).json()["incident_id"]
        accepted = client.post(
            f"/incidents/{incident_id}/feedback",
            json={
                "feedback_type": "DIAGNOSIS_CONFIRMED",
                "operator": "m.iyer",
                "root_cause_id": RC_KAFKA_LAG,
            },
        )
        rejected = client.post(
            f"/incidents/{incident_id}/feedback",
            json={
                "feedback_type": "DIAGNOSIS_CONFIRMED",
                "operator": "m.iyer",
                "root_cause_id": "RC-does-not-exist",
            },
        )

    assert accepted.status_code == 200, accepted.text
    assert RC_KAFKA_LAG in root_causes
    assert rejected.status_code == 422


def test_unknown_route_is_404() -> None:
    with make_client() as client:
        assert client.get("/nope").status_code == 404


def test_cors_allows_the_frontend_origin() -> None:
    with make_client() as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_startup_degrades_honestly_when_memory_is_down() -> None:
    store = InMemoryMemoryStore(bank_id="dejaops-test", fail_retain=True)

    class FailingBankStore(InMemoryMemoryStore):
        def create_bank_if_needed(self):  # type: ignore[no-untyped-def]
            from src.memory.trace import (
                ErrorCode,
                MemoryMode,
                MemoryOperation,
                MemoryTrace,
                utcnow,
            )

            return MemoryTrace.build(
                operation=MemoryOperation.CREATE_BANK,
                mode=MemoryMode.DEGRADED,
                started_at=utcnow(),
                success=False,
                bank_id=self.bank_id,
                degraded=True,
                error_code=ErrorCode.UNAVAILABLE,
                error_message="provider down",
            )

    with make_client(store=FailingBankStore(bank_id="dejaops-test")) as client:
        # The app still starts and serves; it just reports the degraded state.
        assert client.get("/health").status_code == 200
        body = client.get("/api/memory/traces").json()

    assert body["traces"][0]["success"] is False
    assert body["traces"][0]["degraded"] is True
    assert body["traces"][0]["error_code"] == "hindsight_unavailable"
    assert store.fail_retain is True
