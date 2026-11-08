"""Trusted-backend integration example. Keys stay in environment variables, never in a browser."""
from __future__ import annotations

import json
import os

import requests


class MedicalClient:
    def __init__(self) -> None:
        self.dify_url = os.environ.get("DIFY_BASE_URL", "http://127.0.0.1/v1").rstrip("/")
        self.medical_url = os.environ.get("MEDICAL_BASE_URL", "http://127.0.0.1:8095").rstrip("/")
        self.consultation_key = os.environ["DIFY_CONSULTATION_KEY"]
        self.booking_key = os.environ["DIFY_BOOKING_KEY"]
        self.service_key = os.environ["MEDICAL_SERVICE_KEY"]
        self.http = requests.Session()
        self.http.trust_env = False

    def patient_session(self, patient_id: str) -> str:
        response = self.http.post(self.medical_url + "/api/patients/session", headers={"X-Service-Key": self.service_key}, json={"patient_id": patient_id}, timeout=10)
        response.raise_for_status()
        return response.json()["patient_token"]

    def consult(self, user: str, query: str, conversation_id: str = "") -> tuple[str, str]:
        response = self.http.post(self.dify_url + "/chat-messages", headers={"Authorization": "Bearer " + self.consultation_key}, json={"inputs": {}, "query": query, "response_mode": "blocking", "conversation_id": conversation_id, "user": user}, timeout=120)
        response.raise_for_status()
        output = response.json()
        return output["conversation_id"], output["answer"]

    def staff_record(self, conversation_id: str, latest: bool = True) -> dict[str, object]:
        # 调用方先验证工作人员权限或当前患者对话归属。
        response = self.http.get(self.medical_url + "/api/consultations/" + conversation_id + ("/latest" if latest else ""), headers={"X-Service-Key": self.service_key}, timeout=10)
        response.raise_for_status()
        return response.json()

    def submit_selected(self, user: str, conversation_id: str, patient_token: str) -> dict[str, object]:
        # 患者明确提交后调用；当前前台约定使用预约按钮，user须对应登录身份。
        saved = self.staff_record(conversation_id)
        if not saved.get("ok") or saved["latest"]["user_id"] != user:
            raise ValueError("无权访问该对话或记录不可用")
        inputs = dict(saved["latest"]["result"].get("booking_workflow_inputs") or {})
        if not inputs:
            raise ValueError("尚未选定可预约时段")
        inputs["patient_token"] = patient_token
        return self.run_booking_inputs(user, inputs)

    def booking(self, user: str, action: str, patient_token: str = "", **fields: str) -> dict[str, object]:
        inputs = {"booking_payload": json.dumps({"action": action, **fields}, ensure_ascii=False), "patient_token": patient_token}
        return self.run_booking_inputs(user, inputs)

    def run_booking_inputs(self, user: str, inputs: dict[str, str]) -> dict[str, object]:
        # The caller supplies request_id for mutations and retains it until the result is known.
        response = self.http.post(self.dify_url + "/workflows/run", headers={"Authorization": "Bearer " + self.booking_key}, json={"inputs": inputs, "response_mode": "blocking", "user": user}, timeout=60)
        response.raise_for_status()
        output = response.json()["data"]
        if output["status"] != "succeeded":
            raise RuntimeError("Workflow incomplete; verify mutation outcome with the original request_id")
        return json.loads(output["outputs"]["result_json"])


if __name__ == "__main__":
    client = MedicalClient()
    user = "PATIENT-CLIENT"
    conversation, result = client.consult(user, "35岁，饭后上腹轻微胀痛两天，无呕血黑便、胸痛、呼吸困难和晕厥，能正常活动")
    print(json.dumps({"conversation_id": conversation, "guidance": result}, ensure_ascii=False, indent=2))
    # Demonstrate read-only slot lookup. Hold and confirm must follow actual user button actions.
    print(json.dumps(client.booking(user, "query", doctor_id="2"), ensure_ascii=False, indent=2))
