import os, sqlite3
from datetime import datetime

from flask import Flask, request, jsonify, render_template, send_file
from openpyxl import Workbook

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "bookings.sqlite")
XLSX_PATH = os.path.join(DATA_DIR, "bookings.xlsx")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234").strip()

ROOMS = {
    "GO_3":  "Переговорная ГО (3 этаж)",
    "MINOR": "Кабинет офис Минор",
}

app = Flask(__name__)

def db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.execute("""
    CREATE TABLE IF NOT EXISTS bookings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      user_id TEXT,
      username TEXT,
      full_name TEXT,
      phone TEXT,
      room_id TEXT NOT NULL,
      room_name TEXT NOT NULL,
      date TEXT NOT NULL,
      start_time TEXT NOT NULL,
      end_time TEXT NOT NULL,
      purpose TEXT NOT NULL,
      participants TEXT,
      status TEXT NOT NULL
    )
    """)
    con.commit()
    con.close()

def tmin(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)

def overlaps(a1,a2,b1,b2):
    return a1 < b2 and b1 < a2

def is_past(date_s: str, start_time: str) -> bool:
    d = datetime.strptime(date_s, "%Y-%m-%d").date()
    now = datetime.now()
    if d < now.date():
        return True
    if d > now.date():
        return False
    return tmin(start_time) <= (now.hour*60 + now.minute)

def has_conflict(room_id: str, date_s: str, start_t: str, end_t: str, ignore_id=None) -> bool:
    s0, e0 = tmin(start_t), tmin(end_t)
    con = db()
    rows = con.execute(
        "SELECT id,start_time,end_time FROM bookings WHERE room_id=? AND date=? AND status IN ('pending','approved')",
        (room_id, date_s)
    ).fetchall()
    con.close()
    for r in rows:
        if ignore_id is not None and int(r["id"]) == int(ignore_id):
            continue
        if overlaps(s0, e0, tmin(r["start_time"]), tmin(r["end_time"])):
            return True
    return False

def list_bookings():
    con = db()
    rows = con.execute("SELECT * FROM bookings ORDER BY date, start_time, id").fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_booking(bid: int):
    con = db()
    r = con.execute("SELECT * FROM bookings WHERE id=?", (int(bid),)).fetchone()
    con.close()
    return dict(r) if r else None

def export_excel():
    rows = list_bookings()
    wb = Workbook()
    ws = wb.active
    ws.title = "Bookings"
    ws.append(["id","created_at","status","room_id","room_name","date","start_time","end_time","purpose","participants","user_id","username","full_name","phone"])
    for r in rows:
        ws.append([
            r.get("id",""), r.get("created_at",""), r.get("status",""),
            r.get("room_id",""), r.get("room_name",""),
            r.get("date",""), r.get("start_time",""), r.get("end_time",""),
            r.get("purpose",""), r.get("participants",""),
            r.get("user_id",""), r.get("username",""), r.get("full_name",""),
            r.get("phone",""),
        ])
    wb.save(XLSX_PATH)

@app.get("/health")
def health():
    return "ok", 200

@app.get("/")
def page():
    return render_template("index.html")

@app.get("/api/bookings")
def api_list():
    return jsonify(list_bookings())

@app.post("/api/bookings")
def api_create():
    payload = request.get_json(force=True, silent=True) or {}
    for k in ("room_id","date","start_time","end_time","purpose"):
        if not payload.get(k):
            return jsonify({"error": f"missing_{k}"}), 400

    room_id = str(payload["room_id"])
    if room_id not in ROOMS:
        return jsonify({"error":"unknown_room"}), 400

    date_s = str(payload["date"])
    start_t = str(payload["start_time"])
    end_t = str(payload["end_time"])

    try:
        datetime.strptime(date_s, "%Y-%m-%d")
        if tmin(end_t) <= tmin(start_t):
            return jsonify({"error":"invalid_time_range"}), 400
    except Exception:
        return jsonify({"error":"invalid_date_time"}), 400

    if is_past(date_s, start_t):
        return jsonify({"error":"past_not_allowed"}), 400

    if has_conflict(room_id, date_s, start_t, end_t):
        return jsonify({"error":"conflict"}), 409

    con = db()
    cur = con.execute("""
      INSERT INTO bookings(created_at,user_id,username,full_name,phone,room_id,room_name,date,start_time,end_time,purpose,participants,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
      str(payload.get("user_id","0")),
      str(payload.get("username","")),
      str(payload.get("full_name","")),
      str(payload.get("phone","")),
      room_id,
      ROOMS[room_id],
      date_s, start_t, end_t,
      str(payload.get("purpose","")),
      str(payload.get("participants","")),
      "pending"
    ))
    bid = cur.lastrowid
    con.commit()
    con.close()

    export_excel()
    return jsonify(get_booking(bid) or {"id": bid}), 200

@app.post("/api/admin/check")
def api_admin_check():
    payload = request.get_json(force=True, silent=True) or {}
    ok = str(payload.get("password","")).strip() == ADMIN_PASSWORD
    return jsonify({"ok": ok}), (200 if ok else 403)

@app.post("/api/bookings/<int:bid>/status")
def api_status(bid: int):
    payload = request.get_json(force=True, silent=True) or {}
    if str(payload.get("admin_password","")).strip() != ADMIN_PASSWORD:
        return jsonify({"error":"bad_password"}), 403

    status = str(payload.get("status",""))
    if status not in ("approved","rejected","pending"):
        return jsonify({"error":"bad_status"}), 400

    b = get_booking(bid)
    if not b:
        return jsonify({"error":"not_found"}), 404

    if status == "approved":
        if has_conflict(b["room_id"], b["date"], b["start_time"], b["end_time"], ignore_id=bid):
            return jsonify({"error":"conflict"}), 409

    con = db()
    con.execute("UPDATE bookings SET status=? WHERE id=?", (status, int(bid)))
    con.commit()
    con.close()

    export_excel()
    return jsonify(get_booking(bid) or {"id": bid, "status": status}), 200

@app.get("/excel")
def excel():
    if not os.path.exists(XLSX_PATH):
        export_excel()
    return send_file(XLSX_PATH, as_attachment=True, download_name="bookings.xlsx")

def main():
    init_db()
    export_excel()
    port = int(os.getenv("PORT","8000"))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
