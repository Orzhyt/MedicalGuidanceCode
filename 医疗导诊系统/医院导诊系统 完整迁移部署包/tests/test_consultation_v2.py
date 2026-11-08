import json
from pathlib import Path

from nodes.booking_dialogue import main as dialogue
from nodes.booking_input import main as booking_input
from nodes.render_consultation import main as render
from nodes.triage_guard import main as guard
from nodes.staff_output import main as staff_output
from service import BookingService, create_app


def test_patient_reply_contains_no_staff_information() -> None:
    result = {"stage": "ask", "questions": ["多大年龄？", "不舒服多久了？"], "knowledge_ids": ["K03"], "rationale": "后台判断依据"}
    reply = render(json.dumps(result), "{}")
    assert len(reply["answer"]) < 100
    assert not any(value in reply["answer"] for value in ["K03", "后台", "json", "参考", "模拟", "培训"])


def test_low_department_confidence_routes_to_general() -> None:
    model = {"profile": {"age": 35, "main_symptom": "乏力", "duration": "两天", "severity": "轻微", "red_flags_status": "absent"}, "summary": "乏力两天", "urgency": "routine", "specialty_code": "endocrine", "department_confidence": "low", "questions": [], "knowledge_ids": ["K01"], "rationale": "无法明确专业科室"}
    checked = guard(json.dumps(model), "{}", 0, "K01")
    assert checked["specialty"] == "general"


def options() -> dict:
    return {"ok": True, "server_time": "2026-10-11T12:00:00+08:00", "doctors": [{"id": 2, "name": "李慧敏", "registration_type": "ordinary", "department": "消化内科"}, {"id": 14, "name": "梁文清", "registration_type": "expert", "department": "消化内科"}], "slots": [{"slot_id": "ordinary-am", "doctor_id": 2, "doctor_name": "李慧敏", "registration_type": "ordinary", "starts_at": "2026-10-12T09:00:00+08:00", "ends_at": "2026-10-12T09:20:00+08:00", "hospital_name": "第一人民医院", "department": "消化内科"}, {"slot_id": "expert-next-day", "doctor_id": 14, "doctor_name": "梁文清", "registration_type": "expert", "starts_at": "2026-10-13T09:00:00+08:00", "ends_at": "2026-10-13T09:20:00+08:00", "hospital_name": "第一人民医院", "department": "消化内科"}]}


def test_mild_symptoms_do_not_imply_absent_red_flags() -> None:
    model = {"profile": {"age": 35, "main_symptom": "上腹胀痛", "duration": "两天", "severity": "轻微", "red_flags_status": "absent"}, "summary": "上腹胀痛两天", "urgency": "routine", "specialty_code": "digestive", "department_confidence": "high", "questions": [], "knowledge_ids": ["K03"]}
    checked = guard(json.dumps(model), json.dumps({"history": ["35岁，饭后上腹胀痛两天，轻微，不影响活动"]}), 0, "K03")
    assert checked["stage"] == "recommend"
    assert json.loads(checked["result_json"])["profile"]["red_flags_status"] == "unknown"


def run_dialogue(prefs: dict, state: dict | None = None) -> dict:
    context = {"result": {"stage": "recommend", "specialty_code": "digestive", "department_recommendation": "消化内科"}, "state": state or {"phase": "booking_preferences", "rounds": 3}}
    return json.loads(dialogue(json.dumps(prefs), json.dumps(context), json.dumps(options()))["envelope"])


def test_type_and_time_are_negotiated_together_without_silent_relaxation() -> None:
    pref = {"registration_type": "expert", "date": "2026-10-12", "period": "morning", "priority": "unknown"}
    output = run_dialogue(pref)
    assert output["result"]["stage"] == "booking_options"
    assert not output["result"].get("booking_payload")
    assert {item["slot_id"] for item in output["state"]["offered_slots"]} == {"ordinary-am", "expert-next-day"}
    assert "普通" in output["answer"] and "专家" in output["answer"]


def test_preserving_expert_priority_never_suggests_ordinary_number() -> None:
    output = run_dialogue({"registration_type": "expert", "date": "2026-10-12", "period": "morning", "priority": "type"})
    assert all(item["registration_type"] == "expert" for item in output["state"]["offered_slots"])
    assert not output["result"].get("booking_payload")


def test_booking_rounds_are_not_subject_to_clinical_three_round_limit() -> None:
    first = run_dialogue({"registration_type": "ordinary", "date": "2026-10-12", "period": "morning", "priority": "time"})
    selected = run_dialogue({"selection": 1}, first["state"])
    assert selected["result"]["stage"] == "booking_ready"
    assert selected["state"]["rounds"] == 3
    payload = selected["result"]["booking_payload"]
    assert payload["action"] == "hold" and payload["slot_id"] == "ordinary-am"
    assert "slot_id" not in selected["answer"] and "预约成功" not in selected["answer"]


def test_disappeared_offered_slot_is_not_handed_to_booking() -> None:
    first = run_dialogue({"registration_type": "ordinary", "date": "2026-10-12", "period": "morning"})
    catalog = options()
    catalog["slots"] = []
    context = {"result": first["result"], "state": first["state"]}
    output = json.loads(dialogue('{"selection":1}', json.dumps(context), json.dumps(catalog))["envelope"])
    assert output["result"]["stage"] != "booking_ready"
    assert not output["result"].get("booking_payload")


def test_booking_workflow_accepts_direct_handover_json() -> None:
    payload = {"action": "hold", "doctor_id": "2", "slot_id": "a-slot", "appointment_id": "", "request_id": "request-a", "patient_token": ""}
    result = booking_input("", "", "", "", "", "patient-credential", json.dumps(payload))
    assert result["valid"] == 1
    body = json.loads(result["body"])
    assert body["patient_token"] == "patient-credential" and body["slot_id"] == "a-slot"


def test_staff_record_is_persistent_complete_and_authenticated(tmp_path: Path) -> None:
    service = BookingService(tmp_path / "medical.db", "signing-key")
    client = create_app(service, "service-key").test_client()
    headers = {"X-Service-Key": "service-key"}
    first = {"event_id": "event-1", "conversation_id": "conversation-1", "user_id": "user-1", "patient_query": "胃疼", "patient_answer": "疼多久了？", "result": {"stage": "ask", "knowledge_ids": ["K03"]}, "knowledge": "K03完整参考", "model_output": "完整模型输出"}
    assert client.post("/api/consultations/record", headers=headers, json=first).json["ok"]
    assert client.post("/api/consultations/record", headers=headers, json=first).json["replayed"]
    second = {**first, "event_id": "event-2", "patient_query": "两天", "patient_answer": "建议挂消化内科。"}
    assert client.post("/api/consultations/record", headers=headers, json=second).json["ok"]
    assert client.get("/api/consultations/conversation-1").status_code == 401
    restored = create_app(BookingService(tmp_path / "medical.db", "signing-key"), "service-key").test_client()
    saved = restored.get("/api/consultations/conversation-1", headers=headers).json
    assert len(saved["turns"]) == 2 and saved["turns"][0]["knowledge"] == "K03完整参考"
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant", "user", "assistant"]


def test_record_rejects_embedded_credentials(tmp_path: Path) -> None:
    client = create_app(BookingService(tmp_path / "medical.db", "signing-key"), "service-key").test_client()
    record = {"event_id": "event-x", "conversation_id": "c", "user_id": "u", "patient_query": "q", "patient_answer": "a", "result": {"booking_payload": {"patient_token": "must-not-be-recorded"}}}
    assert client.post("/api/consultations/record", headers={"X-Service-Key": "service-key"}, json=record).json["code"] == "INVALID_RECORD"


def test_same_selected_slot_keeps_request_id_and_change_generates_new_id() -> None:
    initial = run_dialogue({"registration_type": "ordinary", "date": "2026-10-12", "period": "morning"})
    selected = run_dialogue({"selection": 1}, initial["state"])
    first = json.loads(staff_output(json.dumps(selected), "c", "u", "选1", "run-1")["envelope"])
    repeated = run_dialogue({}, first["state"])
    second = json.loads(staff_output(json.dumps(repeated), "c", "u", "就这个", "run-2")["envelope"])
    assert first["result"]["booking_payload"]["request_id"] == second["result"]["booking_payload"]["request_id"]
    assert json.loads(second["result"]["booking_workflow_inputs"]["booking_payload"])["slot_id"] == "ordinary-am"


def test_malformed_booking_model_or_catalog_yields_no_booking_payload() -> None:
    context = json.dumps({"result": {"stage": "recommend"}, "state": {}})
    for text, catalog in [("not-json", json.dumps(options())), ("{}", "{bad-json")]:
        result = json.loads(dialogue(text, context, catalog)["envelope"])["result"]
        assert result["stage"] == "fallback" and not result.get("booking_payload")
