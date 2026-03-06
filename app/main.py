from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import sqlite3, os

app = FastAPI()
DB_PATH = "/data/bjj.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            belt TEXT NOT NULL,
            stripes INTEGER NOT NULL DEFAULT 0,
            duration_minutes INTEGER DEFAULT 60,
            notes TEXT,
            instructor_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instructors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rolls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            partner_belt TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    # migrate: add columns if they don't exist yet (for existing DBs)
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN instructor_id INTEGER")
    except: pass
    conn.commit()
    conn.close()

init_db()

class Roll(BaseModel):
    partner_belt: str

class Session(BaseModel):
    date: str
    belt: str
    stripes: int
    duration_minutes: Optional[int] = 60
    notes: Optional[str] = None
    instructor_id: Optional[int] = None
    rolls: Optional[List[Roll]] = []

class SessionUpdate(BaseModel):
    date: Optional[str] = None
    belt: Optional[str] = None
    stripes: Optional[int] = None
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None
    instructor_id: Optional[int] = None
    rolls: Optional[List[Roll]] = None

class Instructor(BaseModel):
    name: str

def session_with_rolls(conn, row):
    s = dict(row)
    rolls = conn.execute("SELECT * FROM rolls WHERE session_id=?", (s["id"],)).fetchall()
    s["rolls"] = [dict(r) for r in rolls]
    return s

# ── Instructors ──────────────────────────────────────────────
@app.get("/api/instructors")
def get_instructors():
    conn = get_db()
    rows = conn.execute("SELECT * FROM instructors ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/instructors")
def add_instructor(i: Instructor):
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO instructors (name) VALUES (?)", (i.name.strip(),))
        conn.commit()
        row = conn.execute("SELECT * FROM instructors WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
        return dict(row)
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Instructor already exists")

@app.delete("/api/instructors/{iid}")
def delete_instructor(iid: int):
    conn = get_db()
    conn.execute("DELETE FROM instructors WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── Sessions ─────────────────────────────────────────────────
@app.get("/api/sessions")
def get_sessions():
    conn = get_db()
    rows = conn.execute("SELECT * FROM sessions ORDER BY date DESC").fetchall()
    result = [session_with_rolls(conn, r) for r in rows]
    conn.close()
    return result

@app.post("/api/sessions")
def add_session(s: Session):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO sessions (date,belt,stripes,duration_minutes,notes,instructor_id) VALUES (?,?,?,?,?,?)",
        (s.date, s.belt, s.stripes, s.duration_minutes or 60, s.notes, s.instructor_id)
    )
    sid = cur.lastrowid
    for r in (s.rolls or []):
        conn.execute("INSERT INTO rolls (session_id, partner_belt) VALUES (?,?)", (sid, r.partner_belt))
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    result = session_with_rolls(conn, row)
    conn.close()
    return result

@app.put("/api/sessions/{sid}")
def update_session(sid: int, s: SessionUpdate):
    conn = get_db()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    u = dict(row)
    if s.date is not None: u["date"] = s.date
    if s.belt is not None: u["belt"] = s.belt
    if s.stripes is not None: u["stripes"] = s.stripes
    if s.duration_minutes is not None: u["duration_minutes"] = s.duration_minutes
    if s.notes is not None: u["notes"] = s.notes
    if s.instructor_id is not None: u["instructor_id"] = s.instructor_id
    conn.execute(
        "UPDATE sessions SET date=?,belt=?,stripes=?,duration_minutes=?,notes=?,instructor_id=? WHERE id=?",
        (u["date"],u["belt"],u["stripes"],u["duration_minutes"],u["notes"],u["instructor_id"],sid)
    )
    if s.rolls is not None:
        conn.execute("DELETE FROM rolls WHERE session_id=?", (sid,))
        for r in s.rolls:
            conn.execute("INSERT INTO rolls (session_id, partner_belt) VALUES (?,?)", (sid, r.partner_belt))
    conn.commit()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    result = session_with_rolls(conn, row)
    conn.close()
    return result

@app.delete("/api/sessions/{sid}")
def delete_session(sid: int):
    conn = get_db()
    conn.execute("DELETE FROM rolls WHERE session_id=?", (sid,))
    conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/stats")
def get_stats():
    conn = get_db()
    sessions = [dict(r) for r in conn.execute("SELECT * FROM sessions ORDER BY date ASC").fetchall()]
    rolls = [dict(r) for r in conn.execute("SELECT * FROM rolls").fetchall()]
    conn.close()

    belt_order = ["white","blue","purple","brown","black"]
    belt_counts = {b: 0 for b in belt_order}
    belt_first_date = {}
    monthly = {}

    for s in sessions:
        belt_counts[s["belt"]] = belt_counts.get(s["belt"], 0) + 1
        if s["belt"] not in belt_first_date:
            belt_first_date[s["belt"]] = s["date"]
        month = s["date"][:7]
        monthly[month] = monthly.get(month, 0) + 1

    current_belt = "white"; current_stripes = 0
    for b in belt_order:
        bs = [s for s in sessions if s["belt"] == b]
        if bs: current_belt = b; current_stripes = bs[-1]["stripes"]

    total_minutes = sum(s["duration_minutes"] or 60 for s in sessions)
    total_rolls = len(rolls)
    rolls_by_belt = {b: 0 for b in belt_order}
    rolls_by_belt['unknown'] = 0
    for r in rolls:
        rolls_by_belt[r["partner_belt"]] = rolls_by_belt.get(r["partner_belt"], 0) + 1

    return {
        "total_sessions": len(sessions),
        "total_minutes": total_minutes,
        "total_rolls": total_rolls,
        "current_belt": current_belt,
        "current_stripes": current_stripes,
        "belt_counts": belt_counts,
        "belt_first_dates": belt_first_date,
        "monthly_sessions": monthly,
        "belt_order": belt_order,
        "rolls_by_belt": rolls_by_belt,
    }

app.mount("/", StaticFiles(directory="/app/static", html=True), name="static")
