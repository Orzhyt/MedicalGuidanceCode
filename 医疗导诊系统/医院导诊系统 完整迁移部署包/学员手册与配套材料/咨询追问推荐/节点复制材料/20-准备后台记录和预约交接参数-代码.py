import json


def main(envelope: str, conversation_id: str, user_id: str, query: str, run_id: str) -> dict:
    source = json.loads(envelope)
    result = source["result"]
    if result.get("booking_payload"):
        request_id = source["state"].get("handover_request_id") or "H-" + run_id.replace("-", "")
        source["state"]["handover_request_id"] = request_id
        result["booking_payload"]["request_id"] = request_id
        result["booking_workflow_inputs"] = {"booking_payload": json.dumps(result["booking_payload"], ensure_ascii=False), "patient_token": ""}
    record = {"event_id": run_id, "conversation_id": conversation_id, "user_id": user_id, "patient_query": query, "patient_answer": source["answer"], "result": result, "state": source["state"], "knowledge": source.get("knowledge", ""), "model_output": source.get("model_output", ""), "availability": source.get("availability", {})}
    return {"body": json.dumps(record, ensure_ascii=False), "envelope": json.dumps(source, ensure_ascii=False)}
