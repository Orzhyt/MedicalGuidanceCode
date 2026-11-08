import json


def main(merged: str, context: str) -> dict:
    if merged:
        result = json.loads(merged)
        state = result.pop("_state", {})
        source = {"result": result, "state": state, "model_output": result.pop("_model_output", ""), "knowledge": result.pop("_knowledge", "")}
    else:
        state = json.loads(context)
        source = {"result": state.get("clinical_result", {}), "state": state, "model_output": "", "knowledge": ""}
    return {"context": json.dumps(source, ensure_ascii=False), "specialty": source["result"].get("specialty_code", state.get("specialty_code", "general"))}
