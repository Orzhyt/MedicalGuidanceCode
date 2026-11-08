"""Read-only structural audit for a Chinese Dify Word lab guide."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
with ZipFile(Path(sys.argv[1])) as z:
    doc = ET.fromstring(z.read("word/document.xml"))
    styles = ET.fromstring(z.read("word/styles.xml"))
style_map = {s.get(W + "styleId"): s for s in styles.findall(W + "style")}

def text(p):
    return "".join(t.text or "" for t in p.iter(W + "t"))

def outline(p):
    pr = p.find(W + "pPr")
    if pr is None:
        return None
    direct = pr.find(W + "outlineLvl")
    if direct is not None:
        return int(direct.get(W + "val"))
    st = pr.find(W + "pStyle")
    visited = set()
    sid = st.get(W + "val") if st is not None else None
    while sid in style_map and sid not in visited:
        visited.add(sid)
        s = style_map[sid]
        level = s.find(f"{W}pPr/{W}outlineLvl")
        if level is not None:
            return int(level.get(W + "val"))
        base = s.find(W + "basedOn")
        sid = base.get(W + "val") if base is not None else None
    return None

steps, levels, figures = {}, {}, []
current = None
for p in doc.find(W + "body").findall(W + "p"):
    s = text(p).strip()
    m = re.match(r"^Step\s+(\d+)\s+", s)
    if m:
        current = int(m.group(1))
        if current in steps:
            raise SystemExit(f"Duplicate Step {current}")
        steps[current] = 0
        levels[current] = outline(p)
    elif outline(p) == 0:
        current = None
    if current is not None and any(x.tag in {W + "drawing", W + "pict"} for x in p.iter()):
        steps[current] += 1
    m = re.match(r"^图\s*(\d+)\s+", s)
    if m:
        figures.append(int(m.group(1)))
issues = []
if not steps or list(steps) != list(range(1, len(steps) + 1)):
    issues.append(f"Steps are not consecutive: {list(steps)}")
for n, count in steps.items():
    if levels[n] not in range(1, 9):
        issues.append(f"Step {n} is not a nested outline heading: {levels[n]}")
    if not count:
        issues.append(f"Step {n} has no image")
if not figures or figures != list(range(1, len(figures) + 1)):
    issues.append(f"Figure captions are not consecutive: {figures}")
print(f"Steps: {len(steps)}; captions: {len(figures)}")
if issues:
    raise SystemExit("\n".join(issues))
print("Structural audit passed; visual and functional verification still required.")
