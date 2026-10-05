"""Private inventory and moderated catalogue. Pack copies are immutable snapshots."""
import json
import re
from datetime import date
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, Field, FiniteFloat, field_validator, model_validator

from .db import connect, IntegrityError

SPECS = ('brand', 'range', 'model', 'chemistry', 'cells', 'nominal_mah', 'c_rating',
         'connector', 'weight_g', 'charge_c', 'charge_max_a', 'charge_final_v',
         'charge_rate_source', 'manufacturer_guide_url', 'manufacturer_guide_scope', 'notes')


class LotInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    acquired_on: date | None = None
    seller: str = Field(default='', max_length=150)
    total_price: FiniteFloat | None = Field(default=None, ge=0, le=10000000)
    currency: str = Field(default='EUR', pattern=r'^[A-Z]{3}$')
    notes: str = Field(default='', max_length=5000)
    condition: Literal['new', 'used'] = 'new'

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Nom requis')
        return value.strip()


class ChargerInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    brand: str = Field(default='', max_length=100)
    model: str = Field(default='', max_length=100)
    channels: int = Field(default=1, ge=1, le=64)
    firmware: str = Field(default='', max_length=100)
    notes: str = Field(default='', max_length=5000)
    status: Literal['active', 'archived'] = 'active'

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Nom personnel requis')
        return value.strip()


class PreviewInput(BaseModel):
    model_id: str
    quantity: int = Field(ge=1, le=100)
    start_number: str = Field(default='001', min_length=1, max_length=30)

    @field_validator('start_number')
    @classmethod
    def clean_start_number(cls, value):
        value = value.strip()
        if not value or not (value.isdigit() or re.fullmatch(r'.*[-_.][0-9]+', value)):
            raise ValueError('Utilisez un numéro seul ou un préfixe séparé du compteur par - _ ou .')
        return value


class CreateFromModel(BaseModel):
    model_id: str
    numbers: list[str] = Field(min_length=1, max_length=100)
    lot: LotInput | None = None
    condition: Literal['new', 'used'] = 'new'
    expected_revision: int | None = Field(default=None, ge=1)

    @field_validator('numbers')
    @classmethod
    def clean_numbers(cls, values):
        cleaned = [v.strip() for v in values]
        if any(not v or len(v) > 30 for v in cleaned):
            raise ValueError('Chaque numéro doit contenir entre 1 et 30 caractères')
        return cleaned


class MembersInput(BaseModel):
    battery_ids: list[str] = Field(max_length=1000)


class DuplicateInput(BaseModel):
    number: str = Field(min_length=1, max_length=30)

    @field_validator('number')
    @classmethod
    def clean_number(cls, value):
        if not value.strip():
            raise ValueError('Numéro requis')
        return value.strip()


class DecisionInput(BaseModel):
    action: Literal['publish', 'reject', 'archive']
    expected_revision: int = Field(ge=1)
    reason: str = Field(default='', max_length=2000)


def register_inventory(app, get_user, BatteryInput, battery_for, now, uid, fail):
    class ModelInput(BaseModel):
        brand: str = Field(default='', max_length=100)
        range: str = Field(default='', max_length=100)
        model: str = Field(min_length=1, max_length=100)
        chemistry: Literal['LiPo', 'LiHV', 'Li-ion', 'LiFe'] = 'LiPo'
        cells: int = Field(ge=1, le=16)
        nominal_mah: int = Field(gt=0, le=100000)
        c_rating: FiniteFloat | None = Field(default=None, gt=0, le=1000)
        connector: str = Field(default='', max_length=100)
        weight_g: FiniteFloat | None = Field(default=None, gt=0, le=100000)
        charge_c: FiniteFloat | None = Field(default=None, gt=0, le=50)
        charge_max_a: FiniteFloat | None = Field(default=None, gt=0, le=1000)
        charge_final_v: FiniteFloat | None = Field(default=None, gt=0, le=100)
        charge_rate_source: str = Field(default='', max_length=250)
        manufacturer_guide_url: str = Field(default='', max_length=1000)
        manufacturer_guide_scope: str = Field(default='', max_length=300)
        notes: str = Field(default='', max_length=5000)
        provenance: str = Field(default='', max_length=1000)

        @model_validator(mode='after')
        def validate_specs(self):
            if not self.model.strip():
                raise ValueError('Référence requise')
            BatteryInput.model_validate({'number': 'template', **self.model_dump()})
            return self

    def owned(db, table, item_id, user, write=False):
        # table comes only from internal constants, never from the request.
        lock = 'FOR NO KEY UPDATE' if write else ''
        row = db.execute(f'SELECT * FROM {table} WHERE id=? AND user_id=? {lock}', (item_id, user['id'])).fetchone()
        if not row:
            fail()
        return dict(row)

    def available_model(db, model_id, user, lock=False):
        suffix = 'FOR SHARE' if lock else ''
        row = db.execute(f'SELECT * FROM battery_models WHERE id=? {suffix}', (model_id,)).fetchone()
        if not row or row['status'] == 'archived' or (row['user_id'] != user['id'] and row['status'] != 'published'):
            fail(404, 'Modèle indisponible')
        row = dict(row)
        # A pending revision of a published model is never used before approval.
        return row, json.loads(row['published_data'] if row['status'] == 'published' else row['data'])

    def view_model(row, public=False):
        row = dict(row)
        data = json.loads(row['published_data'] if public else row['data'])
        return {**data, 'id': row['id'], 'author_id': row['user_id'], 'status': row['status'],
                'revision': row['published_revision'] if public else row['revision'],
                'published_revision': row['published_revision'],
                'created_at': row['created_at'], 'updated_at': row['updated_at'],
                'origin_model_id': row['origin_model_id'], 'origin_revision': row['origin_revision'],
                **({} if public else {'pending': bool(row['pending_data']), 'rejection_reason': row['rejection_reason']})}

    def audit(db, model_id, revision, user, event, data, reason=''):
        db.execute('INSERT INTO model_revisions VALUES (?,?,?,?,?,?,?,?)',
                   (uid(), model_id, revision, user['id'], event, json.dumps(data), reason, now()))

    def insert(db, table, data):
        db.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values()))

    @app.get('/api/models')
    def models(q: str = '', status: str = '', chemistry: str = '', user=Depends(get_user)):
        with connect() as db:
            rows = db.execute('SELECT * FROM battery_models WHERE user_id=? ORDER BY updated_at DESC', (user['id'],)).fetchall()
        items = [view_model(row) for row in rows]
        return [r for r in items if (not status or r['status'] == status) and (not chemistry or r['chemistry'] == chemistry) and q.casefold() in (' '.join(str(r[k]) for k in ('brand','range','model'))).casefold()]

    @app.post('/api/models', status_code=201)
    def add_model(payload: ModelInput, user=Depends(get_user)):
        data = payload.model_dump(mode='json')
        row = {'id': uid(), 'user_id': user['id'], 'status': 'private', 'revision': 1,
               'data': json.dumps(data), 'created_at': now(), 'updated_at': now()}
        with connect() as db:
            insert(db, 'battery_models', row)
            audit(db, row['id'], 1, user, 'created', data)
            return view_model(db.execute('SELECT * FROM battery_models WHERE id=?', (row['id'],)).fetchone())

    @app.get('/api/models/{model_id}')
    def model_detail(model_id: str, user=Depends(get_user)):
        with connect() as db:
            row = owned(db, 'battery_models', model_id, user)
            result = view_model(row)
            result['history'] = [dict(r) | {'data': json.loads(r['data'])} for r in db.execute('SELECT * FROM model_revisions WHERE model_id=? ORDER BY created_at', (model_id,))]
            return result

    @app.put('/api/models/{model_id}')
    def edit_model(model_id: str, payload: ModelInput, user=Depends(get_user)):
        data = payload.model_dump(mode='json')
        with connect() as db:
            row = owned(db, 'battery_models', model_id, user, write=True)
            if row['status'] == 'archived':
                fail(422, 'Modèle archivé')
            revision = row['revision'] + 1
            pending = json.dumps(data) if row['status'] in ('published', 'proposed') else None
            db.execute("UPDATE battery_models SET data=?, pending_data=?, revision=?, rejection_reason='', updated_at=? WHERE id=?", (json.dumps(data), pending, revision, now(), model_id))
            audit(db, model_id, revision, user, 'change_proposed' if pending else 'edited', data)
        return model_detail(model_id, user)

    @app.post('/api/models/{model_id}/submit')
    def submit_model(model_id: str, user=Depends(get_user)):
        with connect() as db:
            row = owned(db, 'battery_models', model_id, user, write=True)
            if row['status'] != 'private':
                fail(422, 'Seul un modèle privé peut être proposé')
            db.execute("UPDATE battery_models SET status='proposed', pending_data=data, rejection_reason='', updated_at=? WHERE id=?", (now(), model_id))
            audit(db, model_id, row['revision'], user, 'submitted', json.loads(row['data']))
        return model_detail(model_id, user)

    @app.post('/api/models/{model_id}/archive')
    def archive_model(model_id: str, user=Depends(get_user)):
        with connect() as db:
            row = owned(db, 'battery_models', model_id, user, write=True)
            if row['status'] != 'private':
                fail(403, 'Archivage du catalogue réservé aux administrateurs')
            db.execute("UPDATE battery_models SET status='archived', updated_at=? WHERE id=?", (now(), model_id))
            audit(db, model_id, row['revision'], user, 'archived', json.loads(row['data']))
        return model_detail(model_id, user)

    @app.get('/api/catalogue')
    def catalogue(q: str = '', chemistry: str = '', user=Depends(get_user)):
        with connect() as db:
            rows = db.execute("SELECT * FROM battery_models WHERE status='published' ORDER BY updated_at DESC").fetchall()
        items = [view_model(row, True) | ({'decision_revision': row['revision']} if user['is_admin'] else {}) for row in rows]
        return [r for r in items if (not chemistry or r['chemistry'] == chemistry) and q.casefold() in (' '.join(str(r[k]) for k in ('brand','range','model'))).casefold()]

    @app.post('/api/catalogue/{model_id}/copy', status_code=201)
    def copy_model(model_id: str, user=Depends(get_user)):
        with connect() as db:
            source = db.execute("SELECT * FROM battery_models WHERE id=? AND status='published' FOR SHARE", (model_id,)).fetchone()
            if not source:
                fail()
            row = {'id': uid(), 'user_id': user['id'], 'status': 'private', 'revision': 1,
                   'data': source['published_data'], 'origin_model_id': model_id,
                   'origin_revision': source['published_revision'], 'created_at': now(), 'updated_at': now()}
            insert(db, 'battery_models', row)
            audit(db, row['id'], 1, user, 'copied', json.loads(row['data']))
            return view_model(db.execute('SELECT * FROM battery_models WHERE id=?', (row['id'],)).fetchone())

    def admin(user):
        if not user['is_admin']:
            fail(403, 'Administrateur requis')

    @app.get('/api/catalogue/review')
    def reviews(user=Depends(get_user)):
        admin(user)
        with connect() as db:
            rows = db.execute("SELECT * FROM battery_models WHERE status='proposed' OR (status='published' AND pending_data IS NOT NULL) ORDER BY updated_at").fetchall()
        return [view_model(r) for r in rows]

    @app.get('/api/catalogue/{model_id}/history')
    def public_history(model_id: str, user=Depends(get_user)):
        with connect() as db:
            row = db.execute('SELECT * FROM battery_models WHERE id=?', (model_id,)).fetchone()
            if not row:
                fail()
            if row['user_id'] == user['id']:
                events = db.execute('SELECT * FROM model_revisions WHERE model_id=? ORDER BY created_at', (model_id,))
            elif user['is_admin']:
                # A private revision is not shared just because its author later submits.
                events = db.execute("SELECT * FROM model_revisions WHERE model_id=? AND (event IN ('submitted','change_proposed','published','rejected') OR (event='archived' AND ?)) ORDER BY created_at", (model_id, bool(row['published_data']))).fetchall()
                if not events:
                    fail()
            else:
                if not row['published_data']:
                    fail()
                events = db.execute("SELECT * FROM model_revisions WHERE model_id=? AND event IN ('published','archived') ORDER BY created_at", (model_id,))
            return [dict(r) | {'data': json.loads(r['data'])} for r in events]

    @app.post('/api/catalogue/{model_id}/decision')
    def decide(model_id: str, payload: DecisionInput, user=Depends(get_user)):
        admin(user)
        with connect() as db:
            row = db.execute('SELECT * FROM battery_models WHERE id=? FOR NO KEY UPDATE', (model_id,)).fetchone()
            if not row:
                fail()
            if row['revision'] != payload.expected_revision:
                fail(409, 'La proposition a changé : relisez la révision actuelle')
            if payload.action == 'archive':
                if row['status'] not in ('published', 'proposed'):
                    fail(422, 'Modèle non publié ou proposé')
                data = json.loads(row['published_data'] or row['data'])
                db.execute("UPDATE battery_models SET status='archived', pending_data=NULL, updated_at=? WHERE id=?", (now(), model_id))
                audit(db, model_id, row['published_revision'] or row['revision'], user, 'archived', data)
            else:
                if not row['pending_data'] or row['status'] not in ('proposed','published'):
                    fail(422, 'Aucune proposition à examiner')
                data = json.loads(row['pending_data'])
                if payload.action == 'publish':
                    db.execute("UPDATE battery_models SET status='published', published_data=pending_data, published_revision=revision, pending_data=NULL, rejection_reason='', updated_at=? WHERE id=?", (now(), model_id))
                    audit(db, model_id, row['revision'], user, 'published', data)
                else:
                    if not payload.reason.strip():
                        fail(422, 'Motif de refus requis')
                    db.execute("UPDATE battery_models SET status=?, pending_data=NULL, rejection_reason=?, updated_at=? WHERE id=?", ('published' if row['published_data'] else 'private', payload.reason.strip(), now(), model_id))
                    audit(db, model_id, row['revision'], user, 'rejected', data, payload.reason.strip())
        return {'ok': True}

    @app.post('/api/lots/preview')
    def preview(payload: PreviewInput, user=Depends(get_user)):
        with connect() as db:
            row, data = available_model(db, payload.model_id, user)
            taken = {r[0] for r in db.execute('SELECT number FROM batteries WHERE user_id=?', (user['id'],))}
        match = re.fullmatch(r'(.*?)([0-9]+)', payload.start_number)
        if not match:
            fail(422, 'Le numéro de départ doit se terminer par un chiffre')
        prefix, first_digits = match.groups()
        numbers, n = [], int(first_digits)
        while len(numbers) < payload.quantity:
            number = prefix + str(n).zfill(len(first_digits))
            if len(number) > 30:
                fail(422, 'Suite de numéros trop longue')
            if number not in taken:
                numbers.append(number)
            n += 1
        return {'numbers': numbers, 'specs': data, 'model_revision': row['published_revision'] if row['status'] == 'published' else row['revision']}

    @app.post('/api/lots/from-model', status_code=201)
    def from_model(payload: CreateFromModel, user=Depends(get_user)):
        created = []
        try:
            with connect() as db:
                source, specs = available_model(db, payload.model_id, user, lock=True)
                revision = source['published_revision'] if source['status'] == 'published' else source['revision']
                if payload.expected_revision is not None and payload.expected_revision != revision:
                    fail(409, 'Le modèle a changé : refaites l’aperçu')
                numbers = [BatteryInput.model_validate({'number': n, **{k: specs.get(k) for k in SPECS}}).number for n in payload.numbers]
                if len(set(numbers)) != len(numbers):
                    fail(409, 'Numéros en double')
                lot = None
                if payload.lot:
                    lot = {**payload.lot.model_dump(mode='json'), 'id': uid(), 'user_id': user['id'], 'created_at': now(), 'updated_at': now()}
                    insert(db, 'purchase_lots', lot)
                for number in numbers:
                    base = BatteryInput.model_validate({'number': number, **{k: specs.get(k) for k in SPECS},
                        'condition': lot['condition'] if lot else payload.condition, 'acquired_on': lot['acquired_on'] if lot else None}).model_dump(mode='json')
                    battery = {**base, 'id': uid(), 'user_id': user['id'], 'model_id': source['id'],
                        'model_revision': source['published_revision'] if source['status'] == 'published' else source['revision'],
                        'lot_id': lot['id'] if lot else None, 'created_at': now(), 'updated_at': now()}
                    insert(db, 'batteries', battery)
                    created.append(battery)
        except IntegrityError:
            fail(409, 'Un numéro existe déjà : aucune batterie ni lot créés')
        return {'lot': lot, 'batteries': created}

    @app.get('/api/lots')
    def lots(q: str = '', condition: str = '', user=Depends(get_user)):
        with connect() as db:
            return [dict(r) for r in db.execute("SELECT l.*, (SELECT COUNT(*) FROM batteries b WHERE b.lot_id=l.id AND b.user_id=l.user_id) AS battery_count FROM purchase_lots l WHERE l.user_id=? AND l.name ILIKE ? AND (?='' OR l.condition=?) ORDER BY l.acquired_on DESC, l.created_at DESC", (user['id'], '%'+q+'%', condition, condition))]

    @app.post('/api/lots', status_code=201)
    def add_lot(payload: LotInput, user=Depends(get_user)):
        row = {**payload.model_dump(mode='json'), 'id': uid(), 'user_id': user['id'], 'created_at': now(), 'updated_at': now()}
        with connect() as db:
            insert(db, 'purchase_lots', row)
        return row

    @app.get('/api/lots/{lot_id}')
    def lot_detail(lot_id: str, user=Depends(get_user)):
        with connect() as db:
            row = owned(db, 'purchase_lots', lot_id, user)
            row['batteries'] = [dict(r) for r in db.execute('SELECT * FROM batteries WHERE lot_id=? AND user_id=? ORDER BY number', (lot_id, user['id']))]
            return row

    @app.put('/api/lots/{lot_id}')
    def edit_lot(lot_id: str, payload: LotInput, user=Depends(get_user)):
        data = payload.model_dump(mode='json') | {'updated_at': now()}
        with connect() as db:
            owned(db, 'purchase_lots', lot_id, user, write=True)
            db.execute(f"UPDATE purchase_lots SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), lot_id))
        return lot_detail(lot_id, user)

    @app.put('/api/lots/{lot_id}/members')
    def members(lot_id: str, payload: MembersInput, user=Depends(get_user)):
        with connect() as db:
            owned(db, 'purchase_lots', lot_id, user, write=True)
            # Lock all affected batteries in a stable order, including removals.
            placeholders = ','.join('?' for _ in payload.battery_ids) or 'NULL'
            db.execute(f'SELECT id FROM batteries WHERE user_id=? AND (lot_id=? OR id IN ({placeholders})) ORDER BY id FOR UPDATE',
                       (user['id'], lot_id, *payload.battery_ids)).fetchall()
            for battery_id in sorted(set(payload.battery_ids)):
                battery_for(db, user['id'], battery_id, write=True)
            db.execute('UPDATE batteries SET lot_id=NULL WHERE lot_id=? AND user_id=?', (lot_id, user['id']))
            for battery_id in sorted(set(payload.battery_ids)):
                db.execute('UPDATE batteries SET lot_id=? WHERE id=? AND user_id=?', (lot_id, battery_id, user['id']))
        return lot_detail(lot_id, user)

    @app.delete('/api/lots/{lot_id}', status_code=204)
    def delete_lot(lot_id: str, user=Depends(get_user)):
        with connect() as db:
            owned(db, 'purchase_lots', lot_id, user, write=True)
            db.execute('DELETE FROM purchase_lots WHERE id=?', (lot_id,))

    @app.get('/api/chargers')
    def chargers(q: str = '', status: str = '', user=Depends(get_user)):
        with connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM chargers WHERE user_id=? AND (name ILIKE ? OR brand ILIKE ? OR model ILIKE ?) AND (?='' OR status=?) ORDER BY name", (user['id'], *['%'+q+'%']*3, status, status))]

    @app.post('/api/chargers', status_code=201)
    def add_charger(payload: ChargerInput, user=Depends(get_user)):
        row = {**payload.model_dump(mode='json'), 'id': uid(), 'user_id': user['id'], 'created_at': now(), 'updated_at': now()}
        with connect() as db:
            insert(db, 'chargers', row)
        return row

    @app.get('/api/chargers/{charger_id}')
    def charger_detail(charger_id: str, user=Depends(get_user)):
        with connect() as db:
            return owned(db, 'chargers', charger_id, user)

    @app.put('/api/chargers/{charger_id}')
    def edit_charger(charger_id: str, payload: ChargerInput, user=Depends(get_user)):
        data = payload.model_dump(mode='json') | {'updated_at': now()}
        with connect() as db:
            owned(db, 'chargers', charger_id, user, write=True)
            db.execute(f"UPDATE chargers SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), charger_id))
        return charger_detail(charger_id, user)

    @app.post('/api/batteries/{battery_id}/duplicate', status_code=201)
    def duplicate(battery_id: str, payload: DuplicateInput, user=Depends(get_user)):
        try:
            with connect() as db:
                original = battery_for(db, user['id'], battery_id)
                data = BatteryInput.model_validate({'number': payload.number, **{k: original[k] for k in SPECS if k != 'notes'}, 'condition': original['condition']}).model_dump(mode='json')
                data.update(id=uid(), user_id=user['id'], model_id=original['model_id'], model_revision=original['model_revision'], created_at=now(), updated_at=now())
                insert(db, 'batteries', data)
        except IntegrityError:
            fail(409, 'Ce numéro existe déjà')
        return data
