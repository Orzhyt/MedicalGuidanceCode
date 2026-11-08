import json


def main(body: str, envelope: str, previous: str, rounds: float) -> dict:
    source = json.loads(envelope)
    try:
        recorded = bool(json.loads(body).get("ok"))
    except (ValueError, TypeError):
        recorded = False
    if not recorded:
        return {"answer": "服务暂时繁忙，请稍后再试或联系导诊台。", "profile_json": previous, "rounds": int(rounds or 0)}
    return {"answer": source["answer"], "profile_json": json.dumps(source["state"], ensure_ascii=False), "rounds": int(source["state"].get("rounds", rounds or 0))}
