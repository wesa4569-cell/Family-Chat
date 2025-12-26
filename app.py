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

def _load_or_create_persistent_secret_key() -> str:
    """Return a stable SECRET_KEY.

    - Prefer SECRET_KEY from environment (recommended for production).
    - Otherwise, generate a strong key once and persist it under instance/secret_key.txt
      so Flask sessions remain valid across restarts.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    try:
        os.makedirs(app.instance_path, exist_ok=True)
        key_path = os.path.join(app.instance_path, "secret_key.txt")
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as f:
                persisted = (f.read() or "").strip()
                if persisted:
                    return persisted
        import secrets
        persisted = secrets.token_hex(32)
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(persisted)
        return persisted
    except Exception:
        # Last resort (dev only). In production, always set SECRET_KEY.
        return "dev-secret-key-change-in-production"


# Security: use env var in production; fallback to persisted key for stable sessions.
app.secret_key = _load_or_create_persistent_secret_key()

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
# Default "remember me" session lifetime (can be overridden at login).
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=int(os.environ.get("SESSION_DAYS", "30")))
# Refresh session expiry on activity so the user stays logged in while active.
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
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
    payload = {
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
        "is_read": getattr(msg, "is_read", False),
        "delivered_at": _utc_iso(getattr(msg, "delivered_at", None)),
        "read_at": _utc_iso(getattr(msg, "read_at", None)),
        "edited_at": _utc_iso(getattr(msg, "edited_at", None)),
        "deleted_for_all": bool(getattr(msg, "deleted_for_all", False)),
        "reply_to_id": getattr(msg, "reply_to_id", None),
        "forwarded": bool(getattr(msg, "forwarded", False)),
    }

    # Best-effort: include reply snippet to render quote in UI
    try:
        rtid = getattr(msg, "reply_to_id", None)
        if rtid:
            is_group = getattr(msg, "group_id", None) is not None
            if is_group:
                rm = db.session.get(GroupMessage, int(rtid))
            else:
                rm = db.session.get(Message, int(rtid))
            if rm and not bool(getattr(rm, "deleted_for_all", False)):
                rsender = getattr(rm, "sender", None) or db.session.get(User, getattr(rm, "sender_id", None))
                payload["reply_to"] = {
                    "id": int(rm.id),
                    "sender_name": (rsender.name if rsender else ""),
                    "content": (getattr(rm, "content", "") or "")[:400],
                }
    except Exception:
        pass

    return payload


def _emit_direct_message(msg):
    # If receiver is online at the moment of emit, mark as delivered.
    try:
        if msg.receiver_id in online_users and getattr(msg, "delivered_at", None) is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            msg.delivered_at = now
            db.session.commit()
            socketio.emit(
                "message_status",
                {"type": "dm", "status": "delivered", "message_ids": [int(msg.id)], "at": _utc_iso(now)},
                room=f"user_{msg.sender_id}",
            )
    except Exception:
        db.session.rollback()

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


def _load_group_activity(uid: int):
    memberships = GroupMember.query.filter_by(user_id=uid, status="accepted").all()
    if not memberships:
        return {}, {}

    group_ids = [m.group_id for m in memberships]
    blocked_ids = {
        gid
        for (gid,) in db.session.query(GroupBlock.group_id)
        .filter(GroupBlock.user_id == uid, GroupBlock.group_id.in_(group_ids))
        .all()
    }
    active_group_ids = [gid for gid in group_ids if gid not in blocked_ids]
    if not active_group_ids:
        return {}, {}

    group_counts = {int(gid): 0 for gid in active_group_ids}
    last_read_expr = func.coalesce(
        GroupMember.last_read_at,
        GroupMember.responded_at,
        GroupMember.invited_at,
    )
    rows = (
        db.session.query(GroupMessage.group_id, func.count(GroupMessage.id))
        .join(GroupMember, GroupMember.group_id == GroupMessage.group_id)
        .filter(
            GroupMember.user_id == uid,
            GroupMember.status == "accepted",
            GroupMessage.group_id.in_(active_group_ids),
            GroupMessage.sender_id != uid,
            GroupMessage.timestamp > last_read_expr,
        )
        .group_by(GroupMessage.group_id)
        .all()
    )
    for gid, cnt in rows:
        group_counts[int(gid)] = int(cnt)

    last_ts_groups = {}
    rows = (
        db.session.query(GroupMessage.group_id, func.max(GroupMessage.timestamp))
        .filter(GroupMessage.group_id.in_(active_group_ids))
        .group_by(GroupMessage.group_id)
        .all()
    )
    for gid, mx in rows:
        if gid is None or mx is None:
            continue
        last_ts_groups[int(gid)] = int(mx.timestamp() * 1000)

    return group_counts, last_ts_groups


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

    # WhatsApp-like status metadata (backward-compatible additions)
    delivered_at = db.Column(db.DateTime, nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)
    edited_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_for_all = db.Column(db.Boolean, default=False, index=True)
    reply_to_id = db.Column(db.Integer, nullable=True, index=True)
    forwarded = db.Column(db.Boolean, default=False, index=True)

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


class MessageReaction(db.Model):
    __tablename__ = "message_reactions"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    emoji = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.UniqueConstraint("message_id", "user_id", "emoji", name="uq_react_msg_user_emoji"),
        db.Index("ix_react_msg_user", "message_id", "user_id"),
    )


class MessageVisibility(db.Model):
    """Per-user soft delete (delete for me) for direct and group messages."""
    __tablename__ = "message_visibility"

    id = db.Column(db.Integer, primary_key=True)
    message_type = db.Column(db.String(16), nullable=False, index=True)  # dm|group
    message_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    is_deleted_for_me = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    __table_args__ = (
        db.UniqueConstraint("message_type", "message_id", "user_id", name="uq_vis_type_msg_user"),
        db.Index("ix_vis_user_type", "user_id", "message_type"),
    )


class ConversationSetting(db.Model):
    __tablename__ = "conversation_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    conversation_type = db.Column(db.String(16), nullable=False, index=True)  # dm|group
    conversation_id = db.Column(db.Integer, nullable=False, index=True)  # other_user_id or group_id
    is_archived = db.Column(db.Boolean, default=False, index=True)
    pinned_rank = db.Column(db.Integer, nullable=True, index=True)
    muted_until = db.Column(db.DateTime, nullable=True, index=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        db.UniqueConstraint("user_id", "conversation_type", "conversation_id", name="uq_conv_settings"),
        db.Index("ix_conv_user_arch", "user_id", "is_archived"),
    )




def is_conversation_muted(user_id: int, conv_type: str, conv_id: int) -> bool:
    """Return True if user has muted this conversation and mute is still active."""
    try:
        row = ConversationSetting.query.filter_by(
            user_id=int(user_id),
            conversation_type=str(conv_type),
            conversation_id=int(conv_id),
        ).first()
        if row and row.muted_until:
            return row.muted_until > datetime.utcnow()
    except Exception:
        return False
    return False

class StarredMessage(db.Model):
    __tablename__ = "starred_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message_type = db.Column(db.String(16), nullable=False, index=True)  # dm|group
    message_id = db.Column(db.Integer, nullable=False, index=True)
    starred_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "message_type", "message_id", name="uq_star_user_type_msg"),
        db.Index("ix_star_user", "user_id", "starred_at"),
    )


class GroupInviteLink(db.Model):
    __tablename__ = "group_invite_links"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False, index=True)
    token = db.Column(db.String(128), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    max_uses = db.Column(db.Integer, nullable=True)
    uses = db.Column(db.Integer, default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)


class GroupMessageReceipt(db.Model):
    __tablename__ = "group_message_receipts"

    id = db.Column(db.Integer, primary_key=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey("group_messages.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    delivered_at = db.Column(db.DateTime, nullable=True, index=True)
    read_at = db.Column(db.DateTime, nullable=True, index=True)
    __table_args__ = (
        db.UniqueConstraint("group_message_id", "user_id", name="uq_gmr_msg_user"),
    )


class GroupMessageMention(db.Model):
    __tablename__ = "group_message_mentions"

    id = db.Column(db.Integer, primary_key=True)
    group_message_id = db.Column(db.Integer, db.ForeignKey("group_messages.id"), nullable=False, index=True)
    mentioned_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    __table_args__ = (
        db.UniqueConstraint("group_message_id", "mentioned_user_id", name="uq_gmm_msg_user"),
    )


# ----------------- Socket.IO Events -----------------
@socketio.on("connect")
def handle_socket_connect():
    user_id = session.get("user_id")
    if not user_id:
        return
    # Touch last_seen on connect
    try:
        u = db.session.get(User, user_id)
        if u:
            u.touch_last_seen()
            db.session.commit()
    except Exception:
        db.session.rollback()

    _mark_user_online(user_id)
    join_room(f"user_{user_id}")
    emit("presence_state", {"online_user_ids": list(online_users.keys())})

    # Mark undelivered direct messages to this user as delivered
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        undelivered = (
            Message.query
            .filter(Message.receiver_id == user_id)
            .filter(or_(Message.delivered_at == None, Message.delivered_at.is_(None)))  # noqa: E711
            .all()
        )
        if undelivered:
            by_sender: dict[int, list[int]] = {}
            for m in undelivered:
                # do not mark self-sent messages
                if m.sender_id == user_id:
                    continue
                m.delivered_at = now
                by_sender.setdefault(int(m.sender_id), []).append(int(m.id))
            db.session.commit()
            # notify senders about delivery
            for sid, ids in by_sender.items():
                socketio.emit(
                    "message_status",
                    {"type": "dm", "status": "delivered", "message_ids": ids, "at": _utc_iso(now)},
                    room=f"user_{sid}",
                )
    except Exception:
        db.session.rollback()

    # (delivery marking handled above)


@socketio.on("disconnect")
def handle_socket_disconnect():
    user_id = session.get("user_id")
    if not user_id:
        return
    try:
        u = db.session.get(User, user_id)
        if u:
            u.touch_last_seen()
            db.session.commit()
    except Exception:
        db.session.rollback()
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
        next_url = (request.args.get("next") or "").strip()
        # Basic open-redirect protection: only allow relative paths
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
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
    role = db.Column(db.String(20), default="member", index=True)  # owner / admin / member
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
    edited_at = db.Column(db.DateTime, nullable=True, index=True)
    deleted_for_all = db.Column(db.Boolean, default=False, index=True)
    reply_to_id = db.Column(db.Integer, nullable=True, index=True)
    message_kind = db.Column(db.String(20), default="user", index=True)  # user / system
    system_payload = db.Column(db.Text, nullable=True)

    group = db.relationship("Group", foreign_keys=[group_id])
    sender = db.relationship("User", foreign_keys=[sender_id])

    __table_args__ = (
        db.Index("ix_group_messages_group_ts", "group_id", "timestamp"),
    )


# ----------------- DB schema safety (no Alembic) -----------------
def _db_is_sqlite() -> bool:
    try:
        return db.engine.dialect.name == "sqlite"
    except Exception:
        return False


def _ensure_columns_sqlite(table: str, columns: list[tuple[str, str]]):
    """Add missing columns to an existing SQLite table.

    NOTE: SQLite supports ALTER TABLE ADD COLUMN (at the end). We keep defaults simple
    and NULL-friendly to avoid breaking running deployments.
    """
    try:
        rows = db.session.execute(db.text(f"PRAGMA table_info({table});")).fetchall()
        existing = {r[1] for r in rows}  # name is 2nd field
        for name, decl in columns:
            if name in existing:
                continue
            db.session.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {name} {decl};"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _ensure_columns_postgres(table: str, columns: list[tuple[str, str]]):
    try:
        for name, decl in columns:
            db.session.execute(db.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {decl};"))
        db.session.commit()
    except Exception:
        db.session.rollback()


_schema_checked = False

@app.before_request
def _ensure_schema_once():
    """Ensure new columns/tables exist without requiring a migration tool."""
    global _schema_checked
    if _schema_checked:
        return
    _schema_checked = True

    try:
        db.create_all()
    except Exception:
        # If create_all fails (permissions etc.), we still try to continue.
        pass

    # Add columns for Message (WhatsApp-like status/reply/forward/edit/delete)
    msg_cols = [
        ("delivered_at", "DATETIME"),
        ("read_at", "DATETIME"),
        ("edited_at", "DATETIME"),
        ("deleted_for_all", "BOOLEAN DEFAULT 0"),
        ("reply_to_id", "INTEGER"),
        ("forwarded", "BOOLEAN DEFAULT 0"),
    ]

    # SQLite vs Postgres declarations
    if _db_is_sqlite():
        _ensure_columns_sqlite("messages", msg_cols)
        # Group members role + group message extras
        _ensure_columns_sqlite("group_members", [("role", "TEXT DEFAULT 'member'")])
        _ensure_columns_sqlite("group_messages", [
            ("edited_at", "DATETIME"),
            ("deleted_for_all", "BOOLEAN DEFAULT 0"),
            ("reply_to_id", "INTEGER"),
            ("message_kind", "TEXT DEFAULT 'user'"),
            ("system_payload", "TEXT"),
        ])
    else:
        pg_cols = [
            ("delivered_at", "TIMESTAMP"),
            ("read_at", "TIMESTAMP"),
            ("edited_at", "TIMESTAMP"),
            ("deleted_for_all", "BOOLEAN DEFAULT FALSE"),
            ("reply_to_id", "INTEGER"),
            ("forwarded", "BOOLEAN DEFAULT FALSE"),
        ]
        _ensure_columns_postgres("messages", pg_cols)
        _ensure_columns_postgres("group_members", [("role", "TEXT DEFAULT 'member'")])
        _ensure_columns_postgres("group_messages", [
            ("edited_at", "TIMESTAMP"),
            ("deleted_for_all", "BOOLEAN DEFAULT FALSE"),
            ("reply_to_id", "INTEGER"),
            ("message_kind", "TEXT DEFAULT 'user'"),
            ("system_payload", "TEXT"),
        ])




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
        remember = (request.form.get("remember") or "").strip().lower() in {"1", "true", "on", "yes"}

        if not phone or not password:
            return render_template("login.html", error="جميع الحقول مطلوبة")

        user = User.query.filter_by(phone_number=phone).first()
        if not user or not user.check_password(password):
            return render_template("login.html", error="رقم الهاتف أو كلمة المرور غير صحيحة")

        session.clear()
        session["user_id"] = user.id
        session["phone_number"] = user.phone_number
        session["name"] = user.name
        # Keep the user logged in across browser restarts (without storing password).
        session.permanent = bool(remember)

        try:
            user.touch_last_seen()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

        return redirect(url_for("web_chat"))

    return render_template("login.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        phone = (request.form.get("phone") or "").strip()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not phone or not new_password or not confirm_password:
            return render_template("forgot_password.html", error="جميع الحقول مطلوبة")

        user = User.query.filter_by(phone_number=phone).first()
        if not user:
            return render_template("forgot_password.html", error="رقم الهاتف غير موجود")

        if len(new_password) < 8:
            return render_template("forgot_password.html", error="كلمة المرور يجب ألا تقل عن 8 أحرف")

        if new_password != confirm_password:
            return render_template("forgot_password.html", error="كلمتا المرور غير متطابقتين")

        try:
            user.set_password(new_password)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Forgot password error")
            return render_template("forgot_password.html", error="حدث خطأ أثناء تحديث كلمة المرور")

        return redirect(url_for("login", reset=1))

    return render_template("forgot_password.html")

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
        .outerjoin(dm_last_subq, dm_last_subq.c.other_id == User.id)
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

    
    # Attach conversation settings (pin/archive/mute) for current user
    conv_keys = []
    for c in conversations:
        if c.get("type") == "group":
            conv_keys.append(("group", int(c.get("group").id)))
        else:
            conv_keys.append(("dm", int(c.get("user").id)))
    settings_rows = (
        ConversationSetting.query
        .filter(ConversationSetting.user_id == user.id)
        .filter(
            or_(
                and_(ConversationSetting.conversation_type == "group", ConversationSetting.conversation_id.in_([cid for t, cid in conv_keys if t == "group"] or [-1])),
                and_(ConversationSetting.conversation_type == "dm", ConversationSetting.conversation_id.in_([cid for t, cid in conv_keys if t == "dm"] or [-1])),
            )
        )
        .all()
    )
    settings_map = {(r.conversation_type, int(r.conversation_id)): r for r in settings_rows}
    for c in conversations:
        if c.get("type") == "group":
            c["settings"] = settings_map.get(("group", int(c.get("group").id)))
        else:
            c["settings"] = settings_map.get(("dm", int(c.get("user").id)))

    # ترتيب مثل واتساب: المثبت أولاً (حسب pinned_rank) ثم حسب آخر رسالة، والأرشيف في الأسفل
    def _conv_name(it):
        if it.get("type") == "user":
            return ((it.get("user").name or "").lower(), int(it.get("user").id))
        return ((it.get("group").name or "").lower(), int(it.get("group").id))

    def _pinned_rank(it):
        s = it.get("settings")
        if s and s.pinned_rank is not None:
            return int(s.pinned_rank)
        return 10**9

    def _is_archived(it):
        s = it.get("settings")
        return bool(s and s.is_archived)

    conversations.sort(key=lambda it: (_conv_name(it)))
    conversations.sort(
        key=lambda it: (
            _is_archived(it),                 # archived last
            _pinned_rank(it),                 # pinned first
            not (it.get("ts") is not None),   # ts present first
            -(it.get("ts").timestamp() if it.get("ts") else 0.0),
        )
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


@app.route("/api/users/<int:user_id>/presence")
def api_user_presence(user_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    u = db.session.get(User, user_id)
    if not u:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({
        "ok": True,
        "user_id": user_id,
        "online": bool(user_id in online_users),
        "last_seen": _utc_iso(u.last_seen),
    })





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
        db.session.add(GroupMember(group_id=g.id, user_id=me.id, status="accepted", invited_by=me.id, role="owner"))

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


@app.route("/api/groups/<int:group_id>/members/<int:user_id>/promote", methods=["POST"])
def api_group_promote_member(group_id: int, user_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404
    my = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not my:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    # Owner can promote/demote; admins can only remove/ban in future (not here).
    if my.role != "owner":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target or target.status != "accepted":
        return jsonify({"ok": False, "error": "not_found"}), 404
    if target.role == "owner":
        return jsonify({"ok": False, "error": "cannot_change_owner"}), 400
    target.role = "admin"
    try:
        db.session.commit()
        # System message
        db.session.add(GroupMessage(group_id=group_id, sender_id=me.id, content=f"تمت ترقية العضو {user_id} إلى مشرف", message_type="system", message_kind="system"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500
    return jsonify({"ok": True})


@app.route("/api/groups/<int:group_id>/members/<int:user_id>/demote", methods=["POST"])
def api_group_demote_member(group_id: int, user_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    my = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not my or my.role != "owner":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    target = GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not target or target.status != "accepted":
        return jsonify({"ok": False, "error": "not_found"}), 404
    if target.role == "owner":
        return jsonify({"ok": False, "error": "cannot_change_owner"}), 400
    target.role = "member"
    try:
        db.session.commit()
        db.session.add(GroupMessage(group_id=group_id, sender_id=me.id, content=f"تمت إزالة صلاحيات المشرف عن العضو {user_id}", message_type="system", message_kind="system"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500
    return jsonify({"ok": True})


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


@app.route("/api/groups/<int:group_id>/invite_link", methods=["POST"])
def api_group_invite_link(group_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    g = db.session.get(Group, group_id)
    if not g:
        return jsonify({"ok": False, "error": "group_not_found"}), 404
    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    # Only owner/admin can create invite links
    if member.role not in {"owner", "admin"}:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    expires_seconds = data.get("expires_seconds")
    max_uses = data.get("max_uses")
    try:
        expires_at = None
        if expires_seconds:
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(expires_seconds))
        max_uses_i = int(max_uses) if max_uses is not None and str(max_uses).strip() != "" else None
    except Exception:
        expires_at = None
        max_uses_i = None

    import secrets
    token = secrets.token_urlsafe(32)
    try:
        row = GroupInviteLink(group_id=group_id, token=token, expires_at=expires_at, max_uses=max_uses_i, uses=0, created_by=me.id)
        db.session.add(row)
        db.session.commit()
        return jsonify({"ok": True, "token": token, "url": f"/join/{token}"})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/join/<string:token>")
def join_by_token(token: str):
    # If not logged in, redirect to login then back to join
    if not login_required():
        return redirect(url_for("login", next=url_for("join_by_token", token=token)))
    me = current_user()
    if not me:
        session.clear()
        return redirect(url_for("login", next=url_for("join_by_token", token=token)))

    link = GroupInviteLink.query.filter_by(token=token).first()
    if not link:
        return render_template("home.html", error="رابط الدعوة غير صالح")
    if link.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > link.expires_at:
        return render_template("home.html", error="انتهت صلاحية رابط الدعوة")
    if link.max_uses is not None and int(link.uses) >= int(link.max_uses):
        return render_template("home.html", error="تم استهلاك رابط الدعوة")

    g = db.session.get(Group, link.group_id)
    if not g:
        return render_template("home.html", error="المجموعة غير موجودة")

    try:
        gm = GroupMember.query.filter_by(group_id=g.id, user_id=me.id).first()
        if not gm:
            gm = GroupMember(group_id=g.id, user_id=me.id, status="accepted", invited_by=link.created_by, role="member")
            db.session.add(gm)
        else:
            gm.status = "accepted"
        link.uses = int(link.uses or 0) + 1
        db.session.commit()
        return redirect(url_for("web_chat", group=g.id))
    except Exception:
        db.session.rollback()
        return render_template("home.html", error="تعذر الانضمام للمجموعة")



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

    # Filter "delete for me" visibilities for group messages
    try:
        deleted_ids = {
            int(r[0]) for r in db.session.query(MessageVisibility.message_id)
            .filter_by(user_id=me.id, message_type="group", is_deleted_for_me=True)
            .all()
        }
    except Exception:
        deleted_ids = set()

    res = []
    for m in messages:
        if int(m.id) in deleted_ids:
            continue
        # Group read receipts (best-effort): mark read for this user
        try:
            if int(m.sender_id) != int(me.id):
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                rec = GroupMessageReceipt.query.filter_by(group_message_id=m.id, user_id=me.id).first()
                if not rec:
                    rec = GroupMessageReceipt(group_message_id=m.id, user_id=me.id, delivered_at=now, read_at=now)
                    db.session.add(rec)
                else:
                    if rec.delivered_at is None:
                        rec.delivered_at = now
                    if rec.read_at is None:
                        rec.read_at = now
        except Exception:
            pass
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


@app.route("/api/group_messages/<int:message_id>/delete_for_me", methods=["POST"])
def api_group_message_delete_for_me(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    msg = db.session.get(GroupMessage, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    member = GroupMember.query.filter_by(group_id=msg.group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        row = MessageVisibility.query.filter_by(message_type="group", message_id=message_id, user_id=me.id).first()
        if not row:
            db.session.add(MessageVisibility(message_type="group", message_id=message_id, user_id=me.id, is_deleted_for_me=True))
        else:
            row.is_deleted_for_me = True
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/send_group_message", methods=["POST"])
def send_group_message():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = current_user()
    if not me:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    group_id = (request.form.get("group_id") or "").strip()
    content = (request.form.get("content") or "").strip()
    reply_to_raw = (request.form.get("reply_to_id") or "").strip()
    try:
        group_id = int(group_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400

    member = GroupMember.query.filter_by(group_id=group_id, user_id=me.id, status="accepted").first()
    if not member:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    if not content:
        return jsonify({"ok": False, "error": "empty"}), 400

    # Optional reply_to_id: must exist inside the same group
    reply_to_id = None
    if reply_to_raw:
        try:
            rt = int(reply_to_raw)
            rm = db.session.get(GroupMessage, rt)
            if rm and int(rm.group_id) == int(group_id):
                reply_to_id = rt
        except Exception:
            reply_to_id = None

    msg = GroupMessage(group_id=group_id, sender_id=me.id, content=content, message_type="text", reply_to_id=reply_to_id)
    try:
        db.session.add(msg)
        db.session.commit()

        # Detect mentions in message content (e.g., @+201234567890)
        try:
            mentioned_users: set[int] = set()
            for ph in re.findall(r"@\+?[0-9]{10,15}", content or ""):
                phone = ph[1:]
                u = User.query.filter_by(phone_number=phone).first()
                if u and int(u.id) != int(me.id):
                    mentioned_users.add(int(u.id))
                    try:
                        db.session.add(GroupMessageMention(group_message_id=msg.id, mentioned_user_id=int(u.id)))
                    except Exception:
                        pass
            if mentioned_users:
                db.session.commit()
        except Exception:
            db.session.rollback()

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
                if not is_conversation_muted(mm.user_id, 'group', group_id):

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
                if not is_conversation_muted(mm.user_id, 'group', group_id):

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
                if not is_conversation_muted(mm.user_id, 'group', group_id):

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
                if not is_conversation_muted(mm.user_id, 'group', group_id):

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

    # Mark unread messages (received by me) as read + emit read receipts to sender
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        changed_ids_by_sender: dict[int, list[int]] = {}
        changed = False
        for m in msgs:
            if m.receiver_id == me and not m.is_read:
                m.is_read = True
                # keep read_at synced
                try:
                    m.read_at = now
                except Exception:
                    pass
                changed_ids_by_sender.setdefault(int(m.sender_id), []).append(int(m.id))
                changed = True
        if changed:
            db.session.commit()
            for sid, mids in changed_ids_by_sender.items():
                socketio.emit(
                    "message_status",
                    {"type": "dm", "status": "read", "message_ids": mids, "at": _utc_iso(now)},
                    room=f"user_{sid}",
                )
    except SQLAlchemyError:
        db.session.rollback()

    # Filter "delete for me" visibilities
    try:
        deleted_ids = {
            int(r[0]) for r in db.session.query(MessageVisibility.message_id)
            .filter_by(user_id=me, message_type="dm", is_deleted_for_me=True)
            .all()
        }
    except Exception:
        deleted_ids = set()

    out = []
    for m in msgs:
        if int(m.id) in deleted_ids:
            continue
        # If deleted for everyone, render a placeholder
        if bool(getattr(m, "deleted_for_all", False)):
            m = m  # keep id/timestamp
            placeholder = _serialize_message(m)
            placeholder["content"] = "تم حذف هذه الرسالة"
            placeholder["message_type"] = "deleted"
            out.append(placeholder)
        else:
            out.append(_serialize_message(m))

    return jsonify(out)


@app.route("/api/messages/<int:message_id>/delete_for_me", methods=["POST"])
def api_message_delete_for_me(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = session["user_id"]
    msg = db.session.get(Message, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # Only participants can delete
    if int(msg.sender_id) != int(me) and int(msg.receiver_id) != int(me):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        row = MessageVisibility.query.filter_by(message_type="dm", message_id=message_id, user_id=me).first()
        if not row:
            row = MessageVisibility(message_type="dm", message_id=message_id, user_id=me, is_deleted_for_me=True)
            db.session.add(row)
        else:
            row.is_deleted_for_me = True
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500
    return jsonify({"ok": True})


@app.route("/api/messages/<int:message_id>/delete_for_all", methods=["POST"])
def api_message_delete_for_all(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = session["user_id"]
    msg = db.session.get(Message, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    # Only sender can delete for all
    if int(msg.sender_id) != int(me):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        msg.deleted_for_all = True
        db.session.commit()
        # Notify both sides
        socketio.emit("message_deleted", {"type": "dm", "message_id": int(message_id), "deleted_for_all": True}, room=f"user_{msg.receiver_id}")
        socketio.emit("message_deleted", {"type": "dm", "message_id": int(message_id), "deleted_for_all": True}, room=f"user_{msg.sender_id}")
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500
    return jsonify({"ok": True})


def _get_or_create_conv_settings(user_id: int, conv_type: str, conv_id: int) -> ConversationSetting:
    row = ConversationSetting.query.filter_by(user_id=user_id, conversation_type=conv_type, conversation_id=conv_id).first()
    if not row:
        row = ConversationSetting(user_id=user_id, conversation_type=conv_type, conversation_id=conv_id)
        db.session.add(row)
    return row


@app.route("/api/conversations/<string:conv_type>/<int:conv_id>/settings", methods=["POST"])
def api_conversation_settings(conv_type: str, conv_id: int):
    """Set pinned/archive/mute. conv_type: dm|group"""
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if conv_type not in {"dm", "group"}:
        return jsonify({"ok": False, "error": "bad_type"}), 400
    me = int(session["user_id"])
    data = request.get_json(silent=True) or {}
    try:
        row = _get_or_create_conv_settings(me, conv_type, int(conv_id))
        if "is_archived" in data:
            row.is_archived = bool(data.get("is_archived"))
        if "pinned_rank" in data:
            pr = data.get("pinned_rank")
            row.pinned_rank = int(pr) if pr is not None and str(pr).strip() != "" else None
        if "muted_until" in data:
            mu = data.get("muted_until")
            if not mu:
                row.muted_until = None
            else:
                # accept ISO string or seconds
                if isinstance(mu, (int, float)):
                    row.muted_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(mu))
                else:
                    try:
                        row.muted_until = datetime.fromisoformat(str(mu).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        row.muted_until = None
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/messages/<int:message_id>/star", methods=["POST"])
def api_star_message(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    msg = db.session.get(Message, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if int(msg.sender_id) != me and int(msg.receiver_id) != me:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    enable = bool(data.get("enable", True))
    try:
        row = StarredMessage.query.filter_by(user_id=me, message_type="dm", message_id=message_id).first()
        if enable:
            if not row:
                db.session.add(StarredMessage(user_id=me, message_type="dm", message_id=message_id))
        else:
            if row:
                db.session.delete(row)
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/starred", methods=["GET"])
def api_get_starred():
    """Return recent starred messages (direct messages)."""
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    try:
        rows = (
            db.session.query(StarredMessage, Message)
            .join(Message, StarredMessage.message_id == Message.id)
            .filter(StarredMessage.user_id == me, StarredMessage.message_type == "dm")
            .order_by(Message.timestamp.desc(), Message.id.desc())
            .limit(200)
            .all()
        )
        out = []
        for sm, m in rows:
            # Compute chat name (other party)
            other_id = int(m.receiver_id) if int(m.sender_id) == me else int(m.sender_id)
            other = db.session.get(User, other_id)
            out.append({
                "message_id": int(m.id),
                "chat_name": (other.name if other else ""),
                "content": (m.content or ""),
                "timestamp": _utc_iso(m.timestamp),
            })
        return jsonify({"ok": True, "messages": out})
    except Exception:
        return jsonify({"ok": False, "error": "db_error"}), 500



@app.route("/api/search_messages", methods=["GET"])
def api_search_messages():
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    q = (request.args.get("q") or "").strip()
    conv_type = (request.args.get("type") or "").strip()  # dm|group
    conv_id_raw = (request.args.get("id") or "").strip()
    if not q or conv_type not in {"dm", "group"} or not conv_id_raw:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    try:
        conv_id = int(conv_id_raw)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400

    like = f"%{q}%"
    out = []
    try:
        if conv_type == "dm":
            # Only if participant
            other = db.session.get(User, conv_id)
            if not other:
                return jsonify({"ok": False, "error": "not_found"}), 404
            rows = (
                Message.query
                .filter(
                    or_(
                        and_(Message.sender_id == me, Message.receiver_id == conv_id),
                        and_(Message.sender_id == conv_id, Message.receiver_id == me),
                    )
                )
                .filter(Message.content.ilike(like))
                .order_by(Message.timestamp.desc(), Message.id.desc())
                .limit(50)
                .all()
            )
            for m in rows:
                out.append({
                    "type": "dm",
                    "id": int(m.id),
                    "content": (m.content or "")[:300],
                    "timestamp": _utc_iso(m.timestamp),
                    "sender_id": int(m.sender_id),
                })
        else:
            member = GroupMember.query.filter_by(group_id=conv_id, user_id=me, status="accepted").first()
            if not member:
                return jsonify({"ok": False, "error": "forbidden"}), 403
            rows = (
                GroupMessage.query
                .filter(GroupMessage.group_id == conv_id)
                .filter(GroupMessage.content.ilike(like))
                .order_by(GroupMessage.timestamp.desc(), GroupMessage.id.desc())
                .limit(50)
                .all()
            )
            for m in rows:
                out.append({
                    "type": "group",
                    "id": int(m.id),
                    "content": (m.content or "")[:300],
                    "timestamp": _utc_iso(m.timestamp),
                    "sender_id": int(m.sender_id),
                })
        return jsonify({"ok": True, "results": out})
    except Exception:
        return jsonify({"ok": False, "error": "db_error"}), 500



@app.route("/api/messages/<int:message_id>/forward", methods=["POST"])
def api_forward_message(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    msg = db.session.get(Message, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if int(msg.sender_id) != me and int(msg.receiver_id) != me:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    target_type = (data.get("target_type") or "").strip()
    target_id = data.get("target_id")
    try:
        target_id = int(target_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    try:
        if target_type == "dm":
            if not db.session.get(User, target_id):
                return jsonify({"ok": False, "error": "not_found"}), 404
            nm = Message(sender_id=me, receiver_id=target_id, content=msg.content, message_type=msg.message_type,
                         media_url=getattr(msg, "media_url", None), media_mime=getattr(msg, "media_mime", None),
                         forwarded=True)
            db.session.add(nm)
            db.session.commit()
            _emit_direct_message(nm)
            return jsonify({"ok": True, "message": _serialize_message(nm)})
        if target_type == "group":
            member = GroupMember.query.filter_by(group_id=target_id, user_id=me, status="accepted").first()
            if not member:
                return jsonify({"ok": False, "error": "forbidden"}), 403
            nm = GroupMessage(group_id=target_id, sender_id=me, content=msg.content, message_type=msg.message_type,
                             media_url=getattr(msg, "media_url", None), media_mime=getattr(msg, "media_mime", None),
                             message_kind="user")
            try:
                nm.forwarded = True  # type: ignore
            except Exception:
                pass
            db.session.add(nm)
            db.session.commit()
            _emit_group_message(nm)
            return jsonify({"ok": True, "message": _serialize_message(nm)})
        return jsonify({"ok": False, "error": "bad_type"}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/group_messages/<int:message_id>/forward", methods=["POST"])
def api_forward_group_message(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    msg = db.session.get(GroupMessage, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    member_src = GroupMember.query.filter_by(group_id=int(msg.group_id), user_id=me, status="accepted").first()
    if not member_src:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    target_type = (data.get("target_type") or "").strip()
    target_id = data.get("target_id")
    try:
        target_id = int(target_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad_request"}), 400
    try:
        if target_type == "dm":
            if not db.session.get(User, target_id):
                return jsonify({"ok": False, "error": "not_found"}), 404
            nm = Message(sender_id=me, receiver_id=target_id, content=msg.content, message_type=msg.message_type,
                         media_url=getattr(msg, "media_url", None), media_mime=getattr(msg, "media_mime", None),
                         forwarded=True)
            db.session.add(nm)
            db.session.commit()
            _emit_direct_message(nm)
            return jsonify({"ok": True, "message": _serialize_message(nm)})
        if target_type == "group":
            member_dst = GroupMember.query.filter_by(group_id=target_id, user_id=me, status="accepted").first()
            if not member_dst:
                return jsonify({"ok": False, "error": "forbidden"}), 403
            nm = GroupMessage(group_id=target_id, sender_id=me, content=msg.content, message_type=msg.message_type,
                             media_url=getattr(msg, "media_url", None), media_mime=getattr(msg, "media_mime", None),
                             message_kind="user")
            try:
                nm.forwarded = True  # type: ignore
            except Exception:
                pass
            db.session.add(nm)
            db.session.commit()
            _emit_group_message(nm)
            return jsonify({"ok": True, "message": _serialize_message(nm)})
        return jsonify({"ok": False, "error": "bad_type"}), 400
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/messages/<int:message_id>/edit", methods=["POST"])
def api_edit_message(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    msg = db.session.get(Message, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if int(msg.sender_id) != me:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "empty"}), 400
    if len(content) > 5000:
        return jsonify({"ok": False, "error": "too_long"}), 400
    try:
        msg.content = content
        msg.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        # broadcast updated message
        socketio.emit("message_edited", {"type": "dm", "message": _serialize_message(msg)}, room=f"user_{msg.sender_id}")
        socketio.emit("message_edited", {"type": "dm", "message": _serialize_message(msg)}, room=f"user_{msg.receiver_id}")
        return jsonify({"ok": True, "edited_at": _utc_iso(msg.edited_at)})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/group_messages/<int:message_id>/edit", methods=["POST"])
def api_edit_group_message(message_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    msg = db.session.get(GroupMessage, message_id)
    if not msg:
        return jsonify({"ok": False, "error": "not_found"}), 404
    if int(msg.sender_id) != me:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "empty"}), 400
    if len(content) > 5000:
        return jsonify({"ok": False, "error": "too_long"}), 400
    try:
        msg.content = content
        msg.edited_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
        socketio.emit("message_edited", {"type": "group", "message": _serialize_message(msg)}, room=f"group_{msg.group_id}")
        return jsonify({"ok": True, "edited_at": _utc_iso(msg.edited_at)})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/api/groups/<int:group_id>/members", methods=["GET"])
def api_group_members(group_id: int):
    if not login_required():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    me = int(session["user_id"])
    m = GroupMember.query.filter_by(group_id=group_id, user_id=me, status="accepted").first()
    if not m:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    try:
        members = (
            db.session.query(GroupMember, User)
            .join(User, GroupMember.user_id == User.id)
            .filter(GroupMember.group_id == group_id, GroupMember.status == "accepted")
            .order_by(GroupMember.role.desc(), User.name.asc())
            .all()
        )
        out = []
        for gm, u in members:
            out.append({
                "user_id": int(u.id),
                "name": u.name,
                "phone_number": u.phone_number,
                "role": gm.role,
            })
        return jsonify({"ok": True, "members": out, "my_role": m.role})
    except Exception:
        return jsonify({"ok": False, "error": "db_error"}), 500


@app.route("/send_message", methods=["POST"])
def send_message():
    if not login_required():
        return jsonify({"error": "غير مسموح"}), 401

    sender_id = session["user_id"]
    receiver_id_raw = request.form.get("receiver_id", "").strip()
    content = (request.form.get("content") or "").strip()
    reply_to_raw = (request.form.get("reply_to_id") or "").strip()

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

    # Validate reply_to_id (optional) - must belong to the same conversation
    reply_to_id = None
    if reply_to_raw:
        try:
            rt = int(reply_to_raw)
            rm = db.session.get(Message, rt)
            if rm and (
                (rm.sender_id == sender_id and rm.receiver_id == receiver_id) or
                (rm.sender_id == receiver_id and rm.receiver_id == sender_id)
            ):
                reply_to_id = rt
        except Exception:
            reply_to_id = None

    try:
        msg = Message(sender_id=sender_id, receiver_id=receiver_id, content=content, reply_to_id=reply_to_id)
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
            if not is_conversation_muted(receiver_id, 'dm', sender_id):

                send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({"status": "ok", "message": _serialize_message(msg)})
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
            if not is_conversation_muted(receiver_id, 'dm', sender_id):

                send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({"status": "ok", "message": _serialize_message(msg)})
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
            if not is_conversation_muted(receiver_id, 'dm', sender_id):

                send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({"status": "ok", "message": _serialize_message(msg)})
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
            if not is_conversation_muted(receiver_id, 'dm', sender_id):

                send_push_to_user(receiver_id, payload)
        except Exception:
            pass

        _emit_direct_message(msg)

        return jsonify({"status": "ok", "message": _serialize_message(msg)})
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
    last_ts_groups = {}
    try:
        group_counts, last_ts_groups = _load_group_activity(uid)
    except Exception:
        group_counts = {}
        last_ts_groups = {}

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
    last_ts_groups = {}
    try:
        group_counts, last_ts_groups = _load_group_activity(uid)
    except Exception:
        group_counts = {}
        last_ts_groups = {}

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
