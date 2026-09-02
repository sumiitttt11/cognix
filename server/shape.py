"""Shape checks for anything that arrives from a browser.

sanitize.js does this on the way *out* of storage, because a map that has
been sitting in localStorage since an older version has to be survivable.
This is the other side: nothing reaches Postgres without passing through
here first, so a request cannot store text without a ceiling, a chat with
ten thousand messages, or a key that means something to a JavaScript engine.

Raise Bad(msg) and api.py turns it into a 400 with that message. The messages
are written to be read by the person who caused them.
"""
import json
import re

from . import config

UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                  r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
EMAIL = re.compile(r'^[^@\s,;<>"]{1,64}@[A-Za-z0-9.\-]{3,190}\.[A-Za-z]{2,24}$')
ID = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')          # client-side ids like map-3f2a
BADKEY = ('__proto__', 'prototype', 'constructor')
TABS = ('map', 'plan')
ROLES = ('user', 'admin')
STATUS = ('active', 'suspended')


class Bad(Exception):
    """A 400 with a sentence in it."""

    def __init__(self, msg, field=''):
        Exception.__init__(self, msg)
        self.msg = msg
        self.field = field


def s(v, n=config.MAX_TEXT, field='', required=False):
    """A single line of text, capped. Control characters go, because they are
    invisible in every interface that will later show this."""
    if v is None:
        v = ''
    if not isinstance(v, (str, int, float)):
        raise Bad('%s must be text.' % (field or 'That field'), field)
    out = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(v)).strip()
    if required and not out:
        raise Bad('%s cannot be empty.' % (field or 'That field'), field)
    return out[:n]


def para(v, n=8000, field=''):
    """Text that is allowed to have line breaks in it."""
    if v is None:
        return ''
    if not isinstance(v, (str, int, float)):
        raise Bad('%s must be text.' % (field or 'That field'), field)
    out = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(v))
    return out[:n]


def num(v, lo, hi, default=None, field=''):
    try:
        out = int(v)
    except (TypeError, ValueError):
        if default is None:
            raise Bad('%s must be a whole number.' % (field or 'That field'), field)
        return default
    return max(lo, min(hi, out))


def one_of(v, allowed, field='', default=None):
    got = str(v or '').strip().lower()
    if got in allowed:
        return got
    if default is not None:
        return default
    raise Bad('%s must be one of: %s.' % (field or 'That field', ', '.join(allowed)), field)


def email(v, field='email'):
    out = s(v, 254, field, required=True).lower()
    if not EMAIL.match(out):
        raise Bad('That does not look like an email address.', field)
    return out


def uuid(v, field='id'):
    out = str(v or '').strip()
    if not UUID.match(out):
        raise Bad('That is not a valid id.', field)
    return out


def local_id(v, field='id'):
    out = str(v or '').strip()
    if not ID.match(out):
        raise Bad('That is not a valid id.', field)
    return out


def _walk(v, depth, field):
    if depth <= 0:
        raise Bad('%s is nested too deeply.' % field, field)
    if isinstance(v, dict):
        out = {}
        for k, sub in list(v.items())[:600]:
            key = str(k)[:80]
            if not key or key in BADKEY:
                continue
            out[key] = _walk(sub, depth - 1, field)
        return out
    if isinstance(v, (list, tuple)):
        return [_walk(x, depth - 1, field) for x in list(v)[:2000]]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, str):
        return para(v, 20000, field)
    if isinstance(v, (int, float)):
        # NaN and Infinity are ordinary Python floats and invalid JSON, so they
        # would fail at json.dumps time with a much worse message than this.
        if isinstance(v, float) and (v != v or v in (float('inf'), float('-inf'))):
            return 0
        return v
    raise Bad('%s contains something that cannot be stored.' % field, field)


def blob(v, field='data', limit=config.MAX_JSON, depth=24):
    """A nested JSON value, cleaned and weighed.

    Keys that mean something to a JavaScript engine go here as well as in
    sanitize.js. The browser is what gets hurt by them, but this is the last
    place they can be stopped before they are stored and later handed to
    somebody's tab."""
    out = _walk(v, depth, field)
    try:
        raw = json.dumps(out, separators=(',', ':'))
    except (TypeError, ValueError):
        raise Bad('%s cannot be stored.' % field, field)
    if len(raw.encode('utf-8')) > limit:
        raise Bad('%s is too large (the limit is %d KB).' % (field, limit // 1024), field)
    return out


def message(m, field='messages'):
    """One turn of a conversation, in the shape the messages table stores."""
    if not isinstance(m, dict):
        raise Bad('Every message has to be an object.', field)
    text = m.get('text')
    if text is None:
        text = m.get('content')
    out = {
        'role': one_of(m.get('role'), ('user', 'assistant', 'system'),
                       field='role', default='assistant'),
        'kind': one_of(m.get('kind'), ('chat', 'map', 'plan', 'note', 'error'),
                       field='kind', default='chat'),
        'text': para(text, 20000, 'message text'),
        'ts': num(m.get('ts'), 0, 4102444800000, default=0),
    }
    meta = m.get('meta')
    if isinstance(meta, dict) and meta:
        out['meta'] = blob(meta, 'message meta', 8000, 8)
    return out


def snapshot(obj):
    """One whole chat as the browser holds it — what PUT /api/data/chats/<id>
    writes. `version` is the optimistic-concurrency token: the browser sends
    back the version it last saw, and a save against a stale one is refused
    rather than silently overwriting a newer tab's work."""
    if not isinstance(obj, dict):
        raise Bad('The request body has to be an object.')
    msgs = obj.get('messages')
    if msgs is None:
        msgs = []
    if not isinstance(msgs, list):
        raise Bad('messages has to be a list.', 'messages')
    if len(msgs) > config.MAX_MSGS:
        raise Bad('One chat can hold %d messages. That one has %d — start a new '
                  'chat and this one stays as it is.' % (config.MAX_MSGS, len(msgs)),
                  'messages')
    out = {
        'title': s(obj.get('title'), config.MAX_TEXT, 'title') or 'Untitled',
        'tab': one_of(obj.get('tab'), TABS, field='tab', default='map'),
        'model': s(obj.get('model'), 80, 'model'),
        'messages': [message(m) for m in msgs],
        'version': num(obj.get('version'), 0, 10 ** 9, default=0),
        # A save that never mentioned messages must not be read as 'delete them
        # all'. Only a caller that sent a list gets its messages rewritten.
        'has_messages': isinstance(obj.get('messages'), list),
    }
    if obj.get('map') is not None:
        out['map'] = blob(obj.get('map'), 'map')
    if obj.get('style') is not None:
        out['style'] = blob(obj.get('style'), 'style', 60000, 8)
    raw = str(obj.get('local_id') or obj.get('id') or '')
    if ID.match(raw):
        out['local_id'] = raw
    return out


BUNDLE_MAX = 200


def bundle(obj):
    """The localStorage backup a browser offers to upload the first time
    somebody signs in on a machine that already had maps on it.

    Capped at one request's worth, not at the account's ceiling. Truncating to
    MAX_CHATS here would make a backup that does not fit look like one that
    arrived in full — and persist.js deletes the local copy on being told
    everything came over. How many actually land is api._import's business."""
    chats = obj.get('chats') if isinstance(obj, dict) else obj
    if not isinstance(chats, list):
        raise Bad('That backup has no chats in it.', 'chats')
    return [snapshot(c) for c in chats[:BUNDLE_MAX]]


def yes(v, default=False):
    if isinstance(v, bool):
        return v
    got = str('' if v is None else v).strip().lower()
    if got in ('1', 'true', 'yes', 'on'):
        return True
    if got in ('0', 'false', 'no', 'off'):
        return False
    return default


def password(v, addr='', field='password'):
    """The rule lives in crypto.weak; this turns its answer into a 400."""
    from . import crypto
    raw = v if isinstance(v, str) else ''
    reason = crypto.weak(raw, addr)
    if reason:
        raise Bad(reason, field)
    return raw


# The gateway URL an administrator types in. An origin, optionally with a path
# prefix, and nothing else: no query, no fragment, no credentials, no space.
ORIGIN = re.compile(r'^https?://[A-Za-z0-9.\-]{1,190}(:\d{2,5})?'
                    r'(/[A-Za-z0-9._~\-]{1,40}){0,4}$')
# What an API key is made of. Not a check that it is *the* key — only that it
# could be one, and that it cannot carry a header break or a URL into a request.
KEYISH = re.compile(r'^[A-Za-z0-9._\-]{8,300}$')
# A vendor model id. Wider than KEYISH by a colon and a slash, because those are
# how the gateways in the wild spell a namespaced or versioned model —
# `vendor/model-name` and `model-name:latest` are both ordinary.
MODELID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/\-]{1,79}$')


def origin(v, field='base'):
    """Where the model calls go. '' is a real answer: it means 'stop using the
    stored one and go back to the environment'."""
    out = s(v, 200, field).rstrip('/')
    if not out:
        return ''
    if not out.lower().startswith(('http://', 'https://')):
        raise Bad('The gateway URL has to start with https:// .', field)
    if not ORIGIN.match(out):
        raise Bad('That is not a URL this can call. Give the origin — '
                  'https://api.example.com — with no query and no spaces.', field)
    return out


def gateway_key(v, field='key'):
    """The gateway's API key. '' clears it. Never logged, never echoed: what
    comes back out of the console is config.mask() of it.

    Length is a refusal here rather than the truncation every other field in
    this module does, and the difference matters: a title with its tail cut off
    is still a title, and a key with its tail cut off is an afternoon spent
    reading 'the gateway rejected our key'."""
    out = s(v, 400, field)
    if not out:
        return ''
    if len(out) < 8:
        raise Bad('That is too short to be an API key.', field)
    if len(out) > 300:
        raise Bad('That is longer than any key this can send. Check for '
                  'something pasted in twice.', field)
    if not KEYISH.match(out):
        raise Bad('An API key is letters, digits, dots, dashes and underscores. '
                  'That has something else in it — check for a stray space.', field)
    return out


def model_id(v, field='model'):
    """One vendor model id, as an administrator copies it out of their gateway's
    model list. '' is a real answer: it means 'go back to the id this build
    ships with for that agent'.

    Not a check that any gateway serves it — nothing here could know that, and
    gateway.check() is the thing that asks. This only says it can be put in a
    JSON body and a log line without carrying anything else in with it. Long is
    a refusal rather than a truncation for the same reason a key is: a model id
    with its tail cut off is a gateway saying no to a name nobody typed."""
    out = s(v, 200, field)
    if not out:
        return ''
    if len(out) > 80:
        raise Bad('That is longer than any model id this can send. Check for '
                  'something pasted in twice.', field)
    if not MODELID.match(out):
        raise Bad('A model id is letters, digits and . _ - : / — no spaces. '
                  'Copy it exactly as the gateway lists it.', field)
    return out


def gateway_models(v, field='models'):
    """Which vendor model each agent asks for, as the console sends it.

    Only the agents this build has, and only the ones actually named: a request
    that mentions one agent leaves the other one alone, for the same reason
    saving the gateway URL leaves the key alone. An agent name that is not one
    of ours is a refusal rather than a silent no-op — it would otherwise be a
    typo that looks exactly like a save that worked."""
    if not isinstance(v, dict):
        raise Bad('That has to be an object of agent name to model id.', field)
    for name in v:
        if name not in config.AGENTS:
            raise Bad('%s is not an agent this build has. They are: %s.'
                      % (s(name, 40) or 'That', ', '.join(config.AGENTS)), field)
    out = {}
    for name in config.AGENTS:
        if name in v:
            out[name] = model_id(v[name], name)
    if not out:
        raise Bad('Name at least one agent: %s.' % ', '.join(config.AGENTS), field)
    return out


def settings(obj):
    """An admin's change to app_settings. Only the keys that were sent come
    back, so one switch can be flipped without resending the rest."""
    if not isinstance(obj, dict):
        raise Bad('The request body has to be an object.')
    out = {}
    if 'signups_open' in obj:
        out['signups_open'] = yes(obj['signups_open'], True)
    if 'maintenance' in obj:
        out['maintenance'] = yes(obj['maintenance'], False)
    if 'announcement' in obj:
        out['announcement'] = para(obj['announcement'], 600, 'announcement')
    if 'default_token_cap' in obj:
        out['default_token_cap'] = num(obj['default_token_cap'], 0, 10 ** 9,
                                       default=config.TOKEN_CAP, field='default_token_cap')
    if 'allowed_models' in obj:
        raw = obj['allowed_models']
        if isinstance(raw, str):
            raw = raw.split(',')
        if not isinstance(raw, list):
            raise Bad('allowed_models has to be a list of model names.', 'allowed_models')
        # A set, kept in the order it was sent: the screen shows one row per
        # entry, so a name stored twice is a duplicate row an administrator can
        # tick once and untick once.
        keep = []
        for name in (s(m, 80, 'model') for m in raw[:20]):
            if name and name not in keep:
                keep.append(name)
        out['allowed_models'] = keep
    if not out:
        raise Bad('There is nothing to change in that request.')
    return out
