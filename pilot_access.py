"""Small, explicit access boundary for a trusted, single-workspace pilot.

This is a shared workspace passcode, not a multi-tenant account system.
Participant capability links and the authenticated voice callback stay separate.
"""
import hashlib
import hmac
import os
import secrets
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, RedirectResponse

COOKIE = 'delegate_pilot'
WINDOW = 12 * 60 * 60
hits = OrderedDict()


def passcode():
    return os.getenv('DELEGATE_PILOT_PASSCODE', '').strip()


def public_mode():
    return os.getenv('DELEGATE_PUBLIC_MODE', '').lower() in ('1', 'true', 'yes')


def ready():
    return not public_mode() or len(passcode()) >= 16


def signature(value):
    return hmac.new(passcode().encode(), value.encode(), hashlib.sha256).hexdigest()


def authenticated(connection):
    if not passcode():
        return not public_mode()
    cookie = connection.cookies.get(COOKIE, '')
    try:
        expires, nonce, signed = cookie.split('.')
        value = f'{expires}.{nonce}'
        return int(expires) > time.time() and hmac.compare_digest(signature(value), signed)
    except (ValueError, TypeError):
        return False


def allow(key, limit, seconds=60):
    now = time.monotonic()
    recent = [stamp for stamp in hits.get(key, []) if now - stamp < seconds]
    accepted = len(recent) < limit
    if accepted:
        recent.append(now)
    hits[key] = recent
    hits.move_to_end(key)
    while len(hits) > 2048:
        hits.popitem(last=False)
    return accepted


class Login(BaseModel):
    passcode: str = Field(min_length=1, max_length=512)


def install(app):
    @app.middleware('http')
    async def protect(request: Request, call_next):
        path = request.url.path
        protected = path.startswith('/api/session/') or path in ('/api/models', '/api/demo/speech', '/app', '/demo', '/docs', '/redoc', '/openapi.json')
        response = None
        if protected and not ready():
            response = JSONResponse({'detail': 'Public access requires a pilot passcode of at least 16 characters. Ask the host to finish setup.'}, status_code=503)
        elif protected and not authenticated(request):
            response = RedirectResponse('/login', status_code=303) if path in ('/app', '/demo', '/docs', '/redoc') else JSONResponse({'detail': 'Your pilot access has expired. Sign in again at /login, then retry.'}, status_code=401)
        # Cookies and capability links must not authorize requests from another website.
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get('host') or parsed.scheme not in ('http', 'https'):
                response = JSONResponse({'detail': 'Open Delegate directly to make changes.'}, status_code=403)
        if response is None and (passcode() or public_mode()):
            expensive = path.startswith('/api/') and (request.method == 'POST' or path.endswith(('/listen', '/voice')))
            if expensive and path not in ('/api/access/login',) and not path.endswith(('/interrupt', '/reject', '/complete', '/delete', '/voice/stop', '/invitation/revoke')):
                # A server-wide cap also covers many participants behind different IPs.
                if not allow('pilot-work', 120):
                    response = JSONResponse({'detail': 'The pilot is busy. Wait a minute and try again.'}, status_code=429, headers={'Retry-After': '60'})
            if path == '/api/session/create' and response is None and not allow('pilot-create', 20, 3600):
                response = JSONResponse({'detail': 'The pilot has reached its hourly conversation limit. Try again later.'}, status_code=429, headers={'Retry-After': '3600'})
        if response is None:
            response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(self)'
        if path.startswith(('/api/', '/receptionist/', '/v1/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/access/login')
    async def login(request: Request, credentials: Login):
        host = request.client.host if request.client else 'unknown'
        if not allow('login:' + host, 8, 300):
            return JSONResponse({'detail': 'Too many attempts. Wait five minutes before trying again.'}, status_code=429, headers={'Retry-After': '300'})
        if not ready():
            return JSONResponse({'detail': 'The host must configure a pilot passcode of at least 16 characters.'}, status_code=503)
        if not passcode() or not hmac.compare_digest(credentials.passcode.encode(), passcode().encode()):
            return JSONResponse({'detail': 'That passcode is not correct.'}, status_code=401)
        value = f'{int(time.time()) + WINDOW}.{secrets.token_hex(16)}'
        response = JSONResponse({'status': 'ok'})
        response.set_cookie(COOKIE, f'{value}.{signature(value)}', max_age=WINDOW, httponly=True, samesite='strict', secure=public_mode() or request.url.scheme == 'https')
        return response

    @app.post('/api/access/logout')
    def logout():
        response = JSONResponse({'status': 'ok'})
        response.delete_cookie(COOKIE)
        return response
