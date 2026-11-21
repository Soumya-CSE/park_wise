"""Business logic for parking operations."""
import re
import secrets
from datetime import datetime, timedelta

from flask import current_app

from billing import calculate_fee, duration_text
from db import get_db, now_iso, parse_ts

PLATE_RE = re.compile(r"^[A-Z0-9]{4,12}$")
VEHICLE_TYPES = ("bike", "car", "truck")


class ParkingError(Exception):
    """Raised for expected, user-facing problems."""


def normalize_plate(plate):
    plate = re.sub(r"[\s-]", "", (plate or "").upper())
    if not PLATE_RE.match(plate):
        raise ParkingError("Invalid plate number. Use 4-12 letters/digits (e.g. WB02AB1234).")
    return plate


def validate_vehicle_type(vtype):
    if vtype not in VEHICLE_TYPES:
        raise ParkingError("Vehicle type must be bike, car or truck.")
    return vtype


def log_action(username, action, detail="", ip=None):
    db = get_db()
    db.execute(
        "INSERT INTO audit_log (ts, username, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), username, action, detail, ip),
    )
    db.commit()


def expire_reservations():
    db = get_db()
    now = now_iso()
    rows = db.execute(
        "SELECT id, slot_id FROM reservations WHERE status='active' AND expires_at < ?", (now,)
    ).fetchall()
    for r in rows:
        db.execute("UPDATE reservations SET status='expired' WHERE id=?", (r["id"],))
        db.execute("UPDATE slots SET status='free' WHERE id=? AND status='reserved'", (r["slot_id"],))
    db.commit()


def find_open_session(plate):
    return get_db().execute(
        "SELECT * FROM sessions WHERE plate=? AND exit_time IS NULL", (plate,)
    ).fetchone()


def pick_slot(vehicle_type, preferred_floor=None):
    """Nearest free slot (lowest floor, then code) for the vehicle type."""
    db = get_db()
    q = "SELECT * FROM slots WHERE status='free' AND vehicle_type=?"
    args = [vehicle_type]
    if preferred_floor is not None:
        q += " AND floor=?"
        args.append(preferred_floor)
    q += " ORDER BY floor, code LIMIT 1"
    return db.execute(q, args).fetchone()


def vehicle_entry(plate, vehicle_type):
    expire_reservations()
    plate = normalize_plate(plate)
    validate_vehicle_type(vehicle_type)
    db = get_db()

    if find_open_session(plate):
        raise ParkingError(f"Vehicle {plate} is already parked.")

    # Honour an active reservation first.
    res = db.execute(
        "SELECT * FROM reservations WHERE plate=? AND status='active'", (plate,)
    ).fetchone()
    if res:
        slot = db.execute("SELECT * FROM slots WHERE id=?", (res["slot_id"],)).fetchone()
        if res["vehicle_type"] != vehicle_type:
            raise ParkingError("Vehicle type does not match the reservation.")
        db.execute("UPDATE reservations SET status='used' WHERE id=?", (res["id"],))
    else:
        slot = pick_slot(vehicle_type)
        if slot is None:
            raise ParkingError(f"No free {vehicle_type} slots available.")

    ticket = "T" + datetime.now().strftime("%y%m%d%H%M%S") + secrets.token_hex(2).upper()
    db.execute(
        "INSERT INTO sessions (ticket_no, plate, vehicle_type, slot_id, entry_time) VALUES (?,?,?,?,?)",
        (ticket, plate, vehicle_type, slot["id"], now_iso()),
    )
    db.execute("UPDATE slots SET status='occupied' WHERE id=?", (slot["id"],))
    db.commit()
    return {"ticket_no": ticket, "plate": plate, "slot": slot["code"], "floor": slot["floor"]}


def vehicle_exit(plate=None, ticket_no=None, lost_ticket=False, exit_time=None):
    db = get_db()
    if ticket_no:
        sess = db.execute(
            "SELECT * FROM sessions WHERE ticket_no=? AND exit_time IS NULL", (ticket_no.strip(),)
        ).fetchone()
    elif plate:
        sess = find_open_session(normalize_plate(plate))
    else:
        raise ParkingError("Provide a plate number or ticket number.")
    if sess is None:
        raise ParkingError("No active parking session found.")

    exit_dt = exit_time or datetime.now().replace(microsecond=0)
    entry_dt = parse_ts(sess["entry_time"])
    fee = calculate_fee(
        sess["vehicle_type"], entry_dt, exit_dt,
        current_app.config["RATES"],
        lost_ticket=lost_ticket,
        lost_penalty=current_app.config["LOST_TICKET_PENALTY"],
    )
    db.execute(
        "UPDATE sessions SET exit_time=?, fee=?, paid=1 WHERE id=?",
        (exit_dt.strftime("%Y-%m-%d %H:%M:%S"), fee, sess["id"]),
    )
    db.execute("UPDATE slots SET status='free' WHERE id=? AND status='occupied'", (sess["slot_id"],))
    db.commit()
    slot = db.execute("SELECT code FROM slots WHERE id=?", (sess["slot_id"],)).fetchone()
    return {
        "ticket_no": sess["ticket_no"],
        "plate": sess["plate"],
        "slot": slot["code"],
        "entry_time": sess["entry_time"],
        "exit_time": exit_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": duration_text(entry_dt, exit_dt),
        "fee": fee,
    }


def quote_fee(plate):
    """Fee if the vehicle left right now (no state change)."""
    sess = find_open_session(normalize_plate(plate))
    if sess is None:
        raise ParkingError("No active parking session found.")
    now = datetime.now().replace(microsecond=0)
    fee = calculate_fee(sess["vehicle_type"], parse_ts(sess["entry_time"]), now, current_app.config["RATES"])
    return {"plate": sess["plate"], "fee": fee, "duration": duration_text(parse_ts(sess["entry_time"]), now)}


def reserve_slot(plate, vehicle_type):
    expire_reservations()
    plate = normalize_plate(plate)
    validate_vehicle_type(vehicle_type)
    db = get_db()
    if find_open_session(plate):
        raise ParkingError("Vehicle is already parked.")
    if db.execute("SELECT 1 FROM reservations WHERE plate=? AND status='active'", (plate,)).fetchone():
        raise ParkingError("This plate already has an active reservation.")
    slot = pick_slot(vehicle_type)
    if slot is None:
        raise ParkingError(f"No free {vehicle_type} slots to reserve.")
    now = datetime.now().replace(microsecond=0)
    expires = now + timedelta(minutes=current_app.config["RESERVATION_MINUTES"])
    db.execute(
        "INSERT INTO reservations (plate, vehicle_type, slot_id, reserved_at, expires_at) VALUES (?,?,?,?,?)",
        (plate, vehicle_type, slot["id"], now.strftime("%Y-%m-%d %H:%M:%S"), expires.strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.execute("UPDATE slots SET status='reserved' WHERE id=?", (slot["id"],))
    db.commit()
    return {"plate": plate, "slot": slot["code"], "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


def cancel_reservation(res_id):
    db = get_db()
    res = db.execute("SELECT * FROM reservations WHERE id=? AND status='active'", (res_id,)).fetchone()
    if res is None:
        raise ParkingError("Active reservation not found.")
    db.execute("UPDATE reservations SET status='cancelled' WHERE id=?", (res_id,))
    db.execute("UPDATE slots SET status='free' WHERE id=? AND status='reserved'", (res["slot_id"],))
    db.commit()


def set_slot_maintenance(slot_id, on):
    db = get_db()
    slot = db.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
    if slot is None:
        raise ParkingError("Slot not found.")
    if on:
        if slot["status"] != "free":
            raise ParkingError("Only free slots can be put under maintenance.")
        db.execute("UPDATE slots SET status='maintenance' WHERE id=?", (slot_id,))
    else:
        if slot["status"] != "maintenance":
            raise ParkingError("Slot is not under maintenance.")
        db.execute("UPDATE slots SET status='free' WHERE id=?", (slot_id,))
    db.commit()
    return slot["code"]


def add_slot(code, floor, vehicle_type):
    validate_vehicle_type(vehicle_type)
    code = (code or "").strip().upper()
    if not re.match(r"^[A-Z0-9-]{2,10}$", code):
        raise ParkingError("Slot code must be 2-10 letters, digits or dashes.")
    db = get_db()
    if db.execute("SELECT 1 FROM slots WHERE code=?", (code,)).fetchone():
        raise ParkingError("Slot code already exists.")
    db.execute("INSERT INTO slots (code, floor, vehicle_type) VALUES (?,?,?)", (code, int(floor), vehicle_type))
    db.commit()
    return code


def occupancy_summary():
    db = get_db()
    rows = db.execute(
        "SELECT vehicle_type, status, COUNT(*) c FROM slots GROUP BY vehicle_type, status"
    ).fetchall()
    summary = {v: {"free": 0, "occupied": 0, "reserved": 0, "maintenance": 0, "total": 0} for v in VEHICLE_TYPES}
    for r in rows:
        summary[r["vehicle_type"]][r["status"]] = r["c"]
        summary[r["vehicle_type"]]["total"] += r["c"]
    return summary


def revenue_stats():
    db = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT COALESCE(SUM(fee),0) total, COUNT(*) n FROM sessions WHERE exit_time LIKE ?", (today + "%",)
    ).fetchone()
    week = db.execute(
        """SELECT substr(exit_time,1,10) d, COALESCE(SUM(fee),0) total, COUNT(*) n
           FROM sessions WHERE exit_time IS NOT NULL AND exit_time >= date('now','-6 days')
           GROUP BY d ORDER BY d"""
    ).fetchall()
    return {"today_total": row["total"], "today_count": row["n"], "week": [dict(w) for w in week]}
