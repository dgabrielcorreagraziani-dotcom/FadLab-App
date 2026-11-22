import hashlib
import hmac
import secrets
import time

from flask import abort, current_app, has_request_context, request, session
from flask_login import current_user
from sqlalchemy import false
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import Audit, Membership, RateWindow, db


def token():
    return secrets.token_urlsafe(32)


def is_admin():
    return current_user.is_authenticated and current_user.role == 'ADMIN'


def ministry_ids(write=False):
    stmt = db.select(Membership.ministry_id).where(Membership.user_id == current_user.id)
    if write:
        stmt = stmt.where(Membership.coordinates.is_(True))
    return stmt


def scope(stmt, column, write=False):
    if is_admin():
        return stmt
    if write and current_user.role != 'COORDINATOR':
        return stmt.where(false())
    return stmt.where(column.in_(ministry_ids(write)))


def require_admin():
    if not is_admin():
        abort(403)


def require_ministry(ministry_id, write=False):
    from .models import Ministry

    if db.session.scalar(scope(db.select(Ministry.id), Ministry.id, write).where(Ministry.id == ministry_id)) is None:
        abort(404)


def lock_mutations():
    # Serialize administrative and scheduling writes across serverless workers.
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(db.text('SELECT pg_advisory_xact_lock(7211093301)'))
    if has_request_context() and current_user.is_authenticated:
        user = current_user._get_current_object()
        db.session.refresh(user)
        if not user.enabled or user.session_key != session.get('_user_id'):
            abort(403)


def audit(action, resource, ident=None, actor=None):
    if actor is None and current_user.is_authenticated:
        actor = current_user.id
    db.session.add(Audit(action=action, resource=resource, resource_id=ident, actor_id=actor))


def rate_limit(action, identity, maximum, seconds=900):
    try:
        secret = current_app.secret_key.encode()
    except AttributeError:
        secret = current_app.secret_key
    key = hmac.new(secret, f'{action}:{identity}'.encode(), hashlib.sha256).hexdigest()
    window = int(time.time()) // seconds * seconds
    insert = postgres_insert if db.engine.dialect.name == 'postgresql' else sqlite_insert
    stmt = insert(RateWindow).values(key=key, window=window, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=['key', 'window'], set_={'count': RateWindow.count + 1}
    ).returning(RateWindow.count)
    count = db.session.scalar(stmt)
    db.session.commit()
    if count > maximum:
        abort(429)


def client_ip():
    if current_app.config['VERCEL_EDGE']:
        # Vercel supplies the client address and overwrites this header.
        return request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or 'unknown'
    return request.remote_addr or 'unknown'
