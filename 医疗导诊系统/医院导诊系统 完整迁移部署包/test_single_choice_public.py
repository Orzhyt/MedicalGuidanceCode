"""真实单一候选对话及两个已发布工作流的事务交接。"""
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path("/tmp/hospital_guidance")
keys = json.loads((ROOT / "private/dify_api_keys.json").read_text())
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
http = requests.Session()
http.trust_env = False
headers = {"X-Service-Key": credentials["api_key"]}
base = "http://medical-guidance:8095"
run_id = uuid.uuid4().hex[:10]
user = "PATIENT-SINGLE-" + run_id
records = []
operations = []
cleanup = []


def service(path: str, payload: dict) -> dict:
    response = http.post(base + path, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return response.json()


def token(patient: str) -> str:
    return service("/api/patients/session", {"patient_id": patient})["patient_token"]


def chat(query: str, conversation: str = "") -> tuple[str, dict]:
    response = http.post("http://nginx/v1/chat-messages", headers={"Authorization": "Bearer " + keys["consultation"]}, json={"inputs": {}, "query": query, "conversation_id": conversation, "response_mode": "blocking", "user": user}, timeout=90)
    response.raise_for_status()
    output = response.json()
    saved = http.get(base + "/api/consultations/" + output["conversation_id"] + "/latest", headers=headers, timeout=10).json()["latest"]
    assert saved["patient_answer"] == output["answer"]
    assert not any(word in output["answer"] for word in ["参考资料", "slot_id", "request_id", "培训", "模拟"])
    model_output = saved["model_output"]
    records.append({"query": query, "answer": output["answer"], "stage": saved["result"]["stage"], "booking_model_output": model_output.get("booking", "") if isinstance(model_output, dict) else ""})
    print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    return output["conversation_id"], saved


def booking(inputs: dict, patient: str = user) -> dict:
    response = http.post("http://nginx/v1/workflows/run", headers={"Authorization": "Bearer " + keys["booking"]}, json={"inputs": inputs, "response_mode": "blocking", "user": patient}, timeout=60)
    response.raise_for_status()
    output = response.json()["data"]
    assert output["status"] == "succeeded", output.get("error")
    result = json.loads(output["outputs"]["result_json"])
    operations.append({"code": result["code"], "ok": result["ok"]})
    return result


def action(operation: str, patient_token: str, **fields: str) -> dict:
    return booking({"booking_payload": json.dumps({"action": operation, **fields}), "patient_token": patient_token})


try:
    catalog = http.get(base + "/api/booking/options?specialty_code=ent", headers=headers, timeout=15).json()
    target_date = (datetime.fromisoformat(catalog["server_time"]) + timedelta(days=3)).date().isoformat()
    day_slots = [slot for slot in catalog["slots"] if slot["starts_at"][:10] == target_date]
    assert len(day_slots) >= 2
    selected_slot = day_slots[0]
    # 只临时占用本轮建立的测试号源，finally释放，不触碰已有患者预约。
    for index, slot in enumerate(day_slots[1:]):
        fixture_token = token(user + "-fixture-" + str(index))
        held = service("/api/booking/run", {"action": "hold", "slot_id": slot["slot_id"], "request_id": run_id + "-fixture-" + str(index), "patient_token": fixture_token})
        assert held["code"] == "HELD"
        cleanup.append((fixture_token, held["appointment"]["appointment_id"]))
    day = int(target_date[-2:])
    conversation, row = chat("耳鸣")
    assert row["result"]["stage"] == "ask" and "多久" in row["patient_answer"]
    conversation, row = chat(f"三天了，听力正常，想挂{day}号的专家号", conversation)
    assert row["result"]["stage"] == "booking_options"
    assert len(row["state"]["offered_slots"]) == 1
    assert "只有普通号" in row["patient_answer"]
    conversation, row = chat(f"那就{day}号吧，帮我预约", conversation)
    assert row["result"]["stage"] == "booking_ready"
    inputs = dict(row["result"]["booking_workflow_inputs"])
    payload = json.loads(inputs["booking_payload"])
    assert payload["slot_id"] == selected_slot["slot_id"]
    assert "预约成功" not in row["patient_answer"]
    patient_token = token(user)
    inputs["patient_token"] = patient_token
    held = booking(inputs)
    assert held["code"] == "HELD"
    appointment_id = held["appointment"]["appointment_id"]
    cleanup.append((patient_token, appointment_id))
    repeated = booking(inputs)
    assert repeated["appointment"]["appointment_id"] == appointment_id
    other = token(user + "-other")
    conflict = action("hold", other, slot_id=selected_slot["slot_id"], request_id=run_id + "-other")
    assert conflict["code"] == "SLOT_TAKEN" and conflict["alternatives"]
    confirmed = action("confirm", patient_token, appointment_id=appointment_id, request_id=run_id + "-confirm")
    assert confirmed["code"] == "CONFIRMED"
    cancelled = action("cancel", patient_token, appointment_id=appointment_id, request_id=run_id + "-cancel")
    assert cancelled["code"] == "CANCELLED"
    report = {"passed": True, "chat_calls": len(records), "workflow_calls": len(operations), "records": records, "operations": operations, "handover": "后台原样转交咨询私有输入，仅补充患者令牌", "idempotency_passed": True, "conflict_passed": True, "fixture_cleanup": "仅释放本轮创建的预约"}
    (ROOT / "reports/single_choice_public.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "chat_calls": len(records), "workflow_calls": len(operations)}, ensure_ascii=False))
finally:
    for index, (patient_token, appointment_id) in enumerate(cleanup):
        result = service("/api/booking/run", {"action": "cancel", "appointment_id": appointment_id, "request_id": run_id + "-cleanup-" + str(index), "patient_token": patient_token})
        assert result["code"] == "CANCELLED", result["code"]
