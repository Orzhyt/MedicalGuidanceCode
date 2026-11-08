import json
from pathlib import Path
import re
import sys

import requests

ROOT = Path("/tmp/hospital_guidance")
keys = json.loads((ROOT / "private/dify_api_keys.json").read_text())
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())

def staff_result(output: dict) -> dict:
    saved = session.get("http://medical-guidance:8095/api/consultations/" + output["conversation_id"] + "/latest", headers={"X-Service-Key": credentials["api_key"]}, timeout=10).json()
    assert saved["ok"] and saved["latest"]["patient_answer"] == output["answer"]
    return saved["latest"]["result"]

session = requests.Session()
session.trust_env = False
cases = [
    ("S01 妇产方向", "女，28岁，月经期间轻微下腹疼一天，偶尔会有，不影响活动，没有怀孕、异常出血、发热、晕厥、呼吸困难或突然剧烈疼痛", 4),
    ("S02 耳鼻喉方向", "32岁，喉咙轻微疼痛两天，没有发热、吞咽困难、流涎、气促或晕厥，能正常吃饭喝水，日常活动正常", 11),
    ("S03 皮肤方向", "30岁，左前臂局部瘙痒红疹三天，轻微，没有发热、黏膜破损、脸部肿胀、气促、晕厥或快速扩散，没有使用新药", 12),
    ("S04 内分泌复诊", "45岁，已确诊糖尿病三年，近一周空腹血糖7.5mmol/L，想复诊评估，轻微口渴，正常活动，没有呕吐、严重乏力、意识异常、胸痛、呼吸困难或晕厥", 5),
]
records = json.loads((ROOT / "reports/specialties_public.json").read_text(encoding="utf-8")) if "--short-only" in sys.argv else []
if "--short-only" in sys.argv:
    cases = []
for case, query, expected in cases:
    response = session.post("http://nginx/v1/chat-messages", headers={"Authorization": "Bearer " + keys["consultation"]}, json={"inputs": {}, "query": query, "conversation_id": "", "response_mode": "blocking", "user": "DEMO-SPECIALTY"}, timeout=120)
    response.raise_for_status()
    output = response.json()
    result = staff_result(output)
    records.append({"case": case, "query": query, "expected_doctor_id": expected, "result": result})
    (ROOT / "reports/specialties_public.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case": case, "stage": result["stage"], "doctors": [doctor["doctor_id"] for doctor in result["recommended_doctors"]]}, ensure_ascii=False), flush=True)
    assert result["stage"] == "recommend" and result["recommended_doctors"][0]["doctor_id"] == expected
print("PASS four additional specialty routes")

conversation = ""
for case, query, expected_stage in [
    ("S05 方向明确不强制补齐字段", "我是35岁男性，饭后上腹胀痛两天，轻微，不影响活动，应该挂什么科？", "recommend"),
    ("S06 只给日期直接列出余号", "后天", "booking_options"),
]:
    response = session.post("http://nginx/v1/chat-messages", headers={"Authorization": "Bearer " + keys["consultation"]}, json={"inputs": {}, "query": query, "conversation_id": conversation, "response_mode": "blocking", "user": "DEMO-SHORT-REPLY"}, timeout=120)
    response.raise_for_status()
    output = response.json()
    conversation = output["conversation_id"]
    result = staff_result(output)
    records.append({"case": case, "query": query, "result": result})
    (ROOT / "reports/specialties_public.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["stage"] == expected_stage
    if expected_stage == "recommend":
        assert result["recommended_doctors"][0]["doctor_id"] in [2, 13]
print("PASS short negative reply with preserved context")
