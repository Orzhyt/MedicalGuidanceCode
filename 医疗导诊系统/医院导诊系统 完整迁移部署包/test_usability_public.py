"""业务可用性回归：短主诉、日期即选号、明确专科和当天就诊。"""
import json
import uuid
from pathlib import Path
import requests

ROOT=Path('/tmp/hospital_guidance')
keys=json.loads((ROOT/'private/dify_api_keys.json').read_text())
credentials=json.loads((ROOT/'private/service_credentials.json').read_text())
http=requests.Session(); http.trust_env=False
records=[]
user='PATIENT-USABILITY-'+uuid.uuid4().hex[:8]

def chat(query: str, conversation: str='') -> tuple[str, dict]:
    reply=http.post('http://nginx/v1/chat-messages',headers={'Authorization':'Bearer '+keys['consultation']},json={'inputs':{},'query':query,'conversation_id':conversation,'response_mode':'blocking','user':user},timeout=90)
    reply.raise_for_status(); output=reply.json()
    row=http.get('http://medical-guidance:8095/api/consultations/'+output['conversation_id']+'/latest',headers={'X-Service-Key':credentials['api_key']},timeout=10).json()['latest']
    assert row['patient_answer']==output['answer']
    assert len(output['answer'])<=220
    assert not any(word in output['answer'] for word in ['参考资料','K09','slot_id','培训','模拟','possible_causes'])
    records.append({'query':query,'answer':output['answer'],'result':row['result'],'rounds':row['state'].get('rounds',0)})
    print(json.dumps({'query':query,'answer':output['answer'],'stage':row['result']['stage'],'specialty':row['result'].get('specialty_code')},ensure_ascii=False),flush=True)
    return output['conversation_id'],row

conversation,row=chat('35')
assert row['result']['stage']=='ask' and len(row['result']['questions'])==1
conversation,row=chat('想挂耳鼻喉，耳朵有嗡嗡声',conversation)
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='ask'
assert '多久' in row['patient_answer'] and '听力' in row['patient_answer']
conversation,row=chat('三天了，听力没有变化',conversation)
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='recommend'
assert row['result']['questions']==['想哪天就诊？']
conversation,row=chat('后天',conversation)
assert row['result']['stage']=='booking_options'
assert {slot['starts_at'][11:13] for slot in row['state']['offered_slots']} >= {'09','14'}
assert 'period' not in row['state']['preferences']
conversation,row=chat('想要专家号',conversation)
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='booking_options'
assert '只有普通号' in row['patient_answer']
assert not row['result']['booking_payload']
conversation,row=chat('那就普通号，下午',conversation)
assert all(int(slot['starts_at'][11:13])>=12 and slot['registration_type']=='ordinary' for slot in row['state']['offered_slots'])
conversation,row=chat('选第1个',conversation)
assert row['result']['stage']=='booking_ready'
assert row['result']['booking_payload']['doctor_id']=='11'
conversation,row=chat('现在耳朵突然听不见了',conversation)
assert row['result']['stage']=='urgent' and not row['result']['booking_payload']
_,row=chat('耳朵嗡嗡响三天了，听力还行，后天去')
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='booking_options'
assert row['state'].get('rounds',0)==0
_,row=chat('今天突然左耳听不见了，还有耳鸣')
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='urgent'
assert not row['result']['booking_payload']
_,row=chat('10岁的孩子耳朵疼一天了，想后天看')
assert row['result']['specialty_code']=='ent' and row['result']['stage']=='booking_options'
(ROOT/'reports/usability_v21.json').write_text(json.dumps({'passed':True,'chat_calls':len(records),'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS business usability regressions')

