import json
from datetime import datetime, timedelta, timezone

STATUS_NAMES = {"held": "等待确认", "confirmed": "预约成功", "cancelled": "已取消", "expired": "保留已过期"}


def readable_time(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8)))
        return f"{moment.month}月{moment.day}日{moment:%H:%M}"
    except (ValueError, TypeError):
        return "时间待核实"


def main(body: str, action: str, request_id: str) -> dict:
    try:
        result = json.loads(body)
        if not isinstance(result, dict) or "ok" not in result or "code" not in result:
            raise ValueError("结果格式不完整")
    except (ValueError, TypeError):
        result = {"ok": False, "code": "RESULT_UNKNOWN", "message": "正在核实办理结果，请勿重复提交。"}
    result.update(schema_version="2.0", action=action, request_id=request_id)
    if result["code"] in ["TRANSPORT_UNKNOWN", "RESULT_UNKNOWN", "RETRYABLE_DB_BUSY"]:
        result["outcome_unknown"] = True
        result["next_action"] = "status" if action in ["hold", "confirm", "cancel"] else "retry"
        result["message"] = "正在核实办理结果，请勿重复提交。"
    messages = {"HELD": "已为您保留号源，请在3分钟内确认。", "CONFIRMED": "预约成功。", "CANCELLED": "预约已取消。", "ALREADY_RELEASED": "该预约已释放。", "HOLD_EXPIRED": "保留时间已过，预约尚未成功，请重新选择。", "SLOT_TAKEN": "这个时段已约满，请选择其他时段。", "PATIENT_TIME_CONFLICT": "这个时间与您的其他预约冲突，请换个时间。", "EXISTING_APPOINTMENT": "您已保留或预约这个时段，不需要重复提交。"}
    message = messages.get(result["code"], str(result.get("message", "")))
    appointment = result.get("appointment")
    if appointment:
        message += f"\n{readable_time(appointment['starts_at'])}，{appointment['doctor_name']}，{appointment['hospital_name']}。"
        if result["code"] == "APPOINTMENT_STATUS":
            message += "\n当前状态：" + STATUS_NAMES.get(appointment["status"], "待核实") + "。"
        if appointment["status"] == "held":
            message += "\n请在" + readable_time(appointment["expires_at"]) + "前确认。"
    if result.get("alternatives"):
        message += "\n还可选择：\n" + "\n".join(f"{index}. {readable_time(slot['starts_at'])}，{slot['doctor_name']}" for index, slot in enumerate(result["alternatives"][:3], 1))
    if result.get("code") == "SLOTS":
        message = "已查到可预约时段，请选择医生和时间。" if result.get("slots") else "暂时没有可预约时段，请换个日期或医生。"
    if result.get("code") == "APPOINTMENTS":
        message = "您的预约：\n" + "\n".join(f"{readable_time(item['starts_at'])}，{item['doctor_name']}，{STATUS_NAMES.get(item['status'], '待核实')}" for item in result.get("appointments", [])[:5]) if result.get("appointments") else "您目前没有预约记录。"
    return {"result_json": json.dumps(result, ensure_ascii=False), "message": message, "code": str(result["code"]), "ok": 1 if result["ok"] else 0}
