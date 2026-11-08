"""Generate reviewable Dify 1.9.1 DSL without embedding deployment secrets."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
base_path = ROOT / "private/current_model_source.yml"
if not base_path.exists():
    base_path = ROOT / "exports/consultation.yml"
BASE = yaml.safe_load(base_path.read_text(encoding="utf-8"))
MODEL = next(node["data"]["model"] for node in BASE["workflow"]["graph"]["nodes"] if node["data"]["type"] == "llm")

# 画布上的名称和说明面向维护人员；节点标识和接口字段保持稳定。
NODE_TEXT = {
    "advanced-chat": {
        "start": ("接收患者描述", "读取患者这一轮说的话。"),
        "precheck": ("整理已知信息，检查紧急情况", "合并之前的回答；识别重新咨询指令和明显需要急诊的情况。"),
        "route": ("判断是否继续咨询", "重新咨询时清空记录；出现明显危险症状时直接提示急诊。其余情况继续分析。"),
        "early": ("准备急诊提示或重新咨询提示", "生成患者能直接看懂的提示，同时保留前台需要的结果字段。"),
        "remember_early": ("记录本次咨询状态", "保存清空后的记录或急诊状态，避免下一轮误转普通门诊。"),
        "answer_early": ("回复患者", "告诉患者下一步怎么做，或请患者开始描述新的问题。"),
        "knowledge": ("查找相关导诊资料", "检索与症状有关的追问要点和急诊提醒，最多取6条。"),
        "format_knowledge": ("整理参考资料", "把检索到的内容和来源编号整理给模型；没有资料时明确说明。"),
        "extract": ("分析症状，确定是否需要追问", "只使用患者明确提供的信息；有必要才追问，信息足够时给出就诊建议。"),
        "guard": ("检查分析结果", "检查科室方向、就诊紧急程度和来源；只追问影响下一步的信息，方向明确就进入选号。"),
        "remember": ("记住患者信息和已问问题", "保存本轮整理后的信息和追问次数，供下一轮继续使用。"),
        "recommend": ("判断是否可以推荐医生", "可以推荐时获取医生名单；其余情况直接回复追问或就诊提醒。"),
        "catalog": ("获取医院医生名单", "调用医院接口获取医生资料，避免编造医生。"),
        "render_recommend": ("整理科室和医生推荐", "按医生擅长的方向筛选候选，说明推荐理由和预约方式。"),
        "answer_recommend": ("回复就诊建议", "向患者展示科室、医生和下一步操作，并附上前台需要的结果数据。"),
        "empty_catalog": ("跳过医生推荐", "需要继续追问、急诊处理或人工导诊时，不获取医生名单。"),
        "render_other": ("整理追问和就诊提醒", "生成补充问题、急诊提醒或人工导诊说明。"),
        "answer_other": ("回复问题或就诊提醒", "展示本轮问题或提醒，等待患者继续回答。"),
    },
    "workflow": {
        "start": ("填写预约操作和患者信息", "选择要做的操作，填写对应编号和患者凭证。查询档期不需要患者凭证。"),
        "validate": ("检查填写内容", "检查操作类型和必填参数；重复提交同一次操作时，请保留原请求编号。"),
        "branch": ("判断信息是否齐全", "信息齐全时调用医院接口；缺少信息时说明需要补充什么。"),
        "execute": ("办理查询或预约", "医院接口负责查询、占位、确认、取消和状态查询，并防止重复预约或多人抢到同一个号。"),
        "invalid": ("说明填写问题", "返回具体的参数错误，不执行预约。"),
        "merge": ("汇总处理结果", "接收医院接口的返回结果，或输入检查给出的错误说明。"),
        "render": ("整理预约结果和可选时段", "把预约状态、时间和其他可选时段写成中文；网络异常时提醒先核实结果。"),
        "end": ("返回办理结果", "返回可读说明、结果数据和状态码，供前台展示和处理。"),
    },
}


def var(name: str, node: str, key: str, value_type: str = "string") -> dict[str, object]:
    return {"variable": name, "value_selector": [node, key], "value_type": value_type}


def env(name: str, value: object, value_type: str = "string") -> dict[str, object]:
    descriptions = {"API_BASE_URL": "医院接口地址；Dify容器需要能访问这个地址。", "SERVICE_API_KEY": "医院接口密钥；请使用保密变量，不要发给前台浏览器。"}
    return {"id": str(uuid.uuid4()), "name": name, "value": value, "value_type": value_type, "description": descriptions.get(name, name), "selector": ["env", name]}


class Builder:
    def __init__(self, name: str, mode: str) -> None:
        self.document = {"version": "0.4.0", "kind": "app", "app": {"name": name, "mode": mode, "icon": "🏥", "icon_background": "#E8F3FF", "description": "根据症状帮助患者选择科室，通过多轮对话选择医生、号别和时间；患者回复与后台记录分开保存。", "use_icon_as_answer_icon": False}, "dependencies": copy.deepcopy(BASE.get("dependencies", [])), "workflow": {"conversation_variables": [], "environment_variables": [env("API_BASE_URL", "http://medical-guidance:8095"), env("SERVICE_API_KEY", "", "secret")], "features": copy.deepcopy(BASE["workflow"]["features"]), "graph": {}, "rag_pipeline_variables": []}}
        self.nodes: list[dict[str, object]] = []
        self.edges: list[dict[str, object]] = []
        self.document["workflow"]["features"].update(opening_statement="", suggested_questions=[], suggested_questions_after_answer={"enabled": False})

    def add(self, ident: str, kind: str, title: str, position: tuple[int, int], **data: object) -> None:
        x, y = position
        title, description = NODE_TEXT[self.document["app"]["mode"]].get(ident, (title, title))
        self.nodes.append({"id": ident, "type": "custom", "width": 260, "height": 120, "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y}, "sourcePosition": "right", "targetPosition": "left", "data": {"type": kind, "title": title, "desc": description, "selected": False, **data}})

    def edge(self, source: str, target: str, handle: str = "source") -> None:
        types = {node["id"]: node["data"]["type"] for node in self.nodes}
        self.edges.append({"id": source + "-" + handle + "-" + target, "type": "custom", "source": source, "target": target, "sourceHandle": handle, "targetHandle": "target", "data": {"sourceType": types[source], "targetType": types[target], "isInIteration": False, "isInLoop": False}, "zIndex": 0})

    def code(self, ident: str, title: str, filename: str, position: tuple[int, int], variables: list[dict[str, object]], outputs: dict[str, str]) -> None:
        self.add(ident, "code", title, position, code_language="python3", code=(ROOT / "nodes" / filename).read_text(encoding="utf-8"), variables=variables, outputs={key: {"type": value, "children": None} for key, value in outputs.items()})

    def branch(self, ident: str, title: str, position: tuple[int, int], selector: list[str], values: list[str], number: bool = False) -> None:
        self.add(ident, "if-else", title, position, cases=[{"id": value, "case_id": value, "logical_operator": "and", "conditions": [{"id": ident + value, "variable_selector": selector, "varType": "number" if number else "string", "comparison_operator": "=" if number else "is", "value": value}]} for value in values])

    def remember(self, ident: str, title: str, position: tuple[int, int], source: str, state_key: str) -> None:
        self.add(ident, "assigner", title, position, version="2", items=[{"variable_selector": ["conversation", "profile_json"], "input_type": "variable", "operation": "over-write", "value": [source, state_key]}, {"variable_selector": ["conversation", "rounds"], "input_type": "variable", "operation": "over-write", "value": [source, "rounds"]}])

    def http(self, ident: str, title: str, position: tuple[int, int], path: str, method: str = "GET", body: str = "") -> None:
        self.add(ident, "http-request", title, position, method=method, url="{{#env.API_BASE_URL#}}" + path, authorization={"type": "no-auth"}, headers="Content-Type:application/json\nX-Service-Key:{{#env.SERVICE_API_KEY#}}", params="", body={"type": "json" if body else "none", "data": [{"id": ident + "body", "key": "", "type": "text", "value": body}] if body else []}, timeout={"connect": 3, "read": 15, "write": 15}, retry_config={"retry_enabled": False, "max_retries": 0, "retry_interval": 1000}, error_strategy="default-value", default_value=[{"key": "body", "type": "string", "value": json.dumps({"ok": False, "code": "TRANSPORT_UNKNOWN", "message": "暂时连接不上医院服务。如果刚才提交了预约或取消，请先用原请求编号查一下结果，再决定是否重试。"}, ensure_ascii=False)}])

    def answer(self, ident: str, title: str, position: tuple[int, int], selector: str) -> None:
        self.add(ident, "answer", title, position, answer="{{#" + selector + "#}}", variables=[])

    def save(self, filename: str) -> None:
        self.document["workflow"]["graph"] = {"nodes": self.nodes, "edges": self.edges, "viewport": {"x": 0, "y": 0, "zoom": 0.55}}
        (ROOT / filename).write_text(yaml.safe_dump(self.document, allow_unicode=True, sort_keys=False), encoding="utf-8")



b = Builder("医院导诊 咨询追问推荐", "advanced-chat")
NODE_TEXT["advanced-chat"].update({
    "route": ("区分病情咨询和预约选择", "重新咨询、急诊、病情追问和预约选择走不同分支。"),
    "guard": ("检查判断把握和追问次数", "病情最多追问3轮；科室判断把握低时先选全科；预约阶段另行多轮确认。"),
    "remember": ("保存本次对话状态", "保存病情或预约阶段的状态，继续下一轮对话。"),
    "answer_other": ("只回复患者需要的信息", "只发送简短问题或就诊提醒，不展示参考资料、判断依据和预约参数。"),
})
b.document["workflow"]["conversation_variables"] = [{"id": str(uuid.uuid4()), "name": name, "value_type": kind, "value": value, "description": description, "selector": ["conversation", name]} for name, kind, value, description in [("profile_json", "string", "{}", "病情信息、当前阶段、预约偏好和候选时段；输入重新咨询可以清空。"), ("rounds", "number", 0, "只计算病情追问，最多3轮；不限制预约对话轮数。")]]
b.document["workflow"]["features"].update(opening_statement="您好，哪里不舒服，或想挂什么科？", suggested_questions=["我肚子不舒服，该挂什么科？", "我想挂明天上午的专家号", "我想给孩子预约儿科"])
b.add("start", "start", "接收患者描述", (0, 550), variables=[])
b.code("precheck", "整理信息，先看紧急情况", "precheck.py", (300, 550), [var("query", "sys", "query"), var("profile_json", "conversation", "profile_json"), var("rounds", "conversation", "rounds", "number")], {"action": "string", "context": "string", "query": "string", "early_state": "string", "rounds": "number"})
b.branch("route", "区分对话阶段", (600, 550), ["precheck", "action"], ["reset", "emergency", "booking"])
early_code = '''import json

def main(action: str, state_json: str, rounds: float) -> dict:
    stage = "reset" if action == "reset" else "emergency"
    state = {} if stage == "reset" else json.loads(state_json)
    state.update(stage=stage, phase="clinical" if stage == "reset" else "emergency", rounds=0 if stage == "reset" else int(rounds or 0))
    answer = "好的，主要哪里不舒服？" if stage == "reset" else "请尽快去就近医院急诊，必要时拨打120，不要等待门诊预约。"
    result = {"schema_version":"2.0","stage":stage,"message":answer,"questions":[],"recommended_doctors":[],"booking_payload":None}
    return {"envelope":json.dumps({"answer":answer,"result":result,"state":state,"model_output":"","knowledge":""},ensure_ascii=False)}
'''
b.add("early", "code", "准备急诊或重新咨询回复", (900, 0), code_language="python3", code=early_code, variables=[var("action", "precheck", "action"), var("state_json", "precheck", "early_state"), var("rounds", "precheck", "rounds", "number")], outputs={"envelope":{"type":"string","children":None}})
b.add("knowledge", "knowledge-retrieval", "查找相关导诊资料", (900, 500), dataset_ids=[], query_variable_selector=["precheck", "query"], retrieval_mode="multiple", multiple_retrieval_config={"top_k": 6, "reranking_enable": False, "reranking_mode": "reranking_model", "score_threshold": 0}, error_strategy="default-value", default_value=[{"key": "result", "type": "array[object]", "value": []}])
b.add("format_knowledge", "template-transform", "整理参考资料", (1200, 500), variables=[var("sources", "knowledge", "result", "array[object]")], template="{% for source in sources %}{{ source.content }}\n\n{% else %}没有找到相关参考资料。{% endfor %}")
model = copy.deepcopy(MODEL)
model["completion_params"] = {"temperature": 0.1, "max_tokens": 3000, "thinking": False, "response_format": "json_object"}
def llm(ident: str, title: str, position: tuple[int,int], prompt_file: str, message: str) -> None:
    b.add(ident, "llm", title, position, model=copy.deepcopy(model), reasoning_format="separated", context={"enabled": False}, vision={"enabled": False}, prompt_template=[{"id": ident+"-system", "role": "system", "text": (ROOT/prompt_file).read_text(encoding="utf-8")}, {"id": ident+"-user", "role": "user", "text": message}], retry_config={"retry_enabled": True, "max_retries": 1, "retry_interval": 1000}, error_strategy="default-value", default_value=[{"key": "text", "type": "string", "value": "{}"}])
llm("extract", "判断科室，必要时追问", (1500,500), "clinical_prompt.txt", "患者之前的信息和本轮描述：\n{{#precheck.context#}}\n已追问轮数：{{#precheck.rounds#}}\n参考资料：\n{{#format_knowledge.output#}}")
b.code("guard", "检查科室判断和病情追问", "triage_guard.py", (1800,500), [var("text", "extract", "text"), var("context", "precheck", "context"), var("rounds", "precheck", "rounds", "number"), var("knowledge", "format_knowledge", "output")], {"stage": "string", "profile_json": "string", "rounds": "number", "result_json": "string", "specialty": "string"})
b.branch("clinical_choice", "病情是否已完成初步判断", (2100,500), ["guard","stage"], ["recommend"])
b.add("restore", "code", "读取已有预约偏好", (900,1100), code_language="python3", code='''import json

def main(context: str)->dict:
    state=json.loads(context)
    result=dict(state.get("clinical_result", {}))
    result["_state"]=state
    return {"result_json":json.dumps(result,ensure_ascii=False)}
''', variables=[var("context","precheck","context")], outputs={"result_json":{"type":"string","children":None}})
b.add("booking_merge", "variable-aggregator", "接入预约选择阶段", (2400,950), output_type="string", variables=[["guard","result_json"],["restore","result_json"]], advanced_settings={"group_enabled":False,"groups":[]})
b.code("booking_context", "整理科室和预约偏好", "prepare_booking.py", (2700,950), [var("merged","booking_merge","output"),var("context","precheck","context")], {"context":"string","specialty":"string"})
b.http("catalog", "查询相关医生和实时余号", (3000,950), "/api/booking/options?specialty_code={{#booking_context.specialty#}}")
b.code("compact_catalog", "准备医生名单和当前日期", "booking_model_context.py", (3300,950), [var("body","catalog","body")], {"context":"string"})
llm("booking_extract", "理解号别、医生和时间偏好", (3600,950), "booking_prompt.txt", "患者本轮说：{{#sys.query#}}\n已有病情和预约状态：{{#booking_context.context#}}\n医院医生及服务器时间：{{#compact_catalog.context#}}")
b.code("booking_dialogue", "匹配余号，协商号别和时间", "booking_dialogue.py", (3900,950), [var("text","booking_extract","text"),var("context","booking_context","context"),var("options_body","catalog","body")], {"envelope":"string"})
b.add("empty_catalog", "template-transform", "跳过预约查询", (2400,250), variables=[], template="{}")
b.code("render_other", "生成简短问题或就诊提醒", "render_consultation.py", (2700,250), [var("result_json","guard","result_json"),var("catalog_body","empty_catalog","output")], {"answer":"string","result_json":"string","envelope":"string"})
b.add("reply_merge", "variable-aggregator", "汇总本轮处理结果", (4200,450), output_type="string", variables=[["early","envelope"],["render_other","envelope"],["booking_dialogue","envelope"]], advanced_settings={"group_enabled":False,"groups":[]})
b.code("staff_output", "准备后台记录和预约交接参数", "staff_output.py", (4500,450), [var("envelope","reply_merge","output"),var("conversation_id","sys","conversation_id"),var("user_id","sys","user_id"),var("query","sys","query"),var("run_id","sys","workflow_run_id")], {"body":"string","envelope":"string"})
b.http("save_record", "保存完整对话和判断依据", (4800,450), "/api/consultations/record", "POST", "{{#staff_output.body#}}")
b.code("finalize", "确认记录保存，再回复患者", "finalize_reply.py", (5100,450), [var("body","save_record","body"),var("envelope","staff_output","envelope"),var("previous","precheck","context"),var("rounds","precheck","rounds","number")], {"answer":"string","profile_json":"string","rounds":"number"})
b.remember("remember", "保存对话阶段", (5400,450), "finalize", "profile_json")
b.answer("answer_other", "只给患者简短回复", (5700,450), "finalize.answer")
for left,right in [("start","precheck"),("precheck","route"),("early","reply_merge"),("knowledge","format_knowledge"),("format_knowledge","extract"),("extract","guard"),("guard","clinical_choice"),("restore","booking_merge"),("booking_merge","booking_context"),("booking_context","catalog"),("catalog","compact_catalog"),("compact_catalog","booking_extract"),("booking_extract","booking_dialogue"),("booking_dialogue","reply_merge"),("empty_catalog","render_other"),("render_other","reply_merge"),("reply_merge","staff_output"),("staff_output","save_record"),("save_record","finalize"),("finalize","remember"),("remember","answer_other")]:b.edge(left,right)
b.edge("route","early","reset")
b.edge("route","early","emergency")
b.edge("route","restore","booking")
b.edge("route","knowledge","false")
b.edge("clinical_choice","booking_merge","recommend")
b.edge("clinical_choice","empty_catalog","false")
b.save("consultation.yml")

w = Builder("医院导诊 医生档期预约", "workflow")
NODE_TEXT["workflow"]["start"] = ("接收预约参数和患者凭证", "由导诊记录直接取得预约参数；后端补充患者凭证，患者不需要填写这些技术字段。")
fields = [("booking_payload","预约参数（从导诊后台记录直接取得）","paragraph",True,[]),("patient_token","患者凭证（由后端补充）","paragraph",False,[])]
w.add("start","start","接收预约参数",(0,300), variables=[{"variable":name,"label":label,"type":kind,"required":required,"max_length":4096,"options":options} for name,label,kind,required,options in fields])
w.code("validate","检查预约参数","booking_input.py",(300,300),[var(name,"start",name) for name,*_ in fields],{"valid":"number","body":"string","error_json":"string","action":"string","request_id":"string"})
w.branch("branch","参数是否齐全",(600,300),["validate","valid"],["1"],number=True)
w.http("execute","办理查询或预约",(900,100),"/api/booking/run","POST","{{#validate.body#}}")
w.add("invalid","template-transform","说明填写问题",(900,500),variables=[var("error","validate","error_json")],template="{{ error }}")
w.add("merge","variable-aggregator","汇总办理结果",(1200,300),output_type="string",variables=[["execute","body"],["invalid","output"]],advanced_settings={"group_enabled":False,"groups":[]})
w.code("render","整理预约结果","booking_output.py",(1500,300),[var("body","merge","output"),var("action","validate","action"),var("request_id","validate","request_id")],{"result_json":"string","message":"string","code":"string","ok":"number"})
w.add("end","end","返回办理结果",(1800,300),outputs=[{"variable":name,"value_selector":["render",name],"value_type":kind} for name,kind in [("result_json","string"),("message","string"),("code","string"),("ok","number")]])
for left,right in [("start","validate"),("validate","branch"),("execute","merge"),("invalid","merge"),("merge","render"),("render","end")]:w.edge(left,right)
w.edge("branch","execute","1")
w.edge("branch","invalid","false")
w.save("booking.yml")
print(json.dumps({"consultation_nodes":len(b.nodes),"booking_nodes":len(w.nodes),"model":MODEL["name"],"version":"2.1"}))
