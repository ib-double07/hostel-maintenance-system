from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3, os, uuid, base64, re
from datetime import datetime

app = Flask(__name__, static_folder="static")
CORS(app)  # Allow requests from the student HTML page

# ── Database setup ─────────────────────────────────────────────────────────────

DB_PATH = "maintenance.db"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db():
    """Open a database connection with row factory (returns dict-like rows)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist yet."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_number  TEXT    NOT NULL UNIQUE,
                hostel      TEXT    NOT NULL,
                room        TEXT,
                student     TEXT    NOT NULL,
                category    TEXT    NOT NULL,   -- electrical | plumbing | furniture
                description TEXT    NOT NULL,
                photo_path  TEXT,
                status      TEXT    DEFAULT 'pending',  -- pending | in_progress | resolved
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            )
        """)
        conn.commit()

init_db()


# ── Helper ─────────────────────────────────────────────────────────────────────

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def generate_ref():
    return "MCR-" + str(uuid.uuid4())[:8].upper()

def save_photo(data_url: str, ref: str) -> str | None:
    """
    Accept a base64 data URL from the student page,
    decode it, save it as a file, and return the path.
    """
    match = re.match(r"data:(image/\w+);base64,(.*)", data_url, re.DOTALL)
    if not match:
        return None
    ext = match.group(1).split("/")[1]   # e.g. "jpeg" or "png"
    raw  = base64.b64decode(match.group(2))
    filename = f"{ref}.{ext}"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "wb") as f:
        f.write(raw)
    return filename


# ── Routes ─────────────────────────────────────────────────────────────────────

# Serve the student HTML page directly (optional — useful in development)
@app.route("/")
def index():
    return send_from_directory("static", "student_complaint.html")

# Admin dashboard
@app.route("/admin")
def admin():
    return send_from_directory("static", "admin_dashboard.html")

# Serve uploaded photos
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ── 1. Submit a new complaint (called by student page) ─────────────────────────

@app.route("/api/complaints", methods=["POST"])
def submit_complaint():
    data = request.get_json(force=True)

    # Validate required fields
    required = ["hostel", "student", "category", "description"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    valid_categories = {"electrical", "plumbing", "furniture"}
    if data["category"] not in valid_categories:
        return jsonify({"error": "Invalid category"}), 400

    ref    = generate_ref()
    ts     = now()
    photo  = save_photo(data["photo"], ref) if data.get("photo") else None

    with get_db() as conn:
        conn.execute("""
            INSERT INTO complaints
              (ref_number, hostel, room, student, category, description, photo_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref,
            data["hostel"],
            data.get("room", ""),
            data["student"],
            data["category"],
            data["description"],
            photo,
            ts, ts
        ))
        conn.commit()

    # ── Notify admin (optional: email / push / etc.) ─────────────────────────
    # Uncomment and configure to send an email notification:
    #
    # send_admin_email(
    #     subject = f"New complaint [{ref}] – {data['category']}",
    #     body    = f"From: {data['student']}\nHostel: {data['hostel']}\n\n{data['description']}"
    # )
    # ─────────────────────────────────────────────────────────────────────────

    return jsonify({
        "success": True,
        "ref_number": ref,
        "message": "Complaint submitted successfully."
    }), 201


# ── 2. Admin: list all complaints ──────────────────────────────────────────────

@app.route("/api/admin/complaints", methods=["GET"])
def list_complaints():
    status   = request.args.get("status")   # filter by status if provided
    category = request.args.get("category") # filter by category if provided

    query  = "SELECT * FROM complaints WHERE 1=1"
    params = []

    if status:
        query  += " AND status = ?"
        params.append(status)
    if category:
        query  += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


# ── 3. Admin: get single complaint ─────────────────────────────────────────────

@app.route("/api/admin/complaints/<ref>", methods=["GET"])
def get_complaint(ref):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM complaints WHERE ref_number = ?", (ref,)
        ).fetchone()

    if not row:
        return jsonify({"error": "Complaint not found"}), 404

    return jsonify(dict(row))


# ── 4. Admin: update status ────────────────────────────────────────────────────

@app.route("/api/admin/complaints/<ref>/status", methods=["PATCH"])
def update_status(ref):
    data   = request.get_json(force=True)
    status = data.get("status")

    valid_statuses = {"pending", "in_progress", "resolved"}
    if status not in valid_statuses:
        return jsonify({"error": "Invalid status"}), 400

    with get_db() as conn:
        result = conn.execute("""
            UPDATE complaints SET status = ?, updated_at = ?
            WHERE ref_number = ?
        """, (status, now(), ref))
        conn.commit()

    if result.rowcount == 0:
        return jsonify({"error": "Complaint not found"}), 404

    return jsonify({"success": True, "ref_number": ref, "status": status})


# ── 5. Stats for admin dashboard ───────────────────────────────────────────────

@app.route("/api/admin/stats", methods=["GET"])
def get_stats():
    with get_db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
        pending  = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='pending'").fetchone()[0]
        in_prog  = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='in_progress'").fetchone()[0]
        resolved = conn.execute("SELECT COUNT(*) FROM complaints WHERE status='resolved'").fetchone()[0]

        by_cat   = conn.execute("""
            SELECT category, COUNT(*) as count
            FROM complaints GROUP BY category
        """).fetchall()

    return jsonify({
        "total": total,
        "pending": pending,
        "in_progress": in_prog,
        "resolved": resolved,
        "by_category": [dict(r) for r in by_cat]
    })


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=True, host="0.0.0.0", port=port)