import json
from datetime import datetime

LABELS = {"general": "全科门诊", "digestive": "消化内科", "respiratory": "呼吸内科", "neurology": "神经内科", "orthopedic": "骨科", "gynecology": "妇产科", "pediatrics": "儿科", "ent": "耳鼻喉科", "dermatology": "皮肤科", "endocrine": "内分泌科", "cardiac": "心血管内科", "surgery": "外科"}
ENUMS = {"registration_type": {"expert", "ordinary", "any", "unknown"}, "period": {"morning", "afternoon", "any", "unknown"}, "priority": {"type", "time", "doctor", "flexible", "unknown"}}


def date_of(slot: dict) -> str:
    return slot["starts_at"][:10]


def period_of(slot: dict) -> str:
    return "morning" if int(slot["starts_at"][11:13]) < 12 else "afternoon"


def matches(slot: dict, prefs: dict, ignore: set | None = None) -> bool:
    ignored = ignore or set()
    values = {"registration_type": slot["registration_type"], "period": period_of(slot), "date": date_of(slot), "doctor_id": slot["doctor_id"]}
    return all(key in ignored or not prefs.get(key) or prefs[key] in ["unknown", "any"] or prefs[key] == value for key, value in values.items())


def slot_text(slot: dict) -> str:
    moment = datetime.fromisoformat(slot["starts_at"])
    period = "上午" if moment.hour < 12 else "下午"
    kind = "专家号" if slot["registration_type"] == "expert" else "普通号"
    hospital = str(slot.get("hospital_name", "")).split("市")[-1]
    venue = "，" + hospital if hospital else ""
    return f"{moment.month}月{moment.day}日{period}{moment:%H:%M}，{slot['doctor_name']}，{kind}{venue}"


def diverse_slots(slots: list[dict]) -> list[dict]:
    chosen = []
    groups = set()
    for slot in slots:
        group = (date_of(slot), period_of(slot), slot["registration_type"], slot["doctor_id"])
        if group not in groups:
            chosen.append(slot)
            groups.add(group)
        if len(chosen) == 3:
            break
    for slot in slots:
        if len(chosen) == 3:
            break
        if not any(item["slot_id"] == slot["slot_id"] for item in chosen):
            chosen.append(slot)
    return chosen


def main(text: str, context: str, options_body: str) -> dict:
    source = json.loads(context)
    state = source.get("state", {})
    clinical = dict(source.get("result", state.get("clinical_result", {})))
    clinical.pop("booking_payload", None)
    state["clinical_result"] = dict(clinical)
    first_booking_turn = not state.get("booking_started")
    state["booking_started"] = True
    result = dict(clinical)
    result["schema_version"] = "2.0"
    result["booking_payload"] = None
    result["questions"] = []
    answer = ""
    catalog = {}
    try:
        changes = json.loads(text)
        if not isinstance(changes, dict):
            raise ValueError("偏好格式不正确")
        catalog = json.loads(options_body)
        if not catalog.get("ok"):
            raise ValueError("无法查询档期")
        doctors = catalog["doctors"]
        doctor_ids = {doctor["id"] for doctor in doctors}
        result["recommended_doctors"] = [{"doctor_id": doctor["id"], "doctor_name": doctor["name"], **doctor} for doctor in doctors]
        specialty = result.get("specialty_code", state.get("specialty_code", "general"))
        result["department_recommendation"] = LABELS.get(specialty, "全科门诊")
        intro = ""
        if first_booking_turn:
            intro = "建议挂" + result["department_recommendation"] + "。"
            if specialty == "general":
                intro = "暂时难判断具体科室，建议先挂全科门诊，再由医生分诊。"
        prefs = dict(state.get("preferences", {}))
        if changes.get("intent") == "change":
            state.pop("selected_slot", None)
        for key, choices in ENUMS.items():
            if changes.get(key) in choices and changes[key] != "unknown":
                prefs[key] = changes[key]
        date = changes.get("date")
        if date == "any":
            prefs["date"] = date
        elif isinstance(date, str) and len(date) == 10:
            datetime.strptime(date, "%Y-%m-%d")
            prefs["date"] = date
        doctor = changes.get("doctor_id")
        if isinstance(doctor, int) and not isinstance(doctor, bool):
            if doctor == 0:
                prefs.pop("doctor_id", None)
            elif doctor in doctor_ids:
                prefs["doctor_id"] = doctor
            else:
                answer = "这个科室没有您说的医生，请换一位或让我列出可选医生。"
        state["preferences"] = prefs
        result["preferences"] = prefs
        slots = [slot for slot in catalog["slots"] if slot["doctor_id"] in doctor_ids]
        slots.sort(key=lambda slot: (slot["starts_at"], slot["doctor_id"]))
        if result.get("urgency") == "urgent":
            answer = "建议今天到医院就诊，先到导诊台咨询，不要等之后的预约。"
            result["stage"] = "urgent"
        elif changes.get("intent") == "stop":
            answer = "好的，先不预约。需要时可以继续告诉我想选的医生和时间。"
            result["stage"] = "booking_preferences"
            state.pop("selected_slot", None)
        elif not answer:
            selection = changes.get("selection")
            offered = state.get("offered_slots", [])
            selected = None
            lost_slot = False
            if selection is None and changes.get("intent") == "choose" and len(offered) == 1:
                explicit_changes = {key: prefs[key] for key in ["date", "period", "registration_type", "doctor_id"] if key in changes and key in prefs and changes[key] not in [None, "unknown"]}
                if matches(offered[0], explicit_changes):
                    selection = 1
            if isinstance(selection, int) and not isinstance(selection, bool) and 1 <= selection <= len(offered):
                wanted = offered[selection - 1]
                selected = next((slot for slot in slots if slot["slot_id"] == wanted["slot_id"]), None)
                lost_slot = selected is None
                explicit_changes = {key: prefs[key] for key in ["date", "period", "registration_type", "doctor_id"] if key in changes and key in prefs and changes[key] not in [None, "unknown"]}
                if selected and not matches(selected, explicit_changes):
                    selected = None
            elif state.get("selected_slot") and changes.get("intent") != "change" and not any(key in changes and changes[key] not in [None, "unknown"] for key in ["date", "period", "registration_type", "doctor_id"]):
                selected = next((slot for slot in slots if slot["slot_id"] == state["selected_slot"]["slot_id"]), None)
                lost_slot = selected is None
            if selected:
                if state.get("selected_slot", {}).get("slot_id") != selected["slot_id"]:
                    state.pop("handover_request_id", None)
                state["selected_slot"] = selected
                prefs.update(registration_type=selected["registration_type"], date=date_of(selected), period=period_of(selected), doctor_id=selected["doctor_id"])
                result.update(stage="booking_ready", selected_slot=selected, preferences=prefs,
                              booking_payload={"action": "hold", "doctor_id": str(selected["doctor_id"]), "slot_id": selected["slot_id"], "appointment_id": "", "request_id": "", "patient_token": ""})
                answer = "已选" + slot_text(selected) + "。请点击“预约”提交，提交后才会保留号源。"
            else:
                state.pop("selected_slot", None)
                state.pop("handover_request_id", None)
                result.pop("selected_slot", None)
                missing = ["想哪天就诊？"] if prefs.get("date", "unknown") == "unknown" else []
                if missing and not lost_slot:
                    result["stage"] = "recommend" if first_booking_turn else "booking_preferences"
                    result["questions"] = missing[:2]
                    answer = intro + "".join(missing[:2])
                    state["offered_slots"] = []
                else:
                    exact = [slot for slot in slots if matches(slot, prefs)]
                    candidates = diverse_slots(exact)
                    changed = not candidates
                    if changed:
                        priority = prefs.get("priority", "unknown")
                        if priority in ["type", "doctor"]:
                            candidates = [slot for slot in slots if matches(slot, prefs, {"date", "period"})][:3]
                        elif priority == "time":
                            candidates = [slot for slot in slots if matches(slot, prefs, {"registration_type", "doctor_id"})][:3]
                        else:
                            keep_type = [slot for slot in slots if matches(slot, prefs, {"date", "period"})]
                            keep_time = [slot for slot in slots if matches(slot, prefs, {"registration_type", "doctor_id"})]
                            candidates = ([keep_type[0]] if keep_type else []) + ([keep_time[0]] if keep_time else [])
                            same_period = [slot for slot in keep_type if period_of(slot) == prefs.get("period")]
                            candidates += same_period[:1]
                            seen = set()
                            candidates = [slot for slot in candidates if not (slot["slot_id"] in seen or seen.add(slot["slot_id"]))][:3]
                            if not candidates:
                                candidates = slots[:3]
                    state["offered_slots"] = candidates
                    result.update(stage="booking_options", options=candidates, exact_match=not changed)
                    if candidates:
                        prefix = "这个时段刚被占用了，请再选：" if lost_slot else ("这个科室目前只有普通号，可选：" if prefs.get("registration_type") == "expert" and not any(doctor.get("registration_type") == "expert" for doctor in doctors) else "符合要求的号已约满，可改选：") if changed else "可选："
                        answer = intro + prefix + "\n" + "\n".join(f"{index}. {slot_text(slot)}" for index, slot in enumerate(candidates, 1)) + "\n您选哪个？也可以调整号别或时间。"
                    else:
                        answer = intro + "暂时没有符合要求的号源。要换个日期，还是调整普通号／专家号？"
        if answer and "stage" not in result:
            result["stage"] = "booking_preferences"
        if result.get("stage") == "recommend" and not first_booking_turn:
            result["stage"] = "booking_preferences"
    except (ValueError, KeyError, TypeError):
        result.update(stage="fallback", recommended_doctors=[], booking_payload=None)
        answer = "暂时查不到可预约时段，请稍后再试或联系导诊台。"
    state["phase"] = "booking_preferences" if result.get("stage") == "recommend" else result.get("stage", "booking_preferences")
    state["stage"] = result.get("stage", "booking_preferences")
    envelope = {"answer": answer, "result": result, "state": state, "model_output": {"clinical": source.get("model_output", ""), "booking": text}, "knowledge": source.get("knowledge", ""), "availability": catalog}
    return {"envelope": json.dumps(envelope, ensure_ascii=False)}
