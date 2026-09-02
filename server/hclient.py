"""One place where this process talks to something else over HTTP.

urllib, deliberately: no dependency to install, no wheel to build into the
container, nothing to keep patched. What it adds over urlopen is the part
that is easy to get wrong — a timeout on every call, retries only on the
statuses that mean "not now", and a return value that never raises, so a
handler cannot be knocked over by somebody else's outage.
"""
import json
import time
import urllib.error
import urllib.request

from . import config

RETRY_ON = (429, 502, 503, 504)
TIMEOUT = 25
TRIES = 2


class Reply(object):
    """status is 0 when nothing came back at all; err then says why."""

    __slots__ = ('status', 'body', 'err', 'headers')

    def __init__(self, status=0, body=None, err=None, headers=None):
        self.status = status
        self.body = body if body is not None else {}
        self.err = err
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status < 300

    def msg(self, fallback='Upstream refused the request.'):
        """The human-readable half of an error, whatever shape it arrived in.
        Supabase uses msg / message / error_description / error."""
        b = self.body
        if isinstance(b, dict):
            for k in ('msg', 'message', 'error_description', 'error_message', 'hint'):
                v = b.get(k)
                if isinstance(v, str) and v.strip():
                    return config.redact(v.strip())
            e = b.get('error')
            if isinstance(e, str) and e.strip():
                return config.redact(e.strip())
            if isinstance(e, dict) and isinstance(e.get('message'), str):
                return config.redact(e['message'])
        if isinstance(b, str) and b.strip():
            return config.redact(b.strip()[:300])
        return config.redact(self.err or fallback)


def _decode(raw, ctype):
    if not raw:
        return {}
    if 'json' in (ctype or ''):
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return {}
    return raw.decode('utf-8', 'replace')[:2000]


def call(method, url, headers=None, body=None, timeout=TIMEOUT, tries=TRIES):
    data = None
    head = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        head.setdefault('content-type', 'application/json')
    head.setdefault('accept', 'application/json')
    # Cloudflare answers Python-urllib's default User-Agent with a 403 in front
    # of at least one host this talks to, so say something ordinary.
    head.setdefault('user-agent', 'cognix/1.0')
    last = None
    for attempt in range(1, max(1, tries) + 1):
        req = urllib.request.Request(url, data=data, method=method.upper())
        for k, v in head.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return Reply(r.status, _decode(raw, r.headers.get('content-type')),
                             None, dict(r.headers))
        except urllib.error.HTTPError as e:
            raw = e.read()
            rep = Reply(e.code, _decode(raw, e.headers.get('content-type')),
                        None, dict(e.headers))
            if e.code in RETRY_ON and attempt < tries:
                last = rep
                time.sleep(0.4 * attempt)
                continue
            return rep
        except Exception as e:                                   # noqa: BLE001
            last = Reply(0, {}, str(e) or e.__class__.__name__)
            if attempt < tries:
                time.sleep(0.4 * attempt)
                continue
            return last
    return last or Reply(0, {}, 'no attempt was made')


def get(url, **kw):
    return call('GET', url, **kw)


def post(url, **kw):
    return call('POST', url, **kw)


def patch(url, **kw):
    return call('PATCH', url, **kw)


def delete(url, **kw):
    return call('DELETE', url, **kw)
