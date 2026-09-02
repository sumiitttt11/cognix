"""The shape of one API call, in and out.

serve.py owns the socket. Everything under /api/* is a function from a Req to
a Res and nothing else — no handler, no `self`, no writing to a stream halfway
through. That is what makes the routers testable without a port, and it is
why a bug in a route cannot leave a half-written response on the wire.

api.py and admin.py both build these, so they live below both.
"""
import json

from . import config, shape


class Req(object):
    __slots__ = ('method', 'path', 'parts', 'query', 'raw', 'head', 'jar',
                 'ip', 'secure', '_json')

    def __init__(self, method, path, query=None, raw=b'', head=None, jar=None,
                 ip='', secure=False):
        self.method = (method or 'GET').upper()
        self.path = path or '/'
        self.parts = [p for p in self.path.split('/') if p]
        self.query = query or {}
        self.raw = raw or b''
        self.head = head or {}
        self.jar = jar or {}
        self.ip = ip or ''
        self.secure = bool(secure)
        self._json = None

    def q(self, name, default=''):
        v = self.query.get(name)
        if isinstance(v, list):
            v = v[0] if v else None
        return default if v in (None, '') else v

    def h(self, name, default=''):
        return self.head.get(name.lower(), default)

    @property
    def unsafe(self):
        return self.method in ('POST', 'PUT', 'PATCH', 'DELETE')

    def json(self):
        """The body, parsed once. An empty body is an empty object, because
        several endpoints legitimately take nothing."""
        if self._json is None:
            if not self.raw.strip():
                self._json = {}
            else:
                if len(self.raw) > config.MAX_JSON:
                    raise shape.Bad('That request is too large.')
                try:
                    self._json = json.loads(self.raw.decode('utf-8'))
                except (ValueError, UnicodeDecodeError):
                    raise shape.Bad('That request body is not valid JSON.')
        return self._json

    def obj(self):
        got = self.json()
        if not isinstance(got, dict):
            raise shape.Bad('The request body has to be an object.')
        return got


class Res(object):
    __slots__ = ('code', 'obj', 'cookies', 'head')

    def __init__(self, code=200, obj=None, cookies=None, head=None):
        self.code = code
        self.obj = {} if obj is None else obj
        self.cookies = list(cookies or ())
        self.head = dict(head or {})


def ok(obj=None, cookies=None, head=None):
    return Res(200, {'ok': True} if obj is None else obj, cookies, head)


def fail(code, msg, field='', cookies=None):
    out = {'error': config.redact(msg)}
    if field:
        out['field'] = field
    return Res(code, out, cookies)


# PostgREST's way of saying the thing you asked for was never created:
# PGRST205 for a table, PGRST202 for a function, and 42P01 straight from
# Postgres when a statement inside one of ours names a missing relation.
NO_SCHEMA = ('PGRST205', 'PGRST202', '42P01')

SETUP = ('This deployment has an account system but no database yet. Run the '
         'four files in supabase/ in the Supabase SQL editor, in this order — '
         'schema.sql, functions.sql, policies.sql, seed.sql — then reload. '
         'supabase/README.md is the walkthrough.')


def _unmade(reply):
    """Is this 'the SQL has not been run' rather than a real refusal?

    Worth catching in one place rather than reading like a bug in whichever
    panel asked first. PostgREST's own wording — `Could not find the table
    'public.chats' in the schema cache` — is accurate and tells somebody
    deploying this nothing about what to do next, which is a shame on the one
    day they will ever see it."""
    b = reply.body if isinstance(reply.body, dict) else {}
    if str(b.get('code') or '') in NO_SCHEMA:
        return True
    said = ' '.join(str(b.get(k) or '') for k in ('message', 'msg', 'hint',
                                                  'details', 'error'))
    low = said.lower()
    return ('schema cache' in low
            or ('does not exist' in low and 'relation' in low))


def upstream(reply, fallback='Supabase refused that.', keep=(400, 401, 403, 404, 409, 422, 429)):
    """A failed Supabase reply, turned into something a person can read.

    Upstream statuses are not passed through blindly: a 401 from PostgREST
    means our token expired, which for the caller is a 401, but a 500 from
    Supabase is not the browser's fault and should not read like it is. Nor is
    a missing table a 404 about the row that was asked for — it is this
    instance not being finished, so it answers 503 and says which files."""
    if reply.status == 0:
        return fail(504, 'Could not reach Supabase. Try again in a moment.')
    if _unmade(reply):
        return fail(503, SETUP)
    code = reply.status if reply.status in keep else 502
    return fail(code, reply.msg(fallback))
