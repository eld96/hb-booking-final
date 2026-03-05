from flask import Flask, request, jsonify, render_template
import sqlite3
from datetime import datetime

app = Flask(__name__)

DB="bookings.db"
ADMIN_PASSWORD="bank2024"


def db():
    return sqlite3.connect(DB)


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/bookings")
def bookings():

    con=db()
    cur=con.cursor()

    cur.execute("""
    SELECT id,room,booking_date,start_time,status,created_at
    FROM bookings
    """)

    rows=cur.fetchall()

    result=[]

    for r in rows:

        result.append({
            "id":r[0],
            "room":r[1],
            "booking_date":r[2],
            "start_time":r[3],
            "status":r[4],
            "created_at":r[5]
        })

    return jsonify(result)


@app.post("/api/book")
def book():

    data=request.json

    con=db()
    cur=con.cursor()

    cur.execute("""
    INSERT INTO bookings(room,booking_date,start_time,status,created_at)
    VALUES(?,?,?,?,?)
    """,(
        data["room"],
        data["date"],
        data["time"],
        "pending",
        datetime.now().isoformat()
    ))

    con.commit()

    return jsonify({"ok":True})


@app.post("/api/bookings/<int:id>/cancel")
def cancel_booking(id):

    con=db()
    cur=con.cursor()

    cur.execute("""
    UPDATE bookings
    SET status='cancelled'
    WHERE id=?
    """,(id,))

    con.commit()

    return jsonify({"ok":True})


@app.post("/api/admin/status")
def admin_status():

    data=request.json

    if data["password"]!=ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    con=db()
    cur=con.cursor()

    cur.execute("""
    UPDATE bookings
    SET status=?
    WHERE id=?
    """,(data["status"],data["id"]))

    con.commit()

    return jsonify({"ok":True})


@app.post("/api/admin/analytics")
def analytics():

    data=request.json

    if data["password"]!=ADMIN_PASSWORD:
        return jsonify({"error":"forbidden"}),403

    con=db()
    cur=con.cursor()

    cur.execute("SELECT status,booking_date FROM bookings")

    rows=cur.fetchall()

    total=len(rows)
    approved=len([r for r in rows if r[0]=="approved"])
    rejected=len([r for r in rows if r[0]=="rejected"])
    pending=len([r for r in rows if r[0]=="pending"])

    by_day={}

    for r in rows:
        d=r[1]
        by_day[d]=by_day.get(d,0)+1

    return jsonify({
        "total":total,
        "approved":approved,
        "rejected":rejected,
        "pending":pending,
        "by_day":by_day
    })


app.run(debug=True)
