import os
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, render_template, request, send_from_directory
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .models import PasswordReset, RateWindow, User, db, now
from .security import is_admin, lock_mutations, token

ROOT = Path(__file__).resolve().parents[1]
login = LoginManager()
csrf = CSRFProtect()
migrations = Migrate()


def create_app(config=None):
    app = Flask(__name__, static_folder=str(ROOT / 'public' / 'assets'), static_url_path='/assets')
    production = os.getenv('FADLAB_ENV') == 'production' or os.getenv('VERCEL') == '1'
    uri = os.getenv('DATABASE_URL', 'sqlite:///fadlab.sqlite')
    if uri.startswith(('postgres://', 'postgresql://')):
        uri = 'postgresql+psycopg://' + uri.split('://', 1)[1]
    app.config.from_mapping(
        SECRET_KEY=os.getenv('SECRET_KEY') or (None if production else token()),
        SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS={
            'poolclass': NullPool,
            'connect_args': {'prepare_threshold': None, 'connect_timeout': 8},
            'hide_parameters': True,
        } if uri.startswith('postgresql+psycopg://') else {},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=128 * 1024,
        SUPABASE_URL=os.getenv('SUPABASE_URL', '').rstrip('/'),
        SUPABASE_STORAGE_KEY=os.getenv('SUPABASE_STORAGE_KEY'),
        SUPABASE_AVATAR_BUCKET=os.getenv('SUPABASE_AVATAR_BUCKET', 'fadlab-avatars'),
        WTF_CSRF_SSL_STRICT=True,
        PUBLIC_URL=os.getenv('PUBLIC_URL', '').rstrip('/'),
        TRUSTED_HOSTS=os.getenv('TRUSTED_HOSTS', '').split(',') if os.getenv('TRUSTED_HOSTS') else None,
        VERCEL_EDGE=os.getenv('VERCEL') == '1',
        SMTP_HOST=os.getenv('SMTP_HOST'),
        SMTP_PORT=int(os.getenv('SMTP_PORT', '465')),
        SMTP_USER=os.getenv('SMTP_USER'),
        SMTP_PASSWORD=os.getenv('SMTP_PASSWORD'),
        SMTP_FROM=os.getenv('SMTP_FROM'),
        PROD=production,
    )
    app.config.update(config or {})
    if app.config['VERCEL_EDGE']:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    if app.config['PROD']:
        from urllib.parse import urlsplit
        public = urlsplit(app.config['PUBLIC_URL'])
        if not app.secret_key or len(app.secret_key) < 32:
            raise RuntimeError('Configure SECRET_KEY com pelo menos 32 caracteres aleatórios.')
        if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql+psycopg://'):
            raise RuntimeError('Configure DATABASE_URL do PostgreSQL.')
        if make_url(app.config['SQLALCHEMY_DATABASE_URI']).query.get('sslmode') not in {
            'require', 'verify-ca', 'verify-full'
        }:
            raise RuntimeError('A conexão de produção com PostgreSQL exige TLS.')
        if public.scheme != 'https' or not public.hostname or public.path or public.query or public.fragment:
            raise RuntimeError('Configure PUBLIC_URL como uma origem HTTPS.')
        storage_url = urlsplit(app.config['SUPABASE_URL'])
        if app.config['SUPABASE_URL'] and (storage_url.scheme != 'https' or not storage_url.hostname
                                           or storage_url.path or storage_url.query or storage_url.fragment
                                           or storage_url.username):
            raise RuntimeError('Configure SUPABASE_URL como a origem HTTPS do projeto.')
        app.config['TRUSTED_HOSTS'] = app.config['TRUSTED_HOSTS'] or [public.hostname]

    db.init_app(app)
    with app.app_context():
        if db.engine.dialect.name == 'sqlite':
            @event.listens_for(db.engine, 'connect')
            def sqlite_constraints(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
    @app.before_request
    def apply_upload_limit():
        if request.endpoint == 'avatars.update':
            request.max_content_length = (1024 + 16) * 1024

    csrf.init_app(app)
    login.init_app(app)
    migrations.init_app(app, db, directory=str(ROOT / 'migrations'))

    from .auth import bp as auth
    from .views import bp as views

    app.register_blueprint(auth)
    app.register_blueprint(views)
    app.context_processor(lambda: {'is_admin': is_admin, 'modules': views_modules()})

    @app.get('/sw.js')
    def worker():
        response = send_from_directory(ROOT / 'public', 'sw.js')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/offline.html')
    def offline_page():
        return send_from_directory(ROOT / 'public', 'offline.html')

    @app.get('/manifest.webmanifest')
    def manifest():
        return send_from_directory(ROOT / 'public', 'manifest.webmanifest')

    @app.errorhandler(HTTPException)
    def http_error(error):
        labels = {400: 'Pedido inválido ou sessão expirada.', 403: 'Acesso restrito.',
                  404: 'Página indisponível.', 413: 'Pedido muito grande.',
                  429: 'Muitas tentativas. Aguarde e tente novamente.'}
        return render_template('error.html', message=labels.get(error.code, 'Não foi possível concluir a operação.')), error.code

    @app.after_request
    def secure_headers(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if app.config['PROD']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if request.endpoint not in {'static', 'worker', 'manifest'}:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.cli.command('create-admin')
    @click.option('--name', prompt='Nome')
    @click.option('--email', prompt='E-mail')
    @click.password_option()
    def create_admin(name, email, password):
        from .auth import normalize_email, password_valid
        from werkzeug.security import generate_password_hash

        email = normalize_email(email)
        if not email or not name.strip() or len(name.strip()) > 100 or not password_valid(password):
            raise click.ClickException('Informe nome, e-mail válido e senha com 12 a 128 caracteres.')
        lock_mutations()
        if db.session.scalar(db.select(User.id).where(User.role == 'ADMIN').limit(1)):
            raise click.ClickException('Já existe um administrador.')
        if db.session.scalar(db.select(User.id).where(User.email == email)):
            raise click.ClickException('E-mail já cadastrado.')
        db.session.add(User(name=name.strip(), email=email, password=generate_password_hash(password),
                            role='ADMIN', session_key=token()))
        db.session.commit()
        click.echo('Administrador provisionado.')

    @app.cli.command('prune-security')
    def prune_security():
        import time
        db.session.execute(db.delete(RateWindow).where(RateWindow.window < int(time.time()) - 86400))
        db.session.execute(db.delete(PasswordReset).where(PasswordReset.expires <= now()))
        db.session.commit()

    from .avatars import bp as avatars
    app.register_blueprint(avatars)
    app.context_processor(lambda: {'avatar_storage': bool(app.config['SUPABASE_URL'] and
                                                           app.config['SUPABASE_STORAGE_KEY'])})
    return app


def views_modules():
    from .views import MODULES
    return MODULES


@login.user_loader
def load_user(value):
    return db.session.scalar(db.select(User).where(User.session_key == value, User.enabled.is_(True)))


login.login_view = 'auth.sign_in'
