import json
import re

ALLOWED = {"general", "digestive", "respiratory", "neurology", "orthopedic", "gynecology", "pediatrics", "ent", "dermatology", "endocrine", "cardiac", "surgery"}


def main(text: str, context: str, rounds: float, knowledge: str) -> dict:
    previous = json.loads(context)
    state = {"history": previous.get("history", []), "asked": list(previous.get("asked", []))[-9:], "phase": "clinical"}
    count = int(rounds or 0)
    result = {"schema_version": "1.0", "stage": "fallback", "summary": "目前的信息还不够判断该挂什么科", "questions": [], "recommended_doctors": [], "knowledge_ids": [], "message": "暂时没法根据这些信息判断该挂什么科，建议到医院导诊台咨询，或先挂全科门诊。"}
    try:
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        model = json.loads(cleaned)
        if not isinstance(model, dict):
            raise ValueError("Expected object")
        fields = model.get("profile")
        if not isinstance(fields, dict) or model.get("urgency") not in ["emergency", "urgent", "routine", "undetermined"]:
            raise ValueError("Invalid profile or urgency")
        age = fields.get("age")
        if isinstance(age, str) and re.fullmatch(r"\d{1,3}(?:岁)?", age.strip()):
            age = int(age.strip().removesuffix("岁"))
            fields["age"] = age
        if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 <= age <= 120):
            raise ValueError("Invalid age")
        specialty = model.get("specialty_code", "general")
        if specialty not in ALLOWED:
            specialty = "general"
        ids = set(re.findall(r"K\d{2}", knowledge))
        cited = [value for value in model.get("knowledge_ids", []) if value in ids]
        summary = str(model.get("summary", ""))[:500]
        if not summary:
            raise ValueError("Missing summary")
        urgency = model["urgency"]
        red_flags = fields.get("red_flags_status", "unknown")
        if red_flags not in ["present", "absent", "unknown"]:
            red_flags = "unknown"
        if red_flags == "absent" and previous.get("history"):
            history = "。".join(str(item) for item in previous["history"])
            explicit_negative = bool(re.search(r"(?:没有|否认|不伴|无).{0,100}(?:胸痛|呼吸困难|气促|晕|出血|剧烈|危险|上述|这些)", history))
            answered_screening = bool(previous.get("asked") and re.search(r"(?:上述|这些|危险).{0,10}(?:都没有|没有|均无)", history))
            retained_negative = previous.get("profile", {}).get("red_flags_status") == "absent"
            if not (explicit_negative or answered_screening or retained_negative):
                red_flags = "unknown"
                fields["red_flags_status"] = "unknown"
        state.update(profile=fields, summary=summary, specialty_code=specialty)
        questions = [str(question)[:60] for question in model.get("questions", []) if isinstance(question, str) and question.strip()][:2]
        questions = list(dict.fromkeys(questions))
        confidence = model.get("department_confidence", "medium")
        if confidence not in ["high", "medium", "low"]:
            confidence = "low"
        complaint = str(fields.get("main_symptom") or "").strip()
        clear_direction = specialty != "general" and confidence in ["high", "medium"] and complaint not in ["", "unknown", "未知", "不详", "不舒服", "身体不舒服", "未提供"]
        purpose = model.get("question_purpose", "none")
        duration = str(fields.get("duration") or "unknown").strip().lower()
        duration_unknown = duration in ["", "unknown", "未知", "不详", "未提供"]
        duration_asked = any(re.search(r"多久|多长时间|什么时候|几天", item) for item in state["asked"])
        basic_question = ""
        if clear_direction and duration_unknown and not duration_asked and count < 3 and urgency not in ["emergency", "urgent"] and "复诊" not in complaint:
            basic_question = "这种不舒服持续多久了？"
            if specialty == "ent" and re.search(r"耳鸣|嗡嗡|耳朵.*响", complaint):
                associated = str(fields.get("associated") or "")
                hearing_known = bool(re.search(r"听力|听不见|耳聋", associated))
                basic_question = "耳鸣多久了？" if hearing_known else "耳鸣多久了？有没有突然听力下降？"
        if urgency == "emergency" or (red_flags == "present" and urgency != "urgent"):
            stage = "emergency"
            questions = []
        elif not ids or not cited:
            stage = "fallback"
            questions = []
        elif basic_question:
            stage = "ask"
            questions = [basic_question]
            count += 1
            state["asked"] = (state["asked"] + questions)[-9:]
        elif clear_direction and not (questions and purpose == "urgency" and urgency == "undetermined"):
            stage = "recommend"
            questions = []
        elif count < 3 and (questions or not complaint or any(word in complaint for word in ["unknown", "未知", "不舒服", "说不清", "未提供"])):
            stage = "ask"
            questions = (questions or ["主要哪里不舒服？"] )[:1]
            count += 1
            state["asked"] = (state["asked"] + questions)[-9:]
        else:
            stage = "recommend"
            questions = []
            if not clear_direction:
                specialty = "general"
                confidence = "low"
        # Localized specialist complaints retain their department; age is not a universal gate.
        if stage == "recommend" and specialty == "respiratory" and age is not None and age < 14:
            specialty = "pediatrics"
        state.update(stage=stage, specialty_code=specialty, rounds=count)
        if stage == "recommend":
            state["phase"] = "booking_preferences"
        elif stage == "emergency":
            state["phase"] = "emergency"
        result.update(stage=stage, summary=summary, profile=fields, questions=questions, specialty_code=specialty,
                      urgency=urgency, knowledge_ids=cited, rationale=str(model.get("rationale", ""))[:500],
                      possible_causes=[], department_confidence=confidence)
        if stage == "emergency":
            result["message"] = "请尽快去就近医院急诊，必要时拨打120，不要等待门诊预约。"
        elif stage == "ask":
            result["message"] = ""
        elif stage == "recommend":
            result["message"] = "" if urgency != "urgent" else "建议今天到医院就诊，不要等几天后的预约。"
    except (ValueError, TypeError, KeyError):
        state["stage"] = "fallback"
    result.update(_state=state, _model_output=text, _knowledge=knowledge)
    return {"stage": result["stage"], "profile_json": json.dumps(state, ensure_ascii=False), "rounds": count,
            "result_json": json.dumps(result, ensure_ascii=False), "specialty": result.get("specialty_code", "general")}


