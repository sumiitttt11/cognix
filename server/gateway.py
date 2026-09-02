"""Where the model calls go, with which key, and asking for which model — read
from the database so an administrator can change all three without a code edit
or a redeploy.

Four things make this small rather than clever:

  * It is five columns on `app_settings`, not a table of its own. That row is
    already read once a minute for the sign-up switch, already cached, and is
    already the only table a browser with no session may select from — which
    matters, because a *guest* model call carries no token, so the read that
    resolves the key has to work as `anon`.
  * The key is stored sealed. `app_settings` being anon-readable means the
    ciphertext is readable too, so confidentiality comes from
    crypto.seal_secret() and the secret it uses lives in the environment. A
    dump of this database is not a working API key.
  * The model ids are *not* sealed, because they are not credentials — they are
    two words a gateway publishes in its own model list. They are stored plainly
    for the same reason the key is not: this read runs as `anon` on a guest call,
    and a value that has to be decrypted on that path is a value that fails on
    it.
  * The environment still wins when the database says nothing. `COGNIX_BASE`
    and `COGNIX_KEY` keep working exactly as before, config.MODELS stays the
    pair this build asks for until somebody says otherwise, local mode never
    touches this module, and a deployment whose SQL has not been migrated yet
    falls back instead of failing.

Import direction: this sits below api.py, so it may not import it. config,
crypto, limits, shape and supa only.
"""
import time

from . import config, crypto, limits, shape, supa

# The columns. Named once here so admin.py can strip exactly these out of the
# settings row it hands the console, and nothing drifts.
COLS = ('gateway_base', 'gateway_sealed', 'gateway_hint', 'gateway_updated_at',
        'gateway_models')

# app_settings is one row and it is read on nearly every request, so it is
# cached in limits.SETTINGS under one key. api.settings_now() delegates here so
# there is one fetch, one cache entry and one clear() between the two of them.
KEY = 'one'

# PostgREST's two ways of saying 'no such column': one on a write, one from
# Postgres itself on a read. Both mean the same thing here — schema.sql has not
# been run since these columns were added — and both are worth a sentence that
# says so rather than a raw upstream error.
NO_COLUMN = ('PGRST204', '42703')

# What serve.py has in COGNIX_BASE / COGNIX_KEY. A callable rather than a copy,
# because those are module globals over there and the test suite repoints them
# per test — reading them late is what makes that keep working. Nothing else in
# this package knows the environment's gateway, on purpose: there is one place
# the key is read from and this is how the rest of the code borrows it.
_env = None


def bind_env(fn):
    global _env
    _env = fn


def env_pair():
    if _env is None:
        return '', ''
    try:
        base, key = _env()
    except Exception:                                            # noqa: BLE001
        return '', ''
    return str(base or ''), str(key or '')


def row():
    """The whole app_settings row, cached, read with the anon key.

    `{}` is a real answer and is cached as one: no row, no table yet, or a
    select that failed. Every caller treats a missing field as 'not configured',
    which is the same thing. Local mode short-circuits before the cache — there
    is no database, so there is nothing stored and the environment is the only
    answer there can be.
    """
    if not supa.ready():
        return {}
    got = limits.SETTINGS.get(KEY)
    if got is not None:
        return got
    out = {}
    rep = supa.select('app_settings', None, select='*', id='eq.1', limit=1)
    if rep.ok:
        out = supa.one(rep) or {}
    return limits.SETTINGS.put(KEY, out)


def forget():
    limits.SETTINGS.clear()


def peek():
    """What is already known, without asking Supabase. /readyz answers from
    configuration alone on purpose — a probe that called the database would take
    the deployment down the first time the database was slow — so it uses this
    and accepts 'don't know yet'."""
    if not supa.ready():
        return {}
    return limits.SETTINGS.get(KEY) or {}


def _base_of(st):
    return str(st.get('gateway_base') or '').strip().rstrip('/')


def _key_of(st):
    sealed = str(st.get('gateway_sealed') or '')
    if not sealed:
        return ''
    return crypto.open_secret(config.SESSION_SECRET, sealed) or ''


def override(st=None):
    """{'base':…, 'key':…, 'source':…} for whatever the database has to say.

    Only the fields that are actually set come back, so serve.py can write
    `stored or environment` per field: a console that sets the URL and leaves
    the key alone gets the stored URL and the environment's key.

    `source` is for the health endpoint and the console, and it is a word
    ('panel', 'env', 'mixed'), never a value.
    """
    st = row() if st is None else st
    base, key = _base_of(st), _key_of(st)
    out = {}
    if base:
        out['base'] = base
    if key:
        out['key'] = key
    if base and key:
        out['source'] = 'panel'
    elif base or key:
        out['source'] = 'mixed'
    return out


def unreadable(st=None):
    """True when a key is stored and this process cannot open it — the one
    failure worth naming, because it looks like 'the key is set' everywhere else
    and behaves like 'there is no key'. It means COGNIX_SESSION_SECRET was not
    set, so each restart invented a new one."""
    st = row() if st is None else st
    return bool(str(st.get('gateway_sealed') or '') and not _key_of(st))


def effective(st=None):
    """(base, key, source) — what a model call should actually use.

    Per field, not per set: an administrator who fills in the URL and leaves the
    key alone gets the stored URL and the environment's key, and the source says
    'mixed' so both screens can tell the truth about it.
    """
    st = row() if st is None else st
    env_base, env_key = env_pair()
    got = override(st)
    return (got.get('base') or env_base,
            got.get('key') or env_key,
            got.get('source') or 'env')


# ------------------------------------------------------------- which model
# The agents are the product; the vendor ids behind them are a default. A
# gateway that serves the same model under another name — or a deployment moving
# to a gateway that serves a different model altogether — is a row on
# app_settings, not a code change. config.ALIAS is what is asked for until this
# column says otherwise, per agent.


def stored_models(st=None):
    """Just the overrides, as the row holds them.

    `{}` for a column that was never written, for a database that has not got it
    yet, and for a value that is not an object — all three mean the same thing
    here, which is 'this deployment asks for what it ships with'. Unknown agent
    names are dropped rather than kept: shape.gateway_models refuses them on the
    way in, and a row written by a newer build should not put a name this one
    does not understand into the table a request is resolved against.
    """
    st = row() if st is None else st
    got = st.get('gateway_models')
    if not isinstance(got, dict):
        return {}
    out = {}
    for name in config.AGENTS:
        val = str(got.get(name) or '').strip()
        if val:
            out[name] = val
    return out


def models(st=None):
    """agent name -> the model id a call for that agent should actually ask for.

    The built-in pair, with the stored overrides on top of it. Every agent is in
    here always: an override is a replacement for one entry, never a shorter
    table, so nothing downstream has to carry an 'or the default' of its own.
    """
    st = row() if st is None else st
    out = dict(config.ALIAS)
    out.update(stored_models(st))
    return out


def public(st=None):
    """model id -> the agent's name, for everything on the way back out.

    Both the effective ids and the two built-in ones are in here, because a chat
    saved before an agent was repointed recorded the old id and an answer to a
    re-run of it still has to come back as the agent's name. The stored side is
    applied second, so where the two disagree the current configuration is what
    a body is rewritten to.
    """
    st = row() if st is None else st
    out = dict(config.PUBLIC)
    for name in config.AGENTS:                  # in order: a degenerate row that
        got = models(st).get(name)              # points both agents at one id
        if got:                                 # resolves to the later agent
            out[got] = name
    return out


def allowed(st=None):
    """Every model id the proxy may send upstream.

    The effective ones, plus the pair this build ships with — those stay allowed
    however the mapping is changed, because a chat saved by a version that
    recorded vendor ids has to be re-runnable. An id that *used* to be mapped and
    is not any more is not in here, which is correct: the deployment has been
    pointed somewhere else since.
    """
    st = row() if st is None else st
    out = list(config.MODELS)
    for name in config.AGENTS:
        got = models(st).get(name)
        if got and got not in out:
            out.append(got)
    return tuple(out)


def agent_of(name, st=None):
    """Which agent an incoming `model` belongs to — its own name if it is one,
    the agent it is mapped to if it is an id, '' if neither."""
    st = row() if st is None else st
    got = str(name or '')
    if got in config.ALIAS:
        return got
    return public(st).get(got, '')


def missing_column(rep):
    """A reply that failed because this database has never heard of the gateway
    columns, as opposed to one that was refused."""
    body = rep.body if isinstance(getattr(rep, 'body', None), dict) else {}
    if str(body.get('code') or '') in NO_COLUMN:
        return True
    said = (str(body.get('message') or '') + ' '
            + str(body.get('hint') or '')).lower()
    return 'gateway_' in said and 'column' in said


def status(st=None):
    """What the console draws. Never the key — `hint` is the masked form the
    save stored, which is all of it that ever comes back out.

    `models` is the one place in this product where a vendor id goes out over
    HTTP, and it goes to an administrator who is being asked to type one. The
    console is the operator's screen — three gates in front of it and a role read
    from the caller's own profiles row — and an operator who cannot see which id
    an agent asks for cannot change it. It is the same carve-out the usage rows
    are: nothing a *user* can reach is in here.
    """
    st = row() if st is None else st
    base, key = _base_of(st), _key_of(st)
    env_base, env_key = env_pair()
    use_base, _use_key, source = effective(st)
    kept, eff = stored_models(st), models(st)
    return {
        'base': base,
        'key_set': bool(key),
        'hint': str(st.get('gateway_hint') or ''),
        'updated_at': st.get('gateway_updated_at') or None,
        'stored': bool(base or str(st.get('gateway_sealed') or '') or kept),
        'unreadable': unreadable(st),
        'sealable': bool(config.SESSION_SECRET_GIVEN),
        'source': source,
        # what the server would use this second, so the screen is never a
        # description of a setting that is not the one in force
        'in_use': use_base,
        'in_use_key': bool(_use_key),
        'env_base': env_base,
        'env_key': bool(env_key),
        # one entry per agent, always, in the order the product lists them
        'models': [{'name': name, 'id': eff[name],
                    'built_in': config.ALIAS[name],
                    'source': 'panel' if name in kept else 'built-in'}
                   for name in config.AGENTS],
        'models_stored': bool(kept),
    }


def fields(obj):
    """An admin's change, validated, as the columns to write. Raises shape.Bad.

    `key: ''` is 'forget the stored key and go back to the environment', which
    is a different request from not sending the field at all, and both have to
    be expressible. A model id reads the same way, and for the same reason it is
    merged into what is already stored rather than replacing it: a request that
    names one agent has not said anything about the other.
    """
    got = obj if isinstance(obj, dict) else {}
    out = {}
    if 'base' in got:
        out['gateway_base'] = shape.origin(got.get('base'), 'base')
    if 'key' in got:
        key = shape.gateway_key(got.get('key'))
        if key:
            if not config.SESSION_SECRET_GIVEN:
                raise shape.Bad(
                    'Set COGNIX_SESSION_SECRET before storing a key here — '
                    'without it the key cannot be read back after a restart.',
                    'key')
            out['gateway_sealed'] = crypto.seal_secret(config.SESSION_SECRET, key)
            out['gateway_hint'] = config.mask(key)
        else:
            out['gateway_sealed'] = ''
            out['gateway_hint'] = ''
    if 'models' in got:
        want = shape.gateway_models(got.get('models'))
        keep = stored_models()
        for name, mid in want.items():
            if mid and mid != config.ALIAS[name]:
                keep[name] = mid
            else:
                # the built-in id typed in by hand is not an override, it is the
                # default spelled out; storing it would pin the agent to an id a
                # later build has moved on from
                keep.pop(name, None)
        out['gateway_models'] = keep
    if not out:
        raise shape.Bad('There is nothing to change in that request.')
    out['gateway_updated_at'] = _iso()
    return out


def _iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def check(base, key, timeout=8, want=None):
    """Ask the gateway who it is, with the key that would be used. GET /v1/models
    spends nothing, so the console can offer this before anything is saved.

    Returns (ok, sentence, ids) — the ids being the gateway's own model list,
    which is the whole reason this is worth calling before a save: the console
    offers them as the thing to point an agent at, so nobody has to type one
    from memory. Everything else is deliberately not a passthrough of the
    upstream body: that body can contain the key back.

    `want` is the agent → id map to judge the answer against, so an
    administrator can check a mapping they have typed and not yet saved.
    """
    from . import hclient                       # local: hclient imports config
    origin = str(base or '').strip().rstrip('/')
    if not origin:
        return False, 'There is no gateway URL to check.', []
    if not key:
        return False, 'There is no API key to check with.', []
    head = {'x-api-key': key, 'authorization': 'Bearer ' + key,
            'accept': 'application/json',
            # the gateway is behind Cloudflare, which 403s urllib's own name
            'user-agent': 'curl/8.5.0'}
    t0 = time.time()
    rep = hclient.get(origin + '/v1/models', headers=head, timeout=timeout,
                      tries=1)
    ms = int((time.time() - t0) * 1000)
    if rep.status == 0:
        return (False,
                'Could not reach %s — %s' % (origin, rep.err or 'no answer'), [])
    if rep.status in (401, 403):
        return False, 'Reached it in %d ms and the key was refused (%d).' % (
            ms, rep.status), []
    if rep.status == 404:
        return False, ('Reached it in %d ms, but it has no /v1/models — check '
                       'the URL is the API origin.' % ms), []
    if 200 <= rep.status < 300:
        got = _model_names(rep)
        return True, _served(ms, got, want), got
    return False, 'Reached it in %d ms and it answered %d.' % (ms, rep.status), []


def _served(ms, names, want=None):
    """The sentence for a gateway that answered.

    A count on its own is not enough to move a deployment onto a new gateway:
    the app asks for two particular models by id, and a gateway that serves
    neither of them passes this check perfectly and then fails every generation.
    So the ones that are not in its list are named — by the agents' names, which
    is the only name this product has for them and the only one that may go to a
    browser. The ids themselves are not in this sentence, in either branch: the
    reply carries the gateway's list separately, and the sentence is the part
    that gets read out, pasted into a ticket and screenshotted.
    """
    if not names:
        return 'Answered in %d ms.' % ms
    eff = models() if want is None else want
    missing = [n for n in config.AGENTS
               if eff.get(n) and eff[n] not in names]
    if not missing:
        return ('Answered in %d ms with %d models, and both agents are among '
                'them.' % (ms, len(names)))
    return ('Answered in %d ms with %d models, but %s not in its list — either '
            'this gateway has another name for that, or that agent will fail '
            'here. Point it at one of the %d below.'
            % (ms, len(names),
               ' and '.join(missing) + (' are' if len(missing) > 1 else ' is'),
               len(names)))


def _model_names(rep):
    body = rep.body if isinstance(rep.body, dict) else {}
    got = body.get('data') if isinstance(body.get('data'), list) else []
    out = []
    for one in got:
        name = (one or {}).get('id') if isinstance(one, dict) else None
        if name:
            out.append(str(name)[:60])
    return out
