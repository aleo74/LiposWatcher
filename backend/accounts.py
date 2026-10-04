import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from argon2.exceptions import VerificationError

from .db import connect, IntegrityError
from . import mail

GENERIC = {'message': 'Si la demande peut être traitée, un message vous sera envoyé. Consultez votre messagerie.'}
CSRF_COOKIE = 'lipowatcher_csrf'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_check(request):
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return
    nonce = request.cookies.get(CSRF_COOKIE, '')
    supplied = request.headers.get('X-CSRF-Token', '')
    if (not nonce or not secrets.compare_digest(digest(nonce), supplied) or
            request.headers.get('sec-fetch-site') == 'cross-site'):
        raise HTTPException(403, 'Protection CSRF : rechargez la page')


def rate_limit(request, scope, email='', maximum=None):
    limit = maximum or int(os.environ.get('AUTH_REQUEST_MAX', '8'))
    window = max(60, int(os.environ.get('AUTH_REQUEST_WINDOW', '900')))
    current = int(time.time())
    keys = [digest(f'{scope}:ip:{request.client.host if request.client else "unknown"}')]
    if email:
        keys.append(digest(f'{scope}:email:{email.casefold()}'))
    blocked = False
    with connect() as db:
        for key in sorted(set(keys)):
            row = db.execute("""INSERT INTO request_limits VALUES (?,1,?)
                ON CONFLICT(bucket) DO UPDATE SET
                  count=CASE WHEN request_limits.started<? THEN 1 ELSE request_limits.count+1 END,
                  started=CASE WHEN request_limits.started<? THEN excluded.started ELSE request_limits.started END
                RETURNING count""", (key, current, current-window, current-window)).fetchone()
            blocked |= row[0] > limit
    if blocked:
        raise HTTPException(429, 'Trop de demandes. Réessayez plus tard.', headers={'Retry-After': str(window)})


class EmailInput(BaseModel):
    email: EmailStr


class Signup(EmailInput):
    password: str = Field(min_length=12, max_length=256)


class LinkEmail(EmailInput):
    password: str = Field(max_length=256)


class TokenInput(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class Reset(TokenInput):
    password: str = Field(min_length=12, max_length=256)


def register_accounts(app, get_user, hasher, now, uid, secure_cookie, fail):
    def issue(db, user_id, email, purpose, tasks):
        db.execute('SELECT id FROM users WHERE id=? FOR UPDATE', (user_id,)).fetchone()
        token = secrets.token_urlsafe(48)
        ttl = int(os.environ.get('VERIFY_TOKEN_MINUTES' if purpose == 'verify' else 'RESET_TOKEN_MINUTES', '60'))
        expires = (datetime.now(timezone.utc)+timedelta(minutes=max(1, ttl))).isoformat()
        db.execute('UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL', (now(), user_id, purpose))
        db.execute('INSERT INTO auth_tokens VALUES (?,?,?,?,?,NULL)', (digest(token), user_id, purpose, email, expires))
        tasks.add_task(mail.deliver, email, purpose, token)

    @app.get('/api/auth/csrf')
    def csrf(request: Request, response: Response):
        nonce = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(48)
        response.set_cookie(CSRF_COOKIE, nonce, httponly=True, secure=secure_cookie, samesite='strict', max_age=86400)
        response.headers['Cache-Control'] = 'no-store'
        return {'token': digest(nonce)}

    @app.get('/api/auth/options')
    def options():
        enabled = os.environ.get('PUBLIC_REGISTRATION', 'true').lower() == 'true'
        return {'registration': enabled and mail.available(), 'mail': mail.available()}

    @app.post('/api/auth/register', status_code=202)
    def register(payload: Signup, request: Request, tasks: BackgroundTasks):
        email = str(payload.email).casefold()
        rate_limit(request, 'signup', email)
        if os.environ.get('PUBLIC_REGISTRATION', 'true').lower() != 'true' or not mail.available():
            fail(503, 'Inscription indisponible : service de courrier non configuré')
        password_hash = hasher.hash(payload.password)
        with connect() as db:
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                fail(503, 'Le compte initial doit être créé par l’exploitant')
            if not db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                user_id = uid()
                inserted = db.execute('INSERT INTO users(id,username,password_hash,is_admin,created_at,email,provenance_ref) VALUES (?,?,?,0,?,?,?) ON CONFLICT DO NOTHING RETURNING id',
                           (user_id, 'member-'+uid(), password_hash, now(), email, secrets.token_hex(16))).fetchone()
                if inserted:
                    issue(db, user_id, email, 'verify', tasks)
        return GENERIC

    @app.post('/api/auth/email', status_code=202)
    def link_email(payload: LinkEmail, request: Request, tasks: BackgroundTasks, user=Depends(get_user)):
        email = str(payload.email).casefold()
        rate_limit(request, 'email', email)
        if not mail.available():
            fail(503, 'Service de courrier indisponible')
        with connect() as db:
            row = db.execute('SELECT * FROM users WHERE id=? FOR UPDATE', (user['id'],)).fetchone()
            try:
                valid = hasher.verify(row['password_hash'], payload.password)
            except VerificationError:
                valid = False
            if not valid:
                fail(401, 'Identifiants incorrects')
            if not db.execute('SELECT 1 FROM users WHERE email=? AND id!=?', (email, user['id'])).fetchone():
                issue(db, user['id'], email, 'verify', tasks)
        return GENERIC

    @app.post('/api/auth/resend-verification', status_code=202)
    def resend(request: Request, tasks: BackgroundTasks, user=Depends(get_user)):
        rate_limit(request, 'email', user.get('email') or user['id'])
        if not mail.available():
            fail(503, 'Service de courrier indisponible')
        with connect() as db:
            current_user = db.execute('SELECT email FROM users WHERE id=? FOR UPDATE', (user['id'],)).fetchone()
            pending = db.execute("SELECT email FROM auth_tokens WHERE user_id=? AND purpose='verify' AND used_at IS NULL ORDER BY expires_at DESC LIMIT 1", (user['id'],)).fetchone()
            email = pending['email'] if pending else current_user['email']
            if email:
                issue(db, user['id'], email, 'verify', tasks)
        return GENERIC

    @app.post('/api/auth/forgot-password', status_code=202)
    def forgot(payload: EmailInput, request: Request, tasks: BackgroundTasks):
        email = str(payload.email).casefold()
        rate_limit(request, 'email', email)
        if not mail.available():
            fail(503, 'Service de courrier indisponible')
        with connect() as db:
            row = db.execute('SELECT id FROM users WHERE email=? FOR UPDATE', (email,)).fetchone()
            if row:
                issue(db, row['id'], email, 'reset', tasks)
        return GENERIC

    @app.post('/api/auth/verify-email')
    def verify(payload: TokenInput, request: Request, user=Depends(get_user)):
        rate_limit(request, 'token', maximum=20)
        with connect() as db:
            db.execute("SELECT id FROM users WHERE id=? FOR UPDATE", (user["id"],)).fetchone()
            row = db.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND user_id=? AND purpose='verify' AND used_at IS NULL AND expires_at>? FOR UPDATE", (digest(payload.token), user["id"], now())).fetchone()
            if not row or row['user_id'] != user['id']:
                fail(400, 'Lien invalide ou expiré')
            try:
                db.execute('UPDATE users SET email=?,email_verified_at=? WHERE id=?', (row['email'], now(), row['user_id']))
            except IntegrityError:
                fail(400, 'Lien invalide ou expiré')
            db.execute('UPDATE auth_tokens SET used_at=? WHERE token_hash=?', (now(), row['token_hash']))
        return {'message': 'Adresse vérifiée. Connectez-vous à votre compte.'}

    @app.post('/api/auth/reset-password')
    def reset(payload: Reset, request: Request):
        rate_limit(request, 'token', maximum=20)
        password_hash = hasher.hash(payload.password)
        with connect() as db:
            row = db.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='reset' AND used_at IS NULL AND expires_at>?", (digest(payload.token), now())).fetchone()
            if not row:
                fail(400, 'Lien invalide ou expiré')
            owner = db.execute('SELECT email,email_verified_at FROM users WHERE id=? FOR UPDATE', (row['user_id'],)).fetchone()
            row = db.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND purpose='reset' AND used_at IS NULL AND expires_at>? FOR UPDATE", (digest(payload.token), now())).fetchone()
            if not row:
                fail(400, 'Lien invalide ou expiré')
            if owner['email'] != row['email']:
                fail(400, 'Lien invalide ou expiré')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash, row['user_id']))
            db.execute('UPDATE auth_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL', (now(), row['user_id']))
            db.execute('DELETE FROM login_sessions WHERE user_id=?', (row['user_id'],))
        return {'message': 'Mot de passe remplacé. Reconnectez-vous.'}
