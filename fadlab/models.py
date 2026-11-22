from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, true

db = SQLAlchemy()
ROLES = ('USER', 'COORDINATOR', 'ADMIN')


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default='USER')
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    session_key = db.Column(db.String(64), nullable=False, unique=True)
    theme = db.Column(db.String(8), nullable=False, default='system')
    avatar_key = db.Column(db.String(96), unique=True, index=True)
    show_notice_highlights = db.Column(db.Boolean, nullable=False, default=True, server_default=true())
    __table_args__ = (CheckConstraint("role IN ('ADMIN','COORDINATOR','USER')", name='ck_user_role'),)

    @property
    def is_active(self):
        return self.enabled

    def get_id(self):
        return self.session_key


class Ministry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False, default='')


class Membership(db.Model):
    ministry_id = db.Column(db.ForeignKey('ministry.id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True, index=True)
    coordinates = db.Column(db.Boolean, nullable=False, default=False)
    ministry = db.relationship(Ministry, lazy='joined')
    user = db.relationship(User, lazy='joined')


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ministry_id = db.Column(db.ForeignKey('ministry.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    starts = db.Column(db.DateTime, nullable=False, index=True)
    ends = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(120), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')
    ministry = db.relationship(Ministry, lazy='joined')
    __table_args__ = (CheckConstraint('ends > starts', name='ck_event_times'),)


class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    duty = db.Column(db.String(100), nullable=False)
    event = db.relationship(Event, lazy='joined')
    user = db.relationship(User, lazy='joined')
    __table_args__ = (UniqueConstraint('event_id', 'user_id', name='uq_shift_event_user'),)


class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ministry_id = db.Column(db.ForeignKey('ministry.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False, index=True)
    artist = db.Column(db.String(100), nullable=False, default='')
    musical_key = db.Column(db.String(12), nullable=False, default='')
    bpm = db.Column(db.Integer)
    category = db.Column(db.String(60), nullable=False, default='')
    notes = db.Column(db.Text, nullable=False, default='')


class Repertoire(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False, index=True)
    song_id = db.Column(db.ForeignKey('song.id', ondelete='CASCADE'), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    event = db.relationship(Event, lazy='joined')
    song = db.relationship(Song, lazy='joined')
    __table_args__ = (UniqueConstraint('event_id', 'position', name='uq_repertoire_position'),)


class Equipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ministry_id = db.Column(db.ForeignKey('ministry.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    state = db.Column(db.String(32), nullable=False)
    notes = db.Column(db.Text, nullable=False, default='')


class EquipmentLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.ForeignKey('equipment.id', ondelete='CASCADE'), nullable=False, index=True)
    event_id = db.Column(db.ForeignKey('event.id', ondelete='SET NULL'), index=True)
    kind = db.Column(db.String(32), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    equipment = db.relationship(Equipment, lazy='joined')
    event = db.relationship(Event, lazy='joined')


class Notice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ministry_id = db.Column(db.ForeignKey('ministry.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.ForeignKey('user.id', ondelete='CASCADE'), index=True)
    title = db.Column(db.String(100), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    user = db.relationship(User, lazy='joined')


class NoticeRead(db.Model):
    notice_id = db.Column(db.ForeignKey('notice.id', ondelete='CASCADE'), primary_key=True)
    user_id = db.Column(db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)


class Audit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.ForeignKey('user.id', ondelete='SET NULL'), index=True)
    action = db.Column(db.String(32), nullable=False)
    resource = db.Column(db.String(32), nullable=False)
    resource_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)


class RateWindow(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    window = db.Column(db.Integer, primary_key=True, index=True)
    count = db.Column(db.Integer, nullable=False)


class PasswordReset(db.Model):
    digest = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    expires = db.Column(db.DateTime, nullable=False, index=True)


class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    registration_enabled = db.Column(db.Boolean, nullable=False)
    __table_args__ = (CheckConstraint('id = 1', name='ck_site_settings_singleton'),)
