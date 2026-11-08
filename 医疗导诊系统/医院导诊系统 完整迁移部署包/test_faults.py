"""Temporarily inject draft-only failures, always restore original graphs in finally."""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import app
from extensions.ext_database import db
from models import Account, App
from models.workflow import Workflow
from services.account_service import AccountService

ROOT = Path("/tmp/hospital_guidance")
installed = json.loads((ROOT / "installed.json").read_text())
credential = json.loads((ROOT / "private/service_credentials.json").read_text())
http = requests.Session()
http.trust_env = False
patient_response = http.post("http://medical-guidance:8095/api/demo/session", headers={"X-Service-Key": credential["api_key"]}, json={"patient_id": "DEMO-FAULT"}, timeout=10)
patient_token = patient_response.json()["patient_token"]
records = []
with app.app_context(), Session(db.engine, expire_on_commit=False) as session:
    for case, key, node_id in [("F01 HTTP不可达时写结果未知", "booking", "execute"), ("F02 医生接口不可达不编造医生", "consultation", "catalog"), ("F03 知识不可用转人工导诊", "consultation", "knowledge"), ("F04 记录不可达不交接预约", "consultation", "save_record")]:
        model = session.get(App, installed[key])
        account = session.get(Account, model.created_by)
        account.set_tenant_id(model.tenant_id)
        token = AccountService.get_account_jwt_token(account)
        draft = session.scalar(select(Workflow).where(Workflow.app_id == model.id, Workflow.version == "draft"))
        original = draft.graph
        try:
            graph = json.loads(original)
            node = next(item["data"] for item in graph["nodes"] if item["id"] == node_id)
            if node_id == "knowledge":
                node["dataset_ids"] = []
            else:
                node["url"] = "http://medical-guidance:8096/api/booking/run" if key == "booking" else "http://medical-guidance:8096/api/doctors"
            draft.graph = json.dumps(graph, ensure_ascii=False)
            session.commit()
            path = f"/console/api/apps/{model.id}/workflows/draft/run" if key == "booking" else f"/console/api/apps/{model.id}/advanced-chat/workflows/draft/run"
            payload = {"inputs": {}, "files": []}
            if key == "booking":
                payload["inputs"] = {"booking_payload": json.dumps({"action":"hold","slot_id":"S-02-20990101-0900","request_id":"fault-test"}), "patient_token": patient_token}
            else:
                payload.update(query="35岁，饭后上腹胀痛两天，轻微，无发热、呕血黑便、胸痛、呼吸困难和晕厥，能正常活动", conversation_id=None)
            with app.test_client() as client:
                response = client.post(path, json=payload, headers={"Authorization": "Bearer " + token}, buffered=True)
            events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
            finished = next((event["data"] for event in events if event.get("event") == "workflow_finished"), {})
            if key == "booking":
                result = json.loads(finished.get("outputs", {}).get("result_json", "{}"))
                assert result.get("outcome_unknown") is True and result["next_action"] == "status"
            else:
                answer = "".join(event.get("answer", "") for event in events if event.get("event") == "message")
                conversation = next(event["conversation_id"] for event in events if event.get("conversation_id"))
                saved = http.get("http://medical-guidance:8095/api/consultations/" + conversation + "/latest", headers={"X-Service-Key":credential["api_key"]}, timeout=10).json()
                if node_id == "save_record":
                    assert not saved["ok"] and "繁忙" in answer
                    result = {"stage":"record_unavailable","booking_payload":None}
                else:
                    assert saved["ok"]
                    result = saved["latest"]["result"]
                    assert result["stage"] == "fallback" and not result["recommended_doctors"]
            assert finished.get("status") in ["succeeded", "partial-succeeded"]
            records.append({"case": case, "status": finished.get("status"), "result": result, "passed": True})
            print(json.dumps({"case": case, "passed": True}, ensure_ascii=False), flush=True)
        finally:
            draft.graph = original
            session.commit()
(ROOT / "reports/faults.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
