"""Verify the published Dify service API that the colleague will actually integrate."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import requests

ROOT = Path("/tmp/hospital_guidance")
keys = json.loads((ROOT / "private/dify_api_keys.json").read_text())
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
session = requests.Session()
session.trust_env = False
records = []


def booking(action: str, patient_token: str = "", **fields: str) -> dict:
    data = {"action": action, "doctor_id": "", "slot_id": "", "appointment_id": "", "request_id": "", "patient_token": "", **fields}
    inputs = {"booking_payload": json.dumps(data), "patient_token": patient_token}
    response = session.post("http://nginx/v1/workflows/run", headers={"Authorization": "Bearer " + keys["booking"]}, json={"inputs": inputs, "response_mode": "blocking", "user": "DEMO-PUBLIC-A"}, timeout=90)
    response.raise_for_status()
    finished = response.json()["data"]
    assert finished["status"] == "succeeded", finished.get("error")
    result = json.loads(finished["outputs"]["result_json"])
    records.append({"operation": action, "status": finished["status"], "result": result})
    return result


def chat(query: str, conversation_id: str = "") -> tuple[str, dict]:
    response = session.post("http://nginx/v1/chat-messages", headers={"Authorization": "Bearer " + keys["consultation"]}, json={"inputs": {}, "query": query, "conversation_id": conversation_id, "response_mode": "blocking", "user": "DEMO-PUBLIC-A"}, timeout=120)
    response.raise_for_status()
    message = response.json()
    saved = session.get("http://medical-guidance:8095/api/consultations/" + message["conversation_id"] + "/latest", headers={"X-Service-Key": credentials["api_key"]}, timeout=10).json()
    assert saved["ok"] and saved["latest"]["patient_answer"] == message["answer"]
    result = saved["latest"]["result"]
    assert not any(word in message["answer"] for word in ["```", "参考资料", "培训", "模拟"])
    records.append({"operation": "chat", "query": query, "conversation_id": message["conversation_id"], "result": result})
    return message["conversation_id"], result


conversation, result = chat("我身体不舒服，说不清哪里")
assert result["stage"] == "ask"
conversation, result = chat("35岁，饭后上腹胀痛两天，轻微，无发热、呕血黑便、胸痛、气促或晕厥，活动正常", conversation)
assert result["stage"] == "recommend" and result["recommended_doctors"][0]["doctor_id"] in [2, 13]
token_response = session.post("http://medical-guidance:8095/api/demo/session", headers={"X-Service-Key": credentials["api_key"]}, json={"patient_id": "DEMO-PUBLIC-A"}, timeout=10)
token_response.raise_for_status()
patient_token = token_response.json()["patient_token"]
available = booking("query", doctor_id=str(result["recommended_doctors"][0]["doctor_id"]))
target = available["slots"][0]["slot_id"]
request_id = "public-" + uuid.uuid4().hex[:12]
held = booking("hold", patient_token, slot_id=target, request_id=request_id)
assert held["code"] == "HELD"
confirmed = booking("confirm", patient_token, appointment_id=held["appointment"]["appointment_id"], request_id=request_id + "-confirm")
assert confirmed["code"] == "CONFIRMED"
cancelled = booking("cancel", patient_token, appointment_id=held["appointment"]["appointment_id"], request_id=request_id + "-cancel")
assert cancelled["code"] == "CANCELLED"
(ROOT / "reports/public_api.json").write_text(json.dumps({"passed": True, "calls": records}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": True, "published_api_calls": len(records), "conversation_preserved": True}))

