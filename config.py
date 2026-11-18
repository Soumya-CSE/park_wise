import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
    DATABASE = os.environ.get("PARKING_DB", os.path.join(BASE_DIR, "parking.db"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Pricing (INR): first hour flat rate, then per additional hour. Daily cap applies per 24h.
    RATES = {
        "bike": {"first_hour": 10, "per_hour": 5, "daily_cap": 50},
        "car": {"first_hour": 30, "per_hour": 20, "daily_cap": 200},
        "truck": {"first_hour": 60, "per_hour": 40, "daily_cap": 400},
    }
    LOST_TICKET_PENALTY = 100
    RESERVATION_MINUTES = 30
    MAX_FAILED_LOGINS = 5
    LOCKOUT_MINUTES = 10    
