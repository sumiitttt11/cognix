#!/usr/bin/env python3
"""Behaviour of serve.py, against a real socket on an ephemeral port.

    python -m unittest discover -s tests -v

Nothing here reaches the gateway. The proxy is exercised only on the paths that
refuse before spending money, which is every path except a valid POST — and a
valid POST is checked by pointing BASE at a stub server in this process.
"""
import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import serve                                                    # noqa: E402


def get(url, method='GET', body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class NoHop(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def hop(url, method='GET'):
    """get(), stopped at the redirect instead of following it."""
    opener = urllib.request.build_opener(NoHop)
    req = urllib.request.Request(url, method=method)
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class Base(unittest.TestCase):
    KEY = None                       # what serve.KEY is set to for this class

    @classmethod
    def setUpClass(cls):
        cls._key = serve.KEY
        serve.KEY = cls.KEY
        # These cases are about the proxy and the static layer, not about
        # accounts. Local mode is pinned so the suite answers the same way
        # whether or not there are Supabase keys in .env; the cloud-mode
        # behaviour of the same paths is test_api.py's job.
        cls._cloud = serve.config.CLOUD
        serve.config.CLOUD = False
        # keep the log rather than printing it: quiet output, and the redaction
        # that every line goes through becomes something a test can read back
        cls.logs = []
        cls._log = serve.Handler.log_message
        serve.Handler.log_message = lambda h, fmt, *a: cls.logs.append(serve.redact(fmt % a))
        cls.srv = serve.make_server(0, '127.0.0.1')
        cls.port = cls.srv.server_address[1]
        cls.base = 'http://127.0.0.1:%d' % cls.port
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)
        serve.Handler.log_message = cls._log
        serve.KEY = cls._key
        serve.config.CLOUD = cls._cloud


class Static(Base):
    def test_the_app_is_served(self):
        code, h, body = get(self.base + '/app/')
        self.assertEqual(200, code)
        self.assertIn(b'Cognix', body)

    def test_its_own_source_is_not_content(self):
        for path in ('/serve.py', '/.env', '/.env.example', '/.gitignore',
                     '/tests/test_server.py', '/app/../serve.py'):
            code, h, body = get(self.base + path)
            self.assertEqual(404, code, path)
            self.assertNotIn(b'COGNIX_KEY', body, path)

    def test_no_key_leaks_through_a_denied_path(self):
        code, h, body = get(self.base + '/.env')
        self.assertNotIn(b'sk-', body)

    def test_security_headers_on_every_response(self):
        code, h, body = get(self.base + '/app/')
        self.assertEqual('nosniff', h.get('x-content-type-options'))
        self.assertEqual('DENY', h.get('x-frame-options'))
        self.assertEqual('same-origin', h.get('referrer-policy'))
        self.assertEqual('same-origin', h.get('cross-origin-opener-policy'))
        self.assertIn('geolocation=()', h.get('permissions-policy', ''))

    def test_one_strict_csp_everywhere(self):
        """One application, one policy. Every page loads its scripts and styles
        from this origin by URL, so nothing here needs 'unsafe-inline' and no
        path is allowed it."""
        for p in ('/app/', '/app/auth/', '/app/styles.css', '/nothing-here'):
            _, h, _ = get(self.base + p)
            csp = h.get('content-security-policy', '')
            self.assertNotIn('unsafe-inline', csp, p)
            self.assertIn("default-src 'none'", csp, p)
            self.assertIn("frame-ancestors 'none'", csp, p)

    def test_the_bare_address_is_the_app(self):
        """/ used to be a hub of single-file prototypes. Those are gone, so the
        address somebody types has to land on the app rather than 404."""
        code, h, _ = hop(self.base + '/')
        self.assertEqual(302, code)
        self.assertEqual('/app/', h.get('location'))
        code, _, body = get(self.base + '/')          # urlopen follows it
        self.assertEqual(200, code)
        self.assertIn(b'Cognix', body)

    def test_no_directory_listing(self):
        code, _, _ = get(self.base + '/app/src/')
        self.assertEqual(404, code)

    def test_head_works_and_sends_no_body(self):
        code, h, body = get(self.base + '/app/', method='HEAD')
        self.assertEqual(200, code)
        self.assertEqual(b'', body)


# assembled at runtime so the "no key literal in source" static test, which
# scans this file too, does not trip over the fixture
FAKE = 'sk-' + 'zz9' * 6
JSONH = {'content-type': 'application/json'}


def post(url, obj, headers=None, raw=None):
    h = dict(JSONH)
    h.update(headers or {})
    body = raw if raw is not None else json.dumps(obj).encode('utf-8')
    return get(url, method='POST', body=body, headers=h)


class Stub(BaseHTTPRequestHandler):
    """Stands in for the gateway. Records what it was sent; answers with
    whatever the test parked in Stub.reply."""
    seen = None
    reply = (200, {'ok': True})

    def do_POST(self):
        n = int(self.headers.get('content-length') or 0)
        Stub.seen = {'path': self.path, 'body': self.rfile.read(n),
                     'headers': {k.lower(): v for k, v in self.headers.items()}}
        code, obj = Stub.reply
        out = obj if isinstance(obj, bytes) else json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class ProxyNoKey(Base):
    KEY = None

    def test_health_reports_the_missing_key_and_how_to_fix_it(self):
        code, h, body = get(self.base + '/gw/health')
        d = json.loads(body)
        self.assertEqual(200, code)
        self.assertFalse(d['key'])
        self.assertIn('COGNIX_KEY', d['detail'])
        self.assertEqual('no-store', h.get('cache-control'))

    def test_a_call_without_a_key_says_so_instead_of_failing_upstream(self):
        code, h, body = post(self.base + '/gw/v1/messages',
                             {'model': serve.MODELS[0], 'max_tokens': 10})
        self.assertEqual(503, code)
        self.assertIn('COGNIX_KEY', json.loads(body)['error']['message'])


class Proxy(Base):
    KEY = FAKE

    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        cls.upthread = threading.Thread(target=cls.up.serve_forever, daemon=True)
        cls.upthread.start()
        cls._base = serve.BASE
        serve.BASE = 'http://127.0.0.1:%d' % cls.up.server_address[1]
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        serve.BASE = cls._base
        cls.up.shutdown()
        cls.up.server_close()
        cls.upthread.join(timeout=5)

    def setUp(self):
        Stub.seen = None
        Stub.reply = (200, {'ok': True})
        self.ok = {'model': serve.MODELS[0], 'max_tokens': 100,
                   'messages': [{'role': 'user', 'content': 'hi'}]}

    # ------------------------------------------------------------ what it is
    def test_it_is_not_an_open_relay(self):
        self.assertEqual(404, post(self.base + '/v1/messages', self.ok)[0])
        self.assertEqual(403, post(self.base + '/gw/v1/embeddings', self.ok)[0])
        # urllib may fold the dots before it sends; either answer is a refusal
        self.assertIn(post(self.base + '/gw/../v1/messages', self.ok)[0], (403, 404))
        self.assertIsNone(Stub.seen)

    def test_only_this_app_may_spend_the_key(self):
        """Any other page in this browser can POST to localhost. Browsers label
        a cross-site fetch for us, and that label cannot be forged from a page."""
        for hdr in ({'sec-fetch-site': 'cross-site'},
                    {'sec-fetch-site': 'same-site'},
                    {'origin': 'https://evil.example'},
                    {'origin': 'http://127.0.0.1:1'}):
            code, h, body = post(self.base + '/gw/v1/messages', self.ok, hdr)
            self.assertEqual(403, code, hdr)
        self.assertIsNone(Stub.seen)

    def test_the_app_itself_is_allowed(self):
        for hdr in ({'sec-fetch-site': 'same-origin'},
                    {'origin': 'http://localhost:%d' % self.port},
                    {'origin': 'http://127.0.0.1:%d' % self.port},
                    {}):                      # curl sends no label at all
            self.assertEqual(200, post(self.base + '/gw/v1/messages', self.ok, hdr)[0])

    # ---------------------------------------------------------- what it costs
    def test_the_body_has_to_be_json(self):
        self.assertEqual(415, post(self.base + '/gw/v1/messages', None, raw=b'{}',
                                   headers={'content-type': 'text/plain'})[0])
        self.assertEqual(400, post(self.base + '/gw/v1/messages', None, raw=b'not json')[0])
        self.assertEqual(400, post(self.base + '/gw/v1/messages', [1, 2, 3])[0])
        self.assertEqual(413, post(self.base + '/gw/v1/messages', None, raw=b'')[0])
        self.assertIsNone(Stub.seen)

    def test_a_body_bigger_than_the_cap_never_reaches_the_gateway(self):
        """Sent by hand: the point is that the refusal comes from the declared
        length, before half a megabyte has been read off the socket."""
        s = socket.create_connection(('127.0.0.1', self.port), timeout=10)
        data = b''
        try:
            s.sendall(('POST /gw/v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                       'Content-Type: application/json\r\n'
                       'Content-Length: %d\r\n\r\n' % (serve.MAX_BODY + 1)
                       ).encode('ascii'))
            s.shutdown(socket.SHUT_WR)       # nothing more coming from this side
            while b'\r\n' not in data:
                chunk = s.recv(400)
                if not chunk:
                    break
                data += chunk
        except OSError:
            pass                             # a reset after the reply is fine
        finally:
            s.close()
        self.assertIn('413', data.decode('ascii', 'replace').split('\r\n')[0])
        self.assertIsNone(Stub.seen)

    def test_only_the_two_models_this_app_uses(self):
        for m in ('claude-3-opus', '', None, 12, 'claude-opus-5-thinking-x'):
            code, h, body = post(self.base + '/gw/v1/messages', dict(self.ok, model=m))
            self.assertEqual(400, code, m)
            self.assertIn('model not allowed', json.loads(body)['error']['message'])
        self.assertIsNone(Stub.seen)

    # ------------------------------------------------- the two agents' names
    def test_the_app_asks_by_the_agents_name_and_the_proxy_translates_it(self):
        """The browser sends `cognix-mind-v1`. The gateway is asked for the
        model that name stands for, and the translation happens here — which is
        the whole mechanism: the vendor id exists on this side of the proxy and
        nowhere a page, a stored chat or a network tab can read it."""
        for said, real in (('cognix-mind-v1', serve.MODELS[0]),
                           ('cognix-apex-v2', serve.MODELS[1])):
            code, h, body = post(self.base + '/gw/v1/messages',
                                 dict(self.ok, model=said))
            self.assertEqual(200, code, said)
            self.assertEqual(real, json.loads(Stub.seen['body'])['model'])

    def test_a_vendor_name_still_works_because_older_chats_recorded_it(self):
        """Re-running a chat saved by an earlier version has to work, so the
        real names are accepted on the way in as well."""
        for real in serve.MODELS:
            code, h, body = post(self.base + '/gw/v1/messages',
                                 dict(self.ok, model=real))
            self.assertEqual(200, code, real)
            self.assertEqual(real, json.loads(Stub.seen['body'])['model'])

    def test_the_answer_comes_back_naming_the_agent_and_not_the_model(self):
        """An Anthropic reply quotes the model it came from. Relaying that
        verbatim would put the vendor name in the network tab of every call,
        which is exactly what the alias is for."""
        Stub.reply = (200, {'model': serve.MODELS[0], 'stop_reason': 'tool_use',
                            'content': [], 'usage': {'input_tokens': 7}})
        code, h, body = post(self.base + '/gw/v1/messages',
                             dict(self.ok, model='cognix-mind-v1'))
        self.assertEqual(200, code)
        self.assertNotIn(serve.MODELS[0].encode(), body)
        self.assertEqual('cognix-mind-v1', json.loads(body)['model'])
        self.assertEqual(7, json.loads(body)['usage']['input_tokens'])

    def test_an_upstream_error_naming_the_model_is_translated_too(self):
        Stub.reply = (400, {'error': {'message':
                            serve.MODELS[1] + ' is not available to you'}})
        code, h, body = post(self.base + '/gw/v1/messages', self.ok)
        self.assertEqual(400, code)
        self.assertNotIn(serve.MODELS[1].encode(), body)
        self.assertIn('cognix-apex-v2 is not available',
                      json.loads(body)['error']['message'])

    def test_health_names_the_agents_and_not_the_models(self):
        code, h, body = get(self.base + '/gw/health')
        d = json.loads(body)
        self.assertEqual(['cognix-mind-v1', 'cognix-apex-v2'], d['models'])
        for real in serve.MODELS:
            self.assertNotIn(real.encode(), body)

    def test_max_tokens_is_required_and_capped(self):
        for mt in (None, 0, -5, True, '900', 1.5):
            self.assertEqual(400, post(self.base + '/gw/v1/messages',
                                       dict(self.ok, max_tokens=mt))[0], mt)
        self.assertIsNone(Stub.seen)
        code, h, body = post(self.base + '/gw/v1/messages',
                             dict(self.ok, max_tokens=999999))
        self.assertEqual(200, code)
        self.assertEqual(serve.MAX_TOKENS, json.loads(Stub.seen['body'])['max_tokens'])

    def test_streaming_is_refused_rather_than_half_supported(self):
        code, h, body = post(self.base + '/gw/v1/messages', dict(self.ok, stream=True))
        self.assertEqual(400, code)
        self.assertIn('streaming', json.loads(body)['error']['message'])
        self.assertIsNone(Stub.seen)

    # ------------------------------------------------------------- the secret
    def test_the_key_goes_up_and_never_comes_back(self):
        code, h, body = post(self.base + '/gw/v1/messages', self.ok)
        self.assertEqual(200, code)
        self.assertEqual(FAKE, Stub.seen['headers']['x-api-key'])
        self.assertEqual('curl/8.5.0', Stub.seen['headers']['user-agent'])
        self.assertNotIn(FAKE.encode(), body)

    def test_an_upstream_error_quoting_the_key_is_redacted(self):
        Stub.reply = (401, {'error': {'message': 'bad key ' + FAKE}})
        code, h, body = post(self.base + '/gw/v1/messages', self.ok)
        self.assertEqual(401, code)
        self.assertNotIn(FAKE.encode(), body)
        self.assertIn(b'redacted', body)

    def test_health_never_shows_the_key(self):
        code, h, body = get(self.base + '/gw/health')
        d = json.loads(body)
        self.assertTrue(d['key'])              # a bool, not the string
        self.assertNotIn(FAKE.encode(), body)

    def test_mask_shows_the_length_and_the_tail_only(self):
        m = serve.mask(FAKE)
        self.assertNotIn(FAKE, m)
        self.assertIn(FAKE[-4:], m)
        self.assertEqual('not set', serve.mask(None))

    # ------------------------------------------------------------ upstream up
    def test_a_dead_gateway_is_a_502_with_a_readable_reason(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        dead = s.getsockname()[1]
        s.close()
        was, serve.BASE = serve.BASE, 'http://127.0.0.1:%d' % dead
        try:
            code, h, body = post(self.base + '/gw/v1/messages', self.ok)
        finally:
            serve.BASE = was
        self.assertEqual(502, code)
        self.assertIn('unreachable', json.loads(body)['error']['message'])

    def test_a_final_upstream_status_is_relayed_once(self):
        """A 400 from the gateway means the request was wrong; retrying it just
        spends time. Only 502/503/504 are worth a second attempt."""
        Stub.reply = (400, {'error': {'message': 'nope'}})
        calls = []
        real = Stub.do_POST

        def counted(self):
            calls.append(1)
            real(self)
        Stub.do_POST = counted
        try:
            self.assertEqual(400, post(self.base + '/gw/v1/messages', self.ok)[0])
        finally:
            Stub.do_POST = real
        self.assertEqual(1, len(calls))

    def test_nothing_that_looks_like_a_key_reaches_the_log(self):
        Stub.reply = (401, {'error': {'message': 'bad key ' + FAKE}})
        post(self.base + '/gw/v1/messages', self.ok)
        self.assertTrue(self.logs)
        for line in self.logs:
            self.assertNotIn(FAKE, line)


class Redaction(unittest.TestCase):
    """No socket needed: this is the last line of defence for the key, so it is
    worth testing on its own rather than only through a response body."""

    def test_a_key_is_replaced_wherever_it_sits(self):
        for s in (FAKE, 'oops ' + FAKE, FAKE + ' oops', '{"m":"' + FAKE + '"}'):
            out = serve.redact(s)
            self.assertNotIn(FAKE, out)
            self.assertIn('redacted', out)

    def test_ordinary_text_is_left_alone(self):
        self.assertEqual('GET /app/ 200', serve.redact('GET /app/ 200'))

    def test_it_accepts_whatever_it_is_handed(self):
        self.assertEqual('None', serve.redact(None))
        self.assertEqual('42', serve.redact(42))


class Denials(unittest.TestCase):
    """_denied is what stops SimpleHTTPRequestHandler serving this server's own
    source and its key file, which it would otherwise do happily."""

    def test_paths_that_are_not_content(self):
        for p in ('/serve.py', '/.env', '/.git/config', '/app/.env',
                  '/tests/test_server.py', '/x/__pycache__/y.pyc',
                  '/notes.log', '/id.pem', '/SERVE.PY', '/a/.hidden/b.js'):
            self.assertTrue(serve.Handler._denied(p), p)

    def test_paths_that_are(self):
        for p in ('/', '/app/', '/app/index.html', '/app/src/main.js',
                  '/app/styles.css', '/app/vendor/react.js',
                  '/app/assets/cognix-mark-64.png', '/app/index.html?v=2'):
            self.assertFalse(serve.Handler._denied(p), p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
