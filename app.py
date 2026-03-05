import os
import csv
import io
from flask import Flask, request, jsonify, render_template, Response
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "bank2024")


def db():
    return psycopg2.connect(DATABASE_URL)


@app.route("/")
def index():
    return render_template("index.html")


# =========================
# BOOKINGS
# =========================

@app.get("/api/bookings")
def bookings():

    user_id = request.args.get("user_id")
    phone = request.args.get("phone")

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if user_id:
        cur.execute("SELECT * FROM bookings WHERE user_id=%s",(user_id,))
    elif phone:
        cur.execute("SELECT * FROM bookings WHERE phone=%s",(phone,))
    else:
        cur.execute("SELECT * FROM bookings")

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(rows)


# =========================
# CREATE BOOKING
# =========================

@app.post("/api/book")
def book():

    data = request.json

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO bookings
    (room, booking_date, start_time, phone, user_id, comment, status)
    VALUES (%s,%s,%s,%s,%s,%s,'pending')
    """,(
        data.get("room"),
        data.get("date"),
        data.get("time"),
        data.get("phone"),
        data.get("user_id"),
        data.get("comment")
    ))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"ok":True})


# =========================
# CANCEL
# =========================

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


# =========================
# ADMIN STATUS
# =========================

@app.post("/api/bookings/<int:bid>/status")
def admin_status(bid):

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE bookings
    SET status=%s
    WHERE id=%s
    """,(
        data.get("status"),
        bid
    ))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"ok":True})


# =========================
# ANALYTICS
# =========================

@app.post("/api/admin/analytics")
def analytics():

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    conn = db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM bookings")

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


# =========================
# CSV
# =========================

@app.post("/api/admin/export.csv")
def export():

    data = request.json

    if data.get("admin_password") != ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    conn=db()
    cur=conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM bookings")

    rows=cur.fetchall()

    output=io.StringIO()
    writer=csv.writer(output)

    writer.writerow(["id","room","date","time","status"])

    for r in rows:
        writer.writerow([
            r["id"],
            r["room"],
            r["booking_date"],
            r["start_time"],
            r["status"]
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":"attachment;filename=bookings.csv"
        }
    )


# =========================
# RUN
# =========================

if __name__ == "__main__":

    port=int(os.environ.get("PORT",10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
