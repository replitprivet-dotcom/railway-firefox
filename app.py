import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request
from cryptography.fernet import Fernet
from werkzeug.security import check_password_hash, generate_password_hash

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "users.db"
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
RAILWAY_API_TOKEN = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "")
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
RAILWAY_API = "https://backboard.railway.com/graphql/v2"
FIREFOX_IMAGE = "lscr.io/linuxserver/firefox:latest"
FERNET = Fernet(base64.urlsafe_b64encode(hashlib.sha256(os.environ.get("SECRET_KEY", "").encode()).digest()))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_urlsafe(48))
LOGIN_PAGE = """<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Firefox Gateway</title><style>body{font:16px system-ui;background:#f4f7fb;margin:0;padding:3rem;color:#172033}.card{max-width:620px;margin:auto;background:#fff;padding:2rem;border-radius:16px;box-shadow:0 8px 30px #17203318}h1{margin-top:0}</style></head><body><main class='card'><h1>Firefox Gateway</h1><p>Protected multi-user Firefox service.</p><p>Each provisioned user gets a separate Railway service, Firefox process, and persistent profile volume.</p></main></body></html>"""


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, endpoint TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            railway_service_id TEXT, backend_url TEXT, backend_auth TEXT
        )""")
        # Upgrade databases created by the earlier gateway version.
        for column in ("railway_service_id TEXT", "backend_url TEXT", "backend_auth TEXT"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def valid_key(value):
    return bool(ADMIN_API_KEY) and bool(value) and hmac.compare_digest(value, ADMIN_API_KEY)


def api_call(query, variables):
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(RAILWAY_API, data=payload, headers={"Authorization": f"Bearer {RAILWAY_API_TOKEN}", "Content-Type": "application/json", "User-Agent": "curl/8.5.0 railway-firefox-gateway"})
    with urllib.request.urlopen(req, timeout=45) as response:
        data = json.loads(response.read())
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "Railway API error"))
    return data["data"]


def provision_firefox(username):
    if not (RAILWAY_API_TOKEN and RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID):
        raise RuntimeError("Railway provisioning variables are not configured")
    safe_name = "firefox-" + "".join(c if c.isalnum() else "-" for c in username.lower())[:35] + "-" + secrets.token_hex(4)
    child_password = secrets.token_urlsafe(32)
    service = api_call("""mutation($input: ServiceCreateInput!){serviceCreate(input:$input){id name}}""", {"input": {
        "projectId": RAILWAY_PROJECT_ID, "environmentId": RAILWAY_ENVIRONMENT_ID, "name": safe_name,
        "source": {"image": FIREFOX_IMAGE},
        "variables": {"PUID": "0", "PGID": "0", "TZ": os.environ.get("TZ", "Asia/Kolkata"), "CUSTOM_USER": username, "PASSWORD": child_password}
    }})["serviceCreate"]
    service_id = service["id"]
    api_call("""mutation($input: VolumeCreateInput!){volumeCreate(input:$input){id}}""", {"input": {
        "projectId": RAILWAY_PROJECT_ID, "environmentId": RAILWAY_ENVIRONMENT_ID,
        "serviceId": service_id, "mountPath": "/config"
    }})
    domain = api_call("""mutation($input: ServiceDomainCreateInput!){serviceDomainCreate(input:$input){domain}}""", {"input": {
        "serviceId": service_id, "environmentId": RAILWAY_ENVIRONMENT_ID, "targetPort": 3000
    }})["serviceDomainCreate"]["domain"]
    basic = "Basic " + base64.b64encode(f"{username}:{child_password}".encode()).decode()
    return service_id, domain, FERNET.encrypt(basic.encode()).decode()


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
    username, password = (request.args.get("username") or "").strip(), request.args.get("password") or ""
    if not username or len(username) > 80 or not password or len(password) < 8:
        return jsonify(status="error", error="username is required and password must be at least 8 characters"), 400
    endpoint = secrets.token_urlsafe(18).replace("-", "").replace("_", "")
    try:
        service_id, backend, backend_auth = provision_firefox(username)
        with db() as conn:
            old = conn.execute("SELECT active FROM users WHERE username=?", (username,)).fetchone()
            if old and old["active"]:
                raise ValueError("username already exists")
            if old:
                conn.execute("UPDATE users SET password_hash=?, endpoint=?, created_at=?, active=1, railway_service_id=?, backend_url=?, backend_auth=? WHERE username=?",
                             (generate_password_hash(password), endpoint, datetime.now(timezone.utc).isoformat(), service_id, backend, backend_auth, username))
            else:
                conn.execute("INSERT INTO users(username,password_hash,endpoint,created_at,active,railway_service_id,backend_url,backend_auth) VALUES(?,?,?,?,1,?,?,?)",
                             (username, generate_password_hash(password), endpoint, datetime.now(timezone.utc).isoformat(), service_id, backend, backend_auth))
            conn.commit()
    except (sqlite3.IntegrityError, ValueError):
        return jsonify(status="error", error="username already exists"), 409
    except Exception as exc:
        return jsonify(status="error", error=f"could not provision Firefox service: {exc}"), 502
    url = f"{PUBLIC_BASE_URL or request.host_url.rstrip('/')}/firefox/{endpoint}/"
    return jsonify(status="created", username=username, endpoint=f"/firefox/{endpoint}", url=url, isolated_service=service_id), 201


@app.get("/delete")
def delete_user():
    if not valid_key(request.args.get("key")):
        return jsonify(status="error", error="invalid admin key"), 401
    username, password = (request.args.get("username") or "").strip(), request.args.get("password") or ""
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify(status="error", error="invalid username or password"), 401
        if user["railway_service_id"] and RAILWAY_API_TOKEN:
            try:
                api_call("""mutation($id:String!){serviceDelete(id:$id)}""", {"id": user["railway_service_id"]})
            except Exception as exc:
                return jsonify(status="error", error=f"Railway service deletion failed: {exc}"), 502
        conn.execute("UPDATE users SET active=0 WHERE username=?", (username,))
        conn.commit()
    return jsonify(status="deleted", username=username)


@app.get("/internal/auth/<endpoint>")
def internal_auth(endpoint):
    with db() as conn:
        user = conn.execute("SELECT backend_url, backend_auth FROM users WHERE endpoint=? AND active=1", (endpoint,)).fetchone()
    if not user or not user["backend_url"]:
        return ("", 404)
    response = app.response_class("", status=204)
    response.headers["X-Backend"] = user["backend_url"]
    response.headers["X-Backend-Auth"] = FERNET.decrypt(user["backend_auth"].encode()).decode()
    return response


@app.get("/internal/launch/<endpoint>")
def internal_launch(endpoint):
    with db() as conn:
        user = conn.execute("SELECT backend_url, backend_auth FROM users WHERE endpoint=? AND active=1", (endpoint,)).fetchone()
    if not user:
        return jsonify(status="error", error="endpoint not found"), 404
    basic = FERNET.decrypt(user["backend_auth"].encode()).decode()
    raw = base64.b64decode(basic.removeprefix("Basic ")).decode()
    child_user, child_password = raw.split(":", 1)
    target = f"https://{child_user}:{child_password}@{user['backend_url']}/"
    return redirect(target, code=302)


@app.errorhandler(404)
def not_found(_):
    return jsonify(status="error", error="not found"), 404


init_db()
