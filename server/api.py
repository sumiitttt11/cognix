"""/api/* — everything the browser is allowed to ask for.

The rule this file exists to enforce: a request proves who it is with a cookie
it cannot read, and then every read and write it causes is made *as that
person* against PostgREST, so the policies in supabase/policies.sql decide
what comes back. No route here builds a query that trusts a user id from the
request body.

Two modes. Without SUPABASE_URL the app is what it has always been — one
browser, localStorage, no accounts — and these endpoints say so plainly
instead of half-working. With it, /api/auth/* is real and /api/data/* is the
only way anything is stored.
"""
import time

from . import admin, config, gateway, limits, sessions, shape, supa, wire

PROFILE_COLS = ('id,email,display_name,role,status,token_cap,created_at,'
                'updated_at,last_seen_at')


def handle(req):
    """Route one call. Returns a wire.Res, always."""
    tail = req.parts[1:]
    if not tail:
        return wire.ok({'ok': True, 'mode': config.mode()})
    top = tail[0]
    try:
        if top == 'config':
            return _config(req)
        if req.unsafe and not sessions.csrf_ok(req.jar, req.h(config.CSRF_HEADER)):
            return wire.fail(403, 'That request did not carry its CSRF token. '
                                  'Reload the page and try again.')
        if top == 'auth':
            return _auth(req, tail[1:])
        if top not in ('data', 'usage', 'profile', 'admin'):
            return wire.fail(404, 'No such endpoint.')
        if not supa.ready():
            return wire.fail(503, 'This instance runs without Supabase, so there '
                                  'are no accounts and nothing to store. Set '
                                  'SUPABASE_URL to turn them on.')
        sess, fresh, refused = need(req)
        if refused:
            return refused
        res = _signed_in(req, tail, sess)
        if fresh and res is not None:
            res.cookies = list(res.cookies) + sessions.seal(sess, req.secure)
        return res
    except shape.Bad as e:
        return wire.fail(400, e.msg, e.field)


def _signed_in(req, tail, sess):
    """Past this line there is a person, and every call below carries their
    token. The role and status come from Postgres, never from the cookie —
    an admin demoted a minute ago still holds a cookie that says 'admin'."""
    prof = profile_of(sess)
    if (prof or {}).get('status') == 'suspended':
        return wire.fail(403, 'This account is suspended. An administrator can lift that.')
    top = tail[0]
    if top == 'admin':
        return admin.handle(req, sess, prof, tail[1:])
    if top == 'data':
        return _data(req, tail[1:], sess)
    if top == 'usage':
        if req.method != 'GET':
            return wire.fail(405, 'That endpoint takes a GET.')
        return wire.ok(usage_of(sess, prof))
    if top == 'profile':
        return _profile(req, sess, prof)
    return wire.fail(404, 'No such endpoint.')


def who(req):
    """(session, fresh) for a request that may or may not have one."""
    raw = sessions.read(req.jar)
    if not raw:
        return None, False
    return sessions.live(raw)


def need(req):
    """(session, fresh, refusal). Exactly one of session and refusal is set."""
    raw = sessions.read(req.jar)
    if not raw:
        return None, False, wire.fail(401, 'Please sign in.')
    sess, fresh = sessions.live(raw)
    if not sess:
        return None, False, wire.Res(
            401, {'error': 'That sign-in has run out. Please sign in again.'},
            sessions.clear(req.secure))
    return sess, fresh, None


def profile_of(sess, force=False):
    """The caller's own row, read with the caller's own token."""
    uid = sess.get('u') or ''
    if not uid:
        return {}
    if force:
        limits.PROFILE.drop(uid)
    got = limits.PROFILE.get(uid)
    if got is not None:
        return got
    rep = supa.select('profiles', sess.get('at'), select=PROFILE_COLS,
                      id='eq.' + uid, limit=1)
    if not rep.ok:
        return {}
    row = supa.one(rep) or _make_profile(sess)
    return limits.PROFILE.put(uid, row or {})


def _make_profile(sess):
    """Signup normally makes this row from a trigger. This is the repair for a
    user that predates the trigger — an upsert as themselves, which the
    owner-only insert policy allows and nothing else does."""
    rep = supa.insert('profiles', sess.get('at'), [{
        'id': sess.get('u'), 'email': sess.get('e'),
        'display_name': sess.get('n') or '',
    }], upsert=True)
    return supa.one(rep) if rep.ok else {}


def usage_of(sess, prof=None):
    """This month's tokens against this month's ceiling.

    A cap of 0 means no ceiling; that is how an administrator says 'let them
    work'. The sum is an RPC because doing it here would mean pulling every
    usage row across the wire to add them up."""
    uid = sess.get('u') or ''
    raw = (prof or {}).get('token_cap')
    cap = config.TOKEN_CAP if raw is None else int(raw or 0)
    used = limits.USAGE.get(uid)
    if used is None:
        used = limits.USAGE.put(uid, _sum_usage(sess))
    return {'used': used, 'cap': cap, 'unlimited': cap <= 0,
            'left': max(0, cap - used) if cap > 0 else None,
            'month': time.strftime('%Y-%m')}


def _sum_usage(sess):
    rep = supa.rpc('usage_this_month', sess.get('at'))
    if not rep.ok:
        return 0
    b = rep.body
    if isinstance(b, bool):
        return 0
    if isinstance(b, (int, float)):
        return int(b)
    if isinstance(b, list):
        b = b[0] if b and isinstance(b[0], dict) else {}
    if isinstance(b, dict):
        for k in ('total', 'total_tokens', 'usage_this_month', 'sum'):
            if isinstance(b.get(k), (int, float)):
                return int(b[k])
    return 0


def settings_now():
    """app_settings, read with the anon key. The policy on that table says
    exactly this: anybody may read the row, only an admin may write it.

    The read itself lives in gateway.py, because the gateway columns are on the
    same row and a model call has to resolve them before it can go anywhere —
    one fetch, one cache entry, one clear() between the two callers."""
    return gateway.row()


def _config(req):
    """The only endpoint an unauthenticated page may call, and the reason the
    app asks for it before drawing anything: it also mints the CSRF cookie
    that every later state-changing call has to echo back — and, for a visitor
    with no account, the signed cookie their free allowance is counted in."""
    out = dict(_public_settings())
    cookies = []
    if not req.jar.get(config.CSRF_COOKIE):
        cookies.append(sessions.csrf_cookie(req.secure))
    guest = None
    if supa.ready() and config.GUEST and not sessions.read(req.jar):
        got = sessions.guest_read(req.jar)
        if not got:
            got = sessions.guest_new()
            cookies += sessions.guest_seal(got, req.secure)
        guest = guest_json(got)
    out.update({
        'mode': config.mode(),
        'cloud': supa.ready(),
        # True only where a visitor is not let in at all. With guests allowed
        # the app draws itself for anybody and asks for an account when the
        # allowance runs out, so this is normally False even in cloud mode.
        'auth_required': bool(supa.ready() and not config.GUEST),
        'guest': guest,
        'password_min': config.PASSWORD_MIN,
        'brand': 'Cognix',
    })
    return wire.ok(out, cookies=cookies or None)


def guest_json(got):
    """What the page is told about a free trial. The chat number is the one it
    shows and enforces; the call number is what this server actually counts."""
    used = int((got or {}).get('n') or 0)
    return {'chats': config.GUEST_CHATS, 'calls': config.GUEST_CALLS,
            'used': used, 'left': max(0, config.GUEST_CALLS - used)}


def me_json(sess, prof, usage=None):
    """What the browser is told about the person using it. `verified` is the
    only field the interface treats as a gate, and it comes from GoTrue."""
    prof = prof or {}
    return {
        'id': sess.get('u') or '',
        'email': prof.get('email') or sess.get('e') or '',
        'name': prof.get('display_name') or sess.get('n') or '',
        'role': prof.get('role') or 'user',
        'status': prof.get('status') or 'active',
        'verified': bool(sess.get('v')),
        'created_at': prof.get('created_at') or '',
        'usage': usage if usage is not None else usage_of(sess, prof),
    }


def _now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _slow(retry, what='requests'):
    return wire.Res(429, {'error': 'Too many %s. Try again in %d seconds.'
                                   % (what, retry), 'retry_after': retry},
                    head={'retry-after': str(retry)})


def _touch(sess):
    """last_seen_at, at most once every fifteen minutes per person. The admin
    panel is the only reader, and it does not need the truth to the second."""
    uid = sess.get('u') or ''
    if not uid or limits.TOUCHED.get(uid):
        return
    limits.TOUCHED.put(uid, 1)
    supa.update('profiles', sess.get('at'), {'last_seen_at': _now_iso()},
                id='eq.' + uid)


# ------------------------------------------------------------------ auth
def _auth(req, tail):
    what = tail[0] if tail else ''
    if what == 'me':
        return _me(req)
    if req.method != 'POST':
        return wire.fail(405, 'That endpoint takes a POST.')
    if what == 'logout':
        return _logout(req)
    if not supa.ready():
        return wire.fail(503, 'Accounts need Supabase. This instance is running '
                              'local-only, so there is nothing to sign in to.')
    body = req.obj()
    fn = {'signup': _signup, 'login': _login, 'recover': _recover,
          'resend': _resend, 'reset': _reset, 'adopt': _adopt,
          'password': _password}.get(what)
    if not fn:
        return wire.fail(404, 'No such endpoint.')
    return fn(req, body)


def _me(req):
    """Never 401s. The front-end calls this on every load to find out whether
    it is drawing an app or a sign-in page, and a 401 there is noise."""
    if not supa.ready():
        return wire.ok({'user': None, 'mode': 'local'})
    sess, fresh = who(req)
    if not sess:
        return wire.ok({'user': None, 'mode': 'cloud'})
    prof = profile_of(sess)
    out = wire.ok({'user': me_json(sess, prof), 'mode': 'cloud'})
    if fresh:
        out.cookies = sessions.seal(sess, req.secure)
    _touch(sess)
    return out


def _logout(req):
    """Clears the cookie whatever happens. Telling GoTrue is a courtesy that
    revokes the refresh token; a failure there must not leave the person
    stuck signed in on this side."""
    sess = sessions.read(req.jar)
    if sess and supa.ready() and sess.get('at'):
        supa.logout(sess['at'])
    if sess and sess.get('u'):
        limits.PROFILE.drop(sess['u'])
        limits.USAGE.drop(sess['u'])
    return wire.ok({'ok': True, 'user': None}, cookies=sessions.clear(req.secure))


def _grant_in(req, grant):
    """A GoTrue grant becomes our cookie. Shared by sign-up, sign-in and the
    confirmation link, so the three cannot drift apart."""
    if not isinstance(grant, dict) or not grant.get('access_token'):
        return wire.fail(502, 'Supabase returned a sign-in this server cannot use.')
    uid = str((grant.get('user') or {}).get('id') or '')
    prof = {}
    if uid:
        rep = supa.select('profiles', grant['access_token'], select=PROFILE_COLS,
                          id='eq.' + uid, limit=1)
        if rep.ok:
            prof = supa.one(rep) or {}
    sess = sessions.from_grant(grant, prof)
    if not sess.get('u') or not sess.get('rt'):
        return wire.fail(502, 'Supabase returned a sign-in this server cannot use.')
    if not prof:
        prof = _make_profile(sess) or {}
    if (prof or {}).get('status') == 'suspended':
        return wire.fail(403, 'This account is suspended. An administrator can lift that.')
    sess['r'] = prof.get('role') or 'user'
    sess['n'] = prof.get('display_name') or sess.get('n') or ''
    limits.PROFILE.put(sess['u'], prof)
    limits.USAGE.drop(sess['u'])
    # There is an account now, so the free-trial tally goes. Otherwise somebody
    # who signed up two calls in would still be carrying a spent allowance if
    # they ever signed out again.
    return wire.ok({'user': me_json(sess, prof)},
                   cookies=(sessions.seal(sess, req.secure)
                            + sessions.guest_clear(req.secure)))


def _signup(req, body):
    st = settings_now()
    if not (config.SIGNUPS and shape.yes(st.get('signups_open'), True)):
        return wire.fail(403, 'New accounts are closed at the moment. An '
                              'administrator can send you an invite.')
    addr = shape.email(body.get('email'))
    name = shape.s(body.get('name'), 80, 'name')
    pw = shape.password(body.get('password'), addr)
    fine, retry = limits.hit('signup:' + req.ip, 5, 3600)
    if not fine:
        return _slow(retry)
    rep = supa.signup(addr, pw, name)
    if not rep.ok:
        low = (rep.msg('') or '').lower()
        if 'already' in low or rep.status == 422:
            return wire.fail(409, 'There is already an account on that address. '
                                  'Sign in, or use the reset link.')
        return wire.upstream(rep, 'That sign-up was refused.')
    grant = rep.body if isinstance(rep.body, dict) else {}
    if not grant.get('access_token'):
        # Email confirmations are on, which is the setting we want in production.
        return wire.ok({'verify': True, 'email': addr,
                        'message': 'Check %s for the confirmation link, then '
                                   'sign in.' % addr})
    return _grant_in(req, grant)


def _login(req, body):
    addr = shape.email(body.get('email'))
    pw = body.get('password') if isinstance(body.get('password'), str) else ''
    if not pw:
        raise shape.Bad('Enter your password.', 'password')
    keys = ('login:' + req.ip, 'login:' + addr)
    for k in keys:
        fine, retry = limits.hit(k, config.LOGIN_TRIES, config.LOGIN_WINDOW)
        if not fine:
            return _slow(retry, 'attempts')
    rep = supa.login(addr, pw)
    if not rep.ok:
        low = (rep.msg('') or '').lower()
        if 'confirm' in low:
            return wire.fail(403, 'That address has not been confirmed yet. '
                                  'Check your inbox, or ask for a new link.')
        if rep.status in (400, 401):
            return wire.fail(401, 'That email and password do not match.')
        return wire.upstream(rep, 'Sign-in was refused.')
    for k in keys:
        limits.forget(k)
    return _grant_in(req, rep.body if isinstance(rep.body, dict) else {})


def _recover(req, body):
    """The reply is the same sentence whether or not the address exists. Any
    other behaviour turns this endpoint into a list of who has an account."""
    addr = shape.email(body.get('email'))
    fine, retry = limits.hit('recover:' + req.ip, 5, 3600)
    if not fine:
        return _slow(retry)
    limits.hit('recover:' + addr, 3, 3600)
    supa.recover(addr)
    return wire.ok({'sent': True, 'message': 'If that address has an account, a '
                                             'reset link is on its way.'})


def _resend(req, body):
    addr = shape.email(body.get('email'))
    fine, retry = limits.hit('resend:' + req.ip, 5, 3600)
    if not fine:
        return _slow(retry)
    supa.resend(addr)
    return wire.ok({'sent': True, 'message': 'If that address is waiting to be '
                                             'confirmed, a new link is on its way.'})


def _adopt(req, body):
    """The tokens GoTrue puts in the URL fragment after a confirmation, reset
    or invite link. The page hands them here and gets a cookie back, so a
    fragment full of tokens never has to become the app's way of staying in.

    The token is not trusted on sight — whoami() is GoTrue verifying its own
    signature, and a forged one dies there."""
    token = shape.s(body.get('access_token'), 4096, 'access_token', required=True)
    rt = shape.s(body.get('refresh_token'), 4096, 'refresh_token')
    fine, retry = limits.hit('adopt:' + req.ip, 20, 900)
    if not fine:
        return _slow(retry)
    rep = supa.whoami(token)
    if not rep.ok or not isinstance(rep.body, dict) or not rep.body.get('id'):
        return wire.fail(401, 'That link has expired. Ask for a new one.')
    user = rep.body
    if not rt:
        # A recovery link carries no refresh token; without one we cannot keep
        # the person signed in past the hour, and pretending otherwise ends in
        # a dead session later.
        return wire.ok({'user': None, 'verified': bool(user.get('email_confirmed_at')),
                        'message': 'That link is confirmed. Please sign in.'})
    return _grant_in(req, {'access_token': token, 'refresh_token': rt,
                           'expires_in': 3600, 'user': user})


def _reset(req, body):
    """A new password from a reset link. The recovery token is the proof, and
    it can do exactly this one thing before it expires."""
    token = shape.s(body.get('access_token') or body.get('token'), 4096,
                    'access_token', required=True)
    fine, retry = limits.hit('reset:' + req.ip, 10, 900)
    if not fine:
        return _slow(retry)
    seen = supa.whoami(token)
    if not seen.ok or not isinstance(seen.body, dict) or not seen.body.get('id'):
        return wire.fail(401, 'That reset link has expired. Ask for a new one.')
    addr = (seen.body.get('email') or '').lower()
    pw = shape.password(body.get('password'), addr)
    rep = supa.update_self(token, {'password': pw})
    if not rep.ok:
        return wire.upstream(rep, 'That password was refused.')
    return wire.Res(200, {'ok': True, 'email': addr,
                          'message': 'Password changed. Sign in with it.'},
                    sessions.clear(req.secure))


def _password(req, body):
    """Changing it from inside the app. The current one is checked by asking
    GoTrue to sign in with it, because GoTrue is the only thing that knows."""
    sess, _fresh, refused = need(req)
    if refused:
        return refused
    addr = sess.get('e') or ''
    cur = body.get('current') if isinstance(body.get('current'), str) else ''
    fine, retry = limits.hit('pw:' + (sess.get('u') or req.ip), 8, 900)
    if not fine:
        return _slow(retry, 'attempts')
    if not cur or not supa.login(addr, cur).ok:
        return wire.fail(401, 'That is not your current password.')
    pw = shape.password(body.get('next') or body.get('password'), addr)
    rep = supa.update_self(sess.get('at'), {'password': pw})
    if not rep.ok:
        return wire.upstream(rep, 'That password was refused.')
    # The change can revoke the refresh token we are holding, so take a new
    # grant rather than keep a cookie that will fail in an hour.
    again = supa.login(addr, pw)
    if again.ok:
        return _grant_in(req, again.body if isinstance(again.body, dict) else {})
    return wire.Res(200, {'ok': True, 'message': 'Password changed. Please sign '
                                                 'in again.'},
                    sessions.clear(req.secure))


# --------------------------------------------------------------- profile
def _profile(req, sess, prof):
    if len(req.parts) > 2:
        return wire.fail(404, 'No such endpoint.')
    if req.method == 'GET':
        return wire.ok({'user': me_json(sess, prof)})
    if req.method not in ('PUT', 'PATCH'):
        return wire.fail(405, 'That endpoint takes a GET or a PUT.')
    body = req.obj()
    if 'name' not in body and 'display_name' not in body:
        raise shape.Bad('There is nothing to change in that request.')
    name = shape.s(body.get('name', body.get('display_name')), 80, 'name')
    rep = supa.update('profiles', sess.get('at'), {'display_name': name},
                      id='eq.' + sess['u'])
    if not rep.ok:
        return wire.upstream(rep, 'That change was refused.')
    # GoTrue keeps its own copy in user_metadata; leaving it stale means a
    # fresh sign-in would show the old name for a moment.
    supa.update_self(sess.get('at'), {'data': {'display_name': name}})
    row = supa.one(rep) or {}
    limits.PROFILE.put(sess['u'], row)
    return wire.ok({'user': me_json(sess, row)})


# ------------------------------------------------------------------ data
CHAT_COLS = ('id,local_id,title,tab,model,version,message_count,'
             'created_at,updated_at')
MSG_COLS = 'seq,role,kind,text,meta,ts'
IMPORT_BATCH = 40


def _mine(sess):
    """The owner filter every query in this section carries.

    The policies would nearly do it on their own — chats_own is scoped to the
    owner and there is no administrator policy on messages or maps at all. But
    chats_read_admin deliberately grants an administrator a select across the
    whole chats table, because the console lists titles across accounts, and
    this API is not the console: without this filter an administrator's own
    sidebar would fill up with other people's chat titles."""
    return 'eq.' + (sess.get('u') or '')


def _data(req, tail, sess):
    top = tail[0] if tail else ''
    if top in ('', 'bootstrap'):
        if req.method != 'GET':
            return wire.fail(405, 'That endpoint takes a GET.')
        return _bootstrap(req, sess)
    if top == 'chats':
        return _chats(req, tail[1:], sess)
    if top == 'import':
        if req.method != 'POST':
            return wire.fail(405, 'That endpoint takes a POST.')
        return _import(req, sess)
    if top == 'export':
        if req.method != 'GET':
            return wire.fail(405, 'That endpoint takes a GET.')
        return _export(req, sess)
    return wire.fail(404, 'No such endpoint.')


def _public_settings():
    """The four fields of the settings row that anybody may see, named one by
    one rather than by handing over the row: everything else on it — the token
    ceiling, who changed it, the gateway columns — is nobody's business.

    The model list goes through config.public_models on the way out. A row
    written before the agents were named holds vendor ids, and this is the last
    place before a signed-out page where they can be turned back into the names
    the product uses — through the effective table, so an agent an administrator
    has repointed publicises its own id too."""
    st = settings_now()
    return {
        'signups': bool(config.SIGNUPS and shape.yes(st.get('signups_open'), True)),
        'maintenance': shape.yes(st.get('maintenance'), False),
        'announcement': shape.s(st.get('announcement') or '', 600),
        'models': config.public_models(st.get('allowed_models') or [],
                                       gateway.public(st)),
    }


def _bootstrap(req, sess):
    """Everything the app needs to draw itself, in one request: who you are,
    what you have, and how this instance is configured."""
    prof = profile_of(sess)
    rep = supa.select('chats', sess.get('at'), select=CHAT_COLS,
                      user_id=_mine(sess),
                      order='updated_at.desc', limit=config.MAX_CHATS)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read your chats.')
    _touch(sess)
    return wire.ok({'user': me_json(sess, prof),
                    'chats': rep.body if isinstance(rep.body, list) else [],
                    'settings': _public_settings(),
                    'limits': {'chats': config.MAX_CHATS, 'messages': config.MAX_MSGS}})


def _chats(req, tail, sess):
    token = sess.get('at')
    if not tail:
        if req.method == 'GET':
            rep = supa.select('chats', token, select=CHAT_COLS,
                              user_id=_mine(sess),
                              order='updated_at.desc', limit=config.MAX_CHATS)
            if not rep.ok:
                return wire.upstream(rep, 'Could not read your chats.')
            return wire.ok({'chats': rep.body if isinstance(rep.body, list) else []})
        if req.method == 'POST':
            return _create_chat(req, sess)
        return wire.fail(405, 'That endpoint takes a GET or a POST.')
    cid = shape.uuid(tail[0], 'chat')
    if req.method == 'GET':
        return _read_chat(req, sess, cid)
    if req.method in ('PUT', 'PATCH'):
        return _save_chat(req, sess, cid)
    if req.method == 'DELETE':
        return _drop_chat(req, sess, cid)
    return wire.fail(405, 'That endpoint takes a GET, a PUT or a DELETE.')


def _chat_count(sess):
    rep = supa.select('chats', sess.get('at'), select='id', count=True,
                      user_id=_mine(sess), limit=1)
    return supa.total(rep) if rep.ok else 0


def _put_messages(sess, cid, msgs):
    """One transaction through an RPC, not delete-then-insert over two HTTP
    calls: if the second of those failed, a conversation would be gone."""
    rows = []
    for i, m in enumerate(msgs):
        row = dict(m)
        row['seq'] = i
        rows.append(row)
    return supa.rpc('replace_messages', sess.get('at'),
                    {'p_chat': cid, 'p_rows': rows})


def _put_map(sess, cid, snap, version):
    fields = {'chat_id': cid, 'user_id': sess['u'], 'version': version,
              'updated_at': _now_iso()}
    if snap.get('map') is not None:
        fields['data'] = snap['map']
    if snap.get('style') is not None:
        fields['style'] = snap['style']
    return supa.upsert('maps', sess.get('at'), [fields], 'chat_id')


def _create_chat(req, sess):
    snap = shape.snapshot(req.obj())
    n = _chat_count(sess)
    if n >= config.MAX_CHATS:
        return wire.fail(409, 'You have %d chats, which is as many as one account '
                              'holds. Delete one to start another.' % n)
    row = {'user_id': sess['u'], 'title': snap['title'], 'tab': snap['tab'],
           'model': snap['model'], 'version': 1,
           'message_count': len(snap['messages'])}
    if snap.get('local_id'):
        row['local_id'] = snap['local_id']
    rep = supa.insert('chats', sess.get('at'), [row])
    if not rep.ok:
        return wire.upstream(rep, 'Could not start that chat.')
    chat = supa.one(rep) or {}
    cid = chat.get('id')
    if cid and snap['messages']:
        _put_messages(sess, cid, snap['messages'])
    if cid and (snap.get('map') is not None or snap.get('style') is not None):
        _put_map(sess, cid, snap, 1)
    return wire.ok({'chat': chat})


def _read_chat(req, sess, cid):
    token = sess.get('at')
    rep = supa.select('chats', token, select=CHAT_COLS, id='eq.' + cid,
                      user_id=_mine(sess), limit=1)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read that chat.')
    chat = supa.one(rep)
    if not chat:
        # RLS makes somebody else's chat indistinguishable from a deleted one,
        # which is the answer we want to give in both cases anyway.
        return wire.fail(404, 'That chat is not there.')
    msgs = supa.select('messages', token, select=MSG_COLS, chat_id='eq.' + cid,
                       order='seq.asc', limit=config.MAX_MSGS)
    sheet = supa.one(supa.select('maps', token, select='data,style,version',
                                 chat_id='eq.' + cid, limit=1)) or {}
    return wire.ok({
        'chat': chat,
        'messages': msgs.body if msgs.ok and isinstance(msgs.body, list) else [],
        'map': sheet.get('data'),
        'style': sheet.get('style'),
    })


def _stale(have):
    return wire.Res(409, {'error': 'This chat was changed somewhere else — '
                                   'another tab, or another device. Reload to '
                                   'pick up the newer copy.',
                          'version': have})


def _save_chat(req, sess, cid):
    """The whole chat, written as one snapshot.

    `version` is compared in the WHERE clause, not just read first: two tabs
    saving at the same moment both pass the read, and only the one that
    matches the version it saw gets its update applied. The other is told."""
    token = sess.get('at')
    snap = shape.snapshot(req.obj())
    rep = supa.select('chats', token, select='id,version', id='eq.' + cid,
                      user_id=_mine(sess), limit=1)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read that chat.')
    row = supa.one(rep)
    if not row:
        return wire.fail(404, 'That chat is not there.')
    have = int(row.get('version') or 0)
    if snap['version'] and snap['version'] < have:
        return _stale(have)
    nxt = have + 1
    up = supa.update('chats', token, {
        'title': snap['title'], 'tab': snap['tab'], 'model': snap['model'],
        'version': nxt, 'message_count': len(snap['messages']),
        'updated_at': _now_iso(),
    }, id='eq.' + cid, user_id=_mine(sess), version='eq.%d' % have)
    if not up.ok:
        return wire.upstream(up, 'Could not save that chat.')
    chat = supa.one(up)
    if not chat:
        return _stale(have)
    if snap['has_messages']:
        done = _put_messages(sess, cid, snap['messages'])
        if not done.ok:
            return wire.upstream(done, 'Could not save the messages in that chat.')
    if snap.get('map') is not None or snap.get('style') is not None:
        done = _put_map(sess, cid, snap, nxt)
        if not done.ok:
            return wire.upstream(done, 'Could not save the map in that chat.')
    return wire.ok({'chat': chat, 'version': nxt})


def _drop_chat(req, sess, cid):
    """Messages and the map go with it, by foreign key, in the database."""
    rep = supa.remove('chats', sess.get('at'), id='eq.' + cid,
                      user_id=_mine(sess))
    if not rep.ok:
        return wire.upstream(rep, 'Could not delete that chat.')
    return wire.ok({'deleted': cid})


def _import(req, sess):
    """The localStorage backup, uploaded once.

    Matched on local_id, so pressing the button twice does not double
    everything, and capped per request so a hundred chats do not become one
    request that outlives its own timeout — the client calls again while
    `remaining` is not zero."""
    chats = shape.bundle(req.obj())
    if not chats:
        return wire.ok({'added': 0, 'skipped': 0, 'remaining': 0})
    seen = set()
    rep = supa.select('chats', sess.get('at'), select='local_id',
                      user_id=_mine(sess), limit=config.MAX_CHATS)
    if rep.ok and isinstance(rep.body, list):
        seen = set(r.get('local_id') for r in rep.body if r.get('local_id'))
    room = max(0, config.MAX_CHATS - _chat_count(sess))
    added = skipped = no_room = 0
    left = list(chats)
    for snap in chats[:IMPORT_BATCH]:
        left.pop(0)
        lid = snap.get('local_id') or ''
        if lid and lid in seen:
            skipped += 1
            continue
        if added >= room:
            # Counted apart from the ones that were already here, because this
            # is the case the browser must not read as 'all of it arrived' —
            # persist.js deletes the local copy when it is told that.
            no_room += 1
            skipped += 1
            continue
        row = {'user_id': sess['u'], 'title': snap['title'], 'tab': snap['tab'],
               'model': snap['model'], 'version': 1,
               'message_count': len(snap['messages'])}
        if lid:
            row['local_id'] = lid
        made = supa.insert('chats', sess.get('at'), [row])
        chat = supa.one(made) if made.ok else None
        if not chat or not chat.get('id'):
            skipped += 1
            continue
        if lid:
            seen.add(lid)
        if snap['messages']:
            _put_messages(sess, chat['id'], snap['messages'])
        if snap.get('map') is not None or snap.get('style') is not None:
            _put_map(sess, chat['id'], snap, 1)
        added += 1
    return wire.ok({'added': added, 'skipped': skipped, 'remaining': len(left),
                    'full': bool(no_room) or (room <= added and len(left) > 0)})


def _export(req, sess):
    """Everything this person has, in one file. Two queries rather than two
    per chat: PostgREST takes an in.() list and Python does the grouping."""
    token = sess.get('at')
    rep = supa.select('chats', token, select=CHAT_COLS, order='updated_at.desc',
                      user_id=_mine(sess), limit=config.MAX_CHATS)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read your chats.')
    chats = [c for c in (rep.body if isinstance(rep.body, list) else []) if c.get('id')]
    msgs, sheets = {}, {}
    if chats:
        ids = 'in.(%s)' % ','.join(c['id'] for c in chats)
        mr = supa.select('messages', token, select='chat_id,' + MSG_COLS,
                         chat_id=ids, order='chat_id.asc,seq.asc', limit=40000)
        for m in (mr.body if mr.ok and isinstance(mr.body, list) else []):
            msgs.setdefault(m.pop('chat_id', ''), []).append(m)
        pr = supa.select('maps', token, select='chat_id,data,style,version',
                         chat_id=ids, limit=config.MAX_CHATS)
        for p in (pr.body if pr.ok and isinstance(pr.body, list) else []):
            sheets[p.get('chat_id')] = p
    for c in chats:
        c['messages'] = msgs.get(c['id'], [])
        c['map'] = (sheets.get(c['id']) or {}).get('data')
        c['style'] = (sheets.get(c['id']) or {}).get('style')
    return wire.ok({'app': 'cognix', 'exported_at': _now_iso(),
                    'email': sess.get('e') or '', 'chats': chats})


# ------------------------------------------------------- the model gateway
def _guest_gate(req):
    """The free trial. Reached only when there is no session cookie at all, so
    there is nobody to suspend, no monthly ceiling to read and no usage row to
    write — the one question is whether this visitor has calls left.

    Three bounds, each worth exactly what the place it is kept is worth. The
    per-visitor count is in a cookie this server signed: it survives a restart,
    it is the same on every instance and it cannot be edited, but clearing
    cookies starts it over. The per-address count is the answer to that, and it
    lives in this process, so it is per instance and forgets after an hour. The
    per-minute one is the same limiter an account gets. Together they bound what
    a stranger can spend on casual use; none of them is a security boundary, and
    the note in config.py says so at more length."""
    if not config.GUEST:
        return None, wire.fail(401, 'Please sign in.'), []
    got = sessions.guest_read(req.jar) or sessions.guest_new()
    if got['n'] >= config.GUEST_CALLS:
        return None, wire.Res(402, {
            'error': 'That is the whole free trial. Make an account to keep '
                     'going — it is free, and it saves your chats.',
            'guest': guest_json(got), 'signin': '/app/auth/'}), []
    # Checked after the ceiling on purpose: a visitor who is already out of
    # calls should not spend a shared address's budget by retrying.
    fine, retry = limits.hit('guest-ip:' + (req.ip or '?'),
                             config.GUEST_PER_IP, 3600)
    if not fine:
        return None, _slow(retry, 'model calls from this address'), []
    fine, retry = limits.hit('gw:guest:' + got['g'], config.GW_PER_MIN, 60)
    if not fine:
        return None, _slow(retry, 'model calls'), []
    # Counted before the call, not after it. The number exists to bound what
    # somebody with no account can spend, and a refund path is a way to spend
    # for nothing.
    got['n'] = int(got['n']) + 1
    return None, None, sessions.guest_seal(got, req.secure)


def gate(req):
    """Called by serve.py before it proxies /gw/*. Returns (session, refusal,
    cookies) and refuses on any of four grounds.

    A model call is the one thing in this app that spends money, so in cloud
    mode it needs a person, an account that is not suspended, a hand that is
    not hammering the button, and room under the monthly ceiling. In local
    mode there is nobody to check, and the proxy behaves as it always has.

    The exception is a visitor with no session at all, who gets a small free
    trial instead of a refusal — see `_guest_gate`. A session that reads but
    has expired is not that visitor: it still earns the honest 401, because the
    app has work on screen that belongs to an account and needs to say so."""
    if not supa.ready():
        return None, None, []
    if not sessions.read(req.jar):
        return _guest_gate(req)
    sess, fresh, refused = need(req)
    if refused:
        return None, refused, []
    cookies = sessions.seal(sess, req.secure) if fresh else []
    prof = profile_of(sess)
    if (prof or {}).get('status') == 'suspended':
        return None, wire.fail(403, 'This account is suspended. An administrator '
                                    'can lift that.'), cookies
    fine, retry = limits.hit('gw:' + (sess.get('u') or req.ip),
                             config.GW_PER_MIN, 60)
    if not fine:
        return None, _slow(retry, 'model calls'), cookies
    seen = usage_of(sess, prof)
    if not seen['unlimited'] and seen['used'] >= seen['cap']:
        return None, wire.Res(402, {
            'error': 'This account has used its %s tokens for %s. An '
                     'administrator can raise the ceiling.'
                     % ('{:,}'.format(seen['cap']), seen['month']),
            'usage': seen}), cookies
    return sess, None, cookies


def record(sess, kind, model, usage, ms=0, ok=True, note=''):
    """One row per model call: what the cap counts and what the admin panel
    charts. Written as the caller, so a person can read their own usage and
    nobody else's."""
    if not sess or not supa.ready():
        return
    u = usage if isinstance(usage, dict) else {}
    pt = int(u.get('prompt_tokens') or u.get('input_tokens') or 0)
    ct = int(u.get('completion_tokens') or u.get('output_tokens') or 0)
    supa.insert('usage_events', sess.get('at'), [{
        'user_id': sess.get('u'), 'kind': shape.s(kind, 24),
        'model': shape.s(model, 80), 'prompt_tokens': pt,
        'completion_tokens': ct,
        'total_tokens': int(u.get('total_tokens') or (pt + ct)),
        'ms': int(ms or 0), 'ok': bool(ok), 'note': shape.s(note, 200),
    }])
    limits.USAGE.drop(sess.get('u') or '')
