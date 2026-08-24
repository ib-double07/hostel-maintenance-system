import base64
import os
import re
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

import resend
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS


# -----------------------------------------------------------------------------
# App configuration
# -----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FOLDER = os.path.join(BASE_DIR, "static")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "maintenance.db")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_FOLDER)
CORS(app, supports_credentials=True)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key-before-deploying",
)

ADMIN_USERNAME = os.environ.get("Username", "admin")
ADMIN_PASSWORD = os.environ.get("Password", "changeme123")

# Resend configuration
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------

def login_required(view):
    """Protect an admin route and return JSON for unauthenticated API calls."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return send_from_directory(STATIC_FOLDER, "admin_login.html")
        return view(*args, **kwargs)

    return wrapped_view


# -----------------------------------------------------------------------------
# Email notification
# -----------------------------------------------------------------------------

def send_admin_notification(complaint):
    """
    Send an email to the admin when a complaint is submitted.

    Email errors are intentionally ignored after logging so a complaint
    is still saved even when email delivery fails.
    """
    if not RESEND_API_KEY:
        print("Email notification skipped: RESEND_API_KEY not set.")
        return

    if not ADMIN_EMAIL:
        print("Email notification skipped: ADMIN_EMAIL not set.")
        return

    subject = (
        f"New maintenance complaint — "
        f"{complaint['category'].title()} "
        f"({complaint['ref_number']})"
    )

    # Escape user-provided values before placing them in HTML.
    def esc(value):
        value = "" if value is None else str(value)
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6;">
        <h2>New Hostel Maintenance Complaint</h2>
        <p>A new maintenance complaint has been submitted.</p>
        <hr>
        <p><strong>Reference:</strong> {esc(complaint['ref_number'])}</p>
        <p><strong>Hostel:</strong> {esc(complaint['hostel'])}</p>
        <p><strong>Room:</strong> {esc(complaint['room'])}</p>
        <p><strong>Student ID:</strong> {esc(complaint['student'])}</p>
        <p><strong>Category:</strong> {esc(complaint['category'])}</p>
        <p><strong>Description:</strong></p>
        <p>{esc(complaint['description'])}</p>
        <hr>
        <p>
            Log in to the UMHM admin dashboard to view the full complaint
            and any attached photo.
        </p>
    </body>
    </html>
    """

    try:
        resend.api_key = RESEND_API_KEY
        response = resend.Emails.send(
            {
                "from": EMAIL_FROM,
                "to": ADMIN_EMAIL,
                "subject": subject,
                "html": html,
            }
        )
        print(f"Admin email notification sent successfully: {response}")
    except Exception as exc:
        print(f"Failed to send admin email notification: {exc}")


# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------

def get_db():
    """Open a SQLite connection that returns dictionary-like rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the complaints table if it does not already exist."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_number TEXT NOT NULL UNIQUE,
                hostel TEXT NOT NULL,
                room TEXT,
                student TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                photo_path TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def generate_ref():
    return "MCR-" + str(uuid.uuid4())[:8].upper()


def save_photo(data_url, ref):
    """
    Accept an image data URL, decode it, save it, and return the filename.
    Returns None for an invalid/missing data URL.
    """
    if not isinstance(data_url, str):
        return None

    match = re.match(r"^data:(image/(?:jpeg|jpg|png|gif|webp));base64,(.*)$",
                     data_url, re.DOTALL | re.IGNORECASE)
    if not match:
        return None

    mime_type = match.group(1).lower()
    encoded_data = match.group(2)

    extension = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }[mime_type]

    try:
        raw = base64.b64decode(encoded_data, validate=True)
    except (ValueError, TypeError):
        return None

    filename = f"{ref}.{extension}"
    path = os.path.join(UPLOAD_FOLDER, filename)

    with open(path, "wb") as file:
        file.write(raw)

    return filename


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_FOLDER, "student_complaint.html")


@app.route("/admin-login")
def admin_login_page():
    return send_from_directory(STATIC_FOLDER, "admin_login.html")


@app.route("/admin")
@login_required
def admin():
    return send_from_directory(STATIC_FOLDER, "admin_dashboard.html")


@app.route("/qr-codes")
@login_required
def qr_codes():
    return send_from_directory(STATIC_FOLDER, "qr_generator.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# -----------------------------------------------------------------------------
# Authentication API
# -----------------------------------------------------------------------------

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", ""))
    password = str(data.get("password", ""))

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session.clear()
        session["logged_in"] = True
        return jsonify({"success": True})

    return jsonify({"error": "Invalid username or password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


# -----------------------------------------------------------------------------
# Student API: submit complaint
# -----------------------------------------------------------------------------

@app.route("/api/complaints", methods=["POST"])
def submit_complaint():
    data = request.get_json(silent=True) or {}

    required = ["hostel", "student", "category", "description"]
    missing = [
        field for field in required
        if not str(data.get(field, "")).strip()
    ]

    if missing:
        return jsonify(
            {"error": f"Missing fields: {', '.join(missing)}"}
        ), 400

    category = str(data["category"]).strip().lower()
    valid_categories = {"electrical", "plumbing", "furniture"}

    if category not in valid_categories:
        return jsonify({"error": "Invalid category"}), 400

    ref = generate_ref()
    timestamp = now()

    photo = None
    if data.get("photo"):
        photo = save_photo(data["photo"], ref)

    hostel = str(data["hostel"]).strip()
    room = str(data.get("room", "")).strip()
    student = str(data["student"]).strip()
    description = str(data["description"]).strip()

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO complaints
                (
                    ref_number,
                    hostel,
                    room,
                    student,
                    category,
                    description,
                    photo_path,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref,
                    hostel,
                    room,
                    student,
                    category,
                    description,
                    photo,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(f"Database error while saving complaint: {exc}")
        return jsonify({"error": "Unable to save complaint"}), 500

    send_admin_notification(
        {
            "ref_number": ref,
            "hostel": hostel,
            "room": room,
            "student": student,
            "category": category,
            "description": description,
        }
    )

    return jsonify(
        {
            "success": True,
            "ref_number": ref,
            "message": "Complaint submitted successfully.",
        }
    ), 201


# -----------------------------------------------------------------------------
# Admin API: complaints
# -----------------------------------------------------------------------------

@app.route("/api/admin/complaints", methods=["GET"])
@login_required
def list_complaints():
    status = request.args.get("status")
    category = request.args.get("category")

    query = "SELECT * FROM complaints WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/complaints/<ref>", methods=["GET"])
@login_required
def get_complaint(ref):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM complaints WHERE ref_number = ?",
            (ref,),
        ).fetchone()

    if row is None:
        return jsonify({"error": "Complaint not found"}), 404

    return jsonify(dict(row))


@app.route("/api/admin/complaints/<ref>/status", methods=["PATCH"])
@login_required
def update_status(ref):
    data = request.get_json(silent=True) or {}
    status = data.get("status")

    valid_statuses = {"pending", "in_progress", "resolved"}
    if status not in valid_statuses:
        return jsonify({"error": "Invalid status"}), 400

    with get_db() as conn:
        result = conn.execute(
            """
            UPDATE complaints
            SET status = ?, updated_at = ?
            WHERE ref_number = ?
            """,
            (status, now(), ref),
        )
        conn.commit()

    if result.rowcount == 0:
        return jsonify({"error": "Complaint not found"}), 404

    return jsonify(
        {
            "success": True,
            "ref_number": ref,
            "status": status,
        }
    )


# -----------------------------------------------------------------------------
# Admin API: statistics
# -----------------------------------------------------------------------------

@app.route("/api/admin/stats", methods=["GET"])
@login_required
def get_stats():
    with get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM complaints"
        ).fetchone()[0]

        pending = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE status = 'pending'"
        ).fetchone()[0]

        in_progress = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE status = 'in_progress'"
        ).fetchone()[0]

        resolved = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE status = 'resolved'"
        ).fetchone()[0]

        by_category = conn.execute(
            """
            SELECT category, COUNT(*) AS count
            FROM complaints
            GROUP BY category
            """
        ).fetchall()

    return jsonify(
        {
            "total": total,
            "pending": pending,
            "in_progress": in_progress,
            "resolved": resolved,
            "by_category": [dict(row) for row in by_category],
        }
    )


# -----------------------------------------------------------------------------
# Local development
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
    )
