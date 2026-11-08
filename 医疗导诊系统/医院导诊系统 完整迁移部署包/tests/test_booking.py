from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from service import BookingService


@pytest.fixture
def service(tmp_path: Path) -> BookingService:
    return BookingService(tmp_path / "medical.db", "test-signing-key", clock=lambda: 1791705600.0)


def run(service: BookingService, patient: str, action: str, **fields: object) -> dict:
    return service.run({"action": action, "patient_token": service.issue_token(patient), **fields})


def slot(service: BookingService, doctor: int = 2) -> str:
    return service.run({"action": "query", "doctor_id": doctor})["slots"][0]["slot_id"]


def test_hold_confirm_cancel_releases_slot(service: BookingService) -> None:
    target = slot(service)
    held = run(service, "DEMO-A", "hold", slot_id=target, request_id="hold-a")
    assert held["ok"] and held["appointment"]["status"] == "held"
    conflict = run(service, "DEMO-B", "hold", slot_id=target, request_id="hold-b")
    assert conflict["code"] == "SLOT_TAKEN" and conflict["alternatives"]
    appointment_id = held["appointment"]["appointment_id"]
    confirmed = run(service, "DEMO-A", "confirm", appointment_id=appointment_id, request_id="confirm-a")
    assert confirmed["appointment"]["status"] == "confirmed"
    cancelled = run(service, "DEMO-A", "cancel", appointment_id=appointment_id, request_id="cancel-a")
    assert cancelled["appointment"]["status"] == "cancelled"
    assert run(service, "DEMO-B", "hold", slot_id=target, request_id="hold-b2")["ok"]


def test_twenty_simultaneous_patients_only_one_wins(service: BookingService) -> None:
    target = slot(service)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda i: run(service, f"DEMO-{i}", "hold", slot_id=target, request_id=f"race-{i}"), range(20)))
    assert sum(result["ok"] for result in results) == 1
    assert all(result["code"] == "SLOT_TAKEN" for result in results if not result["ok"])


def test_expiry_is_checked_on_access_without_cleanup_job(service: BookingService) -> None:
    target = slot(service)
    held = run(service, "DEMO-A", "hold", slot_id=target, request_id="hold-a")
    original = service.clock()
    service.clock = lambda: original + 181
    other = run(service, "DEMO-B", "hold", slot_id=target, request_id="hold-b")
    assert other["ok"]
    result = run(service, "DEMO-A", "confirm", appointment_id=held["appointment"]["appointment_id"], request_id="confirm-a")
    assert result["code"] == "HOLD_EXPIRED" and result["alternatives"]


def test_idempotent_retry_and_payload_mismatch(service: BookingService) -> None:
    targets = service.run({"action": "query", "doctor_id": 2})["slots"]
    first = run(service, "DEMO-A", "hold", slot_id=targets[0]["slot_id"], request_id="request-1")
    retry = run(service, "DEMO-A", "hold", slot_id=targets[0]["slot_id"], request_id="request-1")
    assert retry["replayed"] and retry["appointment"]["appointment_id"] == first["appointment"]["appointment_id"]
    mismatch = run(service, "DEMO-A", "hold", slot_id=targets[1]["slot_id"], request_id="request-1")
    assert mismatch["code"] == "IDEMPOTENCY_CONFLICT"
    status = run(service, "DEMO-A", "status", request_id="request-1")
    assert status["appointment"]["appointment_id"] == first["appointment"]["appointment_id"]


def test_patient_cannot_confirm_cancel_or_read_anothers_appointment(service: BookingService) -> None:
    held = run(service, "DEMO-A", "hold", slot_id=slot(service), request_id="hold-a")
    for action in ["confirm", "cancel", "status"]:
        result = run(service, "DEMO-B", action, appointment_id=held["appointment"]["appointment_id"], request_id=f"{action}-b")
        assert result["code"] == "NOT_FOUND"


def test_invalid_and_expired_identity(service: BookingService) -> None:
    assert service.run({"action": "list", "patient_token": "DEMO-A"})["code"] == "UNAUTHORIZED"
    token = service.issue_token("DEMO-A", ttl=-1)
    assert service.run({"action": "list", "patient_token": token})["code"] == "UNAUTHORIZED"


def test_restart_preserves_appointments(service: BookingService) -> None:
    target = slot(service)
    held = run(service, "DEMO-A", "hold", slot_id=target, request_id="hold-a")
    restarted = BookingService(service.path, "test-signing-key", clock=service.clock)
    assert run(restarted, "DEMO-A", "status", appointment_id=held["appointment"]["appointment_id"])["appointment"]["status"] == "held"
    assert run(restarted, "DEMO-B", "hold", slot_id=target, request_id="hold-b")["code"] == "SLOT_TAKEN"


def test_invalid_slot_and_operation(service: BookingService) -> None:
    assert run(service, "DEMO-A", "hold", slot_id="not-a-slot", request_id="a")["code"] == "INVALID_SLOT"
    assert service.run({"action": "book anything"})["code"] == "INVALID_ACTION"


def test_same_patient_overlapping_doctors_is_blocked(service: BookingService) -> None:
    left = service.run({"action": "query", "doctor_id": 2})["slots"]
    right = service.run({"action": "query", "doctor_id": 1})["slots"]
    matching = next((a, b) for a in left for b in right if a["starts_at"] == b["starts_at"])
    assert run(service, "DEMO-A", "hold", slot_id=matching[0]["slot_id"], request_id="a")["ok"]
    assert run(service, "DEMO-A", "hold", slot_id=matching[1]["slot_id"], request_id="b")["code"] == "PATIENT_TIME_CONFLICT"


def test_expired_original_request_never_reacquires_slot(service: BookingService) -> None:
    target = slot(service)
    run(service, "DEMO-A", "hold", slot_id=target, request_id="a")
    now = service.clock()
    service.clock = lambda: now + 181
    result = run(service, "DEMO-A", "hold", slot_id=target, request_id="a")
    assert not result["ok"] and result["code"] == "HOLD_EXPIRED" and result["replayed"]
