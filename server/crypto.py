"""Signing, comparing and generating. No third-party crypto: hmac and
hashlib are in the standard library and this is all a cookie needs.

The session cookie is signed, not encrypted. What it holds is a Supabase
access token, which is a bearer token for the same user it belongs to — so
encrypting it would protect against nothing that reading it does not already
imply. Signing is what stops someone editing `"role":"admin"` into it.

One thing here does hide its contents: seal_secret() / open_secret(), for the
gateway key an administrator types into the console. That is stored in a row a
signed-out browser is allowed to read, so signing it would not be enough.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time


def b64(raw):
    """base64url with no padding, so it is cookie-safe."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def unb64(s):
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign(secret, msg):
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    if isinstance(msg, str):
        msg = msg.encode('utf-8')
    return b64(hmac.new(secret, msg, hashlib.sha256).digest())


def same(a, b):
    """Constant time, and tolerant of the types a header actually arrives as."""
    return hmac.compare_digest(str(a or '').encode('utf-8'),
                               str(b or '').encode('utf-8'))


def token(n=32):
    return secrets.token_urlsafe(n)


def seal(secret, obj):
    """obj -> 'v1.<payload>.<mac>'. Compact because it has to fit a cookie."""
    body = b64(json.dumps(obj, separators=(',', ':')))
    return 'v1.' + body + '.' + sign(secret, body)


def unseal(secret, blob):
    """Returns the object, or None for anything that is not exactly right:
    wrong shape, wrong version, wrong signature, unreadable payload."""
    parts = str(blob or '').split('.')
    if len(parts) != 3 or parts[0] != 'v1':
        return None
    body, mac = parts[1], parts[2]
    if not same(sign(secret, body), mac):
        return None
    try:
        out = json.loads(unb64(body).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    return out if isinstance(out, dict) else None


BOX = b'cognix-box-v2'


def _box_keys(secret):
    """Two keys from one, so the thing that encrypts is never the thing that
    authenticates. Both are derived, so the session secret itself is never used
    as a stream key."""
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    return (hmac.new(secret, b'cognix-box-enc', hashlib.sha256).digest(),
            hmac.new(secret, b'cognix-box-mac', hashlib.sha256).digest())


def _stream(ek, nonce, want):
    """HMAC-SHA256 in counter mode. Not because a stream cipher is a good idea
    to build by hand, but because the standard library has no cipher at all and
    this is the shape that is safe to build out of a PRF: one keystream block
    per counter, never reused, because the nonce is fresh per seal."""
    out = bytearray()
    counter = 0
    while len(out) < want:
        out += hmac.new(ek, nonce + counter.to_bytes(4, 'big'),
                        hashlib.sha256).digest()
        counter += 1
    return bytes(out[:want])


def seal_secret(secret, plain):
    """A value that has to come back out again -> 'v2.<nonce>.<ct>.<tag>'.

    seal() above signs and does not hide; this hides as well, which is what an
    API key stored in a row a browser may read needs. Encrypt then MAC, fresh
    16-byte nonce every time, and the tag covers the nonce — so a row somebody
    edited by hand fails to open rather than decrypting to nonsense.
    """
    raw = plain.encode('utf-8') if isinstance(plain, str) else bytes(plain or b'')
    if not raw:
        return ''
    ek, mk = _box_keys(secret)
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(raw, _stream(ek, nonce, len(raw))))
    tag = hmac.new(mk, BOX + nonce + ct, hashlib.sha256).digest()[:16]
    return 'v2.' + b64(nonce) + '.' + b64(ct) + '.' + b64(tag)


def open_secret(secret, blob):
    """The string back, or None for anything that is not exactly right — wrong
    version, wrong tag, wrong secret, or bytes that are not text. A different
    secret is the ordinary case: COGNIX_SESSION_SECRET was not set, so the
    process invented one and cannot read what the last one wrote."""
    parts = str(blob or '').split('.')
    if len(parts) != 4 or parts[0] != 'v2':
        return None
    try:
        nonce, ct, tag = unb64(parts[1]), unb64(parts[2]), unb64(parts[3])
    except (ValueError, base64.binascii.Error):
        return None
    if len(nonce) != 16 or len(tag) != 16 or not ct:
        return None
    ek, mk = _box_keys(secret)
    want = hmac.new(mk, BOX + nonce + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(want, tag):
        return None
    try:
        return bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct)))
                     ).decode('utf-8')
    except UnicodeDecodeError:
        return None


def expired(epoch, skew=60):
    """True when `epoch` is in the past, or close enough that a call started
    now would arrive after it. Missing means expired."""
    try:
        return (float(epoch) - skew) <= time.time()
    except (TypeError, ValueError):
        return True


COMMON = frozenset("""password password1 12345678 123456789 qwertyuiop
    letmein00 iloveyou1 admin12345 welcome123 password123 changeme1
    qwerty12345 abc12345678 1q2w3e4r5t passw0rd123 monkey12345""".split())


def weak(pw, email=''):
    """Why check here as well as at Supabase: GoTrue's minimum is a length,
    and the account this protects can spend money. Returns a reason or ''."""
    p = str(pw or '')
    if len(p) < 10:
        return 'Use at least 10 characters.'
    if len(p) > 200:
        return 'That is longer than 200 characters.'
    if p.lower() in COMMON:
        return 'That password is one of the first ones anybody tries.'
    if p.strip() == '':
        return 'A password of spaces is not a password.'
    local = str(email or '').split('@')[0].lower()
    if len(local) > 3 and local in p.lower():
        return 'Do not put your email address in your password.'
    if len(set(p)) < 5:
        return 'That is too few different characters.'
    return ''
