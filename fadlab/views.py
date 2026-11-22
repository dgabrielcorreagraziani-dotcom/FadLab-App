from calendar import Calendar
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import exists, false, or_
from sqlalchemy.exc import IntegrityError

from . import forms as f
from .models import (
    ROLES, Audit, Equipment, EquipmentLog, Event, Membership, Ministry, Notice,
    NoticeRead, Repertoire, Shift, SiteSettings, Song, User, db,
)
from .security import audit, is_admin, lock_mutations, rate_limit, require_admin, scope, token

bp = Blueprint('views', __name__)
LOCAL_TZ = ZoneInfo('America/Sao_Paulo')


@dataclass(frozen=True)
class Module:
    label: str
    singular: str
    icon: str
    model: type
    form: type
    order: object
    fields: tuple
    searches: tuple = ()


MODULES = {
    'agenda': Module('Agenda', 'evento', 'calendar', Event, f.EventForm, Event.starts,
                     ('title', 'starts', 'ends', 'location', 'ministry.name', 'notes'), (Event.title, Event.location)),
    'escalas': Module('Escalas', 'escala', 'users', Shift, f.ShiftForm, Shift.id.desc(),
                      ('event.title', 'user.name', 'duty', 'event.starts')),
    'ministerios': Module('Ministérios', 'ministério', 'layers', Ministry, f.MinistryForm, Ministry.name,
                          ('name', 'description'), (Ministry.name,)),
    'louvor': Module('Louvor', 'música', 'music', Song, f.SongForm, Song.title,
                     ('title', 'artist', 'musical_key', 'bpm', 'category', 'notes'), (Song.title, Song.artist, Song.category)),
    'repertorios': Module('Repertórios', 'item do repertório', 'music', Repertoire, f.RepertoireForm,
                         (Repertoire.event_id, Repertoire.position), ('event.title', 'song.title', 'position')),
    'sonoplastia': Module('Sonoplastia', 'equipamento', 'headphones', Equipment, f.EquipmentForm, Equipment.name,
                          ('name', 'state', 'notes'), (Equipment.name,)),
    'historico': Module('Ocorrências e checklists', 'registro', 'headphones', EquipmentLog, f.EquipmentLogForm,
                        EquipmentLog.created_at.desc(), ('equipment.name', 'kind', 'event.title', 'notes')),
    'avisos': Module('Avisos', 'aviso', 'bell', Notice, f.NoticeForm, Notice.created_at.desc(),
                    ('title', 'body', 'created_at'), (Notice.title, Notice.body)),
}
FIELD_LABELS = {
    'starts': 'Início', 'ends': 'Fim', 'location': 'Local', 'ministry.name': 'Ministério',
    'notes': 'Observações', 'user.name': 'Participante', 'duty': 'Função', 'event.starts': 'Início',
    'event.title': 'Evento', 'musical_key': 'Tom', 'bpm': 'BPM', 'category': 'Categoria',
    'artist': 'Artista', 'song.title': 'Música', 'position': 'Ordem', 'state': 'Estado',
    'kind': 'Tipo', 'body': 'Mensagem', 'created_at': 'Enviado', 'equipment.name': 'Equipamento',
    'description': 'Descrição',
}


def get_module(key):
    if key not in MODULES:
        abort(404)
    return MODULES[key]


def query(model, write=False):
    stmt = db.select(model)
    if model is Ministry:
        if write and not is_admin():
            return stmt.where(false())
        return scope(stmt, Ministry.id)
    if model in (Shift, Repertoire):
        stmt = scope(stmt.join(Event), Event.ministry_id, write)
        if model is Shift and not write and not is_admin():
            managed = (Membership.ministry_id == Event.ministry_id) & (Membership.user_id == current_user.id)
            is_manager = exists(db.select(Membership.user_id).where(managed, Membership.coordinates.is_(True)))
            visible = Shift.user_id == current_user.id
            if current_user.role == 'COORDINATOR':
                visible = visible | is_manager
            stmt = stmt.where(visible)
        return stmt
    if model is EquipmentLog:
        return scope(stmt.join(Equipment), Equipment.ministry_id, write)
    stmt = scope(stmt, model.ministry_id, write)
    if model is Notice and not write and not is_admin():
        visible = or_(Notice.user_id.is_(None), Notice.user_id == current_user.id)
        if current_user.role == 'COORDINATOR':
            visible = visible | Notice.ministry_id.in_(scope(db.select(Ministry.id), Ministry.id, True))
        stmt = stmt.where(visible)
    return stmt


def lookup(model, ident, write=False):
    return db.session.scalar(query(model, write).where(model.id == ident)) or abort(404)


def can_write():
    return is_admin() or (
        current_user.role == 'COORDINATOR'
        and db.session.scalar(scope(db.select(Ministry.id), Ministry.id, True).limit(1)) is not None
    )


def field_value(item, path):
    value = item
    for key in path.split('.'):
        value = getattr(value, key, None)
    return value.strftime('%d/%m/%Y %H:%M') if isinstance(value, datetime) else value


def page(stmt):
    selected = min(10000, max(1, request.args.get('page', 1, type=int)))
    return db.paginate(stmt, page=selected, per_page=20, max_per_page=20, error_out=False)


def text_filter(stmt, columns, term):
    if term and columns:
        return stmt.where(or_(*(col.icontains(term, autoescape=True) for col in columns)))
    return stmt


@bp.get('/')
@login_required
def dashboard():
    current = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    upcoming = db.session.scalars(query(Event).where(Event.ends >= current).order_by(Event.starts).limit(5)).all()
    shifts = db.session.scalars(query(Shift).where(Shift.user_id == current_user.id, Event.ends >= current)
                               .order_by(Event.starts).limit(5)).all()
    unread = query(Notice).where(~exists(db.select(NoticeRead.notice_id).where(
        NoticeRead.notice_id == Notice.id, NoticeRead.user_id == current_user.id)))
    notices = db.session.scalars(unread.order_by(Notice.created_at.desc()).limit(5)).all() \
        if current_user.show_notice_highlights else []
    pending = db.session.scalars(query(EquipmentLog).where(EquipmentLog.kind == 'Checklist pendente')
                                 .order_by(EquipmentLog.created_at.desc()).limit(5)).all()
    return render_template('dashboard.html', upcoming=upcoming, shifts=shifts, notices=notices, pending=pending)


@bp.get('/painel/<key>')
@login_required
def listing(key):
    module = get_module(key)
    term = request.args.get('q', '').strip()[:100]
    stmt = text_filter(query(module.model), module.searches, term)
    order = module.order if isinstance(module.order, tuple) else (module.order,)
    stmt = stmt.order_by(*order, module.model.id)
    calendar = None
    if key == 'escalas':
        period = request.args.get('period')
        current = datetime.now(LOCAL_TZ).replace(tzinfo=None)
        if period == 'upcoming':
            stmt = stmt.where(Event.ends >= current)
        elif period == 'past':
            stmt = stmt.where(Event.ends < current)
        if request.args.get('mine') == 'yes':
            stmt = stmt.where(Shift.user_id == current_user.id)
    if key == 'agenda':
        ministry_id = request.args.get('ministry', type=int)
        if ministry_id:
            stmt = stmt.where(Event.ministry_id == ministry_id)
        if request.args.get('view') == 'calendar':
            selected = request.args.get('month', datetime.now(LOCAL_TZ).strftime('%Y-%m'))
            try:
                first = date.fromisoformat(selected + '-01')
                following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            except ValueError:
                abort(400)
            entries = db.session.scalars(stmt.where(Event.starts >= datetime.combine(first, datetime.min.time()),
                                                   Event.starts < datetime.combine(following, datetime.min.time())).limit(250))
            days = {}
            for entry in entries:
                days.setdefault(entry.starts.day, []).append(entry)
            calendar = (selected, Calendar(firstweekday=6).monthdayscalendar(first.year, first.month), days)
    permissions = set()
    result = page(stmt) if calendar is None else None
    if result and can_write():
        ids = [item.id for item in result.items]
        permissions = set(db.session.scalars(query(module.model, True).where(module.model.id.in_(ids))
                                             .with_only_columns(module.model.id)))
    read_ids = set()
    if key == 'avisos' and result:
        read_ids = set(db.session.scalars(db.select(NoticeRead.notice_id).where(
            NoticeRead.user_id == current_user.id, NoticeRead.notice_id.in_([i.id for i in result.items]))))
    return render_template('listing.html', title=module.label, module=module, key=key, result=result,
                           labels=FIELD_LABELS, value=field_value, editable=permissions, read_ids=read_ids,
                           can_add=is_admin() if key == 'ministerios' else can_write(), term=term, calendar=calendar,
                           ministries=f.choices(query(Ministry).with_only_columns(Ministry.id, Ministry.name)
                                                .order_by(Ministry.name)) if key == 'agenda' else [])


def member_options(ministry_id, selected=None):
    stmt = db.select(User.id, User.name).join(Membership).where(
        Membership.ministry_id == ministry_id, User.enabled.is_(True)).order_by(User.name, User.id)
    return f.choices(stmt, selected=selected)


def populate_options(form, item):
    def selected(field):
        if item:
            return getattr(item, field, None)
        return request.values.get(field, type=int)

    event_id = request.form.get('event_id', type=int) or selected('event_id')
    ministry_id = request.form.get('ministry_id', type=int) or selected('ministry_id')
    events = query(Event, True).with_only_columns(Event.id, Event.title).order_by(Event.starts.desc())
    ministries = query(Ministry).with_only_columns(Ministry.id, Ministry.name).order_by(Ministry.name)
    ministries = scope(ministries, Ministry.id, True)
    if hasattr(form, 'ministry_id'):
        form.ministry_id.choices = f.choices(ministries, selected=ministry_id)
        if item:
            # Moving records would invalidate existing member and event references.
            form.ministry_id.choices = [choice for choice in form.ministry_id.choices if choice[0] == item.ministry_id]
    if hasattr(form, 'event_id'):
        form.event_id.choices = f.choices(events, optional=isinstance(form, f.EquipmentLogForm), selected=event_id)
    if hasattr(form, 'equipment_id'):
        equipment = query(Equipment, True).with_only_columns(Equipment.id, Equipment.name).order_by(Equipment.name)
        form.equipment_id.choices = f.choices(equipment, selected=selected('equipment_id'))
    if isinstance(form, (f.ShiftForm, f.RepertoireForm)) and event_id:
        event = db.session.scalar(query(Event, True).where(Event.id == event_id))
        ministry_id = event.ministry_id if event else None
    if isinstance(form, f.NoticeForm) and ministry_id:
        ministry_id = db.session.scalar(scope(db.select(Ministry.id), Ministry.id, True)
                                        .where(Ministry.id == ministry_id))
    if hasattr(form, 'user_id'):
        options = member_options(ministry_id, selected('user_id')) if ministry_id else []
        if isinstance(form, f.NoticeForm):
            options = [(0, 'Todo o ministério')] + options
        form.user_id.choices = options
    if hasattr(form, 'song_id'):
        songs = query(Song, True).where(Song.ministry_id == ministry_id).with_only_columns(Song.id, Song.title)
        form.song_id.choices = f.choices(songs.order_by(Song.title), selected=selected('song_id'))


def overlap(user_id, starts, ends, event_id):
    return db.select(Shift.id).join(Event).where(
        Shift.user_id == user_id, Event.id != event_id, Event.starts < ends, Event.ends > starts)


def validate_relations(form, item):
    if isinstance(form, f.EventForm):
        if form.ends.data <= form.starts.data:
            return 'O fim deve ocorrer depois do início.'
        if item:
            users = db.select(Shift.user_id).where(Shift.event_id == item.id)
            conflicts = overlap(Shift.user_id, form.starts.data, form.ends.data, item.id).where(Shift.user_id.in_(users))
            if db.session.scalar(conflicts.limit(1)):
                return 'O novo horário coincide com outra escala de um participante.'
    if isinstance(form, f.ShiftForm):
        event = lookup(Event, form.event_id.data, True)
        if db.session.scalar(overlap(form.user_id.data, event.starts, event.ends, event.id).limit(1)):
            return 'Este participante já está escalado nesse horário.'
    if isinstance(form, f.EquipmentLogForm) and form.event_id.data:
        event = lookup(Event, form.event_id.data, True)
        equipment = lookup(Equipment, form.equipment_id.data, True)
        if event.ministry_id != equipment.ministry_id:
            return 'Selecione um evento do mesmo ministério do equipamento.'
    return None


@bp.route('/painel/<key>/novo', methods=['GET', 'POST'])
@bp.route('/painel/<key>/<int:ident>/editar', methods=['GET', 'POST'])
@login_required
def edit(key, ident=None):
    module = get_module(key)
    if request.method == 'POST':
        rate_limit('write', current_user.id, 120)
        lock_mutations()
    if not can_write() or (key == 'ministerios' and not is_admin()):
        abort(403)
    item = lookup(module.model, ident, True) if ident is not None else None
    form = module.form(obj=item)
    # Recompute dependent choices from the submitted ministry or event.
    populate_options(form, item)
    if request.method == 'POST' and request.form.get('refresh') != '1' and form.validate():
        message = validate_relations(form, item)
        if message:
            flash(message, 'error')
        else:
            obj = item or module.model()
            for name, field in form._fields.items():
                if name != 'csrf_token':
                    setattr(obj, name, None if name in {'user_id', 'event_id'} and field.data == 0 else field.data)
            try:
                db.session.add(obj)
                db.session.flush()
                audit('update' if item else 'create', key, obj.id)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash('Já existe um registro com os dados informados ou a referência foi alterada.', 'error')
            else:
                flash('Registro salvo.', 'success')
                return redirect(url_for('views.listing', key=key))
    return render_template('edit.html', title=('Editar ' if item else 'Adicionar ') + module.singular,
                           form=form, key=key, item=item)


@bp.post('/painel/<key>/<int:ident>/excluir')
@login_required
def delete(key, ident):
    module = get_module(key)
    rate_limit('write', current_user.id, 120)
    lock_mutations()
    item = lookup(module.model, ident, True)
    if request.form.get('confirm') != 'yes':
        abort(400)
    db.session.delete(item)
    audit('delete', key, ident)
    db.session.commit()
    flash('Registro excluído.', 'success')
    return redirect(url_for('views.listing', key=key))


@bp.post('/avisos/<int:ident>/lido')
@login_required
def mark_read(ident):
    rate_limit('read-notice', current_user.id, 120)
    lookup(Notice, ident)
    if not db.session.get(NoticeRead, (ident, current_user.id)):
        db.session.add(NoticeRead(notice_id=ident, user_id=current_user.id))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    return redirect(url_for('views.listing', key='avisos'))


@bp.get('/membros')
@login_required
def members():
    term = request.args.get('q', '').strip()[:100]
    stmt = db.select(User).where(User.enabled.is_(True))
    if not is_admin():
        ids = db.select(Membership.user_id).where(
            Membership.ministry_id.in_(scope(db.select(Ministry.id), Ministry.id)))
        stmt = stmt.where(User.id.in_(ids))
    ministry_id = request.args.get('ministry', type=int)
    if ministry_id:
        permitted = scope(db.select(Membership.user_id), Membership.ministry_id)
        stmt = stmt.where(User.id.in_(permitted.where(Membership.ministry_id == ministry_id)))
    stmt = text_filter(stmt, (User.name,), term).order_by(User.name, User.id)
    options = f.choices(query(Ministry).with_only_columns(Ministry.id, Ministry.name).order_by(Ministry.name))
    return render_template('members.html', result=page(stmt), term=term, ministries=options)


@bp.route('/administracao', methods=['GET', 'POST'])
@login_required
def admin():
    require_admin()
    term = request.args.get('q', '').strip()[:100]
    if request.method == 'POST':
        rate_limit('admin', current_user.id, 60)
        lock_mutations()
        require_admin()
        action = request.form.get('action')
        if action == 'settings':
            enabled = request.form.get('registration') == 'yes'
            db.session.merge(SiteSettings(id=1, registration_enabled=enabled))
            audit('update_settings', 'registration', 1)
            db.session.commit()
            flash('Configurações salvas.', 'success')
            return redirect(url_for('views.admin'))
        user_id = request.form.get('user_id', type=int)
        user = db.session.get(User, user_id) if user_id else None
        if not user:
            abort(404)
        if action == 'rename':
            name = request.form.get('name', '').strip()
            if not name or len(name) > 100:
                abort(400)
            user.name = name
            audit('update_member', 'user', user.id)
        elif action == 'role':
            role = request.form.get('role')
            enabled = request.form.get('enabled') == 'yes'
            if role not in ROLES or user.id == current_user.id:
                abort(400)
            if user.role != role or user.enabled != enabled:
                user.role, user.enabled, user.session_key = role, enabled, token()
                if role != 'COORDINATOR':
                    db.session.execute(db.update(Membership).where(Membership.user_id == user.id)
                                       .values(coordinates=False))
                audit('change_role', 'user', user.id)
        elif action in {'membership', 'remove_membership'}:
            ministry_id = request.form.get('ministry_id', type=int)
            if not ministry_id or not db.session.get(Ministry, ministry_id):
                abort(404)
            membership = db.session.get(Membership, (ministry_id, user.id))
            if action == 'remove_membership':
                if membership:
                    scheduled = db.select(Shift).join(Event).where(Shift.user_id == user.id,
                                                                  Event.ministry_id == ministry_id)
                    if db.session.scalar(scheduled.limit(1)):
                        flash('Remova as escalas vinculadas antes de remover a participação.', 'error')
                        return redirect(url_for('views.admin'))
                    db.session.delete(membership)
                    audit('remove_member', 'ministry', ministry_id)
            else:
                coordinates = request.form.get('coordinates') == 'yes'
                if coordinates and user.role != 'COORDINATOR':
                    abort(400)
                if membership is None:
                    membership = Membership(ministry_id=ministry_id, user_id=user.id)
                    db.session.add(membership)
                membership.coordinates = coordinates
                audit('set_coordinator' if coordinates else 'add_member', 'ministry', ministry_id)
        else:
            abort(400)
        db.session.commit()
        flash('Alteração aplicada.', 'success')
        return redirect(url_for('views.admin'))
    users = page(text_filter(db.select(User), (User.name,), term).order_by(User.name, User.id))
    memberships = db.session.scalars(db.select(Membership).where(
        Membership.user_id.in_([u.id for u in users.items])).order_by(Membership.ministry_id)).all()
    groups = {}
    for membership in memberships:
        groups.setdefault(membership.user_id, []).append(membership)
    from .auth import registration_enabled
    return render_template('admin.html', result=users, roles=ROLES, memberships=groups,
                           registration_open=registration_enabled(),
                           ministries=f.choices(db.select(Ministry.id, Ministry.name).order_by(Ministry.name)), term=term)


@bp.get('/administracao/auditoria')
@login_required
def audit_log():
    require_admin()
    return render_template('audit.html', result=page(db.select(Audit).order_by(Audit.id.desc())))


@bp.get('/pesquisa')
@login_required
def search():
    term = request.args.get('q', '').strip()[:100]
    result = {}
    if len(term) >= 2:
        for key, module in MODULES.items():
            if module.searches:
                stmt = text_filter(query(module.model), module.searches, term)
                result[key] = list(db.session.scalars(stmt.order_by(module.model.id).limit(5)))
    return render_template('search.html', result=result, term=term, value=field_value)


@bp.get('/mais')
@login_required
def more():
    return render_template('more.html')
