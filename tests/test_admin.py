#!/usr/bin/env python3
"""The admin console, end to end, on the two servers test_api.py stands up.

Same harness, same cookie jar, same copy of the policies — this file is about
the half of the API that can see across accounts, and the two questions that
half raises:

  * who gets in. The role is decided by the *profile row*, never by the cookie's
    copy of it, and the first administrator exists only because the deployer
    named their own address in ADMIN_EMAILS. Everything in `Door` and
    `Bootstrap` is one of the ways that could go wrong.
  * whose token does the work. A console request reads other people's rows, so
    the temptation is the service key, which bypasses every policy in
    supabase/policies.sql. It is used in exactly two places and `Trust` pins
    both — everything else arrives as the administrator themselves and is
    bounded by `is_admin()` in the database.

    python -m unittest tests.test_admin -v
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import test_api as base                                            # noqa: E402
from server import config, crypto, gateway, limits                  # noqa: E402
import fake_supabase as stub                                       # noqa: E402

# One stub, one app, started and stopped by test_api's fixtures. Aliasing them
# rather than writing new ones is the point: the console is tested against the
# same policies, the same signed cookie and the same wire as everything else.
setUpModule = base.setUpModule
tearDownModule = base.tearDownModule

ADMIN, PW = base.ADMIN, base.PW
NOBODY = 'nobody@cognix.test'
UUID0 = '00000000-0000-4000-8000-000000000000'      # valid shape, no such row


class Console(base.Live):
    """A signed-in administrator, and the small vocabulary the tests share."""

    def boss(self):
        """The first administrator, made the only way there is: sign up like
        anybody else, then open the console once — that first GET is what
        promotes the row."""
        c, _ = self.joined(ADMIN, 'Boss')
        code, out = c.get('/api/admin/overview')
        self.assertEqual(200, code, out)
        return c

    def audit(self, c, action=''):
        code, out = c.get('/api/admin/audit?per=200')
        self.assertEqual(200, code, out)
        rows = out['rows']
        return [r for r in rows if not action or r['action'] == action]

    def row(self, uid):
        """The profile as the database holds it, rather than as the API reports
        it. A change that only ever existed in a reply is not a change."""
        return stub.find('profiles', uid) or {}

    def gotrue_admin(self):
        return [r for r in base.SEEN
                if r[1].startswith('/admin/users') or r[1] == '/invite']

    def uid_of(self, c):
        code, out = c.get('/api/auth/me')
        self.assertEqual(200, code, out)
        return (out.get('user') or {})['id']

    def me(self, c):
        code, out = c.get('/api/auth/me')
        self.assertEqual(200, code, out)
        return out.get('user') or {}


class Door(Console):
    """Who may open the console. Two systems have to agree before anything in
    it answers: this module refuses anybody whose profile row does not say
    admin, and the policies refuse the queries as well."""

    PAGES = ('/api/admin', '/api/admin/overview', '/api/admin/users',
             '/api/admin/users/' + UUID0, '/api/admin/usage',
             '/api/admin/audit', '/api/admin/settings', '/api/admin/gateway',
             '/api/admin/chats')

    def test_an_ordinary_account_is_refused_every_page(self):
        c, _ = self.joined(NOBODY)
        for path in self.PAGES:
            code, out = c.get(path)
            self.assertEqual(403, code, path)
            self.assertEqual('That is an administrator page.', out['error'])

    def test_an_ordinary_account_is_refused_every_write(self):
        c, _ = self.joined(NOBODY)
        writes = (('POST', '/api/admin/invite', {'email': 'x@cognix.test'}),
                  ('POST', '/api/admin/users/%s/confirm' % UUID0, {}),
                  ('PATCH', '/api/admin/users/' + UUID0, {'role': 'admin'}),
                  ('PUT', '/api/admin/settings', {'signups_open': False}),
                  ('PUT', '/api/admin/gateway', {'base': 'https://x.example'}),
                  ('POST', '/api/admin/gateway/check', {}),
                  ('DELETE', '/api/admin/users/' + UUID0, None))
        for method, path, body in writes:
            code, out = c.call(method, path, body)
            self.assertEqual(403, code, path)
            self.assertEqual('That is an administrator page.', out['error'])

    def test_a_refusal_never_touches_the_database_as_the_owner(self):
        """The refusal is the first thing that happens, so a request that was
        going to be turned away cannot have read a row on the way there — and
        certainly not with the key that ignores the policies."""
        c, _ = self.joined(NOBODY)
        del base.SEEN[:]
        self.assertEqual(403, c.get('/api/admin/users')[0])
        self.assertEqual([], self.as_service())

    def test_with_no_session_it_is_sign_in_and_not_a_refusal(self):
        c = base.Client()
        c.boot()
        code, out = c.get('/api/admin/overview')
        self.assertEqual(401, code)
        self.assertIn('sign in', out['error'].lower())

    def test_the_cookies_copy_of_the_role_is_not_what_decides(self):
        """The interface reads the role out of the session to know whether to
        draw the console link. This proves that is all it is good for: the row
        is demoted underneath a cookie that still says admin, and the next
        request is refused."""
        c = self.boss()
        uid = self.uid_of(c)
        stub.find('profiles', uid)['role'] = 'user'
        stub.save()
        limits.PROFILE.drop(uid)
        config.ADMIN_EMAILS = ()          # or the door would promote them again
        try:
            code, out = c.get('/api/admin/overview')
        finally:
            config.ADMIN_EMAILS = (ADMIN,)
        self.assertEqual(403, code)
        self.assertEqual('That is an administrator page.', out['error'])


class Bootstrap(Console):
    """How the first administrator comes to exist. Nothing in the app can grant
    a role — the policies stop that on purpose — so the one promotion that has
    to happen before there is anybody to do it is done with the service key,
    once, for an address the deployer wrote into ADMIN_EMAILS themselves."""

    def test_the_named_address_is_an_ordinary_account_until_it_knocks(self):
        c, _ = self.joined(ADMIN, 'Boss')
        self.assertEqual('user', self.me(c)['role'])
        self.assertEqual('user', self.row(self.uid_of(c)).get('role'))
        self.assertEqual(200, c.get('/api/admin/overview')[0])
        self.assertEqual('admin', self.row(self.uid_of(c)).get('role'))
        self.assertEqual('admin', self.me(c)['role'])

    def test_an_address_nobody_named_is_not_promoted_by_asking(self):
        c, _ = self.joined(NOBODY)
        self.assertEqual(403, c.get('/api/admin/overview')[0])
        self.assertEqual('user', self.row(self.uid_of(c)).get('role'))

    def test_the_promotion_is_written_down_once(self):
        c = self.boss()
        rows = self.audit(c, 'admin.bootstrap')
        self.assertEqual(1, len(rows), rows)
        self.assertEqual(ADMIN, rows[0]['actor_email'])
        self.assertEqual(ADMIN, rows[0]['target_email'])
        self.assertEqual({'from': 'ADMIN_EMAILS'}, rows[0]['detail'])
        for _ in range(3):
            self.assertEqual(200, c.get('/api/admin/overview')[0])
        self.assertEqual(1, len(self.audit(c, 'admin.bootstrap')))

    def test_the_promotion_is_the_only_thing_the_service_key_does_here(self):
        """One PATCH of one profile row, and then nothing: every later console
        request is the administrator's own token against the policies."""
        c = self.boss()
        self.assertEqual([('PATCH', '/profiles', 'service')], self.as_service())
        del base.SEEN[:]
        for path in ('/api/admin/overview', '/api/admin/users',
                     '/api/admin/usage', '/api/admin/audit',
                     '/api/admin/settings', '/api/admin/chats'):
            self.assertEqual(200, c.get(path)[0], path)
        self.assertEqual([], self.as_service())
        self.assertTrue(self.as_user())

    def test_without_the_service_key_the_console_says_which_key_is_missing(self):
        """The state a deployment is in between pasting the publishable key and
        finding the secret one. The promotion cannot run, so the console stays
        shut — and says so, to the address that is supposed to be opening it,
        rather than pretending it does not know them."""
        was = config.SUPABASE_SERVICE_KEY
        config.SUPABASE_SERVICE_KEY = ''
        try:
            c, _ = self.joined(ADMIN, 'Boss')
            code, out = c.get('/api/admin/overview')
            self.assertEqual(503, code, out)
            self.assertIn('SERVICE_KEY', out['error'])
            self.assertEqual('user', self.row(self.uid_of(c)).get('role'))
        finally:
            config.SUPABASE_SERVICE_KEY = was


class People(Console):
    """The user list and one user's page — the two screens support actually
    lives in."""

    def test_the_list_pages_and_carries_each_persons_chat_count(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        self.made(a, title='One')
        self.made(a, title='Two')
        code, out = boss.get('/api/admin/users?per=1&page=1')
        self.assertEqual(200, code, out)
        self.assertEqual(1, len(out['users']))
        self.assertEqual(2, out['total'])
        self.assertEqual(1, out['per'])
        seen = {}
        for page in (1, 2):
            for u in boss.get('/api/admin/users?per=1&page=%d' % page)[1]['users']:
                seen[u['email']] = u
        self.assertEqual({ADMIN, 'ann@cognix.test'}, set(seen))
        self.assertEqual(2, seen['ann@cognix.test']['chats'])
        self.assertEqual(0, seen[ADMIN]['chats'])

    def test_the_list_filters_by_role_and_by_status(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'status': 'suspended'})[0])
        got = boss.get('/api/admin/users?role=admin')[1]
        self.assertEqual([ADMIN], [u['email'] for u in got['users']])
        got = boss.get('/api/admin/users?status=suspended')[1]
        self.assertEqual(['ann@cognix.test'], [u['email'] for u in got['users']])
        got = boss.get('/api/admin/users?role=nonsense')[1]
        self.assertEqual(2, got['total'])          # an unknown value is no filter

    def test_a_search_box_cannot_add_a_filter_of_its_own(self):
        """`or=(email.ilike.*x*,…)` is built from what somebody typed. A comma
        or a bracket would end that term and start another, so they are taken
        out rather than escaped — this syntax has no quoting to escape into."""
        boss = self.boss()
        self.joined('ann@cognix.test', 'Ann')
        self.assertEqual(['ann@cognix.test'],
                         [u['email'] for u in
                          boss.get('/api/admin/users?q=ann')[1]['users']])
        self.assertEqual(['ann@cognix.test'],
                         [u['email'] for u in
                          boss.get('/api/admin/users?q=Ann')[1]['users']])
        for hostile, want in (('ann,role.eq.admin', []),
                              ('ann)', ['ann@cognix.test']),
                              ('ann*)(,', ['ann@cognix.test'])):
            code, out = boss.get('/api/admin/users?q=' + hostile)
            self.assertEqual(200, code, hostile)
            self.assertEqual(want, [u['email'] for u in out['users']], hostile)
        # a box with nothing searchable left in it is an empty box rather than a
        # term that matches nothing: `*` alone lists everybody, as no q does
        self.assertEqual(2, boss.get('/api/admin/users?q=*')[1]['total'])

    def test_one_persons_page_gathers_what_support_would_ask_for(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.made(a, title='Only')
        code, out = boss.get('/api/admin/users/' + uid)
        self.assertEqual(200, code, out)
        self.assertEqual('ann@cognix.test', out['user']['email'])
        self.assertEqual(['Only'], [c['title'] for c in out['chats']])
        self.assertEqual(1, out['usage']['chats'])
        # the login half comes from GoTrue, which has no policies to lean on —
        # so it is the one read here that carries the service key
        self.assertTrue(out['login']['confirmed'])
        self.assertEqual([('GET', '/admin/users/' + uid, 'service')],
                         [r for r in self.as_service() if r[0] == 'GET'])

    def test_a_uuid_that_is_nobody_is_a_404_and_a_shape_that_is_not_a_uuid_a_400(self):
        boss = self.boss()
        code, out = boss.get('/api/admin/users/' + UUID0)
        self.assertEqual(404, code)
        self.assertEqual('There is no such user.', out['error'])
        code, out = boss.get('/api/admin/users/not-a-uuid')
        self.assertEqual(400, code)
        self.assertEqual('user', out.get('field'))


class Change(Console):
    """Editing somebody's row. Every one of these writes a line in the audit
    log, and the two that would leave nobody able to open the console are
    refused here and again by the database."""

    def test_a_name_and_a_note_go_in_and_are_written_down(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        code, out = boss.call('PATCH', '/api/admin/users/' + uid,
                              {'name': 'Ann R.', 'notes': 'asked about billing'})
        self.assertEqual(200, code, out)
        self.assertEqual('Ann R.', out['user']['display_name'])
        self.assertEqual('asked about billing', self.row(uid).get('notes'))
        rows = self.audit(boss, 'user.change')
        self.assertEqual(1, len(rows), rows)
        self.assertEqual('ann@cognix.test', rows[0]['target_email'])
        self.assertEqual(ADMIN, rows[0]['actor_email'])
        self.assertEqual({'display_name': 'Ann R.',
                          'notes': 'asked about billing'}, rows[0]['detail'])

    def test_a_personal_ceiling_can_be_put_on_and_taken_off_again(self):
        """The empty box is a real setting and not a missing one. Without the
        `None` branch a cap could be put on from the panel and never lifted,
        and the person would keep hitting it with nothing on screen to explain
        why."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'token_cap': 5000})[0])
        # immediately, on their next request: _user_change drops both caches
        seen = a.get('/api/usage')[1]
        self.assertEqual(5000, seen['cap'])
        self.assertFalse(seen['unlimited'])
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'token_cap': ''})[0])
        self.assertIsNone(self.row(uid).get('token_cap'))
        self.assertEqual(config.TOKEN_CAP, a.get('/api/usage')[1]['cap'])
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'token_cap': 0})[0])
        self.assertTrue(a.get('/api/usage')[1]['unlimited'])

    def test_you_cannot_lock_yourself_out_of_the_panel(self):
        boss = self.boss()
        me = self.uid_of(boss)
        for body in ({'role': 'user'}, {'status': 'suspended'}):
            code, out = boss.call('PATCH', '/api/admin/users/' + me, body)
            self.assertEqual(409, code, body)
            self.assertEqual('That would lock you out of this panel. Another '
                             'administrator can do it for you.', out['error'])
        self.assertEqual('admin', self.row(me).get('role'))
        self.assertEqual('active', self.row(me).get('status'))
        self.assertEqual([], self.audit(boss, 'user.change'))

    def test_a_request_that_changes_nothing_and_a_role_that_is_not_a_role(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        code, out = boss.call('PATCH', '/api/admin/users/' + uid, {'nope': 1})
        self.assertEqual(400, code)
        self.assertEqual('There is nothing to change in that request.',
                         out['error'])
        code, out = boss.call('PATCH', '/api/admin/users/' + uid,
                              {'role': 'superuser'})
        self.assertEqual(400, code)
        self.assertEqual('role', out.get('field'))
        self.assertEqual('user', self.row(uid).get('role'))

    def test_the_console_can_be_handed_over_to_somebody_else(self):
        """A promotion the new administrator can see on her next request, and a
        demotion of the person who promoted her that actually shuts the door."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        ann, me = self.uid_of(a), self.uid_of(boss)
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + ann,
                                       {'role': 'admin'})[0])
        self.assertEqual('admin', self.me(a)['role'])
        self.assertEqual(200, a.get('/api/admin/overview')[0])
        self.assertEqual(200, a.call('PATCH', '/api/admin/users/' + me,
                                    {'role': 'user'})[0])
        config.ADMIN_EMAILS = ()          # or the door would let them back in
        try:
            code, out = boss.get('/api/admin/overview')
        finally:
            config.ADMIN_EMAILS = (ADMIN,)
        self.assertEqual(403, code, out)
        actors = set((r['actor_email'], r['target_email'])
                     for r in self.audit(a, 'user.change'))
        self.assertEqual({(ADMIN, 'ann@cognix.test'), ('ann@cognix.test', ADMIN)},
                         actors)

    def test_the_deployer_can_always_get_back_in(self):
        """ADMIN_EMAILS is the way back after a handover goes wrong: the row is
        demoted, and the next knock at the console promotes it again."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        ann, me = self.uid_of(a), self.uid_of(boss)
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + ann,
                                       {'role': 'admin'})[0])
        self.assertEqual(200, a.call('PATCH', '/api/admin/users/' + me,
                                    {'role': 'user'})[0])
        self.assertEqual('user', self.row(me).get('role'))
        self.assertEqual(200, boss.get('/api/admin/overview')[0])
        self.assertEqual('admin', self.row(me).get('role'))


class Suspension(Console):
    """Suspending somebody has to take effect while they are still holding a
    perfectly valid cookie, or it is not a suspension — it is a suspension that
    starts in twenty minutes."""

    SAID = 'This account is suspended. An administrator can lift that.'

    def suspend(self, boss, uid, how='suspended'):
        code, out = boss.call('PATCH', '/api/admin/users/' + uid,
                              {'status': how})
        self.assertEqual(200, code, out)

    def test_it_bites_on_the_very_next_request(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.assertEqual(200, a.get('/api/data/bootstrap')[0])
        self.suspend(boss, uid)
        for path in ('/api/data/bootstrap', '/api/data/chats', '/api/usage'):
            code, out = a.get(path)
            self.assertEqual(403, code, path)
            self.assertEqual(self.SAID, out['error'], path)
        # …and the one path that spends money, which serve.py guards before it
        # reads the body or reaches for the key
        code, out = a.call('POST', '/gw/v1/messages',
                           {'model': 'claude-opus-5-thinking', 'max_tokens': 8,
                            'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(403, code)
        self.assertEqual(self.SAID, out['error'])

    def test_they_are_told_why_rather_than_signed_out(self):
        """/api/auth/me keeps answering, because the page has to be able to say
        what happened instead of bouncing them to a sign-in form that would
        work and then refuse everything behind it."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        self.suspend(boss, self.uid_of(a))
        self.assertEqual('suspended', self.me(a)['status'])

    def test_signing_in_again_does_not_get_around_it(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        self.suspend(boss, self.uid_of(a))
        fresh = base.Client()
        fresh.boot()
        code, out = fresh.post('/api/auth/login',
                               {'email': 'ann@cognix.test', 'password': PW})
        self.assertEqual(403, code, out)
        self.assertEqual(self.SAID, out['error'])
        self.assertNotIn(config.SESSION_COOKIE, fresh.jar)

    def test_lifting_it_gives_the_work_back(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.made(a, title='Kept')
        self.suspend(boss, uid)
        self.assertEqual(403, a.get('/api/data/bootstrap')[0])
        self.suspend(boss, uid, 'active')
        code, out = a.get('/api/data/bootstrap')
        self.assertEqual(200, code, out)
        self.assertEqual(['Kept'], [c['title'] for c in out['chats']])


class Removal(Console):
    """Deleting an account. GoTrue owns the login, so that is what is deleted;
    the profile row and everything hanging off it go with it."""

    def test_the_login_goes_first_and_the_rows_follow_it(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.made(a, title='Gone with it')
        code, out = boss.call('DELETE', '/api/admin/users/' + uid, None)
        self.assertEqual(200, code, out)
        self.assertEqual(uid, out['deleted'])
        self.assertEqual({}, self.row(uid))
        self.assertNotIn(uid, stub.USERS)
        self.assertEqual([], [c for c in stub.DB['chats']
                              if c.get('user_id') == uid])
        rows = self.audit(boss, 'user.delete')
        self.assertEqual(1, len(rows), rows)
        self.assertEqual('ann@cognix.test', rows[0]['target_email'])
        self.assertEqual({'upstream': 200}, rows[0]['detail'])
        # the login is GoTrue's, which has no policy to lean on; the row is
        # deleted as the administrator, and the policies allow that
        self.assertEqual([('DELETE', '/admin/users/' + uid, 'service')],
                         [r for r in self.as_service() if r[0] == 'DELETE'])
        self.assertIn(('DELETE', '/profiles', 'authenticated'), self.as_user())

    def test_the_log_outlives_the_account(self):
        """`target` is a bare uuid and `actor` is `on delete set null`, both on
        purpose: a log that loses its entries when the account goes is not a
        log, and what happened to somebody is most worth reading afterwards."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'notes': 'before'})[0])
        self.assertEqual(200, boss.call('DELETE', '/api/admin/users/' + uid,
                                       None)[0])
        kept = self.audit(boss, 'user.change')
        self.assertEqual(1, len(kept), kept)
        self.assertEqual(uid, kept[0]['target'])

    def test_you_cannot_delete_yourself_from_here(self):
        boss = self.boss()
        code, out = boss.call('DELETE', '/api/admin/users/' + self.uid_of(boss),
                              None)
        self.assertEqual(409, code)
        self.assertEqual('You cannot delete your own account from here.',
                         out['error'])
        self.assertEqual('admin', self.row(self.uid_of(boss)).get('role'))


class Actions(Console):
    """The four buttons that send mail or unstick somebody: reset, resend,
    confirm by hand, and invite."""

    def test_a_reset_link_is_sent_and_written_down(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        del stub.MAIL[:]
        code, out = boss.post('/api/admin/users/%s/reset' % uid)
        self.assertEqual(200, code, out)
        self.assertIn('ann@cognix.test', out['message'])
        self.assertEqual([('recover', 'ann@cognix.test')],
                         [(m['kind'], m['to']) for m in stub.MAIL])
        self.assertEqual(1, len(self.audit(boss, 'user.reset')))

    def test_an_address_can_be_confirmed_by_hand(self):
        """The first day of a deployment, before SMTP is set up. GoTrue owns the
        confirmation flag, so this is one of the two places the service key is
        used — there is no policy that could grant it."""
        stub.CONFIRM = True
        try:
            c = base.Client()
            c.boot()
            code, out = c.post('/api/auth/signup',
                               {'email': 'wait@cognix.test', 'password': PW,
                                'name': 'Wait'})
            self.assertEqual(200, code, out)
            self.assertTrue(out.get('verify'))
        finally:
            stub.CONFIRM = False
        boss = self.boss()
        uid = stub.by_email('wait@cognix.test')['id']
        self.assertFalse(stub.USERS[uid]['confirmed'])
        del base.SEEN[:]
        code, out = boss.post('/api/admin/users/%s/confirm' % uid)
        self.assertEqual(200, code, out)
        self.assertEqual('wait@cognix.test is confirmed.', out['message'])
        self.assertTrue(stub.USERS[uid]['confirmed'])
        self.assertEqual([('PUT', '/admin/users/' + uid, 'service')],
                         self.as_service())
        self.assertEqual(1, len(self.audit(boss, 'user.confirm')))
        # and now they can sign in, which is the point of the button
        fresh = base.Client()
        fresh.boot()
        self.assertEqual(200, fresh.post('/api/auth/login',
                                         {'email': 'wait@cognix.test',
                                          'password': PW})[0])

    def test_a_new_confirmation_link_can_be_asked_for(self):
        """GoTrue answers 200 whether or not there was anything to send, which
        is the behaviour to keep: the reply must not say whether an address is
        waiting to be confirmed."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        code, out = boss.post('/api/admin/users/%s/resend' % self.uid_of(a))
        self.assertEqual(200, code, out)
        self.assertIn('ann@cognix.test', out['message'])
        self.assertEqual(1, len(self.audit(boss, 'user.resend')))

    def test_an_action_nobody_has_and_a_user_nobody_is(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        code, out = boss.post('/api/admin/users/%s/promote' % uid)
        self.assertEqual(404, code)
        self.assertEqual('No such action.', out['error'])
        code, out = boss.post('/api/admin/users/%s/reset' % UUID0)
        self.assertEqual(404, code)
        self.assertEqual('There is no such user.', out['error'])
        code, out = boss.get('/api/admin/users/%s/reset' % uid)
        self.assertEqual(405, code)
        self.assertEqual('That endpoint takes a POST.', out['error'])
        self.assertEqual([], self.audit(boss, 'user.reset'))

    def test_an_invitation_goes_out_once_and_then_says_so(self):
        boss = self.boss()
        del stub.MAIL[:]
        code, out = boss.post('/api/admin/invite', {'email': 'New@Cognix.test'})
        self.assertEqual(200, code, out)
        self.assertEqual('new@cognix.test', out['invited'])
        self.assertEqual([('invite', 'new@cognix.test')],
                         [(m['kind'], m['to']) for m in stub.MAIL])
        rows = self.audit(boss, 'user.invite')
        self.assertEqual(1, len(rows), rows)
        self.assertEqual('new@cognix.test', rows[0]['target_email'])
        self.assertEqual([('POST', '/invite', 'service')],
                         [r for r in self.as_service() if r[1] == '/invite'])
        code, out = boss.post('/api/admin/invite', {'email': 'new@cognix.test'})
        self.assertEqual(409, code)
        self.assertEqual('There is already an account on that address.',
                         out['error'])
        code, out = boss.post('/api/admin/invite', {'email': 'not-an-address'})
        self.assertEqual(400, code)
        self.assertEqual('email', out.get('field'))


class Settings(Console):
    """The one row in app_settings. Anybody may read it — that is how a signed
    out page knows whether to draw a sign-up form — and only an administrator
    may write it, which is a policy and not a promise this module makes."""

    def test_the_page_shows_the_row_and_what_the_environment_says(self):
        boss = self.boss()
        code, out = boss.get('/api/admin/settings')
        self.assertEqual(200, code, out)
        self.assertTrue(out['settings']['signups_open'])
        self.assertEqual(1, out['settings']['id'])
        self.assertEqual(1, out['env']['admin_emails'])          # a count, not the address
        self.assertEqual('cloud', out['env']['mode'])
        self.assertEqual(config.TOKEN_CAP, out['env']['token_cap_default'])
        self.assertIs(True, out['env']['signups_env'])

    def test_closing_signups_closes_them_for_the_next_browser(self):
        """The switch has to bite without a restart, which is what the cleared
        settings cache is for: the row is read once every few seconds and the
        write drops that copy."""
        boss = self.boss()
        code, out = boss.put('/api/admin/settings', {'signups_open': False})
        self.assertEqual(200, code, out)
        self.assertFalse(out['settings']['signups_open'])
        fresh = base.Client()
        self.assertFalse(fresh.boot()[1]['signups'])
        code, out = fresh.post('/api/auth/signup',
                               {'email': 'late@cognix.test', 'password': PW})
        self.assertEqual(403, code)
        self.assertEqual('New accounts are closed at the moment. An '
                         'administrator can send you an invite.', out['error'])
        rows = self.audit(boss, 'settings.change')
        self.assertEqual(1, len(rows), rows)
        self.assertEqual({'signups_open': False,
                          'updated_by': self.uid_of(boss)}, rows[0]['detail'])
        self.assertEqual(200, boss.put('/api/admin/settings',
                                       {'signups_open': True})[0])
        self.assertTrue(fresh.boot()[1]['signups'])
        self.assertEqual(200, fresh.post('/api/auth/signup',
                                         {'email': 'late@cognix.test',
                                          'password': PW})[0])

    def test_an_announcement_and_a_model_list_reach_the_signed_out_page(self):
        """...and the list names the agents, whatever the row happens to hold.
        A stored vendor id — which is what a row written before the agents were
        named holds — is turned back into the agent's name on the way out, here
        and on the console's own settings screen. This is the one endpoint that
        answers a browser with no session at all, so it is the one where getting
        that wrong would tell everybody which model is behind which agent."""
        boss = self.boss()
        code, out = boss.put('/api/admin/settings',
                             {'announcement': 'Back at 14:00 UTC.',
                              'maintenance': True,
                              'allowed_models': 'claude-opus-5-thinking, x '})
        self.assertEqual(200, code, out)
        self.assertEqual(['cognix-apex-v2', 'x'], out['settings']['allowed_models'])
        got = base.Client().boot()[1]
        self.assertEqual('Back at 14:00 UTC.', got['announcement'])
        self.assertTrue(got['maintenance'])
        self.assertEqual(['cognix-apex-v2', 'x'], got['models'])
        self.assertNotIn('claude', json.dumps(got))
        self.assertEqual(['cognix-apex-v2', 'x'],
                         boss.get('/api/admin/settings')[1]['settings']['allowed_models'])

    def test_a_row_holding_both_an_id_and_its_agent_name_lists_one_model(self):
        """The condition a real project is in the day after the agents were
        named: the row was saved from a console that offered the new names while
        the old ids were still ticked, so it holds four entries for two agents.
        Publicising maps two of them onto the other two, and a list of allowed
        models is a set — so the screen would otherwise draw the same agent twice,
        with two switches for one model, and the next Save would store the
        duplicate back."""
        boss = self.boss()
        both = ('claude-opus-4-8-thinking, claude-opus-5-thinking, '
                'cognix-apex-v2, cognix-mind-v1')
        code, out = boss.put('/api/admin/settings', {'allowed_models': both})
        self.assertEqual(200, code, out)
        self.assertEqual(['cognix-mind-v1', 'cognix-apex-v2'],
                         out['settings']['allowed_models'])
        self.assertEqual(['cognix-mind-v1', 'cognix-apex-v2'],
                         base.Client().boot()[1]['models'])
        # And a duplicate that is already public never reaches the row either.
        code, out = boss.put('/api/admin/settings',
                             {'allowed_models': ['cognix-mind-v1', 'cognix-mind-v1']})
        self.assertEqual(200, code, out)
        self.assertEqual(['cognix-mind-v1'], out['settings']['allowed_models'])

    def test_a_settings_write_that_changes_nothing_is_refused(self):
        boss = self.boss()
        code, out = boss.put('/api/admin/settings', {'nonsense': True})
        self.assertEqual(400, code)
        self.assertEqual('There is nothing to change in that request.',
                         out['error'])

    def test_the_settings_screen_does_not_carry_the_gateway_columns(self):
        """They live on the same row and have their own screen. Popping them out
        here is what stops the masked key riding along on a page that does not
        need it — on the read and on the write, because both return the row."""
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'key': Gate.KEY})[0])
        for got in (boss.get('/api/admin/settings')[1],
                    boss.put('/api/admin/settings', {'maintenance': True})[1]):
            for col in gateway.COLS:
                self.assertNotIn(col, got['settings'])
            self.assertNotIn(Gate.KEY, json.dumps(got))


class Gate(Console):
    """The gateway: where the model calls go and with which key, changed from
    here rather than by a redeploy.

    It is the console's one write that stores a secret, so three things are held
    down that no other screen needs. The key goes in and never comes back out —
    not in a reply, not in the audit row, not on the signed-out page. What is
    stored is ciphertext under COGNIX_SESSION_SECRET, so a dump of app_settings
    is not a working key. And the environment stays the fallback *per field*, so
    saving a URL here does not quietly unset COGNIX_KEY."""

    KEY = 'sk-' + 'Ab3' * 14                   # keyish, and nobody's
    ENV_KEY = 'sk-' + 'Env7' * 8

    def setUp(self):
        Console.setUp(self)
        # gateway.env_pair() calls back into serve for these, late, which is
        # what makes pointing them here enough. SESSION_SECRET_GIVEN is not in
        # test_api's saved list on purpose — it depends on whether the checkout
        # has a .env — so it is pinned per test and put back.
        self.was = (config.SESSION_SECRET_GIVEN, base.serve.BASE, base.serve.KEY)
        config.SESSION_SECRET_GIVEN = True
        base.serve.BASE = self.dead()
        base.serve.KEY = self.ENV_KEY
        gateway.forget()

    def tearDown(self):
        config.SESSION_SECRET_GIVEN, base.serve.BASE, base.serve.KEY = self.was
        gateway.forget()

    def dead(self):
        """A URL on a port nothing is listening on. Every gateway address in
        this class is one: `check` really does open a connection, and nothing in
        this suite may reach the real gateway."""
        return 'http://127.0.0.1:%d' % base._dead_port()

    def stored(self):
        """The row as Postgres holds it, which is where the ciphertext is. A
        value that only ever existed in a reply is not a stored value."""
        return stub.find('app_settings', 1) or {}

    def gw(self, c):
        code, out = c.get('/api/admin/gateway')
        self.assertEqual(200, code, out)
        return out

    def ask(self, c):
        """A model call, by the name the app uses for the agent."""
        return c.call('POST', '/gw/v1/messages',
                      {'model': 'cognix-mind-v1', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}]})

    # -------------------------------------------------------- what is in force
    def test_with_nothing_stored_the_environment_is_what_answers(self):
        boss = self.boss()
        out = self.gw(boss)
        g = out['gateway']
        self.assertEqual('', g['base'])
        self.assertFalse(g['key_set'])
        self.assertFalse(g['stored'])
        self.assertFalse(g['unreadable'])
        self.assertEqual('env', g['source'])
        self.assertEqual(base.serve.BASE, g['in_use'])
        self.assertIs(True, g['in_use_key'])            # a bool, not the key
        self.assertEqual(base.serve.BASE, g['env_base'])
        self.assertIs(True, g['env_key'])
        self.assertIs(True, g['sealable'])
        self.assertEqual('cloud', out['env']['mode'])
        self.assertNotIn(self.ENV_KEY, json.dumps(out))

    def test_a_url_saved_here_wins_and_the_environments_key_still_answers(self):
        """Per field, not per set. Somebody moving the gateway to a new host has
        not asked for the key to be forgotten as well, and `mixed` is the screen
        saying which half came from where."""
        boss = self.boss()
        url = self.dead()
        code, out = boss.put('/api/admin/gateway', {'base': url + '/'})
        self.assertEqual(200, code, out)
        g = out['gateway']
        self.assertEqual(url, g['base'])                # the slash is dropped
        self.assertEqual(url, g['in_use'])
        self.assertFalse(g['key_set'])
        self.assertIs(True, g['in_use_key'])
        self.assertEqual('mixed', g['source'])
        self.assertTrue(g['updated_at'])
        self.assertEqual(url, self.stored()['gateway_base'])
        self.assertEqual('', self.stored()['gateway_sealed'])

    def test_a_key_saved_here_is_sealed_and_comes_back_masked_or_not_at_all(self):
        boss = self.boss()
        code, out = boss.put('/api/admin/gateway', {'key': self.KEY})
        self.assertEqual(200, code, out)
        g = out['gateway']
        self.assertIs(True, g['key_set'])
        self.assertEqual(config.mask(self.KEY), g['hint'])
        self.assertEqual('mixed', g['source'])          # panel key, env URL
        self.assertEqual(base.serve.BASE, g['in_use'])
        self.assertNotIn(self.KEY, json.dumps(out))
        sealed = self.stored()['gateway_sealed']
        self.assertNotIn(self.KEY, sealed)
        self.assertEqual(self.KEY,
                         crypto.open_secret(config.SESSION_SECRET, sealed))
        # ...and the same row, to anybody without that secret, is not a key
        self.assertFalse(crypto.open_secret('some other secret', sealed))

    def test_both_saved_here_is_the_panel_answering_and_nothing_else(self):
        boss = self.boss()
        url = self.dead()
        code, out = boss.put('/api/admin/gateway',
                             {'base': url, 'key': self.KEY})
        self.assertEqual(200, code, out)
        self.assertEqual('panel', out['gateway']['source'])
        base.serve.BASE = base.serve.KEY = ''           # nothing in the env now
        gateway.forget()
        g = self.gw(boss)['gateway']
        self.assertEqual('panel', g['source'])
        self.assertEqual(url, g['in_use'])
        self.assertIs(True, g['in_use_key'])
        self.assertEqual('', g['env_base'])
        self.assertIs(False, g['env_key'])

    # ------------------------------------------------------------ giving it up
    def test_clearing_the_key_hands_the_question_back_to_the_environment(self):
        """'' is a different request from not sending the field at all, and both
        have to be expressible: one means 'leave it alone', the other means
        'forget what is stored and use COGNIX_KEY again'."""
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'key': self.KEY})[0])
        code, out = boss.put('/api/admin/gateway', {'key': ''})
        self.assertEqual(200, code, out)
        g = out['gateway']
        self.assertFalse(g['key_set'])
        self.assertEqual('', g['hint'])
        self.assertEqual('env', g['source'])
        self.assertIs(True, g['in_use_key'])            # the environment's
        self.assertEqual('', self.stored()['gateway_sealed'])
        self.assertEqual('', self.stored()['gateway_hint'])

    def test_clearing_the_url_does_the_same_for_the_url(self):
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': self.dead()})[0])
        code, out = boss.put('/api/admin/gateway', {'base': ''})
        self.assertEqual(200, code, out)
        self.assertEqual('', out['gateway']['base'])
        self.assertEqual('env', out['gateway']['source'])
        self.assertEqual(base.serve.BASE, out['gateway']['in_use'])

    # ------------------------------------------------------------ what is said
    def test_every_save_is_audited_in_the_masked_form_and_no_other(self):
        boss = self.boss()
        uid = self.uid_of(boss)
        url = self.dead()
        for body in ({'base': url}, {'key': self.KEY}, {'key': ''}):
            self.assertEqual(200, boss.put('/api/admin/gateway', body)[0], body)
        rows = self.audit(boss, 'gateway.change')
        self.assertEqual(3, len(rows), rows)
        said = [r['detail'] for r in rows]
        self.assertIn({'base': url, 'key': None, 'cleared': False,
                       'models': None, 'updated_by': uid}, said)
        self.assertIn({'base': None, 'key': config.mask(self.KEY),
                       'cleared': False, 'models': None, 'updated_by': uid}, said)
        self.assertIn({'base': None, 'key': '', 'cleared': True,
                       'models': None, 'updated_by': uid}, said)
        # an audit log is read by people, so the sealed value is not in it
        self.assertNotIn(self.KEY, json.dumps(rows))

    def test_nothing_about_the_gateway_reaches_a_signed_out_page(self):
        """app_settings is the one table a browser with no session may select
        from, and this now lives on it. What a page is told comes from a list of
        four fields in server/api.py rather than from the row, which is what
        keeps the ciphertext, the hint and even the URL on this side."""
        boss = self.boss()
        url = self.dead()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': url, 'key': self.KEY})[0])
        got = json.dumps(base.Client().boot()[1])
        for word in ('gateway', url, self.KEY, config.mask(self.KEY),
                     self.stored()['gateway_sealed']):
            self.assertNotIn(word, got)

    # ----------------------------------------------------------- what it refuses
    def test_a_write_that_changes_nothing_is_refused(self):
        boss = self.boss()
        code, out = boss.put('/api/admin/gateway', {'nonsense': True})
        self.assertEqual(400, code)
        self.assertEqual('There is nothing to change in that request.',
                         out['error'])

    def test_a_url_this_could_not_call_is_refused_and_the_field_is_named(self):
        boss = self.boss()
        for bad in ('api.example.com', 'ftp://api.example.com',
                    'https://api.example.com/v1?model=x',
                    'https://api example.com', 'https://api.example.com/#f'):
            code, out = boss.put('/api/admin/gateway', {'base': bad})
            self.assertEqual(400, code, bad)
            self.assertEqual('base', out.get('field'), bad)
        self.assertEqual('', self.stored()['gateway_base'])

    def test_a_key_with_something_in_it_that_is_not_a_key_is_refused(self):
        """A header break or a space in this value would end up in a request
        header, so the shape is checked before anything is sealed."""
        boss = self.boss()
        for bad in ('has a space', 'sk-line\r\nx: y', 'sk-' + 'x' * 400, 'sk'):
            code, out = boss.put('/api/admin/gateway', {'key': bad})
            self.assertEqual(400, code, bad)
            self.assertEqual('key', out.get('field'), bad)
        self.assertEqual('', self.stored()['gateway_sealed'])

    def test_without_a_session_secret_a_key_is_refused_rather_than_stored(self):
        """It is sealed under COGNIX_SESSION_SECRET, and without one each
        restart invents a new secret — so a key stored now could never be read
        back. Refusing is the only honest answer, and `sealable` is how the
        screen says so before anybody types."""
        config.SESSION_SECRET_GIVEN = False
        boss = self.boss()
        out = self.gw(boss)
        self.assertIs(False, out['gateway']['sealable'])
        self.assertIs(False, out['env']['secret_given'])
        code, out = boss.put('/api/admin/gateway', {'key': self.KEY})
        self.assertEqual(400, code)
        self.assertEqual('key', out.get('field'))
        self.assertIn('COGNIX_SESSION_SECRET', out['error'])
        self.assertEqual('', self.stored()['gateway_sealed'])
        self.assertEqual([], self.audit(boss, 'gateway.change'))
        # the URL is not a secret, so that half still works
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': self.dead()})[0])

    def test_a_key_this_process_cannot_open_is_named_as_exactly_that(self):
        """The one failure worth its own word. COGNIX_SESSION_SECRET changed, so
        the stored key is sealed under a secret this process does not have: it
        looks like 'a key is set' from every other angle and behaves like 'there
        is no key'. Checked against the row rather than over HTTP, because
        changing that secret also invalidates the cookie asking the question."""
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'key': self.KEY})[0])
        was, config.SESSION_SECRET = config.SESSION_SECRET, 'a different secret'
        try:
            st = gateway.status(self.stored())
            self.assertTrue(st['unreadable'])
            self.assertFalse(st['key_set'])
            self.assertTrue(st['stored'])
            self.assertEqual('env', st['source'])       # falls back, not dies
            self.assertEqual(self.ENV_KEY,
                             gateway.effective(self.stored())[1])
        finally:
            config.SESSION_SECRET = was

    def test_a_database_that_has_never_heard_of_the_columns_says_so(self):
        """A project whose SQL has not been re-run has no such columns, and
        PostgREST answers that with a code rather than a sentence. It is worth
        one, because the fix is 'run schema.sql again' and nothing else."""
        class Rep(object):
            ok, status = False, 400
            body = {'code': 'PGRST204',
                    'message': "Could not find the 'gateway_base' column"}
        self.assertTrue(gateway.missing_column(Rep()))
        Rep.body = {'code': '42501', 'message': 'permission denied'}
        self.assertFalse(gateway.missing_column(Rep()))

    # ------------------------------------------------------------ trying it out
    def test_check_calls_the_gateway_with_what_is_about_to_be_saved(self):
        """GET /v1/models spends nothing, which is what makes 'try it before you
        save it' free — and a dry run has to store nothing, or it would not be
        one."""
        boss = self.boss()
        url = self.dead()
        code, out = boss.post('/api/admin/gateway/check',
                              {'base': url, 'key': self.KEY})
        self.assertEqual(200, code, out)
        self.assertIs(False, out['ok'])
        self.assertEqual(url, out['base'])
        self.assertIn('Could not reach', out['message'])
        self.assertNotIn(self.KEY, json.dumps(out))
        self.assertEqual('', self.stored()['gateway_base'])
        self.assertEqual('', self.stored()['gateway_sealed'])
        self.assertEqual([{'base': url, 'ok': False}],
                         [r['detail'] for r in self.audit(boss, 'gateway.check')])

    def test_check_with_an_empty_body_asks_about_what_is_in_force(self):
        boss = self.boss()
        code, out = boss.post('/api/admin/gateway/check', {})
        self.assertEqual(200, code, out)
        self.assertEqual(base.serve.BASE, out['base'])
        self.assertIs(False, out['ok'])
        url = self.dead()
        self.assertEqual(200, boss.put('/api/admin/gateway', {'base': url})[0])
        self.assertEqual(url, boss.post('/api/admin/gateway/check', {})[1]['base'])

    def test_check_takes_a_post_and_says_so_otherwise(self):
        boss = self.boss()
        code, out = boss.get('/api/admin/gateway/check')
        self.assertEqual(405, code)
        self.assertEqual('That endpoint takes a POST.', out['error'])
        self.assertEqual(404, boss.get('/api/admin/gateway/nonsense')[0])

    def test_check_says_whether_the_gateway_serves_what_the_app_asks_for(self):
        """The sentence a gateway that answers gets back. A count on its own is
        not enough to move onto a new gateway: the app asks for two models by id,
        and one that serves neither passes this check and then fails every
        generation. So the check names the agents that are not there — by the
        agents' names, because the reply goes to a browser — and hands back the
        gateway's own list, which is what turns 'not in its list' into a choice.

        No socket: the reply the gateway would have sent is handed straight to
        gateway.check, which is the only way to exercise the 200 branch in a
        suite that is not allowed to reach a real gateway."""
        from server import hclient

        def answering(models):
            def get(url, headers=None, timeout=None, tries=1):
                self.assertTrue(url.endswith('/v1/models'))
                return hclient.Reply(200, {'data': [{'id': m} for m in models]})
            return get

        was = hclient.get
        try:
            hclient.get = answering(list(config.MODELS) + ['something-else'])
            ok, said, got = gateway.check('https://gw.example', 'sk-' + 'x' * 20)
            self.assertIs(True, ok)
            self.assertIn('3 models', said)
            self.assertIn('both agents are among them', said)
            # the ids come back beside the sentence rather than inside it
            self.assertEqual(list(config.MODELS) + ['something-else'], got)

            hclient.get = answering(['a-model', 'another-model'])
            ok, said, got = gateway.check('https://gw.example', 'sk-' + 'x' * 20)
            self.assertIs(True, ok)          # it answered; it is the wrong one
            self.assertIn('cognix-mind-v1 and cognix-apex-v2 are not in its list',
                          said)
            self.assertEqual(['a-model', 'another-model'], got)
            for real in config.MODELS:       # never, in either branch
                self.assertNotIn(real, said)

            hclient.get = answering([config.MODELS[0]])
            self.assertIn('cognix-apex-v2 is not in its list',
                          gateway.check('https://gw.example', 'sk-' + 'x' * 20)[1])

            # ...and judged against a mapping that has been typed and not saved,
            # which is what makes the button worth pressing before the save
            hclient.get = answering(['a-model', 'another-model'])
            said = gateway.check('https://gw.example', 'sk-' + 'x' * 20,
                                 want={'cognix-mind-v1': 'a-model',
                                       'cognix-apex-v2': 'another-model'})[1]
            self.assertIn('both agents are among them', said)
        finally:
            hclient.get = was

    # --------------------------------------------------------- and it is spent
    def test_a_key_saved_here_is_what_a_model_call_actually_spends(self):
        """The whole point of the screen. With nothing in the environment the
        call is refused before it goes anywhere; with a key stored from the
        console the same call gets through — to a dead port, so the 502 is the
        proof it was tried and the proof that nothing was spent."""
        boss = self.boss()
        base.serve.KEY = ''
        gateway.forget()
        c = base.Client()
        c.boot()
        code, out = self.ask(c)
        self.assertEqual(503, code, out)
        self.assertIn('COGNIX_KEY', out['error']['message'])
        url = self.dead()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': url, 'key': self.KEY})[0])
        code, out = self.ask(c)
        self.assertEqual(502, code, out)
        self.assertIn('unreachable', out['error']['message'])
        self.assertNotIn(self.KEY, json.dumps(out))
        self.assertEqual([], [ln for ln in base.NET['lines'] if self.KEY in ln])

    def test_a_new_key_is_in_force_within_the_minute_and_not_a_restart(self):
        """The row is cached for a minute, and the write drops that copy — the
        same mechanism the sign-up switch uses, and the reason none of this
        needs a redeploy."""
        boss = self.boss()
        first, second = self.dead(), self.dead()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': first, 'key': self.KEY})[0])
        self.assertEqual(first, gateway.effective()[0])
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'base': second})[0])
        self.assertEqual(second, gateway.effective()[0])
        self.assertEqual(self.KEY, gateway.effective()[1])

    # ------------------------------------------------------- and which model
    # The third thing on that row. The agents are the product and the vendor ids
    # behind them are a default: a gateway that spells the same model
    # differently is a save here rather than a code change. Everything below is
    # about that being true per agent, and about the agents' names still being
    # the only ones anything outside the console is told.
    MINE = 'their-name-for-it'
    MINE2 = 'and-this-other-one'

    def rows(self, out):
        return dict((m['name'], m) for m in out['gateway']['models'])

    def test_with_nothing_stored_every_agent_is_on_the_id_it_ships_with(self):
        out = self.gw(self.boss())
        g = out['gateway']
        # one entry per agent, always, in the order the product lists them
        self.assertEqual(list(config.AGENTS), [m['name'] for m in g['models']])
        got = self.rows(out)
        for name in config.AGENTS:
            self.assertEqual(config.ALIAS[name], got[name]['id'])
            self.assertEqual(config.ALIAS[name], got[name]['built_in'])
            self.assertEqual('built-in', got[name]['source'])
        self.assertIs(False, g['models_stored'])
        self.assertIs(False, g['stored'])

    def test_one_agent_can_be_pointed_elsewhere_and_the_other_is_left_alone(self):
        """The same rule as the URL and the key: a request that names one agent
        has not said anything about the other, so a second save merges into the
        first rather than replacing it."""
        boss = self.boss()
        code, out = boss.put('/api/admin/gateway',
                             {'models': {config.AGENTS[0]: self.MINE}})
        self.assertEqual(200, code, out)
        got = self.rows(out)
        self.assertEqual(self.MINE, got[config.AGENTS[0]]['id'])
        self.assertEqual('panel', got[config.AGENTS[0]]['source'])
        self.assertEqual(config.ALIAS[config.AGENTS[0]],
                         got[config.AGENTS[0]]['built_in'])
        self.assertEqual(config.ALIAS[config.AGENTS[1]],
                         got[config.AGENTS[1]]['id'])
        self.assertEqual('built-in', got[config.AGENTS[1]]['source'])
        self.assertIs(True, out['gateway']['models_stored'])
        self.assertIs(True, out['gateway']['stored'])
        self.assertEqual({config.AGENTS[0]: self.MINE},
                         self.stored()['gateway_models'])
        # what a call would ask for, per agent
        self.assertEqual(self.MINE, gateway.models()[config.AGENTS[0]])
        self.assertEqual(config.ALIAS[config.AGENTS[1]],
                         gateway.models()[config.AGENTS[1]])
        code, out = boss.put('/api/admin/gateway',
                             {'models': {config.AGENTS[1]: self.MINE2}})
        self.assertEqual(200, code, out)
        self.assertEqual({config.AGENTS[0]: self.MINE,
                          config.AGENTS[1]: self.MINE2},
                         self.stored()['gateway_models'])

    def test_clearing_one_puts_that_agent_back_on_the_built_in_id(self):
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway', {'models': {
            config.AGENTS[0]: self.MINE, config.AGENTS[1]: self.MINE2}})[0])
        code, out = boss.put('/api/admin/gateway',
                             {'models': {config.AGENTS[0]: ''}})
        self.assertEqual(200, code, out)
        got = self.rows(out)
        self.assertEqual(config.ALIAS[config.AGENTS[0]],
                         got[config.AGENTS[0]]['id'])
        self.assertEqual('built-in', got[config.AGENTS[0]]['source'])
        self.assertEqual(self.MINE2, got[config.AGENTS[1]]['id'])
        self.assertEqual({config.AGENTS[1]: self.MINE2},
                         self.stored()['gateway_models'])

    def test_the_built_in_id_typed_in_by_hand_is_not_stored_as_an_override(self):
        """It is the default spelled out, not a choice. Storing it would pin the
        agent to an id a later build has moved on from, which is the one way this
        screen could break a deployment by being used correctly."""
        boss = self.boss()
        code, out = boss.put('/api/admin/gateway', {'models': {
            config.AGENTS[0]: config.ALIAS[config.AGENTS[0]]}})
        self.assertEqual(200, code, out)
        self.assertEqual({}, self.stored()['gateway_models'])
        self.assertIs(False, out['gateway']['models_stored'])
        self.assertEqual('built-in', self.rows(out)[config.AGENTS[0]]['source'])

    def test_an_agent_this_build_does_not_have_is_refused_not_ignored(self):
        """A typo in the name would otherwise look exactly like a save that
        worked, and the agent would go on asking for the old id."""
        boss = self.boss()
        code, out = boss.put('/api/admin/gateway',
                             {'models': {'cognix-mind-v9': self.MINE}})
        self.assertEqual(400, code, out)
        self.assertEqual('models', out.get('field'))
        self.assertIn('not an agent this build has', out['error'])
        for name in config.AGENTS:
            self.assertIn(name, out['error'])
        code, out = boss.put('/api/admin/gateway', {'models': {}})
        self.assertEqual(400, code, out)
        code, out = boss.put('/api/admin/gateway', {'models': 'nonsense'})
        self.assertEqual(400, code, out)
        self.assertEqual({}, self.stored()['gateway_models'])

    def test_an_id_that_could_not_be_sent_is_refused_and_the_agent_is_named(self):
        """This value goes into a JSON body and a log line. A space or a line
        break in it is refused rather than trimmed, and over-long is refused
        rather than truncated — a model id with its tail cut off is a gateway
        saying no to a name nobody typed."""
        boss = self.boss()
        for bad in ('has a space', 'a-model\r\nx: y', 'x' * 81, 'what?'):
            code, out = boss.put('/api/admin/gateway',
                                 {'models': {config.AGENTS[0]: bad}})
            self.assertEqual(400, code, bad)
            self.assertEqual(config.AGENTS[0], out.get('field'), bad)
        self.assertEqual({}, self.stored()['gateway_models'])
        # ...and the shapes a real gateway actually uses are not refused
        for good in ('vendor/model-name', 'model-name:latest', 'a_model.1-2'):
            code, out = boss.put('/api/admin/gateway',
                                 {'models': {config.AGENTS[0]: good}})
            self.assertEqual(200, code, good)
            self.assertEqual(good, self.stored()['gateway_models'][config.AGENTS[0]])

    def test_the_mapping_is_in_the_audit_row_and_the_key_still_is_not(self):
        boss = self.boss()
        uid = self.uid_of(boss)
        self.assertEqual(200, boss.put('/api/admin/gateway', {
            'key': self.KEY, 'models': {config.AGENTS[0]: self.MINE}})[0])
        said = [r['detail'] for r in self.audit(boss, 'gateway.change')]
        self.assertEqual([{'base': None, 'key': config.mask(self.KEY),
                           'cleared': False,
                           'models': {config.AGENTS[0]: self.MINE},
                           'updated_by': uid}], said)
        self.assertNotIn(self.KEY, json.dumps(said))

    def outgoing(self):
        """A stand-in for the one method that opens a socket, so the whole proxy
        edge can be exercised without one. It returns what a gateway would have
        answered, quoting back the model it was asked for — which is the shape
        that matters here, because that quote is what has to come back out of the
        proxy as the agent's name."""
        seen = []

        def fake(handler, upstream, body, base='', key=''):
            got = json.loads(body.decode('utf-8'))
            seen.append(got)
            return (json.dumps({
                'id': 'msg_1', 'model': got.get('model'),
                'content': [{'type': 'text', 'text': 'ok'}],
                'usage': {'input_tokens': 1, 'output_tokens': 2},
            }).encode('utf-8'), 200, None)
        return seen, fake

    def test_a_call_for_an_agent_asks_for_the_id_it_has_been_pointed_at(self):
        """The whole point of the card. The app asks for the agent by name, the
        proxy turns that into whatever this deployment's gateway calls it, and
        the answer comes back as the agent's name either way — so a remap is
        invisible to every page and to every stored chat."""
        boss = self.boss()
        seen, fake = self.outgoing()
        was = base.serve.Handler._upstream
        base.serve.Handler._upstream = fake
        try:
            self.assertEqual(200, boss.put('/api/admin/gateway',
                                           {'base': self.dead(),
                                            'key': self.KEY})[0])
            c = base.Client()
            c.boot()
            code, out = self.ask(c)                 # nothing stored: the default
            self.assertEqual(200, code, out)
            self.assertEqual(config.ALIAS[config.AGENTS[0]], seen[-1]['model'])
            self.assertEqual(config.AGENTS[0], out['model'])
            self.assertEqual(200, boss.put('/api/admin/gateway', {
                'models': {config.AGENTS[0]: self.MINE}})[0])
            code, out = self.ask(c)
            self.assertEqual(200, code, out)
            self.assertEqual(self.MINE, seen[-1]['model'])
            # ...and the browser is told the agent's name, not that one
            self.assertEqual(config.AGENTS[0], out['model'])
            self.assertNotIn(self.MINE, json.dumps(out))
        finally:
            base.serve.Handler._upstream = was

    def test_an_id_nothing_is_pointed_at_any_more_is_refused_upstream(self):
        """An id that used to be mapped is not accepted once the deployment has
        been pointed somewhere else — but the pair this build ships with always
        is, because a chat saved by a version that recorded vendor ids has to
        stay re-runnable."""
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'models': {config.AGENTS[0]: self.MINE}})[0])
        allowed = gateway.allowed()
        for real in config.MODELS:
            self.assertIn(real, allowed)
        self.assertIn(self.MINE, allowed)
        self.assertNotIn(self.MINE2, allowed)
        self.assertEqual(config.AGENTS[0], gateway.agent_of(self.MINE))
        self.assertEqual(config.AGENTS[0], gateway.agent_of(config.AGENTS[0]))
        self.assertEqual('', gateway.agent_of(self.MINE2))
        c = base.Client()
        c.boot()
        code, out = c.call('POST', '/gw/v1/messages',
                           {'model': self.MINE2, 'max_tokens': 16,
                            'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(400, code, out)
        self.assertIn('model not allowed', out['error']['message'])

    def test_a_remap_is_not_something_a_page_can_see(self):
        """/gw/health is readable by any page, so it names agents and never ids —
        including the id an administrator has just typed in."""
        boss = self.boss()
        self.assertEqual(200, boss.put('/api/admin/gateway',
                                       {'models': {config.AGENTS[0]: self.MINE}})[0])
        c = base.Client()
        code, out = c.get('/gw/health')
        self.assertEqual(200, code, out)
        self.assertEqual(list(config.AGENTS), out['models'])
        got = json.dumps(out) + json.dumps(c.boot()[1])
        for word in (self.MINE,) + tuple(config.MODELS):
            self.assertNotIn(word, got)






class Numbers(Console):
    """The four read-only screens. All of the arithmetic happens in Postgres —
    adding up a month of usage in this process would mean fetching a month of
    usage into this process — so what is tested here is that the panel asks the
    right question and reads the answer by the right name."""

    def event(self, uid, tokens, when=None):
        stub.DB['usage_events'].append({
            'id': len(stub.DB['usage_events']) + 1, 'user_id': uid,
            'kind': 'map', 'model': 'claude-opus-5-thinking',
            'prompt_tokens': 0, 'completion_tokens': tokens,
            'total_tokens': tokens, 'ms': 20, 'ok': True, 'note': '',
            'created_at': when or stub.iso()})

    def test_the_overview_counts_what_is_there(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        self.made(a, title='One')
        self.made(a, title='Two')
        self.event(self.uid_of(a), 300)
        code, out = boss.get('/api/admin/overview')
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['users'])
        self.assertEqual(1, out['admins'])
        self.assertEqual(0, out['suspended'])
        self.assertEqual(2, out['chats'])
        self.assertEqual(300, out['tokens_month'])
        self.assertEqual('cloud', out['mode'])
        self.assertEqual(stub.iso()[:7], out['month'])
        self.assertIn('admin.bootstrap', [r['action'] for r in out['recent']])
        newest = out['newest']
        self.assertEqual({'ann@cognix.test', ADMIN},
                         set(p['email'] for p in newest))
        self.assertGreaterEqual(newest[0]['created_at'], newest[1]['created_at'])

    def test_usage_comes_back_a_day_at_a_time_including_the_quiet_ones(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        self.event(uid, 1234)
        code, out = boss.get('/api/admin/usage?days=7')
        self.assertEqual(200, code, out)
        self.assertEqual(7, out['days'])
        self.assertEqual(7, len(out['daily']))          # zeros draw the gap
        self.assertEqual(1234, out['daily'][-1]['tokens'])
        self.assertEqual(0, sum(d['tokens'] for d in out['daily'][:-1]))
        self.assertEqual([('ann@cognix.test', 1234)],
                         [(u['email'], u['tokens']) for u in out['users']])
        # an unreadable ?days is the default rather than a 400
        self.assertEqual(30, boss.get('/api/admin/usage?days=nonsense')[1]['days'])

    def test_the_log_pages_and_filters_by_action(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        for note in ('one', 'two'):
            self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                           {'notes': note})[0])
        code, out = boss.get('/api/admin/audit?per=1')
        self.assertEqual(200, code, out)
        self.assertEqual(1, len(out['rows']))
        self.assertEqual(3, out['total'])           # two changes and the bootstrap
        got = boss.get('/api/admin/audit?action=user.change')[1]
        self.assertEqual(2, got['total'])
        self.assertEqual(['user.change'] * 2, [r['action'] for r in got['rows']])
        # the filter is an eq. term built from a query string, and a comma there
        # would end it and start another
        self.assertEqual(0, boss.get('/api/admin/audit?action=user.change,role.'
                                     'eq.admin')[1]['total'])

    def test_the_chat_list_carries_titles_and_sizes_and_no_words(self):
        """An administrator can see that somebody has forty maps and when they
        last touched one. Reading the maps is not part of the job, and there is
        no policy that would allow it."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        b, _ = self.joined('bob@cognix.test', 'Bob')
        self.made(a, title='Hers', messages=[{'role': 'user', 'text': 'secret'}])
        self.made(b, title='His')
        code, out = boss.get('/api/admin/chats')
        self.assertEqual(200, code, out)
        self.assertEqual(2, out['total'])
        self.assertEqual({'Hers', 'His'}, set(c['title'] for c in out['chats']))
        self.assertEqual(set('id,user_id,title,tab,model,message_count,'
                             'created_at,updated_at'.split(',')),
                         set(out['chats'][0]))
        self.assertNotIn('secret', json.dumps(out))
        got = boss.get('/api/admin/chats?user=' + self.uid_of(a))[1]
        self.assertEqual(['Hers'], [c['title'] for c in got['chats']])
        code, out = boss.get('/api/admin/chats?user=not-a-uuid')
        self.assertEqual(400, code)
        self.assertEqual('user', out.get('field'))


class Trust(Console):
    """Whose token does the work.

    Everything the console reads about other people is granted to is_admin() by
    supabase/policies.sql, so it arrives as the administrator and the database
    decides. The service key bypasses all of that, and it is used in exactly
    two places: GoTrue's own admin endpoints, which have no policies to lean
    on, and the one-time promotion of the first administrator."""

    def tour(self, boss):
        for path in ('/api/admin/overview', '/api/admin/users',
                     '/api/admin/usage', '/api/admin/audit',
                     '/api/admin/settings', '/api/admin/chats'):
            self.assertEqual(200, boss.get(path)[0], path)

    def test_every_table_the_console_reads_is_read_as_the_administrator(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        del base.SEEN[:]
        self.tour(boss)
        self.assertEqual(200, boss.get('/api/admin/users/' + uid)[0])
        self.assertEqual(200, boss.call('PATCH', '/api/admin/users/' + uid,
                                       {'notes': 'seen'})[0])
        self.assertEqual(200, boss.put('/api/admin/settings',
                                       {'maintenance': False})[0])
        rest = [r for r in base.SEEN if r[1].startswith('/profiles')
                or r[1].startswith('/chats') or r[1].startswith('/audit_log')
                or r[1].startswith('/app_settings') or r[1].startswith('/rpc/')]
        self.assertTrue(rest)
        # app_settings is the one row a visitor with no account may read, and
        # the policy on it says exactly that. A guest model call has to resolve
        # the gateway columns off it before the call can go anywhere, so that
        # read is one shared fetch made with the anon key — including when the
        # caller happens to be an administrator, which is why saving a setting
        # (that clears the cache) can be followed by an anon read of it. What
        # the console does to that row that nobody else can is write it.
        for method, path, who in rest:
            if method == 'GET' and path.startswith('/app_settings'):
                self.assertIn(who, ('anon', 'authenticated'), path)
                continue
            self.assertEqual('authenticated', who, (method, path))
        wrote = [r for r in rest
                 if r[1].startswith('/app_settings') and r[0] != 'GET']
        self.assertTrue(wrote)

    def test_the_service_key_is_only_ever_gotrues_own_admin_endpoints(self):
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        uid = self.uid_of(a)
        del base.SEEN[:]
        self.tour(boss)
        self.assertEqual(200, boss.get('/api/admin/users/' + uid)[0])
        self.assertEqual(200, boss.post('/api/admin/users/%s/confirm' % uid)[0])
        self.assertEqual(200, boss.post('/api/admin/invite',
                                       {'email': 'new@cognix.test'})[0])
        self.assertEqual([('GET', '/admin/users/' + uid, 'service'),
                          ('PUT', '/admin/users/' + uid, 'service'),
                          ('POST', '/invite', 'service')], self.as_service())
        self.assertEqual(self.as_service(), self.gotrue_admin())

    def test_listing_somebodys_chats_is_not_reading_them(self):
        """chats_read_admin grants a select across the whole chats table, and
        there is deliberately no such policy on messages or maps. The API adds
        the owner filter anyway, so the two halves have to both be wrong."""
        boss = self.boss()
        a, _ = self.joined('ann@cognix.test', 'Ann')
        chat = self.made(a, title='Hers',
                         messages=[{'role': 'user', 'text': 'secret'}])
        self.assertEqual(['Hers'], [c['title'] for c in
                                    boss.get('/api/admin/chats')[1]['chats']])
        code, out = boss.get('/api/data/chats/' + chat['id'])
        self.assertEqual(404, code)
        self.assertEqual('That chat is not there.', out['error'])
        self.assertEqual([], boss.get('/api/data/bootstrap')[1]['chats'])
        self.assertEqual([], [r for r in base.SEEN
                              if r[1].startswith('/messages')
                              and r[2] == 'service'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
