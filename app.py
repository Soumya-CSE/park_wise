import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import db as database
import services as svc
from config import Config
from db import get_db, now_iso, parse_ts
from services import ParkingError


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    database.init_app(app)

    with app.app_context():
        database.init_db()

    register_security(app)
    register_routes(app)
    register_api(app)
    return app


# ------------------------------------------------------------- security
def register_security(app):
    @app.before_request
    def csrf_protect():
        if app.config.get("TESTING") and not app.config.get("ENFORCE_CSRF"):
            return
        if request.path.startswith("/api/"):
            return  # API uses session auth + JSON; same-site cookie blocks cross-site posts
        if request.method == "POST":
            token = session.get("csrf_token")
            if not token or token != request.form.get("csrf_token"):
                abort(400, "Invalid CSRF token")

    @app.context_processor
    def inject_globals():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf_token"], "current_user": session.get("user"),
                "current_role": session.get("role")}

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'"
        return resp

    @app.errorhandler(ParkingError)
    def parking_error(e):
        if request.path.startswith("/api/"):
            return jsonify(error=str(e)), 400
        flash(str(e), "error")
        return redirect(request.referrer or url_for("dashboard"))


def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "user" not in session:
                if request.path.startswith("/api/"):
                    return jsonify(error="Authentication required"), 401
                return redirect(url_for("login", next=request.path))
            if role and session.get("role") != role:
                if request.path.startswith("/api/"):
                    return jsonify(error="Forbidden"), 403
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def audit(action, detail=""):
    svc.log_action(session.get("user"), action, detail, request.remote_addr)


# ------------------------------------------------------------ web routes
def register_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            now = datetime.now()

            if user and user["locked_until"] and parse_ts(user["locked_until"]) > now:
                svc.log_action(username, "LOGIN_LOCKED", "Attempt while locked", request.remote_addr)
                flash("Account temporarily locked. Try again later.", "error")
            elif user and check_password_hash(user["password_hash"], password):
                db.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (user["id"],))
                db.commit()
                session.clear()
                session["user"], session["role"] = user["username"], user["role"]
                session["csrf_token"] = secrets.token_hex(16)
                svc.log_action(username, "LOGIN_OK", "", request.remote_addr)
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            else:
                if user:
                    attempts = user["failed_attempts"] + 1
                    locked = None
                    if attempts >= app.config["MAX_FAILED_LOGINS"]:
                        locked = (now + timedelta(minutes=app.config["LOCKOUT_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
                        attempts = 0
                    db.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                               (attempts, locked, user["id"]))
                    db.commit()
                svc.log_action(username, "LOGIN_FAIL", "Bad credentials", request.remote_addr)
                flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        audit("LOGOUT")
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required()
    def dashboard():
        svc.expire_reservations()
        db = get_db()
        slots = db.execute("SELECT * FROM slots ORDER BY floor, code").fetchall()
        parked = db.execute(
            """SELECT s.*, sl.code slot_code FROM sessions s JOIN slots sl ON sl.id=s.slot_id
               WHERE s.exit_time IS NULL ORDER BY s.entry_time DESC"""
        ).fetchall()
        return render_template("dashboard.html", summary=svc.occupancy_summary(), slots=slots,
                               parked=parked, revenue=svc.revenue_stats())

    @app.route("/entry", methods=["GET", "POST"])
    @login_required()
    def entry():
        result = None
        if request.method == "POST":
            result = svc.vehicle_entry(request.form.get("plate"), request.form.get("vehicle_type"))
            audit("ENTRY", f"{result['plate']} -> {result['slot']}")
        return render_template("entry.html", result=result, summary=svc.occupancy_summary())

    @app.route("/exit", methods=["GET", "POST"])
    @login_required()
    def exit_():
        result, quote = None, None
        if request.method == "POST":
            action = request.form.get("action", "checkout")
            if action == "quote":
                quote = svc.quote_fee(request.form.get("plate"))
            else:
                result = svc.vehicle_exit(
                    plate=request.form.get("plate") or None,
                    ticket_no=request.form.get("ticket_no") or None,
                    lost_ticket=bool(request.form.get("lost_ticket")),
                )
                audit("EXIT", f"{result['plate']} fee={result['fee']}")
        return render_template("exit.html", result=result, quote=quote)

    @app.route("/reservations", methods=["GET", "POST"])
    @login_required()
    def reservations():
        svc.expire_reservations()
        if request.method == "POST":
            if request.form.get("cancel_id"):
                svc.cancel_reservation(int(request.form["cancel_id"]))
                audit("RESERVATION_CANCEL", request.form["cancel_id"])
                flash("Reservation cancelled.", "ok")
            else:
                r = svc.reserve_slot(request.form.get("plate"), request.form.get("vehicle_type"))
                audit("RESERVATION_CREATE", f"{r['plate']} -> {r['slot']}")
                flash(f"Slot {r['slot']} reserved for {r['plate']} until {r['expires_at']}.", "ok")
            return redirect(url_for("reservations"))
        rows = get_db().execute(
            """SELECT r.*, s.code slot_code FROM reservations r JOIN slots s ON s.id=r.slot_id
               ORDER BY r.id DESC LIMIT 50"""
        ).fetchall()
        return render_template("reservations.html", rows=rows)

    @app.route("/history")
    @login_required()
    def history():
        q = request.args.get("q", "").strip().upper()
        db = get_db()
        if q:
            rows = db.execute(
                """SELECT s.*, sl.code slot_code FROM sessions s JOIN slots sl ON sl.id=s.slot_id
                   WHERE s.plate LIKE ? ORDER BY s.id DESC LIMIT 200""", (f"%{q}%",)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT s.*, sl.code slot_code FROM sessions s JOIN slots sl ON sl.id=s.slot_id
                   ORDER BY s.id DESC LIMIT 200"""
            ).fetchall()
        return render_template("history.html", rows=rows, q=q)

    @app.route("/admin/slots", methods=["GET", "POST"])
    @login_required("admin")
    def admin_slots():
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                code = svc.add_slot(request.form.get("code"), request.form.get("floor", 1),
                                    request.form.get("vehicle_type"))
                audit("SLOT_ADD", code)
                flash(f"Slot {code} added.", "ok")
            elif action in ("maintenance_on", "maintenance_off"):
                code = svc.set_slot_maintenance(int(request.form["slot_id"]), action == "maintenance_on")
                audit("SLOT_MAINTENANCE", f"{code} {action}")
                flash(f"Slot {code} updated.", "ok")
            return redirect(url_for("admin_slots"))
        slots = get_db().execute("SELECT * FROM slots ORDER BY floor, code").fetchall()
        return render_template("admin_slots.html", slots=slots)

    @app.route("/admin/audit")
    @login_required("admin")
    def admin_audit():
        rows = get_db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()
        return render_template("audit.html", rows=rows)


# ---------------------------------------------------------- JSON API
def register_api(app):
    @app.get("/api/status")
    @login_required()
    def api_status():
        svc.expire_reservations()
        return jsonify(occupancy=svc.occupancy_summary(), revenue=svc.revenue_stats())

    @app.get("/api/slots")
    @login_required()
    def api_slots():
        rows = get_db().execute("SELECT id, code, floor, vehicle_type, status FROM slots ORDER BY floor, code").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/entry")
    @login_required()
    def api_entry():
        data = request.get_json(silent=True) or {}
        res = svc.vehicle_entry(data.get("plate"), data.get("vehicle_type"))
        audit("ENTRY", f"{res['plate']} -> {res['slot']} (api)")
        return jsonify(res), 201

    @app.post("/api/exit")
    @login_required()
    def api_exit():
        data = request.get_json(silent=True) or {}
        res = svc.vehicle_exit(plate=data.get("plate"), ticket_no=data.get("ticket_no"),
                               lost_ticket=bool(data.get("lost_ticket")))
        audit("EXIT", f"{res['plate']} fee={res['fee']} (api)")
        return jsonify(res)

    @app.get("/api/quote/<plate>")
    @login_required()
    def api_quote(plate):
        return jsonify(svc.quote_fee(plate))


if __name__ == "__main__":
    create_app().run(debug=False, host="127.0.0.1", port=5000)

 