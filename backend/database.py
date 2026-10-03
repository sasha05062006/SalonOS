import os
from pathlib import Path
from sqlalchemy import create_engine, text

BASE_DIR=Path(__file__).resolve().parent.parent
DATABASE_URL=os.getenv("DATABASE_URL") or os.getenv("DB_URL") or f"sqlite:///{BASE_DIR/'salonos.db'}"
if DATABASE_URL.startswith("postgres://"): DATABASE_URL=DATABASE_URL.replace("postgres://","postgresql+psycopg://",1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL: DATABASE_URL=DATABASE_URL.replace("postgresql://","postgresql+psycopg://",1)
_engine=create_engine(DATABASE_URL,pool_pre_ping=True,future=True)

SCHEMA=[
"CREATE TABLE IF NOT EXISTS salons (id TEXT PRIMARY KEY,slug TEXT UNIQUE NOT NULL,name TEXT NOT NULL,description TEXT DEFAULT '',logo_url TEXT DEFAULT '',phone TEXT DEFAULT '',address TEXT DEFAULT '',timezone TEXT DEFAULT 'Asia/Tashkent',theme TEXT DEFAULT 'light',accent_color TEXT DEFAULT '#7c3aed',status TEXT DEFAULT 'DRAFT',is_active BOOLEAN DEFAULT TRUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY,salon_id TEXT,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,pin_hash TEXT,role TEXT DEFAULT 'admin',master_id TEXT,is_active BOOLEAN DEFAULT TRUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at TIMESTAMP NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS subscriptions (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL UNIQUE,plan TEXT DEFAULT 'START',status TEXT DEFAULT 'NONE',price INTEGER DEFAULT 0,currency TEXT DEFAULT 'UZS',started_at TIMESTAMP,expires_at TIMESTAMP,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS masters (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL,name TEXT NOT NULL,photo_url TEXT DEFAULT '',description TEXT DEFAULT '',phone TEXT DEFAULT '',is_active BOOLEAN DEFAULT TRUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS services (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT DEFAULT '',price INTEGER DEFAULT 0,duration_minutes INTEGER DEFAULT 60,is_active BOOLEAN DEFAULT TRUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS master_services (master_id TEXT NOT NULL,service_id TEXT NOT NULL,PRIMARY KEY(master_id,service_id))",
"CREATE TABLE IF NOT EXISTS master_schedules (id TEXT PRIMARY KEY,master_id TEXT NOT NULL,weekday INTEGER NOT NULL,start_time TEXT DEFAULT '10:00',end_time TEXT DEFAULT '19:00',is_working BOOLEAN DEFAULT TRUE)",
"CREATE TABLE IF NOT EXISTS schedule_exceptions (id TEXT PRIMARY KEY,master_id TEXT NOT NULL,date TEXT NOT NULL,start_time TEXT,end_time TEXT,is_day_off BOOLEAN DEFAULT FALSE)",
"CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL,name TEXT NOT NULL,phone TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS appointments (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL,client_id TEXT NOT NULL,master_id TEXT NOT NULL,service_id TEXT NOT NULL,start_at TIMESTAMP NOT NULL,end_at TIMESTAMP NOT NULL,price INTEGER DEFAULT 0,status TEXT DEFAULT 'pending',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS support_messages (id TEXT PRIMARY KEY,salon_id TEXT NOT NULL,sender_role TEXT NOT NULL,sender_user_id TEXT,body TEXT NOT NULL,is_read BOOLEAN DEFAULT FALSE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"CREATE TABLE IF NOT EXISTS announcements (id TEXT PRIMARY KEY,salon_id TEXT,title TEXT NOT NULL,body TEXT NOT NULL,kind TEXT DEFAULT 'info',is_active BOOLEAN DEFAULT TRUE,starts_at TIMESTAMP,ends_at TIMESTAMP,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
]
def get_engine(): return _engine
def init_db():
    with _engine.begin() as conn:
        for sql in SCHEMA: conn.execute(text(sql))
        if conn.dialect.name=="sqlite":
            cols={r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        else:
            cols={r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='users'")).fetchall()}
        if "pin_hash" not in cols: conn.execute(text("ALTER TABLE users ADD COLUMN pin_hash TEXT"))
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_users_salon_role ON users(salon_id,role,is_active)",
            "CREATE INDEX IF NOT EXISTS idx_appointments_salon_day ON appointments(salon_id,start_at)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)"
        ]: conn.execute(text(sql))
