import json

LABELS = {"general": "全科门诊", "digestive": "消化内科", "respiratory": "呼吸内科", "neurology": "神经内科", "orthopedic": "骨科", "gynecology": "妇产科", "pediatrics": "儿科", "ent": "耳鼻喉科", "dermatology": "皮肤科", "endocrine": "内分泌科", "cardiac": "心血管内科", "surgery": "外科"}


def main(result_json: str, catalog_body: str) -> dict:
    result = json.loads(result_json)
    state = result.pop("_state", {})
    model_output = result.pop("_model_output", "")
    knowledge = result.pop("_knowledge", "")
    result.pop("simulation", None)
    stage = result["stage"]
    if stage == "ask":
        answer = "\n".join(result.get("questions", [])[:2]) or "患者多大年龄，哪里不舒服？"
    elif stage == "emergency":
        answer = "请尽快去就近医院急诊，必要时拨打120，不要等待门诊预约。"
    elif stage == "reset":
        answer = "好的，请说说患者多大年龄、哪里不舒服。"
    elif stage == "recommend":
        try:
            catalog = json.loads(catalog_body)
            if not catalog.get("ok"):
                raise ValueError("无法获取医生名单")
            specialty = result.get("specialty_code", "general")
            doctors = [doctor for doctor in catalog["doctors"] if doctor["specialty_code"] == specialty]
            result["recommended_doctors"] = doctors
            result["department_recommendation"] = LABELS[specialty]
            answer = "建议先挂" + LABELS[specialty] + "。"
            if specialty == "general":
                answer = "暂时不容易判断具体科室，建议先挂全科门诊，再由医生分诊。"
        except (ValueError, KeyError, TypeError):
            result.update(stage="fallback", recommended_doctors=[])
            answer = "暂时查不到医生名单，请稍后再试或联系医院导诊台。"
    else:
        answer = "暂时无法判断具体科室，建议先挂全科门诊或到导诊台咨询。"
    state.update(stage=result["stage"], rounds=state.get("rounds", 0))
    envelope = {"answer": answer, "result": result, "state": state, "model_output": model_output, "knowledge": knowledge}
    return {"answer": answer, "result_json": json.dumps(result, ensure_ascii=False), "envelope": json.dumps(envelope, ensure_ascii=False)}
