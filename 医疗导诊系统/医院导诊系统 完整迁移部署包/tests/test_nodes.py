import json

from nodes.booking_input import main as booking_input
from nodes.booking_output import main as booking_output
from nodes.precheck import main as precheck
from nodes.render_consultation import main as render
from nodes.triage_guard import main as guard


def model(**extra: object) -> dict:
    result = {"profile": {"age": 35, "main_symptom": "上腹胀痛", "location": "上腹", "duration": "2天", "severity": "轻微", "red_flags_status": "absent"}, "summary": "35岁上腹胀痛两天", "urgency": "routine", "specialty_code": "digestive", "questions": [], "knowledge_ids": ["K03"], "rationale": "消化方向", "possible_causes": ["消化系统不适"]}
    result.update(extra)
    return result


def test_model_output_error_degrades_without_diagnosis() -> None:
    for text in ["not-json", "{}", "[]", '{"profile":null}']:
        result = guard(text, "{}", 0, "K03")
        assert result["stage"] == "fallback"


def test_missing_knowledge_and_fabricated_citation_degrade() -> None:
    assert guard(json.dumps(model()), "{}", 0, "未命中知识")["stage"] == "fallback"
    assert guard(json.dumps(model(knowledge_ids=["K99"])), "{}", 0, "K03 腹痛")["stage"] == "fallback"


def test_no_catalog_never_invents_physician() -> None:
    result = guard(json.dumps(model()), "{}", 0, "K03")
    output = json.loads(render(result["result_json"], "bad response")["result_json"])
    assert output["stage"] == "fallback" and not output["recommended_doctors"]


def test_unknown_age_or_danger_signs_are_not_assumed_absent() -> None:
    output = model()
    output["profile"].update(age=None, red_flags_status="unknown")
    result = guard(json.dumps(output), "{}", 0, "K03")
    assert result["stage"] == "recommend" and result["rounds"] == 0
    assert json.loads(result["result_json"])["profile"]["red_flags_status"] == "unknown"


def test_three_question_round_limit_preserves_clear_department() -> None:
    output = model(questions=["还需要一些信息？"])
    result = guard(json.dumps(output), "{}", 3, "K03")
    assert result["stage"] == "recommend" and result["specialty"] == "digestive"


def test_danger_negation_and_context_reset() -> None:
    result = precheck("胸痛冒冷汗，喘不过气", "{}", 0)
    assert result["action"] == "emergency"
    remembered = precheck("现在想预约", result["early_state"], 0)
    assert remembered["action"] == "emergency"
    reset = precheck("重新咨询", result["early_state"], 3)
    assert reset["action"] == "reset" and reset["early_state"] == "{}" and reset["rounds"] == 0
    assert precheck("没有胸痛、呼吸困难和晕厥", "{}", 0)["action"] == "continue"
    assert precheck("没有胸痛，但是喘不过气", "{}", 0)["action"] == "emergency"


def test_early_danger_after_none_is_not_suppressed() -> None:
    assert precheck("没有发热，但突然剧烈头痛", "{}", 0)["action"] == "emergency"
    assert precheck("没有外伤、发热、颈部僵硬、肢体无力、说话异常或晕厥", "{}", 0)["action"] == "continue"
    assert precheck("无法呼吸", "{}", 0)["action"] == "emergency"


def test_network_failure_is_unknown_outcome_not_booking_failure() -> None:
    result = booking_output('{"ok":false,"code":"TRANSPORT_UNKNOWN","message":"网络中断"}', "hold", "request-a")
    parsed = json.loads(result["result_json"])
    assert parsed["outcome_unknown"] and parsed["next_action"] == "status"
    parsed = json.loads(booking_output("<html>error</html>", "confirm", "request-a")["result_json"])
    assert parsed["code"] == "RESULT_UNKNOWN" and parsed["outcome_unknown"]


def test_natural_language_is_not_a_booking_operation() -> None:
    assert booking_input("我要预约随便一个医生", "", "", "", "", "")["valid"] == 0


def test_danger_model_never_produces_regular_doctor_recommendation() -> None:
    result = guard(json.dumps(model(urgency="emergency")), "{}", 0, "K02 K03")
    parsed = json.loads(render(result["result_json"], "{}")["result_json"])
    assert parsed["stage"] == "emergency" and not parsed["recommended_doctors"]
