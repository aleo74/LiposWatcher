import csv
import hashlib
import io
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, FiniteFloat, HttpUrl, TypeAdapter, ValidationError, field_validator, model_validator

from .db import connect, check_schema, dispose, IntegrityError
from .logic import entry_metrics
from .accounts import csrf_check
from .transfers import reconcile

hasher = PasswordHasher()
COOKIE = "lipowatcher_session"
SECURE_COOKIE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
SESSION_DAYS = 30
LOGIN_MAX_ATTEMPTS = max(1, int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")))
LOGIN_WINDOW_SECONDS = max(60, int(os.environ.get("LOGIN_WINDOW_SECONDS", "900")))
LOGIN_LOCK_SECONDS = max(60, int(os.environ.get("LOGIN_LOCK_SECONDS", "900")))
FRONT = Path(__file__).parent.parent / "frontend" / "dist"
GUIDE_STEPS = {"inspection", "identity", "connections", "program", "limits", "first_sessions", "reference", "storage"}
GUIDE_STEPS.update({"monitoring", "cooling"})
HTTP_URL = TypeAdapter(HttpUrl)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def rowdict(row):
    return dict(row) if row else None


def fail(code=404, message="Introuvable"):
    raise HTTPException(code, message)


def login_buckets(request: Request, username: str):
    address = request.client.host if request.client else "unknown"
    return [hashlib.sha256(value.encode()).hexdigest() for value in
            ("user:" + username.casefold(), "address:" + address)]


def lock_login_buckets(db, keys):
    # A stable order prevents deadlocks when requests share some buckets.
    # INSERT handles missing rows; FOR UPDATE holds each counter through auth.
    for key in sorted(set(keys)):
        db.execute("INSERT INTO login_attempts VALUES (?,0,?,0) ON CONFLICT(bucket_key) DO NOTHING", (key, int(time.time())))
        db.execute("SELECT bucket_key FROM login_attempts WHERE bucket_key=? FOR UPDATE", (key,)).fetchone()


def check_login_limit(db, keys):
    current = int(time.time())
    rows = db.execute(f"SELECT locked_until FROM login_attempts WHERE bucket_key IN ({','.join('?' for _ in keys)})", keys).fetchall()
    retry = max((row["locked_until"] - current for row in rows), default=0)
    if retry > 0:
        raise HTTPException(429, "Trop de tentatives. Réessayez plus tard.", headers={"Retry-After": str(retry)})


def record_login_failure(db, keys):
    current = int(time.time())
    for key in keys:
        row = db.execute("SELECT failed_count, window_started FROM login_attempts WHERE bucket_key=?", (key,)).fetchone()
        if row and current - row["window_started"] < LOGIN_WINDOW_SECONDS:
            count, started = row["failed_count"] + 1, row["window_started"]
        else:
            count, started = 1, current
        locked = current + LOGIN_LOCK_SECONDS if count >= LOGIN_MAX_ATTEMPTS else 0
        db.execute("INSERT INTO login_attempts VALUES (?,?,?,?) ON CONFLICT(bucket_key) DO UPDATE SET failed_count=excluded.failed_count, window_started=excluded.window_started, locked_until=excluded.locked_until", (key, count, started, locked))


def get_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        fail(401, "Connexion requise")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute("SELECT u.id, u.username, u.is_admin, u.email, u.email_verified_at FROM login_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (digest, now())).fetchone()
    if not row:
        fail(401, "Session expirée")
    return dict(row)


def check_origin(request: Request):
    csrf_check(request)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlsplit
            origin_url = urlsplit(origin)
            request_url = urlsplit(str(request.base_url))
            if origin_url.netloc != request_url.netloc or origin_url.scheme != request_url.scheme:
                fail(403, "Origine non autorisée")


def battery_for(db, user_id, battery_id, write=False):
    lock = "FOR UPDATE" if write else "FOR SHARE"
    row = db.execute(f"SELECT * FROM batteries WHERE id=? AND user_id=? {lock}", (battery_id, user_id)).fetchone()
    if not row:
        fail()
    return dict(row)


def entry_for(db, user_id, battery_id, entry_id, write=False):
    battery = battery_for(db, user_id, battery_id, write=write)
    row = db.execute("SELECT * FROM entries WHERE id=? AND battery_id=?", (entry_id, battery_id)).fetchone()
    if not row:
        fail()
    return battery, dict(row)


def decode_entry(row, nominal_mah=None):
    entry = dict(row)
    entry.pop("author_id", None)
    for key in ("before_v", "after_v", "resistance_mohm"):
        entry[key] = json.loads(entry[key]) if entry[key] else None
    if nominal_mah:
        entry["metrics"] = entry_metrics(entry, nominal_mah)
    return entry


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=12, max_length=256)


class BatteryInput(BaseModel):
    number: str = Field(min_length=1, max_length=30)
    brand: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=100)
    range: str = Field(default="", max_length=100)
    connector: str = Field(default="", max_length=100)
    weight_g: FiniteFloat | None = Field(default=None, gt=0, le=100000)
    charge_max_a: FiniteFloat | None = Field(default=None, gt=0, le=1000)
    charge_final_v: FiniteFloat | None = Field(default=None, gt=0, le=100)
    chemistry: Literal["LiPo", "LiHV", "Li-ion", "LiFe"] = "LiPo"
    cells: int = Field(ge=1, le=16)
    nominal_mah: int = Field(gt=0, le=100000)
    c_rating: FiniteFloat | None = Field(default=None, gt=0, le=1000)
    acquired_on: date | None = None
    condition: Literal["new", "used"] = "new"
    prior_history: Literal["known", "unknown"] = "unknown"
    prior_cycles: int | None = Field(default=None, ge=0, le=1000000)
    status: Literal["active", "storage", "check", "retired"] = "active"
    notes: str = Field(default="", max_length=5000)
    charge_c: FiniteFloat | None = Field(default=None, gt=0, le=50)
    charge_rate_source: str = Field(default="", max_length=250)
    manufacturer_guide_url: str = Field(default="", max_length=1000)
    manufacturer_guide_scope: str = Field(default="", max_length=300)

    @field_validator("number")
    @classmethod
    def number_clean(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Numéro vide")
        return value

    @field_validator("manufacturer_guide_url")
    @classmethod
    def valid_manufacturer_url(cls, value):
        if not value:
            return ""
        try:
            parsed = HTTP_URL.validate_python(value)
        except ValidationError as exc:
            raise ValueError("Indiquer une URL fabricant HTTP ou HTTPS valide") from exc
        if parsed.username or parsed.password:
            raise ValueError("L'URL fabricant ne doit pas contenir d'identifiants")
        return value.strip()

    @model_validator(mode="after")
    def check_sources(self):
        if any(v is not None for v in (self.charge_c, self.charge_max_a, self.charge_final_v)) and not self.charge_rate_source.strip():
            raise ValueError("Indiquer la source du taux maximal de charge déclaré")
        if self.manufacturer_guide_url and not self.manufacturer_guide_scope.strip():
            raise ValueError("Indiquer le champ d'application de la notice fabricant")
        return self


class BatteryBatch(BaseModel):
    numbers: list[str] = Field(min_length=1, max_length=100)
    template: BatteryInput


class EntryInput(BaseModel):
    kind: Literal["charge", "test", "storage", "flight", "baseline"] = "charge"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    added_mah: int | None = Field(default=None, ge=0, le=1000000)
    discharged_mah: int | None = Field(default=None, ge=0, le=1000000)
    cutoff_v: FiniteFloat | None = Field(default=None, gt=0, le=100)
    charger: str = Field(default="", max_length=100)
    charger_id: str | None = None
    charger_channel: int | None = Field(default=None, ge=1, le=64)
    initial_percent: FiniteFloat | None = Field(default=None, ge=0, le=100)
    initial_percent_source: str = Field(default="", max_length=100)
    final_percent: FiniteFloat | None = Field(default=None, ge=0, le=100)
    before_v: list[FiniteFloat | None] | None = None
    after_v: list[FiniteFloat | None] | None = None
    resistance_mohm: list[FiniteFloat | None] | None = None
    current_a: FiniteFloat | None = Field(default=None, gt=0, le=1000)
    ambient_c: FiniteFloat | None = Field(default=None, ge=-50, le=100)
    aircraft: str = Field(default="", max_length=100)
    duration_min: FiniteFloat | None = Field(default=None, gt=0, le=10000)
    voltage_sag: str = Field(default="", max_length=500)
    heat: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def check_values(self):
        if self.kind == "charge" and self.added_mah is None:
            raise ValueError("Indiquer les mAh ajoutés")
        if self.kind == "test" and self.discharged_mah is None:
            raise ValueError("Indiquer les mAh déchargés")
        if self.initial_percent is not None and self.final_percent is not None and self.final_percent <= self.initial_percent and self.kind == "charge":
            raise ValueError("Le pourcentage final doit dépasser le pourcentage initial")
        for values, lower, upper, label in ((self.before_v, 0, 5, "Tensions avant"), (self.after_v, 0, 5, "Tensions après"), (self.resistance_mohm, 0, 1000, "Résistances")):
            if values is not None and any(v is not None and (v <= lower or v > upper) for v in values):
                raise ValueError(f"{label} hors limites")
        return self


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_schema()
    try:
        yield
    finally:
        dispose()


app = FastAPI(title="LipoWatcher", lifespan=lifespan, dependencies=[Depends(check_origin)])


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default errors can echo submitted passwords or mail tokens.
    return JSONResponse(status_code=422, content={'detail': [
        {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()]})


@app.get("/api/health")
def health():
    try:
        check_schema()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


@app.get("/api/auth/setup")
def setup_state():
    with connect() as db:
        return {"needed": db.execute("SELECT NOT EXISTS(SELECT 1 FROM users)").fetchone()[0] == 1}


def set_cookie(response, user_id, transaction=None):
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with (nullcontext(transaction) if transaction is not None else connect()) as db:
        db.execute("INSERT INTO login_sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires.isoformat()))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=SECURE_COOKIE, samesite="strict")


@app.post("/api/auth/setup", status_code=201)
def setup(payload: Credentials, response: Response):
    with connect() as db:
        try:
            # This advisory lock is ONLY for the one-time empty-base bootstrap.
            db.execute("SELECT pg_advisory_xact_lock(73492004)")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                fail(403, "Compte initial déjà créé")
            user_id = uid()
            db.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at,provenance_ref) VALUES (?,?,?,?,?,?)", (user_id, payload.username, hasher.hash(payload.password), 1, now(), secrets.token_hex(16)))
            db.commit()
        except IntegrityError:
            fail(409, "Identifiant déjà utilisé")
    set_cookie(response, user_id)
    return {"id": user_id, "username": payload.username, "is_admin": 1}


class LoginInput(BaseModel):
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


DUMMY_PASSWORD_HASH = hasher.hash(secrets.token_urlsafe(48))


@app.post("/api/auth/login")
def login(payload: LoginInput, response: Response, request: Request):
    buckets = login_buckets(request, payload.username)
    with connect() as db:
        # Checking, incrementing and session creation share one DB transaction.
        # No parallel worker can pass the limit using an old counter or log in
        # with a hash superseded by a concurrent password reset.
        lock_login_buckets(db, buckets)
        check_login_limit(db, buckets)
        user = db.execute("SELECT * FROM users WHERE lower(username)=lower(?) OR lower(email)=lower(?) FOR SHARE", (payload.username, payload.username.casefold())).fetchone()
        try:
            valid = hasher.verify(user["password_hash"] if user else DUMMY_PASSWORD_HASH, payload.password) and user is not None
        except (VerifyMismatchError, VerificationError):
            valid = False
        if not valid:
            record_login_failure(db, buckets)
            db.commit()  # Keep the failure even though the HTTP response is 401.
            fail(401, "Identifiants incorrects")
        db.executemany("UPDATE login_attempts SET failed_count=0,locked_until=0 WHERE bucket_key=?", [(key,) for key in sorted(set(buckets))])
        set_cookie(response, user["id"], db)
    return {key: user[key] for key in ("id", "username", "is_admin", "email", "email_verified_at")}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user=Depends(get_user)):
    token = request.cookies.get(COOKIE)
    with connect() as db:
        db.execute("DELETE FROM login_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(get_user)):
    return user


@app.post("/api/users", status_code=201)
def create_user(payload: Credentials, user=Depends(get_user)):
    if not user["is_admin"]:
        fail(403, "Administrateur requis")
    user_id = uid()
    try:
        with connect() as db:
            db.execute("INSERT INTO users(id,username,password_hash,is_admin,created_at,provenance_ref) VALUES (?,?,?,?,?,?)", (user_id, payload.username, hasher.hash(payload.password), 0, now(), secrets.token_hex(16)))
    except IntegrityError:
        fail(409, "Identifiant déjà utilisé")
    return {"id": user_id, "username": payload.username}


@app.get("/api/batteries")
def batteries(q: str = "", status: str = "", user=Depends(get_user)):
    with connect() as db:
        rows = db.execute("SELECT b.*, (SELECT COUNT(*) FROM entries e WHERE e.battery_id=b.id AND e.kind='charge') AS charge_count FROM batteries b WHERE b.user_id=? AND b.number ILIKE ? AND (?='' OR b.status=?) ORDER BY b.number", (user["id"], f"%{q}%", status, status)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/batteries", status_code=201)
def add_battery(payload: BatteryInput, user=Depends(get_user)):
    data = payload.model_dump(mode="json")
    data.update(id=uid(), user_id=user["id"], created_at=now(), updated_at=now())
    try:
        with connect() as db:
            db.execute(f"INSERT INTO batteries ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values()))
    except IntegrityError:
        fail(409, "Ce numéro existe déjà")
    return data


@app.post("/api/batteries/batch", status_code=201)
def add_batteries_batch(payload: BatteryBatch, user=Depends(get_user)):
    base = payload.template.model_dump(mode="json")
    numbers = [BatteryInput.model_validate({**base, "number": n}).number for n in payload.numbers]
    if len(set(numbers)) != len(numbers):
        fail(409, "Numéros en double dans le lot")
    created = []
    try:
        with connect() as db:
            for number in numbers:
                data = {**base, "number": number, "id": uid(), "user_id": user["id"], "created_at": now(), "updated_at": now()}
                db.execute(f"INSERT INTO batteries ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values()))
                created.append(data)
    except IntegrityError:
        fail(409, "Un numéro du lot existe déjà")
    return created


@app.get("/api/batteries/{battery_id}")
def battery_detail(battery_id: str, user=Depends(get_user)):
    with connect() as db:
        battery = battery_for(db, user["id"], battery_id)
        entries = [decode_entry(r, battery["nominal_mah"]) for r in db.execute("SELECT * FROM entries WHERE battery_id=? ORDER BY occurred_at DESC", (battery_id,))]
        steps = {row["step_key"]: row["completed_at"] for row in db.execute("SELECT step_key, completed_at FROM guide_steps WHERE battery_id=?", (battery_id,))}
        for entry in entries:
            entry["annotations"] = [dict(r) for r in db.execute("SELECT id,origin_ref,text,created_at FROM entry_annotations WHERE entry_id=? ORDER BY created_at", (entry["id"],))]
        battery["transfer_chain"] = [dict(r) for r in db.execute("SELECT from_ref,to_ref,closed_at FROM transfers WHERE battery_id=? AND status='accepted' ORDER BY closed_at", (battery_id,))]
    battery["entries"] = entries
    battery["guide_steps"] = steps
    battery["charge_count"] = sum(e["kind"] == "charge" for e in entries)
    battery["total_added_mah"] = sum(e["added_mah"] or 0 for e in entries)
    return battery


class GuideStepInput(BaseModel):
    done: bool


@app.put("/api/batteries/{battery_id}/guide/{step_key}")
def set_guide_step(battery_id: str, step_key: str, payload: GuideStepInput, user=Depends(get_user)):
    if step_key not in GUIDE_STEPS:
        fail(404, "Étape inconnue")
    with connect() as db:
        battery_for(db, user["id"], battery_id, write=True)
        if payload.done:
            db.execute("INSERT INTO guide_steps VALUES (?,?,?) ON CONFLICT(battery_id, step_key) DO UPDATE SET completed_at=excluded.completed_at", (battery_id, step_key, now()))
        else:
            db.execute("DELETE FROM guide_steps WHERE battery_id=? AND step_key=?", (battery_id, step_key))
        reconcile(db, now(), battery_id)
    return {"step_key": step_key, "done": payload.done}


@app.put("/api/batteries/{battery_id}")
def update_battery(battery_id: str, payload: BatteryInput, user=Depends(get_user)):
    data = payload.model_dump(mode="json")
    data["updated_at"] = now()
    try:
        with connect() as db:
            battery_for(db, user["id"], battery_id, write=True)
            db.execute(f"UPDATE batteries SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), battery_id))
            reconcile(db, now(), battery_id)
    except IntegrityError:
        fail(409, "Ce numéro existe déjà")
    return battery_detail(battery_id, user)


@app.delete("/api/batteries/{battery_id}", status_code=204)
def delete_battery(battery_id: str, user=Depends(get_user)):
    with connect() as db:
        battery_for(db, user["id"], battery_id, write=True)
        if db.execute("SELECT 1 FROM transfers WHERE battery_id=? AND status='accepted'", (battery_id,)).fetchone():
            fail(409, "Historique transféré conservé : retirez la batterie via son statut")
        db.execute("DELETE FROM batteries WHERE id=?", (battery_id,))
        reconcile(db, now(), battery_id)


def validate_cells(payload, battery):
    for key in ("before_v", "after_v", "resistance_mohm"):
        value = getattr(payload, key)
        if value is not None and len(value) != battery["cells"]:
            fail(422, f"{key} : {battery['cells']} valeurs attendues")


def write_entry(payload, battery_id, user_id, entry_id=None):
    with connect() as db:
        db.execute("SELECT id FROM users WHERE id=? FOR KEY SHARE", (user_id,)).fetchone()
        battery = battery_for(db, user_id, battery_id, write=True)
        if entry_id:
            _, previous_entry = entry_for(db, user_id, battery_id, entry_id)
            if previous_entry["locked"]:
                fail(409, "Relevé transféré verrouillé : ajoutez une annotation datée")
        validate_cells(payload, battery)
        data = payload.model_dump(mode="json")
        if payload.charger_id:
            charger = db.execute("SELECT * FROM chargers WHERE id=? AND user_id=? FOR SHARE", (payload.charger_id, user_id)).fetchone()
            if not charger:
                fail(404, "Chargeur introuvable")
            previous = db.execute("SELECT charger_id, charger_channel, charger, charger_snapshot FROM entries WHERE id=?", (entry_id,)).fetchone() if entry_id else None
            retained = previous and previous['charger_id'] == payload.charger_id and previous['charger_channel'] == payload.charger_channel
            if charger['status'] == 'archived' and not retained:
                fail(422, "Chargeur archivé : choisissez un appareil actif")
            if payload.charger_channel is not None and payload.charger_channel > charger['channels'] and not retained:
                fail(422, "Canal hors limites du chargeur")
            # Snapshot the physical device name; keep the historical text on edits.
            data['charger'] = previous['charger'] if retained else charger['name']
            data['charger_snapshot'] = previous['charger_snapshot'] if retained and previous['charger_snapshot'] else json.dumps({'brand': charger['brand'], 'model': charger['model']})
        elif payload.charger_channel is not None:
            fail(422, "Sélectionnez un chargeur pour indiquer un canal")
        else:
            data["charger_snapshot"] = None
        for key in ("before_v", "after_v", "resistance_mohm"):
            data[key] = json.dumps(data[key]) if data[key] is not None else None
        data["updated_at"] = now()
        if entry_id:
            db.execute(f"UPDATE entries SET {','.join(k+'=?' for k in data)} WHERE id=?", (*data.values(), entry_id))
        else:
            entry_id = uid()
            origin = db.execute("SELECT provenance_ref FROM users WHERE id=?", (user_id,)).fetchone()[0]
            data.update(id=entry_id, battery_id=battery_id, created_at=now(), author_id=user_id, origin_ref=origin)
            db.execute(f"INSERT INTO entries ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values()))
        reconcile(db, now(), battery_id)
        row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        return decode_entry(row, battery["nominal_mah"])


@app.post("/api/batteries/{battery_id}/entries", status_code=201)
def add_entry(battery_id: str, payload: EntryInput, user=Depends(get_user)):
    return write_entry(payload, battery_id, user["id"])


@app.put("/api/batteries/{battery_id}/entries/{entry_id}")
def update_entry(battery_id: str, entry_id: str, payload: EntryInput, user=Depends(get_user)):
    return write_entry(payload, battery_id, user["id"], entry_id)


@app.delete("/api/batteries/{battery_id}/entries/{entry_id}", status_code=204)
def delete_entry(battery_id: str, entry_id: str, user=Depends(get_user)):
    with connect() as db:
        _, previous = entry_for(db, user["id"], battery_id, entry_id, write=True)
        if previous["locked"]:
            fail(409, "Relevé transféré verrouillé : ajoutez une annotation datée")
        db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        reconcile(db, now(), battery_id)


@app.get("/api/batteries/{battery_id}/qr.png")
def battery_qr(battery_id: str, user=Depends(get_user)):
    with connect() as db:
        battery_for(db, user["id"], battery_id)
    image = qrcode.make(f"lipowatcher:v1:{battery_id}")
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return StreamingResponse(output, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@app.get("/api/export/{table}.csv")
def export_csv(table: Literal["batteries", "entries"], user=Depends(get_user)):
    with connect() as db:
        # One MVCC snapshot avoids stale-owner checks across export queries.
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if table == "batteries":
            rows = db.execute("SELECT * FROM batteries WHERE user_id=? ORDER BY number", (user["id"],)).fetchall()
        else:
            rows = db.execute("SELECT e.*, b.number AS battery_number FROM entries e JOIN batteries b ON b.id=e.battery_id WHERE b.user_id=? ORDER BY e.occurred_at", (user["id"],)).fetchall()
            rows = [{**dict(row), 'annotations': json.dumps([dict(a) for a in db.execute(
                'SELECT origin_ref,text,created_at FROM entry_annotations WHERE entry_id=? ORDER BY created_at', (row['id'],))], ensure_ascii=False)} for row in rows]
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=[key for key in rows[0].keys() if key != "author_id"])
        writer.writeheader()
        text_fields = ({"number", "brand", "model", "range", "connector", "chemistry", "condition", "prior_history", "status", "notes", "charge_rate_source", "manufacturer_guide_url", "manufacturer_guide_scope"}
                       if table == "batteries" else
                       {"battery_number", "kind", "charger", "initial_percent_source", "aircraft", "voltage_sag", "heat", "notes"})
        writer.writerows([{key: csv_safe(value) if key in text_fields else value for key, value in dict(row).items() if key != "author_id"} for row in rows])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})


def csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


from .accounts import register_accounts
from .transfers import register_transfers
register_accounts(app, get_user, hasher, now, uid, SECURE_COOKIE, fail)
register_transfers(app, get_user, battery_for, now, uid, fail)


@app.middleware("http")
async def sensitive_headers(request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


from .inventory import register_inventory
register_inventory(app, get_user, BatteryInput, battery_for, now, uid, fail)


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    target = (FRONT / path).resolve()
    if target.is_file() and FRONT.resolve() in target.parents:
        return FileResponse(target)
    index = FRONT / "index.html"
    if index.exists() and not path.startswith("api/"):
        return FileResponse(index)
    fail()
