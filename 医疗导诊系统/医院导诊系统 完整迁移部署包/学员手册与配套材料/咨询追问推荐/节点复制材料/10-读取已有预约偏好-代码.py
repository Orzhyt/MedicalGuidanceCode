import json

def main(context: str)->dict:
    state=json.loads(context)
    result=dict(state.get("clinical_result", {}))
    result["_state"]=state
    return {"result_json":json.dumps(result,ensure_ascii=False)}
