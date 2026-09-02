"""Every knob this app has, read once, in one place.

Order of precedence, highest first:

  1. a real environment variable  (COGNIX_X, then NODERELS_X, then X)
  2. a line in .env next to serve.py
  3. the default written here

Two modes fall out of one question — is SUPABASE_URL set?

  local mode   no accounts, no database. Maps live in the browser. This is
               the prototype behaviour, kept because it is a genuinely nice
               way to run the thing on your own laptop.
  cloud mode   Supabase Auth issues the session, Postgres holds the chats,
               and /gw/* refuses anyone who is not signed in.

Nothing in here reaches the network or the filesystem beyond .env, so it can
be imported by tests that do not want a server.
"""
import base64
import json
import os
import re
import secrets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dotenv(path):
    """KEY=value, one per line, # for comments. No quoting rules beyond
    stripping one matching pair, because one program reads this file."""
    out = {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError:
        pass
    return out


FILE_ENV = _dotenv(os.path.join(ROOT, '.env'))
PLAIN_OK = ('PORT', 'HOST', 'K_SERVICE')     # names the platform owns, unprefixed


def env(name, default=None):
    """COGNIX_* is the current name; NODERELS_* still works so an existing
    shell or launch entry survives the rename. PORT with no prefix is read
    too, because that is the one Cloud Run sets."""
    for k in ('COGNIX_' + name, 'NODERELS_' + name):
        if os.environ.get(k):
            return os.environ[k]
    if name in PLAIN_OK and os.environ.get(name):
        return os.environ[name]
    for k in ('COGNIX_' + name, 'NODERELS_' + name, name):
        if FILE_ENV.get(k):
            return FILE_ENV[k]
    return default


def as_int(v, fallback):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return fallback


def as_bool(v, fallback=False):
    if v is None:
        return fallback
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def flag(name, fallback=False):
    return as_bool(env(name), fallback)


# ----------------------------------------------------------------- Supabase
SUPABASE_URL = (env('SUPABASE_URL', '') or '').rstrip('/')
SUPABASE_ANON_KEY = env('SUPABASE_ANON_KEY', '') or ''
SUPABASE_SERVICE_KEY = env('SUPABASE_SERVICE_KEY', '') or ''
# where GoTrue should send people back to after a magic link / recovery mail
PUBLIC_URL = (env('PUBLIC_URL', '') or '').rstrip('/')

CLOUD = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
AUTH_URL = SUPABASE_URL + '/auth/v1'
REST_URL = SUPABASE_URL + '/rest/v1'

# ------------------------------------------------------------------ sessions
SESSION_COOKIE = 'cx_session'
CSRF_COOKIE = 'cx_csrf'
CSRF_HEADER = 'x-cx-csrf'
SESSION_DAYS = as_int(env('SESSION_DAYS', '30'), 30)
# Signing key for the session cookie. Generated per process if absent, which
# is fine on a laptop and wrong on Cloud Run: two instances would reject each
# other's cookies, so main() refuses to start without one out there.
SESSION_SECRET = env('SESSION_SECRET') or secrets.token_urlsafe(48)
SESSION_SECRET_GIVEN = bool(env('SESSION_SECRET'))

# ------------------------------------------------------------------- limits
MAX_BODY = 512 * 1024                 # anything larger is refused unread
MAX_JSON = 256 * 1024                 # /api/* bodies
MAX_TEXT = 240                        # one box of text; mirrors sanitize.js
MAX_CHATS = as_int(env('MAX_CHATS', '400'), 400)
MAX_MSGS = as_int(env('MAX_MSGS', '400'), 400)
TOKEN_CAP = as_int(env('TOKEN_CAP', '400000'), 400000)   # per user per month
LOGIN_TRIES = as_int(env('LOGIN_TRIES', '10'), 10)       # per IP per window
LOGIN_WINDOW = as_int(env('LOGIN_WINDOW', '900'), 900)
GW_PER_MIN = as_int(env('GW_PER_MIN', '12'), 12)         # model calls a minute
PASSWORD_MIN = 10

# --------------------------------------------------------------- the agents
# Two agents, one vendor model each, and the mapping between them lives here
# because it is the one fact this app deliberately does not tell anybody.
#
# The browser asks for `cognix-mind-v1`; serve.py's proxy edge turns that into
# the vendor id on the way up and turns the vendor id back into the agent name
# on the way down. PUBLIC is what every other module reaches for: anything that
# names a model in something a page can read goes through it first, so a model
# id that arrives from an older stored chat, or from a settings row written
# before the rename, still comes back out as the name of the agent.
#
# AGENTS is the product. MODELS is the pair of vendor ids this build ships with,
# and that pair is a *default*: server/gateway.py lets an administrator point
# either agent at whatever id their gateway happens to serve, because a gateway
# that names the same model differently should not be a code change. So these
# three are the built-in answer, and anything deciding what to send upstream
# asks gateway.models() rather than reading them directly.
AGENTS = ('cognix-mind-v1', 'cognix-apex-v2')
MODELS = ('claude-opus-4-8-thinking', 'claude-opus-5-thinking')
ALIAS = dict(zip(AGENTS, MODELS))               # agent name -> vendor id
PUBLIC = dict(zip(MODELS, AGENTS))              # ...and back again


def public_model(name, table=None):
    """The agent's name for a model id. Anything unrecognised — an id from a
    newer version, or a word an administrator typed into the model list — comes
    back as it went in: this hides the names it knows, it does not filter.

    `table` is an id→name mapping to use instead of the built-in one, which is
    how a deployment that has repointed an agent still publicises its own id.
    """
    got = str(name or '')
    return (PUBLIC if table is None else table).get(got, got)


def public_models(names, table=None):
    """A list of models as the agents' names, each one once.

    The dedupe is the reason this exists rather than a comprehension at each
    call site: a row that holds both a vendor id and the agent's name — which is
    what a project gets when it was saved through an older build of the console —
    publicises to the same word twice, and a list of allowed models is a set. So
    the name would appear twice on the settings screen, twice in /api/config, and
    twice again in the row the next Save writes."""
    out = []
    for name in names or ():
        got = public_model(name, table)
        if got and got not in out:
            out.append(got)
    return out


# --------------------------------------------------------------- deployment
TRUST_PROXY = flag('TRUST_PROXY', bool(env('K_SERVICE')))   # Cloud Run sets it
ALLOW_OPEN = flag('ALLOW_OPEN')       # let an unauthenticated app face a network
LOG_JSON = flag('LOG_JSON', bool(env('K_SERVICE')))
SIGNUPS = flag('SIGNUPS', True)       # can be turned off from the admin panel
ADMIN_EMAILS = tuple(e.strip().lower() for e in (env('ADMIN_EMAILS', '') or '').split(',') if e.strip())

# ------------------------------------------------------------- guest access
# A visitor with no account can use the app straight away. Their chats stay in
# their own browser — nothing is written to the database without a person to
# own the row — and they get a small number of model calls before the app asks
# them to make an account.
#
# This is the one path in the product that spends money with nobody signed in,
# so be clear about what the ceiling is worth. GUEST_CHATS is what the page
# shows and enforces, and a page can be lied to. GUEST_CALLS is the real one:
# it is counted in a cookie this server signs, so it survives a restart and
# cannot be edited, but a visitor who clears cookies starts over. GUEST_PER_IP
# is the backstop for exactly that, and it is in-memory per instance. So the
# guest allowance is a courtesy bound on casual use, not a security boundary —
# COGNIX_GUEST=0 turns it off and puts the sign-in page back in front.
GUEST = flag('GUEST', True)
GUEST_COOKIE = 'cx_guest'
GUEST_CHATS = as_int(env('GUEST_CHATS', '3'), 3)      # chats the app allows
GUEST_CALLS = as_int(env('GUEST_CALLS', '9'), 9)      # model calls per visitor
GUEST_PER_IP = as_int(env('GUEST_PER_IP', '40'), 40)  # ...per address per hour
GUEST_DAYS = as_int(env('GUEST_DAYS', '7'), 7)        # how long that count lasts

SECRET_RE = re.compile(r'(sk-[A-Za-z0-9_\-]{6,}'
                       r'|sb_(?:secret|publishable)_[A-Za-z0-9_\-]{6,}'
                       r'|eyJ[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,})')


def redact(s):
    """Everything on its way to a caller or a log goes through this. Covers
    gateway keys, Supabase's newer `sb_secret_…` / `sb_publishable_…` keys, and
    anything JWT-shaped, which is what the older Supabase keys are."""
    return SECRET_RE.sub('…redacted', s if isinstance(s, str) else str(s))


def key_role(key):
    """Which of the two Supabase keys this is — 'anon', 'service' or ''.

    Supabase has issued two shapes. The newer keys say which they are in the
    prefix; the older ones are JWTs with the role in the payload, which is
    signed but not encrypted, so reading it needs no secret and proves nothing
    on its own. This is only used to catch the two ways of pasting a key into
    the wrong line, below — nothing is authorised on the strength of it."""
    key = (key or '').strip()
    if key.startswith('sb_secret_'):
        return 'service'
    if key.startswith('sb_publishable_'):
        return 'anon'
    parts = key.split('.')
    if len(parts) != 3 or not key.startswith('eyJ'):
        return ''
    seg = parts[1]
    try:
        raw = base64.urlsafe_b64decode(seg + '=' * (-len(seg) % 4))
        role = str((json.loads(raw.decode('utf-8', 'replace')) or {}).get('role') or '')
    except (ValueError, TypeError, AttributeError):
        return ''
    if role == 'service_role':
        return 'service'
    return 'anon' if role == 'anon' else ''


def mask(key):
    if not key:
        return 'not set'
    return 'set · %d chars · …%s' % (len(key), key[-4:])


def mode():
    return 'cloud' if CLOUD else 'local'


def problems():
    """Refusals and warnings, worked out before the socket is open. Returns
    (fatal, warnings) — main() prints both and exits on any fatal."""
    fatal, warn = [], []
    if CLOUD and not SUPABASE_SERVICE_KEY:
        # The console itself runs on the caller's own token, so somebody whose
        # profiles row already says admin can open it. What needs the secret key
        # is GoTrue's admin API — invite, delete, confirm-by-hand — and the
        # one-time promotion of the first address in ADMIN_EMAILS, which is why
        # a project with no administrator yet has no way in without it.
        warn.append('SUPABASE_SERVICE_KEY is not set: in the admin console, '
                    'invite, delete and confirm will fail, and nobody can be '
                    'made the first administrator. Everything else works.')
    if CLOUD and not PUBLIC_URL:
        warn.append('PUBLIC_URL is not set: password-reset mail will point at '
                    'Supabase\'s default site URL instead of this app.')
    if SUPABASE_URL and not SUPABASE_ANON_KEY:
        fatal.append('SUPABASE_URL is set but SUPABASE_ANON_KEY is not. '
                     'Set both, or neither to run in local mode.')
    # The two keys are next to each other in the dashboard and one line apart
    # in .env, and swapping them is silent: the app would keep working while
    # every request that is supposed to be bounded by a policy ran as the owner
    # of the database instead. So it is a refusal to start, not a warning.
    if SUPABASE_ANON_KEY and key_role(SUPABASE_ANON_KEY) == 'service':
        fatal.append('SUPABASE_ANON_KEY holds the service key. That key '
                     'bypasses every policy in supabase/policies.sql, so this '
                     'is a refusal rather than a warning. Put the publishable '
                     '(anon) key there and the secret one in '
                     'SUPABASE_SERVICE_KEY.')
    if SUPABASE_SERVICE_KEY and key_role(SUPABASE_SERVICE_KEY) == 'anon':
        warn.append('SUPABASE_SERVICE_KEY holds the publishable (anon) key. '
                    'The admin console will refuse every request, because '
                    'GoTrue\'s admin endpoints need the secret one.')
    if SUPABASE_URL and not SUPABASE_URL.startswith('https://'):
        warn.append('SUPABASE_URL is not https. Tokens would cross the '
                    'network in the clear.')
    return fatal, warn

