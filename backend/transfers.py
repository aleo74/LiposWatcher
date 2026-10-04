"""Explicit, single-pack transfers. Snapshots are consent records, not public data."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from .db import connect, IntegrityError
from . import mail
from .accounts import TokenInput, digest, rate_limit

BATTERY_FIELDS = ('id', 'brand', 'model', 'range', 'connector', 'weight_g', 'chemistry', 'cells',
                  'nominal_mah', 'c_rating', 'charge_c', 'charge_max_a', 'charge_final_v',
                  'charge_rate_source', 'manufacturer_guide_url', 'manufacturer_guide_scope',
                  'condition', 'prior_history', 'prior_cycles', 'status')
ENTRY_FIELDS = ('id', 'kind', 'occurred_at', 'added_mah', 'discharged_mah', 'cutoff_v',
                'initial_percent', 'final_percent', 'before_v', 'after_v', 'resistance_mohm',
                'current_a', 'ambient_c', 'duration_min', 'origin_ref', 'created_at')
PRIVATE_FIELDS = ('notes', 'initial_percent_source', 'voltage_sag', 'heat')


class PreviewInput(BaseModel):
    recipient_email: EmailStr
    share_notes: list[str] = Field(default_factory=list, max_length=10000)


class ProposalInput(PreviewInput):
    snapshot_hash: str = Field(min_length=64, max_length=64)


class Acceptance(BaseModel):
    number: str = Field(min_length=1, max_length=30)
    confirmed: bool


class AnnotationInput(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


def capture_legacy_chargers(db, battery_id):
    # Freeze legacy technical identification once. Later private device edits
    # cannot silently change the reading, or revive a changed-then-reverted preview.
    rows = db.execute('SELECT e.id,c.brand,c.model FROM entries e JOIN chargers c ON c.id=e.charger_id WHERE e.battery_id=? AND e.charger_snapshot IS NULL ORDER BY c.id FOR SHARE OF c', (battery_id,)).fetchall()
    for row in rows:
        device = {'brand': row['brand'], 'model': row['model'],
                  'identification_basis': 'Fiche disponible au premier aperçu ; identification historique non vérifiée'}
        db.execute('UPDATE entries SET charger_snapshot=? WHERE id=?', (json.dumps(device), row['id']))


def snapshot(db, battery, selected, recipient_email):
    selected = set(selected)
    options = []

    def note(key, text):
        if text:
            options.append({'key': key, 'text': text})
        return text if key in selected else ''

    pack = {key: battery[key] for key in BATTERY_FIELDS}
    pack['notes'] = note('battery.notes', battery['notes'])
    records = []
    for row in db.execute('SELECT * FROM entries WHERE battery_id=? ORDER BY id', (battery['id'],)):
        entry = {key: row[key] for key in ENTRY_FIELDS}
        for key in ('before_v', 'after_v', 'resistance_mohm'):
            entry[key] = json.loads(entry[key]) if entry[key] else None
        for key in PRIVATE_FIELDS:
            entry[key] = note(f'entry:{row["id"]}:{key}', row[key])
        device = json.loads(row['charger_snapshot']) if row['charger_snapshot'] else None
        # Snapshot at recording time, never transmit a private device ID or name.
        entry['charger'] = device or {}
        entry['charger']['channel'] = row['charger_channel']
        entry['annotations'] = []
        for annotation in db.execute('SELECT * FROM entry_annotations WHERE entry_id=? ORDER BY id', (row['id'],)):
            entry['annotations'].append({'id': annotation['id'], 'origin_ref': annotation['origin_ref'],
                                         'created_at': annotation['created_at'],
                                         'text': note('annotation:'+annotation['id'], annotation['text'])})
        records.append(entry)
    steps = [dict(row) for row in db.execute('SELECT step_key,completed_at FROM guide_steps WHERE battery_id=? ORDER BY step_key', (battery['id'],))]
    payload = {'battery': pack, 'entries': records, 'guide_steps': steps}
    # Include hashes of private text in the fingerprint only: any edit invalidates
    # consent, but excluded private text must never be sent to the recipient.
    fingerprint = json.dumps({'payload': payload, 'private': options, 'selection': sorted(selected),
                              'owner': battery['user_id'], 'recipient_email': recipient_email},
                             sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return payload, digest(fingerprint), options


def reconcile(db, timestamp, battery_id):
    """Caller holds this battery FOR UPDATE; then lock its pending proposal.

    Every technical write reconciles only this battery, so edit-then-revert
    cannot revive consent and no unrelated battery is scanned or locked.
    """
    for transfer in db.execute("SELECT * FROM transfers WHERE battery_id=? AND status='pending' ORDER BY id FOR UPDATE", (battery_id,)).fetchall():
        status = None
        if transfer['expires_at'] <= timestamp:
            status = 'expired'
        else:
            battery = db.execute('SELECT * FROM batteries WHERE id=? AND user_id=?', (transfer['battery_id'], transfer['sender_id'])).fetchone()
            if not battery or snapshot(db, battery, json.loads(transfer['note_selection']), transfer['recipient_email'])[1] != transfer['snapshot_hash']:
                status = 'invalidated'
        if status:
            db.execute("UPDATE transfers SET status=?,closed_at=?,snapshot='{}',note_selection='[]' WHERE id=?", (status, timestamp, transfer['id']))


def register_transfers(app, get_user, battery_for, now, uid, fail):
    def verified(user, db=None):
        if db is not None:
            row = db.execute('SELECT email,email_verified_at FROM users WHERE id=? FOR SHARE', (user['id'],)).fetchone()
            user = {**user, **dict(row)}
        if not user.get('email') or not user.get('email_verified_at'):
            fail(403, 'Vérifiez votre adresse e-mail dans Mon compte avant un transfert')
        return user

    def recipient(row, user):
        return bool(user.get('email_verified_at') and user.get('email') == row['recipient_email'])

    def authorized(row, user):
        if not row or (row['sender_id'] != user['id'] and not recipient(row, user) and row['recipient_id'] != user['id']):
            fail()

    def serialized(row, user, full=False):
        # Terminal receipts intentionally contain no battery history or invitation address.
        result = {key: row[key] for key in ('id', 'battery_id', 'status', 'created_at', 'expires_at', 'closed_at')}
        result['side'] = 'sent' if row['sender_id'] == user['id'] else 'received'
        if row['status'] == 'pending' and full:
            result['snapshot'] = json.loads(row['snapshot'])
            if result['side'] == 'sent':
                result['recipient_email'] = row['recipient_email']
        return result

    def refresh_states(user):
        # Listing maintenance touches only this user's proposals, one battery per
        # transaction. A busy battery is skipped; actions always recheck it.
        with connect() as db:
            rows = db.execute("SELECT DISTINCT battery_id FROM transfers WHERE status='pending' AND (sender_id=? OR (recipient_email=? AND CAST(? AS TEXT) IS NOT NULL)) ORDER BY battery_id",
                              (user['id'], user.get('email'), user.get('email_verified_at'))).fetchall()
        for item in rows:
            with connect() as db:
                locked = db.execute('SELECT id FROM batteries WHERE id=? FOR UPDATE SKIP LOCKED', (item['battery_id'],)).fetchone()
                if locked:
                    reconcile(db, now(), item['battery_id'])

    def lock_transfer(db, user, transfer_id=None, token_hash=None):
        # Discover/authorize without locking, then enforce battery -> proposal
        # ordering, identical to technical writes. Re-read after every wait.
        column, value = ('id', transfer_id) if transfer_id else ('token_hash', token_hash)
        row = db.execute(f'SELECT * FROM transfers WHERE {column}=?', (value,)).fetchone()
        authorized(row, user)
        db.execute('SELECT id FROM batteries WHERE id=? FOR UPDATE', (row['battery_id'],)).fetchone()
        row = db.execute('SELECT * FROM transfers WHERE id=? FOR UPDATE', (row['id'],)).fetchone()
        authorized(row, user)
        reconcile(db, now(), row['battery_id'])
        row = db.execute('SELECT * FROM transfers WHERE id=?', (row['id'],)).fetchone()
        if row['status'] != 'pending':
            # Persist expiry/invalidation even if the caller then returns 409/410.
            db.commit()
        return row

    @app.post('/api/batteries/{battery_id}/transfer-preview')
    def preview(battery_id: str, payload: PreviewInput, user=Depends(get_user)):
        verified(user)
        email = str(payload.recipient_email).casefold()
        if email == user['email']:
            fail(422, 'Un transfert vers soi-même est impossible')
        with connect() as db:
            user = verified(user, db)
            if email == user['email']:
                fail(422, 'Un transfert vers soi-même est impossible')
            battery = battery_for(db, user['id'], battery_id, write=True)
            capture_legacy_chargers(db, battery_id)
            data, fingerprint, choices = snapshot(db, battery, payload.share_notes, email)
            if set(payload.share_notes) - {choice['key'] for choice in choices}:
                fail(422, 'Sélection de notes inconnue')
        return {'snapshot': data, 'snapshot_hash': fingerprint, 'note_options': choices, 'recipient_email': email}

    @app.post('/api/batteries/{battery_id}/transfers', status_code=201)
    def propose(battery_id: str, payload: ProposalInput, request: Request, tasks: BackgroundTasks, user=Depends(get_user)):
        verified(user)
        email = str(payload.recipient_email).casefold()
        rate_limit(request, 'transfer', maximum=15)
        if email == user['email']:
            fail(422, 'Un transfert vers soi-même est impossible')
        if not mail.available():
            fail(503, 'Service de courrier indisponible')
        with connect() as db:
            user = verified(user, db)
            if email == user['email']:
                fail(422, 'Un transfert vers soi-même est impossible')
            battery = battery_for(db, user['id'], battery_id, write=True)
            reconcile(db, now(), battery_id)
            data, fingerprint, choices = snapshot(db, battery, payload.share_notes, email)
            if fingerprint != payload.snapshot_hash or set(payload.share_notes)-{choice['key'] for choice in choices}:
                fail(409, 'Les données ont changé. Recréez et confirmez l’aperçu.')
            token, transfer_id = secrets.token_urlsafe(48), uid()
            sender = db.execute('SELECT provenance_ref FROM users WHERE id=?', (user['id'],)).fetchone()[0]
            expires = (datetime.now(timezone.utc)+timedelta(hours=max(1, int(os.environ.get('TRANSFER_HOURS', '72'))))).isoformat()
            try:
                db.execute('INSERT INTO transfers(id,battery_id,sender_id,recipient_email,token_hash,expires_at,snapshot,snapshot_hash,note_selection,created_at,from_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (transfer_id, battery_id, user['id'], email, digest(token), expires, json.dumps(data), fingerprint, json.dumps(payload.share_notes), now(), sender))
            except IntegrityError:
                fail(409, 'Une proposition est déjà en attente pour cette batterie')
            row = db.execute('SELECT * FROM transfers WHERE id=?', (transfer_id,)).fetchone()
        tasks.add_task(mail.deliver, email, 'transfer', token)
        return serialized(row, user, True)

    @app.get('/api/transfers')
    def listing(user=Depends(get_user)):
        refresh_states(user)
        with connect() as db:
            current = db.execute('SELECT email,email_verified_at FROM users WHERE id=? FOR SHARE', (user['id'],)).fetchone()
            user = {**user, **dict(current)}
            rows = db.execute('SELECT * FROM transfers WHERE sender_id=? OR recipient_id=? OR (recipient_email=? AND CAST(? AS TEXT) IS NOT NULL) ORDER BY created_at DESC',
                              (user['id'], user['id'], user.get('email'), user.get('email_verified_at'))).fetchall()
        return [serialized(row, user) for row in rows]

    @app.post('/api/transfers/resolve')
    def resolve(payload: TokenInput, request: Request, user=Depends(get_user)):
        verified(user)
        rate_limit(request, 'transfer-link', maximum=20)
        with connect() as db:
            user = verified(user, db)
            row = lock_transfer(db, user, token_hash=digest(payload.token))
            if not row or not recipient(row, user):
                fail()
            if row['status'] != 'pending':
                fail(410, 'Lien clôturé : consultez le reçu dans vos transferts')
        return serialized(row, user, True)

    @app.get('/api/transfers/{transfer_id}')
    def detail(transfer_id: str, user=Depends(get_user)):
        with connect() as db:
            current = db.execute('SELECT email,email_verified_at FROM users WHERE id=? FOR SHARE', (user['id'],)).fetchone()
            user = {**user, **dict(current)}
            row = lock_transfer(db, user, transfer_id=transfer_id)
        return serialized(row, user, True)

    @app.post('/api/transfers/{transfer_id}/cancel')
    def cancel(transfer_id: str, user=Depends(get_user)):
        return close(transfer_id, user, 'canceled')

    @app.post('/api/transfers/{transfer_id}/refuse')
    def refuse(transfer_id: str, user=Depends(get_user)):
        verified(user)
        return close(transfer_id, user, 'refused')

    def close(transfer_id, user, status):
        with connect() as db:
            if status == 'refused':
                user = verified(user, db)
            row = lock_transfer(db, user, transfer_id=transfer_id)
            if (status == 'canceled' and row['sender_id'] != user['id']) or (status == 'refused' and not recipient(row, user)):
                fail()
            if row['status'] != 'pending':
                fail(409, 'Proposition déjà clôturée')
            changed = db.execute("UPDATE transfers SET status=?,closed_at=?,recipient_id=?,snapshot='{}',note_selection='[]' WHERE id=? AND status='pending'",
                                 (status, now(), user['id'] if status == 'refused' else None, transfer_id))
            if changed.rowcount != 1:
                fail(409, 'Proposition déjà clôturée')
            row = db.execute('SELECT * FROM transfers WHERE id=?', (transfer_id,)).fetchone()
        return serialized(row, user)

    @app.post('/api/transfers/{transfer_id}/accept')
    def accept(transfer_id: str, payload: Acceptance, user=Depends(get_user)):
        verified(user)
        number = payload.number.strip()
        if not number or not payload.confirmed:
            fail(422, 'Choisissez un numéro et confirmez explicitement')
        with connect() as db:
            user = verified(user, db)
            row = lock_transfer(db, user, transfer_id=transfer_id)
            if not row or not recipient(row, user) or row['sender_id'] == user['id']:
                fail()
            if row['status'] != 'pending' or row['expires_at'] <= now():
                fail(409, 'Proposition clôturée ou expirée')
            battery = battery_for(db, row['sender_id'], row['battery_id'], write=True)
            data, fingerprint, _ = snapshot(db, battery, json.loads(row['note_selection']), row['recipient_email'])
            if fingerprint != row['snapshot_hash']:
                # Commit invalidation before returning an error.
                db.execute("UPDATE transfers SET status='invalidated',closed_at=?,snapshot='{}',note_selection='[]' WHERE id=?", (now(), transfer_id))
                db.commit()
                fail(409, 'Les données ont changé. Une nouvelle proposition est nécessaire.')
            try:
                db.execute('UPDATE batteries SET user_id=?,number=?,lot_id=NULL,model_id=NULL,model_revision=NULL,acquired_on=NULL,notes=?,updated_at=? WHERE id=?',
                           (user['id'], number, data['battery']['notes'], now(), row['battery_id']))
            except IntegrityError:
                fail(409, 'Ce numéro existe déjà dans votre parc. Choisissez-en un autre.')
            for entry in data['entries']:
                device = entry['charger']
                db.execute('UPDATE entries SET locked=1,charger_id=NULL,charger_snapshot=?,charger=?,aircraft=?,notes=?,initial_percent_source=?,voltage_sag=?,heat=? WHERE id=?',
                           (json.dumps(device), ' '.join(device.get(key, '') for key in ('brand', 'model')).strip(), '',
                            *(entry[key] for key in PRIVATE_FIELDS), entry['id']))
                for annotation in entry['annotations']:
                    db.execute('UPDATE entry_annotations SET text=? WHERE id=?', (annotation['text'], annotation['id']))
            to_ref = db.execute('SELECT provenance_ref FROM users WHERE id=?', (user['id'],)).fetchone()[0]
            changed = db.execute("UPDATE transfers SET status='accepted',recipient_id=?,closed_at=?,to_ref=?,snapshot='{}',note_selection='[]' WHERE id=? AND status='pending'",
                                 (user['id'], now(), to_ref, transfer_id))
            if changed.rowcount != 1:
                fail(409, 'Proposition déjà clôturée')
            # Strip private data from the stored consent document after completion.
            row = db.execute('SELECT * FROM transfers WHERE id=?', (transfer_id,)).fetchone()
        return serialized(row, user)

    @app.post('/api/batteries/{battery_id}/entries/{entry_id}/annotations', status_code=201)
    def annotate(battery_id: str, entry_id: str, payload: AnnotationInput, user=Depends(get_user)):
        with connect() as db:
            db.execute('SELECT id FROM users WHERE id=? FOR KEY SHARE', (user['id'],)).fetchone()
            battery_for(db, user['id'], battery_id, write=True)
            if not db.execute('SELECT 1 FROM entries WHERE id=? AND battery_id=?', (entry_id, battery_id)).fetchone():
                fail()
            text = payload.text.strip()
            if not text:
                fail(422, 'Annotation vide')
            ref = db.execute('SELECT provenance_ref FROM users WHERE id=?', (user['id'],)).fetchone()[0]
            item = {'id': uid(), 'entry_id': entry_id, 'author_id': user['id'], 'origin_ref': ref, 'text': text, 'created_at': now()}
            db.execute('INSERT INTO entry_annotations VALUES (?,?,?,?,?,?)', tuple(item.values()))
            reconcile(db, now(), battery_id)
        item.pop('author_id')
        return item
