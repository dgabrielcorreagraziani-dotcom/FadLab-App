import os
import re
from pathlib import Path

import pytest
from flask_migrate import upgrade
from werkzeug.security import check_password_hash, generate_password_hash

from fadlab import create_app
from fadlab.models import (
    Audit, Equipment, Event, Membership, Ministry, Notice, NoticeRead,
    Repertoire, Shift, Song, User, db,
)
from fadlab.security import token

PASSWORD = 'Gerado-apenas-para-teste-43'


@pytest.fixture(scope='session')
def password_hash():
    return generate_password_hash(PASSWORD)


@pytest.fixture
def app(tmp_path, password_hash):
    uri = os.getenv('FADLAB_TEST_DATABASE_URL', f'sqlite:///{tmp_path / "test.sqlite"}')
    config = {'TESTING': True, 'SECRET_KEY': 'testing-only-key-for-session',
              'SQLALCHEMY_DATABASE_URI': uri, 'PROD': False}
    if uri.startswith('postgresql'):
        schema = 'test_' + os.urandom(10).hex()
        from sqlalchemy import create_engine, text
        engine = create_engine(uri)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'options': f'-csearch_path={schema}'}}
    app = create_app(config)
    try:
        with app.app_context():
            upgrade(directory=str(Path(__file__).resolve().parents[1] / 'migrations'))
            for name, role in [('Admin', 'ADMIN'), ('Coordenador', 'COORDINATOR'),
                               ('Membro', 'USER'), ('Outra', 'USER')]:
                db.session.add(User(name=name, email=f'{name.lower()}@example.com', role=role,
                                    password=password_hash, session_key=token()))
            db.session.add_all([Ministry(name='Louvor'), Ministry(name='Mídia Reservada')])
            db.session.flush()
            for ministry, user, manages in [(1, 2, True), (1, 3, False), (1, 4, False), (2, 4, False)]:
                db.session.add(Membership(ministry_id=ministry, user_id=user, coordinates=manages))
            db.session.commit()
        yield app
    finally:
        if uri.startswith('postgresql'):
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def submit(client, path, get_path=None, **data):
    return client.post(path, data=dict(data, csrf_token=csrf(client, get_path or path)))


def login(client, name):
    response = submit(client, '/entrar', email=f'{name}@example.com', password=PASSWORD)
    assert response.status_code == 302
    return client


def add_event(client, ministry_id=1, title='Ensaio geral', starts='2030-05-10T18:00', ends='2030-05-10T20:00'):
    return submit(client, '/painel/agenda/novo', ministry_id=ministry_id, title=title, starts=starts, ends=ends)


def test_signup_and_logout(app, client):
    response = submit(client, '/cadastro', name='Pessoa nova', email='Nova@example.com', password=PASSWORD, role='ADMIN')
    assert response.status_code == 302
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == 'nova@example.com'))
        assert user.role == 'USER'
        assert user.password != PASSWORD
        assert check_password_hash(user.password, PASSWORD)
    assert client.get('/administracao').status_code == 403
    assert client.get('/sair').status_code == 405
    assert client.post('/sair').status_code == 400
    assert submit(client, '/sair', '/perfil').status_code == 302
    assert client.get('/perfil').status_code == 302
    assert submit(client, '/entrar', email='nova@example.com', password='incorreta').status_code == 200
    assert submit(client, '/entrar', email='nova@example.com', password=PASSWORD).status_code == 302


def test_csrf_security_headers_and_rate_limiting(client):
    assert client.post('/entrar', data={'email': 'admin@example.com', 'password': PASSWORD}).status_code == 400
    with client.session_transaction() as session:
        assert '_user_id' not in session
    response = client.get('/entrar')
    assert 'no-store' == response.headers['Cache-Control']
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    key = csrf(client, '/entrar')
    for _ in range(12):
        assert client.post('/entrar', data={'csrf_token': key, 'email': 'sem-conta@example.com', 'password': 'x'}).status_code == 200
    assert client.post('/entrar', data={'csrf_token': key, 'email': 'sem-conta@example.com', 'password': 'x'}).status_code == 429


def test_password_recovery_invalidates_sessions(app, client, monkeypatch):
    from fadlab import auth

    sent = []
    app.config.update(SMTP_HOST='mail.invalid', SMTP_FROM='fadlab@example.com', PUBLIC_URL='https://example.com')
    monkeypatch.setattr(auth, 'send_reset', lambda email, code: sent.append((email, code)))
    original = login(app.test_client(), 'membro')
    assert submit(client, '/recuperar', email='membro@example.com').status_code == 200
    email, code = sent.pop()
    assert email == 'membro@example.com'
    path = '/redefinir'
    assert submit(client, path, code=code, password='Nova senha de teste 44').status_code == 302
    assert submit(client, path, code=code, password='Outra senha de teste 45').status_code == 404
    assert original.get('/perfil').status_code == 302
    assert submit(client, '/entrar', email=email, password='Nova senha de teste 44').status_code == 302


def test_admin_and_ministry_scope(app, client):
    admin = login(client, 'admin')
    coordinator = login(app.test_client(), 'coordenador')
    user = login(app.test_client(), 'membro')
    assert submit(admin, '/painel/ministerios/novo', name='Sonoplastia', description='Equipe de áudio').status_code == 302
    assert add_event(admin, 2, 'Reunião restrita').status_code == 302
    assert add_event(coordinator).status_code == 302
    assert add_event(coordinator, 2, 'Registro proibido').status_code == 200
    assert b'Registro proibido' not in admin.get('/painel/agenda').data
    assert 'Reunião restrita' not in user.get('/painel/agenda').text
    assert 'Reunião restrita' not in user.get('/pesquisa?q=restrita').text
    assert coordinator.get('/painel/agenda/1/editar').status_code == 404
    assert coordinator.get('/painel/avisos/novo?ministry_id=2').status_code == 200
    assert '<option value="4">Outra</option>' not in coordinator.get('/painel/avisos/novo?ministry_id=2').text
    assert user.get('/painel/agenda/novo').status_code == 403
    assert submit(user, '/painel/agenda/2/excluir', '/perfil', confirm='yes').status_code == 404
    assert submit(user, '/administracao', '/perfil', action='role', user_id=3, role='ADMIN', enabled='yes').status_code == 403
    assert 'Ensaio geral' in user.get('/painel/agenda?view=calendar&month=2030-05').text
    assert 'membro@example.com' not in coordinator.get('/membros').text
    assert submit(admin, '/administracao', action='role', user_id=3, role='COORDINATOR', enabled='yes').status_code == 302
    assert user.get('/perfil').status_code == 302
    assert admin.get('/administracao/auditoria').status_code == 200
    with app.app_context():
        assert db.session.get(User, 3).role == 'COORDINATOR'
        assert db.session.scalar(db.select(Audit.id).where(Audit.action == 'change_role'))


def test_shifts_detect_time_conflict_and_scope(app, client):
    coordinator = login(client, 'coordenador')
    assert add_event(coordinator).status_code == 302
    assert add_event(coordinator, title='Culto', starts='2030-05-10T19:00', ends='2030-05-10T21:00').status_code == 302
    assert add_event(coordinator, title='Outro turno', starts='2030-05-10T20:00', ends='2030-05-10T21:00').status_code == 302
    assert submit(coordinator, '/painel/escalas/novo', event_id=1, user_id=3, duty='Teclado').status_code == 302
    conflict = submit(coordinator, '/painel/escalas/novo', event_id=2, user_id=3, duty='Violão')
    assert 'já está escalado' in conflict.text
    assert submit(coordinator, '/painel/escalas/novo', event_id=3, user_id=3, duty='Baixo').status_code == 302
    conflict = submit(coordinator, '/painel/agenda/3/editar', ministry_id=1, title='Outro turno',
                      starts='2030-05-10T19:30', ends='2030-05-10T21:00')
    assert 'coincide com outra escala' in conflict.text
    assert 'Teclado' in login(app.test_client(), 'membro').get('/painel/escalas').text
    assert 'Teclado' not in login(app.test_client(), 'outra').get('/painel/escalas').text
    with app.app_context():
        assert len(db.session.scalars(db.select(Shift)).all()) == 2


def test_louvor_equipment_notice_and_search(app, client):
    coordinator = login(client, 'coordenador')
    assert add_event(coordinator).status_code == 302
    assert submit(coordinator, '/painel/louvor/novo', ministry_id=1, title='Canção de teste',
                  artist='Artista de teste', musical_key='D', bpm=105, category='Culto').status_code == 302
    assert submit(coordinator, '/painel/repertorios/novo', event_id=1, song_id=1, position=1).status_code == 302
    assert submit(coordinator, '/painel/sonoplastia/novo', ministry_id=1, name='Mesa de teste',
                  state='Disponível').status_code == 302
    assert submit(coordinator, '/painel/historico/novo', equipment_id=1, event_id=1,
                  kind='Checklist realizado', notes='Som conferido.').status_code == 302
    assert submit(coordinator, '/painel/avisos/novo', ministry_id=1, user_id=3,
                  title='Aviso individual', body='<section>Conteúdo</section>').status_code == 302
    assert submit(coordinator, '/painel/avisos/novo', ministry_id=1, user_id=0,
                  title='Aviso geral', body='Aberto ao ministério.').status_code == 302
    member = login(app.test_client(), 'membro')
    other = login(app.test_client(), 'outra')
    assert 'Aviso individual' in member.get('/').text
    assert '&lt;section&gt;Conteúdo&lt;/section&gt;' in member.get('/painel/avisos').text
    assert 'Aviso individual' not in other.get('/painel/avisos').text
    assert 'Aviso geral' in other.get('/painel/avisos').text
    assert submit(other, '/avisos/1/lido', '/perfil').status_code == 404
    assert submit(member, '/avisos/1/lido', '/perfil').status_code == 302
    assert 'Aviso individual' not in member.get('/').text
    assert 'Canção de teste' in member.get('/pesquisa?q=Canção').text
    assert 'Som conferido.' in member.get('/painel/historico').text
    with app.app_context():
        assert db.session.get(Repertoire, 1).song.title == 'Canção de teste'
        assert db.session.get(Equipment, 1).name == 'Mesa de teste'
        assert db.session.get(NoticeRead, (1, 3))


def test_delete_confirmation_and_cascade(app, client):
    admin = login(client, 'admin')
    assert add_event(admin).status_code == 302
    assert submit(admin, '/painel/escalas/novo', event_id=1, user_id=3, duty='Teclado').status_code == 302
    edit = '/painel/agenda/1/editar'
    path = '/painel/agenda/1/excluir'
    assert submit(admin, path, edit).status_code == 400
    assert submit(admin, path, edit, confirm='yes').status_code == 302
    with app.app_context():
        assert db.session.get(Event, 1) is None
        assert db.session.get(Shift, 1) is None


def test_profile_theme_and_pwa(client):
    login(client, 'membro')
    assert submit(client, '/perfil', name='Nome atualizado', theme='dark').status_code == 302
    assert 'data-theme="dark"' in client.get('/').text
    manifest = client.get('/manifest.webmanifest')
    assert manifest.json['display'] == 'standalone'
    for icon in manifest.json['icons']:
        assert client.get(icon['src']).mimetype == 'image/png'
    assert client.get('/sw.js').headers['Cache-Control'] == 'no-cache'
    assert 'Sem conexão' in client.get('/offline.html').text


def test_registration_settings_and_member_edition(app, client):
    admin = login(client, 'admin')
    assert submit(admin, '/administracao', action='settings').status_code == 302
    guest = app.test_client()
    assert 'Criar conta' not in guest.get('/entrar').text
    assert guest.get('/cadastro').status_code == 403
    assert submit(admin, '/administracao', action='rename', user_id=3, name='Nome revisado').status_code == 302
    member = login(guest, 'membro')
    assert 'Nome revisado' in member.get('/perfil').text
    assert submit(admin, '/administracao', action='settings', registration='yes').status_code == 302
    assert app.test_client().get('/cadastro').status_code == 200


def test_private_avatar_validation_and_removal(app, client, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from fadlab import avatars

    objects = {}
    app.config.update(SUPABASE_URL='https://storage.invalid', SUPABASE_STORAGE_KEY='test-storage-key')

    def storage(method, key, content=None):
        if method == 'POST':
            objects[key] = content
        elif method == 'DELETE':
            del objects[key]
        elif method == 'GET':
            return objects[key]
        return b''

    monkeypatch.setattr(avatars, 'storage', storage)
    login(client, 'membro')
    url = '/perfil/foto'
    assert submit(client, url, '/perfil', photo=(BytesIO(b'invalid content'), 'foto.png')).status_code == 302
    assert not objects
    raw = BytesIO()
    Image.new('RGB', (284, 412), 'purple').save(raw, format='PNG')
    raw.seek(0)
    assert submit(client, url, '/perfil', photo=(raw, 'foto.png')).status_code == 302
    response = client.get(url)
    assert response.mimetype == 'image/jpeg'
    assert response.headers['Cache-Control'] == 'no-store'
    with Image.open(BytesIO(response.data)) as result:
        assert result.size == (256, 256)
    assert app.test_client().get(url).status_code == 302
    assert login(app.test_client(), 'outra').get(url).status_code == 404
    assert submit(client, url, '/perfil', remove='yes').status_code == 302
    assert not objects
    assert client.get(url).status_code == 404


def test_bootstrap_has_no_default_account(tmp_path):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "empty.sqlite"}'})
    runner = app.test_cli_runner()
    assert runner.invoke(args=['db', 'upgrade']).exit_code == 0
    input_once = f'{PASSWORD}\n{PASSWORD}\n'
    args = ['create-admin', '--name', 'Administrador', '--email']
    assert runner.invoke(args=[*args, 'primeiro@example.com'], input=input_once).exit_code == 0
    assert runner.invoke(args=[*args, 'segundo@example.com'], input=input_once).exit_code == 1
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(User)) == 1


def test_postgres_tables_use_rls(app):
    with app.app_context():
        if db.engine.dialect.name != 'postgresql':
            pytest.skip('RLS é específico do PostgreSQL.')
        from sqlalchemy import text
        names = db.session.scalars(text(
            'SELECT relname FROM pg_class WHERE relnamespace = current_schema()::regnamespace '
            "AND relkind = 'r' AND relrowsecurity"
        )).all()
        assert set(db.metadata.tables).issubset(names)
