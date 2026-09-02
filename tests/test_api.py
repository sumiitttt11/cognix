#!/usr/bin/env python3
"""Cloud mode, end to end: two servers on ephemeral ports in this process.

serve.py answers on one, tools/fake_supabase.py on the other, and the tests
drive the first one over HTTP with a cookie jar — the same way a browser does.
Nothing is stubbed inside the app: sign-up really goes through GoTrue's shape,
the cookie really is signed, and every row really is read back through the
stub's copy of the policies in supabase/policies.sql.

What this file is for, in one sentence: proving that a request can only reach
the rows it owns. The stub filters every query by the id inside the token it
was given (`scope()` there is `policies.sql` here), so a route that forgot to
say whose rows it meant fails here rather than in production.

    python -m unittest tests.test_api -v
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import serve                                                     # noqa: E402
from server import config, crypto, limits, sessions               # noqa: E402
import fake_supabase as stub                                      # noqa: E402

SECRET = 'test-secret-' + 'c' * 40
ADMIN = 'boss@cognix.test'
PW = 'orbital-kettle-77'
NET = {}
# every PostgREST and GoTrue call the app made, as the stub saw it:
# (method, path, role) where role is what the token proved. 'service' in this
# list on a /api/data path would mean the app had bypassed the policies.
SEEN = []


def _spy(name):
    """Wrap one of the stub's two routers so the tests can see the role every
    call arrived with, without reaching inside the app to find out."""
    real = getattr(stub, name)

    def wrapped(method, path, query, body, *a, **kw):
        role = a[1] if name == 'gotrue' else a[0]
        SEEN.append((method, path, role))
        return real(method, path, query, body, *a, **kw)
    return real, wrapped


def _dead_port():
    """A port that was free a moment ago and has nothing on it now."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def setUpModule():
    """Both servers, once. Config is pointed at the stub here and put back in
    tearDownModule, so the rest of the suite is unaffected."""
    stub.QUIET = True
    stub.CONFIRM = False
    NET['stub'] = ThreadingHTTPServer(('127.0.0.1', 0), stub.Stub)
    NET['stub_at'] = 'http://127.0.0.1:%d' % NET['stub'].server_address[1]
    NET['stub_thread'] = threading.Thread(target=NET['stub'].serve_forever,
                                          daemon=True)
    NET['stub_thread'].start()
    NET['was'] = dict((k, getattr(config, k)) for k in (
        'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_SERVICE_KEY', 'AUTH_URL',
        'REST_URL', 'CLOUD', 'SESSION_SECRET', 'ADMIN_EMAILS', 'PUBLIC_URL',
        'LOGIN_TRIES', 'MAX_CHATS', 'MAX_MSGS', 'SIGNUPS', 'TOKEN_CAP',
        'GW_PER_MIN', 'GUEST', 'GUEST_CHATS', 'GUEST_CALLS', 'GUEST_PER_IP'))
    config.SUPABASE_URL = NET['stub_at']
    config.AUTH_URL = NET['stub_at'] + '/auth/v1'
    config.REST_URL = NET['stub_at'] + '/rest/v1'
    config.SUPABASE_ANON_KEY = stub.ANON_KEY
    config.SUPABASE_SERVICE_KEY = stub.SERVICE_KEY
    config.CLOUD = True
    config.SESSION_SECRET = SECRET
    config.ADMIN_EMAILS = (ADMIN,)
    config.PUBLIC_URL = ''
    # the sign-in limiter has its own test; everywhere else it would count the
    # whole class's sign-ins against one address
    config.LOGIN_TRIES = 1000
    NET['rest'], stub.rest = _spy('rest')
    NET['gotrue'], stub.gotrue = _spy('gotrue')
    # Guests are allowed to pass api.gate, so from here on a /gw/* call in this
    # file could leave the machine and spend the key. It is pointed at a port
    # nothing is listening on instead: a call that gets through gate comes back
    # 502 'gateway unreachable', which is the proof it got through and costs
    # nothing. NOTHING in this file may ever reach the real gateway.
    NET['gw_was'] = serve.BASE
    serve.BASE = 'http://127.0.0.1:%d' % _dead_port()
    NET['log'] = serve.Handler.log_message
    NET['lines'] = []
    serve.Handler.log_message = lambda h, fmt, *a: NET['lines'].append(
        config.redact(fmt % a))
    NET['app'] = serve.make_server(0, '127.0.0.1')
    NET['at'] = 'http://127.0.0.1:%d' % NET['app'].server_address[1]
    NET['thread'] = threading.Thread(target=NET['app'].serve_forever, daemon=True)
    NET['thread'].start()


def tearDownModule():
    for which in ('app', 'stub'):
        NET[which].shutdown()
        NET[which].server_close()
    NET['thread'].join(timeout=5)
    NET['stub_thread'].join(timeout=5)
    serve.Handler.log_message = NET['log']
    stub.rest, stub.gotrue = NET['rest'], NET['gotrue']
    serve.BASE = NET['gw_was']
    for k, v in NET['was'].items():
        setattr(config, k, v)


class Client(object):
    """One browser. Keeps the cookies it is handed, echoes the CSRF one in the
    header — which is the whole double-submit dance the real front end does —
    and never looks inside the session cookie."""

    def __init__(self, csrf=True):
        self.jar = {}
        self.csrf = csrf

    def call(self, method, path, obj=None, head=None):
        body = None if obj is None else json.dumps(obj).encode('utf-8')
        req = urllib.request.Request(NET['at'] + path, data=body, method=method)
        if body is not None:
            req.add_header('content-type', 'application/json')
        if self.jar:
            req.add_header('cookie', '; '.join(
                '%s=%s' % kv for kv in self.jar.items()))
        token = self.jar.get(config.CSRF_COOKIE)
        if token and self.csrf:
            req.add_header(config.CSRF_HEADER, token)
        for k, v in (head or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                code, got, raw = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            code, got, raw = e.code, e.headers, e.read()
        self.lines = got.get_all('set-cookie') or []
        self._eat(self.lines)
        try:
            return code, json.loads(raw.decode('utf-8')) if raw else {}
        except ValueError:
            return code, {'raw': raw.decode('utf-8', 'replace')}

    def _eat(self, lines):
        for line in lines:
            first = line.split(';')[0]
            if '=' not in first:
                continue
            k, v = first.split('=', 1)
            if not v or 'Max-Age=0' in line:
                self.jar.pop(k.strip(), None)
            else:
                self.jar[k.strip()] = v

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, obj=None):
        return self.call('POST', path, obj if obj is not None else {})

    def put(self, path, obj):
        return self.call('PUT', path, obj)

    def delete(self, path):
        return self.call('DELETE', path)

    def boot(self):
        """What the app does before anything else: read the configuration and
        collect the CSRF cookie that every later write has to echo."""
        return self.get('/api/config')


class Live(unittest.TestCase):
    """Base: an empty database and no rate-limit history per test."""

    def setUp(self):
        stub.wipe()
        limits.reset()
        for cache in (limits.PROFILE, limits.USAGE, limits.SETTINGS, limits.TOUCHED):
            cache.clear()
        del SEEN[:]
        del NET['lines'][:]
        config.CLOUD = True
        config.SIGNUPS = True
        config.MAX_CHATS = NET['was']['MAX_CHATS']
        config.MAX_MSGS = NET['was']['MAX_MSGS']
        config.TOKEN_CAP = NET['was']['TOKEN_CAP']
        config.GW_PER_MIN = NET['was']['GW_PER_MIN']
        for k in ('GUEST', 'GUEST_CHATS', 'GUEST_CALLS', 'GUEST_PER_IP'):
            setattr(config, k, NET['was'][k])

    def joined(self, addr, name='Someone', pw=PW):
        """A signed-up, signed-in browser."""
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/signup',
                           {'email': addr, 'password': pw, 'name': name})
        self.assertEqual(200, code, out)
        return c, out

    def made(self, c, **over):
        """One chat, created through the API. Returns its row."""
        body = {'title': 'A map', 'tab': 'map', 'model': 'claude-opus-5-thinking',
                'messages': [{'role': 'user', 'text': 'hello'}]}
        body.update(over)
        code, out = c.post('/api/data/chats', body)
        self.assertEqual(200, code, out)
        return out['chat']

    def rest_calls(self):
        return [row for row in SEEN if not row[1].startswith('/rpc')]

    def as_user(self):
        """The calls that arrived carrying a person's own token. `/api/config`
        reads app_settings with the anon key, so 'nothing happened' is measured
        as 'nothing happened as anybody' rather than 'no traffic at all'."""
        return [row for row in SEEN if row[2] == 'authenticated']

    def as_service(self):
        """Calls that arrived with the service key, which bypasses every
        policy. On an /api/data path there must never be one."""
        return [row for row in SEEN if row[2] == 'service']


class LocalMode(Live):
    """No Supabase. Every account endpoint has to say so in a sentence rather
    than half-work, and /api/auth/me has to keep answering, because the front
    end asks it on every load to find out which app it is drawing."""

    def setUp(self):
        Live.setUp(self)
        config.CLOUD = False

    def test_me_still_answers_and_says_local(self):
        code, out = Client().get('/api/auth/me')
        self.assertEqual(200, code)
        self.assertIsNone(out['user'])
        self.assertEqual('local', out['mode'])

    def test_the_config_endpoint_says_no_accounts(self):
        code, out = Client().get('/api/config')
        self.assertEqual(200, code)
        self.assertEqual('local', out['mode'])
        self.assertFalse(out['cloud'])
        self.assertFalse(out['auth_required'])
        # No accounts here, so there is nothing to be a guest of: everything is
        # already free and nothing is counted.
        self.assertIsNone(out['guest'])

    def test_signing_in_is_refused_with_a_reason(self):
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/login', {'email': 'a@b.test', 'password': PW})
        self.assertEqual(503, code)
        self.assertIn('Supabase', out['error'])

    def test_there_is_nowhere_to_store_a_chat(self):
        c = Client()
        c.boot()
        for path in ('/api/data/bootstrap', '/api/usage', '/api/profile'):
            code, out = c.get(path)
            self.assertEqual(503, code, path)
            self.assertIn('Supabase', out['error'], path)

    def test_the_admin_console_does_not_exist_either(self):
        c = Client()
        c.boot()
        code, out = c.get('/api/admin/overview')
        self.assertEqual(503, code)


class SignUp(Live):
    def test_a_new_account_is_signed_in_at_once(self):
        """Confirmations off, which is how a laptop and the stub behave. The
        reply carries the person and the cookie, so the app can draw itself
        without a second round trip."""
        c, out = self.joined('alice@cognix.test', 'Alice')
        self.assertEqual('alice@cognix.test', out['user']['email'])
        self.assertEqual('user', out['user']['role'])
        self.assertIn(config.SESSION_COOKIE, c.jar)
        code, me = c.get('/api/auth/me')
        self.assertEqual('alice@cognix.test', me['user']['email'])

    def test_the_cookie_that_comes_back_is_not_readable_by_a_script(self):
        c = Client()
        c.boot()
        c.post('/api/auth/signup', {'email': 'flags@cognix.test', 'password': PW})
        line = [x for x in c.lines if x.startswith(config.SESSION_COOKIE)][0]
        self.assertIn('HttpOnly', line)
        self.assertIn('SameSite=Lax', line)

    def test_a_password_shorter_than_the_rule_is_refused_here_not_there(self):
        """crypto.weak runs before GoTrue sees it, so the message names the
        rule this app has rather than the six characters GoTrue would allow."""
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/signup',
                           {'email': 'weak@cognix.test', 'password': 'short'})
        self.assertEqual(400, code)
        self.assertEqual('password', out['field'])
        self.assertNotIn('weak@cognix.test', [u['email'] for u in stub.USERS.values()])

    def test_the_same_address_twice_is_told_to_sign_in_instead(self):
        self.joined('twice@cognix.test')
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/signup',
                           {'email': 'twice@cognix.test', 'password': PW})
        self.assertEqual(409, code)
        self.assertIn('already an account', out['error'])
        self.assertEqual(1, len([u for u in stub.USERS.values()
                                 if u['email'] == 'twice@cognix.test']))

    def test_closing_signups_closes_them_for_real(self):
        """The switch is read on the server, not hidden in the interface: the
        form disappearing is a courtesy, this is the part that holds."""
        config.SIGNUPS = False
        c = Client()
        code, out = c.boot()
        self.assertFalse(out['signups'])
        code, out = c.post('/api/auth/signup',
                           {'email': 'late@cognix.test', 'password': PW})
        self.assertEqual(403, code)
        self.assertIn('closed', out['error'])
        self.assertEqual([], list(stub.USERS.values()))

    def test_a_signup_body_asking_for_admin_does_not_get_it(self):
        """The whole body is attacker-controlled. Nothing in it reaches the
        role column — that comes from the profile row, which the trigger writes
        and only an administrator can change."""
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/signup',
                           {'email': 'cheeky@cognix.test', 'password': PW,
                            'role': 'admin', 'status': 'active',
                            'token_cap': 99999999})
        self.assertEqual(200, code, out)
        self.assertEqual('user', out['user']['role'])
        row = [r for r in stub.DB['profiles']
               if r['email'] == 'cheeky@cognix.test'][0]
        self.assertEqual('user', row['role'])
        self.assertIsNone(row['token_cap'])
        code, out = c.get('/api/admin/overview')
        self.assertEqual(403, code)


class SignIn(Live):
    def test_the_right_password_works_and_the_wrong_one_does_not(self):
        self.joined('bob@cognix.test', 'Bob')
        d = Client()
        d.boot()
        code, out = d.post('/api/auth/login',
                           {'email': 'bob@cognix.test', 'password': 'not-the-one'})
        self.assertEqual(401, code)
        self.assertEqual('That email and password do not match.', out['error'])
        self.assertNotIn(config.SESSION_COOKIE, d.jar)
        code, out = d.post('/api/auth/login',
                           {'email': 'bob@cognix.test', 'password': PW})
        self.assertEqual(200, code, out)
        self.assertEqual('bob@cognix.test', out['user']['email'])
        self.assertIn(config.SESSION_COOKIE, d.jar)

    def test_an_address_that_has_no_account_gets_the_same_sentence(self):
        """Word for word the same as a wrong password. Anything else turns
        sign-in into a way of asking who has an account here."""
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/login',
                           {'email': 'ghost@cognix.test', 'password': PW})
        self.assertEqual(401, code)
        self.assertEqual('That email and password do not match.', out['error'])

    def test_one_capital_letter_is_not_a_different_account(self):
        self.joined('case@cognix.test')
        d = Client()
        d.boot()
        code, out = d.post('/api/auth/login',
                           {'email': 'CASE@Cognix.TEST', 'password': PW})
        self.assertEqual(200, code, out)
        self.assertEqual('case@cognix.test', out['user']['email'])

    def test_me_without_a_cookie_is_an_answer_not_an_error(self):
        code, out = Client().get('/api/auth/me')
        self.assertEqual(200, code)
        self.assertIsNone(out['user'])
        self.assertEqual('cloud', out['mode'])

    def test_guessing_is_slowed_down_and_told_when_to_come_back(self):
        was, config.LOGIN_TRIES = config.LOGIN_TRIES, 3
        try:
            c = Client()
            c.boot()
            body = {'email': 'guessed@cognix.test', 'password': 'wrong-one-here'}
            for n in range(3):
                self.assertEqual(401, c.post('/api/auth/login', body)[0], n)
            code, out = c.post('/api/auth/login', body)
        finally:
            config.LOGIN_TRIES = was
        self.assertEqual(429, code)
        self.assertGreater(out['retry_after'], 0)


class SignOut(Live):
    def test_signing_out_takes_the_cookies_and_the_access_with_them(self):
        c, _ = self.joined('out@cognix.test')
        self.assertEqual(200, c.get('/api/data/bootstrap')[0])
        code, out = c.post('/api/auth/logout')
        self.assertEqual(200, code)
        self.assertIsNone(out['user'])
        self.assertNotIn(config.SESSION_COOKIE, c.jar)
        self.assertNotIn(config.CSRF_COOKIE, c.jar)
        code, out = c.get('/api/data/bootstrap')
        self.assertEqual(401, code)
        self.assertEqual('Please sign in.', out['error'])

    def test_signing_out_works_even_with_nothing_to_sign_out_of(self):
        """The button has to clear the cookie whatever GoTrue thinks, or a
        session GoTrue has already forgotten becomes one nobody can leave."""
        c = Client()
        c.boot()
        code, out = c.post('/api/auth/logout')
        self.assertEqual(200, code)
        self.assertTrue(out['ok'])


class Guards(Live):
    """The three ways a request can carry something that looks like proof and
    is not: no CSRF token, a cookie from somewhere else, and a session whose
    tokens have stopped working."""

    def test_a_write_without_the_csrf_header_is_refused(self):
        c = Client(csrf=False)
        c.boot()
        self.assertIn(config.CSRF_COOKIE, c.jar)      # it was handed one
        code, out = c.post('/api/auth/signup',
                           {'email': 'nocsrf@cognix.test', 'password': PW})
        self.assertEqual(403, code)
        self.assertIn('CSRF', out['error'])
        self.assertEqual([], list(stub.USERS.values()))

    def test_a_write_with_the_wrong_csrf_header_is_refused_too(self):
        """Double submit only works if the two halves are compared. A page on
        another origin can send a header; it cannot read this cookie."""
        c, _ = self.joined('csrf@cognix.test')
        code, out = c.call('POST', '/api/data/chats', {'title': 'x'},
                           {config.CSRF_HEADER: 'not-the-cookie'})
        self.assertEqual(403, code)
        self.assertEqual([], stub.DB['chats'])

    def test_the_csrf_check_happens_before_anything_reads_the_session(self):
        """Order matters: if the session were read first, a cross-origin POST
        would still cost a token refresh and a database round trip."""
        c = Client(csrf=False)
        c.boot()
        code, _ = c.post('/api/data/chats', {'title': 'x'})
        self.assertEqual(403, code)
        self.assertEqual([], self.as_user())

    def test_a_cookie_signed_by_another_instance_is_not_a_session(self):
        c = Client()
        c.boot()
        c.jar[config.SESSION_COOKIE] = crypto.seal(
            'test-secret-' + 'z' * 40,
            {'u': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'e': 'x@y.test',
             'r': 'admin', 'at': 'a', 'rt': 'r', 'ax': time.time() + 3600})
        code, out = c.get('/api/data/bootstrap')
        self.assertEqual(401, code)
        self.assertEqual('Please sign in.', out['error'])
        self.assertEqual([], self.as_user())

    def test_a_session_that_cannot_be_refreshed_is_over_and_is_cleared(self):
        """The token has expired and the refresh token is not one GoTrue has.
        The answer has to be 401 *and* a cleared cookie, or the browser keeps
        presenting a dead session on every request from now on."""
        c, _ = self.joined('stale@cognix.test')
        sess = crypto.unseal(SECRET, c.jar[config.SESSION_COOKIE])
        sess['ax'] = time.time() - 10
        sess['rt'] = 'not-a-refresh-token'
        c.jar[config.SESSION_COOKIE] = crypto.seal(SECRET, sess)
        code, out = c.get('/api/data/bootstrap')
        self.assertEqual(401, code)
        self.assertIn('run out', out['error'])
        self.assertNotIn(config.SESSION_COOKIE, c.jar)

    def test_an_expired_token_with_a_good_refresh_token_just_carries_on(self):
        """The other half of the same path, and the one a person actually
        meets: twenty minutes idle, one silent refresh, a new cookie."""
        c, _ = self.joined('fresh@cognix.test')
        was = c.jar[config.SESSION_COOKIE]
        sess = crypto.unseal(SECRET, was)
        sess['ax'] = time.time() - 10
        c.jar[config.SESSION_COOKIE] = crypto.seal(SECRET, sess)
        code, out = c.get('/api/data/bootstrap')
        self.assertEqual(200, code, out)
        self.assertEqual('fresh@cognix.test', out['user']['email'])
        self.assertNotEqual(was, c.jar[config.SESSION_COOKIE])
        self.assertIn(('POST', '/token', 'anon'), SEEN)


class WhoseToken(Live):
    """The property the whole design rests on.

    Every row this API reads or writes is read or written with the caller's own
    access token, so supabase/policies.sql is what decides what comes back. The
    service key bypasses all of it — so what these tests assert is its absence
    from every path a browser can reach."""

    def test_a_whole_session_of_work_never_uses_the_service_key(self):
        c, _ = self.joined('work@cognix.test')
        chat = self.made(c)
        cid = chat['id']
        self.assertEqual(200, c.get('/api/data/bootstrap')[0])
        self.assertEqual(200, c.get('/api/data/chats')[0])
        self.assertEqual(200, c.get('/api/data/chats/' + cid)[0])
        self.assertEqual(200, c.put('/api/data/chats/' + cid,
                                    {'title': 'Renamed', 'version': 1,
                                     'messages': [{'role': 'user', 'text': 'again'}],
                                     'map': {'nodes': []}})[0])
        self.assertEqual(200, c.get('/api/usage')[0])
        self.assertEqual(200, c.get('/api/profile')[0])
        self.assertEqual(200, c.put('/api/profile', {'name': 'Worked'})[0])
        self.assertEqual(200, c.get('/api/data/export')[0])
        self.assertEqual(200, c.delete('/api/data/chats/' + cid)[0])
        self.assertEqual([], self.as_service())
        self.assertTrue(self.as_user())

    def test_every_table_this_app_touches_is_touched_as_somebody(self):
        c, _ = self.joined('rows@cognix.test')
        self.made(c)
        c.get('/api/data/bootstrap')
        rows = [r for r in SEEN if r[1] in ('/chats', '/messages', '/maps',
                                            '/profiles', '/usage_events')]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual('authenticated', row[2], row)

    def test_signing_up_uses_the_key_supabase_publishes_not_the_secret_one(self):
        """GoTrue's own endpoints take the anon key. The service key can create
        a confirmed account out of nothing, which is why only the invite path in
        the admin panel is allowed to hold it."""
        self.joined('anon@cognix.test')
        self.assertTrue(SEEN)
        self.assertEqual([], self.as_service())
        self.assertIn(('POST', '/signup', 'anon'), SEEN)


class Isolation(Live):
    """Two accounts on one instance. Everything below is the same request with
    somebody else's id in it."""

    def test_a_list_holds_only_your_own_chats(self):
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        mine = self.made(a, title='Mine')
        theirs = self.made(b, title='Theirs')
        self.assertNotEqual(mine['id'], theirs['id'])
        code, out = a.get('/api/data/chats')
        self.assertEqual([mine['id']], [c['id'] for c in out['chats']])
        code, out = a.get('/api/data/bootstrap')
        self.assertEqual(['Mine'], [c['title'] for c in out['chats']])
        code, out = b.get('/api/data/chats')
        self.assertEqual([theirs['id']], [c['id'] for c in out['chats']])

    def test_reading_somebody_elses_chat_by_id_is_a_404(self):
        """Not a 403. A chat that is not yours and a chat that never existed are
        the same answer, because telling them apart is telling somebody that an
        id they guessed is real."""
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        theirs = self.made(b, title='Theirs')
        code, out = a.get('/api/data/chats/' + theirs['id'])
        self.assertEqual(404, code)
        self.assertEqual('That chat is not there.', out['error'])
        gone = '11111111-2222-3333-4444-555555555555'
        code, out = a.get('/api/data/chats/' + gone)
        self.assertEqual(404, code)
        self.assertEqual('That chat is not there.', out['error'])

    def test_writing_over_somebody_elses_chat_changes_nothing(self):
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        theirs = self.made(b, title='Theirs')
        code, out = a.put('/api/data/chats/' + theirs['id'],
                          {'title': 'Mine now', 'version': 1,
                           'messages': [{'role': 'user', 'text': 'hijacked'}]})
        self.assertEqual(404, code)
        code, out = b.get('/api/data/chats/' + theirs['id'])
        self.assertEqual('Theirs', out['chat']['title'])
        self.assertEqual(['hello'], [m['text'] for m in out['messages']])

    def test_deleting_somebody_elses_chat_deletes_nothing(self):
        """This one answers 200, and that is deliberate: the same words as
        deleting a chat you had already deleted. What matters is the row."""
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        theirs = self.made(b, title='Theirs')
        code, out = a.delete('/api/data/chats/' + theirs['id'])
        self.assertEqual(200, code)
        self.assertEqual(1, len(stub.DB['chats']))
        code, out = b.get('/api/data/chats')
        self.assertEqual([theirs['id']], [c['id'] for c in out['chats']])

    def test_an_export_is_your_own_chats_and_nothing_else(self):
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        self.made(a, title='Mine')
        self.made(b, title='Theirs')
        code, out = a.get('/api/data/export')
        self.assertEqual(200, code)
        self.assertEqual('a@cognix.test', out['email'])
        self.assertEqual(['Mine'], [c['title'] for c in out['chats']])
        self.assertEqual(['hello'],
                         [m['text'] for m in out['chats'][0]['messages']])

    def test_an_administrator_does_not_get_everybody_elses_sidebar(self):
        """The reason api._mine exists at all. chats_read_admin grants an
        administrator a select across the whole chats table, because the console
        lists titles across accounts — and this API is not the console. Take the
        owner filter out of _bootstrap and this test goes red while every policy
        stays exactly as it is."""
        a, _ = self.joined('a@cognix.test', 'A')
        mine = self.made(a, title='Someone elses')
        boss, _ = self.joined(ADMIN, 'Boss')
        self.assertEqual(200, boss.get('/api/admin/overview')[0])   # promotes
        ours = self.made(boss, title='The bosss own')
        code, out = boss.get('/api/data/bootstrap')
        self.assertEqual('admin', out['user']['role'])
        self.assertEqual([ours['id']], [c['id'] for c in out['chats']])
        code, out = boss.get('/api/data/chats/' + mine['id'])
        self.assertEqual(404, code)
        code, out = boss.get('/api/data/export')
        self.assertEqual(['The bosss own'], [c['title'] for c in out['chats']])


class Concurrency(Live):
    """Two tabs, one chat. The version column is the whole mechanism."""

    def test_a_save_moves_the_version_on(self):
        c, _ = self.joined('v@cognix.test')
        chat = self.made(c)
        self.assertEqual(1, chat['version'])
        code, out = c.put('/api/data/chats/' + chat['id'],
                          {'title': 'Two', 'version': 1, 'messages': []})
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['version'])
        code, out = c.get('/api/data/chats/' + chat['id'])
        self.assertEqual(2, out['chat']['version'])
        self.assertEqual([], out['messages'])

    def test_the_slower_tab_is_told_instead_of_overwriting_the_other(self):
        """Both tabs loaded version 1. The first save wins and the second is
        refused — the alternative is that whichever tab the person happens to
        touch last silently erases the other one's work."""
        c, _ = self.joined('tabs@cognix.test')
        cid = self.made(c)['id']
        body = {'version': 1, 'messages': [{'role': 'user', 'text': 'hello'}]}
        code, out = c.put('/api/data/chats/' + cid, dict(body, title='From tab one'))
        self.assertEqual(200, code, out)
        code, out = c.put('/api/data/chats/' + cid, dict(body, title='From tab two'))
        self.assertEqual(409, code)
        self.assertIn('changed somewhere else', out['error'])
        self.assertEqual(2, out['version'])
        code, out = c.get('/api/data/chats/' + cid)
        self.assertEqual('From tab one', out['chat']['title'])

    def test_a_save_that_never_mentioned_messages_keeps_them(self):
        """Renaming a chat must not read as 'delete every message in it'. That
        is what shape.snapshot's has_messages is for."""
        c, _ = self.joined('rename@cognix.test')
        cid = self.made(c)['id']
        code, out = c.put('/api/data/chats/' + cid, {'title': 'Retitled',
                                                     'version': 1})
        self.assertEqual(200, code, out)
        code, out = c.get('/api/data/chats/' + cid)
        self.assertEqual('Retitled', out['chat']['title'])
        self.assertEqual(['hello'], [m['text'] for m in out['messages']])

    def test_the_map_and_the_chat_move_together(self):
        c, _ = self.joined('map@cognix.test')
        cid = self.made(c, map={'nodes': [{'id': 'n1', 'label': 'One'}]})['id']
        code, out = c.get('/api/data/chats/' + cid)
        self.assertEqual([{'id': 'n1', 'label': 'One'}], out['map']['nodes'])
        code, out = c.put('/api/data/chats/' + cid,
                          {'title': 'A map', 'version': 1, 'messages': [],
                           'map': {'nodes': []}, 'style': {'theme': 'dark'}})
        self.assertEqual(200, code, out)
        code, out = c.get('/api/data/chats/' + cid)
        self.assertEqual([], out['map']['nodes'])
        self.assertEqual({'theme': 'dark'}, out['style'])


class Ceilings(Live):
    """The limits that stop one account from becoming everybody's problem.
    Each one is a sentence with a number in it, because a refusal somebody
    cannot act on is a bug report."""

    def test_the_chat_limit_holds_and_names_itself(self):
        config.MAX_CHATS = 2
        c, _ = self.joined('full@cognix.test')
        self.made(c, title='One')
        self.made(c, title='Two')
        code, out = c.post('/api/data/chats', {'title': 'Three'})
        self.assertEqual(409, code)
        self.assertIn('Delete one', out['error'])
        self.assertIn('2 chats', out['error'])
        self.assertEqual(2, len(stub.DB['chats']))

    def test_one_chat_cannot_hold_more_messages_than_the_rule_allows(self):
        config.MAX_MSGS = 3
        c, _ = self.joined('chatty@cognix.test')
        code, out = c.post('/api/data/chats', {
            'title': 'Long', 'messages': [{'role': 'user', 'text': str(n)}
                                          for n in range(4)]})
        self.assertEqual(400, code)
        self.assertEqual('messages', out['field'])
        self.assertIn('start a new chat', out['error'])
        self.assertEqual([], stub.DB['chats'])

    def test_the_usage_endpoint_answers_in_the_shape_the_meter_draws(self):
        c, out = self.joined('meter@cognix.test')
        config.TOKEN_CAP = 1000
        limits.USAGE.clear()
        code, out = c.get('/api/usage')
        self.assertEqual(200, code, out)
        self.assertEqual(0, out['used'])
        self.assertEqual(1000, out['cap'])
        self.assertEqual(1000, out['left'])
        self.assertFalse(out['unlimited'])
        self.assertEqual(time.strftime('%Y-%m'), out['month'])

    def test_a_cap_of_zero_means_no_ceiling_at_all(self):
        """How an administrator says 'let them work'. The distinction matters:
        a cap of zero must not read as 'no tokens'."""
        c, _ = self.joined('free@cognix.test')
        config.TOKEN_CAP = 0
        limits.USAGE.clear()
        code, out = c.get('/api/usage')
        self.assertTrue(out['unlimited'])
        self.assertIsNone(out['left'])


class Import(Live):
    """The localStorage backup, uploaded the first time somebody signs in on a
    machine that already had maps on it."""

    def backup(self, *ids):
        return {'chats': [{'local_id': i, 'title': 'Chat ' + i, 'tab': 'map',
                           'model': 'claude-opus-5-thinking',
                           'messages': [{'role': 'user', 'text': i}]}
                          for i in ids]}

    def test_offering_the_same_backup_twice_does_not_double_it(self):
        """Matched on local_id, which is what makes the button safe to press
        again after a timeout — the failure mode otherwise is a sidebar with
        everything in it twice and no way back."""
        c, _ = self.joined('import@cognix.test')
        code, out = c.post('/api/data/import', self.backup('map-1', 'map-2'))
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['added'])
        self.assertEqual(0, out['skipped'])
        code, out = c.post('/api/data/import', self.backup('map-1', 'map-2'))
        self.assertEqual(0, out['added'])
        self.assertEqual(2, out['skipped'])
        code, out = c.get('/api/data/chats')
        self.assertEqual(2, len(out['chats']))
        self.assertEqual(['Chat map-1', 'Chat map-2'],
                         sorted(x['title'] for x in out['chats']))

    def test_the_ids_come_from_a_browser_so_they_are_not_unique_between_people(self):
        """Two laptops both call their first map 'map-1'. local_id is scoped to
        the account, and the select that reads it carries the owner filter."""
        a, _ = self.joined('a@cognix.test', 'A')
        b, _ = self.joined('b@cognix.test', 'B')
        self.assertEqual(1, a.post('/api/data/import', self.backup('map-1'))[1]['added'])
        self.assertEqual(1, b.post('/api/data/import', self.backup('map-1'))[1]['added'])
        for who in (a, b):
            code, out = who.get('/api/data/chats')
            self.assertEqual(1, len(out['chats']))
        self.assertEqual(2, len(stub.DB['chats']))

    def test_a_big_backup_arrives_in_batches_and_says_what_is_left(self):
        """Forty per request. `remaining` is how much of what the server was
        handed it did not get to, and persist.js slices exactly that much off
        before calling again — so forty-five chats are two requests."""
        c, _ = self.joined('big@cognix.test')
        ids = ['map-%d' % n for n in range(45)]
        code, out = c.post('/api/data/import', self.backup(*ids))
        self.assertEqual(200, code, out)
        self.assertEqual(40, out['added'])
        self.assertEqual(5, out['remaining'])
        self.assertFalse(out['full'])
        code, out = c.post('/api/data/import', self.backup(*ids[40:]))
        self.assertEqual(5, out['added'])
        self.assertEqual(0, out['remaining'])
        code, out = c.get('/api/data/chats')
        self.assertEqual(45, len(out['chats']))

    def test_an_account_at_its_chat_limit_is_told_the_backup_did_not_all_fit(self):
        """`full` is the flag persist.js turns on to keep the local copy and say
        'some were brought over'. Without it, an account with room for two out
        of forty-five is told everything arrived and the browser then deletes
        the only copy of the other forty-three."""
        config.MAX_CHATS = 2
        c, _ = self.joined('nearly@cognix.test')
        code, out = c.post('/api/data/import',
                           self.backup(*['map-%d' % n for n in range(45)]))
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['added'])
        self.assertEqual(38, out['skipped'])
        self.assertTrue(out['full'])
        self.assertEqual(2, len(stub.DB['chats']))

    def test_the_ceiling_does_not_quietly_shrink_the_backup_on_the_way_in(self):
        """shape.bundle used to truncate to MAX_CHATS, which made the two cases
        indistinguishable: a batch that fitted and a batch that was thrown away
        both came back as `remaining: 0, full: false`."""
        config.MAX_CHATS = 2
        c, _ = self.joined('shrunk@cognix.test')
        code, out = c.post('/api/data/import',
                           self.backup(*['map-%d' % n for n in range(40)]))
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['added'])
        self.assertEqual(0, out['remaining'])
        self.assertTrue(out['full'])

    def test_a_backup_with_nothing_in_it_is_not_an_error(self):
        c, _ = self.joined('empty@cognix.test')
        code, out = c.post('/api/data/import', {'chats': []})
        self.assertEqual(200, code)
        self.assertEqual(0, out['added'])
        code, out = c.post('/api/data/import', {'chats': 'not a list'})
        self.assertEqual(400, code)
        self.assertEqual('chats', out['field'])


class Gateway(Live):
    """/gw/* is the one path that spends money, so it is the one path with a
    person, a standing, a rate and a ceiling in front of it. serve.py asks
    api.gate before it reads the body or looks at the key.

    Every test here is refused by gate, on purpose: nothing in this suite is
    allowed to reach the real gateway, and setUpModule points BASE at a dead
    port so that it cannot."""

    def ask(self, c):
        return c.call('POST', '/gw/v1/messages',
                      {'model': 'claude-opus-5-thinking', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}]})

    def test_a_model_call_with_no_account_at_all_is_the_free_trial(self):
        """It used to be a 401. A visitor with no session is now a guest, so the
        call goes through gate — 502 is the dead port on the other side of it,
        which is the proof that nothing refused it — and no row is written for
        it, because there is nobody to own one."""
        c = Client()
        c.boot()
        code, out = self.ask(c)
        self.assertEqual(502, code, out)
        self.assertEqual([], self.as_user())

    def test_with_guests_turned_off_it_is_a_refusal_again(self):
        config.GUEST = False
        c = Client()
        c.boot()
        code, out = self.ask(c)
        self.assertEqual(401, code)
        self.assertEqual('Please sign in.', out['error'])
        self.assertEqual([], self.as_user())

    def test_the_monthly_ceiling_is_checked_before_the_call_is_made(self):
        """The refusal has to happen here rather than after the tokens are
        spent, which is why gate reads usage before serve.py touches the key."""
        c, out = self.joined('spend@cognix.test')
        uid = out['user']['id']
        config.TOKEN_CAP = 1000
        stub.DB['usage_events'].append({
            'id': 1, 'user_id': uid, 'kind': 'map', 'model': 'x',
            'prompt_tokens': 900, 'completion_tokens': 200,
            'total_tokens': 1100, 'ms': 10, 'ok': True, 'note': '',
            'created_at': stub.iso()})
        limits.USAGE.clear()
        code, out = self.ask(c)
        self.assertEqual(402, code)
        self.assertIn('1,000 tokens', out['error'])
        self.assertEqual(1100, out['usage']['used'])
        self.assertEqual(0, out['usage']['left'])

    def test_last_months_tokens_are_not_this_months_problem(self):
        c, out = self.joined('rolled@cognix.test')
        uid = out['user']['id']
        config.TOKEN_CAP = 1000
        stub.DB['usage_events'].append({
            'id': 1, 'user_id': uid, 'kind': 'map', 'model': 'x',
            'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 99999,
            'ms': 0, 'ok': True, 'note': '', 'created_at': '2019-04-01T00:00:00Z'})
        limits.USAGE.clear()
        code, out = c.get('/api/usage')
        self.assertEqual(0, out['used'])

    def test_hammering_the_button_is_slowed_down(self):
        """The bucket is filled here rather than by making the calls, because a
        call that passes gate is a call that leaves this machine."""
        c, out = self.joined('fast@cognix.test')
        uid = out['user']['id']
        for _ in range(config.GW_PER_MIN):
            limits.hit('gw:' + uid, config.GW_PER_MIN, 60)
        code, out = self.ask(c)
        self.assertEqual(429, code)
        self.assertIn('model calls', out['error'])


class Guest(Live):
    """The free trial: what somebody with no account can do, and where it stops.

    A guest keeps their chats in their own browser, so the only thing this
    server holds for them is one number — how many model calls they have spent —
    and it holds it in a cookie it signed. These tests are about that number:
    that it is believed only when the signature is ours, that it survives the
    round trip, that running out is a 402 saying what to do next, and that
    clearing cookies runs into the per-address backstop instead of a fresh
    allowance every time.

    A 502 below is a pass through gate: setUpModule points the gateway at a dead
    port, so 'unreachable' is how an allowed call ends here."""

    def ask(self, c):
        return c.call('POST', '/gw/v1/messages',
                      {'model': 'claude-opus-5-thinking', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}]})

    def tally(self, c):
        """The guest's own count, read back out of the cookie they were handed."""
        return sessions.guest_read(c.jar)

    def test_the_app_opens_for_a_visitor_with_no_account(self):
        """The first thing the page asks for. It has to come back saying the app
        is drawable — auth_required false — and carrying the allowance, or the
        front end has nothing to go on and sends them to the sign-in page."""
        c = Client()
        code, out = c.boot()
        self.assertEqual(200, code)
        self.assertTrue(out['cloud'])
        self.assertFalse(out['auth_required'])
        self.assertEqual(config.GUEST_CHATS, out['guest']['chats'])
        self.assertEqual(config.GUEST_CALLS, out['guest']['calls'])
        self.assertEqual(0, out['guest']['used'])
        self.assertEqual(config.GUEST_CALLS, out['guest']['left'])
        self.assertIn(config.GUEST_COOKIE, c.jar)

    def test_the_cookie_is_not_readable_by_the_page(self):
        """It is the count, not a preference. /api/config reports the numbers in
        plain JSON, so the page has no reason to read the cookie — and a script
        that could read it could edit it."""
        c = Client()
        c.boot()
        line = [x for x in c.lines if x.startswith(config.GUEST_COOKIE + '=')]
        self.assertEqual(1, len(line))
        self.assertIn('HttpOnly', line[0])
        self.assertIn('SameSite=Lax', line[0])

    def test_turning_guests_off_puts_the_sign_in_page_back(self):
        config.GUEST = False
        code, out = Client().boot()
        self.assertTrue(out['auth_required'])
        self.assertIsNone(out['guest'])

    def test_a_call_spends_one_and_the_count_comes_back(self):
        c = Client()
        c.boot()
        self.assertEqual(0, self.tally(c)['n'])
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(1, self.tally(c)['n'])
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(2, self.tally(c)['n'])
        code, out = c.boot()
        self.assertEqual(2, out['guest']['used'])
        self.assertEqual(config.GUEST_CALLS - 2, out['guest']['left'])

    def test_the_trial_runs_out_and_the_answer_says_what_to_do(self):
        config.GUEST_CALLS = 2
        c = Client()
        c.boot()
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(502, self.ask(c)[0])
        code, out = self.ask(c)
        self.assertEqual(402, code)
        self.assertIn('free trial', out['error'])
        self.assertIn('account', out['error'])
        self.assertEqual('/app/auth/', out['signin'])
        self.assertEqual(0, out['guest']['left'])
        # and it stays run out, rather than the refusal itself resetting it
        self.assertEqual(402, self.ask(c)[0])

    def test_only_a_tally_this_server_signed_is_believed(self):
        """The count is the whole ceiling, so it is worth exactly as much as the
        signature on it. One sealed with any other key reads as no cookie at
        all — which starts a fresh allowance, not a spent one."""
        spent = {'g': 'abcdefghi', 'n': 99, 'x': time.time() + 86400}
        c = Client()
        c.boot()
        c.jar[config.GUEST_COOKIE] = crypto.seal(SECRET, spent)
        self.assertEqual(402, self.ask(c)[0])
        c.jar[config.GUEST_COOKIE] = crypto.seal('not-the-secret', spent)
        self.assertEqual(502, self.ask(c)[0])
        c.jar[config.GUEST_COOKIE] = 'v1.garbage.garbage'
        self.assertEqual(502, self.ask(c)[0])

    def test_an_expired_tally_is_a_fresh_visit(self):
        c = Client()
        c.boot()
        c.jar[config.GUEST_COOKIE] = crypto.seal(
            SECRET, {'g': 'abcdefghi', 'n': 99, 'x': time.time() - 5})
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(1, self.tally(c)['n'])

    def test_clearing_cookies_runs_into_the_address_instead(self):
        """The cookie cannot be edited, but it can be thrown away. This is the
        answer to that: in-memory, per instance, per hour — a bound on casual
        abuse rather than a wall."""
        config.GUEST_PER_IP = 2
        for _ in range(2):
            fresh = Client()
            fresh.boot()
            self.assertEqual(502, self.ask(fresh)[0])
        fresh = Client()
        fresh.boot()
        code, out = self.ask(fresh)
        self.assertEqual(429, code)
        self.assertIn('from this address', out['error'])

    def test_a_guest_who_is_already_out_does_not_spend_the_address(self):
        """A shared address should not be used up by one visitor's retries after
        their own count has run out — so the per-visitor ceiling is checked
        first, and a 402 costs the address nothing."""
        config.GUEST_CALLS = 1
        config.GUEST_PER_IP = 3
        c = Client()
        c.boot()
        self.assertEqual(502, self.ask(c)[0])
        for _ in range(5):
            self.assertEqual(402, self.ask(c)[0])
        for _ in range(2):
            fresh = Client()
            fresh.boot()
            self.assertEqual(502, self.ask(fresh)[0], 'the address was used up')

    def test_hammering_the_button_is_slowed_down_for_a_guest_too(self):
        config.GW_PER_MIN = 2
        c = Client()
        c.boot()
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(502, self.ask(c)[0])
        code, out = self.ask(c)
        self.assertEqual(429, code)
        self.assertIn('model calls', out['error'])

    def test_nothing_is_written_for_a_guest(self):
        """No person, no rows. Not a usage event, not a chat, and nothing at all
        carrying anybody's token — which is also why a guest costs no database."""
        c = Client()
        c.boot()
        del SEEN[:]
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual([], self.as_user())
        self.assertEqual([], self.as_service())
        self.assertEqual([], stub.DB['usage_events'])
        self.assertEqual([], stub.DB['chats'])

    def test_a_guest_has_nowhere_to_store_anything(self):
        """Their chats live in their own browser. The endpoints that write rows
        are for people who own rows, and they still say so."""
        c = Client()
        c.boot()
        for path in ('/api/data/bootstrap', '/api/usage', '/api/profile'):
            code, out = c.get(path)
            self.assertEqual(401, code, path)
            self.assertEqual('Please sign in.', out['error'], path)

    def test_making_an_account_drops_the_spent_allowance(self):
        """Otherwise signing up two calls in would leave the tally in the jar,
        and it would still be there — spent — if they ever signed out again."""
        c = Client()
        c.boot()
        self.assertEqual(502, self.ask(c)[0])
        self.assertEqual(1, self.tally(c)['n'])
        code, out = c.post('/api/auth/signup',
                           {'email': 'trial@cognix.test', 'password': PW,
                            'name': 'Trial'})
        self.assertEqual(200, code, out)
        self.assertNotIn(config.GUEST_COOKIE, c.jar)
        code, out = c.boot()
        self.assertIsNone(out['guest'])

    def test_a_sign_in_that_has_run_out_is_not_a_guest(self):
        """The difference matters: a guest is somebody who never signed in, and
        an expired session is somebody with work on screen that belongs to an
        account. The second one has to be told, not quietly given a free call."""
        c = Client()
        c.boot()
        c.jar[config.SESSION_COOKIE] = crypto.seal(SECRET, {
            'u': '00000000-0000-4000-8000-000000000001', 'e': 'gone@cognix.test',
            'n': 'Gone', 'r': 'user', 'v': True, 'at': '', 'rt': 'dead-token',
            'ax': 0, 'iat': int(time.time())})
        code, out = self.ask(c)
        self.assertEqual(401, code)
        self.assertIn('run out', out['error'])
        self.assertNotIn(config.SESSION_COOKIE, c.jar)


class Profile(Live):
    def test_the_name_changes_in_both_places_it_is_kept(self):
        """The profile row is what the app reads and GoTrue's copy is what a
        fresh sign-in would show. Writing one and not the other means the old
        name comes back for a moment after signing in again."""
        c, _ = self.joined('named@cognix.test', 'Before')
        code, out = c.put('/api/profile', {'name': 'After'})
        self.assertEqual(200, code, out)
        self.assertEqual('After', out['user']['name'])
        code, out = c.get('/api/auth/me')
        self.assertEqual('After', out['user']['name'])
        d = Client()
        d.boot()
        code, out = d.post('/api/auth/login',
                           {'email': 'named@cognix.test', 'password': PW})
        self.assertEqual('After', out['user']['name'])

    def test_a_request_that_changes_nothing_says_so(self):
        c, _ = self.joined('nothing@cognix.test')
        code, out = c.put('/api/profile', {'nickname': 'ignored'})
        self.assertEqual(400, code)
        self.assertIn('nothing to change', out['error'])

    def test_the_role_and_the_status_are_not_yours_to_send(self):
        """PUT /api/profile takes one field. Everything else in the body is
        ignored — and the columns it would have written are the ones the admin
        panel owns."""
        c, _ = self.joined('sneaky@cognix.test')
        code, out = c.put('/api/profile', {'name': 'Fine', 'role': 'admin',
                                          'status': 'active', 'token_cap': 10 ** 9})
        self.assertEqual(200, code, out)
        self.assertEqual('user', out['user']['role'])
        row = [r for r in stub.DB['profiles']
               if r['email'] == 'sneaky@cognix.test'][0]
        self.assertEqual('user', row['role'])
        self.assertIsNone(row['token_cap'])


class Refusals(Live):
    """The shape of a no. Every one of these is a sentence, because the front
    end prints whatever comes back and 'Bad Request' helps nobody."""

    def test_an_endpoint_that_does_not_exist(self):
        code, out = Client().get('/api/nonsense')
        self.assertEqual(404, code)
        self.assertEqual('No such endpoint.', out['error'])

    def test_the_root_says_which_mode_it_is_in(self):
        code, out = Client().get('/api')
        self.assertEqual(200, code)
        self.assertEqual('cloud', out['mode'])

    def test_an_id_that_is_not_an_id(self):
        c, _ = self.joined('ids@cognix.test')
        code, out = c.get('/api/data/chats/not-a-uuid')
        self.assertEqual(400, code)
        self.assertEqual('chat', out['field'])

    def test_the_wrong_method_names_the_right_one(self):
        c, _ = self.joined('method@cognix.test')
        code, out = c.put('/api/usage', {})
        self.assertEqual(405, code)
        self.assertIn('GET', out['error'])

    def test_a_body_that_is_not_an_object(self):
        c, _ = self.joined('body@cognix.test')
        for body in ([1, 2, 3], 'a string', 7):
            code, out = c.call('POST', '/api/data/chats', body)
            self.assertEqual(400, code, body)
            self.assertIn('has to be an object', out['error'])
        self.assertEqual([], stub.DB['chats'])


if __name__ == '__main__':
    unittest.main(verbosity=2)












