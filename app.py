"""
Hayot Bank — Meeting Room Booking Backend
Flask + PostgreSQL (Render) / SQLite (local)
"""
import os, json, threading, requests, logging
from datetime import datetime, timedelta
from typing import Optional
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from openpyxl import Workbook

# ── PostgreSQL or SQLite ─────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)
if USE_PG and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3

# ── CONFIG ───────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
XLSX_PATH      = os.path.join(DATA_DIR, "bookings.xlsx")

BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "bank2024").strip()
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS","5708770608,6488311852").split(",") if x.strip()]
WEBAPP_URL     = os.getenv("WEBAPP_URL", "https://eld96.github.io/HB-booking/")
RENDER_URL     = os.getenv("RENDER_URL", "https://hb-booking-final.onrender.com")

ROOMS = {
    "GO_3":  "Переговорная ГО (3 этаж)",
    "MINOR": "Кабинет офис Минор",
}

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
logging.basicConfig(level=logging.INFO)
log = app.logger
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── DB HELPERS ───────────────────────────────────────────────────
def get_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    c = sqlite3.connect(os.path.join(DATA_DIR, "bookings.sqlite"), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def db_exec(sql, params=()):
    """Run INSERT/UPDATE/DELETE. For INSERT returns new row id."""
    conn = get_conn()
    try:
        if USE_PG:
            # Always use RETURNING id for INSERT so we get the PG serial id
            if sql.strip().upper().startswith("INSERT"):
                if "RETURNING id" not in sql.upper():
                    sql = sql.rstrip().rstrip(";") + " RETURNING id"
            sql = sql.replace("?", "%s")
            sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            if sql.strip().upper().startswith("INSERT"):
                row = cur.fetchone()
                return row["id"] if row else None
            return None
        else:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()

def db_query(sql, params=()):
    """Run SELECT, return list of dicts."""
    conn = get_conn()
    try:
        if USE_PG:
            sql = sql.replace("?", "%s")
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        else:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def db_one(sql, params=()):
    rows = db_query(sql, params)
    return rows[0] if rows else None

# ── INIT DB ──────────────────────────────────────────────────────
def init_db():
    CREATE = """
    CREATE TABLE IF NOT EXISTS bookings (
        id            {pk},
        created_at    TEXT NOT NULL,
        user_id       TEXT,
        username      TEXT,
        full_name     TEXT,
        phone         TEXT,
        room_id       TEXT NOT NULL,
        room_name     TEXT NOT NULL,
        date          TEXT NOT NULL,
        start_time    TEXT NOT NULL,
        end_time      TEXT NOT NULL,
        purpose       TEXT NOT NULL,
        participants  TEXT,
        status        TEXT NOT NULL DEFAULT 'pending',
        chat_id       TEXT,
        department    TEXT,
        reject_reason TEXT
    )"""
    if USE_PG:
        sql = CREATE.format(pk="SERIAL PRIMARY KEY")
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            log.info("✅ PostgreSQL DB ready")
        finally:
            conn.close()
    else:
        sql = CREATE.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT")
        conn = get_conn()
        conn.execute(sql)
        for col, ct in [("department","TEXT"),("reject_reason","TEXT")]:
            try: conn.execute(f"ALTER TABLE bookings ADD COLUMN {col} {ct}")
            except: pass
        conn.commit()
        conn.close()
        log.info("✅ SQLite DB ready")

# ── LOGIC HELPERS ────────────────────────────────────────────────
def tmin(t):
    h, m = t.split(":")
    return int(h)*60 + int(m)

def has_conflict(room_id, date_s, start_t, end_t, ignore_id=None):
    rows = db_query(
        "SELECT id, start_time, end_time FROM bookings "
        "WHERE room_id=? AND date=? AND status IN ('pending','approved')",
        (room_id, date_s)
    )
    s0, e0 = tmin(start_t), tmin(end_t)
    for r in rows:
        if ignore_id and int(r["id"]) == int(ignore_id):
            continue
        if s0 < tmin(r["end_time"]) and tmin(r["start_time"]) < e0:
            return True
    return False

def get_booking(bid):
    return db_one("SELECT * FROM bookings WHERE id=?", (int(bid),))

def list_bookings(uid=None, phone=None, date=None, status=None):
    where, params = ["1=1"], []
    if uid:    where.append("user_id=?");    params.append(str(uid))
    if phone:
        if USE_PG:
            where.append("REPLACE(phone,' ','')=?")
        else:
            where.append("REPLACE(phone,' ','')=?")
        params.append(phone.replace(" ",""))
    if date:   where.append("date=?");       params.append(date)
    if status: where.append("status=?");     params.append(status)
    return db_query(
        "SELECT * FROM bookings WHERE " + " AND ".join(where) +
        " ORDER BY date, start_time, id", tuple(params)
    )

def set_status(bid, status, reject_reason=""):
    b = get_booking(bid)
    if not b: return None
    if status == "approved" and has_conflict(
        b["room_id"], b["date"], b["start_time"], b["end_time"], ignore_id=bid
    ):
        return None
    if reject_reason:
        db_exec("UPDATE bookings SET status=?, reject_reason=? WHERE id=?",
                (status, reject_reason, bid))
    else:
        db_exec("UPDATE bookings SET status=? WHERE id=?", (status, bid))
    bg(export_excel)
    return get_booking(bid)

def export_excel():
    try:
        rows = list_bookings()
        wb = Workbook(); ws = wb.active; ws.title = "Bookings"
        cols = ["id","created_at","status","room_id","room_name","date",
                "start_time","end_time","purpose","participants",
                "user_id","username","full_name","phone","department","reject_reason"]
        ws.append(cols)
        for r in rows:
            ws.append([str(r.get(k,"") or "") for k in cols])
        wb.save(XLSX_PATH)
    except Exception as e:
        log.error("export_excel: %s", e)

def bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()

# ── TELEGRAM ─────────────────────────────────────────────────────
def tg_send(chat_id, text, markup=None):
    if not BOT_TOKEN or not chat_id: return
    try:
        p = {"chat_id": int(chat_id), "text": text, "parse_mode": "HTML"}
        if markup: p["reply_markup"] = json.dumps(markup)
        requests.post(f"{TG}/sendMessage", json=p, timeout=10)
    except Exception as e:
        log.error("tg_send %s: %s", chat_id, e)

def notify_admins(booking):
    if not BOT_TOKEN: return
    bid  = booking["id"]
    ph   = f"\n📞 {booking['phone']}" if booking.get("phone") else ""
    un   = f" (@{booking['username']})" if booking.get("username") else ""
    dept = f"\n🏗 {booking['department']}" if booking.get("department") else ""
    text = (
        f"📋 <b>НОВАЯ ЗАЯВКА #{bid}</b>\n\n"
        f"👤 <b>{booking.get('full_name','—')}</b>{un}\n"
        f"🏢 {booking['room_name']}\n"
        f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}\n"
        f"📝 {booking.get('purpose','—')}{dept}{ph}"
    )
    kb = {"inline_keyboard": [[
        {"text": "✅ Подтвердить", "callback_data": f"approve_{bid}"},
        {"text": "❌ Отклонить",   "callback_data": f"reject_{bid}"},
    ]]}
    for aid in ADMIN_IDS:
        bg(tg_send, aid, text, kb)

def notify_user(booking, status, reject_reason=""):
    """Send Telegram notification to the booking author."""
    # Get chat_id — prefer chat_id field, fallback to user_id
    chat_id = booking.get("chat_id") or booking.get("user_id")
    if not chat_id or not BOT_TOKEN:
        return
    # Skip non-Telegram users (web_xxx UUID or empty)
    cid_str = str(chat_id).strip()
    if not cid_str or cid_str.startswith("web_") or not cid_str.lstrip("-").isdigit():
        return
    bid = booking["id"]
    if status == "approved":
        text = (
            f"✅ <b>Заявка #{bid} ПОДТВЕРЖДЕНА!</b>\n\n"
            f"🏢 {booking['room_name']}\n"
            f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}\n"
            f"📝 {booking.get('purpose','')}"
        )
    elif status == "rejected":
        r = f"\n📌 <b>Причина:</b> {reject_reason}" if reject_reason else ""
        text = (
            f"❌ <b>Заявка #{bid} ОТКЛОНЕНА</b>{r}\n\n"
            f"🏢 {booking['room_name']}\n"
            f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}"
        )
    else:
        return
    bg(tg_send, int(cid_str), text)

def set_bot_commands():
    if not BOT_TOKEN: return
    try:
        requests.post(f"{TG}/setMyCommands", json={"commands": [
            {"command": "start",      "description": "Открыть бронирование"},
            {"command": "mybookings", "description": "Мои заявки"},
            {"command": "all",        "description": "Бронирования на сегодня"},
            {"command": "cancel",     "description": "Отменить заявку: /cancel 42"},
            {"command": "help",       "description": "Помощь"},
        ]}, timeout=8)
        log.info("Bot commands set ✅")
    except Exception as e:
        log.error("setMyCommands: %s", e)

def register_webhook():
    if not BOT_TOKEN or not RENDER_URL: return
    try:
        url = f"{RENDER_URL}/tg/webhook"
        r = requests.post(f"{TG}/setWebhook",
                          json={"url": url, "drop_pending_updates": True},
                          timeout=10)
        log.info("Webhook: %s", r.json())
    except Exception as e:
        log.error("register_webhook: %s", e)

# ── ROUTES ───────────────────────────────────────────────────────
@app.get("/")
def page():
    return render_template("index.html")

@app.get("/health")
def health():
    rows = list_bookings()
    return jsonify({"ok": True, "rows": len(rows), "db": "pg" if USE_PG else "sqlite"})

@app.get("/excel")
def excel_download():
    if not os.path.exists(XLSX_PATH):
        export_excel()
    return send_file(XLSX_PATH, as_attachment=True, download_name="bookings.xlsx")

@app.get("/api/bookings")
def api_list():
    uid    = request.args.get("user_id","").strip()
    phone  = request.args.get("phone","").strip()
    date   = request.args.get("date","").strip()
    status = request.args.get("status","").strip()
    rows   = list_bookings(uid or None, phone or None, date or None, status or None)
    return jsonify(rows)

@app.post("/api/bookings")
def api_create():
    p = request.get_json(force=True, silent=True) or {}
    for k in ("room_id","date","start_time","end_time","purpose"):
        if not p.get(k):
            return jsonify({"error": f"missing_{k}"}), 400
    room_id = str(p["room_id"])
    if room_id not in ROOMS:
        return jsonify({"error": "unknown_room"}), 400
    date_s, start_t, end_t = str(p["date"]), str(p["start_time"]), str(p["end_time"])
    try:
        datetime.strptime(date_s, "%Y-%m-%d")
        if tmin(end_t) <= tmin(start_t):
            return jsonify({"error": "invalid_time_range"}), 400
    except ValueError:
        return jsonify({"error": "invalid_date_time"}), 400
    if has_conflict(room_id, date_s, start_t, end_t):
        return jsonify({"error": "conflict"}), 409

    bid = db_exec("""
        INSERT INTO bookings
          (created_at,user_id,username,full_name,phone,room_id,room_name,
           date,start_time,end_time,purpose,participants,status,chat_id,department)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        str(p.get("user_id","")),   str(p.get("username","")),
        str(p.get("full_name","")), str(p.get("phone","")),
        room_id, ROOMS[room_id], date_s, start_t, end_t,
        str(p.get("purpose","")),   str(p.get("participants","1")),
        "pending",
        str(p.get("chat_id", p.get("user_id",""))),
        str(p.get("department","")),
    ))
    booking = get_booking(int(bid))
    bg(export_excel)
    bg(notify_admins, booking)
    return jsonify(booking), 200

@app.post("/api/bookings/<int:bid>/status")
def api_status(bid):
    p = request.get_json(force=True, silent=True) or {}
    if str(p.get("admin_password","")) != ADMIN_PASSWORD:
        return jsonify({"error": "bad_password"}), 403
    new_status = str(p.get("status",""))
    if new_status not in ("approved","rejected","pending"):
        return jsonify({"error": "bad_status"}), 400
    booking = get_booking(bid)
    if not booking:
        return jsonify({"error": "not_found"}), 404
    reject_reason = str(p.get("reject_reason",""))
    result = set_status(bid, new_status, reject_reason)
    if result is None:
        return jsonify({"error": "conflict"}), 409
    # ✅ Always notify the booking author
    bg(notify_user, booking, new_status, reject_reason)
    return jsonify(result), 200

@app.route("/api/bookings/<int:bid>", methods=["PATCH"])
def api_patch(bid):
    p = request.get_json(force=True, silent=True) or {}
    if str(p.get("admin_password","")) != ADMIN_PASSWORD:
        return jsonify({"error": "bad_password"}), 403
    booking = get_booking(bid)
    if not booking: return jsonify({"error": "not_found"}), 404
    date_s  = str(p.get("date",       booking["date"]))
    start_t = str(p.get("start_time", booking["start_time"]))
    end_t   = str(p.get("end_time",   booking["end_time"]))
    purpose = str(p.get("purpose",    booking["purpose"]))
    if tmin(end_t) <= tmin(start_t):
        return jsonify({"error": "invalid_time_range"}), 400
    if has_conflict(booking["room_id"], date_s, start_t, end_t, ignore_id=bid):
        return jsonify({"error": "conflict"}), 409
    db_exec("UPDATE bookings SET date=?,start_time=?,end_time=?,purpose=? WHERE id=?",
            (date_s, start_t, end_t, purpose, bid))
    bg(export_excel)
    return jsonify(get_booking(bid)), 200

@app.delete("/api/bookings/<int:bid>")
def api_delete(bid):
    p = request.get_json(force=True, silent=True) or {}
    if str(p.get("admin_password","")) != ADMIN_PASSWORD:
        return jsonify({"error": "bad_password"}), 403
    if not get_booking(bid): return jsonify({"error": "not_found"}), 404
    db_exec("DELETE FROM bookings WHERE id=?", (bid,))
    bg(export_excel)
    return jsonify({"ok": True}), 200


# ── USER CANCEL (guest) ──────────────────────────────────────────
@app.post("/api/bookings/<int:bid>/cancel")
def api_cancel(bid):
    """Allow booking author to cancel their own booking (pending or approved).
    Marks status='cancelled' (does not conflict with availability).
    """
    p = request.get_json(force=True, silent=True) or {}
    user_id = str(p.get("user_id","")).strip()
    phone   = str(p.get("phone","")).strip().replace(" ","")
    booking = get_booking(bid)
    if not booking:
        return jsonify({"error":"not_found"}), 404
    if booking.get("status") not in ("pending","approved"):
        return jsonify({"error":"cannot_cancel"}), 400

    # Author check: match user_id OR phone (normalized)
    b_uid = str(booking.get("user_id","") or "").strip()
    b_ph  = str(booking.get("phone","") or "").strip().replace(" ","")
    if not ((user_id and b_uid and user_id == b_uid) or (phone and b_ph and phone == b_ph)):
        return jsonify({"error":"forbidden"}), 403

    db_exec("UPDATE bookings SET status=? WHERE id=?", ("cancelled", bid))
    bg(export_excel)
    # Notify admins + user (if TG configured)
    try:
        bg(notify_user, booking, "cancelled", "")
    except Exception:
        pass
    return jsonify(get_booking(bid)), 200


# ── ADMIN ANALYTICS (BI) ─────────────────────────────────────────
@app.post("/api/admin/analytics")
def api_admin_analytics():
    """Return aggregated metrics for BI dashboards (format expected by frontend)."""
    p = request.get_json(force=True, silent=True) or {}
    if str(p.get("admin_password","")) != ADMIN_PASSWORD:
        return jsonify({"error":"bad_password"}), 403

    start_date = str(p.get("start_date","")).strip()
    end_date   = str(p.get("end_date","")).strip()
    if not start_date or not end_date:
        return jsonify({"error":"missing_dates"}), 400
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date,   "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error":"bad_dates"}), 400
    if ed < sd:
        return jsonify({"error":"bad_range"}), 400

    rows = db_query(
        "SELECT id,status,room_id,room_name,date,start_time,end_time,created_at,department "
        "FROM bookings WHERE date>=? AND date<=? ORDER BY date,start_time,id",
        (start_date, end_date)
    )

    total = len(rows)
    st_counts = {"pending":0,"approved":0,"rejected":0,"cancelled":0}
    by_day = {}  # date -> count
    by_room = {} # room_id -> {room_id, room_name, count}
    by_hour = {str(h).zfill(2):0 for h in range(0,24)}
    by_dept = {}
    pending_minutes = []
    now = datetime.utcnow()

    # For heatmap: date -> room_id -> minutes (approved + pending)
    heat = {}

    def _parse_hhmm(s: str):
        try:
            hh, mm = s.split(":")[:2]
            return int(hh), int(mm)
        except Exception:
            return 0, 0

    def _duration_minutes(start_s: str, end_s: str) -> int:
        sh, sm = _parse_hhmm(str(start_s or "00:00"))
        eh, em = _parse_hhmm(str(end_s or "00:00"))
        mins = (eh*60+em) - (sh*60+sm)
        return int(max(0, mins))

    for r in rows:
        st = (r.get("status") or "pending").lower()
        if st not in st_counts:
            st_counts[st] = 0
        st_counts[st] += 1

        d = r.get("date")
        by_day[d] = by_day.get(d, 0) + 1

        rid = (r.get("room_id") or "").strip() or (r.get("room_name") or "").strip() or "—"
        rname = (r.get("room_name") or "").strip() or rid
        if rid not in by_room:
            by_room[rid] = {"room_id": rid, "room_name": rname, "count": 0}
        by_room[rid]["count"] += 1

        stt = str(r.get("start_time","00:00"))
        try:
            hh = stt.split(":")[0].zfill(2)
            if hh in by_hour:
                by_hour[hh] += 1
        except Exception:
            pass

        dept = (r.get("department") or "").strip() or "—"
        by_dept[dept] = by_dept.get(dept, 0) + 1

        if st == "pending":
            ca = str(r.get("created_at") or "")
            created = None
            try:
                created = datetime.strptime(ca, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    created = datetime.fromisoformat(ca)
                except Exception:
                    created = None
            if created:
                pending_minutes.append(int(max(0, (now - created).total_seconds()//60)))

        # Heatmap minutes: approved + pending
        if st in ("approved", "pending"):
            mins = _duration_minutes(r.get("start_time"), r.get("end_time"))
            if mins:
                heat.setdefault(d, {})
                heat[d][rid] = int(heat[d].get(rid, 0) + mins)

    approved = st_counts.get("approved",0)
    rejected = st_counts.get("rejected",0)
    decided = approved + rejected
    approval_rate = int(round((approved/decided)*100)) if decided else 0
    avg_pending_minutes = int(round(sum(pending_minutes)/len(pending_minutes))) if pending_minutes else 0

    # Conversion: approved / (approved+rejected) per room and department
    conv_room = {}
    conv_dept = {}
    for r in rows:
        st = (r.get("status") or "").lower()
        rid = (r.get("room_id") or "").strip() or (r.get("room_name") or "").strip() or "—"
        rname = (r.get("room_name") or "").strip() or rid
        dept = (r.get("department") or "").strip() or "—"
        if rid not in conv_room: conv_room[rid] = {"room_id": rid, "room_name": rname, "approved":0, "rejected":0}
        if dept not in conv_dept: conv_dept[dept] = {"approved":0, "rejected":0}
        if st in ("approved","rejected"):
            conv_room[rid][st] += 1
            conv_dept[dept][st] += 1

    def _rate_room(d):
        out = []
        for rid,v in d.items():
            a, rj = v.get("approved",0), v.get("rejected",0)
            denom = a + rj
            out.append({
                "room_id": rid,
                "room_name": v.get("room_name") or rid,
                "rate": int(round((a/denom)*100)) if denom else 0,
                "approved": a,
                "rejected": rj
            })
        out.sort(key=lambda x: (-x["rate"], -(x["approved"]+x["rejected"]), x["room_name"]))
        return out

    def _rate_kv(d):
        out = {}
        for k,v in d.items():
            a, rj = v.get("approved",0), v.get("rejected",0)
            denom = a + rj
            out[k] = int(round((a/denom)*100)) if denom else 0
        return out

    # Build continuous day series (fill missing dates with 0)
    day_counts = []
    cur = sd
    while cur <= ed:
        ds = cur.strftime("%Y-%m-%d")
        day_counts.append({"date": ds, "count": int(by_day.get(ds, 0))})
        cur = cur + timedelta(days=1)

    # Hour series 00..23
    hour_counts = [{"hour": str(h).zfill(2), "count": int(by_hour.get(str(h).zfill(2), 0))} for h in range(24)]

    # Rooms list sorted by count desc
    room_counts = sorted(by_room.values(), key=lambda x: (-int(x.get("count",0)), str(x.get("room_name",""))))

    # Heatmap payload
    heat_rooms = [{"room_id": r["room_id"], "room_name": r.get("room_name") or r["room_id"]} for r in room_counts]
    heat_rows = []
    cur = sd
    while cur <= ed:
        ds = cur.strftime("%Y-%m-%d")
        row = {"date": ds}
        m = heat.get(ds, {})
        for rr in heat_rooms:
            row[rr["room_id"]] = int(m.get(rr["room_id"], 0))
        heat_rows.append(row)
        cur = cur + timedelta(days=1)

    payload = {
        # Summary
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "pending": st_counts.get("pending",0),
        "cancelled": st_counts.get("cancelled",0),
        "approval_rate": approval_rate,
        "avg_pending_minutes": avg_pending_minutes,

        # Frontend-expected fields
        "status_counts": st_counts,
        "day_counts": day_counts,
        "hour_counts": hour_counts,
        "room_counts": room_counts,
        "heatmap_minutes": {
            "rooms": heat_rooms,
            "rows": heat_rows
        },

        # Keep old keys for backward compatibility (optional)
        "counts": st_counts,
        "by_day": by_day,
        "by_room": {k:v["count"] for k,v in by_room.items()},
        "by_hour": by_hour,
        "by_department": by_dept,
        "conversion_room": _rate_kv({k:{"approved":v["approved"],"rejected":v["rejected"]} for k,v in conv_room.items()}),
        "conversion_department": _rate_kv(conv_dept),
        "conversion_room_rows": _rate_room(conv_room),
    }

    return jsonify(payload), 200

@app.post("/api/admin/export.csv")
def api_admin_export_csv():
    p = request.get_json(force=True, silent=True) or {}
    if str(p.get("admin_password","")) != ADMIN_PASSWORD:
        return jsonify({"error":"bad_password"}), 403
    start_date = str(p.get("start_date",""))
    end_date   = str(p.get("end_date",""))
    if not start_date or not end_date:
        return jsonify({"error":"missing_dates"}), 400
    rows = db_query(
        "SELECT id,created_at,status,room_name,date,start_time,end_time,purpose,participants,full_name,phone,department,reject_reason "
        "FROM bookings WHERE date>=? AND date<=? ORDER BY date,start_time,id",
        (start_date, end_date)
    )
    import csv
    from io import StringIO
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["id","created_at","status","room","date","start","end","purpose","participants","full_name","phone","department","reject_reason"])
    for r in rows:
        w.writerow([r.get("id"),r.get("created_at"),r.get("status"),r.get("room_name"),r.get("date"),r.get("start_time"),r.get("end_time"),r.get("purpose"),r.get("participants"),r.get("full_name"),r.get("phone"),r.get("department"),r.get("reject_reason")])
    csv_data = buf.getvalue().encode("utf-8-sig")
    from flask import Response
    return Response(csv_data, mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=hb_analytics_{start_date}_{end_date}.csv"})


# ── TELEGRAM WEBHOOK ─────────────────────────────────────────────
@app.post("/tg/webhook")
def tg_webhook():
    data = request.get_json(silent=True) or {}

    # ── Inline button callbacks (approve / reject from admin) ──
    cb = data.get("callback_query")
    if cb:
        uid   = cb["from"]["id"]
        cid   = cb.get("message", {}).get("chat", {}).get("id")
        cbid  = cb.get("id", "")
        cdata = cb.get("data", "")
        try: requests.post(f"{TG}/answerCallbackQuery",
                           json={"callback_query_id": cbid}, timeout=4)
        except: pass

        if uid in ADMIN_IDS and ("approve_" in cdata or "reject_" in cdata):
            action, bid_s = cdata.split("_", 1)
            bid    = int(bid_s)
            status = "approved" if action == "approve" else "rejected"
            booking = get_booking(bid)
            if not booking:
                bg(tg_send, cid, f"❌ Заявка #{bid} не найдена")
            elif booking["status"] != "pending":
                bg(tg_send, cid, f"ℹ️ Заявка #{bid} уже обработана ({booking['status']})")
            else:
                result = set_status(bid, status)
                if result is None and status == "approved":
                    bg(tg_send, cid, f"⚠️ Конфликт — нельзя подтвердить #{bid}")
                else:
                    icon = "✅" if status == "approved" else "❌"
                    lbl  = "ПОДТВЕРЖДЕНА" if status == "approved" else "ОТКЛОНЕНА"
                    bg(tg_send, cid, f"{icon} Заявка #{bid} {lbl}")
                    bg(notify_user, booking, status)  # ✅ notify author
        return jsonify(ok=True)

    # ── Text commands ──
    msg  = data.get("message", {})
    text = msg.get("text", "").strip()
    if not text: return jsonify(ok=True)

    chat_id  = msg["chat"]["id"]
    uid      = msg["from"]["id"]
    fname    = msg["from"].get("first_name", "")
    is_admin = uid in ADMIN_IDS

    if text.startswith("/start"):
        bg(tg_send, chat_id,
           f"👋 Добро пожаловать, <b>{fname or 'сотрудник'}</b>!\n\n"
           f"🏦 <b>Hayot Bank — Бронирование переговорных</b>\n\n"
           f"🏛 Переговорная ГО (3 этаж)\n"
           f"🏢 Кабинет офис Минор\n\n"
           f"Нажмите кнопку ниже ↓",
           {"keyboard": [[{"text": "📅 Открыть систему бронирования",
                           "web_app": {"url": WEBAPP_URL}}]],
            "resize_keyboard": True})

    elif text.startswith("/help"):
        bg(tg_send, chat_id,
           "📖 <b>Команды:</b>\n\n"
           "/start — Открыть систему\n"
           "/mybookings — Мои заявки\n"
           "/all — Бронирования сегодня\n"
           "/cancel 42 — Отменить заявку #42\n"
           "/help — Справка")

    elif text.startswith("/mybookings"):
        rows = list_bookings(uid=str(uid))
        if not rows:
            bg(tg_send, chat_id, "📭 У вас нет бронирований.\nИспользуйте /start для создания.")
        else:
            SM = {"approved": "✅", "pending": "⏳", "rejected": "❌"}
            lines = ["📋 <b>Ваши последние заявки:</b>\n"]
            for b in sorted(rows, key=lambda x: x["date"]+x["start_time"], reverse=True)[:10]:
                icon = SM.get(b["status"], "•")
                r_line = f"\n   📌 {b['reject_reason']}" if b.get("reject_reason") else ""
                lines.append(
                    f"{icon} <b>#{b['id']}</b> — {b['room_name']}\n"
                    f"   📅 {b['date']}  ⏰ {b['start_time']}–{b['end_time']}\n"
                    f"   📝 {b.get('purpose','—')}{r_line}"
                )
            lines.append("\nОтменить: /cancel <номер>")
            bg(tg_send, chat_id, "\n\n".join(lines))

    elif text.startswith("/all"):
        today = datetime.now().strftime("%Y-%m-%d")
        rows = [b for b in list_bookings(date=today) if b["status"] != "rejected"]
        if not rows:
            bg(tg_send, chat_id, f"📅 На сегодня ({today}) бронирований нет.")
        else:
            SM = {"approved": "✅", "pending": "⏳"}
            lines = [f"📅 <b>Бронирования на {today}:</b>\n"]
            for b in sorted(rows, key=lambda x: x["start_time"]):
                dept = f" [{b['department']}]" if b.get("department") else ""
                lines.append(
                    f"{SM.get(b['status'],'•')} {b['start_time']}–{b['end_time']} | {b['room_name']}\n"
                    f"   👤 {b.get('full_name','—')}{dept}"
                )
            bg(tg_send, chat_id, "\n\n".join(lines))

    elif text.startswith("/cancel"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            bg(tg_send, chat_id, "Укажите номер: <code>/cancel 42</code>")
        else:
            bid = int(parts[1])
            booking = get_booking(bid)
            if not booking:
                bg(tg_send, chat_id, f"❌ Заявка #{bid} не найдена")
            elif str(booking.get("user_id","")) != str(uid) and not is_admin:
                bg(tg_send, chat_id, "🔒 Можно отменять только свои заявки")
            elif booking["status"] != "pending":
                bg(tg_send, chat_id, f"ℹ️ Заявка #{bid} уже {booking['status']}")
            else:
                set_status(bid, "rejected", "Отменено через бот")
                bg(tg_send, chat_id,
                   f"✅ Заявка #{bid} отменена.\n"
                   f"🏢 {booking['room_name']}\n"
                   f"📅 {booking['date']}  ⏰ {booking['start_time']}–{booking['end_time']}")

    return jsonify(ok=True)

# ── STARTUP ──────────────────────────────────────────────────────
# ── STARTUP (runs on import — works with gunicorn AND python app.py) ──
def _startup():
    init_db()
    bg(export_excel)
    bg(register_webhook)
    bg(set_bot_commands)

_startup()  # Gunicorn imports this module, so _startup() always runs

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
