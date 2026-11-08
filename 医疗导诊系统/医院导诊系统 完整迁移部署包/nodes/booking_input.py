import json
import re


def main(action: str = "", doctor_id: str = "", slot_id: str = "", appointment_id: str = "", request_id: str = "", patient_token: str = "", booking_payload: str = "") -> dict:
    if booking_payload:
        try:
            handover = json.loads(booking_payload)
            allowed = {"action", "doctor_id", "slot_id", "appointment_id", "request_id", "patient_token"}
            if not isinstance(handover, dict) or set(handover) - allowed:
                raise ValueError("预约参数格式不正确")
            manual = {"action": action, "doctor_id": doctor_id, "slot_id": slot_id, "appointment_id": appointment_id, "request_id": request_id}
            if any(value and str(handover.get(key, value)) != value for key, value in manual.items()):
                raise ValueError("预约参数和手工填写的内容不一致")
            action = str(handover.get("action") or action or "")
            doctor_id = str(handover.get("doctor_id") or doctor_id or "")
            slot_id = str(handover.get("slot_id") or slot_id or "")
            appointment_id = str(handover.get("appointment_id") or appointment_id or "")
            request_id = str(handover.get("request_id") or request_id or "")
            embedded_token = str(handover.get("patient_token") or "")
            if patient_token and embedded_token and patient_token != embedded_token:
                raise ValueError("患者凭证不一致")
            patient_token = patient_token or embedded_token
        except (ValueError, TypeError):
            return {"valid": 0, "body": "{}", "error_json": json.dumps({"ok": False, "code": "INVALID_INPUT", "message": "预约参数格式不正确或与手工输入不一致。"}, ensure_ascii=False), "action": "", "request_id": ""}
    actions = {"query", "hold", "confirm", "cancel", "status", "list"}
    error = ""
    if action not in actions:
        error = "请选择查询档期、申请占位、确认预约、取消预约、查询状态或查看我的预约（对应query、hold、confirm、cancel、status、list）。"
    elif action != "query" and not patient_token:
        error = "请填写后端生成的患者凭证（patient_token）。"
    elif action in {"hold", "confirm", "cancel"} and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id or ""):
        error = "请填写本次操作的请求编号（request_id）。如果是重试，请使用上次的编号。"
    elif action == "hold" and not slot_id:
        error = "请填写要预约的时段编号（slot_id），可先查询医生档期获取。"
    elif action in {"confirm", "cancel"} and not appointment_id:
        error = "请填写预约编号（appointment_id），可从占位结果中获取。"
    elif action == "status" and not appointment_id and not request_id:
        error = "请填写预约编号（appointment_id）或上次操作的请求编号（request_id）。"
    elif doctor_id and not re.fullmatch(r"\d{1,5}", doctor_id):
        error = "医生编号（doctor_id）请只填数字，例如2。"
    payload = {"action": action, "doctor_id": int(doctor_id or 0) if not error else 0, "slot_id": slot_id or "", "appointment_id": appointment_id or "", "request_id": request_id or "", "patient_token": patient_token or ""}
    return {"valid": 0 if error else 1, "body": json.dumps(payload, ensure_ascii=False), "error_json": json.dumps({"ok": False, "code": "INVALID_INPUT", "message": error}, ensure_ascii=False), "action": action, "request_id": request_id}
