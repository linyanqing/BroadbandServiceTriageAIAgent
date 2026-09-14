from fastapi.testclient import TestClient

from agent.main import app

client = TestClient(app)


def test_health_and_ready_endpoints():
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready").json()
    assert ready["status"] == "ok"
    assert ready["mock_mode"] is True


def test_scenario_a_fault_requires_approval_then_creates_ticket():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "123", "message": "My broadband keeps dropping out today"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval"]["tool"] == "create_fault_ticket"

    r2 = client.post(f"/api/v1/triage/{body['request_id']}/approve", json={"approved": True})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "resolved"
    assert body2["resolution"] == "fault_ticket_created"
    assert body2["ticket_id"] is not None


def test_scenario_b_known_outage_resolves_without_approval():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "456", "message": "My internet is not working"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "known_outage"
    assert body["ticket_id"] is None


def test_scenario_c_healthy_service_returns_guidance():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "789", "message": "My broadband keeps dropping out"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "healthy_no_action"
    assert body["ticket_id"] is None


def test_scenario_d_rejected_approval_escalates():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "123", "message": "My broadband keeps dropping out today"},
    )
    request_id = r.json()["request_id"]

    r2 = client.post(f"/api/v1/triage/{request_id}/approve", json={"approved": False})
    assert r2.status_code == 200
    assert r2.json()["status"] == "escalated"


def test_approve_unknown_request_id_returns_404():
    r = client.post("/api/v1/triage/REQ-DOES-NOT-EXIST/approve", json={"approved": True})
    assert r.status_code == 404


def test_scenario_e_billing_specialist_requires_approval_then_applies_credit():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "111", "message": "I think I've been overcharged on my bill."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval"]["tool"] == "apply_billing_credit"

    r2 = client.post(f"/api/v1/triage/{body['request_id']}/approve", json={"approved": True})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "resolved"
    assert body2["resolution"] == "billing_hold_resolved"


def test_scenario_f_line_testing_specialist_requires_approval_then_schedules_visit():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "222", "message": "There's a lot of crackling static on the line."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval"]["tool"] == "schedule_technician_visit"

    r2 = client.post(f"/api/v1/triage/{body['request_id']}/approve", json={"approved": True})
    assert r2.status_code == 200
    assert r2.json()["resolution"] == "line_test_fault_confirmed"


def test_scenario_g_equipment_reset_specialist_requires_approval_then_resets():
    r = client.post(
        "/api/v1/triage",
        json={"customer_id": "333", "message": "My router needs a reboot, it's offline."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval"]["tool"] == "trigger_equipment_reset"

    r2 = client.post(f"/api/v1/triage/{body['request_id']}/approve", json={"approved": True})
    assert r2.status_code == 200
    assert r2.json()["resolution"] == "equipment_reset_completed"
