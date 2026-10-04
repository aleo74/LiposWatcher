"""Complete empty PostgreSQL schema; no import or seed data."""
from alembic import op

revision = "0001_postgresql"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE TABLE users (
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE login_sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);
CREATE TABLE batteries (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  number TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  chemistry TEXT NOT NULL, cells INTEGER NOT NULL, nominal_mah INTEGER NOT NULL,
  c_rating DOUBLE PRECISION, acquired_on TEXT, condition TEXT NOT NULL DEFAULT 'new',
  prior_history TEXT NOT NULL DEFAULT 'unknown', prior_cycles INTEGER,
  status TEXT NOT NULL DEFAULT 'active', notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id, number)
);
CREATE INDEX batteries_user_number ON batteries(user_id, number);
CREATE TABLE entries (
  id TEXT PRIMARY KEY, battery_id TEXT NOT NULL REFERENCES batteries(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, occurred_at TEXT NOT NULL, added_mah INTEGER,
  discharged_mah INTEGER, cutoff_v DOUBLE PRECISION, charger TEXT NOT NULL DEFAULT '',
  initial_percent DOUBLE PRECISION, initial_percent_source TEXT NOT NULL DEFAULT '',
  final_percent DOUBLE PRECISION, before_v TEXT, after_v TEXT, resistance_mohm TEXT,
  current_a DOUBLE PRECISION, ambient_c DOUBLE PRECISION, aircraft TEXT NOT NULL DEFAULT '',
  duration_min DOUBLE PRECISION, voltage_sag TEXT NOT NULL DEFAULT '', heat TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX entries_battery_date ON entries(battery_id, occurred_at DESC);

ALTER TABLE batteries ADD COLUMN charge_c DOUBLE PRECISION;
ALTER TABLE batteries ADD COLUMN charge_rate_source TEXT NOT NULL DEFAULT '';
ALTER TABLE batteries ADD COLUMN manufacturer_guide_url TEXT NOT NULL DEFAULT '';
ALTER TABLE batteries ADD COLUMN manufacturer_guide_scope TEXT NOT NULL DEFAULT '';

CREATE TABLE guide_steps (
  battery_id TEXT NOT NULL REFERENCES batteries(id) ON DELETE CASCADE,
  step_key TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  PRIMARY KEY (battery_id, step_key)
);

CREATE TABLE login_attempts (
  bucket_key TEXT PRIMARY KEY,
  failed_count INTEGER NOT NULL,
  window_started INTEGER NOT NULL,
  locked_until INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE battery_models (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'private', revision INTEGER NOT NULL DEFAULT 1,
  data TEXT NOT NULL, published_data TEXT, pending_data TEXT,
  published_revision INTEGER, origin_model_id TEXT REFERENCES battery_models(id),
  origin_revision INTEGER, rejection_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX models_owner ON battery_models(user_id, status);
CREATE TABLE model_revisions (
  id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES battery_models(id),
  revision INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
  event TEXT NOT NULL, data TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE purchase_lots (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, acquired_on TEXT, seller TEXT NOT NULL DEFAULT '',
  total_price DOUBLE PRECISION, currency TEXT NOT NULL DEFAULT 'EUR', notes TEXT NOT NULL DEFAULT '',
  condition TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX lots_owner ON purchase_lots(user_id);
CREATE TABLE chargers (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  channels INTEGER NOT NULL DEFAULT 1, firmware TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX chargers_owner ON chargers(user_id);
ALTER TABLE batteries ADD COLUMN model_id TEXT REFERENCES battery_models(id);
ALTER TABLE batteries ADD COLUMN model_revision INTEGER;
ALTER TABLE batteries ADD COLUMN lot_id TEXT REFERENCES purchase_lots(id) ON DELETE SET NULL;
ALTER TABLE batteries ADD COLUMN range TEXT NOT NULL DEFAULT '';
ALTER TABLE batteries ADD COLUMN connector TEXT NOT NULL DEFAULT '';
ALTER TABLE batteries ADD COLUMN weight_g DOUBLE PRECISION;
ALTER TABLE batteries ADD COLUMN charge_max_a DOUBLE PRECISION;
ALTER TABLE batteries ADD COLUMN charge_final_v DOUBLE PRECISION;
ALTER TABLE entries ADD COLUMN charger_id TEXT REFERENCES chargers(id);
ALTER TABLE entries ADD COLUMN charger_channel INTEGER;

ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN email_verified_at TEXT;
ALTER TABLE users ADD COLUMN provenance_ref TEXT;

CREATE UNIQUE INDEX users_provenance ON users(provenance_ref);
CREATE TABLE auth_tokens (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL, email TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE request_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, started INTEGER NOT NULL);
ALTER TABLE entries ADD COLUMN author_id TEXT REFERENCES users(id);
ALTER TABLE entries ADD COLUMN origin_ref TEXT;
ALTER TABLE entries ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;
ALTER TABLE entries ADD COLUMN charger_snapshot TEXT;
CREATE TABLE entry_annotations (
 id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 author_id TEXT NOT NULL REFERENCES users(id), origin_ref TEXT NOT NULL,
 text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE transfers (
 id TEXT PRIMARY KEY, battery_id TEXT NOT NULL, sender_id TEXT NOT NULL REFERENCES users(id),
 recipient_email TEXT NOT NULL, recipient_id TEXT REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 snapshot TEXT NOT NULL, snapshot_hash TEXT NOT NULL, note_selection TEXT NOT NULL,
 created_at TEXT NOT NULL, closed_at TEXT, from_ref TEXT NOT NULL, to_ref TEXT
);
CREATE UNIQUE INDEX transfers_one_pending ON transfers(battery_id) WHERE status='pending';
CREATE INDEX transfers_sender ON transfers(sender_id);
CREATE INDEX transfers_recipient ON transfers(recipient_email);

CREATE UNIQUE INDEX users_username_ci ON users(lower(username));
CREATE UNIQUE INDEX users_email_ci ON users(lower(email)) WHERE email IS NOT NULL;

"""

def upgrade():
    for statement in SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)

def downgrade():
    for table in ['transfers', 'entry_annotations', 'request_limits', 'auth_tokens', 'model_revisions', 'entries', 'guide_steps', 'batteries', 'battery_models', 'purchase_lots', 'chargers', 'login_attempts', 'login_sessions', 'users']:
        op.execute("DROP TABLE " + table + " CASCADE")
