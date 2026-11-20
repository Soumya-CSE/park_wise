import os
import sqlite3
from datetime import datetime

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

DEFAULT_SLOTS = [
    # (prefix, floor, vehicle_type, count)
    ("B", 0, "bike", 10),
    ("C1-", 1, "car", 12),
    ("C2-", 2, "car", 12),
    ("T", 0, "truck", 4),
]


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(seed=True):
    db = get_db()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        db.executescript(f.read())
    if seed:
        seed_defaults(db)
    db.commit()


def seed_defaults(db):
    if db.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 0:
        for prefix, floor, vtype, count in DEFAULT_SLOTS:
            for i in range(1, count + 1):
                db.execute(
                    "INSERT INTO slots (code, floor, vehicle_type) VALUES (?, ?, ?)",
                    (f"{prefix}{i:02d}", floor, vtype),
                )
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        admin_pw = os.environ.get("ADMIN_PASSWORD", "Admin@12345")
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", generate_password_hash(admin_pw)),
        )
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'attendant')",
            ("attendant", generate_password_hash(os.environ.get("ATTENDANT_PASSWORD", "Attend@12345"))),
        )


def init_app(app):
    app.teardown_appcontext(close_db)
