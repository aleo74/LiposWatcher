"""Explicit migration command; never called by application startup."""
from pathlib import Path
from alembic import command
from alembic.config import Config


def config():
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "backend" / "migrations"))
    return cfg


def upgrade():
    command.upgrade(config(), "head")


if __name__ == "__main__":
    upgrade()
