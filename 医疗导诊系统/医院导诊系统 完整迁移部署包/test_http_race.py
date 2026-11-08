"""Twenty concurrent HTTP clients contend for one persisted slot."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid

import requests

ROOT = Path("/tmp/hospital_guidance")
credentials = json.loads((ROOT / "private/service_credentials.json").read_text())
headers = {"X-Service-Key": credentials["api_key"]}
base = "http://medical-guidance:8095"


def post(path: str, payload: dict) -> dict:
    session = requests.Session()
    session.trust_env = False
    response = session.post(base + path, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


target = post("/api/booking/run", {"action": "query", "doctor_id": 13})["slots"][0]["slot_id"]
run = uuid.uuid4().hex[:8]
tokens = [post("/api/demo/session", {"patient_id": f"DEMO-HTTP-{run}-{index}"})["patient_token"] for index in range(20)]
with ThreadPoolExecutor(max_workers=20) as pool:
    responses = list(pool.map(lambda index: post("/api/booking/run", {"action": "hold", "slot_id": target, "request_id": f"http-{run}-{index}", "patient_token": tokens[index]}), range(20)))
winners = [index for index, response in enumerate(responses) if response["ok"]]
assert len(winners) == 1
assert all(response["code"] == "SLOT_TAKEN" and response["alternatives"] for response in responses if not response["ok"])
winner = winners[0]
post("/api/booking/run", {"action": "cancel", "appointment_id": responses[winner]["appointment"]["appointment_id"], "request_id": f"cancel-{run}", "patient_token": tokens[winner]})
(ROOT / "reports/http_race.json").write_text(json.dumps({"passed": True, "concurrent_clients": 20, "successes": 1, "conflicts": 19, "slot_id": target, "responses": responses}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": True, "concurrent_clients": 20, "successes": 1, "conflicts": 19}))
