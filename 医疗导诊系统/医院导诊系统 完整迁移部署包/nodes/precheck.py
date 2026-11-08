import json
import re


def danger(text: str) -> bool:
    clauses = re.split(r"[，。；！？\n]|但是|但|(?<!喘)不过", text)
    positive = []
    for clause in clauses:
        # Negative enumerations can be long; their scope ends at an explicit positive transition.
        clause = re.sub(r"(?:没有|否认|不伴|无(?!法|力))(?:(?!(?:突然|突发|出现|伴有|同时有|现在有|却)).)*", "", clause)
        positive.append(clause)
    visible = "，".join(positive)
    patterns = [
        r"喘不过气|无法呼吸|严重呼吸困难|口唇发紫|意识不清|昏迷|晕厥|呕血|黑便|说话含糊|一侧.{0,5}无力",
        r"(?:突然|突发).{0,6}(?:剧烈|严重).{0,5}(?:头痛|腹痛)|(?:突然|突发).{0,5}(?:头痛|腹痛).{0,6}(?:剧烈|严重)",
        r"大量.{0,5}出血|(?:发热|发烧).{0,6}抽搐",
    ]
    if any(re.search(pattern, visible) for pattern in patterns):
        return True
    return bool(re.search(r"胸痛|胸闷", visible) and re.search(r"冷汗|呼吸困难|气促|晕", visible))


def main(query: str, profile_json: str, rounds: float) -> dict:
    try:
        previous = json.loads(profile_json or "{}")
        if not isinstance(previous, dict):
            previous = {}
    except ValueError:
        previous = {}
    query = query.strip()[:2500]
    reset = query in ["重新咨询", "开始新咨询", "清空咨询"]
    if reset:
        previous = {}
    history = list(previous.get("history", []))[-4:]
    history.append(query)
    previous["history"] = history
    action = "reset" if reset else "emergency" if danger(query) or previous.get("stage") == "emergency" else "continue"
    if action == "continue" and str(previous.get("phase", "")).startswith("booking"):
        changed_symptoms = bool(re.search(r"(?:又|现在|突然|新出现|开始|加重|另外).{0,12}(?:疼|痛|发热|咳|晕|出血|不舒服|听不见|听力下降|耳鸣)", query))
        action = "continue" if changed_symptoms else "booking"
        if changed_symptoms:
            previous["phase"] = "clinical"
            previous.pop("preferences", None)
            previous.pop("offered_slots", None)
            previous.pop("selected_slot", None)
            rounds = 0
    if action == "emergency":
        previous["stage"] = "emergency"
    synonyms = []
    for words, keyword in [
        (["肚子", "胃", "反酸", "腹", "恶心"], "腹痛 消化 digestive"),
        (["咳", "痰", "气喘"], "咳嗽 呼吸 respiratory"),
        (["头痛", "头晕"], "头痛 神经 neurology"),
        (["膝", "关节", "扭伤", "腰痛"], "关节 骨科 orthopedic"),
        (["孩子", "宝宝", "岁小孩"], "儿童 儿科 pediatrics"),
        (["怀孕", "孕", "月经"], "妇产 gynecology"),
        (["喉", "鼻", "耳"], "咽喉 ent"),
        (["皮疹", "痒", "皮肤"], "皮肤 dermatology"),
        (["血糖", "糖尿病"], "内分泌 endocrine"),
    ]:
        if any(word in " ".join(history) for word in words):
            synonyms.append(keyword)
    return {
        "action": action,
        "context": json.dumps(previous, ensure_ascii=False),
        "query": "导诊 追问 " + " ".join(synonyms) + " " + query + " " + str(previous.get("summary", "")),
        "early_state": json.dumps(previous if not reset else {}, ensure_ascii=False),
        "rounds": 0 if reset else int(rounds or 0),
    }
