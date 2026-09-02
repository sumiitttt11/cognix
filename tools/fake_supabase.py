#!/usr/bin/env python3
"""GoTrue and PostgREST, faked well enough to run this app on a laptop.

Why this exists: cloud mode is the interesting half of Cognix — accounts,
Postgres, the admin panel — and none of it should have to wait on somebody
creating a Supabase project before it can be tried, reviewed or tested. This
speaks the two dialects server/supa.py speaks, in memory, on one port.

    python tools/fake_supabase.py            # listens on 127.0.0.1:8779

Then, in .env next to serve.py:

    COGNIX_SUPABASE_URL=http://127.0.0.1:8779
    COGNIX_SUPABASE_ANON_KEY=stub-anon-key
    COGNIX_SUPABASE_SERVICE_KEY=stub-service-key
    COGNIX_ADMIN_EMAILS=you@example.com

and `python serve.py` says `mode: cloud`.

What is faithful, because the app leans on it:

  * a token is the only proof of who you are, and every row that comes back is
    filtered by the id inside that token — the rule policies.sql states, so a
    route that forgot to scope a query is caught here too;
  * the service key bypasses that, and nothing else does;
  * the triggers that matter: a profile row per signup, updated_at on write,
    the privileged profile columns put back unless an admin sent the change;
  * PostgREST's shapes — a list even for one row, content-range for a count,
    a merge on a named column, PTxxx from an RPC as an HTTP status.

What is not faithful: there is no SQL here, nothing survives the process
unless --store is given, mail is printed to this console instead of sent, and
the tokens are shaped like JWTs but signed with nothing. It is a test double.
Do not point anything real at it.
"""
import argparse
import base64
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

ANON_KEY = os.environ.get('STUB_ANON_KEY', 'stub-anon-key')
SERVICE_KEY = os.environ.get('STUB_SERVICE_KEY', 'stub-service-key')
# Off unless asked for: every table answers 'never created', so the app's
# not-set-up path can be walked through in a browser.
NO_TABLES = os.environ.get('STUB_NO_TABLES', '') in ('1', 'true', 'yes')
ACCESS_LIFE = 3600            # seconds, as GoTrue's default
PASSWORD_MIN = 6              # GoTrue's own floor; the app asks for 10
CONFIRM = False               # --confirm: make people follow the mail
QUIET = False
LOCK = threading.RLock()

TABLES = ('profiles', 'chats', 'messages', 'maps', 'usage_events',
          'audit_log', 'app_settings')

# id -> {id, email, password, confirmed, meta, created_at, last_sign_in_at}
USERS = {}
TOKENS = {}                   # access token  -> {'uid', 'exp', 'kind'}
REFRESH = {}                  # refresh token -> uid
LINKED = {}                   # a mail link's access token -> the link's type
MAIL = []                     # what would have been sent, newest last
SEQ = {'messages': 0, 'usage_events': 0, 'audit_log': 0}
DB = {t: [] for t in TABLES}
STORE = None                  # --store path, or None for memory only


def now():
    return datetime.now(timezone.utc)


def iso(when=None):
    return (when or now()).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def uid4():
    return str(uuid.uuid4())
def b64(obj):
    raw = json.dumps(obj, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def mint(uid, kind='access'):
    """A JWT's shape, with nothing behind it. It starts `eyJ` on purpose:
    config.redact() in the app hides anything that looks like this, and a stub
    that logged tokens in the clear would teach the wrong habit."""
    life = ACCESS_LIFE if kind == 'access' else 60 * 60 * 24 * 30
    exp = int(time.time()) + life
    user = USERS.get(uid) or {}
    token = '.'.join([
        b64({'alg': 'none', 'typ': 'JWT'}),
        b64({'sub': uid, 'role': 'authenticated', 'aud': 'authenticated',
             'email': user.get('email', ''), 'exp': exp, 'kind': kind}),
        secrets.token_urlsafe(24),
    ])
    TOKENS[token] = {'uid': uid, 'exp': exp, 'kind': kind}
    return token


def grant(uid):
    """The token reply GoTrue hands back, in the shape sessions.from_grant
    reads: access_token, refresh_token, expires_in, expires_at, user."""
    at = mint(uid)
    rt = secrets.token_urlsafe(32)
    REFRESH[rt] = uid
    return {'access_token': at, 'token_type': 'bearer',
            'expires_in': ACCESS_LIFE,
            'expires_at': int(time.time()) + ACCESS_LIFE,
            'refresh_token': rt, 'user': public_user(uid)}


def public_user(uid):
    u = USERS.get(uid)
    if not u:
        return None
    stamp = iso(u['created_at']) if u.get('confirmed') else None
    return {
        'id': uid, 'aud': 'authenticated', 'role': 'authenticated',
        'email': u['email'], 'email_confirmed_at': stamp,
        'confirmed_at': stamp, 'phone': '',
        'created_at': iso(u['created_at']),
        'updated_at': iso(u.get('updated_at') or u['created_at']),
        'last_sign_in_at': iso(u['last_sign_in_at']) if u.get('last_sign_in_at') else None,
        'app_metadata': {'provider': 'email', 'providers': ['email']},
        'user_metadata': dict(u.get('meta') or {}),
        'identities': [],
    }
def whoami(head):
    """(uid, role) for one request. role is 'service', 'authenticated', 'anon'
    or 'dead', and everything below this line trusts nothing else.

    'dead' matters: a token that has run out is a 401 from the real thing, not
    an empty list, and the app leans on the difference — that 401 is what makes
    it refresh rather than believe you own nothing."""
    raw = str(head.get('authorization') or '')
    token = raw[7:].strip() if raw[:7].lower() == 'bearer ' else raw.strip()
    key = str(head.get('apikey') or '')
    if token == SERVICE_KEY or key == SERVICE_KEY and not token:
        return '', 'service'
    if not token or token == ANON_KEY:
        return '', 'anon'
    seen = TOKENS.get(token)
    if seen and seen['exp'] > time.time() and seen['uid'] in USERS:
        spend_link(token, seen['uid'])
        return seen['uid'], 'authenticated'
    return '', 'dead'


def spend_link(token, uid):
    """Following a confirmation or invitation link is what confirms an address.

    The real GoTrue does it a step earlier, at /verify, and only then redirects
    with the tokens in the fragment — so by the time the app sees them the
    address is already confirmed. There is no /verify here, so the first use of
    a link's token stands in for it. A recovery link deliberately does not
    confirm anything: it proves an address that was confirmed already."""
    kind = LINKED.pop(token, '')
    if kind in ('signup', 'invite', 'email_change') and uid in USERS:
        USERS[uid]['confirmed'] = True


def is_admin(uid):
    """What is_admin() in functions.sql answers: an active admin row."""
    row = find('profiles', uid)
    return bool(row and row.get('role') == 'admin'
                and row.get('status') == 'active')


def find(table, key, column='id'):
    for row in DB[table]:
        if row.get(column) == key:
            return row
    return None


def save():
    if not STORE:
        return
    with open(STORE, 'w', encoding='utf-8') as fh:
        json.dump({
            'db': DB, 'seq': SEQ, 'mail': MAIL[-50:],
            'users': [dict(u, created_at=iso(u['created_at']),
                           last_sign_in_at=iso(u['last_sign_in_at'])
                           if u.get('last_sign_in_at') else None,
                           updated_at=iso(u['updated_at'])
                           if u.get('updated_at') else None)
                      for u in USERS.values()],
        }, fh, indent=1)


def when(text):
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return now()
def load():
    if not STORE or not os.path.exists(STORE):
        return
    try:
        with open(STORE, 'r', encoding='utf-8') as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return
    for table in TABLES:
        DB[table] = list(blob.get('db', {}).get(table) or [])
    SEQ.update(blob.get('seq') or {})
    MAIL.extend(blob.get('mail') or [])
    for u in blob.get('users') or []:
        u['created_at'] = when(u.get('created_at')) or now()
        u['updated_at'] = when(u.get('updated_at'))
        u['last_sign_in_at'] = when(u.get('last_sign_in_at'))
        USERS[u['id']] = u


def settings_row():
    row = find('app_settings', 1)
    if not row:
        row = {'id': 1, 'signups_open': True, 'maintenance': False,
               'announcement': '', 'default_token_cap': 400000,
               'allowed_models': ['cognix-mind-v1', 'cognix-apex-v2'],
               # The gateway an administrator can set from the console. Empty
               # means 'not configured here', which is how the server knows to
               # fall back to COGNIX_BASE / COGNIX_KEY. `gateway_sealed` is
               # ciphertext even here: the real column is anon-readable, so a
               # stub that stored the key in the clear would be a stub that
               # cannot show this side of the code misbehaving. `gateway_models`
               # is not sealed for the same reason the real column is not — a
               # model id is not a credential — and an empty object means every
               # agent asks for the id the build ships with.
               'gateway_base': '', 'gateway_sealed': '', 'gateway_hint': '',
               'gateway_updated_at': None, 'gateway_models': {},
               'updated_by': None, 'updated_at': iso()}
        DB['app_settings'].append(row)
    return row


def post_mail(kind, addr, link):
    """The dev mailbox. Printed, kept, and readable at GET /mail so a test can
    follow a confirmation link without a person reading this console."""
    MAIL.append({'kind': kind, 'to': addr, 'link': link, 'at': iso()})
    if not QUIET:
        print('\n  mail · %s · %s\n  %s\n' % (kind, addr, link), flush=True)


def link_for(kind, uid, redirect):
    """GoTrue puts the tokens in the fragment, which is why the app has a page
    at /app/auth/ whose only job is to read them and trade them for a cookie."""
    at = mint(uid)
    rt = secrets.token_urlsafe(32)
    REFRESH[rt] = uid
    LINKED[at] = kind
    frag = ('access_token=%s&refresh_token=%s&token_type=bearer&expires_in=%d'
            '&type=%s' % (at, rt, ACCESS_LIFE, kind))
    base = redirect or 'http://localhost:8778/app/auth/'
    join = '&' if '#' in base else '#'
    return base + join + frag


def make_profile(uid):
    """handle_new_user(), in Python. `on conflict do nothing`, same as the
    trigger, because signup and the app's own repair insert can race."""
    if find('profiles', uid):
        return find('profiles', uid)
    u = USERS[uid]
    row = {'id': uid, 'email': (u['email'] or '').lower(),
           'display_name': (u.get('meta') or {}).get('display_name')
           or (u.get('meta') or {}).get('name') or '',
           'role': 'user', 'status': 'active', 'token_cap': None, 'notes': '',
           'created_at': iso(), 'updated_at': iso(), 'last_seen_at': None}
    DB['profiles'].append(row)
    return row


def new_user(addr, password, meta=None, confirmed=True):
    uid = uid4()
    USERS[uid] = {'id': uid, 'email': addr.lower(), 'password': password,
                  'confirmed': bool(confirmed), 'meta': dict(meta or {}),
                  'created_at': now(), 'updated_at': now(),
                  'last_sign_in_at': None}
    make_profile(uid)
    return uid


def by_email(addr):
    addr = (addr or '').lower()
    for u in USERS.values():
        if u['email'] == addr:
            return u
    return None


# ------------------------------------------------------------------ GoTrue
def gotrue(method, path, query, body, head, role, uid):
    """Everything under /auth/v1. Returns (status, object)."""
    if path == '/signup' and method == 'POST':
        return au_signup(query, body)
    if path == '/token' and method == 'POST':
        kind = (query.get('grant_type') or [''])[0]
        if kind == 'refresh_token':
            return au_refresh(body)
        return au_password(body)
    if path == '/logout' and method == 'POST':
        TOKENS.pop(_bearer(head), None)
        return 204, None
    if path == '/user':
        if method == 'GET':
            return (200, public_user(uid)) if uid else (401, {'msg': 'invalid claim: missing sub claim'})
        if method == 'PUT':
            return au_update(uid, body)
    if path == '/recover' and method == 'POST':
        return au_recover(query, body)
    if path == '/resend' and method == 'POST':
        return au_resend(body)
    if path == '/invite' and method == 'POST':
        if role != 'service':
            return 403, {'msg': 'User not allowed'}
        return au_invite(query, body)
    if path.startswith('/admin/users'):
        if role != 'service':
            return 403, {'msg': 'User not allowed'}
        return au_admin(method, path[len('/admin/users'):].strip('/'), query, body)
    return 404, {'msg': 'not found'}


def _bearer(head):
    raw = str(head.get('authorization') or '')
    return raw[7:].strip() if raw[:7].lower() == 'bearer ' else raw.strip()


def au_signup(query, body):
    addr = str(body.get('email') or '').strip().lower()
    pw = str(body.get('password') or '')
    if '@' not in addr:
        return 400, {'msg': 'Unable to validate email address: invalid format'}
    if len(pw) < PASSWORD_MIN:
        return 422, {'msg': 'Password should be at least %d characters'
                            % PASSWORD_MIN}
    if by_email(addr):
        # GoTrue's own answer when confirmations are on: it does not say the
        # address is taken, and api.py turns a 422 into 'sign in instead'.
        return 422, {'msg': 'User already registered'}
    uid = new_user(addr, pw, body.get('data'), confirmed=not CONFIRM)
    if CONFIRM:
        redirect = (query.get('redirect_to') or [''])[0]
        post_mail('confirm', addr, link_for('signup', uid, redirect))
        return 200, dict(public_user(uid), email_confirmed_at=None,
                         confirmed_at=None)
    USERS[uid]['last_sign_in_at'] = now()
    return 200, grant(uid)


def au_password(body):
    addr = str(body.get('email') or '').strip().lower()
    u = by_email(addr)
    if not u or u['password'] != str(body.get('password') or ''):
        return 400, {'error': 'invalid_grant',
                     'error_description': 'Invalid login credentials'}
    if not u['confirmed']:
        return 400, {'error': 'invalid_grant',
                     'error_description': 'Email not confirmed'}
    u['last_sign_in_at'] = now()
    return 200, grant(u['id'])
def au_refresh(body):
    rt = str(body.get('refresh_token') or '')
    uid = REFRESH.pop(rt, None)          # one use each, as GoTrue rotates them
    if not uid or uid not in USERS:
        return 400, {'error': 'invalid_grant',
                     'error_description': 'Invalid Refresh Token'}
    return 200, grant(uid)


def au_update(uid, body):
    """PUT /user — a new password, or a change to user_metadata. Changing the
    password revokes the refresh tokens, which is why api.py takes a fresh
    grant afterwards rather than keeping the cookie it has."""
    if not uid:
        return 401, {'msg': 'invalid claim: missing sub claim'}
    u = USERS[uid]
    if body.get('password'):
        pw = str(body['password'])
        if len(pw) < PASSWORD_MIN:
            return 422, {'msg': 'Password should be at least %d characters'
                                % PASSWORD_MIN}
        u['password'] = pw
        for token, held in list(REFRESH.items()):
            if held == uid:
                REFRESH.pop(token, None)
    if isinstance(body.get('data'), dict):
        u['meta'].update(body['data'])
    if body.get('email'):
        u['email'] = str(body['email']).lower()
        row = find('profiles', uid)
        if row:                                   # sync_user_email(), in Python
            row['email'] = u['email']
    u['updated_at'] = now()
    return 200, public_user(uid)


def au_recover(query, body):
    addr = str(body.get('email') or '').strip().lower()
    u = by_email(addr)
    if u:
        redirect = (query.get('redirect_to') or [''])[0]
        post_mail('recover', addr, link_for('recovery', u['id'], redirect))
    return 200, {}


def au_resend(body):
    addr = str(body.get('email') or '').strip().lower()
    u = by_email(addr)
    if u and not u['confirmed']:
        post_mail('confirm', addr, link_for('signup', u['id'], ''))
    return 200, {}
def au_invite(query, body):
    addr = str(body.get('email') or '').strip().lower()
    if '@' not in addr:
        return 400, {'msg': 'Unable to validate email address: invalid format'}
    if by_email(addr):
        return 422, {'msg': 'User already registered'}
    # An invited account has no password until the link is followed; a random
    # one nobody holds is closer to that than an empty string.
    uid = new_user(addr, secrets.token_urlsafe(24), confirmed=True)
    redirect = (query.get('redirect_to') or [''])[0]
    post_mail('invite', addr, link_for('invite', uid, redirect))
    return 200, public_user(uid)


def au_admin(method, tail, query, body):
    if not tail:
        if method != 'GET':
            return 405, {'msg': 'method not allowed'}
        page = int((query.get('page') or ['1'])[0] or 1)
        per = int((query.get('per_page') or ['50'])[0] or 50)
        rows = [public_user(u['id']) for u in sorted(
            USERS.values(), key=lambda x: x['created_at'], reverse=True)]
        cut = rows[(page - 1) * per:page * per]
        return 200, {'users': cut, 'aud': 'authenticated',
                     'total': len(rows)}
    target = unquote(tail.split('/')[0])
    if target not in USERS:
        return 404, {'msg': 'User not found'}
    if method == 'GET':
        return 200, public_user(target)
    if method == 'PUT':
        u = USERS[target]
        if body.get('email_confirm') or body.get('email_confirmed_at'):
            u['confirmed'] = True
        if body.get('password'):
            u['password'] = str(body['password'])
        if body.get('email'):
            u['email'] = str(body['email']).lower()
            row = find('profiles', target)
            if row:
                row['email'] = u['email']
        if isinstance(body.get('user_metadata'), dict):
            u['meta'].update(body['user_metadata'])
        u['updated_at'] = now()
        return 200, public_user(target)
    if method == 'DELETE':
        gone = public_user(target)
        USERS.pop(target, None)
        cascade(target)
        return 200, gone
    return 405, {'msg': 'method not allowed'}
def cascade(uid):
    """`on delete cascade`, by hand. audit_log is the exception the schema
    makes on purpose: the row stays and the actor becomes null, because a log
    that loses its entries when an account goes is not a log."""
    mine = set(c['id'] for c in DB['chats'] if c.get('user_id') == uid)
    DB['profiles'][:] = [r for r in DB['profiles'] if r.get('id') != uid]
    DB['chats'][:] = [r for r in DB['chats'] if r.get('user_id') != uid]
    for table in ('messages', 'maps'):
        DB[table][:] = [r for r in DB[table]
                        if r.get('user_id') != uid and r.get('chat_id') not in mine]
    DB['usage_events'][:] = [r for r in DB['usage_events']
                             if r.get('user_id') != uid]
    for row in DB['audit_log']:
        if row.get('actor') == uid:
            row['actor'] = None
    for row in DB['app_settings']:
        if row.get('updated_by') == uid:
            row['updated_by'] = None


# --------------------------------------------------------------- PostgREST
OPS = ('eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'like', 'ilike', 'in', 'is',
       'cs', 'not')
RESERVED = ('select', 'order', 'limit', 'offset', 'on_conflict', 'or', 'and',
            'columns')


def as_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cmp_one(have, op, want):
    """One filter, as PostgREST spells it: `col=op.value`."""
    if op == 'is':
        low = str(want).lower()
        if low == 'null':
            return have is None
        return bool(have) is (low == 'true')
    if op == 'in':
        parts = [p.strip().strip('"') for p in
                 str(want).strip().lstrip('(').rstrip(')').split(',')]
        return str(have) in parts
    if op in ('like', 'ilike'):
        pat = str(want).replace('*', '').replace('%', '')
        text = '' if have is None else str(have)
        return pat.lower() in text.lower() if op == 'ilike' else pat in text
    if op == 'cs':
        return str(want) in json.dumps(have)
    a, b = as_num(have), as_num(want)
    if a is None or b is None:
        a, b = ('' if have is None else str(have)), str(want)
    if op == 'eq':
        return a == b
    if op == 'neq':
        return a != b
    if op == 'gt':
        return a > b
    if op == 'gte':
        return a >= b
    if op == 'lt':
        return a < b
    if op == 'lte':
        return a <= b
    return False


def split_op(raw):
    """`eq.7` -> ('eq', '7'). An unknown prefix is treated as equality, which
    is what a typo in a filter deserves to look like rather than a 500."""
    if '.' in raw:
        head, tail = raw.split('.', 1)
        if head in OPS:
            if head == 'not' and '.' in tail:
                inner, val = tail.split('.', 1)
                return 'not:' + inner, val
            return head, tail
    return 'eq', raw


def matches(row, column, raw):
    op, want = split_op(raw)
    if op.startswith('not:'):
        return not cmp_one(row.get(column), op[4:], want)
    return cmp_one(row.get(column), op, want)


def or_group(row, raw):
    """`or=(email.ilike.*a*,display_name.ilike.*a*)` — the one composite the
    admin search box builds. Split on the commas that are not inside a list."""
    inner = str(raw).strip()
    if inner.startswith('(') and inner.endswith(')'):
        inner = inner[1:-1]
    for term in re.split(r',(?![^()]*\))', inner):
        if term.count('.') < 2:
            continue
        column, rest = term.split('.', 1)
        if matches(row, column.strip(), rest):
            return True
    return False


def where(table, query):
    rows = [r for r in DB[table]]
    for key, values in query.items():
        if key in RESERVED:
            continue
        for raw in values:
            rows = [r for r in rows if matches(r, key, raw)]
    for raw in query.get('or', ()):
        rows = [r for r in rows if or_group(r, raw)]
    return rows


def sort(rows, query):
    """`order=updated_at.desc` and `order=chat_id.asc,seq.asc`. Applied from
    the last key to the first so the first one wins, which is what a stable
    sort gives for free."""
    for raw in reversed(list(query.get('order', ()))):
        for term in reversed(str(raw).split(',')):
            bits = term.strip().split('.')
            column = bits[0]
            if not column:
                continue
            down = 'desc' in bits
            nulls_last = 'nullslast' in bits or not down
            def key(row, c=column, n=nulls_last):
                v = row.get(c)
                if v is None:
                    return (1 if n else 0, '')
                return (0 if n else 1, v if isinstance(v, (int, float)) else str(v))
            try:
                rows.sort(key=key, reverse=down)
            except TypeError:
                rows.sort(key=lambda r, c=column: str(r.get(c)), reverse=down)
    return rows


def window(rows, query):
    off = int((query.get('offset') or ['0'])[0] or 0)
    lim = query.get('limit')
    cut = rows[off:]
    if lim:
        cut = cut[:max(0, int(lim[0] or 0))]
    return cut, off


def project(table, rows, query):
    """`select=a,b,c`, and the one embed the admin user list asks for:
    profiles(…, chats(count)). PostgREST can only do that across a foreign
    key, which is why chats.user_id points at profiles rather than auth.users;
    the fallback in admin.py exists for the instance where it cannot."""
    raw = (query.get('select') or ['*'])[0]
    embed = 'chats(count)' in raw
    names = [c.strip() for c in re.sub(r'\w+\([^)]*\)', '', raw).split(',')
             if c.strip()]
    out = []
    for row in rows:
        if not names or '*' in names:
            got = dict(row)
        else:
            got = dict((c, row.get(c)) for c in names)
        if embed and table == 'profiles':
            n = sum(1 for c in DB['chats'] if c.get('user_id') == row.get('id'))
            got['chats'] = [{'count': n}]
        out.append(got)
    return out


def scope(table, rows, role, uid, write=False):
    """The policies in supabase/policies.sql, in one function.

    Nothing above this line filters by owner — the routers pass the caller's
    token and ask for what they want, exactly as they do against Postgres. If
    a route forgets to say whose rows it means, this is what refuses."""
    if role == 'service':
        return rows
    if role == 'anon':
        return [r for r in rows if table == 'app_settings' and not write]
    admin = is_admin(uid)
    if table == 'profiles':
        return rows if admin else [r for r in rows if r.get('id') == uid]
    if table == 'chats':
        if admin and not write:
            return rows
        return [r for r in rows if r.get('user_id') == uid]
    if table in ('messages', 'maps'):
        mine = set(c['id'] for c in DB['chats'] if c.get('user_id') == uid)
        return [r for r in rows
                if r.get('user_id') == uid and r.get('chat_id') in mine]
    if table == 'usage_events':
        if write:
            return [r for r in rows if r.get('user_id') == uid]
        return rows if admin else [r for r in rows if r.get('user_id') == uid]
    if table == 'audit_log':
        return rows if admin else []
    if table == 'app_settings':
        return rows if (admin or not write) else []
    return []


def rest(method, path, query, body, role, uid, prefer):
    """Everything under /rest/v1. Returns (status, object, headers)."""
    table = unquote(path.strip('/').split('/')[0])
    if NO_TABLES:
        # STUB_NO_TABLES=1 plays a project whose SQL files have never been run:
        # GoTrue works, sign-in works, and every table read answers the way the
        # live project did before schema.sql. Verbatim from it, code and all.
        return 404, {'code': 'PGRST205', 'details': None, 'hint': None,
                     'message': "Could not find the table 'public.%s' in the "
                                'schema cache' % table}, {}
    if table == 'rpc' or path.startswith('/rpc/'):
        fn = unquote(path.strip('/').split('/')[-1])
        code, out = call_rpc(fn, body if isinstance(body, dict) else {}, role, uid)
        return code, out, {}
    if table not in TABLES:
        return 404, {'message': 'relation "public.%s" does not exist' % table,
                     'code': '42P01'}, {}
    if method == 'GET':
        rows = scope(table, where(table, query), role, uid)
        total = len(rows)
        cut, off = window(sort(rows, query), query)
        head = {}
        if 'count=exact' in (prefer or ''):
            last = off + len(cut) - 1
            head['content-range'] = '%d-%d/%d' % (off, max(off, last), total)
        return 200, project(table, cut, query), head
    if method == 'POST':
        return insert(table, query, body, role, uid, prefer)
    if method in ('PATCH', 'PUT'):
        return patch(table, query, body, role, uid, prefer)
    if method == 'DELETE':
        return drop(table, query, role, uid, prefer)
    return 405, {'message': 'method not allowed'}, {}


def guard_insert(table, row, role, uid):
    """The WITH CHECK half of a policy: may this row be created at all, by
    this caller, with these values in it?"""
    if role == 'service':
        return None
    if role != 'authenticated':
        return 'new row violates row-level security policy for table "%s"' % table
    admin = is_admin(uid)
    deny = 'new row violates row-level security policy for table "%s"' % table
    if table == 'profiles':
        return None if row.get('id') == uid else deny
    if table == 'chats':
        return None if row.get('user_id') in (uid, None) else deny
    if table in ('messages', 'maps'):
        chat = find('chats', row.get('chat_id'))
        if not chat or chat.get('user_id') != uid:
            return deny
        return None if row.get('user_id') in (uid, None) else deny
    if table == 'usage_events':
        return None if row.get('user_id') in (uid, None) else deny
    if table == 'audit_log':
        return None if admin and row.get('actor') == uid else deny
    if table == 'app_settings':
        return None if admin else deny
    return deny


DEFAULTS = {
    'profiles': {'email': '', 'display_name': '', 'role': 'user',
                 'status': 'active', 'token_cap': None, 'notes': '',
                 'last_seen_at': None},
    'chats': {'local_id': None, 'title': 'Untitled', 'tab': 'map',
              'model': '', 'version': 1, 'message_count': 0},
    'messages': {'role': 'assistant', 'kind': 'chat', 'text': '', 'meta': {},
                 'ts': 0, 'seq': 0},
    'maps': {'data': None, 'style': None, 'version': 1},
    'usage_events': {'kind': '', 'model': '', 'prompt_tokens': 0,
                     'completion_tokens': 0, 'total_tokens': 0, 'ms': 0,
                     'ok': True, 'note': ''},
    'audit_log': {'actor': None, 'actor_email': '', 'action': '',
                  'target': None, 'target_email': None, 'detail': {}},
    'app_settings': {},
}
UNIQUE = {'profiles': ('id',), 'chats': ('id',), 'maps': ('chat_id',),
          'app_settings': ('id',), 'messages': ('chat_id', 'seq')}


def fresh(table, sent, uid):
    """Column defaults, including `default auth.uid()` on the owner column —
    the reason a route can insert a chat without naming who it belongs to."""
    row = dict(DEFAULTS.get(table) or {})
    row.update(dict((k, v) for k, v in sent.items() if v is not None or k in row))
    if table in ('chats', 'messages', 'maps', 'usage_events'):
        row['user_id'] = sent.get('user_id') or uid
    if table in ('profiles', 'chats', 'maps'):
        row.setdefault('id', sent.get('id') or uid4())
    if table in SEQ:
        SEQ[table] += 1
        row['id'] = SEQ[table]
    stamp = iso()
    row.setdefault('created_at', stamp)
    if table in ('profiles', 'chats', 'maps', 'app_settings'):
        row['updated_at'] = sent.get('updated_at') or stamp
    return row


def insert(table, query, body, role, uid, prefer):
    """POST, with or without `resolution=merge-duplicates`. The conflict target
    is whatever `on_conflict` names, because for a one-row-per-chat table the
    primary key is not the column that matters."""
    sent = body if isinstance(body, list) else [body]
    merge = 'merge-duplicates' in (prefer or '')
    on = [c.strip() for c in (query.get('on_conflict') or [''])[0].split(',')
          if c.strip()] or list(UNIQUE.get(table) or ())
    out = []
    for raw in sent:
        if not isinstance(raw, dict):
            return 400, {'message': 'expected a JSON object'}, {}
        row = fresh(table, raw, uid)
        bad = guard_insert(table, row, role, uid)
        if bad:
            return 403, {'message': bad, 'code': '42501'}, {}
        clash = None
        if on:
            for have in DB[table]:
                if all(have.get(c) == row.get(c) for c in on):
                    clash = have
                    break
        if clash is not None and not merge:
            return 409, {'message': 'duplicate key value violates unique '
                                    'constraint "%s_pkey"' % table,
                         'code': '23505'}, {}
        if clash is not None:
            keep = {'id': clash.get('id'), 'created_at': clash.get('created_at')}
            clash.update(dict((k, v) for k, v in raw.items() if k != 'id'))
            clash.update(keep)
            clash['updated_at'] = raw.get('updated_at') or iso()
            out.append(clash)
            continue
        DB[table].append(row)
        out.append(row)
    save()
    if 'return=representation' not in (prefer or ''):
        return 201, None, {}
    return 201, project(table, out, query), {}


GUARDED = ('id', 'email', 'role', 'status', 'token_cap', 'notes', 'created_at')


def guard_columns(sent, role, uid):
    """guard_profile_columns(), in Python: a person may change their own
    display_name and nothing else that matters. Written as a trigger there and
    a function here for the same reason — an UPDATE policy sees the row
    arriving but not the row it replaces, so it cannot tell a rename from a
    promotion."""
    if role != 'authenticated' or is_admin(uid):
        return sent
    return dict((k, v) for k, v in sent.items() if k not in GUARDED)


def last_admin(rows, sent):
    """keep_one_admin(). The database half of the guard in admin.py, so the
    lockout is refused even by a hand at the SQL editor."""
    losing = sent.get('role') not in (None, 'admin') or sent.get('status') == 'suspended'
    if not losing:
        return False
    for row in rows:
        if row.get('role') != 'admin' or row.get('status') != 'active':
            continue
        others = [r for r in DB['profiles']
                  if r.get('id') != row.get('id') and r.get('role') == 'admin'
                  and r.get('status') == 'active']
        if not others:
            return True
    return False


def patch(table, query, body, role, uid, prefer):
    if not isinstance(body, dict):
        return 400, {'message': 'expected a JSON object'}, {}
    rows = scope(table, where(table, query), role, uid, write=True)
    if table in ('usage_events', 'audit_log'):
        return 403, {'message': 'permission denied for table %s' % table,
                     'code': '42501'}, {}
    sent = dict(body)
    if table == 'profiles':
        sent = guard_columns(sent, role, uid)
        if last_admin(rows, sent):
            return 409, {'message': 'that is the only administrator left',
                         'code': 'PT409'}, {}
    for row in rows:
        row.update(sent)
        if table in ('profiles', 'chats', 'maps', 'app_settings'):
            row['updated_at'] = sent.get('updated_at') or iso()
    save()
    if 'return=representation' not in (prefer or ''):
        return 204, None, {}
    return 200, project(table, rows, query), {}


def drop(table, query, role, uid, prefer):
    if table in ('usage_events', 'audit_log'):
        return 403, {'message': 'permission denied for table %s' % table,
                     'code': '42501'}, {}
    rows = scope(table, where(table, query), role, uid, write=True)
    if table == 'profiles' and role != 'service' and not is_admin(uid):
        rows = []
    gone = []
    for row in rows:
        DB[table].remove(row)
        gone.append(row)
        if table == 'profiles':
            cascade(row.get('id'))
        if table == 'chats':
            cid = row.get('id')
            for kid in ('messages', 'maps'):
                DB[kid][:] = [r for r in DB[kid] if r.get('chat_id') != cid]
    save()
    if 'return=representation' not in (prefer or ''):
        return 204, None, {}
    return 200, project(table, gone, query), {}


# ---------------------------------------------------------------------- RPCs
def call_rpc(fn, args, role, uid):
    if fn == 'usage_this_month':
        return 200, month_tokens(uid)
    if fn == 'replace_messages':
        return rpc_replace(args, role, uid)
    if fn in ('admin_overview', 'admin_user_usage', 'admin_usage_daily',
              'admin_usage_by_user'):
        if role != 'service' and not is_admin(uid):
            return 403, {'message': 'admin only', 'code': 'PT403'}
        if fn == 'admin_overview':
            return 200, rpc_overview()
        if fn == 'admin_user_usage':
            return 200, rpc_user_usage(str(args.get('p_user') or ''))
        days = int(args.get('p_days') or 30)
        if fn == 'admin_usage_daily':
            return 200, rpc_daily(days)
        return 200, rpc_by_user(days)
    if fn in ('is_admin', 'acting_role'):
        return 200, is_admin(uid) if fn == 'is_admin' else role
    return 404, {'message': 'Could not find the function public.%s' % fn,
                 'code': 'PGRST202'}


def month_tokens(uid, all_time=False):
    tag = iso()[:7]
    return sum(int(r.get('total_tokens') or 0) for r in DB['usage_events']
               if r.get('user_id') == uid
               and (all_time or str(r.get('created_at') or '')[:7] == tag))


def rpc_replace(args, role, uid):
    """replace_messages(). One transaction there, one lock here, and the same
    three refusals: no such chat, not yours, too many rows."""
    cid = str(args.get('p_chat') or '')
    rows = args.get('p_rows')
    chat = find('chats', cid)
    if not chat:
        return 404, {'message': 'no such chat', 'code': 'PT404'}
    if role != 'service' and chat.get('user_id') != uid:
        return 403, {'message': 'that chat is not yours', 'code': 'PT403'}
    if not isinstance(rows, list):
        return 400, {'message': 'p_rows has to be a JSON array', 'code': 'PT400'}
    if len(rows) > 2000:
        return 413, {'message': 'too many messages in one save', 'code': 'PT413'}
    owner = chat.get('user_id')
    DB['messages'][:] = [r for r in DB['messages'] if r.get('chat_id') != cid]
    for i, raw in enumerate(rows):
        m = raw if isinstance(raw, dict) else {}
        SEQ['messages'] += 1
        DB['messages'].append({
            'id': SEQ['messages'], 'chat_id': cid, 'user_id': owner,
            'seq': int(m.get('seq') if str(m.get('seq') or '').isdigit() else i),
            'role': m.get('role') if m.get('role') in ('user', 'assistant', 'system') else 'assistant',
            'kind': m.get('kind') if m.get('kind') in ('chat', 'map', 'plan', 'note', 'error') else 'chat',
            'text': str(m.get('text') or ''),
            'meta': m['meta'] if isinstance(m.get('meta'), dict) else {},
            'ts': int(m.get('ts') or 0) if str(m.get('ts') or '').isdigit() else 0,
            'created_at': iso(),
        })
    chat['message_count'] = len(rows)
    save()
    return 200, len(rows)


def days_ago(n):
    return iso(now() - timedelta(days=n))[:10]


def rpc_overview():
    """The keys admin_overview() returns, spelled the same way, because the
    panel reads them by name and a rename here is a blank tile there."""
    month, today = iso()[:7], iso()[:10]
    week = days_ago(7)
    people = DB['profiles']
    ev = DB['usage_events']
    mine = [e for e in ev if str(e.get('created_at'))[:7] == month]
    day = [e for e in ev if str(e.get('created_at'))[:10] == today]
    spans = sorted(int(e.get('ms') or 0) for e in mine)
    return {
        'month': month,
        'users': len(people),
        'admins': sum(1 for p in people if p.get('role') == 'admin'),
        'suspended': sum(1 for p in people if p.get('status') == 'suspended'),
        'new_7d': sum(1 for p in people if str(p.get('created_at'))[:10] >= week),
        'seen_7d': sum(1 for p in people if str(p.get('last_seen_at') or '')[:10] >= week),
        'chats': len(DB['chats']),
        'maps': len(DB['maps']),
        'messages': len(DB['messages']),
        'calls_month': len(mine),
        'calls_today': len(day),
        'failed_today': sum(1 for e in day if not e.get('ok')),
        'tokens_month': sum(int(e.get('total_tokens') or 0) for e in mine),
        'tokens_today': sum(int(e.get('total_tokens') or 0) for e in day),
        'ms_median': spans[len(spans) // 2] if spans else 0,
    }


def rpc_user_usage(target):
    month = iso()[:7]
    prof = find('profiles', target) or {}
    cap = prof.get('token_cap')
    if cap is None:
        cap = int(settings_row().get('default_token_cap') or 0)
    ev = [e for e in DB['usage_events'] if e.get('user_id') == target]
    mine = [e for e in ev if str(e.get('created_at'))[:7] == month]
    chats = [c for c in DB['chats'] if c.get('user_id') == target]
    return {
        'month': month, 'cap': int(cap or 0), 'unlimited': int(cap or 0) <= 0,
        'used': sum(int(e.get('total_tokens') or 0) for e in mine),
        'used_all': sum(int(e.get('total_tokens') or 0) for e in ev),
        'calls': len(mine), 'failed': sum(1 for e in mine if not e.get('ok')),
        'last_call': max([str(e.get('created_at')) for e in ev] or ['']) or None,
        'chats': len(chats),
        'messages': sum(int(c.get('message_count') or 0) for c in chats),
        'maps': sum(1 for m in DB['maps'] if m.get('user_id') == target),
    }


def rpc_daily(days):
    """One row per day including the quiet ones — the chart needs the zeros to
    draw a gap rather than close it up."""
    span = max(1, min(365, days))
    out = []
    for back in range(span - 1, -1, -1):
        tag = days_ago(back)
        ev = [e for e in DB['usage_events']
              if str(e.get('created_at'))[:10] == tag]
        out.append({
            'day': tag,
            'tokens': sum(int(e.get('total_tokens') or 0) for e in ev),
            'calls': len(ev),
            'failed': sum(1 for e in ev if not e.get('ok')),
            'people': len(set(e.get('user_id') for e in ev)),
        })
    return out


def rpc_by_user(days):
    since = days_ago(max(1, min(365, days)))
    per = {}
    for e in DB['usage_events']:
        if str(e.get('created_at'))[:10] < since:
            continue
        got = per.setdefault(e.get('user_id'), {'tokens': 0, 'calls': 0, 'last': ''})
        got['tokens'] += int(e.get('total_tokens') or 0)
        got['calls'] += 1
        got['last'] = max(got['last'], str(e.get('created_at') or ''))
    out = []
    for target, got in per.items():
        prof = find('profiles', target) or {}
        out.append({'user_id': target, 'email': prof.get('email') or '',
                    'display_name': prof.get('display_name') or '',
                    'tokens': got['tokens'], 'calls': got['calls'],
                    'last_call': got['last'] or None})
    out.sort(key=lambda r: r['tokens'], reverse=True)
    return out[:50]


# ---------------------------------------------------------------- the socket
class Stub(BaseHTTPRequestHandler):
    server_version = 'fake-supabase/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *a):
        if QUIET:
            return
        sys.stderr.write('  %s %s\n' % (self.address_string(), fmt % a))

    def _head(self):
        return dict((k.lower(), v) for k, v in self.headers.items())

    def _body(self):
        n = int(self.headers.get('content-length') or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(min(n, 8 * 1024 * 1024))
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _send(self, code, obj, head=None):
        raw = b'' if obj is None else json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(raw)))
        for k, v in (head or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _serve(self, method):
        bits = urlsplit(self.path)
        query = parse_qs(bits.query, keep_blank_values=True)
        head = self._head()
        body = self._body() if method in ('POST', 'PUT', 'PATCH') else {}
        path = bits.path
        if path in ('/', '/health'):
            return self._send(200, {'ok': True, 'stub': 'fake-supabase',
                                    'users': len(USERS)})
        if path == '/mail':
            return self._send(200, {'mail': MAIL[-40:]})
        if path == '/reset' and method == 'POST':
            with LOCK:
                wipe()
            return self._send(200, {'ok': True, 'wiped': True})
        key = str(head.get('apikey') or '')
        if key not in (ANON_KEY, SERVICE_KEY, ''):
            return self._send(401, {'message': 'Invalid API key'})
        with LOCK:
            uid, role = whoami(head)
            if role == 'dead':
                if path.startswith('/rest/v1'):
                    return self._send(401, {'message': 'JWT expired',
                                            'code': 'PGRST301'})
                return self._send(401, {'msg': 'invalid JWT: unable to parse '
                                               'or verify signature'})
            if path.startswith('/auth/v1'):
                code, out = gotrue(method, path[len('/auth/v1'):] or '/',
                                   query, body, head, role, uid)
                return self._send(code, out)
            if path.startswith('/rest/v1'):
                code, out, extra = rest(method, path[len('/rest/v1'):] or '/',
                                        query, body, role, uid,
                                        head.get('prefer', ''))
                return self._send(code, out, extra)
        return self._send(404, {'message': 'no such path: ' + path})

    def do_GET(self):
        self._guard('GET')

    def do_POST(self):
        self._guard('POST')

    def do_PUT(self):
        self._guard('PUT')

    def do_PATCH(self):
        self._guard('PATCH')

    def do_DELETE(self):
        self._guard('DELETE')

    def do_OPTIONS(self):
        self._send(204, None, {'access-control-allow-origin': '*'})

    def _guard(self, method):
        """A stub that returns a traceback on the wire teaches nothing about
        the real thing, which answers 500 with a JSON body."""
        try:
            self._serve(method)
        except Exception as e:                                   # noqa: BLE001
            self._send(500, {'message': '%s: %s' % (e.__class__.__name__, e),
                             'code': 'XX000'})


def wipe():
    for table in TABLES:
        DB[table] = []
    for k in SEQ:
        SEQ[k] = 0
    USERS.clear()
    TOKENS.clear()
    REFRESH.clear()
    del MAIL[:]
    settings_row()
    save()


def seed(admin_email):
    """One confirmed administrator, so the panel can be opened on the first
    run. The password is printed; it is a stub, and a secret nobody can read
    is a stub nobody can sign into."""
    if not admin_email or by_email(admin_email):
        return
    uid = new_user(admin_email, 'stub-password-10', {'display_name': 'Stub Admin'})
    find('profiles', uid)['role'] = 'admin'
    print('  seeded admin  %s / stub-password-10' % admin_email)


def main(argv=None):
    global STORE, CONFIRM, QUIET, ANON_KEY, SERVICE_KEY
    ap = argparse.ArgumentParser(description='A Supabase-shaped stub.')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8779)
    ap.add_argument('--store', default='', help='keep the data in this JSON file')
    ap.add_argument('--confirm', action='store_true',
                    help='require the confirmation link, as production does')
    ap.add_argument('--admin', default='', help='seed one admin at this address')
    ap.add_argument('--anon-key', default=ANON_KEY)
    ap.add_argument('--service-key', default=SERVICE_KEY)
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)
    STORE = args.store or None
    CONFIRM = bool(args.confirm)
    QUIET = bool(args.quiet)
    ANON_KEY, SERVICE_KEY = args.anon_key, args.service_key
    load()
    settings_row()
    seed(args.admin.strip().lower())
    save()
    srv = ThreadingHTTPServer((args.host, args.port), Stub)
    print('\n  fake supabase on http://%s:%d   %s'
          % (args.host, args.port, 'confirmations on' if CONFIRM else
             'confirmations off'))
    print('  put these three in .env, then run serve.py:\n')
    print('    COGNIX_SUPABASE_URL=http://%s:%d' % (args.host, args.port))
    print('    COGNIX_SUPABASE_ANON_KEY=%s' % ANON_KEY)
    print('    COGNIX_SUPABASE_SERVICE_KEY=%s\n' % SERVICE_KEY)
    print('  mail is printed here and listed at /mail · ^C to stop\n', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n  stopped')
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
