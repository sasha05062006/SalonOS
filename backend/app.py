from contextlib import asynccontextmanager
from datetime import datetime, date, time, timedelta, timezone\nfrom zoneinfo import ZoneInfo
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
def now_utc(): return datetime.now(ZoneInfo("Asia/Tashkent")).replace(tzinfo=None)
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); yield

app=FastAPI(title="SalonOS API",version="1.0.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=False,allow_methods=["*"],allow_headers=["*"])

def auth_user(request: Request):
    token=request.cookies.get(COOKIE)
    if not token: raise HTTPException(401,"Требуется авторизация")
    with get_engine().begin() as conn:
        row=conn.execute(text("""SELECT u.*,s.is_active salon_active,s.name salon_name,s.slug salon_slug
          FROM sessions x JOIN users u ON u.id=x.user_id JOIN salons s ON s.id=u.salon_id
          WHERE x.token=:token AND x.expires_at>:now AND u.is_active=TRUE"""),{"token":token,"now":now_utc()}).first()
    if not row or not row.salon_active: raise HTTPException(401,"Сессия недействительна")
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
        conn.execute(text("INSERT INTO salons(id,slug,name) VALUES(:id,:slug,:name)"),{"id":salon_id,"slug":slug,"name":payload.salon_name.strip()})
        conn.execute(text("""INSERT INTO users(id,salon_id,name,email,password_hash,role) VALUES(:id,:salon,:name,:email,:hash,'admin')"""),
                     {"id":user_id,"salon":salon_id,"name":payload.name.strip(),"email":email,"hash":hash_password(payload.password)})
        token=secrets.token_urlsafe(48)
        conn.execute(text("INSERT INTO sessions(token,user_id,expires_at) VALUES(:token,:uid,:exp)"),{"token":token,"uid":user_id,"exp":now_utc()+timedelta(days=30)})
    response.set_cookie(COOKIE,token,httponly=True,samesite="lax",secure=False,max_age=2592000)
    return {"ok":True,"slug":slug}

@app.post("/api/auth/login")
def login(payload:LoginIn,response:Response):
    email=payload.email.strip().lower()
    with get_engine().begin() as conn:
        row=conn.execute(text("SELECT * FROM users WHERE lower(email)=:email AND is_active=TRUE"),{"email":email}).first()
        if not row or not verify_password(payload.password,rowdict(row)["password_hash"]): raise HTTPException(401,"Неверный email или пароль")
        token=secrets.token_urlsafe(48)
        conn.execute(text("INSERT INTO sessions(token,user_id,expires_at) VALUES(:token,:uid,:exp)"),{"token":token,"uid":row.id,"exp":now_utc()+timedelta(days=30)})
    response.set_cookie(COOKIE,token,httponly=True,samesite="lax",secure=False,max_age=2592000); return {"ok":True}

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
    response.delete_cookie(COOKIE); return {"ok":True}

@app.get("/api/me")
def me(request:Request):
    u=auth_user(request); return {k:u[k] for k in ("id","name","email","role","salon_id","salon_name","salon_slug")}

@app.get("/api/admin/salon")
def get_salon(request:Request):
    u=require_role(request,"admin","master")
    with get_engine().begin() as conn: row=conn.execute(text("SELECT * FROM salons WHERE id=:id"),{"id":u["salon_id"]}).first()
    return rowdict(row)

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
        salon=conn.execute(text("""SELECT id,slug,name,description,logo_url,phone,address,timezone,theme,accent_color FROM salons WHERE slug=:slug AND is_active=TRUE"""),{"slug":slug}).first()
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
        salon=conn.execute(text("SELECT id FROM salons WHERE slug=:slug AND is_active=TRUE"),{"slug":slug}).first()
        if not salon: raise HTTPException(404,"Салон не найден")
        if not conn.execute(text("""SELECT 1 FROM masters m JOIN services s ON s.salon_id=m.salon_id WHERE m.id=:m AND s.id=:s AND m.salon_id=:sid"""),{"m":master_id,"s":service_id,"sid":salon.id}).first(): return {"slots":[]}
        return {"slots":availability_for(conn,master_id,service_id,d)}

@app.post("/api/public/{slug}/appointments")
def public_appointment(slug:str,payload:AppointmentIn):
    start=parse_dt(payload.start_at)
    if start<now_utc(): raise HTTPException(400,"Нельзя записаться в прошлое")
    with get_engine().begin() as conn:
        salon=conn.execute(text("SELECT * FROM salons WHERE slug=:slug AND is_active=TRUE"),{"slug":slug}).first()
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
    send_telegram(f"🔔 Новая запись в {salon.name}\\n{result['client_name']}\\n{result['service']} — {result['master']}\\n{start.strftime('%d.%m.%Y %H:%M')}\\n{result['price']:,} сум".replace(","," "))
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

@app.get("/api/superadmin/salons")
def superadmin_salons(request:Request):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn: rows=conn.execute(text("SELECT id,slug,name,phone,is_active,created_at FROM salons ORDER BY created_at DESC")).fetchall()
    return [rowdict(x) for x in rows]

@app.patch("/api/superadmin/salons/{salon_id}")
def superadmin_toggle(salon_id:str,request:Request,active:bool):
    u=auth_user(request)
    if u["role"]!="superadmin": raise HTTPException(403,"Недостаточно прав")
    with get_engine().begin() as conn: conn.execute(text("UPDATE salons SET is_active=:active WHERE id=:id"),{"active":active,"id":salon_id})
    return {"ok":True}

@app.get("/")
def index(): return FileResponse(BASE_DIR/"frontend"/"index.html")

@app.get("/s/{slug}")
def public_page(slug: str): return FileResponse(BASE_DIR/"frontend"/"index.html")
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(BASE_DIR/"frontend"/"manifest.webmanifest",media_type="application/manifest+json")
@app.get("/sw.js")
def sw(): return FileResponse(BASE_DIR/"frontend"/"sw.js",media_type="application/javascript")
