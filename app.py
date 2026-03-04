import os, sqlite3, json, threading, requests
from datetime import datetime
from typing import Optional
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from openpyxl import Workbook

BASE_DIR       = os.path.dirname(__file__)
DATA_DIR       = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH        = os.path.join(DATA_DIR, "bookings.sqlite")
XLSX_PATH      = os.path.join(DATA_DIR, "bookings.xlsx")
BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "bank2024").strip()
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS", "5708770608,6488311852").split(",") if x.strip()]
WEBAPP_URL     = os.getenv("WEBAPP_URL", "https://eld96.github.io/HB-booking/")
RENDER_URL     = os.getenv("RENDER_URL", "https://hb-booking-final.onrender.com")
ROOMS = {"GO_3": "Переговорная ГО (3 этаж)", "MINOR": "Кабинет офис Минор"}

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""CREATE TABLE IF NOT EXISTS bookings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL, user_id TEXT, username TEXT,
      full_name TEXT, phone TEXT, room_id TEXT NOT NULL,
      room_name TEXT NOT NULL, date TEXT NOT NULL,
      start_time TEXT NOT NULL, end_time TEXT NOT NULL,
      purpose TEXT NOT NULL, participants TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      chat_id TEXT, department TEXT, reject_reason TEXT)""")
    for col, ct in [("department","TEXT"),("reject_reason","TEXT")]:
        try: con.execute(f"ALTER TABLE bookings ADD COLUMN {col} {ct}")
        except: pass
    con.commit(); con.close()

def tmin(t):
    h,m=t.split(":"); return int(h)*60+int(m)

def has_conflict(room_id, date_s, start_t, end_t, ignore_id=None):
    s0,e0=tmin(start_t),tmin(end_t)
    con=db()
    rows=con.execute("SELECT id,start_time,end_time FROM bookings WHERE room_id=? AND date=? AND status IN ('pending','approved')",(room_id,date_s)).fetchall()
    con.close()
    for r in rows:
        if ignore_id and int(r["id"])==int(ignore_id): continue
        if s0<tmin(r["end_time"]) and tmin(r["start_time"])<e0: return True
    return False

def get_booking(bid):
    con=db(); r=con.execute("SELECT * FROM bookings WHERE id=?",(int(bid),)).fetchone(); con.close()
    return dict(r) if r else None

def list_bookings():
    con=db(); rows=con.execute("SELECT * FROM bookings ORDER BY date,start_time,id").fetchall(); con.close()
    return [dict(r) for r in rows]

def set_status(bid, status, reject_reason=""):
    b=get_booking(bid)
    if not b: return None
    if status=="approved" and has_conflict(b["room_id"],b["date"],b["start_time"],b["end_time"],ignore_id=bid): return None
    con=db()
    if reject_reason: con.execute("UPDATE bookings SET status=?,reject_reason=? WHERE id=?",(status,reject_reason,bid))
    else: con.execute("UPDATE bookings SET status=? WHERE id=?",(status,bid))
    con.commit(); con.close(); bg(export_excel)
    return get_booking(bid)

def export_excel():
    try:
        rows=list_bookings(); wb=Workbook(); ws=wb.active; ws.title="Bookings"
        cols=["id","created_at","status","room_id","room_name","date","start_time","end_time","purpose","participants","user_id","username","full_name","phone","department","reject_reason"]
        ws.append(cols)
        for r in rows: ws.append([r.get(k,"") for k in cols])
        wb.save(XLSX_PATH)
    except Exception as e: app.logger.error("export_excel: %s",e)

def bg(fn,*args): threading.Thread(target=fn,args=args,daemon=True).start()

TG=f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send(chat_id,text,reply_markup=None):
    if not BOT_TOKEN: return
    try:
        p={"chat_id":chat_id,"text":text,"parse_mode":"HTML"}
        if reply_markup: p["reply_markup"]=json.dumps(reply_markup)
        requests.post(f"{TG}/sendMessage",json=p,timeout=8)
    except Exception as e: app.logger.error("tg_send: %s",e)

def notify_admins(booking):
    if not BOT_TOKEN: return
    bid=booking["id"]
    ph=f"\n📞 {booking['phone']}" if booking.get("phone") else ""
    un=f" (@{booking['username']})" if booking.get("username") else ""
    dept=f"\n🏗 {booking['department']}" if booking.get("department") else ""
    text=(f"📋 <b>НОВАЯ ЗАЯВКА #{bid}</b>\n\n"
          f"👤 <b>{booking.get('full_name','—')}</b>{un}\n"
          f"🏢 {booking['room_name']}\n"
          f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}\n"
          f"📝 {booking.get('purpose','—')}{dept}{ph}")
    kb={"inline_keyboard":[[{"text":"✅ Подтвердить","callback_data":f"approve_{bid}"},{"text":"❌ Отклонить","callback_data":f"reject_{bid}"}]]}
    for aid in ADMIN_IDS: bg(tg_send,aid,text,kb)

def notify_user(booking, status, reject_reason=""):
    chat_id=booking.get("chat_id") or booking.get("user_id")
    if not chat_id or not BOT_TOKEN: return
    bid=booking["id"]
    if status=="approved":
        text=(f"✅ <b>Заявка #{bid} ПОДТВЕРЖДЕНА</b>\n\n"
              f"🏢 {booking['room_name']}\n"
              f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}\n"
              f"📝 {booking.get('purpose','')}")
    elif status=="rejected":
        reason=f"\n📌 Причина: {reject_reason}" if reject_reason else ""
        text=(f"❌ <b>Заявка #{bid} ОТКЛОНЕНА</b>{reason}\n\n"
              f"🏢 {booking['room_name']}\n"
              f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}")
    else: return
    bg(tg_send,int(chat_id),text)

@app.get("/")
def page(): return render_template("index.html")

@app.get("/health")
def health(): return jsonify({"ok":True,"rows":len(list_bookings())})

@app.get("/excel")
def excel():
    if not os.path.exists(XLSX_PATH): export_excel()
    return send_file(XLSX_PATH,as_attachment=True,download_name="bookings.xlsx")

@app.get("/api/bookings")
def api_list():
    rows=list_bookings()
    uid=request.args.get("user_id","").strip()
    phone=request.args.get("phone","").strip()
    date=request.args.get("date","").strip()
    status=request.args.get("status","").strip()
    if uid: rows=[r for r in rows if str(r.get("user_id",""))==uid]
    if phone: rows=[r for r in rows if (r.get("phone") or "").replace(" ","")==phone.replace(" ","")]
    if date: rows=[r for r in rows if r.get("date")==date]
    if status: rows=[r for r in rows if r.get("status")==status]
    return jsonify(rows)

@app.post("/api/bookings")
def api_create():
    p=request.get_json(force=True,silent=True) or {}
    for k in ("room_id","date","start_time","end_time","purpose"):
        if not p.get(k): return jsonify({"error":f"missing_{k}"}),400
    room_id=str(p["room_id"])
    if room_id not in ROOMS: return jsonify({"error":"unknown_room"}),400
    date_s,start_t,end_t=str(p["date"]),str(p["start_time"]),str(p["end_time"])
    try:
        datetime.strptime(date_s,"%Y-%m-%d")
        if tmin(end_t)<=tmin(start_t): return jsonify({"error":"invalid_time_range"}),400
    except: return jsonify({"error":"invalid_date_time"}),400
    if has_conflict(room_id,date_s,start_t,end_t): return jsonify({"error":"conflict"}),409
    con=db()
    cur=con.execute("""INSERT INTO bookings
      (created_at,user_id,username,full_name,phone,room_id,room_name,
       date,start_time,end_time,purpose,participants,status,chat_id,department)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        str(p.get("user_id","")),str(p.get("username","")),
        str(p.get("full_name","")),str(p.get("phone","")),
        room_id,ROOMS[room_id],date_s,start_t,end_t,
        str(p.get("purpose","")),str(p.get("participants","1")),
        "pending",str(p.get("chat_id",p.get("user_id",""))),
        str(p.get("department","")),))
    bid=cur.lastrowid; con.commit(); con.close()
    booking=get_booking(bid); bg(export_excel); bg(notify_admins,booking)
    return jsonify(booking),200

@app.post("/api/bookings/<int:bid>/status")
def api_status(bid):
    p=request.get_json(force=True,silent=True) or {}
    if str(p.get("admin_password",""))!=ADMIN_PASSWORD: return jsonify({"error":"bad_password"}),403
    new_status=str(p.get("status",""))
    if new_status not in ("approved","rejected","pending"): return jsonify({"error":"bad_status"}),400
    booking=get_booking(bid)
    if not booking: return jsonify({"error":"not_found"}),404
    reject_reason=str(p.get("reject_reason",""))
    result=set_status(bid,new_status,reject_reason)
    if result is None: return jsonify({"error":"conflict"}),409
    notify_user(booking,new_status,reject_reason)
    return jsonify(result),200

@app.route("/api/bookings/<int:bid>",methods=["PATCH"])
def api_patch(bid):
    p=request.get_json(force=True,silent=True) or {}
    if str(p.get("admin_password",""))!=ADMIN_PASSWORD: return jsonify({"error":"bad_password"}),403
    booking=get_booking(bid)
    if not booking: return jsonify({"error":"not_found"}),404
    date_s=str(p.get("date",booking["date"]))
    start_t=str(p.get("start_time",booking["start_time"]))
    end_t=str(p.get("end_time",booking["end_time"]))
    purpose=str(p.get("purpose",booking["purpose"]))
    if tmin(end_t)<=tmin(start_t): return jsonify({"error":"invalid_time_range"}),400
    if has_conflict(booking["room_id"],date_s,start_t,end_t,ignore_id=bid): return jsonify({"error":"conflict"}),409
    con=db()
    con.execute("UPDATE bookings SET date=?,start_time=?,end_time=?,purpose=? WHERE id=?",(date_s,start_t,end_t,purpose,bid))
    con.commit(); con.close(); bg(export_excel)
    return jsonify(get_booking(bid)),200

@app.delete("/api/bookings/<int:bid>")
def api_delete(bid):
    p=request.get_json(force=True,silent=True) or {}
    if str(p.get("admin_password",""))!=ADMIN_PASSWORD: return jsonify({"error":"bad_password"}),403
    if not get_booking(bid): return jsonify({"error":"not_found"}),404
    con=db(); con.execute("DELETE FROM bookings WHERE id=?",(bid,)); con.commit(); con.close()
    bg(export_excel)
    return jsonify({"ok":True}),200

@app.post("/tg/webhook")
def tg_webhook():
    data=request.get_json(silent=True) or {}
    cb=data.get("callback_query")
    if cb:
        uid=cb["from"]["id"]; cid=cb.get("message",{}).get("chat",{}).get("id"); cbid=cb.get("id"); cdata=cb.get("data","")
        if BOT_TOKEN:
            try: requests.post(f"{TG}/answerCallbackQuery",json={"callback_query_id":cbid},timeout=4)
            except: pass
        if uid in ADMIN_IDS and ("approve_" in cdata or "reject_" in cdata):
            action,bid_s=cdata.split("_",1); bid=int(bid_s)
            status="approved" if action=="approve" else "rejected"
            booking=get_booking(bid)
            if not booking: bg(tg_send,cid,f"❌ Заявка #{bid} не найдена")
            elif booking["status"]!="pending": bg(tg_send,cid,f"ℹ️ Заявка #{bid} уже обработана")
            else:
                result=set_status(bid,status)
                if result is None and status=="approved": bg(tg_send,cid,f"⚠️ Конфликт — нельзя подтвердить #{bid}")
                else:
                    icon="✅" if status=="approved" else "❌"
                    bg(tg_send,cid,f"{icon} Заявка #{bid} {'ПОДТВЕРЖДЕНА' if status=='approved' else 'ОТКЛОНЕНА'}")
                    notify_user(booking,status)
    msg=data.get("message",{})
    if msg.get("text","").startswith("/start"):
        chat_id=msg["chat"]["id"]; uid=msg["from"]["id"]
        text=("👋 <b>Hayot Bank Booking</b>\nВы администратор." if uid in ADMIN_IDS else "👋 <b>Hayot Bank Booking</b>\nНажмите кнопку для бронирования.")
        kb={"keyboard":[[{"text":"📅 Забронировать","web_app":{"url":WEBAPP_URL}}]],"resize_keyboard":True}
        bg(tg_send,chat_id,text,kb)
    return jsonify(ok=True)

def register_webhook():
    if BOT_TOKEN and RENDER_URL:
        try:
            url=f"{RENDER_URL}/tg/webhook"
            r=requests.post(f"{TG}/setWebhook",json={"url":url},timeout=10)
            app.logger.info("Webhook: %s → %s",url,r.json())
        except Exception as e: app.logger.error("register_webhook: %s",e)

def main():
    init_db(); bg(export_excel); bg(register_webhook)
    port=int(os.getenv("PORT","8000"))
    app.run(host="0.0.0.0",port=port)

if __name__=="__main__": main()
