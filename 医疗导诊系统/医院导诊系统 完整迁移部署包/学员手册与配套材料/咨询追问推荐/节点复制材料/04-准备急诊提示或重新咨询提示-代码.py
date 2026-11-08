import json

def main(action: str, state_json: str, rounds: float) -> dict:
    stage = "reset" if action == "reset" else "emergency"
    state = {} if stage == "reset" else json.loads(state_json)
    state.update(stage=stage, phase="clinical" if stage == "reset" else "emergency", rounds=0 if stage == "reset" else int(rounds or 0))
    answer = "好的，主要哪里不舒服？" if stage == "reset" else "请尽快去就近医院急诊，必要时拨打120，不要等待门诊预约。"
    result = {"schema_version":"2.0","stage":stage,"message":answer,"questions":[],"recommended_doctors":[],"booking_payload":None}
    return {"envelope":json.dumps({"answer":answer,"result":result,"state":state,"model_output":"","knowledge":""},ensure_ascii=False)}
