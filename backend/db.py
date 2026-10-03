import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent.parent

def database_url() -> str:
    value = os.getenv("DATABASE_URL", "sqlite:///./salonos.db")
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if value.startswith("sqlite:///") and not value.startswith("sqlite:////"):
        value = f"sqlite:///{BASE_DIR / value.removeprefix('sqlite:///')}"
    return value

def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, future=True)

SCHEMA = [
"""CREATE TABLE IF NOT EXISTS salons (
 id VARCHAR(64) PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE, name VARCHAR(160) NOT NULL,
 description TEXT, logo_url TEXT, phone VARCHAR(40), address VARCHAR(255),
 timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Tashkent', theme VARCHAR(20) NOT NULL DEFAULT 'light',
 accent_color VARCHAR(20) NOT NULL DEFAULT '#7c3aed', is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)""",
"""CREATE TABLE IF NOT EXISTS users (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 email VARCHAR(255) NOT NULL, password_hash VARCHAR(255) NOT NULL, role VARCHAR(32) NOT NULL,
 is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(salon_id,email), FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE TABLE IF NOT EXISTS masters (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 photo_url TEXT, description TEXT, phone VARCHAR(40), is_active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE TABLE IF NOT EXISTS services (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 description TEXT, price INTEGER NOT NULL DEFAULT 0, duration_minutes INTEGER NOT NULL DEFAULT 60,
 is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE TABLE IF NOT EXISTS master_services (
 master_id VARCHAR(64) NOT NULL, service_id VARCHAR(64) NOT NULL,
 PRIMARY KEY(master_id,service_id), FOREIGN KEY(master_id) REFERENCES masters(id),
 FOREIGN KEY(service_id) REFERENCES services(id)
)""",
"""CREATE TABLE IF NOT EXISTS clients (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 phone VARCHAR(40) NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(salon_id,phone),
 FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE TABLE IF NOT EXISTS appointments (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, client_id VARCHAR(64) NOT NULL,
 master_id VARCHAR(64) NOT NULL, service_id VARCHAR(64) NOT NULL, start_at TIMESTAMP NOT NULL,
 end_at TIMESTAMP NOT NULL, price INTEGER NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(salon_id) REFERENCES salons(id), FOREIGN KEY(client_id) REFERENCES clients(id),
 FOREIGN KEY(master_id) REFERENCES masters(id), FOREIGN KEY(service_id) REFERENCES services(id)
)""",
"""CREATE TABLE IF NOT EXISTS master_schedules (
 id VARCHAR(64) PRIMARY KEY, master_id VARCHAR(64) NOT NULL, weekday INTEGER NOT NULL,
 start_time VARCHAR(5) NOT NULL, end_time VARCHAR(5) NOT NULL, is_working BOOLEAN NOT NULL DEFAULT TRUE,
 UNIQUE(master_id,weekday), FOREIGN KEY(master_id) REFERENCES masters(id)
)""",
"""CREATE TABLE IF NOT EXISTS schedule_exceptions (
 id VARCHAR(64) PRIMARY KEY, master_id VARCHAR(64) NOT NULL, date VARCHAR(10) NOT NULL,
 start_time VARCHAR(5), end_time VARCHAR(5), is_day_off BOOLEAN NOT NULL DEFAULT FALSE,
 UNIQUE(master_id,date), FOREIGN KEY(master_id) REFERENCES masters(id)
)""",
"""CREATE TABLE IF NOT EXISTS sessions (
 token VARCHAR(128) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, expires_at TIMESTAMP NOT NULL,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id)
)""",
"""CREATE INDEX IF NOT EXISTS idx_appointments_master_time ON appointments(master_id,start_at,end_at)""",
"""CREATE INDEX IF NOT EXISTS idx_appointments_salon_time ON appointments(salon_id,start_at)"""
]

def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = ON"))
        for statement in SCHEMA:
            conn.execute(text(statement))
