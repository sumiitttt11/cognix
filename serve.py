#!/usr/bin/env python3
"""
Cognix — one process, three jobs.

  /app/*     the React app, and nothing else: one strict content-security
             policy for every static path, because every page in it loads
             its scripts and styles from this origin by URL. / redirects
             there, so the bare address opens the app.
  /api/*     accounts, chats, usage and the admin console. Every one of these
             is a function from a wire.Req to a wire.Res inside the server
             package; this file is the only thing in the project that touches
             a socket.
  /gw/*      the model gateway. This process holds the API key, so it is the
             only place that can spend money.

Two reasons the gateway is proxied rather than called from the browser:

  1. The gateway answers the CORS preflight (OPTIONS) with 403, so any
     request carrying x-api-key is blocked before it is sent. Same-origin
     /gw/* has no preflight at all.
  2. The API key stays in this process. It is never shipped to the browser.

Because it can spend money it is not a transparent relay: three paths, two
models, one method, a max_tokens cap, a check that the call came from this app
rather than from another page in the same browser — and, once Supabase is
connected, a signed-in person who is under their monthly ceiling.

    python serve.py                      # http://localhost:8778/app/
    COGNIX_KEY=sk-... python serve.py     # key from the environment
    COGNIX_PORT=8779 python serve.py      # somewhere else
    PORT=8080 COGNIX_HOST=0.0.0.0 ...     # what Cloud Run sets for you

Every setting is read in server/config.py. There is deliberately no default
for the API key or the session secret: a secret written into source is a
secret that leaks.
"""
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import api, config, gateway, sessions, supa, wire    # noqa: E402

# One reader for every knob, so this file and the routers cannot disagree
# about what the environment said: COGNIX_X, then NODERELS_X, then .env.
env = config.env
_int = config.as_int

# Cloud Run hands over the port and expects every interface. A laptop gets
# loopback and the port this app has always used.
MANAGED = bool(env('K_SERVICE'))
HOST = env('HOST', '0.0.0.0' if MANAGED else '127.0.0.1')
PORT = _int(env('PORT', '8080' if MANAGED else '8778'), 8778)
BASE = (env('BASE', 'https://api.justwoker.icu') or '').rstrip('/')
KEY = env('KEY')                      # no default, on purpose

# In cloud mode an administrator can set both of those from the console instead,
# and then this pair is the fallback rather than the answer. server/gateway.py
# holds the stored half and borrows this half through a callable, so the lookup
# happens per call and nothing has to keep a copy in step.
gateway.bind_env(lambda: (BASE, KEY))


def gw():
    """(base, key, source) for this call. `source` is a word — 'panel', 'env' or
    'mixed' — and is the only thing about the key that is ever printed."""
    return gateway.effective()


# only these paths are proxied, so this is not an open relay
ALLOW = ('/v1/messages', '/v1/chat/completions', '/v1/models')
# ...and only these models, so nothing can quietly pick an expensive one. This
# pair is the built-in answer: gateway.allowed() adds whatever an administrator
# has pointed an agent at, and nothing else is ever sent upstream.
MODELS = config.MODELS
# What the browser is allowed to call them. The app sends these names and the
# proxy edge turns them into the real ones, so a page — or anybody reading the
# network tab — never learns which vendor model is behind either agent. The real
# names still work: they are what older stored chats recorded.
#
# Both tables are resolved per request through server/gateway.py rather than read
# from here, because the console can repoint an agent between two calls. These
# are the defaults that module falls back to, and the reason they live in
# server/config.py is that the /api/* side needs them too — a settings row can
# name a model, and that row is read by a page that has not signed in.
ALIAS = config.ALIAS
# and the names the browser may be told, for the same reason
PUBLIC = config.PUBLIC
AGENTS = config.AGENTS
MAX_BODY = config.MAX_BODY            # refused on the declared length, unread
MAX_TOKENS = 4096                     # the app asks for 1500 and 2200
TIMEOUT = 240
TRIES = 2                             # the browser retries too; 2x3 is plenty
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
HSTS = 'max-age=31536000; includeSubDomains'

# never served, however the path is spelled
DENY_NAME = ('.git', '.claude', '__pycache__', 'serve.py', 'tests', 'server',
             'supabase', 'deploy', 'tools', 'dockerfile')
DENY_EXT = ('.py', '.pyc', '.pyo', '.env', '.log', '.pem', '.key', '.ini',
            '.sql', '.yaml', '.yml', '.toml', '.md')


def redact(s):
    """Anything on its way back to a caller or into a log goes through this.
    The pattern lives in server/config.py because the /api/* side needs it
    too: it covers gateway keys and anything JWT-shaped, which is the shape of
    every token Supabase issues."""
    return config.redact(s)


def publicise(raw, said=None):
    """The vendor names, on the way back out.

    An answer quotes the model it came from — in `model` on a success, and often
    in the sentence on a failure — and that is the one thing the alias above
    exists to keep on this side of the proxy. A substitution rather than a parse,
    for the same reason redact() is one: it has to hold for every shape of body,
    including the ones that are not JSON at all.

    Longest id first, because one id can be a prefix of another — a gateway's
    `…-thinking` and `…-thinking-v2` are exactly that — and replacing the short
    one first would leave the tail of the long one behind in the body."""
    for real, name in sorted((said or PUBLIC).items(), key=lambda kv: -len(kv[0])):
        raw = raw.replace(real.encode(), name.encode())
    return raw


def mask(key):
    if not key:
        return 'not set'
    return 'set · %d chars · …%s' % (len(key), key[-4:])


def is_api(path):
    return path == '/api' or path.startswith('/api/')


# Every static path is the React app, and it needs nothing but this origin:
# no inline script, no inline style, no third-party anything. So there is one
# policy rather than one per path, and a page that grows an inline <script>
# breaks loudly here instead of quietly widening this.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "img-src 'self'; font-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

HELP_NO_KEY = (
    'No API key. Put COGNIX_KEY=sk-... in a .env file next to serve.py, '
    'or export COGNIX_KEY before starting it, then restart the server. '
    'With accounts turned on, an administrator can set it at '
    '/app/admin/#/gateway instead, and that needs no restart.'
)

# The URL has a default, so this is nearly unreachable — it takes COGNIX_BASE
# set to something empty. Worth a sentence anyway: without it that would be a
# request to `/v1/messages` on nothing, which fails as 'unreachable' and reads
# like a network fault rather than a setting.
HELP_NO_BASE = (
    'No gateway URL. Set COGNIX_BASE in the .env file next to serve.py and '
    'restart, or set it at /app/admin/#/gateway, which needs no restart.'
)


def usage_of(raw):
    """The token counts out of a model reply, in whichever of the two shapes
    the gateway answers with. Unreadable means None, and the meter records the
    call at zero rather than inventing a number for somebody's bill."""
    try:
        got = json.loads((raw or b'').decode('utf-8'))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    u = got.get('usage') if isinstance(got, dict) else None
    return u if isinstance(u, dict) else None


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'cognix'
    sys_version = ''

    # ---------------------------------------------------------- logging
    def log_message(self, fmt, *a):
        line = redact(fmt % a)
        if config.LOG_JSON:
            # Cloud Logging reads one JSON object per line and shows the
            # severity. A bare text line there is a log nobody can filter.
            sys.stderr.write(json.dumps({
                'severity': 'INFO', 'message': line, 'ip': self._client_ip(),
                'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }) + '\n')
        else:
            sys.stderr.write('%s  %-15s %s\n' % (
                time.strftime('%H:%M:%S'), self.address_string(), line))

    def log_error(self, fmt, *a):        # 404s are not worth a scary label
        self.log_message(fmt, *a)

    def log_request(self, code='-', size='-'):
        """A proxied POST already writes one line with its status, duration and
        size, so skip the generic one rather than logging the call twice."""
        if self.command == 'POST' and (self.path or '').startswith('/gw/'):
            return
        SimpleHTTPRequestHandler.log_request(self, code, size)

    # ----------------------------------------------------- this request
    def _hdr(self, name, default=''):
        """Tolerant on purpose: end_headers also runs for a malformed request
        line, which never produced a header block to read."""
        got = getattr(self, 'headers', None)
        return (got.get(name) if got else None) or default

    def _secure(self):
        """Is the browser's half of this connection https? Behind Cloud Run the
        hop into this process is plain http, so the answer has to come from the
        proxy's header — and only where we have been told to believe it."""
        if not config.TRUST_PROXY:
            return False
        said = self._hdr('x-forwarded-proto').split(',')[0].strip().lower()
        return said == 'https'

    def _client_ip(self):
        """Who a rate limit is counted against. Straight off the socket unless
        there is a proxy in front, because a header anybody can send is not an
        identity — it is a way to spend somebody else's allowance."""
        if config.TRUST_PROXY:
            fwd = self._hdr('x-forwarded-for')
            if fwd:
                return fwd.split(',')[0].strip()[:64]
        try:
            return self.client_address[0]
        except (AttributeError, IndexError):
            return ''

    # ---------------------------------------------------------- headers
    def end_headers(self):
        p = (getattr(self, 'path', '') or '/').split('?')[0]
        self.send_header('x-content-type-options', 'nosniff')
        self.send_header('referrer-policy', 'same-origin')
        self.send_header('x-frame-options', 'DENY')
        self.send_header('cross-origin-opener-policy', 'same-origin')
        self.send_header('permissions-policy',
                         'geolocation=(), camera=(), microphone=(), usb=()')
        if self._secure():
            self.send_header('strict-transport-security', HSTS)

        # A session cookie that was rotated while answering a request which
        # ends in a static file or a redirect has nowhere else to travel.
        rotated = getattr(self, '_cookies', None)
        if rotated:
            for line in rotated:
                self.send_header('set-cookie', line)
            self._cookies = []
        if is_api(p) or p.startswith('/gw/') or p in ('/healthz', '/readyz'):
            self.send_header('cache-control', 'no-store')
            if is_api(p):
                self.send_header('vary', 'cookie')
        else:
            self.send_header('content-security-policy', CSP)
            self.send_header('cache-control', 'no-cache')
        SimpleHTTPRequestHandler.end_headers(self)

    def _json(self, code, obj, cookies=(), head=None):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        for line in cookies or ():
            self.send_header('set-cookie', line)
        for k, v in (head or {}).items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header('connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _page(self, code, title, sentence):
        """The one refusal a person can walk into with the address bar. A JSON
        body would arrive as a download prompt; this is a page. Every word of
        it is a literal — nothing from the request is echoed back into it.

        It borrows the app's own sheet and the crash screen's card, so the one
        page in the product a real person is refused by does not arrive looking
        like a different product. The link is same-origin, which is all
        `style-src 'self'` allows and all this needs."""
        body = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,'
                'initial-scale=1"><title>%s · Cognix</title>'
                '<link rel="stylesheet" href="/app/styles.css">'
                '<div class="crash"><div class="crashcard"><h1>%s</h1>'
                '<p>%s</p><p><a class="crashmore" href="/app/">'
                'Back to Cognix</a></p></div></div></html>'
                % (title, title, sentence)).encode('utf-8')
        self.send_response(code)
        self.send_header('content-type', 'text/html; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _redirect(self, where):
        self.send_response(303)
        self.send_header('location', where)
        self.send_header('content-length', '0')
        self.end_headers()

    def _to_app(self):
        """The bare address. There is one application here, so / is not a page
        of its own — it is the app. 302 rather than 301 so a browser that
        followed it once is not stuck with it if this ever changes."""
        self.send_response(302)
        self.send_header('location', '/app/')
        self.send_header('content-length', '0')
        self.end_headers()

    # ---------------------------------------------------------- static
    @staticmethod
    def _denied(path):
        """The server's own source, its key file, the SQL and anything hidden
        are not content. SimpleHTTPRequestHandler will serve all of it."""
        parts = [p for p in path.split('?')[0].split('/') if p]
        for seg in parts:
            low = seg.lower()
            if seg.startswith('.') or low in DENY_NAME:
                return True
        return bool(parts) and parts[-1].lower().endswith(DENY_EXT)

    def list_directory(self, path):
        """No listings. Without this, /app/src/ is an index of the source and
        every module name in the app is public."""
        self.send_error(404, 'not found')
        return None

    # ----------------------------------------------------------- /api/*
    def _req(self, raw=b''):
        """One wire.Req. Everything the routers are allowed to know about this
        connection is in here, and nothing else reaches them."""
        split = urlsplit(self.path or '/')
        heads = getattr(self, 'headers', None)
        return wire.Req(
            self.command, split.path,
            query=parse_qs(split.query, keep_blank_values=True), raw=raw,
            head={k.lower(): v for k, v in (heads.items() if heads else ())},
            jar=sessions.cookies(self._hdr('cookie')),
            ip=self._client_ip(), secure=self._secure())

    def _api(self):
        """Read the body, hand it to the router, write back what it returns.
        The router never sees this socket, so a bug in a route cannot leave
        half a response on the wire."""
        if self._hdr('transfer-encoding'):
            return self._short(411, 'Send a Content-Length. This server does '
                                    'not read chunked bodies.')
        n = _int(self._hdr('content-length'), 0)
        if n > MAX_BODY:
            return self._short(413, 'That request is too large.')
        raw = self._read_body(n) if n > 0 else b''
        if len(raw) != n:
            return self._short(400, 'The body was shorter than its length.')
        try:
            res = api.handle(self._req(raw))
        except Exception as e:                                  # noqa: BLE001
            self.log_message('%s %s -> 500  %s: %s', self.command,
                             (self.path or '').split('?')[0],
                             e.__class__.__name__, e)
            res = wire.Res(500, {'error': 'Something went wrong in here, not '
                                          'in your browser. Try again.'})
        return self._json(res.code, res.obj, res.cookies, res.head)

    def _short(self, code, sentence):
        """A refusal made before the body was read. The connection closes with
        it: on a keep-alive connection the next request would otherwise be
        parsed starting somewhere inside the body nobody read."""
        self.close_connection = True
        self.log_message('%s %s -> %d  %s', self.command,
                         (self.path or '').split('?')[0], code, sentence)
        return self._json(code, {'error': sentence})

    # -------------------------------------------------------- the verbs
    def do_GET(self):
        p = (self.path or '/').split('?')[0]
        if is_api(p):
            return self._api()
        if p == '/gw/health':
            return self._json(200, self._health())
        if p == '/healthz':
            return self._json(200, {'ok': True, 'mode': config.mode()})
        if p == '/readyz':
            return self._json(*self._ready())
        if p in ('', '/', '/index.html'):
            return self._to_app()
        if self._denied(p):
            return self._json(404, {'error': {'message': 'not found'}})
        if p.startswith('/app/admin') and not self._admin_gate(p):
            return None
        SimpleHTTPRequestHandler.do_GET(self)

    def do_HEAD(self):
        p = (self.path or '/').split('?')[0]
        if is_api(p):
            return self._api()
        if p in ('/gw/health', '/healthz'):
            return self._json(200, {'ok': True})
        if p == '/readyz':
            return self._json(*self._ready())
        if p in ('', '/', '/index.html'):
            return self._to_app()
        if self._denied(p):
            return self._json(404, {'error': {'message': 'not found'}})
        if p.startswith('/app/admin') and not self._admin_gate(p):
            return None
        SimpleHTTPRequestHandler.do_HEAD(self)

    def do_POST(self):
        p = (self.path or '/').split('?')[0]
        if is_api(p):
            return self._api()
        if p.startswith('/gw/'):
            return self._proxy(p)
        return self._refuse(404, 'not a proxy path')

    def _elsewhere(self):
        """PUT, PATCH and DELETE exist for /api/* and nowhere else."""
        if is_api((self.path or '/').split('?')[0]):
            return self._api()
        return self._short(405, 'That method is not used here.')

    do_PUT = do_PATCH = do_DELETE = _elsewhere

    # -------------------------------------------------- health and gates
    def _health(self):
        """/gw/health. Booleans and names only — never a key, never a token.

        Any page may GET this, so the models it names are the public ones: the
        app asks for 'cognix-mind-v1' and that is all this says back. It is the
        agents' own names rather than a translation of the ids in force, because
        an administrator can point an agent anywhere and this endpoint has no
        business either failing or leaking when they have. `source` is which of
        the two configurations answered — the console or the environment — which
        is worth knowing when a key has just been changed and something still is
        not working."""
        base, key, source = gw()
        return {'ok': True, 'key': bool(key), 'base': base,
                'models': list(AGENTS),
                'port': self.server.server_address[1],
                'mode': config.mode(), 'cloud': supa.ready(), 'source': source,
                'detail': None if key else HELP_NO_KEY}

    def _ready(self):
        """Readiness, for whatever decides whether to send traffic here. It
        answers from configuration alone: a probe that called Supabase would
        take the deployment down with it the first time Supabase was slow.

        Which is why the stored gateway is only peeked at here, never fetched.
        'A key is configured' is allowed to be a moment out of date; a readiness
        probe that can block is not."""
        fatal, _warn = boot_problems(HOST)
        stored = gateway.override(gateway.peek())
        return (503 if fatal else 200,
                {'ok': not fatal, 'mode': config.mode(), 'cloud': supa.ready(),
                 'key': bool(KEY or stored.get('key')), 'problems': fatal})

    def _admin_gate(self, p):
        """True when the console may be served.

        This is the third check on the role and the least important of the
        three: api/admin.py refuses every call a non-admin makes, and the
        policies in Postgres refuse the rows underneath it. It is here so the
        page does not open at all for somebody with no business in it."""
        if not supa.ready():
            return True                # local mode: the panel explains itself
        sess, fresh = api.who(self._req())
        if fresh:
            # the token was refreshed to answer this, so the browser has to be
            # handed the new cookie whatever the answer turns out to be
            self._cookies = sessions.seal(sess, self._secure())
        if not sess:
            self._redirect('/app/auth/?next=' + quote(p, safe='/'))
            return False
        role = (api.profile_of(sess) or {}).get('role') or ''
        if role == 'admin' or (sess.get('e') or '').lower() in config.ADMIN_EMAILS:
            return True
        self.log_message('%s %s -> 403  not an administrator', self.command, p)
        self._page(403, 'Not your page', 'That is the administrator console, '
                                        'and this account is not an administrator.')
        return False

    # ------------------------------------------------------- the gateway
    def _read_body(self, n):
        buf, left = [], n
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            buf.append(chunk)
            left -= len(chunk)
        return b''.join(buf)

    def _own_hosts(self):
        """Every name this deployment answers to. The Host header is in here
        because behind Cloud Run it is the only thing that knows the public
        name. It is not a way in: a browser fills Host and Origin from the same
        address bar, so they agree for us and disagree for everybody else."""
        port = self.server.server_address[1]     # not PORT: tests bind port 0
        out = {'%s:%d' % (HOST, port), 'localhost:%d' % port,
               '127.0.0.1:%d' % port, '[::1]:%d' % port}
        host = self._hdr('host').lower()
        if host:
            out.add(host)
            out.add(host.split(':')[0])
        if config.PUBLIC_URL:
            out.add(config.PUBLIC_URL.split('//')[-1].split('/')[0].lower())
        return out

    def _from_this_app(self):
        """Any page in this browser can POST to this origin. Browsers label the
        request for us: a fetch from another site arrives as cross-site, and
        ours arrives as same-origin. Tools like curl send neither header, which
        is why an absent label is allowed — it cannot be forged from a page."""
        site = self._hdr('sec-fetch-site').lower()
        if site and site not in ('same-origin', 'none'):
            return False
        origin = self._hdr('origin')
        if not origin:
            return True
        return origin.split('//')[-1].split('/')[0].lower() in self._own_hosts()

    def _task(self, obj, st=None):
        """What the usage row is called. The app labels its own calls; the
        model is the fallback, since the two it uses do one job each. Either
        name resolves — _vet has already rewritten the public one by the time
        this runs, but a call that never went through it still lands here."""
        said = self._hdr('x-cx-task').strip().lower()[:24]
        if said:
            return said
        got = str((obj or {}).get('model') or '')
        return 'map' if gateway.agent_of(got, st) == AGENTS[0] else 'plan'

    def _vet(self, raw, upstream, st=None):
        """Shape and cost, checked here because the key lives here."""
        if upstream == '/v1/models':
            return raw, None, {}
        try:
            obj = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None, 'body is not JSON', {}
        if not isinstance(obj, dict):
            return None, 'body must be a JSON object', {}
        # The app asks for an agent by the name it shows: this is where that
        # becomes a vendor model, and it is the last place the public name
        # exists. Which vendor model is a question for gateway.models() rather
        # than for the built-in table, because an administrator may have pointed
        # the agent at whatever id their gateway happens to serve. A real name is
        # still accepted rather than translated — it is what chats saved by an
        # earlier version recorded, and re-running one of those must not fail —
        # but nothing in the browser is told one.
        asked = str(obj.get('model') or '')
        eff = gateway.models(st)
        if asked in eff:
            obj['model'] = eff[asked]
        if obj.get('model') not in gateway.allowed(st):
            return None, 'model not allowed: %s' % asked[:60], {}
        mt = obj.get('max_tokens')
        if not isinstance(mt, int) or isinstance(mt, bool) or mt <= 0:
            return None, 'max_tokens must be a positive integer', {}
        if mt > MAX_TOKENS:
            obj['max_tokens'] = MAX_TOKENS       # cap rather than refuse
        if obj.get('stream'):
            return None, 'streaming is not proxied', {}
        return json.dumps(obj).encode('utf-8'), None, obj

    def _upstream(self, upstream, body, base='', key=''):
        """One POST to the gateway, with the pair the caller resolved. The pair
        is passed in rather than read from here, so the whole of one request is
        answered by the configuration that was in force when it arrived — an
        administrator saving a new key mid-call cannot half-apply to it."""
        base = (base or BASE).rstrip('/')
        req = urllib.request.Request(base + upstream, data=body, method='POST')
        req.add_header('content-type', 'application/json')
        req.add_header('x-api-key', key)
        req.add_header('authorization', 'Bearer ' + key)
        req.add_header('anthropic-version', '2023-06-01')
        # Cloudflare in front of the gateway answers Python-urllib's default
        # User-Agent with 403 "error code: 1010", so send a plain client UA.
        req.add_header('user-agent', 'curl/8.5.0')
        req.add_header('accept', '*/*')
        last = None
        for attempt in range(1, TRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return r.read(), r.status, None
            except urllib.error.HTTPError as e:
                out, code = e.read(), e.code
                # only the ones that mean "not now"; a 400 or a 402 is final
                if code in (502, 503, 504) and attempt < TRIES:
                    last = (out, code)
                    time.sleep(0.6 * attempt)
                    continue
                return out, code, None
            except Exception as e:                              # noqa: BLE001
                last = (None, str(e) or e.__class__.__name__)
                if attempt < TRIES:
                    time.sleep(0.6 * attempt)
                    continue
                return None, 0, str(e) or e.__class__.__name__
        if last and last[0] is not None:
            return last[0], last[1], None
        return None, 0, 'upstream unreachable'

    def _refuse(self, code, msg):
        """Every refusal on the path that spends money is worth one line: this
        is the process holding the key, so "why did nothing happen" has to be
        answerable from the log. The body is the gateway's own error shape,
        which is what the app's model client already knows how to read.

        The connection closes as well. Most of these fire before the body has
        been read, and on a keep-alive connection the next request would then
        be parsed starting somewhere inside that unread body."""
        self.close_connection = True
        self.log_message('POST %s -> %d  %s',
                         (self.path or '').split('?')[0], code, msg)
        return self._json(code, {'error': {'message': msg}})

    def _proxy(self, p):
        t0 = time.time()
        upstream = p[3:]
        if upstream not in ALLOW:
            return self._refuse(403, 'path not proxied: ' + upstream)
        if not self._from_this_app():
            return self._refuse(403, 'this proxy only answers the Cognix app '
                                     'on this origin')
        # In cloud mode a model call needs a person, an account in good
        # standing, a hand that is not hammering the button, and room under the
        # monthly ceiling. In local mode there is nobody to ask and this is a
        # no-op, which is why the prototype still runs with no database.
        sess, refusal, cookies = api.gate(self._req())
        cookies = list(cookies or ())
        if refusal is not None and refusal.cookies:
            # A refusal carries cookies of its own when it has decided something
            # about the browser's state — dropping a session that has run out,
            # say — and that matters here as much as it does on /api/*.
            cookies += list(refusal.cookies)
        if cookies:
            self._cookies = cookies
        if refusal is not None:
            self.close_connection = True
            self.log_message('POST %s -> %d  refused before the gateway',
                             p, refusal.code)
            return self._json(refusal.code, refusal.obj, head=refusal.head)
        # Resolved once, here, and carried through the rest of this request: the
        # console can change any of these between two calls, and a single call
        # has to be answered entirely by one configuration or the other. That is
        # one row — the URL, the key and which vendor model each agent asks for
        # all live on it — so it is read once and threaded down rather than
        # looked up again by every step that needs a piece of it.
        st = gateway.row()
        base, key, _source = gateway.effective(st)
        if not key:
            return self._refuse(503, HELP_NO_KEY)
        if not base:
            return self._refuse(503, HELP_NO_BASE)
        ctype = self._hdr('content-type').split(';')[0].strip().lower()
        if ctype != 'application/json':
            return self._refuse(415, 'content-type must be application/json')
        n = _int(self._hdr('content-length'), 0)
        if n <= 0 or n > MAX_BODY:
            return self._refuse(413, 'bad body length')
        raw = self._read_body(n)
        if len(raw) != n:
            return self._refuse(400, 'body shorter than content-length')
        body, why, obj = self._vet(raw, upstream, st)
        if why:
            return self._refuse(400, why)
        out, code, err = self._upstream(upstream, body, base, key)
        ms = int((time.time() - t0) * 1000)
        model = str(obj.get('model') or '')
        if err:
            api.record(sess, self._task(obj, st), model, None, ms=ms, ok=False,
                       note='gateway unreachable')
            self.log_message('POST %s -> unreachable in %dms: %s', p, ms, err)
            return self._json(502, {'error': {'message':
                                    'gateway unreachable: ' + redact(err)}})
        # an upstream error body is the one thing that might quote the key back
        if code >= 400:
            out = redact((out or b'').decode('utf-8', 'replace')).encode('utf-8')
        # ...and any body at all is where the vendor name would come back
        out = publicise(out or b'', gateway.public(st))
        api.record(sess, self._task(obj, st), model,
                   usage_of(out) if code < 400 else None, ms=ms, ok=code < 400,
                   note='' if code < 400 else 'upstream %d' % code)
        self.log_message('POST %s -> %d  %dms  %d bytes', p, code, ms, len(out))
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)


# ------------------------------------------------------------------- serve
def boot_problems(host):
    """What is wrong before the socket is open: (fatal, warnings). main() prints
    both and refuses to start on any fatal, and /readyz answers 503 while one
    stands. The two decided here rather than in config.py both depend on which
    interface this process is about to answer on."""
    fatal, warn = config.problems()
    public = host not in LOOPBACK
    if public and KEY and not supa.ready() and not config.ALLOW_OPEN:
        fatal.append(
            'This instance holds an API key, answers on %s, and has no accounts '
            '(SUPABASE_URL is not set) — so anybody who can reach it can spend '
            'the key. Set SUPABASE_URL and SUPABASE_ANON_KEY, or bind to '
            '127.0.0.1, or set COGNIX_ALLOW_OPEN=1 if that is what you meant.'
            % host)
    if public and supa.ready() and not config.SESSION_SECRET_GIVEN:
        fatal.append(
            'SESSION_SECRET is not set. One was generated for this process, '
            'which means a restart signs everybody out and two instances reject '
            'each other\'s cookies. Make one with: python -c '
            '"import secrets; print(secrets.token_urlsafe(48))"')
    return fatal, warn


def make_server(port=None, host=None):
    """Separate from main() so a test can ask for port 0 and get a real server
    without a banner, a blocked thread or a signal handler."""
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.daemon_threads = True
    return ThreadingHTTPServer((HOST if host is None else host,
                                PORT if port is None else port),
                               partial(Handler, directory=ROOT))


def on_term(srv):
    """Cloud Run sends SIGTERM and then waits. shutdown() has to be called from
    another thread: it waits for serve_forever to return, and serve_forever is
    what the signal just interrupted."""
    def stop(_sig, _frame):
        sys.stderr.write('SIGTERM: finishing what is open, then stopping.\n')
        threading.Thread(target=srv.shutdown, daemon=True).start()
    try:
        signal.signal(signal.SIGTERM, stop)
    except (AttributeError, ValueError, OSError):
        pass                  # not every platform has it, and it is not fatal


def db_line():
    """The startup banner's one sentence about the database.

    Worth a request: the difference between 'the tables are there' and 'the SQL
    has not been run yet' is invisible until somebody signs up, and then it is a
    broken page rather than a message. supa.probe keeps it to one short call."""
    rep = supa.probe()
    if rep.ok:
        return '  database  ready'
    if rep.status == 404:
        return ('  database  no tables yet — run supabase/schema.sql, then '
                'functions.sql, policies.sql, seed.sql')
    if rep.status in (401, 403):
        return ('  database  refused the key (%d) — check COGNIX_SUPABASE_ANON_KEY'
                % rep.status)
    if not rep.status:
        return '  database  unreachable — %s' % rep.msg('no answer')
    return '  database  %d — %s' % (rep.status, rep.msg('refused'))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    port = _int(argv[0], PORT) if argv else PORT
    fatal, warn = boot_problems(HOST)
    for line in warn:
        sys.stderr.write('  ! %s\n' % line)
    if fatal:
        for line in fatal:
            sys.stderr.write('  x %s\n' % line)
        sys.stderr.write('\nRefusing to start.\n')
        return 2
    try:
        srv = make_server(port)
    except OSError as e:
        if getattr(e, 'errno', None) in (48, 98, 10048):
            sys.stderr.write(
                'Port %d is already in use — another copy may still be running.\n'
                'Start this one elsewhere:  COGNIX_PORT=%d python serve.py\n'
                % (port, port + 1))
        else:
            sys.stderr.write('Could not start on %s:%d — %s\n' % (HOST, port, e))
        return 1

    shown = 'localhost' if HOST in ('127.0.0.1', '0.0.0.0', '::', '::1') else HOST
    print('Cognix      http://%s:%d/app/' % (shown, port))
    if supa.ready():
        print('  mode      cloud · accounts and chats live in Supabase')
        print('  supabase  %s' % config.SUPABASE_URL)
        print(db_line())
        if not config.SUPABASE_SERVICE_KEY:
            # Not a closed door: an account whose profiles row already says
            # admin opens the console on its own token. What is missing is
            # GoTrue's admin API — invite, delete, confirm — and the one-time
            # promotion of the first address in ADMIN_EMAILS.
            print('  admin     http://%s:%d/app/admin/  (read-only — invite, '
                  'delete and confirm need COGNIX_SUPABASE_SERVICE_KEY)'
                  % (shown, port))
        else:
            print('  admin     http://%s:%d/app/admin/' % (shown, port))
    else:
        print('  mode      local · no accounts, maps stay in the browser')
        print('            (set SUPABASE_URL and SUPABASE_ANON_KEY for accounts)')
    # What this process would use for the next model call. In cloud mode that
    # can be what an administrator saved rather than what the environment says,
    # and the difference is exactly the thing somebody reading this banner after
    # a restart wants to know — so the source is printed next to it.
    base, key, source = gw()
    whence = {'panel': ' (from the admin console)',
              'mixed': ' (part admin console, part environment)'}.get(source, '')
    print('  gateway   %s%s' % (base or '(not set)', whence))
    # The agents by their own names, and — only where the console has repointed
    # one — the id it now asks for. That arrow is the whole reason this line is
    # here rather than a constant: an operator restarting the process after a
    # remap wants to see it, and the built-in ids are the pair the alias exists
    # to keep off every screen a user can reach, so they are not printed.
    _kept = gateway.stored_models()
    print('  agents    %s' % ', '.join(
        n + ' -> ' + _kept[n] if n in _kept else n for n in AGENTS))
    print('  api key   %s' % mask(key))
    if gateway.unreadable():
        print('  ! a key is stored in the database and this process cannot read')
        print('    it — COGNIX_SESSION_SECRET has changed or was never set.')
    if not key:
        print('')
        print('  ' + HELP_NO_KEY)
    if HOST not in LOOPBACK and key and not supa.ready():
        print('')
        print('  ! bound to %s with no accounts, and COGNIX_ALLOW_OPEN is set:' % HOST)
        print('    anybody who can reach this port can spend the API key.')
    print('')
    print('  Ctrl-C to stop.')
    on_term(srv)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('')
    finally:
        srv.shutdown()
        srv.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
