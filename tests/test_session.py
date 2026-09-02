#!/usr/bin/env python3
"""The session cookie: what it is signed with, what it refuses, and the flags
it goes out with.

This is the one piece of the app where a bug is not a bug but an
authentication bypass. The cookie holds a Supabase access token and a role
hint, and it is signed rather than encrypted — so the property that has to
hold is narrow and testable: *nothing edited on the way back is accepted*.

Nothing here opens a socket. server/sessions.py and server/crypto.py are
pure functions over dicts and strings, which is why they were written that
way.
"""
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import config, crypto, sessions                      # noqa: E402

SECRET = 'test-secret-' + 'a' * 40
OTHER = 'test-secret-' + 'b' * 40


def a_session(**over):
    """A plausible sealed session. `rt` matters: sessions.read refuses one
    without a refresh token, because a session that cannot be refreshed is a
    session that dies silently in twenty minutes."""
    out = {'u': '11111111-2222-3333-4444-555555555555',
           'e': 'someone@example.test', 'n': 'Someone', 'r': 'user',
           'v': True, 'at': 'header.payload.sig', 'rt': 'refresh-token-here',
           'ax': time.time() + 3600, 'iat': int(time.time())}
    out.update(over)
    return out


class Sealing(unittest.TestCase):
    def test_a_round_trip_returns_exactly_what_went_in(self):
        s = a_session()
        self.assertEqual(s, crypto.unseal(SECRET, crypto.seal(SECRET, s)))

    def test_the_payload_is_readable_and_that_is_on_purpose(self):
        """Signed, not encrypted. The comment in crypto.py says why; this is
        the test that will fail if somebody 'fixes' it by base64ing a secret
        into the same blob and calling it private."""
        blob = crypto.seal(SECRET, a_session(e='visible@example.test'))
        body = crypto.unb64(blob.split('.')[1]).decode('utf-8')
        self.assertIn('visible@example.test', json.loads(body)['e'])

    def test_another_secret_cannot_read_it(self):
        self.assertIsNone(crypto.unseal(OTHER, crypto.seal(SECRET, a_session())))

    def test_an_edited_payload_is_refused(self):
        """The attack this is for: take your own cookie, change the role to
        admin, put it back. The signature is over the payload, so it does not
        survive — and unseal returns None rather than a partly trusted dict."""
        blob = crypto.seal(SECRET, a_session(r='user'))
        ver, body, mac = blob.split('.')
        want = json.loads(crypto.unb64(body).decode('utf-8'))
        want['r'] = 'admin'
        forged = ver + '.' + crypto.b64(json.dumps(want)) + '.' + mac
        self.assertIsNone(crypto.unseal(SECRET, forged))

    def test_every_other_way_of_being_wrong(self):
        blob = crypto.seal(SECRET, a_session())
        ver, body, mac = blob.split('.')
        for bad in ('', None, 'nonsense', blob[:-1], blob + 'x',
                    body + '.' + mac,                       # no version
                    'v2.' + body + '.' + mac,               # a version we do not sign
                    ver + '.' + body,                       # no mac
                    ver + '..' + mac,                       # no payload
                    ver + '.' + crypto.b64('[1,2,3]') + '.'
                        + crypto.sign(SECRET, crypto.b64('[1,2,3]')),  # not a dict
                    ver + '.' + crypto.b64('not json') + '.'
                        + crypto.sign(SECRET, crypto.b64('not json'))):
            self.assertIsNone(crypto.unseal(SECRET, bad), repr(bad))

    def test_a_signature_from_a_different_payload_does_not_transfer(self):
        one = crypto.seal(SECRET, a_session(u='aaa', rt='r1'))
        two = crypto.seal(SECRET, a_session(u='bbb', rt='r2'))
        mixed = 'v1.' + one.split('.')[1] + '.' + two.split('.')[2]
        self.assertIsNone(crypto.unseal(SECRET, mixed))


class Reading(unittest.TestCase):
    """sessions.read is what every request calls. It runs on the secret the
    process was configured with, so these swap it for a known one."""

    def setUp(self):
        self._was = config.SESSION_SECRET
        config.SESSION_SECRET = SECRET

    def tearDown(self):
        config.SESSION_SECRET = self._was

    def jar(self, sess=None, **over):
        blob = crypto.seal(SECRET, a_session(**over) if sess is None else sess)
        return {config.SESSION_COOKIE: blob}

    def test_a_good_cookie_reads_back(self):
        got = sessions.read(self.jar(e='who@example.test'))
        self.assertEqual('who@example.test', got['e'])

    def test_no_cookie_no_session(self):
        self.assertIsNone(sessions.read({}))
        self.assertIsNone(sessions.read({config.SESSION_COOKIE: ''}))
        self.assertIsNone(sessions.read({'something_else': 'x'}))

    def test_a_session_missing_its_own_id_is_not_a_session(self):
        """Both of these are signed correctly and still refused: a cookie with
        no user id, and one with no refresh token. Either would leave the app
        holding something that looks like a sign-in and is not one."""
        self.assertIsNone(sessions.read(self.jar(u='')))
        self.assertIsNone(sessions.read(self.jar(rt='')))

    def test_a_cookie_signed_by_another_instance_is_refused(self):
        """This is the failure mode SESSION_SECRET exists to prevent: two
        instances with different secrets, each refusing the other's cookies.
        The refusal is the correct behaviour — the test is that it is a clean
        None rather than a half-read session."""
        stale = {config.SESSION_COOKIE: crypto.seal(OTHER, a_session())}
        self.assertIsNone(sessions.read(stale))


class Expiry(unittest.TestCase):
    def test_a_token_that_has_run_out_is_expired(self):
        self.assertTrue(crypto.expired(time.time() - 1))
        self.assertTrue(crypto.expired(0))

    def test_one_about_to_run_out_counts_as_expired(self):
        """A call that starts now and arrives in forty seconds must not be
        sent with a token that dies in thirty. The skew is the whole point."""
        self.assertTrue(crypto.expired(time.time() + 30))
        self.assertFalse(crypto.expired(time.time() + 3600))

    def test_missing_or_unreadable_means_expired(self):
        for v in (None, '', 'soon', {}, []):
            self.assertTrue(crypto.expired(v), repr(v))

    def test_live_hands_back_a_usable_session_untouched(self):
        sess = a_session(ax=time.time() + 3600)
        out, fresh = sessions.live(sess)
        self.assertIs(sess, out)
        self.assertFalse(fresh)

    def test_live_cannot_refresh_in_local_mode(self):
        """No Supabase means no refresh endpoint, so an expired session is
        simply over. It must not come back as a session with no token in it."""
        was, config.SUPABASE_URL = config.SUPABASE_URL, ''
        was_cloud, config.CLOUD = config.CLOUD, False
        try:
            out, fresh = sessions.live(a_session(ax=time.time() - 10))
        finally:
            config.SUPABASE_URL, config.CLOUD = was, was_cloud
        self.assertIsNone(out)
        self.assertFalse(fresh)


class Grant(unittest.TestCase):
    """from_grant turns what GoTrue sends back into the nine keys we keep. The
    interesting cases are the ones where two sources disagree."""

    def grant(self, **over):
        out = {'access_token': 'at-1', 'refresh_token': 'rt-1',
               'expires_in': 3600,
               'user': {'id': 'uid-1', 'email': 'Mixed.Case@Example.Test',
                        'email_confirmed_at': '2026-01-01T00:00:00Z',
                        'user_metadata': {'display_name': 'From Metadata'}}}
        out.update(over)
        return out

    def test_the_ordinary_mapping(self):
        got = sessions.from_grant(self.grant())
        self.assertEqual('uid-1', got['u'])
        self.assertEqual('at-1', got['at'])
        self.assertEqual('rt-1', got['rt'])
        self.assertTrue(got['v'])
        self.assertGreater(got['ax'], time.time() + 3000)

    def test_the_address_is_stored_folded(self):
        """Everything downstream compares addresses as strings — the admin
        promotion list, the password check, the audit log. One capital letter
        in a sign-in form must not produce a second account."""
        self.assertEqual('mixed.case@example.test',
                         sessions.from_grant(self.grant())['e'])

    def test_the_role_comes_from_the_database_and_nowhere_else(self):
        """The one that matters. user_metadata is writable by whoever owns the
        account — GoTrue takes it straight from the sign-up body. If `r` were
        read from there, signing up with {"role": "admin"} would hand out the
        console. It comes from the profile row, which only the server writes."""
        g = self.grant()
        g['user']['user_metadata']['role'] = 'admin'
        self.assertEqual('user', sessions.from_grant(g)['r'])
        self.assertEqual('admin', sessions.from_grant(g, {'role': 'admin'})['r'])

    def test_the_profile_name_wins_over_the_one_in_the_token(self):
        self.assertEqual('From Metadata', sessions.from_grant(self.grant())['n'])
        self.assertEqual('From Profile',
                         sessions.from_grant(self.grant(),
                                             {'display_name': 'From Profile'})['n'])

    def test_an_unconfirmed_address_is_not_verified(self):
        g = self.grant()
        g['user'].pop('email_confirmed_at')
        self.assertFalse(sessions.from_grant(g)['v'])

    def test_an_absolute_expiry_is_preferred_to_a_relative_one(self):
        when = time.time() + 999
        self.assertAlmostEqual(
            when, sessions.from_grant(self.grant(expires_at=when))['ax'], 3)

    def test_a_reply_with_nothing_in_it_does_not_become_a_session(self):
        """A grant with no user is a GoTrue reply we did not understand. It
        maps to a dict with no `u` and no `rt`, which sessions.read refuses —
        so the failure lands as "not signed in" rather than as a session
        belonging to nobody."""
        empty = sessions.from_grant({})
        config_was, config.SESSION_SECRET = config.SESSION_SECRET, SECRET
        try:
            jar = {config.SESSION_COOKIE: crypto.seal(SECRET, empty)}
            self.assertIsNone(sessions.read(jar))
        finally:
            config.SESSION_SECRET = config_was


class Flags(unittest.TestCase):
    """What the browser is told about the cookie. Each of these is one line in
    sessions._set, and each one is load-bearing."""

    def setUp(self):
        self._was = config.SESSION_SECRET
        config.SESSION_SECRET = SECRET

    def tearDown(self):
        config.SESSION_SECRET = self._was

    def test_the_session_cookie_is_not_readable_from_javascript(self):
        """The reason this app uses a cookie at all instead of localStorage.
        Lose HttpOnly and an injected script can walk off with the token."""
        line = sessions.seal(a_session(), True)[0]
        self.assertIn(config.SESSION_COOKIE + '=', line)
        self.assertIn('HttpOnly', line)
        self.assertIn('SameSite=Lax', line)
        self.assertIn('Path=/', line)

    def test_the_csrf_cookie_is_readable_and_that_is_the_point(self):
        """The other half of the double submit: our own JavaScript reads it and
        echoes it in a header, which is the thing a cross-origin page cannot
        do. So this one must *not* be HttpOnly."""
        line = sessions.seal(a_session(), True)[1]
        self.assertIn(config.CSRF_COOKIE + '=', line)
        self.assertNotIn('HttpOnly', line)

    def test_secure_follows_the_scheme(self):
        """Secure on a plain-http laptop would mean the browser kept no cookie
        at all and nobody could sign in locally; missing on https would put the
        session on the wire in the clear. So it is decided per request."""
        self.assertIn('Secure', sessions.seal(a_session(), True)[0])
        self.assertNotIn('Secure', sessions.seal(a_session(), False)[0])

    def test_signing_out_expires_both_cookies_in_the_past(self):
        for line in sessions.clear(True):
            self.assertIn('Max-Age=0', line)
            self.assertIn('Expires=Thu, 01 Jan 1970', line)

    def test_a_session_too_big_for_a_cookie_drops_the_access_token(self):
        """4096 bytes is all a browser guarantees. Rather than ship a cookie
        that gets silently truncated — which reads as a forged signature and
        signs the person out — the access token is left behind and the next
        request pays for a refresh."""
        big = a_session(at='x' * 5000)
        line = sessions.seal(big, False)[0]
        blob = line.split(';')[0].split('=', 1)[1]
        self.assertLessEqual(len(blob), sessions.MAX_COOKIE)
        got = crypto.unseal(SECRET, blob)
        self.assertEqual('', got['at'])
        self.assertEqual('refresh-token-here', got['rt'])


class Csrf(unittest.TestCase):
    def test_a_matching_pair_passes(self):
        line = sessions.csrf_cookie(False)
        value = line.split(';')[0].split('=', 1)[1]
        self.assertTrue(sessions.csrf_ok({config.CSRF_COOKIE: value}, value))

    def test_everything_else_fails(self):
        for jar, head in (({}, 'anything'),
                          ({config.CSRF_COOKIE: ''}, ''),
                          ({config.CSRF_COOKIE: 'abc'}, 'abd'),
                          ({config.CSRF_COOKIE: 'abc'}, ''),
                          ({config.CSRF_COOKIE: 'abc'}, None),
                          ({config.CSRF_COOKIE: 'abc'}, 'ABC'),
                          ({config.CSRF_COOKIE: 'abc'}, 'abc '),
                          ({}, '')):
            self.assertFalse(sessions.csrf_ok(jar, head), (jar, head))

    def test_an_empty_cookie_never_matches_an_empty_header(self):
        """The bug this is guarding: `cookie == header` is true when both are
        missing, which would make every request from a page with no cookie
        pass the check."""
        self.assertFalse(sessions.csrf_ok({config.CSRF_COOKIE: ''}, ''))

    def test_two_tokens_are_never_the_same(self):
        seen = {crypto.token(18) for _ in range(200)}
        self.assertEqual(200, len(seen))


class Jar(unittest.TestCase):
    """Cookie parsing is input handling, so it has to be boring rather than
    clever: no exception, whatever arrives."""

    def test_the_ordinary_shape(self):
        self.assertEqual({'a': '1', 'b': '2'}, sessions.cookies('a=1; b=2'))

    def test_the_shapes_a_browser_or_a_script_actually_sends(self):
        self.assertEqual({'a': '1'}, sessions.cookies('a=1;'))
        self.assertEqual({'a': ''}, sessions.cookies('a='))
        self.assertEqual({'a': '1=2'}, sessions.cookies('a=1=2'))
        self.assertEqual({'x': 'y'}, sessions.cookies('  x = y  '))
        self.assertEqual({}, sessions.cookies('novalue'))
        self.assertEqual({}, sessions.cookies(''))
        self.assertEqual({}, sessions.cookies(None))
        self.assertEqual({}, sessions.cookies('=orphan'))


class Passwords(unittest.TestCase):
    """crypto.weak runs before the password reaches GoTrue, because GoTrue's
    rule is a length and this account can spend money."""

    def test_the_ones_that_are_refused(self):
        for pw in ('short', 'password123', ' ' * 12, 'aaaaaaaaaaaa',
                   'x' * 201, '', None):
            self.assertTrue(crypto.weak(pw), repr(pw))

    def test_your_own_address_is_not_a_password(self):
        self.assertTrue(crypto.weak('sumitkumar99', 'sumitkumar@example.test'))
        self.assertFalse(crypto.weak('sumitkumar99', 'abc@example.test'))

    def test_a_reasonable_one_passes(self):
        for pw in ('correct-horse-battery', 'Tr0ubled Water!', 'stub-password-10'):
            self.assertEqual('', crypto.weak(pw, 'someone@example.test'))

    def test_the_reason_is_a_sentence_somebody_can_act_on(self):
        said = crypto.weak('short')
        self.assertTrue(said.endswith('.'))
        self.assertIn('10', said)


if __name__ == '__main__':
    unittest.main(verbosity=2)


