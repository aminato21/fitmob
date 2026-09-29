from __future__ import annotations

import hmac
from fastapi import HTTPException, Request


async def require_csrf(request: Request):
    supplied = request.headers.get('x-csrf-token')
    if not supplied:
        form = await request.form()
        supplied = str(form.get('csrf_token', ''))
    cookie = request.cookies.get('runstead_csrf', '')
    if not cookie or not supplied or not hmac.compare_digest(cookie.encode('utf-8'), supplied.encode('utf-8')):
        raise HTTPException(status_code=403, detail='This form expired. Reload the page and try again.')
