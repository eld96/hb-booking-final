import os
import csv
import io
import time
import secrets
from datetime import datetime
from flask import Flask, request, jsonify, render_template, Response
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "bank2024")

RATE_LIMIT = {}
REQUEST_WINDOW = 10
MAX_REQUESTS = 20


# ======================
# DATABASE
# ======================

def db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        room TEXT,
        booking_date DATE,
        start_time TEXT,
        status TEXT DEFAULT 'pending',
        phone TEXT,
        user_id TEXT,
        comment TEXT,
        reject_reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


# ======================
# RATE LIMIT
# ======================

def check_rate_limit(ip):

    now = time.time()

    if ip not in RATE_LIMIT:
        RATE_LIMIT[ip] = []

    RATE_LIMIT[ip] = [
        t for t in RATE_LIMIT[ip]
        if now - t < REQUEST_WINDOW
    ]

    if len(RATE_LIMIT[ip]) > MAX_REQUESTS:
        return False

    RATE_LIMIT[ip].append(now)
    return True


@app.before_request
def protect():

    ip = request.remote_addr

    if not check_rate_limit(ip):
        return jsonify({"error": "rate_limit"}), 429


# ======================
# FRONT
# ======================

@app.route("/")
def index():
    return render_template("index.html")


# ======================
# BOOKINGS
# ======================

@app.get("/api/bookings")
def bookings():

    user_id = request.args.get("user_id")
    phone = request.args.get("phone")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if user_id:
        cur.execute(
            "SELECT * FROM bookings WHERE user_id=%s ORDER BY created_at DESC",
            (user_id,)
        )
    elif phone:
        cur.execute(
            "SELECT * FROM bookings WHERE phone=%s ORDER BY created_at DESC",
            (phone,)
        )
    else:
        cur.execute(
            "SELECT * FROM bookings ORDER BY created_at DESC"
        )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(rows)


# ======================
# CREATE BOOKING
# ======================

@app.post("/api/book")
def book():

    data = request.json

    room = data.get("room")
    date = data.get("date")
    time_slot = data.get("time")

    conn = db()
    cur = conn.cursor()

    # защита от двойного бронирования
    cur.execute("""
        SELECT id FROM bookings
        WHERE room=%s
        AND booking_date=%s
        AND start_time=%s
        AND status IN ('pending','approved')
    """,(room,date,time_slot))

    if cur.fetchone():
        return jsonify({"error":"slot_taken"}),409

    cur.execute("""
        INSERT INTO bookings
        (room, booking_date, start_time, phone, user_id, comment, status)
        VALUES (%s,%s,%s,%s,%s,%s,'pending')
    """,(
        room,
        date,
        time_slot,
        data.get("phone"),
        data.get("user_id"),
        data.get("comment")
    ))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"ok":True})


# ======================
# CANCEL
# ======================

@app.post("/api/bookings/<int:bid>/cancel")
def cancel(bid):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE bookings
        SET status='cancelled'
        WHERE id=%s
    """,(bid,))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"ok":True})


# ======================
# ADMIN STATUS
# ======================

@app.post("/api/bookings/<int:bid>/status")
def admin_status(bid):

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE bookings
        SET status=%s, reject_reason=%s
        WHERE id=%s
    """,(
        data.get("status"),
        data.get("reason"),
        bid
    ))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"ok":True})


# ======================
# ANALYTICS
# ======================

@app.post("/api/admin/analytics")
def analytics():

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    start = data.get("start_date")
    end = data.get("end_date")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT * FROM bookings
        WHERE booking_date BETWEEN %s AND %s
    """,(start,end))

    rows = cur.fetchall()

    total=len(rows)
    pending=len([r for r in rows if r["status"]=="pending"])
    approved=len([r for r in rows if r["status"]=="approved"])
    rejected=len([r for r in rows if r["status"]=="rejected"])

    by_day={}

    for r in rows:
        d=str(r["booking_date"])
        by_day[d]=by_day.get(d,0)+1

    return jsonify({
        "total":total,
        "pending":pending,
        "approved":approved,
        "rejected":rejected,
        "by_day":by_day
    })


# ======================
# CSV EXPORT
# ======================

@app.post("/api/admin/export.csv")
def export():

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM bookings")

    rows=cur.fetchall()

    cur.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "id",
        "room",
        "date",
        "time",
        "status",
        "phone",
        "comment",
        "created"
    ])

    for r in rows:
        writer.writerow([
            r["id"],
            r["room"],
            r["booking_date"],
            r["start_time"],
            r["status"],
            r["phone"],
            r["comment"],
            r["created_at"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":"attachment;filename=bookings.csv"
        }
    )


# ======================
# RUN
# ======================

if __name__ == "__main__":

    port = int(os.environ.get("PORT",10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
