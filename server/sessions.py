"""The session cookie, and the CSRF pair that guards it.

Why a cookie at all, when Supabase's own SDK keeps tokens in localStorage:
this app runs under `script-src 'self'` with no inline anything, and the one
thing that policy is protecting is exactly the thing localStorage would hand
to any injected script. An HttpOnly cookie is not readable from JavaScript,
so a hypothetical XSS in this app cannot walk off with a token.

The trade that comes with cookies is CSRF, so every state-changing /api/*
call has to echo a header that only same-origin JavaScript can read. Both
halves are here.
"""
import time

from . import config, crypto, supa

MAX_COOKIE = 3800            # browsers guarantee 4096 per cookie, header and all


def cookies(header):
    """`a=1; b=2` -> {'a': '1', 'b': '2'}. Tolerant, because this is input."""
    out = {}
    for bit in str(header or '').split(';'):
        if '=' not in bit:
            continue
        k, v = bit.split('=', 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _set(name, value, secure, days=None, http_only=True):
    bits = ['%s=%s' % (name, value), 'Path=/', 'SameSite=Lax']
    if http_only:
        bits.append('HttpOnly')
    if secure:
        bits.append('Secure')
    if days == 0:
        bits.append('Max-Age=0')
        bits.append('Expires=Thu, 01 Jan 1970 00:00:00 GMT')
    elif days:
        bits.append('Max-Age=%d' % int(days * 86400))
    return '; '.join(bits)


def from_grant(grant, profile=None):
    """A GoTrue token reply -> what we keep. Deliberately small: this travels
    on every request. `r` is a hint for the interface; anything that actually
    matters re-reads the role from the database."""
    user = grant.get('user') or {}
    meta = user.get('user_metadata') or {}
    prof = profile or {}
    life = grant.get('expires_in')
    ax = grant.get('expires_at') or (time.time() + (life or 3600))
    return {
        'u': user.get('id') or prof.get('id') or '',
        'e': (user.get('email') or prof.get('email') or '').lower(),
        'n': prof.get('display_name') or meta.get('display_name') or '',
        'r': prof.get('role') or 'user',
        'v': bool(user.get('email_confirmed_at') or user.get('confirmed_at')),
        'at': grant.get('access_token') or '',
        'rt': grant.get('refresh_token') or '',
        'ax': float(ax),
        'iat': int(time.time()),
    }


def seal(sess, secure):
    """Returns the Set-Cookie lines for a session. If the tokens push the
    cookie over what a browser will keep, the access token is dropped and the
    next request pays for a refresh instead — slower, never broken."""
    blob = crypto.seal(config.SESSION_SECRET, sess)
    if len(blob) > MAX_COOKIE:
        small = dict(sess)
        small['at'] = ''
        small['ax'] = 0
        blob = crypto.seal(config.SESSION_SECRET, small)
    out = [_set(config.SESSION_COOKIE, blob, secure, days=config.SESSION_DAYS)]
    out.append(_set(config.CSRF_COOKIE, crypto.token(18), secure,
                    days=config.SESSION_DAYS, http_only=False))
    return out


def clear(secure):
    return [_set(config.SESSION_COOKIE, '', secure, days=0),
            _set(config.CSRF_COOKIE, '', secure, days=0, http_only=False)]


def csrf_cookie(secure):
    """Handed out by GET /api/config so a page that has never signed in still
    has a token to echo. Readable by our own JavaScript on purpose — that is
    the half of the double submit that proves same-origin."""
    return _set(config.CSRF_COOKIE, crypto.token(18), secure,
                days=config.SESSION_DAYS, http_only=False)


def read(jar):
    """Cookie jar -> session dict, or None. Only the signature is checked
    here; whether the access token still works is decided by `live`."""
    sess = crypto.unseal(config.SESSION_SECRET, jar.get(config.SESSION_COOKIE))
    if not sess or not sess.get('u') or not sess.get('rt'):
        return None
    return sess


def live(sess):
    """Hand back a session with a usable access token, refreshing against
    GoTrue if the one we hold has run out. Returns (session, fresh) where
    `fresh` means the caller has to re-issue the cookie — or (None, False)
    when the refresh token itself is dead and the person has to sign in."""
    if sess.get('at') and not crypto.expired(sess.get('ax')):
        return sess, False
    if not supa.ready():
        return None, False
    rep = supa.refresh(sess.get('rt') or '')
    if not rep.ok or not isinstance(rep.body, dict) or not rep.body.get('access_token'):
        return None, False
    out = dict(sess)
    grant = rep.body
    out['at'] = grant.get('access_token') or ''
    out['rt'] = grant.get('refresh_token') or out['rt']
    out['ax'] = float(grant.get('expires_at')
                      or (time.time() + (grant.get('expires_in') or 3600)))
    user = grant.get('user') or {}
    if user.get('email'):
        out['e'] = user['email'].lower()
    if user.get('email_confirmed_at') or user.get('confirmed_at'):
        out['v'] = True
    return out, True


def csrf_ok(jar, header):
    """Double submit: the cookie is readable by our own JavaScript and by
    nothing cross-origin, so a matching header proves the caller is us."""
    want = jar.get(config.CSRF_COOKIE) or ''
    return bool(want) and crypto.same(want, header or '')


# ------------------------------------------------------------- guest access
# A visitor with no account still needs somewhere to keep one number: how many
# model calls they have spent. It goes in a signed cookie rather than in this
# process, so it survives a restart and is the same on every instance — and
# signing it is what stops the count being edited back to zero. Clearing
# cookies does reset it; config.GUEST_PER_IP is the answer to that.
def guest_read(jar):
    """The guest's tally, or None. An expired or unsigned one reads as absent,
    which starts a fresh allowance — the same as a first visit."""
    got = crypto.unseal(config.SESSION_SECRET, jar.get(config.GUEST_COOKIE))
    if not isinstance(got, dict) or not got.get('g'):
        return None
    try:
        if float(got.get('x') or 0) <= time.time():
            return None
    except (TypeError, ValueError):
        return None
    try:
        got['n'] = max(0, int(got.get('n') or 0))
    except (TypeError, ValueError):
        got['n'] = 0
    return got


def guest_new():
    return {'g': crypto.token(9), 'n': 0,
            'x': time.time() + config.GUEST_DAYS * 86400}


def guest_seal(guest, secure):
    """One Set-Cookie line. HttpOnly, like the session: the page has no reason
    to read this, and /api/config tells it the count in plain JSON anyway."""
    return [_set(config.GUEST_COOKIE,
                 crypto.seal(config.SESSION_SECRET, guest), secure,
                 days=config.GUEST_DAYS)]


def guest_clear(secure):
    """Dropped the moment there is an account, so a visitor who signs up is not
    still carrying a spent allowance around."""
    return [_set(config.GUEST_COOKIE, '', secure, days=0)]
