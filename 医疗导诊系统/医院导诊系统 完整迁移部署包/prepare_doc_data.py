import json
from pathlib import Path

import yaml

root = Path("/tmp/hospital_guidance")
for key in ["consultation", "booking"]:
    source = yaml.safe_load((root / "exports" / (key + ".yml")).read_text(encoding="utf-8"))
    (root / "private" / (key + "_config.json")).write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
