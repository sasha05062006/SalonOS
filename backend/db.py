import os
import secrets
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

BASE_DIR = Path(__file__).resolve().parent.parent

def uid() -> str:
    return secrets.token_urlsafe(12)

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
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
)""",
"""CREATE TABLE IF NOT EXISTS users (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, name VARCHAR(160) NOT NULL,
 email VARCHAR(255) NOT NULL, password_hash VARCHAR(255) NOT NULL, role VARCHAR(32) NOT NULL, master_id VARCHAR(64),
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
"""CREATE INDEX IF NOT EXISTS idx_appointments_salon_time ON appointments(salon_id,start_at)""",
"""CREATE TABLE IF NOT EXISTS subscriptions (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL UNIQUE, plan VARCHAR(32) NOT NULL DEFAULT 'START',
 status VARCHAR(32) NOT NULL DEFAULT 'NONE', price INTEGER NOT NULL DEFAULT 0, currency VARCHAR(8) NOT NULL DEFAULT 'UZS',
 started_at TIMESTAMP, expires_at TIMESTAMP, payment_provider VARCHAR(32), payment_id VARCHAR(160),
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE INDEX IF NOT EXISTS idx_subscriptions_status_expiry ON subscriptions(status,expires_at)""",
"""CREATE TABLE IF NOT EXISTS support_messages (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64) NOT NULL, sender_role VARCHAR(32) NOT NULL,
 sender_user_id VARCHAR(64), body TEXT NOT NULL, is_read BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE INDEX IF NOT EXISTS idx_support_messages_salon_time ON support_messages(salon_id,created_at)""",
"""CREATE TABLE IF NOT EXISTS announcements (
 id VARCHAR(64) PRIMARY KEY, salon_id VARCHAR(64), title VARCHAR(180) NOT NULL, body TEXT NOT NULL,
 kind VARCHAR(32) NOT NULL DEFAULT 'info', is_active BOOLEAN NOT NULL DEFAULT TRUE,
 starts_at TIMESTAMP, ends_at TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(salon_id) REFERENCES salons(id)
)""",
"""CREATE INDEX IF NOT EXISTS idx_announcements_salon_active ON announcements(salon_id,is_active,created_at)"""
]

def seed_demo_salons() -> None:
    demos = [
        ("demo-lumiere","Lumière Beauty","Элегантный салон красоты в светлом премиальном стиле.","+998 90 100 10 01","Ташкент, Мирзо-Улугбекский район","light","#7c3aed"),
        ("demo-noir","NOIR Barber Club","Тёмный брутальный барбершоп с атмосферой мужского клуба.","+998 90 100 10 02","Ташкент, Юнусабад","dark","#f59e0b"),
        ("demo-bloom","Bloom Studio","Мягкая уютная студия для красоты, ухода и расслабления.","+998 90 100 10 03","Ташкент, Чиланзар","soft","#db6b9b"),
        ("demo-atelier","ATELIER 24","Современная студия с минималистичным digital-дизайном.","+998 90 100 10 04","Ташкент, Яшнабад","modern","#06b6d4")
    ]
    catalogs = [
        [("Женская стрижка","Стрижка, укладка и уход",120000,60),("Окрашивание","Окрашивание и тонирование",280000,150),("Макияж","Вечерний или дневной макияж",180000,60)],
        [("Мужская стрижка","Стрижка машинкой и ножницами",90000,60),("Стрижка + борода","Полный мужской образ",140000,90),("Оформление бороды","Контур и уход",70000,45)],
        [("Маникюр","Комбинированный маникюр",100000,75),("Маникюр + покрытие","Маникюр с гель-лаком",150000,120),("Брови","Коррекция и оформление",80000,45)],
        [("Signature Hair","Авторская стрижка и укладка",200000,90),("Color Lab","Сложное окрашивание",350000,180),("Express Beauty","Быстрый beauty-комплекс",160000,60)]
    ]
    masters = [
        [("Анна","Колорист и стилист"),("Мария","Визажист")],
        [("Алекс","Барбер, классические стрижки"),("Денис","Барбер, борода и fade")],
        [("София","Мастер ногтевого сервиса"),("Лейла","Бровист и lash-мастер")],
        [("Ника","Hair artist"),("Алина","Beauty artist")]
    ]
    with get_engine().begin() as conn:
        for idx,d in enumerate(demos):
            if conn.execute(text("SELECT 1 FROM salons WHERE slug=:slug"),{"slug":d[0]}).first():
                continue
            sid=uid()
            conn.execute(text("""INSERT INTO salons(id,slug,name,description,phone,address,timezone,theme,accent_color,is_active)
              VALUES(:id,:slug,:name,:description,:phone,:address,'Asia/Tashkent',:theme,:accent,TRUE)"""),
              {"id":sid,"slug":d[0],"name":d[1],"description":d[2],"phone":d[3],"address":d[4],"theme":d[5],"accent":d[6]})
            service_ids=[]
            for name,desc,price,duration in catalogs[idx]:
                xid=uid(); service_ids.append(xid)
                conn.execute(text("""INSERT INTO services(id,salon_id,name,description,price,duration_minutes,is_active)
                  VALUES(:id,:sid,:name,:description,:price,:duration,TRUE)"""),
                  {"id":xid,"sid":sid,"name":name,"description":desc,"price":price,"duration":duration})
            for name,desc in masters[idx]:
                mid=uid()
                conn.execute(text("""INSERT INTO masters(id,salon_id,name,description,is_active)
                  VALUES(:id,:sid,:name,:description,TRUE)"""),{"id":mid,"sid":sid,"name":name,"description":desc})
                for service_id in service_ids:
                    conn.execute(text("INSERT INTO master_services(master_id,service_id) VALUES(:master,:service)"),
                      {"master":mid,"service":service_id})
                for weekday in range(7):
                    conn.execute(text("""INSERT INTO master_schedules(id,master_id,weekday,start_time,end_time,is_working)
                      VALUES(:id,:master,:weekday,'10:00','19:00',:working)"""),
                      {"id":uid(),"master":mid,"weekday":weekday,"working":weekday < 6})

def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = ON"))
        for statement in SCHEMA:
            conn.execute(text(statement))
        if conn.dialect.name == "sqlite":
            cols={r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
            if "master_id" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN master_id VARCHAR(64)"))
        else:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS master_id VARCHAR(64)"))
        if conn.dialect.name=="sqlite":
            salon_cols={r[1] for r in conn.execute(text("PRAGMA table_info(salons)")).fetchall()}
        else:
            salon_cols={r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='salons'")).fetchall()}
        if "status" not in salon_cols:
            conn.execute(text("ALTER TABLE salons ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'"))
        conn.execute(text("UPDATE salons SET status='ACTIVE' WHERE status IS NULL OR status=''"))
    seed_demo_salons()
    with get_engine().begin() as conn:
        rows=conn.execute(text("SELECT id FROM salons WHERE slug<>'salonos-control' AND id NOT IN (SELECT salon_id FROM subscriptions)")).fetchall()
        for row in rows:
            conn.execute(text("INSERT INTO subscriptions(id,salon_id,plan,status,price,currency) VALUES(:id,:sid,'START','NONE',0,'UZS')"),{"id":uid(),"sid":row.id})
