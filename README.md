# 🅿️ Smart Parking Management System

A full-stack parking lot management web app built with **Flask + SQLite**.
Handles slot allocation, vehicle entry/exit, automatic billing, reservations,
role-based access, and an audit trail.

## Features

| Area | What it does |
|---|---|
| **Slot management** | 38 default slots (bike / car / truck, multi-floor). Admins can add slots and put free slots into maintenance. |
| **Smart allocation** | Nearest free slot (lowest floor, then code) for the vehicle type. |
| **Entry / exit** | Plate validation & normalisation, ticket numbers, duplicate-entry protection, printable ticket and receipt. |
| **Billing** | First-hour flat rate + per started hour, 24h daily cap, 10-min free grace, lost-ticket penalty, live fee quote. |
| **Reservations** | Hold a slot for 30 min; auto-expiry; reserved vehicle gets its reserved slot; cancel anytime. |
| **Dashboard** | Live occupancy per type, colour-coded slot map, parked vehicles, revenue today / last 7 days. |
| **History** | Searchable session history by plate. |
| **Security** | Hashed passwords, login lockout (5 fails → 10 min), CSRF tokens, role-based access (admin / attendant), open-redirect protection, security headers + CSP, parameterised SQL, auto-escaped templates. |
| **Audit log** | Every login, entry, exit, reservation and slot change is recorded with user and IP (admin only). |
| **REST API** | JSON endpoints for entry, exit, quote, slots and status. |

## Quick start

```bash
cd smart_parking
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>.

Default accounts (created on first run):

| User | Password | Role |
|---|---|---|
| `admin` | `Admin@12345` | admin |
| `attendant` | `Attend@12345` | attendant |

**Change these before any real use** by setting environment variables *before the first run*
(the database is seeded once):

```bash
export SECRET_KEY="a-long-random-string"
export ADMIN_PASSWORD="your-strong-password"
export ATTENDANT_PASSWORD="another-strong-password"
```

## Tests

```bash
python -m pytest -q
```

22 tests cover billing rules, auth/lockout, role checks, CSRF, open-redirect, entry/exit flow,
lot-full, reservations, maintenance, audit log and security headers.

## Configuration (`config.py`)

- `RATES` – per vehicle type: `first_hour`, `per_hour`, `daily_cap` (INR)
- `LOST_TICKET_PENALTY`, `RESERVATION_MINUTES`, `MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES`
- `PARKING_DB` env var – path to the SQLite file

## REST API

All endpoints need a logged-in session (cookie). Errors return `{"error": "..."}` with HTTP 400/401/403.

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/status` | – | Occupancy summary + revenue |
| GET | `/api/slots` | – | All slots and status |
| POST | `/api/entry` | `{"plate","vehicle_type"}` | Park a vehicle, returns ticket + slot (201) |
| POST | `/api/exit` | `{"plate"}` or `{"ticket_no"}`, optional `"lost_ticket": true` | Check out, returns receipt |
| GET | `/api/quote/<plate>` | – | Current fee estimate |

## Deployment

```bash
pip install gunicorn
gunicorn -b 0.0.0.0:8000 -w 1 wsgi:app
```

or with Docker:

```bash
docker build -t smart-parking .
docker run -p 8000:8000 -v parking-data:/data \
  -e SECRET_KEY=change-me -e ADMIN_PASSWORD=change-me -e ATTENDANT_PASSWORD=change-me smart-parking
```

Use a single worker (SQLite) or move to PostgreSQL for multi-worker scaling, and serve over HTTPS
(set `SESSION_COOKIE_SECURE = True` in `config.py`).

## Project structure

```
smart_parking/
├── app.py            # Flask app factory, routes, API, security
├── services.py       # Parking business logic
├── billing.py        # Pure fee-calculation functions
├── db.py             # SQLite connection, init, seed data
├── config.py         # Rates and settings
├── schema.sql        # Database schema
├── wsgi.py           # Production entry point
├── templates/        # Jinja2 pages
├── static/           # CSS + JS
└── tests/            # pytest suite
```

## Billing example (car)

| Stay | Fee |
|---|---|
| 8 min | ₹0 (grace) |
| 45 min | ₹30 |
| 2h 10m | ₹30 + 2×₹20 = ₹70 |
| 9h+ | ₹200 (daily cap) |
| lost ticket | fee + ₹100 |

## Possible extensions

Payment gateway (UPI), ANPR camera integration, email/SMS reservation reminders, PostgreSQL, CSV export of reports.
