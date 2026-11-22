import hashlib
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from ssl import create_default_context

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from .models import PasswordReset, SiteSettings, User, db, now
from .security import audit, client_ip, lock_mutations, rate_limit, token

bp = Blueprint('auth', __name__)
DUMMY_PASSWORD = generate_password_hash(token())
THEMES = {'system': 'Sistema', 'light': 'Claro', 'dark': 'Escuro'}


def normalize_email(value):
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        return None


def password_valid(value):
    return 12 <= len(value) <= 128


def by_email(email):
    return db.session.scalar(db.select(User).where(User.email == email))


def start_session(user):
    session.clear()
    session.permanent = True
    login_user(user)


@bp.route('/entrar', methods=['GET', 'POST'])
def sign_in():
    if current_user.is_authenticated:
        return redirect(url_for('views.dashboard'))
    if request.method == 'POST':
        rate_limit('login-ip', client_ip(), 40)
        email = normalize_email(request.form.get('email', ''))
        rate_limit('login-account', email or 'invalid', 12)
        password = request.form.get('password', '')
        user = by_email(email) if email else None
        valid = len(password) <= 128 and check_password_hash(user.password if user else DUMMY_PASSWORD, password)
        if user and valid and user.enabled:
            audit('login', 'user', user.id, actor=user.id)
            db.session.commit()
            start_session(user)
            return redirect(url_for('views.dashboard'))
        flash('E-mail ou senha inválidos.', 'error')
    return render_template('auth.html', mode='login', title='Entrar', registration_open=registration_enabled())


def registration_enabled():
    settings = db.session.get(SiteSettings, 1)
    return settings.registration_enabled if settings else not current_app.config['PROD']


@bp.route('/cadastro', methods=['GET', 'POST'])
def register():
    if not registration_enabled():
        return render_template('error.html', message='Cadastro fechado. Fale com a administração.'), 403
    if current_user.is_authenticated:
        return redirect(url_for('views.dashboard'))
    if request.method == 'POST':
        rate_limit('register', client_ip(), 12)
        lock_mutations()
        if not registration_enabled():
            abort(403)
        name = request.form.get('name', '').strip()
        email = normalize_email(request.form.get('email', ''))
        password = request.form.get('password', '')
        if not email or not name or len(name) > 100 or not password_valid(password):
            flash('Informe nome, e-mail válido e senha com 12 a 128 caracteres.', 'error')
        elif by_email(email):
            flash('Se você já possui uma conta, entre ou solicite a recuperação de senha.', 'info')
        else:
            user = User(name=name, email=email, password=generate_password_hash(password),
                        role='USER', session_key=token())
            try:
                db.session.add(user)
                db.session.flush()
                audit('register', 'user', user.id, actor=user.id)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
            else:
                start_session(user)
                return redirect(url_for('views.dashboard'))
            flash('Se você já possui uma conta, entre ou solicite a recuperação de senha.', 'info')
    return render_template('auth.html', mode='register', title='Criar conta')


@bp.post('/sair')
@login_required
def sign_out():
    logout_user()
    session.clear()
    return redirect(url_for('auth.sign_in'))


def send_reset(email, raw):
    config = current_app.config
    link = config['PUBLIC_URL'] + url_for('auth.reset') + '#' + raw
    msg = EmailMessage()
    msg['To'] = email
    msg['From'] = config['SMTP_FROM']
    msg['Subject'] = 'Recuperação de senha do FadLab'
    msg.set_content(f'Abra o link em até 30 minutos para redefinir sua senha:\n\n{link}\n\nCódigo para preenchimento manual: {raw}')
    with smtplib.SMTP_SSL(config['SMTP_HOST'], config['SMTP_PORT'], context=create_default_context(), timeout=8) as smtp:
        if config['SMTP_USER']:
            smtp.login(config['SMTP_USER'], config['SMTP_PASSWORD'])
        smtp.send_message(msg)


@bp.route('/recuperar', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        rate_limit('reset-ip', client_ip(), 16)
        email = normalize_email(request.form.get('email', ''))
        rate_limit('reset-account', email or 'invalid', 4)
        config = current_app.config
        user = by_email(email) if email else None
        if user and user.enabled and all(config[k] for k in ('SMTP_HOST', 'SMTP_FROM', 'PUBLIC_URL')):
            raw = token()
            record = PasswordReset(digest=hashlib.sha256(raw.encode()).hexdigest(),
                                   user_id=user.id, expires=now() + timedelta(minutes=30))
            db.session.add(record)
            db.session.commit()
            try:
                send_reset(user.email, raw)
            except (OSError, smtplib.SMTPException):
                current_app.logger.warning('Falha no envio de recuperação de senha.')
                db.session.delete(record)
                db.session.commit()
        flash('Se houver uma conta ativa e o envio estiver configurado, você receberá um link.', 'info')
    return render_template('auth.html', mode='forgot', title='Recuperar senha')


@bp.route('/redefinir', methods=['GET', 'POST'])
def reset():
    if request.method == 'POST':
        rate_limit('reset-post', client_ip(), 20)
        code = request.form.get('code', '').strip()
        password = request.form.get('password', '')
        if len(code) != 43:
            abort(400)
        if not password_valid(password):
            flash('A senha precisa ter entre 12 e 128 caracteres.', 'error')
            return render_template('auth.html', mode='reset', title='Nova senha', code=code)
        hashed = generate_password_hash(password)
        lock_mutations()
        record = db.session.get(PasswordReset, hashlib.sha256(code.encode()).hexdigest())
        if not record or record.expires <= now():
            abort(404)
        user = db.session.get(User, record.user_id)
        if not user or not user.enabled:
            abort(404)
        user.password = hashed
        user.session_key = token()
        db.session.execute(db.delete(PasswordReset).where(PasswordReset.user_id == user.id))
        audit('password_reset', 'user', user.id, actor=user.id)
        db.session.commit()
        logout_user()
        session.clear()
        flash('Senha redefinida. Entre novamente.', 'success')
        return redirect(url_for('auth.sign_in'))
    return render_template('auth.html', mode='reset', title='Nova senha')


@bp.route('/perfil', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        rate_limit('profile', current_user.id, 25)
        lock_mutations()
        user = db.session.get(User, current_user.id, populate_existing=True)
        if not user.enabled or user.session_key != session.get('_user_id'):
            abort(403)
        name = request.form.get('name', '').strip()
        theme = request.form.get('theme', '')
        password = request.form.get('password', '')
        previous = request.form.get('previous', '')
        if not name or len(name) > 100 or theme not in THEMES:
            flash('Revise o nome e o tema.', 'error')
        elif password and (not password_valid(password) or len(previous) > 128 or
                           not check_password_hash(user.password, previous)):
            flash('Informe a senha atual e uma nova senha entre 12 e 128 caracteres.', 'error')
        else:
            user.name, user.theme = name, theme
            user.show_notice_highlights = request.form.get('notice_highlights') == 'yes'
            if password:
                user.password = generate_password_hash(password)
                user.session_key = token()
                db.session.execute(db.delete(PasswordReset).where(PasswordReset.user_id == user.id))
            audit('update_profile', 'user', user.id)
            db.session.commit()
            if password:
                start_session(user)
            flash('Perfil atualizado.', 'success')
            return redirect(url_for('auth.profile'))
    return render_template('profile.html', themes=THEMES)
