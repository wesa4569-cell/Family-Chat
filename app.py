import os
from urllib.parse import urlparse
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
    os.environ["DATABASE_URL"] = db_url
import re
import mimetypes
import json
from datetime import datetime, timezone, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, abort, make_response
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_, and_, func, case
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError

# Web Push (اختياري)
try:
    from pywebpush import webpush, WebPushException
except Exception:  # pragma: no cover
    webpush = None
    WebPushException = Exception

# Fallback VAPID key generation/storage.
# Web Push requires VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY.
# If they are not provided via environment variables, we generate them once
# and store them under instance/vapid_keys.json.
try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64
except Exception:  # pragma: no cover
    ec = None
    serialization = None
    base64 = None



# ----------------- App Factory-ish Setup -----------------
app = Flask(__name__)

# When running behind a reverse proxy that terminates TLS (nginx/caddy/traefik),
# enable ProxyFix so Flask can correctly detect HTTPS via X-Forwarded-* headers.
# Adjust counts if you have multiple proxies in front.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# When running behind a reverse proxy (nginx/caddy/cloudflare) that terminates HTTPS,
# this makes Flask aware of the original scheme/host (needed for secure cookies, redirects, etc.).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Security: use env var in production
app.secret_key = os.environ.get("SECRET_KEY") or "dev-secret-key-change-in-production"

# In production, you should set a strong SECRET_KEY via environment variables.


# DB
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///chat.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Uploads
app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "static/profile_pics")

app.config["MEDIA_IMAGE_FOLDER"] = os.environ.get("MEDIA_IMAGE_FOLDER", os.path.join("instance","uploads","images"))
app.config["MEDIA_AUDIO_FOLDER"] = os.environ.get("MEDIA_AUDIO_FOLDER", os.path.join("instance","uploads","audio"))
app.config["MEDIA_FILE_FOLDER"] = os.environ.get("MEDIA_FILE_FOLDER", os.path.join("instance","uploads","files"))
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB
app.config["ALLOWED_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif"}

# Session cookies (improve security; safe defaults for prod)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# إذا تستخدم HTTPS في الإنتاج فعّلها:
# app.config["SESSION_COOKIE_SECURE"] = True

db = SQLAlchemy(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=os.environ.get("SOCKETIO_ASYNC_MODE", "threading"),
)

online_users = {}

CONVERSATION_PREVIEW_LIMIT = 10
MESSAGE_PAGE_LIMIT = 50


# ----------------- Helpers -----------------
PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]

def validate_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(phone or ""))

def validate_password(password: str):
    if not password or len(password) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
    return True, ""

def login_required():
    if "user_id" not in session:
        return False
    return True

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


from typing import Optional, Union
from datetime import datetime

def _utc_ms(dt: Union[datetime, None]) -> int:
    """Milliseconds since epoch. Treat naive datetimes as UTC to avoid TZ drift."""
    if not dt:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _utc_iso(dt: Union[datetime, None]) -> Union[str, None]:
    ...

    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _serialize_message(msg, sender_name: Optional[str] = None) -> dict:
    if sender_name is None:
        sender = getattr(msg, "sender", None)
        if not sender:
            sender = db.session.get(User, getattr(msg, "sender_id", None))
        sender_name = sender.name if sender else ""
    return {
        "id": msg.id,
        "sender_id": getattr(msg, "sender_id", None),
        "receiver_id": getattr(msg, "receiver_id", None),
        "group_id": getattr(msg, "group_id", None),
        "sender_name": sender_name or "",
        "content": msg.content,
        "timestamp_iso": _utc_iso(msg.timestamp),
        "timestamp_ms": _utc_ms(msg.timestamp),
        "message_type": getattr(msg, "message_type", "text"),
        "media_url": getattr(msg, "media_url", None),
        "media_mime": getattr(msg, "media_mime", None),
    }


def _emit_direct_message(msg):
    payload = _serialize_message(msg)
    socketio.emit("new_message", {"type": "dm", "message": payload}, room=f"user_{msg.receiver_id}")
    socketio.emit("refresh_unread", {"type": "dm", "message_id": msg.id}, room=f"user_{msg.receiver_id}")


def _emit_group_message(msg):
    payload = _serialize_message(msg)
    socketio.emit("new_message", {"type": "group", "message": payload}, room=f"group_{msg.group_id}")
    socketio.emit("refresh_unread", {"type": "group", "group_id": msg.group_id}, room=f"group_{msg.group_id}")


def _mark_user_online(user_id: int):
    count = online_users.get(user_id, 0) + 1
    online_users[user_id] = count
    if count == 1:
        socketio.emit("user_status", {"user_id": user_id, "status": "online"})


def _mark_user_offline(user_id: int):
    count = online_users.get(user_id, 0) - 1
    if count <= 0:
        online_users.pop(user_id, None)
        socketio.emit("user_status", {"user_id": user_id, "status": "offline"})
    else:
        online_users[user_id] = count


# ----------------- Models -----------------
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=True)
    profile_pic = db.Column(db.String(100), default="default.png")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_seen = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def touch_last_seen(self):
        self.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default="text", index=True)
    media_url = db.Column(db.Text, nullable=True)
    media_mime = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    is_read = db.Column(db.Boolean, default=False)

    sender = db.relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_messages")

    __table_args__ = (
        db.Index("ix_messages_sender_receiver_ts", "sender_id", "receiver_id", "timestamp"),
        db.Index("ix_messages_receiver_is_read", "receiver_id", "is_read"),
    )

class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(255), nullable=False)
    user_agent = db.Column(db.String(255), default="")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (db.UniqueConstraint('user_id', 'endpoint', name='uq_push_user_endpoint'),)


# ----------------- Socket.IO Events -----------------
@socketio.on("connect")
def handle_socket_connect():
    user_id = session.get("user_id")
    if not user_id:
        return
    _mark_user_online(user_id)
    join_room(f"user_{user_id}")
    emit("presence_state", {"online_user_ids": list(online_users.keys())})


@socketio.on("disconnect")
def handle_socket_disconnect():
    user_id = session.get("user_id")
    if not user_id:
        return
    _mark_user_offline(user_id)


@socketio.on("join_groups")
def handle_join_groups(data):
    user_id = session.get("user_id")
    if not user_id:
        return
    group_ids = data.get("groups") if isinstance(data, dict) else []
    for gid in group_ids or []:
        try:
            gid_int = int(gid)
        except (TypeError, ValueError):
            continue
        join_room(f"group_{gid_int}")


@socketio.on("typing")
def handle_typing(data):
    user_id = session.get("user_id")
    if not user_id or not isinstance(data, dict):
        return
    group_id = data.get("group_id")
    receiver_id = data.get("receiver_id")
    is_typing = bool(data.get("is_typing"))
    payload = {
        "sender_id": user_id,
        "group_id": group_id,
        "receiver_id": receiver_id,
        "is_typing": is_typing,
    }
    if group_id:
        try:
            gid_int = int(group_id)
        except (TypeError, ValueError):
            return
        socketio.emit("typing", payload, room=f"group_{gid_int}", include_self=False)
    elif receiver_id:
        try:
            rid_int = int(receiver_id)
        except (TypeError, ValueError):
            return
        socketio.emit("typing", payload, room=f"user_{rid_int}", include_self=False)


# ----------------- Routes -----------------

# ----------------- Web Push Helpers -----------------
def _b64url(data: bytes) -> str:
    if not base64:
        return ""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _instance_vapid_path() -> str:
    # Store generated keys under instance/ so they survive restarts.
    return os.path.join(app.instance_path, "vapid_keys.json")


def ensure_vapid_keys() -> tuple[str, str]:
    """Return (public, private) VAPID keys.

    Priority:
      1) Environment variables VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY
      2) instance/vapid_keys.json (auto-generated if missing)

    If cryptography isn't available, returns empty strings.
    """
    pub = (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
    priv = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    if pub and priv:
        return pub, priv

    # Try load from instance/vapid_keys.json
    try:
        path = _instance_vapid_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f) or {}
            pub2 = (obj.get("public") or "").strip()
            priv2 = (obj.get("private") or "").strip()
            if pub2 and priv2:
                return pub2, priv2
    except Exception:
        pass

    # Generate and persist
    if not ec:
        return "", ""
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()

        # Private key: raw 32-byte value
        private_numbers = private_key.private_numbers()
        private_bytes = private_numbers.private_value.to_bytes(32, "big")

        # Public key: raw uncompressed point 65 bytes
        public_numbers = public_key.public_numbers()
        x = public_numbers.x.to_bytes(32, "big")
        y = public_numbers.y.to_bytes(32, "big")
        public_bytes = b"\x04" + x + y

        pub3 = _b64url(public_bytes)
        priv3 = _b64url(private_bytes)

        with open(_instance_vapid_path(), "w", encoding="utf-8") as f:
            json.dump({"public": pub3, "private": priv3}, f, ensure_ascii=False, indent=2)
        return pub3, priv3
    except Exception:
        return "", ""


def get_vapid_public_key():
    pub, _ = ensure_vapid_keys()
    return (pub or "").strip()


def get_vapid_private_key():
    _, priv = ensure_vapid_keys()
    return (priv or "").strip()

def send_push_to_user_detail(user_id: int, payload: dict):
    """Send Web Push notification to all active subscriptions for a user.

    Returns: (ok: bool, reason: str)
      reason can be:
        - missing_pywebpush
        - missing_vapid
        - no_subscription
        - send_failed
        - ok
    """
    if not webpush:
        return False, "missing_pywebpush"

    pub = get_vapid_public_key()
    priv = get_vapid_private_key()
    if not pub or not priv:
        return False, "missing_vapid"

    subs = PushSubscription.query.filter_by(user_id=user_id, is_active=True).all()
    if not subs:
        return False, "no_subscription"

    vapid_claims = {"sub": os.getenv("VAPID_CLAIMS_SUB", "mailto:admin@example.com")}
    ok_any = False
    failed_any = False

    for s in subs:
        try:
            subscription_info = {
                "endpoint": s.endpoint,
                "keys": {"p256dh": s.p256dh, "auth": s.auth}
            }
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims=vapid_claims
            )
            ok_any = True
        except WebPushException:
            failed_any = True
            try:
                s.is_active = False
                db.session.commit()
            except Exception:
                db.session.rollback()
        except Exception:
            failed_any = True

    if ok_any:
        return True, "ok"
    if failed_any:
        return False, "send_failed"
    return False, "send_failed"


def send_push_to_user(user_id: int, payload: dict) -> bool:
    ok, _reason = send_push_to_user_detail(user_id, payload)
    return bool(ok)


@app.route("/api/push/vapid_public_key", methods=["GET"])
def push_vapid_public_key():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401
    pub = get_vapid_public_key()
    if not pub:
        return jsonify({"error": "missing_vapid"}), 500
    return jsonify({"publicKey": pub})


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    sub = data.get("subscription") or {}
    endpoint = (sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()

    if not endpoint or not p256dh or not auth:
        return jsonify({"error": "bad_request"}), 400

    ua = (request.headers.get("User-Agent") or "")[:255]
    row = PushSubscription.query.filter_by(user_id=me.id, endpoint=endpoint).first()
    if not row:
        row = PushSubscription(user_id=me.id, endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent=ua, is_active=True)
        db.session.add(row)
    else:
        row.p256dh = p256dh
        row.auth = auth
        row.user_agent = ua
        row.is_active = True
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    if not login_required():
        return jsonify({"error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"error": "unauthorized"}), 401

    PushSubscription.query.filter_by(user_id=me.id).update({"is_active": False})
    db.session.commit()
    return {"ok": True}
    

@app.route("/api/push/test", methods=["POST"])
def api_push_test():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    title = data.get("title") or "اختبار إشعار"
    body = data.get("body") or "تم تفعيل إشعارات Push بنجاح ✅"
    url = data.get("url") or "/chat"

    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = {
        "title": title,
        "body": body,
        "url": url,
        "icon": "/static/logo.svg",
        "badge": "/static/logo.svg",
        "tag": "chat-push-test"
    }

    ok, reason = send_push_to_user_detail(me.id, payload)
    if not ok:
        return jsonify({"ok": False, "error": reason}), 400
    return jsonify({"ok": True}), 200

# ----------------- PWA / Service Worker -----------------
# IMPORTANT:
# Web Push requires the Service Worker to control the pages it should notify for.
# If the SW file lives under /static/, its default scope is limited to /static/.
# This route serves the same file at the site root and explicitly allows scope "/".
@app.route("/sw.js")
def sw_root():
    resp = make_response(send_from_directory("static", "sw.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/manifest.json")
def manifest_root():
    resp = make_response(send_from_directory("static", "manifest.json"))
    resp.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
    return resp


@app.route("/")
def home():
    # الصفحة الرئيسية تعرض المحادثات إذا كان المستخدم مسجلاً
    if login_required():
        return redirect(url_for("web_chat"))
    return render_template("home.html")



class Group(db.Model):
    __tablename__ = "groups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    owner = db.relationship("User", foreign_keys=[owner_id])

class GroupMember(db.Model):
    __tablename__ = "group_members"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), default="pending", index=True)  # pending / accepted / declined
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    invited_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    responded_at = db.Column(db.DateTime, nullable=True)
    last_read_at = db.Column(db.DateTime, nullable=True, index=True)

    group = db.relationship("Group", foreign_keys=[group_id])
    user = db.relationship("User", foreign_keys=[user_id])


class GroupBlock(db.Model):
    __tablename__ = "group_blocks"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    group = db.relationship("Group", foreign_keys=[group_id])
    user = db.relationship("User", foreign_keys=[user_id])


class GroupMessage(db.Model):
    __tablename__ = "group_messages"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False, default="")
    message_type = db.Column(db.String(20), default="text", index=True)
    media_url = db.Column(db.Text, nullable=True)
    media_mime = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    group = db.relationship("Group", foreign_keys=[group_id])
    sender = db.relationship("User", foreign_keys=[sender_id])

    __table_args__ = (
        db.Index("ix_group_messages_group_ts", "group_id", "timestamp"),
    )



@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.svg",
        mimetype="image/svg+xml"
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        phone = (request.form.get("phone") or "").strip()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not phone or not name or not password:
            return render_template("register.html", error="جميع الحقول مطلوبة")

        if password != confirm_password:
            return render_template("register.html", error="كلمات المرور غير متطابقة")

        if not validate_phone(phone):
            return render_template("register.html", error="رقم الهاتف غير صحيح")

        ok, msg = validate_password(password)
        if not ok:
            return render_template("register.html", error=msg)

        if len(name) < 2 or len(name) > 50:
            return render_template("register.html", error="الاسم يجب أن يكون بين 2 و 50 حرف")

        # Use exists check
        if User.query.filter_by(phone_number=phone).first():
            return render_template("register.html", error="رقم الهاتف مسجل مسبقاً")

        try:
            user = User(phone_number=phone, name=name, verified=True)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            existing_groups = Group.query.all()
            for g in existing_groups:
                db.session.add(
                    GroupMember(
                        group_id=g.id,
                        user_id=user.id,
                        status="accepted",
                        invited_by=g.owner_id,
                        invited_at=now,
                        responded_at=now,
                        last_read_at=now
                    )
                )
                db.session.add(
                    GroupMessage(
                        group_id=g.id,
                        sender_id=user.id,
                        content=f"{user.name} انضم إلى المجموعة",
                        message_type="text",
                        timestamp=now
                    )
                )

            db.session.commit()
            return redirect(url_for("login", registered=True))
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Registration error")
            return render_template("register.html", error="حدث خطأ أثناء التسجيل")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = (request.form.get("phone") or "").strip()
        password = request.form.get("password") or ""

        if not phone or not password:
            return render_template("login.html", error="جميع الحقول مطلوبة")

        user = User.query.filter_by(phone_number=phone).first()
        if not user or not user.check_password(password):
            return render_template("login.html", error="رقم الهاتف أو كلمة المرور غير صحيحة")

        session.clear()
        session["user_id"] = user.id
        session["phone_number"] = user.phone_number
        session["name"] = user.name

        try:
            user.touch_last_seen()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

        return redirect(url_for("web_chat"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "logged_out"})


@app.route("/chat")
def web_chat():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))

    # تحديث last_seen
    try:
        user.touch_last_seen()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()

    active_user = None
    active_group = None

    active_id_raw = (request.args.get("user") or "").strip()
    if active_id_raw:
        try:
            active_user = db.session.get(User, int(active_id_raw))
        except ValueError:
            active_user = None

    active_group_raw = (request.args.get("group") or "").strip()
    if active_group_raw:
        try:
            active_group = db.session.get(Group, int(active_group_raw))
        except ValueError:
            active_group = None

    # Groups (accepted) with latest message timestamp
    group_last_subq = (
        db.session.query(
            GroupMessage.group_id.label("group_id"),
            func.max(GroupMessage.timestamp).label("last_ts"),
        )
        .group_by(GroupMessage.group_id)
        .subquery()
    )

    group_rows = (
        db.session.query(Group, group_last_subq.c.last_ts)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .outerjoin(GroupBlock, and_(GroupBlock.group_id == Group.id, GroupBlock.user_id == user.id))
        .outerjoin(group_last_subq, group_last_subq.c.group_id == Group.id)
        .filter(
            GroupMember.user_id == user.id,
            GroupMember.status == "accepted",
            GroupBlock.id == None  # noqa: E711
        )
        .order_by(func.coalesce(group_last_subq.c.last_ts, Group.created_at).desc(), Group.id.desc())
        .all()
    )

    # إزالة تكرارات المجموعات (نفس الاسم لنفس المنشئ) - نعرض الأحدث فقط
    _seen = {}
    for g, last_ts in group_rows:
        key = (g.owner_id, (g.name or "").strip().lower())
        if key not in _seen or (g.id or 0) > (_seen[key][0].id or 0):
            _seen[key] = (g, last_ts)
    groups = list(_seen.values())

    # Direct message latest timestamps
    other_id = case(
        (Message.sender_id == user.id, Message.receiver_id),
        else_=Message.sender_id,
    )
    dm_last_subq = (
        db.session.query(other_id.label("other_id"), func.max(Message.timestamp).label("last_ts"))
        .filter(or_(Message.sender_id == user.id, Message.receiver_id == user.id))
        .group_by(other_id)
        .subquery()
    )

    dm_rows = (
        db.session.query(User, dm_last_subq.c.last_ts)
        .join(dm_last_subq, dm_last_subq.c.other_id == User.id)
        .filter(User.id != user.id)
        .order_by(dm_last_subq.c.last_ts.desc().nullslast(), User.id.desc())
        .all()
    )

    # Build conversations list (Users + Groups) then sort by latest
    conversations = []
    for g, last_ts in groups:
        conversations.append({"type": "group", "group": g, "ts": last_ts or g.created_at})
    for u, last_ts in dm_rows:
        conversations.append({"type": "user", "user": u, "ts": last_ts})

    # Ensure active conversation is present even if outside the preview list
    if active_group and not any(c.get("type") == "group" and c.get("group").id == active_group.id for c in conversations):
        conversations.append({"type": "group", "group": active_group, "ts": active_group.created_at})
    if active_user and not any(c.get("type") == "user" and c.get("user").id == active_user.id for c in conversations):
        conversations.append({"type": "user", "user": active_user, "ts": None})

    # ترتيب ثابت أولًا ثم حسب آخر رسالة (تنازليًا)
    conversations.sort(
        key=lambda it: (
            ((it.get("user").name or "").lower() if it.get("type") == "user" else (it.get("group").name or "").lower()),
            (it.get("user").id if it.get("type") == "user" else it.get("group").id),
        )
    )
    conversations.sort(
        key=lambda it: (it.get("ts") is not None, it.get("ts") or datetime.min),
        reverse=True,
    )

    if CONVERSATION_PREVIEW_LIMIT and len(conversations) > CONVERSATION_PREVIEW_LIMIT:
        limited = conversations[:CONVERSATION_PREVIEW_LIMIT]
        active_entries = []
        if active_group:
            active_entries = [c for c in conversations if c.get("type") == "group" and c.get("group").id == active_group.id]
        if not active_entries and active_user:
            active_entries = [c for c in conversations if c.get("type") == "user" and c.get("user").id == active_user.id]
        if active_entries and active_entries[0] not in limited:
            limited.pop(-1)
            limited.append(active_entries[0])
            limited.sort(
                key=lambda it: (it.get("ts") is not None, it.get("ts") or datetime.min),
                reverse=True,
            )
        conversations = limited

    pending_group_invites = (
        GroupMember.query.options(joinedload(GroupMember.group))
        .filter_by(user_id=user.id, status="pending")
        .order_by(GroupMember.invited_at.desc())
        .all()
    )

    group_ids = [
        gid for (gid,) in (
            db.session.query(GroupMember.group_id)
            .outerjoin(GroupBlock, and_(GroupBlock.group_id == GroupMember.group_id, GroupBlock.user_id == user.id))
            .filter(
                GroupMember.user_id == user.id,
                GroupMember.status == "accepted",
                GroupBlock.id == None  # noqa: E711
            )
            .all()
        )
    ]

    return render_template(
        "chat.html",
        current_user=user,
        users=[],
        groups=[g for g, _ in groups],
        conversations=conversations,
        pending_group_invites=[
            {
                "invite_id": m.id,
                "group_id": m.group_id,
                "group_name": m.group.name if m.group else "",
                "invited_by": m.invited_by
            }
            for m in pending_group_invites
        ],
        active_user=active_user,
        active_group=active_group,
        phone_number=user.phone_number,
        group_ids=group_ids
    )





# ---- Groups API ----
@app.route("/api/groups/create", methods=["POST"])
def create_group():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    raw_name = (data.get("name") or "")
    # Normalize spaces: prevent duplicates with leading/trailing/multiple spaces
    name = " ".join(raw_name.split())
    members = data.get("members") or []
    try:
        members = [int(x) for x in members]
    except Exception:
        members = []

    # اسم المجموعة إلزامي
    if not name:
        return jsonify({"ok": False, "error": "اسم المجموعة إلزامي"}), 400

    # منع تكرار اسم المجموعة لنفس المنشئ (غير حساس لحالة الأحرف)
    existing_same_name = (
        Group.query.filter(
            Group.owner_id == me.id,
            func.lower(Group.name) == func.lower(name),
        )
        .order_by(Group.id.desc())
        .first()
    )
    if existing_same_name:
        return jsonify({"ok": False, "error": "اسم المجموعة موجود بالفعل"}), 409


    # لا تسمح بمجموعة بدون أعضاء آخرين
    members = [uid for uid in set(members) if uid != me.id]
    if len(members) == 0:
        return jsonify({"ok": False, "error": "اختر عضوًا واحدًا على الأقل"}), 400

    # حماية من إنشاء نفس المجموعة مرتين بالخطأ (نفس الاسم خلال ثوانٍ)
    try:
        recent = (
            Group.query
            .filter(Group.owner_id == me.id, Group.name == name, Group.created_at >= (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)))
            .order_by(Group.id.desc())
            .first()
        )
        if recent:
            return jsonify({"ok": True, "group": {"id": recent.id, "name": recent.name}})
    except Exception:
        pass

    try:
        g = Group(name=name, owner_id=me.id)
        db.session.add(g)
        db.session.flush()

        # أضف المالك كعضو accepted
        db.session.add(GroupMember(group_id=g.id, user_id=me.id, status="accepted", invited_by=me.id))

        # أضف الأعضاء كطلبات pending + أرسل لهم رسالة دعوة
        for uid in members:
            db.session.add(GroupMember(group_id=g.id, user_id=uid, status="pending", invited_by=me.id))
            try:
                db.session.add(
                    Message(
                        sender_id=me.id,
                        receiver_id=uid,
                        content=f"دعوة للانضمام إلى مجموعة: {name} — افتح (طلبات المجموعات) لقبول/رفض.",
                        message_type="text",
                    )
                )
            except Exception:
                pass

        db.session.commit()
        return jsonify({"ok": True, "group": {"id": g.id, "name": g.name}})
    except SQLAlchemyError as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": "database_error"}), 500


@app.route("/api/groups/invites")
def get_group_invites():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    invites = (
        db.session.query(GroupMember, Group)
        .join(Group, Group.id == GroupMember.group_id)
        .filter(GroupMember.user_id == me.id, GroupMember.status == "pending")
        .order_by(GroupMember.invited_at.desc())
        .all()
    )

    out = []
    for gm, g in invites:
        out.append({
            "invite_id": gm.id,
            "group_id": g.id,
            "group_name": g.name,
            "invited_by": gm.invited_by
        })
    return jsonify({"ok": True, "invites": out})


@app.route("/api/users")
def api_users():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    users = (
        User.query
        .filter(User.id != me.id)
        .order_by(User.name.asc(), User.id.asc())
        .all()
    )
    return jsonify({
        "ok": True,
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "phone_number": u.phone_number,
                "avatar_url": url_for("static", filename=f"profile_pics/{u.profile_pic or 'default.png'}"),
            }
            for u in users
        ]
    })


@app.route("/api/groups/respond", methods=["POST"])
def respond_group_invite():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    invite_id = data.get("invite_id")
    action = (data.get("action") or "").strip()  # accept/decline
    try:
        invite_id = int(invite_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400

    gm = GroupMember.query.filter_by(id=invite_id, user_id=me.id).first()
    if not gm or gm.status != "pending":
        return jsonify({"ok": False, "error": "not_found"}), 404

    gm.status = "accepted" if action == "accept" else "declined"
    gm.responded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        if gm.status == "accepted":
            try:
                db.session.add(
                    GroupMessage(
                        group_id=gm.group_id,
                        sender_id=gm.user_id,
                        content=f"{me.name} انضم إلى المجموعة",
                        message_type="system",
                    )
                )
            except Exception:
                pass
        db.session.commit()
        return jsonify({"ok": True, "status": gm.status})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "database_error"}), 500



@app.route("/api/groups/<int:group_id>/update", methods=["POST"])
def api_group_update(group_id):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404

    if g.owner_id != me.id:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    name = (request.form.get("name") or (request.json.get("name") if request.is_json else "") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "missing_name"}), 400
    if len(name) > 80:
        return jsonify({"ok": False, "error": "name_too_long"}), 400

    try:
        g.name = name
        db.session.commit()
        return jsonify({"ok": True, "group": {"id": g.id, "name": g.name}})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/groups/<int:group_id>/delete", methods=["POST"])
def api_group_delete(group_id):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404

    if g.owner_id != me.id:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    try:
        # Remove related records first
        GroupMember.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        GroupMessage.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        GroupBlock.query.filter_by(group_id=group_id).delete(synchronize_session=False)

        db.session.delete(g)
        db.session.commit()
        return jsonify({"ok": True})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/groups/<int:group_id>/leave", methods=["POST"])
def api_group_leave(group_id):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404

    if g.owner_id == me.id:
        return jsonify({"ok": False, "error": "owner_cannot_leave"}), 400

    m = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not m:
        return jsonify({"ok": False, "error": "not_member"}), 403

    try:
        GroupBlock.query.filter_by(group_id=group_id, user_id=me.id).delete(synchronize_session=False)
        db.session.delete(m)
        # Add a system message in group chat about leaving
        db.session.add(
            GroupMessage(
                group_id=group_id,
                sender_id=me.id,
                content=f"{me.name} خرج من المجموعة",
                message_type="system",
            )
        )

        # Notify group owner privately
        if g.owner_id and g.owner_id != me.id:
            db.session.add(
                Message(
                    sender_id=me.id,
                    receiver_id=g.owner_id,
                    content=f"{me.name} خرج من المجموعة: {g.name}",
                    message_type="system",
                )
            )
        db.session.commit()
        return jsonify({"ok": True})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/groups/<int:group_id>/block", methods=["POST"])
def api_group_block(group_id):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404

    m = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not m:
        return jsonify({"ok": False, "error": "not_member"}), 403

    existing = GroupBlock.query.filter_by(group_id=group_id, user_id=me.id).first()
    try:
        if existing:
            db.session.delete(existing)
            db.session.commit()
            return jsonify({"ok": True, "blocked": False})
        else:
            db.session.add(GroupBlock(group_id=group_id, user_id=me.id))
            db.session.commit()
            return jsonify({"ok": True, "blocked": True})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500



@app.route("/get_group_messages/<int:group_id>")
def get_group_messages(group_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # ensure membership accepted
    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    last_id = (request.args.get("last_id") or "0").strip()
    limit_raw = request.args.get("limit", str(MESSAGE_PAGE_LIMIT))
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = MESSAGE_PAGE_LIMIT
    if limit < 20:
        limit = 20
    if limit > 200:
        limit = 200
    min_id = 0
    try:
        if int(last_id) > 0:
            min_id = int(last_id)
    except ValueError:
        pass

    q = GroupMessage.query.filter(GroupMessage.group_id == group_id).options(joinedload(GroupMessage.sender))

    is_initial = min_id == 0
    if is_initial:
        msgs_desc = q.order_by(GroupMessage.timestamp.desc(), GroupMessage.id.desc()).limit(limit).all()
        messages = list(reversed(msgs_desc))
    else:
        q = q.filter(GroupMessage.id > min_id)
        messages = q.order_by(GroupMessage.timestamp.asc(), GroupMessage.id.asc()).all()

    res = []
    for m in messages:
        res.append({
            "id": m.id,
            "sender_id": m.sender_id,
            "receiver_id": None,
            "sender_name": m.sender.name if m.sender else "",
            "content": m.content or "",
            "message_type": m.message_type,
            "media_url": m.media_url,
            "media_mime": m.media_mime,
            "timestamp_iso": _utc_iso(m.timestamp),
            "timestamp_ms": _utc_ms(m.timestamp),
        })

    # Frontend expects an array like /get_messages

    # Update last_read_at for unread counts
    try:
        member.last_read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify(res)


@app.route("/send_group_message", methods=["POST"])
def send_group_message():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    group_id = (request.form.get("group_id") or "").strip()
    content = (request.form.get("content") or "").strip()
    try:
        group_id = int(group_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400

    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    if not content:
        return jsonify({"ok": False, "error": "empty"}), 400

    msg = GroupMessage(group_id=group_id, sender_id=me.id, content=content, message_type="text")
    try:
        db.session.add(msg)
        db.session.commit()

        # Web Push notification to other group members
        try:
            sender = User.query.get(me.id)
            members = GroupMember.query.filter_by(group_id=group_id, status="accepted").all()
            for mm in members:
                if mm.user_id == me.id:
                    continue
                payload = {
                    "title": "🎤 رسالة صوتية في المجموعة",
                    "body": f"{(sender.name if sender else 'مستخدم')}: أرسل رسالة صوتية",
                    "icon": "/static/logo.svg",
                    "badge": "/static/logo.svg",
                    "url": f"/chat?group={group_id}",
                    "tag": f"group-{group_id}-{msg.id}",
                    "meta": {"type": "group", "group_id": group_id, "sender_id": me.id, "message_id": msg.id}
                }
                send_push_to_user(mm.user_id, payload)
        except Exception:
            pass

        # Web Push notification to other group members
        try:
            sender = User.query.get(me.id)
            members = GroupMember.query.filter_by(group_id=group_id, status="accepted").all()
            for mm in members:
                if mm.user_id == me.id:
                    continue
                payload = {
                    "title": "👥 رسالة جديدة في المجموعة",
                    "body": f"{(sender.name if sender else 'مستخدم')}: {(content[:120] if content else '')}",
                    "icon": "/static/logo.svg",
                    "badge": "/static/logo.svg",
                    "url": f"/chat?group={group_id}",
                    "tag": f"group-{group_id}-{msg.id}",
                    "meta": {"type": "group", "group_id": group_id, "sender_id": me.id, "message_id": msg.id}
                }
                send_push_to_user(mm.user_id, payload)
        except Exception:
            pass

        _emit_group_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": None,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "database_error"}), 500


# ---- Messages API ----


@app.route("/send_group_image", methods=["POST"])
def send_group_image():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    me = current_user()
    if not me:
        return jsonify({"error": "غير مسموح"}), 401

    group_id_raw = (request.form.get("group_id") or "").strip()
    f = request.files.get("image")
    if not group_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        group_id = int(group_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المجموعة غير صحيح"}), 400

    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"error": "غير مسموح"}), 403

    mimetype = (f.mimetype or "").lower()
    if not mimetype.startswith("image/"):
        return jsonify({"error": "نوع الملف غير مدعوم"}), 400

    # Size limit (8 MB)
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 8 * 1024 * 1024:
        return jsonify({"error": "حجم الصورة كبير"}), 400

    from uuid import uuid4
    filename = secure_filename(f.filename or "image")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        ext = ".png"

    upload_dir = os.path.join(app.root_path, "static", "uploads", "images")
    os.makedirs(upload_dir, exist_ok=True)
    new_name = f"g{group_id}_{me.id}_{uuid4().hex}{ext}"
    save_path = os.path.join(upload_dir, new_name)
    f.save(save_path)

    url = url_for("static", filename=f"uploads/images/{new_name}")
    msg = GroupMessage(group_id=group_id, sender_id=me.id, content="", message_type="image", media_url=url, media_mime=mimetype)
    try:
        db.session.add(msg)
        db.session.commit()

        # Web Push notification to other group members
        try:
            sender = User.query.get(me.id)
            members = GroupMember.query.filter_by(group_id=group_id, status="accepted").all()
            for mm in members:
                if mm.user_id == me.id:
                    continue
                payload = {
                    "title": "🖼️ صورة جديدة في المجموعة",
                    "body": f"{(sender.name if sender else 'مستخدم')}: أرسل صورة",
                    "icon": "/static/logo.svg",
                    "badge": "/static/logo.svg",
                    "url": f"/chat?group={group_id}",
                    "tag": f"group-{group_id}-{msg.id}",
                    "meta": {"type": "group", "group_id": group_id, "sender_id": me.id, "message_id": msg.id}
                }
                send_push_to_user(mm.user_id, payload)
        except Exception:
            pass

        _emit_group_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": None,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "database_error"}), 500


@app.route("/send_group_audio", methods=["POST"])
def send_group_audio():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    me = current_user()
    if not me:
        return jsonify({"error": "غير مسموح"}), 401

    group_id_raw = (request.form.get("group_id") or "").strip()
    f = request.files.get("audio")
    if not group_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        group_id = int(group_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المجموعة غير صحيح"}), 400

    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"error": "غير مسموح"}), 403

    mimetype = (f.mimetype or "").lower()
    if not (mimetype.startswith("audio/") or mimetype in ["application/octet-stream"]):
        return jsonify({"error": "نوع الملف غير مدعوم"}), 400

    # Size limit (12 MB)
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 12 * 1024 * 1024:
        return jsonify({"error": "حجم الملف كبير"}), 400

    from uuid import uuid4
    filename = secure_filename(f.filename or "audio")
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        ext = ".webm"

    upload_dir = os.path.join(app.root_path, "static", "uploads", "audio")
    os.makedirs(upload_dir, exist_ok=True)
    new_name = f"g{group_id}_{me.id}_{uuid4().hex}{ext}"
    save_path = os.path.join(upload_dir, new_name)
    f.save(save_path)

    url = url_for("static", filename=f"uploads/audio/{new_name}")
    msg = GroupMessage(group_id=group_id, sender_id=me.id, content="", message_type="audio", media_url=url, media_mime=mimetype)
    try:
        db.session.add(msg)
        db.session.commit()

        # Web Push notification to other group members
        try:
            sender = User.query.get(me.id)
            members = GroupMember.query.filter_by(group_id=group_id, status="accepted").all()
            for mm in members:
                if mm.user_id == me.id:
                    continue
                payload = {
                    "title": "🎤 رسالة صوتية في المجموعة",
                    "body": f"{(sender.name if sender else 'مستخدم')}: أرسل رسالة صوتية",
                    "icon": "/static/logo.svg",
                    "badge": "/static/logo.svg",
                    "url": f"/chat?group={group_id}",
                    "tag": f"group-{group_id}-{msg.id}",
                    "meta": {"type": "group", "group_id": group_id, "sender_id": me.id, "message_id": msg.id}
                }
                send_push_to_user(mm.user_id, payload)
        except Exception:
            pass

        _emit_group_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": None,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"error": "database_error"}), 500





@app.route("/send_group_file", methods=["POST"])
def send_group_file():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    group_id_raw = (request.form.get("group_id") or "").strip()
    f = request.files.get("file")

    if not group_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        group_id = int(group_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المجموعة غير صحيح"}), 400

    # must be accepted member
    gm = GroupMember.query.filter_by(group_id=group_id, user_id=sender_id, status="accepted").first()
    if not gm:
        return jsonify({"error": "غير مسموح"}), 403

    mimetype = (f.mimetype or "application/octet-stream").lower()

    from uuid import uuid4
    orig_name = secure_filename(f.filename or "file")
    ext = os.path.splitext(orig_name)[1].lower()
    if not ext:
        guess = mimetypes.guess_extension(mimetype) or ""
        ext = guess if len(guess) <= 10 else ""

    new_name = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(app.config["MEDIA_FILE_FOLDER"], new_name)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    f.save(abs_path)

    media_url = url_for("serve_media", category="files", filename=new_name)
    try:
        msg = GroupMessage(
            group_id=group_id,
            sender_id=sender_id,
            content=orig_name or "file",
            message_type="file",
            media_url=media_url,
            media_mime=mimetype,
        )
        db.session.add(msg)
        db.session.commit()

        # Web Push notification to other group members
        try:
            sender = User.query.get(sender_id)
            members = GroupMember.query.filter_by(group_id=group_id, status="accepted").all()
            for mm in members:
                if mm.user_id == sender_id:
                    continue
                payload = {
                    "title": "👥 ملف جديد في المجموعة",
                    "body": f"{(sender.name if sender else 'مستخدم')}: {orig_name}",
                    "icon": "/static/logo.svg",
                    "badge": "/static/logo.svg",
                    "url": f"/chat?group={group_id}",
                    "tag": f"group-{group_id}-{msg.id}",
                    "meta": {"type": "group", "group_id": group_id, "sender_id": sender_id, "message_id": msg.id}
                }
                send_push_to_user(mm.user_id, payload)
        except Exception:
            pass

        _emit_group_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "group_id": msg.group_id,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender.name if msg.sender else "",
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Send group file error")
        return jsonify({"error": "فشل إرسال الملف"}), 500


@app.route("/get_messages/<int:other_user_id>")
def get_messages(other_user_id: int):
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    me = session["user_id"]

    # Validate other user exists
    if not db.session.get(User, other_user_id):
        return jsonify({"error": "المستخدم غير موجود"}), 404

    since_ms = request.args.get("since", "0")
    last_id = request.args.get("last_id", "0")
    limit_raw = request.args.get("limit", str(MESSAGE_PAGE_LIMIT))

    # حدّ آمن
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 200
    if limit < 20:
        limit = 20
    if limit > 200:
        limit = 200

    last_load_time = datetime(1970, 1, 1)
    min_id = 0

    # parse since
    try:
        if int(since_ms) > 0:
            last_load_time = datetime.utcfromtimestamp(int(since_ms) / 1000.0)
    except (ValueError, OSError):
        pass

    # parse last_id
    try:
        if int(last_id) > 0:
            min_id = int(last_id)
    except ValueError:
        pass

    base_filter = or_(
        and_(Message.sender_id == me, Message.receiver_id == other_user_id),
        and_(Message.sender_id == other_user_id, Message.receiver_id == me),
    )

    query = Message.query.filter(base_filter)

    # ====== مهم: لو هذه أول مرة (since=0 و last_id=0) لا تجيب كل الرسائل ======
    is_initial = (min_id == 0)
    try:
        is_initial = is_initial and (int(since_ms) <= 0)
    except ValueError:
        is_initial = True  # لو since غير صالح نعتبرها أول مرة بشكل آمن

    if is_initial:
        # آخر limit رسالة فقط (DESC ثم نعكسها للعرض ASC)
        msgs_desc = query.order_by(Message.timestamp.desc(), Message.id.desc()).limit(limit).all()
        msgs = list(reversed(msgs_desc))
    else:
        # Prefer last_id to prevent duplicates
        if min_id > 0:
            query = query.filter(Message.id > min_id)
        else:
            query = query.filter(Message.timestamp > last_load_time)

        msgs = query.order_by(Message.timestamp.asc(), Message.id.asc()).all()

    # Mark unread messages (received by me)
    try:
        changed = False
        for m in msgs:
            if m.receiver_id == me and not m.is_read:
                m.is_read = True
                changed = True
        if changed:
            db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()

    return jsonify([
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "receiver_id": m.receiver_id,
            "content": m.content,
            "timestamp_iso": (m.timestamp.replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z") if m.timestamp else None),
            "timestamp_ms": int((m.timestamp.replace(tzinfo=timezone.utc).timestamp()) * 1000) if m.timestamp else 0,
            "is_read": m.is_read,
            "message_type": getattr(m, "message_type", "text"),
            "media_url": getattr(m, "media_url", None),
            "media_mime": getattr(m, "media_mime", None),
        }
        for m in msgs
    ])


@app.route("/send_message", methods=["POST"])
def send_message():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    receiver_id_raw = request.form.get("receiver_id", "").strip()
    content = (request.form.get("content") or "").strip()

    if not receiver_id_raw or not content:
        return jsonify({"error": "حقول ناقصة"}), 400

    if len(content) > 5000:
        return jsonify({"error": "الرسالة طويلة جداً"}), 400

    try:
        receiver_id = int(receiver_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المستقبل غير صحيح"}), 400

    if not db.session.get(User, receiver_id):
        return jsonify({"error": "المستخدم غير موجود"}), 404

    try:
        msg = Message(sender_id=sender_id, receiver_id=receiver_id, content=content)
        db.session.add(msg)
        db.session.commit()

        # Web Push notification (works even if the page is fully closed) 
        try:
            sender = User.query.get(sender_id)
            payload = {
                "title": "💬 رسالة جديدة",
                "body": f"{(sender.name if sender else 'مستخدم')}: {(content[:120] if content else '')}",
                "icon": "/static/logo.svg",
                "badge": "/static/logo.svg",
                "url": f"/chat?user={sender_id}",
                "tag": f"dm-{msg.id}",
                "meta": {"type": "dm", "sender_id": sender_id, "receiver_id": receiver_id, "message_id": msg.id}
            }
            send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "message_type": getattr(msg, "message_type", "text"),
                "media_url": getattr(msg, "media_url", None),
                "media_mime": getattr(msg, "media_mime", None),
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Send message error")
        return jsonify({"error": "فشل إرسال الرسالة"}), 500

@app.route("/send_image", methods=["POST"])
def send_image():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    receiver_id_raw = (request.form.get("receiver_id") or "").strip()
    f = request.files.get("image")

    if not receiver_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        receiver_id = int(receiver_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المستقبل غير صحيح"}), 400

    if not db.session.get(User, receiver_id):
        return jsonify({"error": "المستخدم غير موجود"}), 404

    # Validate image
    mimetype = (f.mimetype or "").lower()
    if not mimetype.startswith("image/"):
        return jsonify({"error": "نوع الملف غير مدعوم"}), 400

    # Size limit (8 MB)
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 8 * 1024 * 1024:
        return jsonify({"error": "حجم الصورة كبير"}), 400

    from uuid import uuid4
    filename = secure_filename(f.filename or "image")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        # fallback based on mimetype
        ext = ".png" if "png" in mimetype else ".jpg"

    new_name = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(app.config["MEDIA_IMAGE_FOLDER"], new_name)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    f.save(abs_path)

    media_url = url_for("serve_media", category="images", filename=new_name)
    try:
        msg = Message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            content="[image]",
            message_type="image",
            media_url=media_url,
            media_mime=mimetype,
        )
        db.session.add(msg)
        db.session.commit()

        # Web Push notification
        try:
            sender = User.query.get(sender_id)
            payload = {
                "title": "🖼️ صورة جديدة",
                "body": f"{(sender.name if sender else 'مستخدم')}: أرسل صورة",
                "icon": "/static/logo.svg",
                "badge": "/static/logo.svg",
                "url": f"/chat?user={sender_id}",
                "tag": f"dm-{msg.id}",
                "meta": {"type": "dm", "sender_id": sender_id, "receiver_id": receiver_id, "message_id": msg.id}
            }
            send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "is_read": msg.is_read,
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Send image error")
        return jsonify({"error": "فشل إرسال الصورة"}), 500


@app.route("/send_audio", methods=["POST"])
def send_audio():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    receiver_id_raw = (request.form.get("receiver_id") or "").strip()
    f = request.files.get("audio")

    if not receiver_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        receiver_id = int(receiver_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المستقبل غير صحيح"}), 400

    if not db.session.get(User, receiver_id):
        return jsonify({"error": "المستخدم غير موجود"}), 404

    mimetype = (f.mimetype or "").lower()
    allowed = ["audio/webm", "audio/ogg", "audio/wav", "audio/mpeg", "audio/mp4"]
    if not (mimetype.startswith("audio/") or mimetype in allowed):
        return jsonify({"error": "نوع الملف غير مدعوم"}), 400

    # Size limit (12 MB)
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > 12 * 1024 * 1024:
        return jsonify({"error": "حجم الصوت كبير"}), 400

    from uuid import uuid4
    filename = secure_filename(f.filename or "voice")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4"]:
        ext = ".webm"

    new_name = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(app.config["MEDIA_AUDIO_FOLDER"], new_name)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    f.save(abs_path)

    media_url = url_for("serve_media", category="audio", filename=new_name)
    try:
        msg = Message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            content="[audio]",
            message_type="audio",
            media_url=media_url,
            media_mime=mimetype,
        )
        db.session.add(msg)
        db.session.commit()

        # Web Push notification
        try:
            sender = User.query.get(sender_id)
            payload = {
                "title": "🎤 رسالة صوتية",
                "body": f"{(sender.name if sender else 'مستخدم')}: أرسل رسالة صوتية",
                "icon": "/static/logo.svg",
                "badge": "/static/logo.svg",
                "url": f"/chat?user_id={sender_id}",
                "tag": f"dm-{msg.id}",
                "meta": {"type": "dm", "sender_id": sender_id, "receiver_id": receiver_id, "message_id": msg.id}
            }
            send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "is_read": msg.is_read,
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Send audio error")
        return jsonify({"error": "فشل إرسال الصوت"}), 500




# ---- Profile ----



@app.route("/send_file", methods=["POST"])
def send_file():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    receiver_id_raw = (request.form.get("receiver_id") or "").strip()
    f = request.files.get("file")

    if not receiver_id_raw or not f:
        return jsonify({"error": "حقول ناقصة"}), 400

    try:
        receiver_id = int(receiver_id_raw)
    except ValueError:
        return jsonify({"error": "معرف المستقبل غير صحيح"}), 400

    if not db.session.get(User, receiver_id):
        return jsonify({"error": "المستخدم غير موجود"}), 404

    # Size limit handled globally by MAX_CONTENT_LENGTH (25MB)

    mimetype = (f.mimetype or "application/octet-stream").lower()

    from uuid import uuid4
    orig_name = secure_filename(f.filename or "file")
    ext = os.path.splitext(orig_name)[1].lower()
    if not ext:
        # guess extension from mimetype
        guess = mimetypes.guess_extension(mimetype) or ""
        ext = guess if len(guess) <= 10 else ""

    new_name = f"{uuid4().hex}{ext}"
    abs_path = os.path.join(app.config["MEDIA_FILE_FOLDER"], new_name)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    f.save(abs_path)

    media_url = url_for("serve_media", category="files", filename=new_name)
    try:
        msg = Message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            content=orig_name or "file",
            message_type="file",
            media_url=media_url,
            media_mime=mimetype,
        )
        db.session.add(msg)
        db.session.commit()

        # Web Push notification
        try:
            sender = User.query.get(sender_id)
            payload = {
                "title": "📎 ملف جديد",
                "body": f"{(sender.name if sender else 'مستخدم')}: {orig_name}",
                "icon": "/static/logo.svg",
                "badge": "/static/logo.svg",
                "url": f"/chat?user_id={sender_id}",
                "tag": f"dm-{msg.id}",
                "meta": {"type": "dm", "sender_id": sender_id, "receiver_id": receiver_id, "message_id": msg.id}
            }
            send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({
            "status": "ok",
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "receiver_id": msg.receiver_id,
                "content": msg.content,
                "timestamp_iso": _utc_iso(msg.timestamp),
                "timestamp_ms": _utc_ms(msg.timestamp),
                "is_read": msg.is_read,
                "message_type": msg.message_type,
                "media_url": msg.media_url,
                "media_mime": msg.media_mime,
            }
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Send file error")
        return jsonify({"error": "فشل إرسال الملف"}), 500


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if not login_required():
        return redirect(url_for("login"))

    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))

    if request.method == "POST":
        new_name = (request.form.get("name") or "").strip()
        if new_name:
            if len(new_name) < 2 or len(new_name) > 50:
                return render_template("profile.html", user=user, error="الاسم يجب أن يكون بين 2 و 50 حرف")
            user.name = new_name
            session["name"] = new_name

        file = request.files.get("profile_pic")
        if file and file.filename:
            if not allowed_file(file.filename):
                return render_template("profile.html", user=user, error="نوع الملف غير مدعوم")

            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = secure_filename(f"{user.id}_{int(datetime.now().timestamp())}.{ext}")
            ensure_media_folders()
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            try:
                file.save(filepath)

                # delete old file (best-effort)
                if user.profile_pic and user.profile_pic != "default.png":
                    old_path = os.path.join(app.config["UPLOAD_FOLDER"], user.profile_pic)
                    if os.path.exists(old_path):
                        try:
                            os.remove(old_path)
                        except Exception:
                            app.logger.warning("Could not remove old profile picture")

                user.profile_pic = filename

            except Exception:
                app.logger.exception("Upload profile picture error")
                return render_template("profile.html", user=user, error="فشل رفع الصورة")

        try:
            db.session.commit()
            return render_template("profile.html", user=user, success="تم تحديث الملف الشخصي بنجاح")
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Update profile error")
            return render_template("profile.html", user=user, error="فشل تحديث الملف الشخصي")

    return render_template("profile.html", user=user)


@app.route("/api/update_profile_pic", methods=["POST"])
def update_profile_pic():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    user = current_user()
    if not user:
        return jsonify({"error": "المستخدم غير موجود"}), 404

    file = request.files.get("profile_pic")
    if not file or not file.filename:
        return jsonify({"error": "لم يتم اختيار صورة"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "نوع الملف غير مدعوم"}), 400

    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"{user.id}_{int(datetime.now().timestamp())}.{ext}")

    ensure_media_folders()
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)

    try:
        file.save(filepath)

        # delete old picture best-effort
        if user.profile_pic and user.profile_pic != "default.png":
            old_path = os.path.join(app.config["UPLOAD_FOLDER"], user.profile_pic)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    app.logger.warning("Failed removing old profile pic")

        user.profile_pic = filename
        db.session.commit()

        return jsonify({
            "status": "ok",
            "message": "تم تحديث الصورة بنجاح",
            "new_pic_url": url_for("static", filename="profile_pics/" + filename)
        })
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("Update profile pic error")
        return jsonify({"error": "حدث خطأ أثناء رفع الصورة"}), 500


@app.route("/get_unread_count")
def get_unread_count():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    uid = session["user_id"]
    unread_count = Message.query.filter_by(receiver_id=uid, is_read=False).count()
    return jsonify({"unread_count": unread_count})


# ----------------- Error Handlers -----------------
@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_error(_):
    try:
        db.session.rollback()
    except Exception:
        pass
    return render_template("500.html"), 500

def ensure_message_media_columns():
    """Add media-related columns to existing SQLite DB without breaking old installations."""
    try:
        with db.engine.connect() as conn:
            cols = conn.exec_driver_sql("PRAGMA table_info(messages)").fetchall()
            existing = {c[1] for c in cols}  # (cid, name, type, notnull, dflt_value, pk)

            alter_stmts = []
            if "message_type" not in existing:
                alter_stmts.append("ALTER TABLE messages ADD COLUMN message_type VARCHAR(20) DEFAULT 'text'")
            if "media_url" not in existing:
                alter_stmts.append("ALTER TABLE messages ADD COLUMN media_url TEXT")
            if "media_mime" not in existing:
                alter_stmts.append("ALTER TABLE messages ADD COLUMN media_mime VARCHAR(100)")

            for stmt in alter_stmts:
                conn.exec_driver_sql(stmt)

    except Exception:
        # Don't crash app if migration fails; logs will show under debug
        app.logger.exception("DB migration (media columns) failed")


def ensure_group_member_columns():
    """Add group-related columns for backward compatible SQLite DB."""
    try:
        with db.engine.connect() as conn:
            cols = conn.exec_driver_sql("PRAGMA table_info(group_members)").fetchall()
            existing = {c[1] for c in cols}

            alter_stmts = []
            if "last_read_at" not in existing:
                alter_stmts.append("ALTER TABLE group_members ADD COLUMN last_read_at DATETIME")

            for stmt in alter_stmts:
                conn.exec_driver_sql(stmt)
    except Exception:
        app.logger.exception("ensure_group_member_columns failed")


def ensure_group_member_last_read_column():
    """Add last_read_at column to group_members for unread counts in groups."""
    try:
        with db.engine.connect() as conn:
            cols = conn.exec_driver_sql("PRAGMA table_info(group_members)").fetchall()
            existing = {c[1] for c in cols}
            if "last_read_at" not in existing:
                conn.exec_driver_sql("ALTER TABLE group_members ADD COLUMN last_read_at DATETIME")
    except Exception:
        app.logger.exception("DB migration (group_members.last_read_at) failed")

def ensure_media_folders():
    upload_folder = app.config.get("UPLOAD_FOLDER") or os.path.join(app.root_path, "static", "uploads")
    app.config["UPLOAD_FOLDER"] = upload_folder
    os.makedirs(upload_folder, exist_ok=True)


def init_storage_and_db():
    """Initialize DB/tables/columns when running via `flask run` or `python app.py`."""
    with app.app_context():
        db.create_all()
        ensure_message_media_columns()
        ensure_group_member_columns()
        ensure_media_folders()


# Run initialization at import time so it works with `flask run`
try:
    init_storage_and_db()
except Exception:
    # Don't block startup; errors will be visible in logs
    app.logger.exception("Init failed")


# ----------------- Run -----------------



@app.route("/media/<path:category>/<path:filename>")
def serve_media(category, filename):
    """Serve protected media files only to authorized users."""
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    # Normalize category
    category = (category or "").strip().lower()
    if category not in ("images", "audio", "files"):
        abort(404)

    # Rebuild the media_url exactly as stored
    media_url = f"/media/{category}/{filename}"

    # Direct messages authorization
    dm = Message.query.filter(Message.media_url == media_url).first()
    if dm:
        if dm.sender_id != me.id and dm.receiver_id != me.id:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        folder = app.config["MEDIA_IMAGE_FOLDER"] if category == "images" else app.config["MEDIA_AUDIO_FOLDER"] if category == "audio" else app.config["MEDIA_FILE_FOLDER"]
        return send_from_directory(folder, filename, as_attachment=(category == "files"))

    # Group messages authorization
    gm = GroupMessage.query.filter(GroupMessage.media_url == media_url).first()
    if gm:
        # Must be accepted member and not blocked
        m = GroupMember.query.filter_by(group_id=gm.group_id, user_id=me.id, status="accepted").first()
        if not m:
            return jsonify({"ok": False, "error": "forbidden"}), 403
        if GroupBlock.query.filter_by(group_id=gm.group_id, user_id=me.id).first():
            return jsonify({"ok": False, "error": "forbidden"}), 403
        folder = app.config["MEDIA_IMAGE_FOLDER"] if category == "images" else app.config["MEDIA_AUDIO_FOLDER"] if category == "audio" else app.config["MEDIA_FILE_FOLDER"]
        return send_from_directory(folder, filename, as_attachment=(category == "files"))

    abort(404)


@app.route("/api/unread_counts")
def api_unread_counts():
    # لا تستخدم current_user.is_authenticated لأنك لا تستخدم Flask-Login هنا
    if not login_required():
        return jsonify({"ok": False}), 200

    me = current_user()
    if not me:
        return jsonify({"ok": False}), 200

    uid = me.id

    # Unread counts for direct messages grouped by sender
    user_counts = {}
    try:
        rows = (
            db.session.query(Message.sender_id, func.count(Message.id))
            .filter(Message.receiver_id == uid, Message.is_read == False)  # noqa: E712
            .group_by(Message.sender_id)
            .all()
        )
        user_counts = {int(sid): int(cnt) for sid, cnt in rows}
    except Exception:
        user_counts = {}

    # Pending group invites
    invites_count = 0
    try:
        invites_count = GroupMember.query.filter_by(user_id=uid, status="pending").count()
    except Exception:
        invites_count = 0

    # Unread counts for groups: messages newer than last_read_at
    group_counts = {}
    try:
        memberships = GroupMember.query.filter_by(user_id=uid, status="accepted").all()
        for m in memberships:
            if GroupBlock.query.filter_by(group_id=m.group_id, user_id=uid).first():
                continue
            last_read = getattr(m, "last_read_at", None) or m.responded_at or m.invited_at
            q = GroupMessage.query.filter(GroupMessage.group_id == m.group_id)
            q = q.filter(GroupMessage.sender_id != uid)
            if last_read:
                q = q.filter(GroupMessage.timestamp > last_read)
            group_counts[int(m.group_id)] = int(q.count())
    except Exception:
        group_counts = {}

    # Latest interaction timestamp per conversation (to allow live reordering in sidebar)
    last_ts_users = {}
    try:
        other_id = case(
            (Message.sender_id == uid, Message.receiver_id),
            else_=Message.sender_id,
        )
        rows = (
            db.session.query(other_id.label("other_id"), func.max(Message.timestamp).label("mx"))
            .filter(or_(Message.sender_id == uid, Message.receiver_id == uid))
            .group_by(other_id)
            .all()
        )
        for oid, mx in rows:
            if oid is None or mx is None:
                continue
            last_ts_users[int(oid)] = int(mx.timestamp() * 1000)
    except Exception:
        last_ts_users = {}

    last_ts_groups = {}
    try:
        memberships = GroupMember.query.filter_by(user_id=uid, status="accepted").all()
        gids = [
            m.group_id
            for m in memberships
            if not GroupBlock.query.filter_by(group_id=m.group_id, user_id=uid).first()
        ]
        if gids:
            rows = (
                db.session.query(GroupMessage.group_id, func.max(GroupMessage.timestamp))
                .filter(GroupMessage.group_id.in_(gids))
                .group_by(GroupMessage.group_id)
                .all()
            )
            for gid, mx in rows:
                if gid is None or mx is None:
                    continue
                last_ts_groups[int(gid)] = int(mx.timestamp() * 1000)
    except Exception:
        last_ts_groups = {}

    return jsonify({
        "ok": True,
        "users": user_counts,
        "groups": group_counts,
        "invites": invites_count,
        "last_ts": {"users": last_ts_users, "groups": last_ts_groups},
    }), 200

def compute_unread_counts_for_user(uid: int):
    # Unread counts for direct messages grouped by sender
    user_counts = {}
    try:
        rows = (
            db.session.query(Message.sender_id, func.count(Message.id))
            .filter(Message.receiver_id == uid, Message.is_read == False)  # noqa: E712
            .group_by(Message.sender_id)
            .all()
        )
        user_counts = {int(sid): int(cnt) for sid, cnt in rows}
    except Exception:
        user_counts = {}

    # Pending group invites
    invites_count = 0
    try:
        invites_count = GroupMember.query.filter_by(user_id=uid, status="pending").count()
    except Exception:
        invites_count = 0

    # Unread counts for groups: messages newer than last_read_at
    group_counts = {}
    try:
        memberships = GroupMember.query.filter_by(user_id=uid, status="accepted").all()
        for m in memberships:
            if GroupBlock.query.filter_by(group_id=m.group_id, user_id=uid).first():
                continue
            last_read = getattr(m, "last_read_at", None) or m.responded_at or m.invited_at
            q = GroupMessage.query.filter(GroupMessage.group_id == m.group_id)
            q = q.filter(GroupMessage.sender_id != uid)
            if last_read:
                q = q.filter(GroupMessage.timestamp > last_read)
            group_counts[int(m.group_id)] = int(q.count())
    except Exception:
        group_counts = {}

    # Latest interaction timestamp per conversation (for sidebar reorder)
    last_ts_users = {}
    try:
        other_id = case(
            (Message.sender_id == uid, Message.receiver_id),
            else_=Message.sender_id,
        )
        rows = (
            db.session.query(other_id.label("other_id"), func.max(Message.timestamp).label("mx"))
            .filter(or_(Message.sender_id == uid, Message.receiver_id == uid))
            .group_by(other_id)
            .all()
        )
        for oid, mx in rows:
            if oid is None or mx is None:
                continue
            last_ts_users[int(oid)] = int(mx.timestamp() * 1000)
    except Exception:
        last_ts_users = {}

    last_ts_groups = {}
    try:
        memberships = GroupMember.query.filter_by(user_id=uid, status="accepted").all()
        gids = [
            m.group_id
            for m in memberships
            if not GroupBlock.query.filter_by(group_id=m.group_id, user_id=uid).first()
        ]
        if gids:
            rows = (
                db.session.query(GroupMessage.group_id, func.max(GroupMessage.timestamp))
                .filter(GroupMessage.group_id.in_(gids))
                .group_by(GroupMessage.group_id)
                .all()
            )
            for gid, mx in rows:
                if gid is None or mx is None:
                    continue
                last_ts_groups[int(gid)] = int(mx.timestamp() * 1000)
    except Exception:
        last_ts_groups = {}

    return {
        "ok": True,
        "users": user_counts,
        "groups": group_counts,
        "invites": invites_count,
        "last_ts": {"users": last_ts_users, "groups": last_ts_groups},
    }

import os
from flask import Flask, request, send_file, render_template_string, jsonify
import ssl

# تحديد مسار ملفات الشهادات
CERT_DIR = os.path.join(os.path.dirname(__file__), 'certs')
ROOT_CA_PATH = os.path.join(CERT_DIR, 'rootCA.pem')
SERVER_CERT_PATH = os.path.join(CERT_DIR, 'rootCA.crt')
SERVER_KEY_PATH = os.path.join(CERT_DIR, 'server.key')

# التأكد من وجود مجلد الشهادات
if not os.path.exists(CERT_DIR):
    os.makedirs(CERT_DIR)

# ==============================================
# إضافة هذا الكود بعد تعريف app وقبل تعريف الـ routes الأخرى
# ==============================================

# HTML صفحة لتنزيل الشهادة (ضع هذا متغير قبل الدوال)
INSTALL_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>تثبيت شهادة SSL للتطوير</title>
    <meta charset="utf-8">
    <style>
        body { font-family: Arial, sans-serif; direction: rtl; text-align: right; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .warning { background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }
        .steps { margin: 20px 0; }
        .step { margin-bottom: 15px; }
        .btn { display: inline-block; background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>تثبيت شهادة SSL للتطوير</h1>
        
        <div class="warning">
            <strong>تحذير:</strong> هذه الشهادة تستخدم لأغراض التطوير فقط.
            لا تقم بتثبيتها إلا إذا كنت تثق بهذا الخادم.
        </div>
        
        <div class="steps">
            <div class="step">
                <h3>خطوة 1: تنزيل الشهادة</h3>
                <p>انقر على الزر أدناه لتنزيل ملف الشهادة:</p>
                <a href="/download-cert" class="btn">تنزيل شهادة ROOT CA</a>
            </div>
            
            <div class="step">
                <h3>خطوة 2: تثبيت الشهادة</h3>
                <h4>لنظام Windows:</h4>
                <ol>
                    <li>انقر نقراً مزدوجاً على الملف الذي تم تنزيله</li>
                    <li>اختر "تثبيت الشهادة"</li>
                    <li>اختر "المخزن: مخزن الجذر الموثوق به"</li>
                    <li>اتبع الخطوات وأعد تشغيل المتصفح</li>
                </ol>
                
                <h4>لنظام macOS:</h4>
                <ol>
                    <li>انقر نقراً مزدوجاً على الملف الذي تم تنزيله</li>
                    <li>أضف إلى "المفتاح" واختر "تسجيلات الدخول"</li>
                    <li>انقر على "إضافة"</li>
                    <li>أعد تشغيل المتصفح</li>
                </ol>
            </div>
        </div>
        
        <div style="margin-top: 30px;">
            <a href="/" class="btn">العودة للتطبيق</a>
        </div>
    </div>
</body>
</html>"""

# ==============================================
# إضافة routes جديدة (ضعها مع باقي الـ routes)
# ==============================================

@app.route('/install-certificate')
def install_certificate():
    """عرض صفحة تثبيت الشهادة"""
    return render_template_string(INSTALL_PAGE)

@app.route('/download-cert')
def download_cert():
    """تنزيل ملف الشهادة"""
    # تأكد من أن الملف موجود
    if not os.path.exists(ROOT_CA_PATH):
        return """
        <div style='padding:20px; text-align:center;'>
            <h2>❌ ملف الشهادة غير موجود</h2>
            <p>الرجاء وضع ملف rootCA.pem في مجلد certs/</p>
            <p>المسار المطلوب: {}</p>
        </div>
        """.format(ROOT_CA_PATH), 404
    
    return send_file(
        ROOT_CA_PATH,
        as_attachment=True,
        download_name='rootCA.pem',
        mimetype='application/x-pem-certificate'
    )

# ==============================================
# إضافة route للصفحة الرئيسية إذا لم تكن موجودة
# أو تعديلها إذا كانت موجودة
# ==============================================

@app.route('/')
def index():
    """الصفحة الرئيسية مع فحص SSL"""
    
    # كود التطبيق الأصلي الخاص بك هنا
    # إذا كان لديك كود أصلي، احتفظ به وأضف التحقق
    
    # HTML الأساسي
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>تطبيق Flask</title>
        <meta charset="utf-8">
        <style>
            body { font-family: Arial, sans-serif; direction: rtl; text-align: right; padding: 20px; }
            .alert { padding: 15px; border-radius: 5px; margin: 20px 0; }
            .alert-warning { background-color: #fff3cd; border: 1px solid #ffeaa7; color: #856404; }
            .btn { display: inline-block; background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 10px 0; }
        </style>
    </head>
    <body>
        <h1>تطبيق Flask</h1>
    """
    
    # إضافة تحذير إذا كان هناك مشكلة SSL
    # (يمكنك تعديل هذا الجزء حسب حاجتك)
    html += """
        <div class="alert alert-warning">
            <h3>معلومات SSL</h3>
            <p>إذا واجهتك مشكلة في اتصال SSL، يمكنك تثبيت شهادة التطوير:</p>
            <a href="/install-certificate" class="btn">تثبيت شهادة SSL للتطوير</a>
        </div>
    """
    
    # هنا يبدأ كودك الأصلي
    html += """
        <!-- محتوى تطبيقك الأصلي -->
        <p>هذا تطبيق Flask يعمل مع SSL.</p>
    """
    
    html += """
    </body>
    </html>
    """
    
    return html

def setup_ssl():
    """إعداد SSL للخادم"""
    try:
        # إنشاء context للـ SSL
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        
        # تحميل الشهادات إذا كانت موجودة
        if os.path.exists(SERVER_CERT_PATH) and os.path.exists(SERVER_KEY_PATH):
            ssl_context.load_cert_chain(SERVER_CERT_PATH, SERVER_KEY_PATH)
            print("✅ تم تحميل شهادات SSL بنجاح")
            return ssl_context
        else:
            print("⚠️  ملاحظة: شهادات SSL غير موجودة، التشغيل بدون SSL")
            return None
    except Exception as e:
        print(f"❌ خطأ في إعداد SSL: {e}")
        return None

if __name__ == '__main__':
    # محاولة تشغيل مع SSL
    ssl_context = setup_ssl()
    
    if ssl_context:
        print("=" * 50)
        print("🌐 الخادم يعمل مع SSL على:")
        print(f"   https://localhost:5000")
        print(f"   https://127.0.0.1:5000")
        print("\n📄 لتنزيل شهادة التطوير:")
        print(f"   https://localhost:5000/install-certificate")
        print("=" * 50)
        
    socketio.run(
        app,
        host="0.0.0.0",
        port=5443,
        debug=False,
        use_reloader=False,
        ssl_context=("192.168.1.19.pem", "192.168.1.19-key.pem"),
    )
