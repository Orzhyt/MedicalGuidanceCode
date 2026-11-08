import json


def main(body: str) -> dict:
    try:
        catalog = json.loads(body)
        compact = {"server_time": catalog.get("server_time"), "doctors": catalog.get("doctors", [])}
    except (ValueError, TypeError):
        compact = {}
    return {"context": json.dumps(compact, ensure_ascii=False)}
