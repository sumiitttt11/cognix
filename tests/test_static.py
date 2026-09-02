#!/usr/bin/env python3
"""Static checks over the source tree. No Node, no browser, no network.

    python -m unittest discover -s tests -v

These are the invariants that broke something once, plus the two that would be
expensive to find out about in a browser: a key committed to source, and an
import that points at a file which is not there. There is no bundler here, so
nothing else would catch a bad path until the module fails to load at runtime.
"""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, 'app')
SRC = os.path.join(APP, 'src')

# The SQL and the deployment files are read as text here for the same two
# reasons as everything else: a key must not be in one, and a control byte
# written by a tool is invisible in an editor. tests/test_deploy.py is what
# reads them for meaning.
TEXT_EXT = ('.js', '.css', '.html', '.py', '.json', '.md', '.example',
            '.sql', '.yaml', '.yml')
TEXT_NAME = ('Dockerfile', '.dockerignore')
SKIP_DIR = {'.git', '__pycache__', 'vendor', 'node_modules', '.pytest_cache'}


def walk(base=ROOT):
    for here, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for f in files:
            yield os.path.join(here, f)


def text_files():
    for p in walk():
        if p.lower().endswith(TEXT_EXT) or os.path.basename(p) in TEXT_NAME:
            yield p


def read(p):
    with open(p, 'r', encoding='utf-8') as fh:
        return fh.read()


def rel(p):
    return os.path.relpath(p, ROOT).replace('\\', '/')


class Secrets(unittest.TestCase):
    def test_no_key_in_source(self):
        """The one file allowed to hold a live key is .env, which is ignored by
        git and refused by the server. .env.example holds a placeholder."""
        bad = []
        for p in text_files():
            name = os.path.basename(p)
            if name.startswith('.env'):
                continue
            for m in re.finditer(r'sk-[A-Za-z0-9_\-]{12,}', read(p)):
                bad.append('%s: %s…' % (rel(p), m.group(0)[:12]))
        self.assertEqual([], bad, 'API key literal outside .env')

    def test_env_is_gitignored(self):
        ignored = [l.strip() for l in read(os.path.join(ROOT, '.gitignore')).splitlines()]
        self.assertIn('.env', ignored)

    def test_server_has_no_default_key(self):
        src = read(os.path.join(ROOT, 'serve.py'))
        self.assertRegex(src, r"KEY\s*=\s*env\('KEY'\)")
        self.assertNotRegex(src, r"env\('KEY',\s*'")

    def test_server_redacts_before_it_speaks(self):
        src = read(os.path.join(ROOT, 'serve.py'))
        self.assertIn('def redact(', src)
        self.assertIn('redact(fmt % a)', src)          # every log line
        self.assertIn('key[-4:]', src)                 # mask() never prints it whole


class Sources(unittest.TestCase):
    def test_python_compiles(self):
        for p in walk():
            if p.endswith('.py'):
                ast.parse(read(p), filename=p)

    def test_no_control_bytes(self):
        """sanitize.js once held literal NUL/BEL bytes written by a tool call
        that decoded its own escapes. They are invisible in an editor."""
        bad = []
        for p in text_files():
            for i, ch in enumerate(read(p)):
                if ord(ch) < 0x20 and ch not in '\n\r\t':
                    bad.append('%s @%d: U+%04X' % (rel(p), i, ord(ch)))
        self.assertEqual([], bad)

    def test_no_leftover_placeholders(self):
        mark = '@@' + 'NEXT' + '@@'      # split so this file is not a hit itself
        bad = [rel(p) for p in text_files() if mark in read(p)]
        self.assertEqual([], bad, 'chunked-write placeholder left in a file')


# `./x.js` and `../src/x.js` both: the console and the sign-in page live in
# their own directories and reach back into src/ for h.js, session.js and the
# brand. A regex that only saw `./` would silently skip every one of those.
IMPORT = re.compile(r"""\bimport\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]"""
                    r"""|\bfrom\s+['"](\.{1,2}/[^'"]+)['"]""")

PAGES = ('', 'admin', 'auth', 'selftest')       # directories holding an index.html
DIRS = ('src', 'admin', 'auth', 'selftest')     # directories holding modules


def under(*parts):
    """A path inside app/, normalised, always with forward slashes."""
    return os.path.normpath(os.path.join(*parts)).replace('\\', '/')


def js_files():
    """Every module the app has, as a path relative to app/."""
    for d in DIRS:
        for f in sorted(os.listdir(os.path.join(APP, d))):
            if f.endswith('.js'):
                yield '%s/%s' % (d, f)


def entries():
    """What each page actually loads, taken from its own <script type="module">
    rather than from a list kept by hand in this file. Four pages, four roots:
    the app, the console, sign-in, and the in-browser unit runner."""
    out = []
    for d in PAGES:
        html = read(os.path.join(APP, d, 'index.html'))
        for m in re.finditer(r'<script[^>]*type="module"[^>]*src="([^"]+)"', html):
            out.append(under(d, m.group(1)))
    return out


def deps(rel):
    """Relative specifiers this module pulls in, static and dynamic alike,
    resolved against the directory the module itself is in."""
    here = os.path.dirname(rel)
    return [under(here, a or b) for a, b in IMPORT.findall(read(os.path.join(APP, rel)))]


class ModuleGraph(unittest.TestCase):
    """There is no bundler, so nothing verifies these paths before the browser
    does. A typo in an import is a blank page."""

    def test_every_page_loads_a_module_that_is_there(self):
        got = entries()
        self.assertEqual(len(PAGES), len(got), got)
        for e in got:
            self.assertTrue(os.path.isfile(os.path.join(APP, e)), e)

    def test_every_import_resolves(self):
        bad = []
        for f in js_files():
            for spec in deps(f):
                if not os.path.isfile(os.path.join(APP, spec)):
                    bad.append('%s -> %s' % (f, spec))
        self.assertEqual([], bad)

    def test_every_module_is_reachable_from_a_page(self):
        seen, queue = set(), entries()
        while queue:
            f = queue.pop()
            if f in seen:
                continue
            seen.add(f)
            if os.path.isfile(os.path.join(APP, f)):
                queue += deps(f)
        self.assertEqual([], sorted(set(js_files()) - seen),
                         'dead module: no page reaches it')

    def test_h_js_is_the_only_react_binding(self):
        """h.js reads window.React at evaluation time. Anything else touching
        window.React directly would break the vendor-missing screen, which has
        to run before any module that needs React is imported — so the three
        files that put a root on the page are the exception, and nothing else
        is."""
        boot = ('src/h.js', 'src/main.js', 'admin/admin.js', 'auth/auth.js')
        bad = [f for f in js_files() if f not in boot
               and re.search(r'window\.(React|ReactDOM|htm)\b',
                             read(os.path.join(APP, f)))]
        self.assertEqual([], bad)


def src_files():
    """(path relative to app/, text) for every module on every page."""
    for f in js_files():
        yield f, read(os.path.join(APP, f))


class AppSources(unittest.TestCase):
    def test_no_html_injection_sinks(self):
        bad = []
        for f, s in src_files():
            for pat in (r'dangerouslySetInnerHTML\s*[=:]', r'\.innerHTML\s*=',
                        r'\.outerHTML\s*=', r'\bnew\s+Function\s*\(',
                        r'(?<![\w.])eval\s*\('):
                if re.search(pat, s):
                    bad.append('%s: %s' % (f, pat))
        self.assertEqual([], bad)

    def test_no_style_attribute_strings(self):
        """React inline styles are CSSOM writes, which is why the page can run
        under style-src 'self' with no 'unsafe-inline'. A literal style="…" in a
        template would be a style attribute, and the CSP would drop it."""
        for f, s in src_files():
            self.assertNotIn('style="', s, f)

    def test_brand_is_cognix_everywhere(self):
        """The one surviving 'noderels' is the pre-rename localStorage key, read
        once so an existing map is not orphaned by the rename. Stylesheets and
        pages are checked too: a stale name in a <title> is the one place a
        person actually reads it."""
        found = []
        pages = [(under(d, f), read(os.path.join(APP, d, f)))
                 for d in PAGES for f in sorted(os.listdir(os.path.join(APP, d)))
                 if f.endswith(('.html', '.css'))]
        for f, s in list(src_files()) + pages:
            for m in re.finditer(r'(?i)noderels', s):
                line = s[:m.start()].count('\n') + 1
                if f == 'src/store.js' and 'LS_OLD' in s.splitlines()[line - 1]:
                    continue
                found.append('%s:%d' % (f, line))
        self.assertEqual([], found)

    def test_status_strings_are_verbatim(self):
        s = read(os.path.join(SRC, 'Thinking.js'))
        self.assertIn('Cognix is using his brain to deliver a Mindmap', s)
        self.assertIn('Cognix doing some advanced type shit, you have to wait lmao', s)

    def test_one_shared_text_cap(self):
        """Every editing surface has to cap at the same number, or localStorage
        grows without a ceiling. sanitize.js owns it; nobody redefines it."""
        self.assertRegex(read(os.path.join(SRC, 'sanitize.js')),
                         r'export const TEXT_MAX = \d+')
        for f, s in src_files():
            if f != 'src/sanitize.js':
                self.assertNotRegex(s, r'(const|let|var)\s+TEXT_MAX\s*=', f)


class Stylesheet(unittest.TestCase):
    def setUp(self):
        self.css = read(os.path.join(APP, 'styles.css'))

    def test_no_bare_busy_rule(self):
        """A `.busy{position:absolute;inset:0}` layout rule collided with
        `class="model busy"` used as a state flag, and washed out the whole
        window. The overlay is `.cogveil` now; the flag keeps its name."""
        self.assertNotRegex(self.css, r'(^|[},])\s*\.busy\s*\{')
        self.assertIn('.cogveil', self.css)

    def test_focus_is_visible_everywhere(self):
        self.assertIn(':focus-visible', self.css)
        self.assertIn('outline:2px solid var(--acc)', self.css)

    def test_reduced_motion_is_honoured(self):
        self.assertIn('prefers-reduced-motion:reduce', self.css)


class Markup(unittest.TestCase):
    """Four pages, one policy. The console and the sign-in page are as exposed
    as the app is — more so in one respect, since sign-in is where a password
    is typed."""

    def pages(self):
        for d in PAGES:
            yield under(d, 'index.html'), read(os.path.join(APP, d, 'index.html'))

    def test_csp_is_strict(self):
        for name, html in self.pages():
            csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', html)
            self.assertIsNotNone(csp, '%s carries no policy' % name)
            policy = ' '.join(csp.group(1).split())
            self.assertNotIn('unsafe-inline', policy, name)
            self.assertNotIn('unsafe-eval', policy, name)
            for want in ("default-src 'none'", "script-src 'self'",
                         "connect-src 'self'", "base-uri 'none'",
                         "form-action 'none'"):
                self.assertIn(want, policy, name)

    def test_no_inline_script_or_style(self):
        for name, html in self.pages():
            self.assertEqual([], re.findall(r'<script(?![^>]*\ssrc=)[^>]*>\s*\S', html),
                             name)
            self.assertNotIn('<style', html, name)

    def test_nothing_is_loaded_from_another_origin(self):
        for name, html in self.pages():
            for m in re.finditer(r'(?:src|href)="([^"]+)"', html):
                self.assertRegex(m.group(1), r'^\.{1,2}/',
                                 '%s: %s' % (name, m.group(1)))

    def test_the_pages_that_build_on_the_app_sheet_load_it_first(self):
        """admin.css and auth.css take the colour tokens, the fields and the
        crash card from styles.css rather than restating them. Loaded the other
        way round, every one of those rules would win against the sheet that is
        meant to be the base."""
        for d in ('admin', 'auth'):
            html = read(os.path.join(APP, d, 'index.html'))
            base, own = 'href="../styles.css"', 'href="./%s.css"' % d
            self.assertIn(base, html, d)
            self.assertIn(own, html, d)
            self.assertLess(html.index(base), html.index(own), d)

    def test_it_says_what_to_do_without_javascript(self):
        for name, html in self.pages():
            self.assertIn('<noscript>', html, name)
        self.assertIn('Cognix needs JavaScript', read(os.path.join(APP, 'index.html')))


class Vendor(unittest.TestCase):
    def test_production_builds_only(self):
        for f in ('react.js', 'react-dom.js'):
            head = read(os.path.join(APP, 'vendor', f))[:400]
            self.assertIn('.production.min.js', head, f)
            self.assertNotIn('.development.js', head, f)

    def test_vendored_not_fetched(self):
        """Three pages need React and all three take it off this origin. A CDN
        would be a second origin in the policy and a third party in the path of
        every sign-in."""
        for d, up in (('', './'), ('admin', '../'), ('auth', '../')):
            html = read(os.path.join(APP, d, 'index.html'))
            for f in ('react.js', 'react-dom.js', 'htm.js'):
                self.assertIn('%svendor/%s' % (up, f), html, d or 'app')


if __name__ == '__main__':
    unittest.main(verbosity=2)
