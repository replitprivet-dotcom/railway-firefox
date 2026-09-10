import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, render_template_string, request
from werkzeug.security import check_password_hash, generate_password_hash

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "users.db"
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_urlsafe(48)))

LOGIN_PAGE = """<!doctype html>
<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Firefox Gateway</title><style>body{font:16px system-ui;background:#f4f7fb;margin:0;padding:3rem;color:#172033}.card{max-width:620px;margin:auto;background:#fff;padding:2rem;border-radius:16px;box-shadow:0 8px 30px #17203318}h1{margin-top:0}code{background:#eef2f7;padding:.2rem .4rem;border-radius:5px}</style></head>
<body><main class='card'><h1>Firefox Gateway</h1><p>This Railway service exposes the protected LinuxServer Firefox web interface.</p><p>Use the administrator API key with <code>/adduser</code> to provision an endpoint.</p><p>Browser endpoints are private, random, and revocable.</p></main></body></html>"""


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""")
        conn.commit()


def valid_key(value):
    return bool(ADMIN_API_KEY) and bool(value) and hmac.compare_digest(value, ADMIN_API_KEY)


def base_url():
    return PUBLIC_BASE_URL or request.host_url.rstrip("/")


@app.before_request
def ensure_db():
    init_db()


@app.get("/")
def index():
    return render_template_string(LOGIN_PAGE)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/adduser")
def add_user():
    if not valid_key(request.args.get("key")):
        return jsonify(status="error", error="invalid admin key"), 401
    username = (request.args.get("username") or "").strip()
    password = request.args.get("password") or ""
    if not username or len(username) > 80 or not password or len(password) < 8:
        return jsonify(status="error", error="username is required and password must be at least 8 characters"), 400
    endpoint = secrets.token_urlsafe(18).replace("-", "").replace("_", "")
    try:
        with db() as conn:
            conn.execute("INSERT INTO users(username,password_hash,endpoint,created_at,active) VALUES(?,?,?,?,1)",
                         (username, generate_password_hash(password), endpoint, datetime.now(timezone.utc).isoformat()))
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify(status="error", error="username already exists"), 409
    url = f"{base_url()}/firefox/{endpoint}/"
    return jsonify(status="created", username=username, endpoint=f"/firefox/{endpoint}", url=url), 201


@app.get("/delete")
def delete_user():
    if not valid_key(request.args.get("key")):
        return jsonify(status="error", error="invalid admin key"), 401
    username = (request.args.get("username") or "").strip()
    password = request.args.get("password") or ""
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify(status="error", error="invalid username or password"), 401
        conn.execute("UPDATE users SET active=0 WHERE username=?", (username,))
        conn.commit()
    return jsonify(status="deleted", username=username)


@app.get("/internal/auth/<endpoint>")
def internal_auth(endpoint):
    """Nginx auth_request endpoint; never expose this route publicly."""
    with db() as conn:
        user = conn.execute("SELECT username FROM users WHERE endpoint=? AND active=1", (endpoint,)).fetchone()
    return ("", 204) if user else ("", 404)


@app.errorhandler(404)
def not_found(_):
    return jsonify(status="error", error="not found"), 404


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("API_PORT", "5000")))
