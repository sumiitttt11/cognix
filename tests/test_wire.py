#!/usr/bin/env python3
"""wire.upstream — what a failed Supabase reply becomes on the way out.

    python -m unittest discover -s tests -v

Pure functions, no socket. This is the file that decides what somebody reads
when the thing behind this server says no, and one of its answers matters more
than the rest: a table that was never created is not a 404 about the row that
was asked for, it is this deployment not being finished. That case reached a
real person as `Could not find the table 'public.chats' in the schema cache`,
which is true, unactionable, and the first thing they ever saw.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import wire                                          # noqa: E402
from server.hclient import Reply                                 # noqa: E402

# What PostgREST actually sends. Verified against the live project on 2026-09-01:
# every one of the seven tables answered 404 with this exact shape before the
# SQL had been run.
NO_TABLE = {'code': 'PGRST205', 'details': None,
            'hint': "Perhaps you meant the table 'public.chats'",
            'message': "Could not find the table 'public.chats' in the schema cache"}

NO_FUNCTION = {'code': 'PGRST202', 'details': None, 'hint': None,
               'message': 'Could not find the function public.usage_this_month'
                          '(p_user) in the schema cache'}

RELATION = {'code': '42P01', 'details': None, 'hint': None,
            'message': 'relation "public.chats" does not exist'}

# and what an ordinary miss looks like, which must keep its own status
NO_ROW = {'code': 'PGRST116', 'details': 'Results contain 0 rows',
          'hint': None, 'message': 'JSON object requested, multiple (or no) rows returned'}


def out(reply, **kw):
    res = wire.upstream(reply, **kw)
    return res.code, res.obj.get('error', '')


class Unmade(unittest.TestCase):
    """The three shapes that all mean 'run the SQL files'."""

    def test_a_missing_table_asks_for_the_sql(self):
        code, msg = out(Reply(404, NO_TABLE))
        self.assertEqual(503, code)
        self.assertIn('supabase/', msg)
        self.assertNotIn('schema cache', msg)

    def test_a_missing_function_asks_for_the_sql(self):
        self.assertEqual(503, out(Reply(404, NO_FUNCTION))[0])

    def test_a_missing_relation_asks_for_the_sql(self):
        """42P01 arrives when a statement inside one of our own functions names
        a table that is not there — functions.sql run without schema.sql."""
        self.assertEqual(503, out(Reply(400, RELATION))[0])

    def test_it_names_all_four_files_in_the_order_they_run(self):
        msg = out(Reply(404, NO_TABLE))[1]
        at = [msg.index(n) for n in ('schema.sql', 'functions.sql',
                                     'policies.sql', 'seed.sql')]
        self.assertEqual(sorted(at), at, msg)
        self.assertIn('README.md', msg)

    def test_it_says_the_accounts_part_is_working(self):
        """Whoever sees this got through sign-in, so 'no database yet' has to
        read as the half that is missing, not as everything being broken."""
        self.assertIn('no database yet', out(Reply(404, NO_TABLE))[1])

    def test_the_wording_alone_is_enough(self):
        """PostgREST's message without its code — the shape a proxy or an older
        version can produce."""
        bare = {'message': "Could not find the table 'public.maps' in the schema cache"}
        self.assertEqual(503, out(Reply(404, bare))[0])
        loud = {'message': 'ERROR: relation "public.chats" does not exist'}
        self.assertEqual(503, out(Reply(500, loud))[0])

    def test_the_code_alone_is_enough(self):
        self.assertEqual(503, out(Reply(404, {'code': 'PGRST205'}))[0])


class Ordinary(unittest.TestCase):
    """Everything else has to come through unchanged, or this helper has made
    every other failure in the app harder to read instead of easier."""

    def test_a_row_that_is_not_there_is_still_a_404(self):
        code, msg = out(Reply(404, NO_ROW), fallback='Could not read that chat.')
        self.assertEqual(404, code)
        self.assertNotIn('supabase/', msg)

    def test_a_refused_sign_in_is_still_a_400(self):
        code, msg = out(Reply(400, {'error_description': 'Invalid login credentials'}),
                        fallback='Sign-in was refused.')
        self.assertEqual(400, code)
        self.assertEqual('Invalid login credentials', msg)

    def test_an_address_already_registered_is_untouched(self):
        code, msg = out(Reply(422, {'msg': 'User already registered'}))
        self.assertEqual(422, code)
        self.assertIn('already registered', msg)

    def test_an_expired_token_stays_a_401(self):
        self.assertEqual(401, out(Reply(401, {'message': 'JWT expired'}))[0])

    def test_a_policy_refusal_stays_a_403(self):
        """The one that must never be mistaken for a missing table: RLS
        answering no is a working database, not an unbuilt one."""
        rls = {'code': '42501',
               'message': 'new row violates row-level security policy for table "chats"'}
        code, msg = out(Reply(403, rls))
        self.assertEqual(403, code)
        self.assertNotIn('supabase/', msg)

    def test_someone_elses_500_is_a_502(self):
        self.assertEqual(502, out(Reply(500, {'message': 'boom'}))[0])

    def test_nothing_came_back_at_all(self):
        code, msg = out(Reply(0, {}, err='timed out'))
        self.assertEqual(504, code)
        self.assertIn('Could not reach Supabase', msg)

    def test_a_reply_with_no_body_uses_the_fallback(self):
        code, msg = out(Reply(409, {}), fallback='Somebody else got there first.')
        self.assertEqual(409, code)
        self.assertEqual('Somebody else got there first.', msg)

    def test_a_string_body_still_reads(self):
        code, msg = out(Reply(400, 'plain text refusal'))
        self.assertEqual(400, code)
        self.assertIn('plain text refusal', msg)


class Redaction(unittest.TestCase):
    def test_a_key_in_an_upstream_message_does_not_come_back_out(self):
        """Every error body goes through config.redact for this reason: the
        thing on the other end has our key and quotes what it was sent more
        often than anybody expects."""
        leak = {'message': 'bad apikey sb_secret_AAAAAAAAAAAAAAAAAAAA rejected'}
        msg = out(Reply(401, leak))[1]
        self.assertNotIn('sb_secret_AAAAAAAAAAAAAAAAAAAA', msg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
