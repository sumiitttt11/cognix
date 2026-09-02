"""Supabase, spoken over HTTP: GoTrue for who you are, PostgREST for what
you own.

The important rule lives here. Every read and write on behalf of a signed-in
person carries *that person's* access token, so the row-level security in
supabase/policies.sql is what decides what they can see — not this file. The
service key appears in exactly one set of functions, all named admin_*, and
every one of them is called only after api.py has checked the caller's role.

No Supabase SDK: the SDK is an npm package, this project has no build step,
and the two endpoints it would wrap are a POST and a GET.
"""
from urllib.parse import quote, urlencode

from . import config, hclient


def ready():
    return bool(config.CLOUD)


def _auth_head(token=None, admin=False):
    """apikey is which project this is; authorization is who is asking.

    Both headers go out on every call, which is what Supabase's own clients
    do, and both of its key shapes are accepted in either — the older JWT
    (`eyJ…`) and the newer prefixed pair (`sb_publishable_…`,
    `sb_secret_…`). When a person's access token is passed it replaces the key
    in authorization, and that is the whole of how the policies learn whose
    request this is."""
    key = config.SUPABASE_SERVICE_KEY if admin else config.SUPABASE_ANON_KEY
    head = {'apikey': key}
    head['authorization'] = 'Bearer ' + (token or key)
    return head


# --------------------------------------------------------------- GoTrue
def signup(email, password, name=''):
    """With email confirmations on, the reply has a user and no session."""
    body = {'email': email, 'password': password,
            'data': {'display_name': name} if name else {}}
    if config.PUBLIC_URL:
        body['gotrue_meta_security'] = {}
    url = config.AUTH_URL + '/signup'
    if config.PUBLIC_URL:
        url += '?redirect_to=' + quote(config.PUBLIC_URL + '/app/auth/', safe='')
    return hclient.post(url, headers=_auth_head(), body=body)


def login(email, password):
    return hclient.post(config.AUTH_URL + '/token?grant_type=password',
                        headers=_auth_head(),
                        body={'email': email, 'password': password})


def refresh(refresh_token):
    return hclient.post(config.AUTH_URL + '/token?grant_type=refresh_token',
                        headers=_auth_head(),
                        body={'refresh_token': refresh_token})


def logout(access_token):
    return hclient.post(config.AUTH_URL + '/logout',
                        headers=_auth_head(access_token), body={})


def whoami(access_token):
    return hclient.get(config.AUTH_URL + '/user', headers=_auth_head(access_token))


def update_self(access_token, fields):
    return hclient.call('PUT', config.AUTH_URL + '/user',
                        headers=_auth_head(access_token), body=fields)


def recover(email):
    """Sends the reset mail. The reply is 200 whether or not the address
    exists, which is the behaviour we want anyway."""
    url = config.AUTH_URL + '/recover'
    if config.PUBLIC_URL:
        url += '?redirect_to=' + quote(config.PUBLIC_URL + '/app/auth/#reset', safe='')
    return hclient.post(url, headers=_auth_head(), body={'email': email})


def resend(email):
    return hclient.post(config.AUTH_URL + '/resend', headers=_auth_head(),
                        body={'type': 'signup', 'email': email})


# ------------------------------------------------------- GoTrue, as the owner
def admin_users(page=1, per_page=50):
    return hclient.get(config.AUTH_URL + '/admin/users?page=%d&per_page=%d'
                       % (max(1, page), min(200, max(1, per_page))),
                       headers=_auth_head(admin=True))


def admin_user(uid):
    return hclient.get(config.AUTH_URL + '/admin/users/' + quote(str(uid)),
                       headers=_auth_head(admin=True))


def admin_update_user(uid, fields):
    return hclient.call('PUT', config.AUTH_URL + '/admin/users/' + quote(str(uid)),
                        headers=_auth_head(admin=True), body=fields)


def admin_delete_user(uid):
    return hclient.delete(config.AUTH_URL + '/admin/users/' + quote(str(uid)),
                          headers=_auth_head(admin=True))


def admin_invite(email):
    url = config.AUTH_URL + '/invite'
    if config.PUBLIC_URL:
        url += '?redirect_to=' + quote(config.PUBLIC_URL + '/app/auth/', safe='')
    return hclient.post(url, headers=_auth_head(admin=True), body={'email': email})


# ------------------------------------------------------------- PostgREST
def _rest(method, table, token, admin=False, params=None, body=None, prefer=None):
    url = config.REST_URL + '/' + quote(table)
    if params:
        url += '?' + urlencode(params, safe='.,()*:')
    head = _auth_head(token, admin=admin)
    if prefer:
        head['prefer'] = prefer
    return hclient.call(method, url, headers=head, body=body)


def select(table, token, admin=False, count=False, **params):
    """params are PostgREST's own: select='a,b', order='ts.desc', limit=20,
    and filters like id='eq.7'. Nothing is interpolated into SQL anywhere in
    this project — PostgREST builds the statement and the policies bound it."""
    return _rest('GET', table, token, admin=admin, params=params,
                 prefer='count=exact' if count else None)


def insert(table, token, rows, admin=False, upsert=False):
    prefer = 'return=representation'
    if upsert:
        prefer += ',resolution=merge-duplicates'
    return _rest('POST', table, token, admin=admin, body=rows, prefer=prefer)


def upsert(table, token, rows, on_conflict, admin=False):
    """Insert-or-update against a named unique column. PostgREST needs the
    column spelled out; without it the merge resolves on the primary key,
    which for a one-row-per-chat table is not the column that matters."""
    return _rest('POST', table, token, admin=admin,
                 params={'on_conflict': on_conflict}, body=rows,
                 prefer='return=representation,resolution=merge-duplicates')


def update(table, token, fields, admin=False, **params):
    return _rest('PATCH', table, token, admin=admin, params=params,
                 body=fields, prefer='return=representation')


def remove(table, token, admin=False, **params):
    return _rest('DELETE', table, token, admin=admin, params=params)


def rpc(fn, token, args=None, admin=False):
    return hclient.post(config.REST_URL + '/rpc/' + quote(fn),
                        headers=_auth_head(token, admin=admin), body=args or {})


def probe(timeout=4, tries=1):
    """Is the schema actually there? One read of app_settings, which is the one
    table the policies let the anon key see.

    On a short leash and asked exactly once, because this runs while the startup
    banner is being printed: a Supabase project that is paused or slow must not
    hold the port shut. A 404 here is the useful answer — PostgREST answering
    that it has never heard of the table is what 'the SQL files have not been
    run yet' looks like from outside."""
    return hclient.get(config.REST_URL + '/app_settings?select=id&limit=1',
                       headers=_auth_head(), timeout=timeout, tries=tries)


def total(reply):
    """PostgREST puts the count in content-range as `0-24/1337`."""
    rng = ''
    for k, v in (reply.headers or {}).items():
        if k.lower() == 'content-range':
            rng = v
            break
    if '/' in rng:
        tail = rng.split('/')[-1].strip()
        if tail.isdigit():
            return int(tail)
    return len(reply.body) if isinstance(reply.body, list) else 0


def one(reply):
    """PostgREST returns a list even for a single row."""
    b = reply.body
    if isinstance(b, list):
        return b[0] if b else None
    return b if isinstance(b, dict) and b else None
