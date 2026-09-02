#!/usr/bin/env python3
"""What has to hold before this is pushed. No server, no browser, no network.

    python -m unittest tests.test_deploy -v

The rest of the suite proves the app works on a laptop. These are the facts
that only bite once it is somewhere else: a key baked into an image anybody
with pull access can unpack, an RPC the server calls and the database has
never heard of, a table with row-level security switched on and no policy to
let its owner back in, a deploy step that runs before the tests it was meant
to wait for. Each of those is a green laptop and a broken URL, which is the
expensive kind of wrong.

Three files are read as documents rather than run: Dockerfile, .dockerignore
and cloudbuild.yaml. There is no yaml parser in the standard library and this
needs no general one — what is checked is the handful of lines that matter,
by name.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The one import of the application here, and it is for a list of names rather
# than for behaviour: the gateway columns are asserted against the tuple the
# server actually reads, so adding a sixth cannot leave this test passing on
# five. Nothing in server/gateway.py runs at import time.
sys.path.insert(0, ROOT)
from server import gateway                                          # noqa: E402

# The gateway key, and both shapes Supabase issues. Tighter than the server's
# own config.SECRET_RE on the `sk-` branch, which is deliberately loose so
# that a log line is over-redacted rather than under-redacted; here a false
# positive is a failing test, so the threshold is a key's length rather than a
# prefix's. app/styles.css has a class called `sk-composite`.
KEYISH = re.compile(r'sk-[A-Za-z0-9_\-]{16,}'
                    r'|sb_(?:secret|publishable)_[A-Za-z0-9_\-]{10,}'
                    r'|eyJ[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}')

SKIP_DIR = {'__pycache__', '.git', '.pytest_cache'}
BINARY = ('.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.docx')


def read(*parts):
    with open(os.path.join(ROOT, *parts), 'r', encoding='utf-8') as fh:
        return fh.read()


def rel(p):
    return os.path.relpath(p, ROOT).replace('\\', '/')


def text_of(p):
    if p.lower().endswith(BINARY):
        return ''
    with open(p, 'r', encoding='utf-8', errors='replace') as fh:
        return fh.read()

def steps(text):
    r"""Dockerfile instructions as (VERB, rest): comments dropped, blank lines
    dropped, `\` continuations joined into the line they continue."""
    out, held = [], ''
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        if s.endswith('\\'):
            held += s[:-1].strip() + ' '
            continue
        verb, _, rest = (held + s).partition(' ')
        held = ''
        out.append((verb.upper(), rest.strip()))
    return out


def copies(text):
    """(sources, destination) for every COPY, with the flags taken out."""
    out = []
    for verb, rest in steps(text):
        if verb != 'COPY':
            continue
        words = [w for w in rest.split() if not w.startswith('--')]
        out.append((words[:-1], words[-1]))
    return out


def carried():
    """Every file the Dockerfile puts in the image, by walking what it copies.

    This is the list that matters for anything shaped like 'could a secret
    leave the building' — not the working tree, which holds .env and the SQL
    and the test suite, none of which are in the image."""
    for src in sorted({s.rstrip('/') for pair in copies(read('Dockerfile'))
                       for s in pair[0]}):
        p = os.path.join(ROOT, src)
        if os.path.isfile(p):
            yield p
            continue
        for here, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR]
            for f in sorted(files):
                yield os.path.join(here, f)

def rules():
    """(ignored, un-ignored) from .dockerignore, in file order."""
    drop, keep = [], []
    for raw in read('.dockerignore').splitlines():
        s = raw.strip()
        if not s or s.startswith('#'):
            continue
        (keep if s.startswith('!') else drop).append(s.lstrip('!'))
    return drop, keep


def _rx(pat):
    """One .dockerignore pattern as a regex over a slash-joined path: `*` stops
    at a separator and `**` does not. That is Go's filepath.Match, which is
    what Docker uses, plus the `**` it adds to it."""
    out, i = '', 0
    while i < len(pat):
        if pat.startswith('**', i):
            i += 2
            out += '.*'
            if pat[i:i + 1] == '/':
                i += 1
                out += '(?:/)?'          # `**/x` matches a bare `x` as well
        elif pat[i] in '*?':
            out += '[^/]*' if pat[i] == '*' else '[^/]'
            i += 1
        elif pat[i] == '[' and ']' in pat[i:]:
            j = pat.index(']', i)
            out += pat[i:j + 1]
            i = j + 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile('^' + out + r'\Z')


def matched(path, pats):
    """The first pattern that covers this path — directly or by naming a
    directory above it, because excluding a directory excludes its contents."""
    parts = path.split('/')
    trail = ['/'.join(parts[:i + 1]) for i in range(len(parts))]
    for p in pats:
        rx = _rx(p.rstrip('/'))
        if any(rx.match(t) for t in trail):
            return p
    return ''

def policies_of(text):
    """(name, table, body) for every `create policy` in policies.sql. A policy
    statement runs to the first semicolon and its body holds none."""
    return [(m.group(1), m.group(2), m.group(3)) for m in
            re.finditer(r'create policy (\w+) on public\.(\w+)(.*?);', text, re.S)]


def deploy_args(text):
    """The one long line each of --set-env-vars and --set-secrets, unquoted."""
    out = {}
    for flag in ('set-env-vars', 'set-secrets'):
        m = re.search(r"--%s=([^'\"\n]+)" % flag, text)
        out[flag] = m.group(1) if m else ''
    return out


def secrets_of(text):
    """{COGNIX_NAME: 'secret-name:version'} from --set-secrets."""
    out = {}
    for pair in deploy_args(text)['set-secrets'].split(','):
        name, _, ref = pair.partition('=')
        if name.strip():
            out[name.strip()] = ref.strip()
    return out


def names_of(text):
    """Every setting cloudbuild.yaml hands the service, minus the prefix."""
    both = deploy_args(text)
    return {n[len('COGNIX_'):] for n in
            re.findall(r'\b(COGNIX_[A-Z_]+)=', both['set-env-vars'] + ',' + both['set-secrets'])}


class Container(unittest.TestCase):
    """The Dockerfile. Read as a document: what goes in, who it runs as, and
    which port it answers on."""

    def setUp(self):
        self.docker = read('Dockerfile')
        self.steps = steps(self.docker)

    def test_the_base_image_is_a_pinned_one_stage_build(self):
        froms = [rest for verb, rest in self.steps if verb == 'FROM']
        self.assertEqual(['python:3.12-slim'], froms,
                         'nothing to build, so nothing to build in a first stage')
        self.assertNotIn(':latest', self.docker,
                         'an image that moves under you is not a rollback target')

    def test_it_copies_by_name_and_every_name_is_really_there(self):
        """`COPY . .` ships whatever happens to be in the tree on the day it is
        built, which is a different set of files every day. Copying by name
        means a new file has to be added here on purpose — and it means a typo
        is a failed build rather than a missing page at runtime."""
        pairs = copies(self.docker)
        self.assertGreaterEqual(len(pairs), 3)
        for src, dst in pairs:
            self.assertNotIn('.', src, 'COPY . . in %s' % dst)
            self.assertNotIn('./', src, 'COPY ./ in %s' % dst)
            for s in src:
                self.assertTrue(os.path.exists(os.path.join(ROOT, s.rstrip('/'))),
                                'copied but not in the tree: %s' % s)
        self.assertNotIn('ADD', [verb for verb, _ in self.steps],
                         'ADD unpacks archives and fetches URLs; COPY does not')

    def test_it_carries_what_runs_and_nothing_that_does_not(self):
        got = {s.rstrip('/') for pair in copies(self.docker) for s in pair[0]}
        for want in ('serve.py', 'server', 'app'):
            self.assertIn(want, got)
        # The suite runs against the source tree in CI, the stub stands in for
        # a service that is real in production, and the SQL is run once by a
        # person in Supabase's own editor. None of the three is the server.
        for never in ('tests', 'tools', 'supabase', 'deploy', '.env', '.git'):
            self.assertNotIn(never, got)

    def test_it_does_not_run_as_root(self):
        order = [verb for verb, _ in self.steps]
        self.assertIn('USER', order)
        who = [rest for verb, rest in self.steps if verb == 'USER'][-1]
        self.assertNotEqual('root', who)
        made = [rest for verb, rest in self.steps
                if verb == 'RUN' and 'useradd' in rest]
        self.assertTrue(made, 'USER names an account that has to exist first')
        self.assertIn(who, made[0])
        self.assertLess(order.index('USER'), order.index('CMD'))
        # The copies land owned by that account rather than by root. Not for
        # writing — the process never writes — but so that a file it cannot
        # read is a permission error here and not on the first request.
        for verb, rest in self.steps:
            if verb == 'COPY':
                self.assertIn('--chown=%s:%s' % (who, who), rest)

    def test_it_answers_on_the_port_the_platform_chooses(self):
        """Cloud Run picks the port and sends it as PORT, with no prefix. If
        the server only read COGNIX_PORT it would bind 8778, answer nothing,
        and fail its health check with no clue as to why."""
        self.assertRegex(self.docker, r'(?m)^ENV PORT=8080$')
        self.assertRegex(self.docker, r'(?m)^EXPOSE 8080$')
        self.assertRegex(read('serve.py'), r"PORT = _int\(env\('PORT'")
        self.assertIn("PLAIN_OK = ('PORT', 'HOST', 'K_SERVICE')",
                      read('server', 'config.py'))

    def test_the_command_is_exec_form(self):
        """Shell form puts /bin/sh between Cloud Run's SIGTERM and python, and
        sh does not pass it on. The handler in serve.py would never run, the
        instance would be killed instead of stopped, and a request in flight
        would be dropped on every deploy."""
        self.assertEqual(['["python", "serve.py"]'],
                         [rest for verb, rest in self.steps if verb == 'CMD'])
        self.assertNotIn('ENTRYPOINT', [verb for verb, _ in self.steps])

    def test_no_key_is_in_anything_it_carries(self):
        """An image is a tarball, and this one is pushed to a registry. .env is
        kept out twice — it is not in the copy list and .dockerignore names it
        anyway. This is the other half: that no key was written into a file
        that does go in."""
        bad = []
        for p in carried():
            for m in KEYISH.finditer(text_of(p)):
                bad.append('%s: %s…' % (rel(p), m.group(0)[:14]))
        self.assertEqual([], bad, 'a key in a file that ships')


class Ignored(unittest.TestCase):
    """.dockerignore. Half of it keeps secrets out; the other half has to not
    keep the application out."""

    def setUp(self):
        self.drop, self.keep = rules()

    def test_the_env_file_cannot_reach_the_image(self):
        for want in ('.env', '.env.*'):
            self.assertIn(want, self.drop)
        self.assertIn('.env.example', self.keep)
        # Order is not decoration here: Docker applies the lines in sequence,
        # so an un-ignore before the pattern it undoes does nothing at all.
        text = read('.dockerignore')
        self.assertLess(text.index('.env.*'), text.index('!.env.example'))

    def test_the_suite_and_the_stub_and_the_sql_stay_out(self):
        """tools/fake_supabase.py answers GoTrue's and PostgREST's endpoints
        with no authentication behind them. It exists so the suite can run
        without a project; shipping it would be shipping a second, open
        implementation of the database next to the real one."""
        for want in ('tests/', 'tools/', 'supabase/'):
            self.assertIn(want, self.drop)

    def test_it_does_not_quietly_empty_the_image(self):
        """The two files have to agree. A pattern broad enough to catch a stray
        file can also swallow app/src, and the image would build, start, pass a
        health check and serve a blank page."""
        bad = []
        for p in carried():
            r = rel(p)
            hit = matched(r, self.drop)
            if hit and not matched(r, self.keep):
                bad.append('%s <- %s' % (r, hit))
        self.assertEqual([], bad, 'copied by the Dockerfile, dropped by .dockerignore')


class Build(unittest.TestCase):
    """cloudbuild.yaml. Four steps and one environment, and the order of the
    first two is the whole point of having it in a file."""

    def setUp(self):
        self.yaml = read('cloudbuild.yaml')

    def test_the_tests_run_before_anything_is_deployed(self):
        """A green build of a broken tree is worse than a red one, because it
        deploys. This is the only line that stops that."""
        ids = re.findall(r'(?m)^\s*-\s+id:\s*(\S+)', self.yaml)
        self.assertEqual(['tests', 'build', 'push', 'deploy'], ids)
        # Cloud Build runs steps in order unless a step says otherwise, and
        # `waitFor: ['-']` is how a step says 'start immediately'. On the build
        # step that would put the image alongside the suite rather than after
        # it, and the order above would mean nothing.
        self.assertNotIn('waitFor', self.yaml)

    def test_the_first_step_runs_the_whole_suite(self):
        first = self.yaml[:self.yaml.index('id: build')]
        self.assertIn("'-m', 'unittest', 'discover', '-s', 'tests'", first)
        self.assertIn('entrypoint: python', first)
        # On the interpreter the image ships, not whichever one is newest: a
        # suite that passes on 3.13 says nothing about a 3.12 container.
        self.assertIn('name: python:3.12-slim', first)
        self.assertIn('FROM python:3.12-slim', read('Dockerfile'))

    def test_no_value_is_pasted_where_a_name_belongs(self):
        """This file is committed. A key in a committed file is a key that has
        already leaked, so every secret here is a Secret Manager name and a
        version — never the thing itself."""
        self.assertEqual([], KEYISH.findall(self.yaml))
        for name, ref in sorted(secrets_of(self.yaml).items()):
            self.assertRegex(ref, r'^[a-z0-9-]+:latest$', name)

    def test_the_five_values_it_needs_are_all_named(self):
        """Four come out of Supabase and the gateway. The fifth is the cookie
        signing key: without one the process invents its own, and two instances
        would each reject the other's sessions — a sign-in that works until the
        request lands on the other instance, which is a bug report nobody can
        reproduce."""
        self.assertEqual({'COGNIX_KEY', 'COGNIX_SUPABASE_URL',
                          'COGNIX_SUPABASE_ANON_KEY', 'COGNIX_SUPABASE_SERVICE_KEY',
                          'COGNIX_SESSION_SECRET'}, set(secrets_of(self.yaml)))

    def test_every_secret_it_names_is_one_the_runbook_creates(self):
        book = read('deploy', 'cloudrun.md')
        for name, ref in sorted(secrets_of(self.yaml).items()):
            self.assertIn(ref.split(':')[0], book,
                          '%s points at a secret nothing creates' % name)

    def test_every_name_it_sets_is_one_the_server_reads(self):
        """--set-env-vars and --set-secrets each replace their whole side of the
        environment, so this file is the entire description of it. A name
        misspelt here is a setting silently left at its default."""
        src = read('server', 'config.py') + read('serve.py')
        for name in sorted(names_of(self.yaml)):
            self.assertRegex(src, r"(?:env|flag)\('%s'" % name,
                             'nothing reads COGNIX_' + name)

    def test_the_port_it_deploys_matches_the_one_the_image_exposes(self):
        self.assertIn('--port=8080', self.yaml)
        self.assertRegex(read('Dockerfile'), r'(?m)^ENV PORT=8080$')

    def test_a_model_call_is_given_longer_than_the_default(self):
        """A map is a model call and a model call is slow. Cloud Run's default
        request timeout is 300s and the gateway's own ceiling is the same, so
        anything less turns a slow answer into a 504 from the wrong layer."""
        self.assertIn('--timeout=300', self.yaml)

class Rows(unittest.TestCase):
    """The three SQL files, which are the security model. server/api.py decides
    which endpoints exist; these decide which rows come back."""

    def setUp(self):
        self.schema = read('supabase', 'schema.sql')
        self.functions = read('supabase', 'functions.sql')
        self.policies = read('supabase', 'policies.sql')
        self.tables = re.findall(r'create table if not exists public\.(\w+)',
                                 self.schema)

    def test_every_table_has_row_level_security_on(self):
        """Without this line a policy is decoration: PostgreSQL only consults
        policies on a table that has RLS enabled, and grants alone would let
        any signed-in person read the whole table."""
        self.assertIn('profiles', self.tables)
        self.assertEqual(7, len(self.tables), self.tables)
        for t in self.tables:
            self.assertRegex(self.schema,
                             r'alter table public\.%s\s+enable row level security' % t, t)

    def test_every_table_has_at_least_one_policy(self):
        """RLS on and no policy is a locked door: the owner cannot read their
        own rows either, and the app answers empty lists forever."""
        for t in self.tables:
            self.assertTrue(re.findall(r'create policy \w+ on public\.%s\b' % t,
                                       self.policies), t)

    def test_it_is_safe_to_run_twice(self):
        """These files are pasted into the Supabase SQL editor by hand, which
        means they are pasted twice. Every policy is dropped by name first, and
        the drop has to come before the create that needs it."""
        for m in re.finditer(r'create policy (\w+) on public\.(\w+)', self.policies):
            name, table = m.groups()
            drop = 'drop policy if exists %s on public.%s;' % (name, table)
            self.assertIn(drop, self.policies, name)
            self.assertLess(self.policies.index(drop), m.start(), name)
        self.assertIn('if not exists', self.schema)
        self.assertIn('create or replace function', self.functions)
        # PostgREST answers from a cached picture of the schema and the
        # policies. Without this the API keeps refusing rows a policy now
        # allows, and the SQL looks like it did not work.
        self.assertIn("notify pgrst, 'reload schema'", self.policies)

    def test_the_gateway_columns_arrive_on_a_project_that_already_ran_this(self):
        """`create table if not exists` is a no-op the second time, so a column
        written inside it never appears on a project that ran an earlier copy of
        this file — and the admin console would answer PGRST204 forever on a
        database that looks, from the SQL, entirely correct. Every one of them is
        its own `add column if not exists` for that reason, and none of them is
        in the create. The list is gateway.COLS rather than four names typed out
        here, because the names typed out here were four for exactly as long as
        it took to need a fifth."""
        create = re.search(r'create table if not exists public\.app_settings\s*\((.*?)\n\);',
                           self.schema, re.S)
        self.assertIsNotNone(create)
        # The definitions only. A comment in there is allowed to name a column —
        # `allowed_models` has one that says where the vendor id actually lives,
        # and prose about a column is not a column.
        body = re.sub(r'--[^\n]*', '', create.group(1))
        self.assertTrue(gateway.COLS)
        for col in gateway.COLS:
            self.assertRegex(self.schema,
                             r'alter table public\.app_settings\s+'
                             r'add column if not exists %s\b' % col, col)
            self.assertNotIn(col, body, col)

    def test_the_key_is_not_stored_as_something_the_row_could_give_away(self):
        """app_settings is the one table anon can read. What is in the gateway
        column is ciphertext under a secret that lives in the environment, so
        the answer to 'who can read this row' stops mattering — and the column
        is named for what it holds rather than for what it is about, because
        `gateway_key` in a table anybody can select is a thing somebody would
        later be right to panic about."""
        self.assertNotIn('gateway_key', self.schema)
        self.assertIn('ciphertext', self.schema)
        self.assertIn('seal_secret', read('server', 'gateway.py'))

    def test_the_messages_and_the_maps_have_no_administrator(self):
        """The one privacy claim the app makes that is not enforced by a check
        in Python. Support needs to see that somebody has forty maps and when
        they last opened one; it does not need to read them. So there is no
        policy that would let it, and no way to add one from the panel."""
        got = [(n, t, b) for n, t, b in policies_of(self.policies)
               if t in ('messages', 'maps')]
        self.assertEqual({'messages_own', 'maps_own'}, {n for n, _, _ in got})
        for name, _t, body in got:
            self.assertNotIn('is_admin', body, name)
            # Both halves: the row has to be yours and so does the chat it is
            # filed against, or a row could be attached to somebody else's
            # conversation by sending a different chat_id.
            self.assertIn('user_id = auth.uid()', body, name)
            self.assertIn('from public.chats c', body, name)

    def test_the_append_only_tables_cannot_be_edited(self):
        """Somebody who could delete their own usage rows would have no ceiling,
        and a log an administrator can edit is not a log. Neither table has an
        update or a delete policy — for anybody, including an administrator."""
        for name, table, body in policies_of(self.policies):
            if table in ('usage_events', 'audit_log'):
                self.assertRegex(body, r'for (?:select|insert)\b', name)
        ins = [b for n, t, b in policies_of(self.policies)
               if t == 'audit_log' and 'for insert' in b]
        self.assertTrue(ins)
        # What makes the actor column worth reading: the row is written with
        # the administrator's own token and the policy refuses any other value.
        self.assertTrue(all('actor = auth.uid()' in b for b in ins))

    def test_only_the_settings_table_answers_a_browser_with_no_session(self):
        """The sign-in page has to know whether signups are open before anybody
        has signed in. That is the whole reason anon can read anything, and
        nothing secret goes in that table."""
        for name, table, body in policies_of(self.policies):
            to = re.search(r'\bto\s+([\w, ]+)', body)
            self.assertIsNotNone(to, name)
            if 'anon' in to.group(1):
                self.assertEqual('app_settings', table, name)
                self.assertIn('for select', body, name)

    def test_every_rpc_the_server_calls_exists_and_may_be_called(self):
        """The one mismatch the laptop cannot catch, because tools/fake_supabase
        answers any RPC name it is asked for. A name that is only in the Python
        is a 404 from PostgREST on the first request in production."""
        wanted = set()
        for f in ('admin.py', 'api.py'):
            wanted |= set(re.findall(r"rpc\('(\w+)'", read('server', f)))
        self.assertGreaterEqual(len(wanted), 6, wanted)
        for fn in sorted(wanted):
            self.assertRegex(self.functions,
                             r'create or replace function public\.%s\s*\(' % fn,
                             'called and never defined: %s' % fn)
            self.assertRegex(
                self.functions,
                r'grant execute on function public\.%s\s*\([^)]*\)\s*to [^;]*authenticated' % fn,
                'defined and not granted to authenticated: %s' % fn)

    def test_the_policies_lean_on_a_function_the_file_before_them_defines(self):
        """policies.sql is run second for this reason. is_admin() reads the
        caller's own profile row, which is why the role cannot be asserted by
        the cookie or by anything the browser sends."""
        self.assertIn('public.is_admin()', self.policies)
        self.assertRegex(self.functions,
                         r'create or replace function public\.is_admin\s*\(\)')

    def test_every_definer_function_pins_its_search_path(self):
        """A security definer function runs with its owner's rights. Without a
        pinned search_path the caller can put a table of their own in front of
        the one it meant, and it will happily read that instead — with the
        owner's rights. Ten of these exist and every one is a bypass if it is
        the one that gets forgotten."""
        pinned = re.findall(r'security definer\s*\n\s*set search_path = \w+',
                            self.functions)
        self.assertEqual(self.functions.count('security definer'), len(pinned))
        self.assertGreaterEqual(len(pinned), 8)


class Browser(unittest.TestCase):
    """What the front end is allowed to know. Everything under app/ is served
    to anybody who asks for it, signed in or not."""

    def files(self):
        for base in ('src', 'admin', 'auth', 'selftest'):
            here = os.path.join(ROOT, 'app', base)
            for f in sorted(os.listdir(here)):
                if f.endswith(('.js', '.html', '.css')):
                    yield 'app/%s/%s' % (base, f), read('app', base, f)

    def test_the_browser_is_never_handed_a_key(self):
        """Not even the publishable one. This app has no Supabase client in the
        browser — no SDK, no CDN, no key in a script tag — because the anon key
        plus a browser is a direct line to PostgREST, and then the policies are
        the only thing standing between a visitor and every table. They are
        good policies. They are not meant to be the only ones."""
        for name, body in self.files():
            for word in ('sb_publishable_', 'sb_secret_', 'service_role',
                         'SUPABASE_SERVICE', 'createClient', '@supabase',
                         'supabase.co'):
                self.assertNotIn(word, body, name)
            self.assertEqual([], KEYISH.findall(body), name)

    def test_the_browser_is_never_told_which_model_answered(self):
        """The product has two agents with two names. Which vendor model is
        behind either one is a fact that lives in server/config.py — or in
        app_settings.gateway_models, where an administrator has repointed one —
        and is translated at the proxy edge, in both directions, so this holds
        for the request, for the reply, and for anything a page stores.

        Scanned over everything under app/ rather than the four source folders:
        an id in a comment, in a label table or in a saved example is the same
        disclosure as one in a fetch. The test is the point — the mapping is one
        careless label away from being back in the page, and nothing else would
        notice.

        The gateway screen in the console is the one page that prints an id, and
        it stays inside this rule because it prints what the server hands it. No
        id is written down in the console's source either, so the scan below has
        nothing to find there and an administrator still sees which model an
        agent asks for."""
        for real in ('claude-opus-4-8-thinking', 'claude-opus-5-thinking'):
            self.assertIn(real, read('server', 'config.py'))
        for here, dirs, files in os.walk(os.path.join(ROOT, 'app')):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR]
            for f in sorted(files):
                p = os.path.join(here, f)
                body = text_of(p).lower()
                for word in ('claude-opus', 'claude-3', 'anthropic'):
                    if word in body:
                        self.fail('%s names %s' % (rel(p), word))

    def test_the_browser_only_ever_calls_its_own_origin(self):
        """Every request goes to this server, which holds the keys and adds
        them. That is what lets the pages run under `connect-src 'self'` with
        no exceptions, and what makes the gateway key unreachable from a
        browser however the page is compromised."""
        for name, body in self.files():
            if not name.endswith('.js'):
                continue
            for m in re.finditer(r'fetch\(\s*([\'"`])(.*?)\1', body):
                self.assertRegex(m.group(2), r'^/', '%s: %s' % (name, m.group(2)))
            self.assertNotRegex(body, r'fetch\(\s*[\'"`]?https?:', name)
        self.assertIn("base: '/gw'", read('app', 'src', 'api.js'))
        self.assertIn("'/api/'", read('app', 'src', 'session.js'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
