
from flask_wtf import FlaskForm
from wtforms import DateTimeLocalField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import InputRequired, Length, NumberRange, Optional

from .models import db


def string(label, length, required=True, multiline=False):
    field = TextAreaField if multiline else StringField
    return field(label, filters=[lambda value: (value or '').strip()],
                 validators=[InputRequired() if required else Optional(),
                             Length(min=int(required), max=length)])


def number(label, low, high, required=True):
    return IntegerField(label, validators=[InputRequired() if required else Optional(), NumberRange(low, high)])


def select(label, choices=()):
    return SelectField(label, choices=list(choices), coerce=int, validators=[InputRequired()])


def datetime_field(label):
    return DateTimeLocalField(label, format='%Y-%m-%dT%H:%M', validators=[InputRequired()])


def choices(stmt, optional=False, selected=None):
    # Keep options bounded and retain an existing selection while editing.
    rows = [tuple(row) for row in db.session.execute(stmt.limit(250))]
    if selected is not None and not any(ident == selected for ident, _ in rows):
        alias = stmt.order_by(None).subquery()
        rows.extend(tuple(row) for row in db.session.execute(db.select(alias).where(list(alias.c)[0] == selected)))
    return ([(0, 'Nenhum')] if optional else []) + rows


class MinistryForm(FlaskForm):
    name = string('Nome', 100)
    description = string('Descrição', 2000, required=False, multiline=True)


class EventForm(FlaskForm):
    ministry_id = select('Ministério')
    title = string('Título', 100)
    starts = datetime_field('Início')
    ends = datetime_field('Fim')
    location = string('Local', 120, required=False)
    notes = string('Observações', 2000, required=False, multiline=True)


class ShiftForm(FlaskForm):
    event_id = select('Evento')
    user_id = select('Participante')
    duty = string('Função', 100)


class SongForm(FlaskForm):
    ministry_id = select('Ministério')
    title = string('Título', 100)
    artist = string('Artista', 100, required=False)
    musical_key = string('Tom', 12, required=False)
    bpm = number('BPM', 1, 400, required=False)
    category = string('Categoria', 60, required=False)
    notes = string('Observações', 2000, required=False, multiline=True)


class RepertoireForm(FlaskForm):
    event_id = select('Evento')
    song_id = select('Música')
    position = number('Posição', 1, 500)


class EquipmentForm(FlaskForm):
    ministry_id = select('Ministério')
    name = string('Nome', 100)
    state = SelectField('Estado', choices=['Disponível', 'Em manutenção', 'Indisponível'])
    notes = string('Observações', 2000, required=False, multiline=True)


class EquipmentLogForm(FlaskForm):
    equipment_id = select('Equipamento')
    event_id = select('Evento (opcional)')
    kind = SelectField('Tipo', choices=['Ocorrência', 'Manutenção', 'Checklist realizado', 'Checklist pendente'])
    notes = string('Descrição', 2000, multiline=True)


class NoticeForm(FlaskForm):
    ministry_id = select('Ministério')
    user_id = select('Destinatário individual (opcional)')
    title = string('Título', 100)
    body = string('Mensagem', 3000, multiline=True)
