from io import BytesIO
from json import dumps
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from flask import Blueprint, Response, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required
from PIL import Image, ImageOps
from sqlalchemy.exc import SQLAlchemyError

from .models import db
from .security import audit, lock_mutations, rate_limit

bp = Blueprint('avatars', __name__)
MAX_UPLOAD = 1024 * 1024
MAX_PIXELS = 12_000_000


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def storage(method, key, content=None):
    cfg = current_app.config
    bucket = quote(cfg['SUPABASE_AVATAR_BUCKET'], safe='')
    object_key = quote(key, safe='/')
    path = 'object/authenticated' if method == 'GET' else 'object'
    endpoint = f"{cfg['SUPABASE_URL']}/storage/v1/{path}/{bucket}/{object_key}"
    credential = cfg['SUPABASE_STORAGE_KEY']
    headers = {'apikey': credential}
    # Supabase also supports non-JWT secret keys through the apikey header.
    if not credential.startswith('sb_'):
        headers['Authorization'] = f'Bearer {credential}'
    if method == 'DELETE':
        endpoint = f"{cfg['SUPABASE_URL']}/storage/v1/object/{bucket}"
        content = dumps({'prefixes': [key]}).encode()
        headers['Content-Type'] = 'application/json'
    elif content is not None:
        headers['Content-Type'] = 'image/jpeg'
    req = Request(endpoint, data=content, headers=headers, method=method)
    with build_opener(RejectRedirect()).open(req, timeout=5) as response:
        return response.read(MAX_UPLOAD + 1)


def normalized_image(raw):
    if not raw or len(raw) > MAX_UPLOAD:
        raise ValueError
    with Image.open(BytesIO(raw), formats=('JPEG', 'PNG', 'WEBP')) as img:
        if img.width * img.height > MAX_PIXELS or getattr(img, 'is_animated', False):
            raise ValueError
        result = ImageOps.fit(ImageOps.exif_transpose(img).convert('RGB'), (256, 256))
        output = BytesIO()
        result.save(output, format='JPEG', quality=86, optimize=True)
        return output.getvalue()


def configured():
    return bool(current_app.config['SUPABASE_URL'] and current_app.config['SUPABASE_STORAGE_KEY'])


def cleanup(key):
    try:
        storage('DELETE', key)
    except (URLError, OSError):
        current_app.logger.warning('Falha ao excluir objeto anterior de foto de perfil.')


@bp.get('/perfil/foto')
@login_required
def show():
    if not configured() or not current_user.avatar_key:
        abort(404)
    try:
        image = storage('GET', current_user.avatar_key)
    except (URLError, OSError):
        abort(404)
    if len(image) > MAX_UPLOAD:
        abort(404)
    return Response(image, mimetype='image/jpeg')


@bp.post('/perfil/foto')
@login_required
def update():
    if not configured():
        abort(404)
    rate_limit('avatar', current_user.id, 10)
    lock_mutations()
    old_key = current_user.avatar_key
    new_key = None
    if request.form.get('remove') == 'yes':
        if not old_key:
            return redirect(url_for('auth.profile'))
    else:
        upload = request.files.get('photo')
        if not upload:
            abort(400)
        try:
            content = normalized_image(upload.stream.read(MAX_UPLOAD + 1))
        except (ValueError, OSError, Image.DecompressionBombError):
            flash('Selecione uma foto JPG, PNG ou WebP de até 1 MB e 12 megapixels.', 'error')
            return redirect(url_for('auth.profile'))
        new_key = f'{current_user.id}/{uuid4().hex}.jpg'
        try:
            storage('POST', new_key, content)
        except (URLError, OSError):
            flash('O armazenamento de fotos está indisponível.', 'error')
            return redirect(url_for('auth.profile'))
    current_user.avatar_key = new_key
    audit('update_avatar', 'user', current_user.id)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        # A failed commit can be ambiguous. Leave the new object for reconciliation.
        current_app.logger.warning('Falha ao confirmar a foto de perfil.')
        abort(503)
    if old_key:
        cleanup(old_key)
    flash('Foto de perfil atualizada.', 'success')
    return redirect(url_for('auth.profile'))
