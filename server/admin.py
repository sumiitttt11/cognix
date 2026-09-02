"""/api/admin/* — the panel that runs the place.

The role is checked twice, in two systems. This module refuses anybody whose
*database* row does not say admin; the cookie's copy of the role is a hint for
the interface and is not trusted here. And the tables refuse them as well —
the policies in supabase/policies.sql grant the wide reads and writes to
is_admin(), and every call below carries the administrator's own token. A
mistake in the check in this file therefore cannot hand over a table; it would
have to be a mistake in both places at once.

The service key appears here only where there is no policy to lean on: GoTrue's
own admin endpoints, and the one-time promotion of the first administrator.
"""
from . import config, gateway, limits, shape, supa, wire

USER_COLS = ('id,email,display_name,role,status,token_cap,created_at,'
             'updated_at,last_seen_at,notes')
AUDIT_COLS = 'id,actor,actor_email,action,target,target_email,detail,created_at'
CHAT_COLS = 'id,user_id,title,tab,model,message_count,created_at,updated_at'


def handle(req, sess, prof, tail):
    if not _is_admin(sess, prof):
        return _refused(sess)
    top = tail[0] if tail else ''
    rest = tail[1:]
    if top in ('', 'overview'):
        return _overview(req, sess)
    if top == 'users':
        return _users(req, sess, rest)
    if top == 'usage':
        return _usage(req, sess)
    if top == 'audit':
        return _audit_list(req, sess)
    if top == 'settings':
        return _settings(req, sess)
    if top == 'gateway':
        return _gateway(req, sess, rest)
    if top == 'chats':
        return _chats(req, sess)
    if top == 'invite':
        return _invite(req, sess)
    return wire.fail(404, 'No such endpoint.')


def _refused(sess):
    """A stranger gets one sentence; the person named in ADMIN_EMAILS whose
    promotion could not run gets the real reason.

    Telling the deployer 'that is an administrator page' when what actually
    happened is that SUPABASE_SERVICE_KEY was never set is how an afternoon goes
    missing. The reason is only spelled out to an address that is already in
    ADMIN_EMAILS, so it says nothing to anybody else."""
    addr = (sess.get('e') or '').lower()
    if addr and addr in config.ADMIN_EMAILS and not config.SUPABASE_SERVICE_KEY:
        return wire.fail(503, 'This console cannot open yet: granting the first '
                              'administrator needs COGNIX_SUPABASE_SERVICE_KEY, '
                              'and it is not set on this server.')
    return wire.fail(403, 'That is an administrator page.')


def _is_admin(sess, prof):
    if (prof or {}).get('role') == 'admin':
        return True
    addr = (sess.get('e') or '').lower()
    if not addr or addr not in config.ADMIN_EMAILS:
        return False
    return _first_admin(sess, addr)


def _first_admin(sess, addr):
    """How the first administrator comes to exist at all.

    Whoever deploys this names their own address in ADMIN_EMAILS, signs up
    like anybody else, and this promotes the row the signup trigger made. It
    needs the service key exactly once, because the point of the policies is
    that nobody can grant themselves a role."""
    uid = sess.get('u') or ''
    if not uid or not config.SUPABASE_SERVICE_KEY:
        return False
    rep = supa.update('profiles', None, {'role': 'admin'}, admin=True, id='eq.' + uid)
    # The row has to come back saying admin. 'The write was accepted' is a
    # weaker claim than it looks: a PATCH that matched nothing is also a 200,
    # and reading that as a promotion would open this console over a row that
    # still says user — every query behind it would then be refused by the
    # policies, one confusing empty panel at a time.
    if (supa.one(rep) or {}).get('role') != 'admin':
        return False
    limits.PROFILE.drop(uid)
    if not limits.TOUCHED.get('booted:' + uid):
        limits.TOUCHED.put('booted:' + uid, 1)
        _write(sess, 'admin.bootstrap', uid, addr, {'from': 'ADMIN_EMAILS'})
    return True


def _write(sess, action, target='', target_email='', detail=None):
    """One row per thing an administrator did. Written as the administrator,
    which is what makes the actor columns trustworthy: the policy allows an
    insert only when actor = auth.uid()."""
    supa.insert('audit_log', sess.get('at'), [{
        'actor': sess.get('u'), 'actor_email': (sess.get('e') or '').lower(),
        'action': shape.s(action, 48), 'target': target or None,
        'target_email': (target_email or '').lower()[:254],
        'detail': detail or {},
    }])


def _one(rep):
    return supa.one(rep) or {}


def _rows(rep):
    return rep.body if rep.ok and isinstance(rep.body, list) else []


def _first(rep):
    """An RPC that returns a single json row, whichever shape it arrives in."""
    b = rep.body if rep.ok else {}
    if isinstance(b, list):
        b = b[0] if b else {}
    return b if isinstance(b, dict) else {}


# -------------------------------------------------------------- overview
def _overview(req, sess):
    if req.method != 'GET':
        return wire.fail(405, 'That endpoint takes a GET.')
    rep = supa.rpc('admin_overview', sess.get('at'))
    if not rep.ok:
        return wire.upstream(rep, 'Could not read the overview.')
    out = dict(_first(rep))
    out['recent'] = _rows(supa.select('audit_log', sess.get('at'),
                                      select=AUDIT_COLS,
                                      order='created_at.desc', limit=10))
    out['newest'] = _rows(supa.select('profiles', sess.get('at'),
                                      select='id,email,display_name,role,status,created_at',
                                      order='created_at.desc', limit=8))
    out['mode'] = config.mode()
    out['month'] = shape.s(out.get('month') or '', 16)
    return wire.ok(out)


# ----------------------------------------------------------------- users
def _users(req, sess, rest):
    if not rest:
        if req.method != 'GET':
            return wire.fail(405, 'That endpoint takes a GET.')
        return _user_list(req, sess)
    uid = shape.uuid(rest[0], 'user')
    action = rest[1] if len(rest) > 1 else ''
    if action:
        if req.method != 'POST':
            return wire.fail(405, 'That endpoint takes a POST.')
        return _user_action(req, sess, uid, action)
    if req.method == 'GET':
        return _user_one(req, sess, uid)
    if req.method in ('PATCH', 'PUT'):
        return _user_change(req, sess, uid)
    if req.method == 'DELETE':
        return _user_delete(req, sess, uid)
    return wire.fail(405, 'That endpoint takes a GET, a PATCH or a DELETE.')


def _search(q):
    """A PostgREST `or` group built from a search box.

    The parts that could add a term of their own — commas, brackets, the
    wildcard — are taken out rather than escaped, because there is no quoting
    in this syntax to escape them into."""
    safe = ''.join(c for c in q if c not in ',()*:"\'')
    if not safe:
        return None
    return '(email.ilike.*%s*,display_name.ilike.*%s*)' % (safe, safe)


def _user_list(req, sess):
    page = shape.num(req.q('page', '1'), 1, 10000, default=1)
    per = shape.num(req.q('per', '25'), 1, 100, default=25)
    params = {'order': 'created_at.desc', 'limit': per, 'offset': (page - 1) * per}
    q = _search(shape.s(req.q('q', ''), 80))
    if q:
        params['or'] = q
    role = req.q('role', '')
    if role in shape.ROLES:
        params['role'] = 'eq.' + role
    status = req.q('status', '')
    if status in shape.STATUS:
        params['status'] = 'eq.' + status
    # The embedded count is one query instead of one per user. It needs the
    # foreign key to be there, so fall back if PostgREST cannot see it.
    rep = supa.select('profiles', sess.get('at'), count=True,
                      select=USER_COLS + ',chats(count)', **params)
    if not rep.ok:
        rep = supa.select('profiles', sess.get('at'), count=True,
                          select=USER_COLS, **params)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read the user list.')
    rows = rep.body if isinstance(rep.body, list) else []
    for r in rows:
        kids = r.pop('chats', None)
        if isinstance(kids, list):
            kids = kids[0] if kids else {}
        r['chats'] = int((kids or {}).get('count') or 0) if isinstance(kids, dict) else 0
    return wire.ok({'users': rows, 'total': supa.total(rep),
                    'page': page, 'per': per})


def _user_one(req, sess, uid):
    token = sess.get('at')
    row = _one(supa.select('profiles', token, select=USER_COLS, id='eq.' + uid, limit=1))
    if not row:
        return wire.fail(404, 'There is no such user.')
    out = {'user': row}
    out['usage'] = _first(supa.rpc('admin_user_usage', token, {'p_user': uid}))
    out['chats'] = _rows(supa.select('chats', token, select=CHAT_COLS,
                                     user_id='eq.' + uid,
                                     order='updated_at.desc', limit=25))
    seen = supa.admin_user(uid)
    if seen.ok and isinstance(seen.body, dict):
        a = seen.body
        out['login'] = {
            'created_at': a.get('created_at') or '',
            'last_sign_in_at': a.get('last_sign_in_at') or '',
            'confirmed': bool(a.get('email_confirmed_at') or a.get('confirmed_at')),
            'providers': (a.get('app_metadata') or {}).get('providers') or [],
        }
    return wire.ok(out)


def _other_admins(sess, uid):
    """Is there an administrator other than this one? The panel refuses the
    two changes that would otherwise leave nobody able to open it."""
    rep = supa.select('profiles', sess.get('at'), select='id', count=True,
                      role='eq.admin', id='neq.' + uid, limit=1)
    return supa.total(rep) > 0 if rep.ok else False


def _user_change(req, sess, uid):
    body = req.obj()
    me = sess.get('u') or ''
    fields = {}
    if 'role' in body:
        fields['role'] = shape.one_of(body['role'], shape.ROLES, field='role')
    if 'status' in body:
        fields['status'] = shape.one_of(body['status'], shape.STATUS, field='status')
    if 'token_cap' in body:
        raw = body['token_cap']
        # An empty box means "no ceiling of its own", which is a real setting
        # and not a missing one: the account goes back to following the
        # instance default. Without this branch a personal cap could be put on
        # from the panel and never taken off again.
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            fields['token_cap'] = None
        else:
            fields['token_cap'] = shape.num(raw, 0, 10 ** 9, field='token_cap')
    if 'name' in body or 'display_name' in body:
        fields['display_name'] = shape.s(body.get('name', body.get('display_name')),
                                        80, 'name')
    if 'notes' in body:
        fields['notes'] = shape.para(body['notes'], 2000, 'notes')
    if not fields:
        raise shape.Bad('There is nothing to change in that request.')
    if uid == me and (fields.get('role') == 'user'
                      or fields.get('status') == 'suspended'):
        return wire.fail(409, 'That would lock you out of this panel. Another '
                              'administrator can do it for you.')
    if (fields.get('role') == 'user' or fields.get('status') == 'suspended') \
            and not _other_admins(sess, uid):
        return wire.fail(409, 'That is the only administrator left. Promote '
                              'somebody else first.')
    rep = supa.update('profiles', sess.get('at'), fields, id='eq.' + uid)
    if not rep.ok:
        return wire.upstream(rep, 'That change was refused.')
    row = _one(rep)
    if not row:
        return wire.fail(404, 'There is no such user.')
    # A suspension has to bite while they are still holding a valid cookie, and
    # it does: every signed-in request re-reads this row, and this drops the
    # copy that would otherwise be up to twenty seconds stale.
    limits.PROFILE.drop(uid)
    limits.USAGE.drop(uid)
    _write(sess, 'user.change', uid, row.get('email') or '', fields)
    return wire.ok({'user': row})


def _user_delete(req, sess, uid):
    if uid == (sess.get('u') or ''):
        return wire.fail(409, 'You cannot delete your own account from here.')
    row = _one(supa.select('profiles', sess.get('at'), select='email,role',
                           id='eq.' + uid, limit=1))
    if row.get('role') == 'admin' and not _other_admins(sess, uid):
        return wire.fail(409, 'That is the only other administrator. Promote '
                              'somebody else first.')
    # GoTrue owns the login; deleting it takes the profile row with it through
    # the foreign key. The explicit delete after is for the case where the
    # login was already gone and only the row was left.
    rep = supa.admin_delete_user(uid)
    if not rep.ok and rep.status not in (404, 0):
        return wire.upstream(rep, 'Could not delete that account.')
    supa.remove('profiles', sess.get('at'), id='eq.' + uid)
    limits.PROFILE.drop(uid)
    _write(sess, 'user.delete', uid, row.get('email') or '',
           {'upstream': rep.status})
    return wire.ok({'deleted': uid})


def _user_action(req, sess, uid, action):
    row = _one(supa.select('profiles', sess.get('at'), select='email',
                           id='eq.' + uid, limit=1))
    addr = (row.get('email') or '').lower()
    if not addr:
        return wire.fail(404, 'There is no such user.')
    if action == 'reset':
        rep = supa.recover(addr)
        said = 'A reset link is on its way to %s.' % addr
    elif action == 'resend':
        rep = supa.resend(addr)
        said = 'A new confirmation link is on its way to %s.' % addr
    elif action == 'confirm':
        # For the first day of a deployment, before SMTP is set up: mark the
        # address confirmed by hand rather than leave somebody locked out.
        rep = supa.admin_update_user(uid, {'email_confirm': True})
        said = '%s is confirmed.' % addr
    else:
        return wire.fail(404, 'No such action.')
    if not rep.ok:
        return wire.upstream(rep, 'That action was refused.')
    _write(sess, 'user.' + action, uid, addr, {})
    return wire.ok({'ok': True, 'message': said})


def _invite(req, sess):
    if req.method != 'POST':
        return wire.fail(405, 'That endpoint takes a POST.')
    addr = shape.email(req.obj().get('email'))
    rep = supa.admin_invite(addr)
    if not rep.ok:
        low = (rep.msg('') or '').lower()
        if 'already' in low or rep.status == 422:
            return wire.fail(409, 'There is already an account on that address.')
        return wire.upstream(rep, 'That invitation was refused.')
    _write(sess, 'user.invite', '', addr, {})
    return wire.ok({'invited': addr,
                    'message': 'An invitation is on its way to %s.' % addr})


# ----------------------------------------------------------------- usage
def _usage(req, sess):
    """Tokens by day and by person, for as far back as asked. Both come from
    RPCs: adding up a month of rows in this process would mean fetching a
    month of rows into this process."""
    if req.method != 'GET':
        return wire.fail(405, 'That endpoint takes a GET.')
    days = shape.num(req.q('days', '30'), 1, 365, default=30)
    token = sess.get('at')
    daily = supa.rpc('admin_usage_daily', token, {'p_days': days})
    if not daily.ok:
        return wire.upstream(daily, 'Could not read usage.')
    top = supa.rpc('admin_usage_by_user', token, {'p_days': days})
    return wire.ok({
        'days': days,
        'daily': daily.body if isinstance(daily.body, list) else [],
        'users': top.body if top.ok and isinstance(top.body, list) else [],
    })


# ----------------------------------------------------------------- audit
def _audit_list(req, sess):
    if req.method != 'GET':
        return wire.fail(405, 'That endpoint takes a GET.')
    page = shape.num(req.q('page', '1'), 1, 10000, default=1)
    per = shape.num(req.q('per', '50'), 1, 200, default=50)
    params = {'select': AUDIT_COLS, 'order': 'created_at.desc',
              'limit': per, 'offset': (page - 1) * per}
    act = shape.s(req.q('action', ''), 48)
    if act:
        params['action'] = 'eq.' + act.replace(',', '')
    rep = supa.select('audit_log', sess.get('at'), count=True, **params)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read the audit log.')
    return wire.ok({'rows': rep.body if isinstance(rep.body, list) else [],
                    'total': supa.total(rep), 'page': page, 'per': per})


# -------------------------------------------------------------- settings
def _screen(row):
    """The settings row as this screen is allowed to see it.

    The gateway lives on the same row and has its own screen. Popping the
    columns here rather than listing the others in the select is on purpose: a
    project whose migration has not been run has no such columns, and naming one
    that does not exist is a 400 from PostgREST for the whole page.

    The model list is turned back into the agents' names for the same reason
    server/api.py does it — a row written before they were named holds vendor
    ids, and the console is where an administrator would otherwise read the one
    mapping nothing in this product tells anybody. The gateway screen is where
    that mapping is set and is allowed to say it; this screen is not, so the
    table it translates through is the effective one rather than the built-in."""
    for col in gateway.COLS:
        row.pop(col, None)
    if isinstance(row.get('allowed_models'), list):
        row['allowed_models'] = config.public_models(row['allowed_models'],
                                                     gateway.public())
    return row


def _settings(req, sess):
    token = sess.get('at')
    if req.method == 'GET':
        row = _one(supa.select('app_settings', token, select='*', id='eq.1', limit=1))
        return wire.ok({'settings': _screen(row), 'env': {
            'signups_env': config.SIGNUPS,
            'admin_emails': len(config.ADMIN_EMAILS),
            'token_cap_default': config.TOKEN_CAP,
            'mode': config.mode(),
        }})
    if req.method not in ('PUT', 'PATCH'):
        return wire.fail(405, 'That endpoint takes a GET or a PUT.')
    fields = shape.settings(req.obj())
    fields['updated_by'] = sess.get('u')
    rep = supa.upsert('app_settings', token, [dict(fields, id=1)], 'id')
    if not rep.ok:
        return wire.upstream(rep, 'That change was refused.')
    gateway.forget()
    _write(sess, 'settings.change', '', '', fields)
    return wire.ok({'settings': _screen(_one(rep))})


# --------------------------------------------------------------- gateway
def _gateway(req, sess, rest):
    """Where the model calls go, with which key, and asking for which model —
    the whole point of which is that changing any of it is not a deploy.

    Three shapes, and the third is why this is not just part of _settings:

      GET             what is stored, with the key as a masked hint. The key
                      itself never comes back out of here, so there is no
                      screen, log line or audit row that can leak it.
      PUT             store a URL, a key, a model id per agent, or any of them.
                      '' for any one means 'forget the stored one and go back to
                      the environment — or, for a model, to the built-in id'.
      POST /check     call the gateway with what is stored, or with what is in
                      the body before it is saved. GET /v1/models costs
                      nothing, which is what makes 'test before you save' free,
                      and its answer is the list of ids to choose from.
    """
    if rest and rest[0] == 'check':
        return _gateway_check(req, sess)
    if rest:
        return wire.fail(404, 'No such endpoint.')
    if req.method == 'GET':
        return wire.ok({'gateway': gateway.status(),
                        'env': {'mode': config.mode(),
                                'secret_given': config.SESSION_SECRET_GIVEN}})
    if req.method not in ('PUT', 'PATCH'):
        return wire.fail(405, 'That endpoint takes a GET or a PUT.')
    fields = gateway.fields(req.obj())
    fields['updated_by'] = sess.get('u')
    rep = supa.upsert('app_settings', sess.get('at'), [dict(fields, id=1)], 'id')
    if not rep.ok:
        if gateway.missing_column(rep):
            return wire.fail(503, 'This database has not been migrated for the '
                                  'gateway settings yet. Run supabase/schema.sql '
                                  'again — it adds five columns to app_settings '
                                  'and is safe to run twice.')
        return wire.upstream(rep, 'That change was refused.')
    gateway.forget()
    # The audit row records that it changed and to what, in the masked form.
    # The sealed value is not in it, because an audit log is read by people.
    _write(sess, 'gateway.change', '', '', {
        'base': fields.get('gateway_base'),
        'key': fields.get('gateway_hint') if 'gateway_hint' in fields else None,
        'cleared': 'gateway_sealed' in fields and not fields['gateway_sealed'],
        'models': fields.get('gateway_models'),
        'updated_by': sess.get('u')})
    return wire.ok({'gateway': gateway.status()})


def _gateway_check(req, sess):
    """A dry run against whatever the administrator is about to save, or against
    what is already stored when the body is empty.

    The reply carries the gateway's own model list. That is what turns 'this
    agent is not in its list' from a dead end into a choice: the console prints
    the ids it got back and one click points an agent at one of them."""
    if req.method != 'POST':
        return wire.fail(405, 'That endpoint takes a POST.')
    body = req.obj() or {}
    now = gateway.row()
    base = shape.origin(body.get('base'), 'base') if body.get('base') else ''
    key = shape.gateway_key(body.get('key'), 'key') if body.get('key') else ''
    want = gateway.models(now)
    if 'models' in body:
        for name, mid in shape.gateway_models(body.get('models')).items():
            # '' means the same thing here as it does on a save: the id this
            # build ships with. Judging against the override still stored would
            # be judging a mapping the administrator is halfway through removing.
            want[name] = mid or config.ALIAS[name]
    use_base, use_key, _src = gateway.effective(now)
    ok, said, got = gateway.check(base or use_base, key or use_key, want=want)
    _write(sess, 'gateway.check', '', '', {'base': base or use_base, 'ok': ok})
    return wire.ok({'ok': ok, 'message': said, 'base': base or use_base,
                    'models': got})


# ------------------------------------------------------------------ chats
def _chats(req, sess):
    """Chat metadata across every account — titles, sizes and dates, not the
    contents. An administrator can see that somebody has 40 maps and when they
    last touched one, which is what support needs; reading the maps themselves
    is not part of the job, and the policies do not grant it."""
    if req.method != 'GET':
        return wire.fail(405, 'That endpoint takes a GET.')
    page = shape.num(req.q('page', '1'), 1, 10000, default=1)
    per = shape.num(req.q('per', '50'), 1, 200, default=50)
    params = {'select': CHAT_COLS, 'order': 'updated_at.desc',
              'limit': per, 'offset': (page - 1) * per}
    who = req.q('user', '')
    if who:
        params['user_id'] = 'eq.' + shape.uuid(who, 'user')
    rep = supa.select('chats', sess.get('at'), count=True, **params)
    if not rep.ok:
        return wire.upstream(rep, 'Could not read the chat list.')
    return wire.ok({'chats': rep.body if isinstance(rep.body, list) else [],
                    'total': supa.total(rep), 'page': page, 'per': per})
