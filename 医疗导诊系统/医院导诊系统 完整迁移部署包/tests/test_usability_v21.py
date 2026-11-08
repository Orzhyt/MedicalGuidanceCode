import json
from nodes.triage_guard import main as guard
from nodes.booking_dialogue import main as dialogue
from test_consultation_v2 import options


def test_clear_ent_is_not_downgraded_for_missing_fields_or_round_cap() -> None:
    model = {'profile': {'age': None, 'main_symptom': '耳朵有嗡嗡声', 'duration': '三天', 'severity': 'unknown', 'red_flags_status': 'unknown'}, 'summary': '耳朵嗡嗡响，听力还行', 'urgency': 'routine', 'specialty_code': 'ent', 'department_confidence': 'medium', 'questions': ['是否影响睡眠？'], 'knowledge_ids': ['K09']}
    output = guard(json.dumps(model), '{}', 3, 'K09 耳鸣')
    assert output['stage'] == 'recommend' and output['specialty'] == 'ent'
    assert json.loads(output['result_json'])['questions'] == []


def test_date_only_lists_slots_without_forcing_type_or_period() -> None:
    catalog = options()
    catalog['slots'].append({**catalog['slots'][0], 'slot_id': 'ordinary-pm', 'starts_at': '2026-10-12T14:00:00+08:00'})
    context = {'result': {'stage': 'recommend', 'specialty_code': 'digestive'}, 'state': {}}
    output = json.loads(dialogue('{"date":"2026-10-12"}', json.dumps(context), json.dumps(catalog))['envelope'])
    assert output['result']['stage'] == 'booking_options'
    assert {slot['starts_at'][11:13] for slot in output['state']['offered_slots']} == {'09', '14'}
    assert 'registration_type' not in output['state']['preferences']
    assert 'period' not in output['state']['preferences']
    assert output['result']['booking_payload'] is None


def test_child_localized_ent_is_not_forced_to_pediatrics() -> None:
    model = {'profile': {'age': 10, 'main_symptom': '耳朵疼', 'duration': '一天', 'severity': '轻微', 'red_flags_status': 'unknown'}, 'summary': '孩子耳朵疼', 'urgency': 'routine', 'specialty_code': 'ent', 'department_confidence': 'high', 'questions': [], 'knowledge_ids': ['K09']}
    assert guard(json.dumps(model), '{}', 0, 'K09')['specialty'] == 'ent'



def test_age_with_unit_does_not_break_valid_department_recommendation() -> None:
    model = {'profile': {'age': '6岁', 'main_symptom': '咳嗽', 'duration': '两天', 'red_flags_status': 'unknown'}, 'summary': '孩子咳嗽两天', 'urgency': 'routine', 'specialty_code': 'pediatrics', 'department_confidence': 'high', 'questions': [], 'knowledge_ids': ['K08']}
    output = guard(json.dumps(model), '{}', 0, 'K08')
    assert output['stage'] == 'recommend' and output['specialty'] == 'pediatrics'


def test_truly_vague_complaint_at_cap_routes_to_general() -> None:
    model = {'profile': {'age': None, 'main_symptom': '身体不舒服，说不清', 'red_flags_status': 'unknown'}, 'summary': '无法明确主要部位', 'urgency': 'routine', 'specialty_code': 'general', 'department_confidence': 'low', 'questions': ['哪里不舒服？'], 'knowledge_ids': ['K01']}
    assert guard(json.dumps(model), '{}', 3, 'K01')['specialty'] == 'general'


def test_urgent_ent_keeps_specialty_without_future_booking() -> None:
    model = {'profile': {'age': None, 'main_symptom': '突然听力下降', 'red_flags_status': 'unknown'}, 'summary': '突然听力下降', 'urgency': 'urgent', 'specialty_code': 'ent', 'department_confidence': 'high', 'questions': [], 'knowledge_ids': ['K09']}
    checked = guard(json.dumps(model), '{}', 0, 'K09')
    context = {'result': json.loads(checked['result_json']), 'state': json.loads(checked['profile_json'])}
    output = json.loads(dialogue('{"date":"2026-10-12"}',json.dumps(context),json.dumps(options()))['envelope'])
    assert output['result']['stage']=='urgent' and not output['result']['booking_payload']


def test_new_hearing_loss_during_booking_returns_to_triage() -> None:
    from nodes.precheck import main as precheck
    state={'phase':'booking_ready','specialty_code':'ent','preferences':{'date':'2026-10-12'}}
    output=precheck('现在耳朵突然听不见了',json.dumps(state),1)
    assert output['action']=='continue'
    assert json.loads(output['context'])['phase']=='clinical'


def test_urgent_is_distinct_from_life_threatening_emergency() -> None:
    model={'profile':{'age':35,'main_symptom':'突然听不见','red_flags_status':'present'},'summary':'突发听力下降','urgency':'urgent','specialty_code':'ent','department_confidence':'high','questions':[],'knowledge_ids':['K09']}
    output=guard(json.dumps(model),'{}',0,'K09')
    assert output['stage']=='recommend'
    assert json.loads(output['result_json'])['urgency']=='urgent'
