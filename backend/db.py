import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "sqlite:///./salonos.db")
    # Some providers expose postgres:// while SQLAlchemy expects postgresql://.
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def get_engine() -> Engine:
    url = database_url()
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        # Keep the local DB in the project root.
        db_path = Path(__file__).resolve().parent.parent / url.removeprefix("sqlite:///")
        url = f"sqlite:///{db_path}"
    return create_engine(url, pool_pre_ping=True)


def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = ON"))

        # First foundation tables. Business tables will be added through
        # migrations rather than destructive schema resets.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS salons (
                id INTEGER PRIMARY KEY,
                slug VARCHAR(80) NOT NULL UNIQUE,
                name VARCHAR(160) NOT NULL,
                phone VARCHAR(40),
                address VARCHAR(255),
                timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Tashkent',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                salon_id INTEGER NOT NULL,
                email VARCHAR(255) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(32) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (salon_id, email),
                FOREIGN KEY (salon_id) REFERENCES salons(id)
            )
        """))
