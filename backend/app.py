from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib, hmac, os, re, secrets, urllib.parse, urllib.request

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from .db import init_db, get_engine

BASE_DIR = Path(__file__).resolve().parent.parent
COOKIE = "salonos_session"
STATUSES = {"confirmed", "completed", "cancelled", "no_show"}
THEMES = {"light", "dark", "soft", "modern"}
PLANS = {
    "START": {"name":"START","price":49000,"features":["Сайт салона","Онлайн-запись","Услуги","Расписание","До 2 мастеров"]},
    "PRO": {"name":"PRO","price":99000,"features":["Всё из START","Неограниченные мастера","Клиенты","История записей","Telegram-уведомления","Расширенные настройки"]},
    "BUSINESS": {"name":"BUSINESS","price":199000,"features":["Всё из PRO","Расширенные роли","Расширенная аналитика","Приоритетная поддержка"]}
}
SUBSCRIPTION_STATUSES = {"NONE","PENDING_PAYMENT","TRIAL","ACTIVE","EXPIRED","CANCELLED"}

def uid() -> str: return secrets.token_urlsafe(12)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2$210000${salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except Exception: return False

def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", value.lower()).strip("-")
    return value or f"salon-{secrets.token_hex(3)}"

def unique_slug(conn, value: str) -> str:
    base, candidate, n = slugify(value)[:70], slugify(value)[:70], 2
    while conn.execute(text("SELECT 1 FROM salons WHERE slug=:slug"), {"slug": candidate}).first():
        candidate = f"{base}-{n}"; n += 1
    return candidate

def rowdict(row): return dict(row._mapping)
def now_utc(): return datetime.now(timezone.utc).replace(tzinfo=None)
def parse_dt(value: str):
    try: return datetime.fromisoformat(value.replace("Z","+00:00")).replace(tzinfo=None)
    except ValueError: raise HTTPException(400,"Некорректные дата и время")

def clean_phone(phone: str) -> str:
    value = re.sub(r"[^0-9+]", "", phone or "")
    if len(re.sub(r"\D","",value)) < 7: raise HTTPException(400,"Некорректный номер телефона")
    return value

class RegisterIn(BaseModel):
    name: str = Field(min_length=2,max_length=160); email: str = Field(min_length=5,max_length=255)
    password: str = Field(min_length=6,max_length=128); salon_name: str = Field(min_length=2,max_length=160)
    theme: str = "light"
class MasterRegisterIn(BaseModel):
    master_id: str; email: str = Field(min_length=5,max_length=255); password: str = Field(min_length=6,max_length=128)
class LoginIn(BaseModel): email: str; password: str
class SalonIn(BaseModel):
    name: str = Field(min_length=2,max_length=160); description: str=""; logo_url: str=""; phone: str=""; address: str=""
    theme: str="light"; accent_color: str="#7c3aed"
class MasterIn(BaseModel):
    name: str = Field(min_length=2,max_length=160); photo_url: str=""; description: str=""; phone: str=""
class ServiceIn(BaseModel):
    name: str = Field(min_length=2,max_length=160); description: str=""; price: int=Field(ge=0,le=1000000000)
    duration_minutes: int=Field(ge=15,le=480)
class MasterServicesIn(BaseModel): service_ids: list[str]
class ScheduleItem(BaseModel):
    weekday: int=Field(ge=0,le=6); start_time: str="10:00"; end_time: str="19:00"; is_working: bool=True
class ExceptionIn(BaseModel):
    date: str; start_time: str|None=None; end_time: str|None=None; is_day_off: bool=False
class AppointmentIn(BaseModel):
    master_id: str; service_id: str; start_at: str; client_name: str=Field(min_length=2,max_length=160)
    client_phone: str=Field(min_length=7,max_length=40)
class AppointmentUpdateIn(BaseModel):
    master_id: str; service_id: str; start_at: str; client_name: str=Field(min_length=2,max_length=160)
    client_phone: str=Field(min_length=7,max_length=40); status: str="confirmed"

def ensure_superadmin():
    email=os.getenv("SUPERADMIN_EMAIL","").strip().lower()
    password=os.getenv("SUPERADMIN_PASSWORD","")
    name=os.getenv("SUPERADMIN_NAME","SalonOS")
    if not email or not password:
        return
    if len(password)<6:
        raise RuntimeError("SUPERADMIN_PASSWORD must contain at least 6 characters")
    with get_engine().begin() as conn:
        existing=conn.execute(text("SELECT id,role FROM users WHERE lower(email)=:email"),{"email":email}).first()
        if existing:
            return
        sid=uid()
        conn.execute(text("""INSERT INTO salons(id,slug,name,description,is_active) VALUES(:id,:slug,:name,:description,TRUE)"""),
                     {"id":sid,"slug":"salonos-control","name":"SalonOS Control","description":"System account for SalonOS superadmin"})
        conn.execute(text("""INSERT INTO users(id,salon_id,name,email,password_hash,role,is_active) VALUES(:id,:sid,:name,:email,:hash,'superadmin',TRUE)"""),
                     {"id":uid(),"sid":sid,"name":name,"email":email,"hash":hash_password(password)})

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_superadmin()
    yield

app=FastAPI(title="SalonOS API",version="1.0.0",lifespan=lifespan)


@app.get("/assets/{path:path}")
def assets(path:str):
    target=BASE_DIR/"frontend"/path
    if not target.is_file(): raise HTTPException(404,"Asset not found")
    return FileResponse(target)
_cors=[x.strip() for x in os.getenv("CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=_cors,allow_credentials=(_cors!=["*"]),allow_methods=["*"],allow_headers=["*"])

def auth_user(request: Request):
    token=request.cookies.get(COOKIE)
    if not token: raise HTTPException(401,"Требуется авторизация")
    with get_engine().begin() as conn:
        row=conn.execute(text("""SELECT u.*,s.is_active salon_active,s.name salon_name,s.slug salon_slug
          FROM sessions x JOIN users u ON u.id=x.user_id JOIN salons s ON s.id=u.salon_id
          WHERE x.token=:token AND x.expires_at>:now AND u.is_active=TRUE"""),{"token":token,"now":now_utc()}).first()
    if not row: raise HTTPException(401,"Сессия недействительна")
    return rowdict(row)

def require_role(request:Request,*roles):
    u=auth_user(request)
    if u["role"] not in roles: raise HTTPException(403,"Недостаточно прав")
    return u

def send_telegram(message:str):
    token,chat_id=os.getenv("TELEGRAM_BOT_TOKEN"),os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not token or not chat_id: return
    try:
        data=urllib.parse.urlencode({"chat_id":chat_id,"text":message}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=data,method="POST"),timeout=5).read()
    except Exception: pass

@app.get("/api/health")
def health(): return {"ok":True,"service":"salonos","version":"1.0.0"}

@app.get("/api/config")
def config(): return {"product":"SalonOS","version":"1.0.0","timezone":"Asia/Tashkent","themes":sorted(THEMES)}

@app.post("/api/auth/register")
def register(payload:RegisterIn,response:Response):
    email=payload.email.strip().lower()
    with get_engine().begin() as conn:
        if conn.execute(text("SELECT 1 FROM users WHERE lower(email)=:email"),{"email":email}).first(): raise HTTPException(409,"Этот email уже зарегистрирован")
        salon_id,user_id=uid(),uid(); slug=unique_slug(conn,payload.salon_name)
        theme = payload.theme.strip().lower()
        if theme not in THEMES: raise HTTPException(400,"Неверный дизайн")
        conn.execute(text("INSERT INTO salons(id,slug,name,theme,status,is_active) VALUES(:id,:slug,:name,:theme,'DRAFT',TRUE)"),{"id":salon_id,"slug":slug,"name":payload.salon_name.strip(),"theme":theme})
        conn.execute(text("INSERT INTO subscriptions(id,salon_id,plan,status,price,currency) VALUES(:id,:sid,'PRO','NONE',99000,'UZS')"),{"id":uid(),"sid":salon_id})
        conn.execute(text("""INSERT INTO users(id,salon_id,name,email,password_hash,role) VALUES(:id,:salon,:name,:email,:hash,'admin')"""),
                     {"id":user_id,"salon":salon_id,"name":payload.name.strip(),"email":email,"hash":hash_password(payload.password)})
        token=secrets.token_urlsafe(48)
        conn.execute(text("INSERT INTO sessions(token,user_id,expires_at) VALUES(:token,:uid,:exp)"),{"token":token,"uid":user_id,"exp":now_utc()+timedelta(days=30)})
    response.set_cookie(COOKIE,token,httponly=True,samesite="lax",secure=os.getenv("APP_ENV","development")=="production",max_age=2592000)
    return {"ok":True,"slug":slug}

@app.post("/api/auth/login")
def login(payload:LoginIn,response:Response):
    email=payload.email.strip().lower()
    with get_engine().begin() as conn:
        row=conn.execute(text("SELECT * FROM users WHERE lower(email)=:email AND is_active=TRUE"),{"email":email}).first()
        if not row or not verify_password(payload.password,rowdict(row)["password_hash"]): raise HTTPException(401,"Неверный email или пароль")
        token=secrets.token_urlsafe(48)
        conn.execute(text("INSERT INTO sessions(token,user_id,expires_at) VALUES(:token,:uid,:exp)"),{"token":token,"uid":row.id,"exp":now_utc()+timedelta(days=30)})
    response.set_cookie(COOKIE,token,httponly=True,samesite="lax",secure=os.getenv("APP_ENV","development")=="production",max_age=2592000); return {"ok":True}

@app.post("/api/auth/master")
def create_master_login(payload:MasterRegisterIn,request:Request):
    u=require_role(request,"admin")
    email=payload.email.strip().lower()
    with get_engine().begin() as conn:
        master=conn.execute(text("SELECT * FROM masters WHERE id=:id AND salon_id=:sid AND is_active=TRUE"),{"id":payload.master_id,"sid":u["salon_id"]}).first()
        if not master: raise HTTPException(404,"Мастер не найден")
        if conn.execute(text("SELECT 1 FROM users WHERE lower(email)=:email"),{"email":email}).first(): raise HTTPException(409,"Этот email уже используется")
        uid_=uid()
        conn.execute(text("""INSERT INTO users(id,salon_id,name,email,password_hash,role,master_id) VALUES(:id,:sid,:name,:email,:hash,'master',:master_id)"""),{"id":uid_,"sid":u["salon_id"],"name":master.name,"email":email,"hash":hash_password(payload.password),"master_id":master.id})
    return {"ok":True,"master_id":master.id}

@app.post("/api/auth/logout")
def logout(request:Request,response:Response):
    token=request.cookies.get(COOKIE)
    if token:
        with get_engine().begin() as conn: conn.execute(text("DELETE FROM sessions WHERE token=:token"),{"token":token})
    response.delete_cookie(COOKIE); response.delete_cookie("salonos_superadmin_return"); return {"ok":True}

@app.get("/api/me")
def me(request:Request):
    u=auth_user(request)
    data={k:u[k] for k in ("id","name","email","role","salon_id","salon_name","salon_slug")}
    data["is_impersonating"]=bool(request.cookies.get("salonos_superadmin_return"))
    return data

@app.get("/api/admin/salon")
def get_salon(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn: row=conn.execute(text("SELECT * FROM salons WHERE id=:id"),{"id":u["salon_id"]}).first()
    return rowdict(row)

@app.get("/api/plans")
def plans():
    return list(PLANS.values())

@app.get("/api/admin/subscription")
def admin_subscription(request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        conn.execute(text("""UPDATE subscriptions SET status='EXPIRED',updated_at=CURRENT_TIMESTAMP
          WHERE salon_id=:sid AND status IN ('TRIAL','ACTIVE') AND expires_at IS NOT NULL AND expires_at<=CURRENT_TIMESTAMP"""),{"sid":u["salon_id"]})
        conn.execute(text("""UPDATE salons SET status='EXPIRED',is_active=FALSE
          WHERE id=:sid AND EXISTS (SELECT 1 FROM subscriptions sub WHERE sub.salon_id=salons.id AND sub.status='EXPIRED')"""),{"sid":u["salon_id"]})
        row=conn.execute(text("""SELECT s.status salon_status,s.slug,s.is_active,sub.plan,sub.status subscription_status,sub.price,sub.currency,sub.started_at,sub.expires_at
          FROM salons s LEFT JOIN subscriptions sub ON sub.salon_id=s.id WHERE s.id=:sid"""),{"sid":u["salon_id"]}).first()
    return rowdict(row) if row else {}

@app.post("/api/admin/subscription/request")
def request_subscription(request:Request,plan:str="PRO"):
    u=require_role(request,"admin")
    plan=plan.upper()
    if plan not in PLANS: raise HTTPException(400,"Неизвестный тариф")
    with get_engine().begin() as conn:
        conn.execute(text("""INSERT INTO subscriptions(id,salon_id,plan,status,price,currency)
          VALUES(:id,:sid,:plan,'PENDING_PAYMENT',:price,'UZS')
          ON CONFLICT(salon_id) DO UPDATE SET plan=:plan,status='PENDING_PAYMENT',price=:price,updated_at=CURRENT_TIMESTAMP"""),
          {"id":uid(),"sid":u["salon_id"],"plan":plan,"price":PLANS[plan]["price"]})
        conn.execute(text("UPDATE salons SET status='PENDING_PAYMENT' WHERE id=:sid"),{"sid":u["salon_id"]})
    return {"ok":True,"status":"PENDING_PAYMENT","plan":plan,"price":PLANS[plan]["price"]}

@app.put("/api/admin/salon")
def update_salon(payload:SalonIn,request:Request):
    u=require_role(request,"admin")
    if payload.theme not in THEMES or not re.fullmatch(r"#[0-9a-fA-F]{6}",payload.accent_color): raise HTTPException(400,"Некорректные настройки темы")
    with get_engine().begin() as conn:
        conn.execute(text("""UPDATE salons SET name=:name,description=:description,logo_url=:logo_url,phone=:phone,address=:address,
          theme=:theme,accent_color=:accent_color WHERE id=:id"""),{**payload.model_dump(),"id":u["salon_id"]})
        row=conn.execute(text("SELECT * FROM salons WHERE id=:id"),{"id":u["salon_id"]}).first()
    return rowdict(row)

@app.get("/api/admin/masters")
def masters(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn: rows=conn.execute(text("SELECT * FROM masters WHERE salon_id=:sid ORDER BY name"),{"sid":u["salon_id"]}).fetchall()
    return [rowdict(x) for x in rows]

@app.post("/api/admin/masters")
def create_master(payload:MasterIn,request:Request):
    u=require_role(request,"admin"); mid=uid()
    with get_engine().begin() as conn:
        conn.execute(text("""INSERT INTO masters(id,salon_id,name,photo_url,description,phone) VALUES(:id,:sid,:name,:photo_url,:description,:phone)"""),{"id":mid,"sid":u["salon_id"],**payload.model_dump()})
        for wd in range(7): conn.execute(text("""INSERT INTO master_schedules(id,master_id,weekday,start_time,end_time,is_working) VALUES(:id,:mid,:wd,'10:00','19:00',TRUE)"""),{"id":uid(),"mid":mid,"wd":wd})
    return {"id":mid}

@app.put("/api/admin/masters/{master_id}")
def update_master(master_id:str,payload:MasterIn,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Мастер не найден")
        conn.execute(text("""UPDATE masters SET name=:name,photo_url=:photo_url,description=:description,phone=:phone WHERE id=:id AND salon_id=:sid"""),{"id":master_id,"sid":u["salon_id"],**payload.model_dump()})
    return {"ok":True}

@app.delete("/api/admin/masters/{master_id}")
def deactivate_master(master_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn: conn.execute(text("UPDATE masters SET is_active=FALSE WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]})
    return {"ok":True}

@app.get("/api/admin/services")
def services(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn: rows=conn.execute(text("SELECT * FROM services WHERE salon_id=:sid ORDER BY name"),{"sid":u["salon_id"]}).fetchall()
    return [rowdict(x) for x in rows]

@app.post("/api/admin/services")
def create_service(payload:ServiceIn,request:Request):
    u=require_role(request,"admin"); sid=uid()
    with get_engine().begin() as conn: conn.execute(text("""INSERT INTO services(id,salon_id,name,description,price,duration_minutes) VALUES(:id,:sid,:name,:description,:price,:duration_minutes)"""),{"id":sid,"sid":u["salon_id"],**payload.model_dump()})
    return {"id":sid}

@app.put("/api/admin/services/{service_id}")
def update_service(service_id:str,payload:ServiceIn,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM services WHERE id=:id AND salon_id=:sid"),{"id":service_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Услуга не найдена")
        conn.execute(text("""UPDATE services SET name=:name,description=:description,price=:price,duration_minutes=:duration_minutes WHERE id=:id AND salon_id=:sid"""),{"id":service_id,"sid":u["salon_id"],**payload.model_dump()})
    return {"ok":True}

@app.delete("/api/admin/services/{service_id}")
def deactivate_service(service_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn: conn.execute(text("UPDATE services SET is_active=FALSE WHERE id=:id AND salon_id=:sid"),{"id":service_id,"sid":u["salon_id"]})
    return {"ok":True}

@app.get("/api/admin/masters/{master_id}/services")
def get_master_services(master_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT ms.service_id FROM master_services ms JOIN masters m ON m.id=ms.master_id WHERE ms.master_id=:mid AND m.salon_id=:sid"""),{"mid":master_id,"sid":u["salon_id"]}).fetchall()
    return [x[0] for x in rows]

@app.put("/api/admin/masters/{master_id}/services")
def set_master_services(master_id:str,payload:MasterServicesIn,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Мастер не найден")
        conn.execute(text("DELETE FROM master_services WHERE master_id=:mid"),{"mid":master_id})
        for service_id in set(payload.service_ids):
            if conn.execute(text("SELECT 1 FROM services WHERE id=:id AND salon_id=:sid"),{"id":service_id,"sid":u["salon_id"]}).first():
                conn.execute(text("INSERT INTO master_services(master_id,service_id) VALUES(:mid,:sid)"),{"mid":master_id,"sid":service_id})
    return {"ok":True}

@app.get("/api/admin/schedules/{master_id}")
def get_schedule(master_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Мастер не найден")
        rows=conn.execute(text("SELECT * FROM master_schedules WHERE master_id=:id ORDER BY weekday"),{"id":master_id}).fetchall()
    return [rowdict(x) for x in rows]

@app.put("/api/admin/schedules/{master_id}")
def set_schedule(master_id:str,payload:list[ScheduleItem],request:Request):
    u=require_role(request,"admin")
    if len(payload)!=7: raise HTTPException(400,"Нужно 7 дней")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Мастер не найден")
        for item in payload:
            if item.is_working and item.start_time>=item.end_time: raise HTTPException(400,"Неверное время")
            conn.execute(text("""UPDATE master_schedules SET start_time=:start,end_time=:end,is_working=:work WHERE master_id=:mid AND weekday=:wd"""),{"start":item.start_time,"end":item.end_time,"work":item.is_working,"mid":master_id,"wd":item.weekday})
    return {"ok":True}

@app.post("/api/admin/schedules/{master_id}/exceptions")
def add_exception(master_id:str,payload:ExceptionIn,request:Request):
    u=require_role(request,"admin")
    try: date.fromisoformat(payload.date)
    except ValueError: raise HTTPException(400,"Неверная дата")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first(): raise HTTPException(404,"Мастер не найден")
        old=conn.execute(text("SELECT id FROM schedule_exceptions WHERE master_id=:mid AND date=:date"),{"mid":master_id,"date":payload.date}).first()
        p={"mid":master_id,"date":payload.date,"start":payload.start_time,"end":payload.end_time,"off":payload.is_day_off}
        if old: conn.execute(text("""UPDATE schedule_exceptions SET start_time=:start,end_time=:end,is_day_off=:off WHERE master_id=:mid AND date=:date"""),p)
        else: conn.execute(text("""INSERT INTO schedule_exceptions(id,master_id,date,start_time,end_time,is_day_off) VALUES(:id,:mid,:date,:start,:end,:off)"""),{**p,"id":uid()})
    return {"ok":True}

def availability_for(conn,master_id:str,service_id:str,day:date):
    master=conn.execute(text("SELECT * FROM masters WHERE id=:id AND is_active=TRUE"),{"id":master_id}).first()
    service=conn.execute(text("SELECT * FROM services WHERE id=:id AND is_active=TRUE"),{"id":service_id}).first()
    if not master or not service or not conn.execute(text("SELECT 1 FROM master_services WHERE master_id=:m AND service_id=:s"),{"m":master_id,"s":service_id}).first(): return []
    ex=conn.execute(text("SELECT * FROM schedule_exceptions WHERE master_id=:m AND date=:d"),{"m":master_id,"d":day.isoformat()}).first()
    if ex:
        ex=rowdict(ex)
        if ex["is_day_off"]: return []
        start_s,end_s=ex["start_time"],ex["end_time"]
    else:
        sch=conn.execute(text("SELECT * FROM master_schedules WHERE master_id=:m AND weekday=:w"),{"m":master_id,"w":day.weekday()}).first()
        if not sch or not sch.is_working: return []
        start_s,end_s=sch.start_time,sch.end_time
    start=datetime.combine(day,time.fromisoformat(start_s)); end=datetime.combine(day,time.fromisoformat(end_s)); duration=timedelta(minutes=service.duration_minutes)
    rows=conn.execute(text("""SELECT start_at,end_at FROM appointments WHERE master_id=:m AND status='confirmed' AND start_at<:end AND end_at>:start"""),{"m":master_id,"start":start,"end":end}).fetchall()
    busy=[(parse_dt(str(x[0])),parse_dt(str(x[1]))) for x in rows]; slots=[]; cursor=start
    while cursor+duration<=end:
        if not any(cursor<b and cursor+duration>a for a,b in busy) and cursor>=now_utc(): slots.append(cursor.strftime("%H:%M"))
        cursor+=timedelta(minutes=30)
    return slots

@app.get("/api/public/{slug}")
def public_salon(slug:str):
    with get_engine().begin() as conn:
        salon=conn.execute(text("""SELECT id,slug,name,description,logo_url,phone,address,timezone,theme,accent_color FROM salons s JOIN subscriptions sub ON sub.salon_id=s.id WHERE s.slug=:slug AND s.is_active=TRUE AND sub.status IN ('ACTIVE','TRIAL') AND (sub.expires_at IS NULL OR sub.expires_at>CURRENT_TIMESTAMP)"""),{"slug":slug}).first()
        if not salon: raise HTTPException(404,"Салон не найден")
        sid=salon.id
        sr=conn.execute(text("SELECT id,name,description,price,duration_minutes FROM services WHERE salon_id=:sid AND is_active=TRUE ORDER BY name"),{"sid":sid}).fetchall()
        mr=conn.execute(text("SELECT id,name,photo_url,description FROM masters WHERE salon_id=:sid AND is_active=TRUE ORDER BY name"),{"sid":sid}).fetchall()
        links=conn.execute(text("""SELECT ms.master_id,ms.service_id FROM master_services ms JOIN masters m ON m.id=ms.master_id JOIN services s ON s.id=ms.service_id WHERE m.salon_id=:sid AND m.is_active=TRUE AND s.is_active=TRUE"""),{"sid":sid}).fetchall()
    return {**rowdict(salon),"services":[rowdict(x) for x in sr],"masters":[rowdict(x) for x in mr],"master_services":[{"master_id":x[0],"service_id":x[1]} for x in links]}

@app.get("/api/public/{slug}/slots")
def public_slots(slug:str,service_id:str,master_id:str,day:str):
    try: d=date.fromisoformat(day)
    except ValueError: raise HTTPException(400,"Неверная дата")
    if d<datetime.now(ZoneInfo("Asia/Tashkent")).date(): return {"slots":[]}
    with get_engine().begin() as conn:
        salon=conn.execute(text("SELECT s.id FROM salons s JOIN subscriptions sub ON sub.salon_id=s.id WHERE s.slug=:slug AND s.is_active=TRUE AND sub.status IN ('ACTIVE','TRIAL') AND (sub.expires_at IS NULL OR sub.expires_at>CURRENT_TIMESTAMP)"),{"slug":slug}).first()
        if not salon: raise HTTPException(404,"Салон не найден")
        if not conn.execute(text("""SELECT 1 FROM masters m JOIN services s ON s.salon_id=m.salon_id WHERE m.id=:m AND s.id=:s AND m.salon_id=:sid"""),{"m":master_id,"s":service_id,"sid":salon.id}).first(): return {"slots":[]}
        return {"slots":availability_for(conn,master_id,service_id,d)}

@app.post("/api/public/{slug}/appointments")
def public_appointment(slug:str,payload:AppointmentIn):
    start=parse_dt(payload.start_at)
    if start<now_utc(): raise HTTPException(400,"Нельзя записаться в прошлое")
    with get_engine().begin() as conn:
        salon=conn.execute(text("SELECT s.* FROM salons s JOIN subscriptions sub ON sub.salon_id=s.id WHERE s.slug=:slug AND s.is_active=TRUE AND sub.status IN ('ACTIVE','TRIAL') AND (sub.expires_at IS NULL OR sub.expires_at>CURRENT_TIMESTAMP)"),{"slug":slug}).first()
        if not salon: raise HTTPException(404,"Салон не найден")
        master=conn.execute(text("SELECT * FROM masters WHERE id=:id AND salon_id=:sid AND is_active=TRUE"),{"id":payload.master_id,"sid":salon.id}).first()
        service=conn.execute(text("SELECT * FROM services WHERE id=:id AND salon_id=:sid AND is_active=TRUE"),{"id":payload.service_id,"sid":salon.id}).first()
        if not master or not service or not conn.execute(text("SELECT 1 FROM master_services WHERE master_id=:m AND service_id=:s"),{"m":payload.master_id,"s":payload.service_id}).first(): raise HTTPException(400,"Мастер недоступен для этой услуги")
        end=start+timedelta(minutes=service.duration_minutes)
        if conn.dialect.name=="postgresql": conn.execute(text("SELECT id FROM masters WHERE id=:id FOR UPDATE"),{"id":payload.master_id}).first()
        if conn.execute(text("""SELECT id FROM appointments WHERE master_id=:m AND status='confirmed' AND start_at<:end AND end_at>:start LIMIT 1"""),{"m":payload.master_id,"start":start,"end":end}).first(): raise HTTPException(409,"Это время уже занято")
        if start.strftime("%H:%M") not in availability_for(conn,payload.master_id,payload.service_id,start.date()): raise HTTPException(409,"Это время больше недоступно")
        phone=clean_phone(payload.client_phone)
        client=conn.execute(text("SELECT id FROM clients WHERE salon_id=:sid AND phone=:phone"),{"sid":salon.id,"phone":phone}).first()
        if client:
            client_id=client.id; conn.execute(text("UPDATE clients SET name=:name,updated_at=:now WHERE id=:id"),{"name":payload.client_name.strip(),"now":now_utc(),"id":client_id})
        else:
            client_id=uid(); conn.execute(text("INSERT INTO clients(id,salon_id,name,phone) VALUES(:id,:sid,:name,:phone)"),{"id":client_id,"sid":salon.id,"name":payload.client_name.strip(),"phone":phone})
        aid=uid()
        conn.execute(text("""INSERT INTO appointments(id,salon_id,client_id,master_id,service_id,start_at,end_at,price,status)
          VALUES(:id,:sid,:client,:master,:service,:start,:end,:price,'confirmed')"""),{"id":aid,"sid":salon.id,"client":client_id,"master":payload.master_id,"service":payload.service_id,"start":start,"end":end,"price":service.price})
        result={"id":aid,"client_name":payload.client_name.strip(),"service":service.name,"master":master.name,"start_at":start.isoformat(),"end_at":end.isoformat(),"price":service.price}
    send_telegram(f"🔔 Новая запись в {salon.name}\
{result['client_name']}\
{result['service']} — {result['master']}\
{start.strftime('%d.%m.%Y %H:%M')}\
{result['price']:,} сум".replace(","," "))
    return result

@app.get("/api/admin/schedules/{master_id}/exceptions")
def list_exceptions(master_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM masters WHERE id=:id AND salon_id=:sid"),{"id":master_id,"sid":u["salon_id"]}).first():
            raise HTTPException(404,"Мастер не найден")
        rows=conn.execute(text("SELECT * FROM schedule_exceptions WHERE master_id=:id ORDER BY date"),{"id":master_id}).fetchall()
    return [rowdict(x) for x in rows]

@app.delete("/api/admin/schedules/{master_id}/exceptions/{exception_id}")
def delete_exception(master_id:str,exception_id:str,request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM schedule_exceptions WHERE id=:id AND master_id=:mid AND master_id IN (SELECT id FROM masters WHERE salon_id=:sid)"),{"id":exception_id,"mid":master_id,"sid":u["salon_id"]})
    return {"ok":True}

@app.get("/api/admin/appointments")
def admin_appointments(request:Request,day:str|None=None,status:str|None=None,master_id:str|None=None):
    u=require_role(request,"admin","master")
    if u["role"]=="master":
        master_id=u.get("master_id") or "__none__"
    day=day or datetime.now(ZoneInfo("Asia/Tashkent")).date().isoformat()
    try: d=date.fromisoformat(day)
    except ValueError: raise HTTPException(400,"Неверная дата")
    start=datetime.combine(d,time.min); end=start+timedelta(days=1)
    clauses=["a.salon_id=:sid","a.start_at>=:start","a.start_at<:end"]; params={"sid":u["salon_id"],"start":start,"end":end}
    if status in STATUSES: clauses.append("a.status=:status"); params["status"]=status
    if master_id: clauses.append("a.master_id=:master"); params["master"]=master_id
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT a.*,c.name client_name,c.phone client_phone,m.name master_name,s.name service_name
          FROM appointments a JOIN clients c ON c.id=a.client_id JOIN masters m ON m.id=a.master_id JOIN services s ON s.id=a.service_id
          WHERE """+" AND ".join(clauses)+" ORDER BY a.start_at"),params).fetchall()
    return [rowdict(x) for x in rows]

@app.put("/api/admin/appointments/{appointment_id}")
def update_appointment(appointment_id:str,payload:AppointmentUpdateIn,request:Request):
    u=require_role(request,"admin")
    if payload.status not in STATUSES: raise HTTPException(400,"Неверный статус")
    start=parse_dt(payload.start_at)
    if start<now_utc(): raise HTTPException(400,"Нельзя назначить запись в прошлое")
    with get_engine().begin() as conn:
        a=conn.execute(text("SELECT * FROM appointments WHERE id=:id AND salon_id=:sid"),{"id":appointment_id,"sid":u["salon_id"]}).first()
        master=conn.execute(text("SELECT * FROM masters WHERE id=:id AND salon_id=:sid AND is_active=TRUE"),{"id":payload.master_id,"sid":u["salon_id"]}).first()
        service=conn.execute(text("SELECT * FROM services WHERE id=:id AND salon_id=:sid AND is_active=TRUE"),{"id":payload.service_id,"sid":u["salon_id"]}).first()
        if not a or not master or not service: raise HTTPException(404,"Запись, мастер или услуга не найдены")
        if not conn.execute(text("SELECT 1 FROM master_services WHERE master_id=:m AND service_id=:s"),{"m":master.id,"s":service.id}).first(): raise HTTPException(400,"Мастер не оказывает эту услугу")
        end=start+timedelta(minutes=service.duration_minutes)
        if conn.execute(text("""SELECT id FROM appointments WHERE master_id=:m AND status='confirmed' AND id<>:id AND start_at<:end AND end_at>:start LIMIT 1"""),{"m":master.id,"id":appointment_id,"start":start,"end":end}).first(): raise HTTPException(409,"Это время уже занято")
        phone=clean_phone(payload.client_phone)
        conn.execute(text("""UPDATE clients SET name=:name,phone=:phone,updated_at=:now WHERE id=:id AND salon_id=:sid"""),{"name":payload.client_name.strip(),"phone":phone,"now":now_utc(),"id":a.client_id,"sid":u["salon_id"]})
        conn.execute(text("""UPDATE appointments SET master_id=:m,service_id=:s,start_at=:start,end_at=:end,price=:price,status=:status,updated_at=:now WHERE id=:id AND salon_id=:sid"""),{"m":master.id,"s":service.id,"start":start,"end":end,"price":service.price,"status":payload.status,"now":now_utc(),"id":appointment_id,"sid":u["salon_id"]})
    return {"ok":True}

@app.patch("/api/admin/appointments/{appointment_id}")
def admin_appointment_status(appointment_id:str,request:Request,status:str):
    u=require_role(request,"admin")
    if status not in STATUSES: raise HTTPException(400,"Неверный статус")
    with get_engine().begin() as conn:
        r=conn.execute(text("UPDATE appointments SET status=:status,updated_at=:now WHERE id=:id AND salon_id=:sid"),{"status":status,"now":now_utc(),"id":appointment_id,"sid":u["salon_id"]})
        if r.rowcount==0: raise HTTPException(404,"Запись не найдена")
    return {"ok":True}

@app.get("/api/admin/clients")
def admin_clients(request:Request):
    u=require_role(request,"admin")
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT c.*,COUNT(a.id) appointments_count FROM clients c LEFT JOIN appointments a ON a.client_id=c.id
          WHERE c.salon_id=:sid GROUP BY c.id ORDER BY c.updated_at DESC"""),{"sid":u["salon_id"]}).fetchall()
    return [rowdict(x) for x in rows]

class SupportMessageIn(BaseModel):
    body: str = Field(min_length=1,max_length=4000)

class AnnouncementIn(BaseModel):
    title: str = Field(min_length=2,max_length=180)
    body: str = Field(min_length=1,max_length=5000)
    kind: str = "info"
    salon_id: str|None = None
    days: int|None = None
    starts_at: str|None = None

@app.get("/api/admin/notifications")
def admin_notifications(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn:
        anns=conn.execute(text("""SELECT id,title,body,kind,created_at,starts_at,ends_at
          FROM announcements WHERE is_active=TRUE AND (salon_id IS NULL OR salon_id=:sid)
          AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
          AND (ends_at IS NULL OR ends_at>CURRENT_TIMESTAMP)
          ORDER BY created_at DESC LIMIT 30"""),{"sid":u["salon_id"]}).fetchall()
        unread=conn.execute(text("SELECT COUNT(*) FROM support_messages WHERE salon_id=:sid AND sender_role='superadmin' AND is_read=FALSE"),{"sid":u["salon_id"]}).scalar_one()
    return {"announcements":[rowdict(x) for x in anns],"unread_support":int(unread or 0)}

@app.post("/api/admin/notifications/{notification_id}/read")
def read_notification(notification_id:str,request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE announcements SET is_active=is_active WHERE id=:id AND (salon_id IS NULL OR salon_id=:sid)"),{"id":notification_id,"sid":u["salon_id"]})
    return {"ok":True}

@app.get("/api/admin/support")
def admin_support(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT id,sender_role,sender_user_id,body,is_read,created_at
          FROM support_messages WHERE salon_id=:sid ORDER BY created_at ASC LIMIT 200"""),{"sid":u["salon_id"]}).fetchall()
        conn.execute(text("UPDATE support_messages SET is_read=TRUE WHERE salon_id=:sid AND sender_role='superadmin'"),{"sid":u["salon_id"]})
    return [rowdict(x) for x in rows]

@app.post("/api/admin/support")
def admin_support_send(payload:SupportMessageIn,request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn:
        mid=uid()
        conn.execute(text("""INSERT INTO support_messages(id,salon_id,sender_role,sender_user_id,body)
          VALUES(:id,:sid,:role,:uid,:body)"""),{"id":mid,"sid":u["salon_id"],"role":u["role"],"uid":u["id"],"body":payload.body.strip()})
        salon=conn.execute(text("SELECT name FROM salons WHERE id=:sid"),{"sid":u["salon_id"]}).first()
    send_telegram(f"💬 Новое сообщение поддержки — {salon.name}\n{payload.body.strip()}")
    return {"ok":True,"id":mid}

@app.get("/api/superadmin/support")
def superadmin_support(request:Request,salon_id:str|None=None):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        clauses=["1=1"]; params={}
        if salon_id: clauses.append("m.salon_id=:sid"); params["sid"]=salon_id
        rows=conn.execute(text("""SELECT m.id,m.salon_id,m.sender_role,m.sender_user_id,m.body,m.is_read,m.created_at,s.name salon_name
          FROM support_messages m JOIN salons s ON s.id=m.salon_id WHERE """+" AND ".join(clauses)+""" ORDER BY m.created_at ASC LIMIT 500"""),params).fetchall()
        if salon_id:
            conn.execute(text("UPDATE support_messages SET is_read=TRUE WHERE salon_id=:sid AND sender_role<>'superadmin'"),{"sid":salon_id})
    return [rowdict(x) for x in rows]

@app.post("/api/superadmin/support/{salon_id}")
def superadmin_support_send(salon_id:str,payload:SupportMessageIn,request:Request):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM salons WHERE id=:sid AND slug<>'salonos-control'"),{"sid":salon_id}).first(): raise HTTPException(404,"Салон не найден")
        mid=uid()
        conn.execute(text("""INSERT INTO support_messages(id,salon_id,sender_role,sender_user_id,body,is_read)
          VALUES(:id,:sid,'superadmin',:uid,:body,TRUE)"""),{"id":mid,"sid":salon_id,"uid":u["id"],"body":payload.body.strip()})
    return {"ok":True,"id":mid}

@app.post("/api/superadmin/announcements")
def superadmin_announcement(payload:AnnouncementIn,request:Request):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    kind=payload.kind if payload.kind in {"info","success","warning","critical","update"} else "info"
    if payload.days is not None and (payload.days<1 or payload.days>365): raise HTTPException(400,"Некорректный срок")
    ends=now_utc()+timedelta(days=payload.days) if payload.days else None
    starts=now_utc()
    if payload.starts_at:
        starts=parse_dt(payload.starts_at)
    with get_engine().begin() as conn:
        if payload.salon_id and not conn.execute(text("SELECT 1 FROM salons WHERE id=:sid AND slug<>'salonos-control'"),{"sid":payload.salon_id}).first(): raise HTTPException(404,"Салон не найден")
        aid=uid()
        conn.execute(text("""INSERT INTO announcements(id,salon_id,title,body,kind,is_active,starts_at,ends_at)
          VALUES(:id,:sid,:title,:body,:kind,TRUE,:starts,:ends)"""),{"id":aid,"sid":payload.salon_id,"title":payload.title.strip(),"body":payload.body.strip(),"kind":kind,"starts":starts,"ends":ends})
    return {"ok":True,"id":aid}

@app.get("/api/superadmin/announcements")
def superadmin_announcements(request:Request):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT a.*,s.name salon_name FROM announcements a
          LEFT JOIN salons s ON s.id=a.salon_id ORDER BY a.created_at DESC LIMIT 100""")).fetchall()
    return [rowdict(x) for x in rows]

@app.patch("/api/superadmin/announcements/{announcement_id}")
def superadmin_announcement_toggle(announcement_id:str,request:Request,active:bool):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        r=conn.execute(text("UPDATE announcements SET is_active=:active WHERE id=:id"),{"active":active,"id":announcement_id})
        if r.rowcount==0: raise HTTPException(404,"Уведомление не найдено")
    return {"ok":True}

@app.post("/api/superadmin/salons/{salon_id}/trial")
def superadmin_trial(salon_id:str,request:Request,days:int=7,plan:str="PRO"):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    plan=plan.upper()
    if plan not in PLANS: raise HTTPException(400,"Неизвестный тариф")
    if days<1 or days>90: raise HTTPException(400,"Срок trial должен быть от 1 до 90 дней")
    started=now_utc(); expires=started+timedelta(days=days)
    with get_engine().begin() as conn:
        if not conn.execute(text("SELECT 1 FROM salons WHERE id=:sid AND slug<>'salonos-control'"),{"sid":salon_id}).first(): raise HTTPException(404,"Салон не найден")
        existing=conn.execute(text("SELECT id FROM subscriptions WHERE salon_id=:sid"),{"sid":salon_id}).first()
        params={"id":uid(),"sid":salon_id,"plan":plan,"price":PLANS[plan]["price"],"started":started,"expires":expires}
        if existing:
            conn.execute(text("""UPDATE subscriptions SET plan=:plan,status='TRIAL',price=:price,started_at=:started,expires_at=:expires,updated_at=:started WHERE salon_id=:sid"""),params)
        else:
            conn.execute(text("""INSERT INTO subscriptions(id,salon_id,plan,status,price,currency,started_at,expires_at)
              VALUES(:id,:sid,:plan,'TRIAL',:price,'UZS',:started,:expires)"""),params)
        conn.execute(text("UPDATE salons SET is_active=TRUE,status='ACTIVE' WHERE id=:sid"),{"sid":salon_id})
    return {"ok":True,"status":"TRIAL","plan":plan,"days":days,"expires_at":expires.isoformat()}

@app.post("/api/superadmin/salons/{salon_id}/enter")
def superadmin_enter_salon(salon_id:str,request:Request,response:Response):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    current=request.cookies.get(COOKIE)
    with get_engine().begin() as conn:
        salon=conn.execute(text("SELECT id,name,slug FROM salons WHERE id=:sid AND slug<>'salonos-control'"),{"sid":salon_id}).first()
        if not salon: raise HTTPException(404,"Салон не найден")
        admin=conn.execute(text("""SELECT id FROM users WHERE salon_id=:sid AND role='admin' AND is_active=TRUE
          ORDER BY id LIMIT 1"""),{"sid":salon_id}).first()
        if not admin: raise HTTPException(404,"У салона нет активного администратора")
        token=secrets.token_urlsafe(48)
        conn.execute(text("INSERT INTO sessions(token,user_id,expires_at) VALUES(:token,:uid,:exp)"),{"token":token,"uid":admin.id,"exp":now_utc()+timedelta(hours=8)})
    response.set_cookie("salonos_superadmin_return",current,httponly=True,samesite="lax",secure=os.getenv("APP_ENV","development")=="production",max_age=28800)
    response.set_cookie(COOKIE,token,httponly=True,samesite="lax",secure=os.getenv("APP_ENV","development")=="production",max_age=28800)
    return {"ok":True,"salon_id":salon.id,"salon_name":salon.name}

@app.post("/api/superadmin/exit-salon")
def superadmin_exit_salon(request:Request,response:Response):
    original=request.cookies.get("salonos_superadmin_return")
    if not original: raise HTTPException(400,"Режим входа в салон не активен")
    current=request.cookies.get(COOKIE)
    with get_engine().begin() as conn:
        if current: conn.execute(text("DELETE FROM sessions WHERE token=:token"),{"token":current})
        row=conn.execute(text("""SELECT u.role FROM sessions x JOIN users u ON u.id=x.user_id
          WHERE x.token=:token AND x.expires_at>:now AND u.role='superadmin'"""),{"token":original,"now":now_utc()}).first()
    if not row: raise HTTPException(401,"Сессия создателя недействительна")
    response.set_cookie(COOKIE,original,httponly=True,samesite="lax",secure=os.getenv("APP_ENV","development")=="production",max_age=2592000)
    response.delete_cookie("salonos_superadmin_return")
    return {"ok":True}

@app.get("/api/superadmin/salons")
def superadmin_salons(request:Request):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        rows=conn.execute(text("""SELECT s.id,s.slug,s.name,s.phone,s.is_active,s.status,s.created_at,
          COALESCE(sub.plan,'START') plan,COALESCE(sub.status,'NONE') subscription_status,sub.price,sub.currency,sub.started_at,sub.expires_at
          FROM salons s LEFT JOIN subscriptions sub ON sub.salon_id=s.id
          WHERE s.slug<>'salonos-control' ORDER BY s.created_at DESC""")).fetchall()
    return [rowdict(x) for x in rows]

@app.patch("/api/superadmin/salons/{salon_id}")
def superadmin_toggle(salon_id:str,request:Request,active:bool):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE salons SET is_active=:active,status=:status WHERE id=:id"),{"active":active,"status":"ACTIVE" if active else "SUSPENDED","id":salon_id})
        if active:
            conn.execute(text("UPDATE subscriptions SET status='ACTIVE',started_at=COALESCE(started_at,CURRENT_TIMESTAMP) WHERE salon_id=:id AND status IN ('PENDING_PAYMENT','NONE')"),{"id":salon_id})
    return {"ok":True}

@app.patch("/api/superadmin/salons/{salon_id}/subscription")
def superadmin_subscription(salon_id:str,request:Request,days:int=30,plan:str="PRO"):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    plan=plan.upper()
    if plan not in PLANS: raise HTTPException(400,"Неизвестный тариф")
    if days<1 or days>3650: raise HTTPException(400,"Некорректный срок")
    expires_at=now_utc()+timedelta(days=days)
    with get_engine().begin() as conn:
        existing=conn.execute(text("SELECT id FROM subscriptions WHERE salon_id=:sid"),{"sid":salon_id}).first()
        params={"id":uid(),"sid":salon_id,"plan":plan,"price":PLANS[plan]["price"],"started":now_utc(),"expires":expires_at}
        if existing:
            conn.execute(text("""UPDATE subscriptions SET plan=:plan,status='ACTIVE',price=:price,started_at=COALESCE(started_at,:started),
              expires_at=:expires,updated_at=:started WHERE salon_id=:sid"""),params)
        else:
            conn.execute(text("""INSERT INTO subscriptions(id,salon_id,plan,status,price,currency,started_at,expires_at)
              VALUES(:id,:sid,:plan,'ACTIVE',:price,'UZS',:started,:expires)"""),params)
        conn.execute(text("UPDATE salons SET is_active=TRUE,status='ACTIVE' WHERE id=:id"),{"id":salon_id})
    return {"ok":True,"status":"ACTIVE","days":days,"plan":plan}

@app.get("/")
def index(): return FileResponse(BASE_DIR/"frontend"/"index.html")

@app.get("/s/{slug}")
def public_page(slug: str): return FileResponse(BASE_DIR/"frontend"/"index.html")
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(BASE_DIR/"frontend"/"manifest.webmanifest",media_type="application/manifest+json")
@app.get("/sw.js")
def sw(): return FileResponse(BASE_DIR/"frontend"/"sw.js",media_type="application/javascript")
