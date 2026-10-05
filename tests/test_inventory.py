import json

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.app import app

PASSWORD = 'only_for_test_123'
SPECS = {'brand': 'Marque test', 'range': 'Gamme A', 'model': 'Pack 1300', 'chemistry': 'LiPo', 'cells': 3,
         'nominal_mah': 1300, 'connector': 'XT60', 'weight_g': 110, 'charge_c': 1,
         'charge_max_a': 1.3, 'charge_final_v': 12.6, 'charge_rate_source': 'Notice test réf. A',
         'manufacturer_guide_url': 'https://example.com/manual', 'manufacturer_guide_scope': 'Référence A',
         'provenance': 'Caractéristiques saisies pour les tests uniquement'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(app) as c:
        assert c.post('/api/auth/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 201
        for name in ('alice', 'bob'):
            assert c.post('/api/users', json={'username': name, 'password': PASSWORD}).status_code == 201
        yield c


def login(c, name):
    c.cookies.clear()
    assert c.post('/api/auth/login', json={'username': name, 'password': PASSWORD}).status_code == 200


def model(c, **kwargs):
    r = c.post('/api/models', json=SPECS | kwargs)
    assert r.status_code == 201, r.text
    return r.json()


def battery(c, number='001', **kwargs):
    r = c.post('/api/batteries', json={'number': number, 'cells': 3, 'nominal_mah': 1300, **kwargs})
    assert r.status_code == 201, r.text
    return r.json()


def test_private_isolation_and_atomic_members(client):
    c = client
    login(c, 'alice')
    m = model(c)
    l = c.post('/api/lots', json={'name': 'Achat privé', 'seller': 'vendeur privé', 'total_price': 99}).json()
    ch = c.post('/api/chargers', json={'name': 'Mon appareil', 'channels': 2}).json()
    b = battery(c)
    login(c, 'admin')
    assert c.get('/api/catalogue/'+m['id']+'/history').status_code == 404
    login(c, 'bob')
    own = battery(c)
    for path in ('/models/'+m['id'], '/lots/'+l['id'], '/chargers/'+ch['id']):
        assert c.get('/api'+path).status_code == 404
    assert c.get('/api/models').json() == []
    assert c.get('/api/lots').json() == []
    assert c.get('/api/chargers').json() == []
    assert c.put('/api/models/'+m['id'], json=SPECS).status_code == 404
    assert c.post('/api/models/'+m['id']+'/submit').status_code == 404
    assert c.post('/api/models/'+m['id']+'/archive').status_code == 404
    assert c.put('/api/lots/'+l['id'], json={'name':'intrusion'}).status_code == 404
    assert c.delete('/api/lots/'+l['id']).status_code == 404
    assert c.put('/api/chargers/'+ch['id'], json={'name':'intrusion'}).status_code == 404
    assert c.post('/api/lots/preview', json={'model_id':m['id'], 'quantity':1}).status_code == 404
    assert c.post('/api/lots/from-model', json={'model_id':m['id'], 'numbers':['002']}).status_code == 404
    assert c.post('/api/batteries/'+own['id']+'/entries', json={'added_mah':100, 'charger_id':ch['id']}).status_code == 404
    own_lot = c.post('/api/lots', json={'name':'Bob'}).json()
    assert c.put('/api/lots/'+own_lot['id']+'/members', json={'battery_ids':[own['id'], b['id']]}).status_code == 404
    assert c.get('/api/batteries/'+own['id']).json()['lot_id'] is None
    assert c.post('/api/batteries/'+b['id']+'/duplicate', json={'number':'002'}).status_code == 404
    assert c.get('/api/catalogue/review').status_code == 403


def test_catalogue_revision_workflow_and_privacy(client):
    c = client
    login(c, 'alice')
    m = model(c)
    lot = c.post('/api/lots', json={'name':'Secret achat', 'total_price':98}).json()
    b = battery(c, notes='Historique privé')
    c.put('/api/lots/'+lot['id']+'/members', json={'battery_ids':[b['id']]})
    c.post('/api/batteries/'+b['id']+'/entries', json={'added_mah':444})
    assert c.post('/api/models/'+m['id']+'/submit').status_code == 200
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'publish','expected_revision':1}).status_code == 403
    login(c, 'bob')
    assert c.get('/api/catalogue').json() == []
    assert c.get('/api/catalogue/'+m['id']+'/history').status_code == 404
    login(c, 'admin')
    assert c.get('/api/catalogue/review').json()[0]['provenance'] == SPECS['provenance']
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'reject','expected_revision':1}).status_code == 422
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'reject','expected_revision':1,'reason':'Source à préciser'}).status_code == 200
    login(c, 'alice')
    assert c.get('/api/models/'+m['id']).json()['rejection_reason'] == 'Source à préciser'
    assert c.post('/api/models/'+m['id']+'/submit').status_code == 200
    login(c, 'admin')
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'publish','expected_revision':1}).status_code == 200
    login(c, 'bob')
    catalogue = c.get('/api/catalogue').json()
    assert catalogue[0]['nominal_mah'] == 1300
    assert not any(k in catalogue[0] for k in ('number','lot_id','entries','total_price','battery_count'))
    assert 'Secret achat' not in json.dumps(catalogue)
    copied = c.post('/api/catalogue/'+m['id']+'/copy').json()
    assert copied['status'] == 'private' and copied['origin_revision'] == 1
    assert copied['origin_model_id'] == m['id']
    login(c, 'alice')
    edited = c.put('/api/models/'+m['id'], json=SPECS | {'nominal_mah':1500}).json()
    assert edited['revision'] == 2 and edited['pending']
    login(c, 'bob')
    assert c.get('/api/catalogue').json()[0]['nominal_mah'] == 1300
    from_published = c.post('/api/lots/from-model', json={'model_id':m['id'], 'numbers':['001']}).json()['batteries'][0]
    assert from_published['nominal_mah'] == 1300 and from_published['model_revision'] == 1
    history = c.get('/api/catalogue/'+m['id']+'/history').json()
    assert [h['event'] for h in history] == ['published']
    assert c.put('/api/models/'+m['id'], json=SPECS).status_code == 404
    login(c, 'admin')
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'publish','expected_revision':1}).status_code == 409
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'reject','expected_revision':2,'reason':'Justification manquante'}).status_code == 200
    assert c.get('/api/catalogue').json()[0]['nominal_mah'] == 1300
    login(c, 'alice')
    c.put('/api/models/'+m['id'], json=SPECS | {'nominal_mah':1400})
    login(c, 'admin')
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'publish','expected_revision':3}).status_code == 200
    assert c.get('/api/catalogue').json()[0]['nominal_mah'] == 1400
    assert c.post('/api/catalogue/'+m['id']+'/decision', json={'action':'archive','expected_revision':3}).status_code == 200
    assert c.get('/api/catalogue').json() == []
    login(c, 'bob')
    assert c.get('/api/batteries/'+from_published['id']).json()['nominal_mah'] == 1300
    assert c.get('/api/models/'+copied['id']).json()['nominal_mah'] == 1300
    assert c.post('/api/catalogue/'+m['id']+'/copy').status_code == 404
    assert c.post('/api/lots/from-model', json={'model_id':m['id'],'numbers':['002']}).status_code == 404


def test_atomic_batch_number_preview_snapshot_and_lot_removal(client):
    c = client
    login(c, 'alice')
    m = model(c)
    old = battery(c)
    c.post('/api/batteries/'+old['id']+'/entries', json={'added_mah':555})
    p = c.post('/api/lots/preview', json={'model_id':m['id'],'quantity':3,'start_number':'001'}).json()
    assert p['numbers'] == ['002','003','004']
    for numbers in (['002','001'], ['002','002']):
        result = c.post('/api/lots/from-model', json={'model_id':m['id'],'numbers':numbers,'lot':{'name':'Atomic'}})
        assert result.status_code == 409, result.text
        assert len(c.get('/api/batteries').json()) == 1
        assert c.get('/api/lots').json() == []
    assert c.post('/api/lots/from-model', json={'model_id':m['id'],'numbers':['   ']}).status_code == 422
    c.put('/api/models/'+m['id'], json=SPECS | {'nominal_mah':1500})
    assert c.post('/api/lots/from-model', json={'model_id':m['id'],'numbers':p['numbers'],'expected_revision':p['model_revision']}).status_code == 409
    result = c.post('/api/lots/from-model', json={'model_id':m['id'],'numbers':p['numbers'],'expected_revision':2,'lot':{'name':'Achat','acquired_on':'2026-10-04','condition':'used','total_price':30}})
    assert result.status_code == 201, result.text
    batch = result.json()
    assert [b['number'] for b in batch['batteries']] == ['002','003','004']
    assert all(b['condition']=='used' and b['nominal_mah']==1500 and b['model_revision']==2 for b in batch['batteries'])
    c.put('/api/models/'+m['id'], json=SPECS | {'nominal_mah':2000})
    first = batch['batteries'][0]
    assert c.get('/api/batteries/'+first['id']).json()['nominal_mah'] == 1500
    lot_id = batch['lot']['id']
    assert c.put('/api/lots/'+lot_id+'/members', json={'battery_ids':[old['id'],first['id']]}).status_code == 200
    assert len(c.get('/api/lots/'+lot_id).json()['batteries']) == 2
    assert c.get('/api/batteries/'+batch['batteries'][1]['id']).json()['lot_id'] is None
    assert c.delete('/api/lots/'+lot_id).status_code == 204
    detail = c.get('/api/batteries/'+old['id']).json()
    assert detail['lot_id'] is None and detail['entries'][0]['added_mah'] == 555
    assert len(c.get('/api/batteries').json()) == 4


def test_prefixed_battery_numbers_can_be_previewed_and_created(client):
    c = client
    login(c, 'alice')
    m = model(c)
    preview = c.post('/api/lots/preview', json={
        'model_id': m['id'], 'quantity': 4, 'start_number': 'F750-001'
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()['numbers'] == ['F750-001', 'F750-002', 'F750-003', 'F750-004']
    invalid = c.post('/api/lots/preview', json={
        'model_id': m['id'], 'quantity': 2, 'start_number': 'F750'
    })
    assert invalid.status_code == 422
    result = c.post('/api/lots/from-model', json={
        'model_id': m['id'], 'numbers': preview.json()['numbers'],
        'lot': {'name': 'Flywoo F750', 'condition': 'used'},
    })
    assert result.status_code == 201, result.text
    assert [item['number'] for item in result.json()['batteries']] == preview.json()['numbers']


def test_chargers_channels_archive_and_duplicate(client):
    c = client
    b = battery(c, prior_history='known', prior_cycles=29, notes='Notes historiques', charge_c=1, charge_rate_source='Notice')
    ch = c.post('/api/chargers', json={'name':'Atelier A','brand':'Marque','model':'Double','channels':2}).json()
    ch2 = c.post('/api/chargers', json={'name':'Atelier B','brand':'Marque','model':'Double','channels':2}).json()
    assert ch['id'] != ch2['id']
    path = '/api/batteries/'+b['id']+'/entries'
    assert c.post(path, json={'added_mah':10,'charger_channel':1}).status_code == 422
    assert c.post(path, json={'added_mah':10,'charger_id':ch['id'],'charger_channel':3}).status_code == 422
    e = c.post(path, json={'added_mah':780,'charger_id':ch['id'],'charger_channel':2,'resistance_mohm':[5,None,7]}).json()
    assert e['charger'] == 'Atelier A' and e['charger_channel'] == 2
    assert c.post(path, json={'added_mah':10,'charger':'Ancien chargeur'}).status_code == 201
    c.put('/api/batteries/'+b['id']+'/guide/inspection', json={'done':True})
    assert c.put('/api/chargers/'+ch['id'], json={**ch,'name':'Renommé','status':'archived','channels':1}).status_code == 200
    assert c.get('/api/batteries/'+b['id']).json()['entries'][1]['charger'] == 'Atelier A'
    assert c.post(path, json={'added_mah':10,'charger_id':ch['id']}).status_code == 422
    assert c.put(path+'/'+e['id'], json={**e,'notes':'Correction après archivage'}).status_code == 200
    assert c.get('/api/batteries/'+b['id']).json()['entries'][1]['charger'] == 'Atelier A'
    copy = c.post('/api/batteries/'+b['id']+'/duplicate', json={'number':'002'})
    assert copy.status_code == 201, copy.text
    detail = c.get('/api/batteries/'+copy.json()['id']).json()
    assert detail['entries'] == [] and detail['guide_steps'] == {} and detail['prior_cycles'] is None and detail['notes'] == ''
    assert detail['charge_count'] == 0 and detail['total_added_mah'] == 0 and detail['charge_c'] == 1
    assert c.post('/api/batteries/'+b['id']+'/duplicate', json={'number':'001'}).status_code == 409
    assert c.post('/api/batteries/'+b['id']+'/duplicate', json={'number':'   '}).status_code == 422


def test_inventory_validation(client):
    c=client
    for key in ('weight_g','charge_max_a','charge_final_v'):
        for value in (-1,'NaN','Infinity'):
            assert c.post('/api/models',json=SPECS|{key:value}).status_code == 422
    assert c.post('/api/models',json=SPECS|{'manufacturer_guide_url':'javascript:alert(1)'}).status_code == 422
    assert c.post('/api/models',json=SPECS|{'charge_rate_source':''}).status_code == 422
    assert c.post('/api/lots',json={'name':' ','total_price':10}).status_code == 422
    assert c.post('/api/lots',json={'name':'Test','total_price':'Infinity'}).status_code == 422
    assert c.post('/api/chargers',json={'name':' ','channels':2}).status_code == 422
    assert c.post('/api/chargers',json={'name':'Test','channels':0}).status_code == 422


def test_moderation_never_exposes_unsubmitted_private_history(client):
    c=client
    login(c,'alice')
    m=model(c, notes='Note privée jamais soumise')
    c.put('/api/models/'+m['id'],json=SPECS|{'notes':'Note proposée'})
    c.post('/api/models/'+m['id']+'/submit')
    login(c,'admin')
    history=c.get('/api/catalogue/'+m['id']+'/history').json()
    assert 'Note privée jamais soumise' not in json.dumps(history)
    assert [h['event'] for h in history] == ['submitted']
    assert c.post('/api/catalogue/'+m['id']+'/decision',json={'action':'publish','expected_revision':2}).status_code == 200
    login(c,'alice')
    c.put('/api/models/'+m['id'],json=SPECS|{'nominal_mah':1600})
    own=c.get('/api/models/'+m['id']).json()
    assert own['revision']==3 and own['published_revision']==2
    login(c,'admin')
    published=c.get('/api/catalogue').json()[0]
    assert published['revision']==2 and published['decision_revision']==3
    assert c.post('/api/catalogue/'+m['id']+'/decision',json={'action':'archive','expected_revision':published['decision_revision']}).status_code == 200
    assert c.get('/api/catalogue').json()==[]
