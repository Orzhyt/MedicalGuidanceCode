"""验证患者回复、后台完整记录、多阶段对话及预约参数交接。"""
import json
from pathlib import Path
import uuid

import requests

ROOT = Path("/tmp/hospital_guidance")
keys = json.loads((ROOT / "private/dify_api_keys.json").read_text())
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
http = requests.Session()
http.trust_env = False
headers = {"X-Service-Key": credentials["api_key"]}
records = []
user = "PATIENT-DIALOGUE-" + uuid.uuid4().hex[:8]


def chat(query: str, conversation: str = "") -> tuple[str, dict]:
    response = http.post("http://nginx/v1/chat-messages", headers={"Authorization": "Bearer " + keys["consultation"]}, json={"inputs": {}, "query": query, "conversation_id": conversation, "response_mode": "blocking", "user": user}, timeout=90)
    response.raise_for_status()
    output = response.json()
    saved = http.get("http://medical-guidance:8095/api/consultations/" + output["conversation_id"] + "/latest", headers=headers, timeout=10).json()
    assert saved["ok"], output["answer"]
    row = saved["latest"]
    assert row["patient_query"] == query and row["patient_answer"] == output["answer"]
    assert not any(word in output["answer"] for word in ["```", "参考资料", "培训", "模拟", "request_id", "slot_id", "K03"])
    assert len(output["answer"]) <= 220, output["answer"]
    records.append({"query": query, "answer": output["answer"], "conversation_id": output["conversation_id"], "result": row["result"], "phase": row["state"]["phase"], "rounds": row["state"].get("rounds", 0)})
    print(json.dumps({"stage": row["result"]["stage"], "answer": output["answer"]}, ensure_ascii=False), flush=True)
    return output["conversation_id"], row


clinical = "35岁，饭后上腹轻微胀痛两天，没有发热、呕血黑便、胸痛、气促或晕厥，能正常活动。"
conversation, row = chat(clinical)
assert row["result"]["stage"] == "recommend"
assert row["knowledge"] and row["model_output"]["clinical"]
assert row["result"]["booking_payload"] is None
conversation, row = chat("想挂专家号", conversation)
assert row["state"]["preferences"]["registration_type"] == "expert"
conversation, row = chat("明天", conversation)
conversation, row = chat("上午", conversation)
assert row["result"]["stage"] == "booking_options"
assert row["result"]["booking_payload"] is None
assert any(slot["registration_type"] == "expert" for slot in row["state"]["offered_slots"])
assert any(slot["registration_type"] == "ordinary" for slot in row["state"]["offered_slots"])
conversation, row = chat("上午最重要，普通号也可以", conversation)
assert row["state"]["preferences"]["priority"] == "time"
offered = row["state"]["offered_slots"]
assert offered and all(slot["registration_type"] == "ordinary" for slot in offered)
conversation, row = chat("选第1个", conversation)
assert row["result"]["stage"] == "booking_ready"
assert row["result"]["booking_payload"]["slot_id"] == offered[0]["slot_id"]
first_id = row["result"]["booking_payload"]["request_id"]
conversation, repeated = chat("就这个，预约成功了吗？", conversation)
assert repeated["result"]["booking_payload"]["request_id"] == first_id
assert "预约成功" not in repeated["patient_answer"]
saved = http.get("http://medical-guidance:8095/api/consultations/" + conversation, headers=headers, timeout=10).json()
assert len(saved["turns"]) == 7 and len(saved["messages"]) == 14
assert http.get("http://medical-guidance:8095/api/consultations/" + conversation, timeout=10).status_code == 401

patient_token = http.post("http://medical-guidance:8095/api/patients/session", headers=headers, json={"patient_id": user}, timeout=10).json()["patient_token"]
inputs = dict(row["result"]["booking_workflow_inputs"])
inputs["patient_token"] = patient_token


def run(inputs: dict) -> dict:
    response = http.post("http://nginx/v1/workflows/run", headers={"Authorization": "Bearer " + keys["booking"]}, json={"inputs": inputs, "response_mode": "blocking", "user": user}, timeout=60)
    response.raise_for_status()
    output = response.json()["data"]
    assert output["status"] == "succeeded", output
    return json.loads(output["outputs"]["result_json"])


held = run(inputs)
assert held["code"] == "HELD"
confirmed = run({"booking_payload": json.dumps({"action": "confirm", "appointment_id": held["appointment"]["appointment_id"], "request_id": first_id + "-confirm"}), "patient_token": patient_token})
assert confirmed["code"] == "CONFIRMED"
cancelled = run({"booking_payload": json.dumps({"action": "cancel", "appointment_id": held["appointment"]["appointment_id"], "request_id": first_id + "-cancel"}), "patient_token": patient_token})
assert cancelled["code"] == "CANCELLED"

expert_conversation, row = chat(clinical + "想挂明天上午的专家号。")
assert row["result"]["stage"] == "booking_options"
expert_conversation, row = chat("我只挂专家，时间可以改", expert_conversation)
assert row["state"]["offered_slots"] and all(slot["registration_type"] == "expert" for slot in row["state"]["offered_slots"])
expert_conversation, row = chat("选第1个", expert_conversation)
assert row["result"]["stage"] == "booking_ready" and row["result"]["selected_slot"]["registration_type"] == "expert"
expert_conversation, row = chat("现在又胸痛、出冷汗、喘不过气", expert_conversation)
assert row["result"]["stage"] == "emergency" and not row["result"].get("booking_payload")

(ROOT / "reports/dialogue_v2.json").write_text(json.dumps({"passed": True, "chat_calls": len(records), "handover_calls": 3, "records": records, "handover": {"held": held["code"], "confirmed": confirmed["code"], "cancelled": cancelled["code"]}, "full_history_saved": True, "staff_auth_required": True}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": True, "chat_calls": len(records), "direct_handover": True, "full_history_saved": True}))
