import json

from nodes.triage_guard import main as guard
from nodes.booking_dialogue import main as dialogue
from test_consultation_v2 import options


def tinnitus(duration: str = "unknown", associated: str = "unknown") -> dict:
    return {"profile": {"age": None, "main_symptom": "耳鸣", "duration": duration, "associated": associated, "red_flags_status": "unknown"}, "summary": "患者耳鸣", "urgency": "routine", "specialty_code": "ent", "department_confidence": "high", "questions": [], "knowledge_ids": ["K09"]}


def test_short_tinnitus_gets_brief_nurse_intake() -> None:
    result = guard(json.dumps(tinnitus()), "{}", 0, "K09")
    assert result["stage"] == "ask" and result["specialty"] == "ent"
    assert json.loads(result["result_json"])["questions"] == ["耳鸣多久了？有没有突然听力下降？"]


def test_known_duration_is_not_asked_again() -> None:
    assert guard(json.dumps(tinnitus("三天", "听力正常")), "{}", 0, "K09")["stage"] == "recommend"


def test_known_hearing_status_is_not_asked_again() -> None:
    result = guard(json.dumps(tinnitus(associated="听力正常")), "{}", 0, "K09")
    assert json.loads(result["result_json"])["questions"] == ["耳鸣多久了？"]


def test_duration_question_is_not_repeated_if_patient_cannot_recall() -> None:
    context = json.dumps({"asked": ["耳鸣多久了？有没有突然听力下降？"], "history": ["耳鸣", "记不清多久，听力正常"]})
    assert guard(json.dumps(tinnitus()), context, 1, "K09")["stage"] == "recommend"


def single_choice(changes: dict, available: bool = True) -> dict:
    catalog = options()
    slot = catalog["slots"][0]
    catalog["slots"] = [slot] if available else []
    state = {"booking_started": True, "preferences": {"date": "2026-10-12", "registration_type": "expert"}, "offered_slots": [slot]}
    context = {"result": {"specialty_code": "digestive", "urgency": "routine"}, "state": state}
    return json.loads(dialogue(json.dumps(changes), json.dumps(context), json.dumps(catalog))["envelope"])


def test_explicit_acceptance_selects_only_candidate_and_accepted_kind() -> None:
    result = single_choice({"intent": "choose", "date": "2026-10-12"})["result"]
    assert result["stage"] == "booking_ready"
    assert result["booking_payload"]["slot_id"] == "ordinary-am"
    assert result["preferences"]["registration_type"] == "ordinary"


def test_single_candidate_is_not_automatically_selected() -> None:
    assert single_choice({"date": "2026-10-12", "intent": "unknown"})["result"]["booking_payload"] is None


def test_confirmation_of_another_date_does_not_select_old_candidate() -> None:
    assert single_choice({"date": "2026-10-13", "intent": "choose"})["result"]["booking_payload"] is None


def test_confirmation_does_not_handover_unavailable_slot() -> None:
    assert single_choice({"intent": "choose"}, available=False)["result"]["booking_payload"] is None
