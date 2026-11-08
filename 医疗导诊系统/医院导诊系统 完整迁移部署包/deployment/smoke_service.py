"""Read-only smoke test for the bundled hospital service."""
from pathlib import Path
from urllib.request import Request, urlopen
import json
import sys
base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8095").rstrip("/")
key = Path(__file__).with_name("SERVICE_API_KEY.txt").read_text(encoding="utf-8").strip()
def get(route, headers=None):
    with urlopen(Request(base + route, headers=headers or {}), timeout=8) as response:
        return json.load(response)
assert get("/health")["ok"] is True
assert get("/api/doctors", {"X-Service-Key": key})["ok"] is True
assert get("/api/booking/options?specialty_code=ent", {"X-Service-Key": key})["ok"] is True
print("PASS: health, authenticated doctors, authenticated booking options")
