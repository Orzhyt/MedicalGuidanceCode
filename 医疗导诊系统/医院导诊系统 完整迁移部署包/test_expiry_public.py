"""Start a real 180-second hold, then check after independent work has completed."""
import json
from pathlib import Path
import sys
import time
import uuid

import requests

ROOT = Path("/tmp/hospital_guidance")
keys = json.loads((ROOT / "private/dify_api_keys.json").read_text())
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
session = requests.Session()
session.trust_env = False


def token(patient_id: str) -> str:
    response = session.post("http://medical-guidance:8095/api/demo/session", headers={"X-Service-Key": credentials["api_key"]}, json={"patient_id": patient_id}, timeout=10)
    response.raise_for_status()
    return response.json()["patient_token"]


def run(action: str, patient_token: str = "", **fields: str) -> dict:
    inputs = {"booking_payload": json.dumps({"action":action, **fields}), "patient_token":patient_token}
    response = session.post("http://nginx/v1/workflows/run", headers={"Authorization": "Bearer " + keys["booking"]}, json={"inputs": inputs, "response_mode": "blocking", "user": "DEMO-EXPIRY"}, timeout=60)
    response.raise_for_status()
    output = response.json()["data"]
    assert output["status"] == "succeeded"
    return json.loads(output["outputs"]["result_json"])


state_path = ROOT / "private/expiry_state.json"
mode = sys.argv[1]
if mode == "start":
    run_id = uuid.uuid4().hex[:10]
    target = run("query", doctor_id="13")["slots"][0]["slot_id"]
    held = run("hold", token("DEMO-EXPIRY-A"), slot_id=target, request_id="expiry-" + run_id)
    assert held["code"] == "HELD"
    state_path.write_text(json.dumps({"created_at": time.time(), "run_id": run_id, "slot_id": target, "appointment_id": held["appointment"]["appointment_id"], "held": held}), encoding="utf-8")
    print(json.dumps({"held": True, "expires_at": held["appointment"]["expires_at"]}))
elif mode == "restart-check":
    state = json.loads(state_path.read_text())
    result = run("status", token("DEMO-EXPIRY-A"), appointment_id=state["appointment_id"])
    assert result["appointment"]["status"] == "held", "Run this check before the actual hold expires"
    (ROOT / "reports/restart_persistence.json").write_text(json.dumps({"passed": True, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "persisted_after_container_restart": True}))
elif mode == "check":
    state = json.loads(state_path.read_text())
    assert time.time() - state["created_at"] >= 181, "Hold has not expired yet; continue independent work"
    a, b = token("DEMO-EXPIRY-A"), token("DEMO-EXPIRY-B")
    lost = run("confirm", a, appointment_id=state["appointment_id"], request_id="expired-confirm-" + state["run_id"])
    assert lost["code"] == "HOLD_EXPIRED" and lost["alternatives"]
    other = run("hold", b, slot_id=state["slot_id"], request_id="other-hold-" + state["run_id"])
    assert other["code"] == "HELD"
    retry = run("hold", a, slot_id=state["slot_id"], request_id="expiry-" + state["run_id"])
    assert retry["code"] == "HOLD_EXPIRED" and retry["replayed"]
    cleanup = run("cancel", b, appointment_id=other["appointment"]["appointment_id"], request_id="expiry-cleanup-" + state["run_id"])
    (ROOT / "reports/expiry_public.json").write_text(json.dumps({"passed": True, "real_elapsed_seconds": round(time.time() - state["created_at"], 1), "hold_seconds": 180, "results": {"expired_confirm": lost, "other_patient_hold": other, "original_request_replay": retry, "cleanup": cleanup}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "real_expiry": True, "hold_seconds": 180}))
else:
    raise ValueError("Expected start, restart-check or check")
