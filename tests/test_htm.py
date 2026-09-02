#!/usr/bin/env python3
"""htm's whitespace rule, checked over every template in the front-end.

htm drops a whitespace run *containing a newline* where it touches a `${field}`;
an inline space with no newline survives. Measured against the vendored htm in a
browser, not assumed:

    `<p>a\\n  ${X} b</p>`  ->  ["a", X, " b"]     the newline run is gone
    `<p>a ${X}\\n  b</p>`  ->  ["a ", X, "b"]     the inline space stays

So a word at the end of a source line with `${…}` at the start of the next line
renders glued. That shipped twice — "tallest isnothing yet" in the usage chart's
caption and "2rows" in the console's pager — which is why it is a test and not a
habit. Attribute positions are unaffected, so this walks each template literal
and only judges boundaries that are in text position.

The check is deliberately conservative: a field whose source holds a string
literal that starts or ends with a space is carrying its own padding, and is
left alone.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIRS = ('app/src', 'app/auth', 'app/admin')


def read(p):
    with open(p, 'r', encoding='utf-8') as fh:
        return fh.read()


def files():
    for d in DIRS:
        base = os.path.join(ROOT, *d.split('/'))
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if f.endswith('.js'):
                yield os.path.join(base, f)


def _str_end(src, i, q):
    """Index just past the closing quote of the literal that starts at i."""
    i += 1
    while i < len(src):
        if src[i] == '\\':
            i += 2
        elif src[i] == q:
            return i + 1
        else:
            i += 1
    return i


def _skip_comment(src, i):
    """Index just past the comment starting at i, or None if none starts there."""
    if src.startswith('//', i):
        j = src.find('\n', i)
        return len(src) if j < 0 else j
    if src.startswith('/*', i):
        j = src.find('*/', i + 2)
        return len(src) if j < 0 else j + 2
    return None


def _field(src, i):
    """Read one ${…}. Returns (source, index past the closing brace, nested).

    `nested` is every template literal found inside the expression — the map
    callbacks in these views are full of them, and each one gets judged too.
    """
    start, depth, n, nested = i, 0, len(src), []
    while i < n:
        c = src[i]
        j = _skip_comment(src, i)
        if j is not None:
            i = j
        elif c == '}' and depth == 0:
            return src[start:i], i + 1, nested
        elif c == '{':
            depth += 1
            i += 1
        elif c == '}':
            depth -= 1
            i += 1
        elif c in '"\'':
            i = _str_end(src, i, c)
        elif c == '`':
            chunks, i, more = _template(src, i + 1)
            nested.append(chunks)
            nested.extend(more)
        else:
            i += 1
    return src[start:i], i, nested


def _template(src, i):
    """Read one template literal. Returns (chunks, index past it, nested).

    chunks alternate: ('t', text) then ('f', source, offset), starting and
    ending with a text chunk so a boundary always has two sides to look at.
    """
    chunks, buf, n, nested = [], [], len(src), []
    while i < n:
        c = src[i]
        if c == '\\':
            buf.append(src[i:i + 2])
            i += 2
        elif c == '`':
            i += 1
            break
        elif c == '$' and src.startswith('${', i):
            chunks.append(('t', ''.join(buf)))
            buf = []
            expr, i, more = _field(src, i + 2)
            chunks.append(('f', expr, i))
            nested.extend(more)
        else:
            buf.append(c)
            i += 1
    chunks.append(('t', ''.join(buf)))
    return chunks, i, nested


def templates(src):
    """Every template literal in one file, outermost and nested alike."""
    out, i, n = [], 0, len(src)
    while i < n:
        j = _skip_comment(src, i)
        if j is not None:
            i = j
        elif src[i] in '"\'':
            i = _str_end(src, i, src[i])
        elif src[i] == '`':
            chunks, i, nested = _template(src, i + 1)
            out.append(chunks)
            out.extend(nested)
        else:
            i += 1
    return out


LIT = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"")


def _lits(expr):
    """The string literals in one expression, unquoted. A field that builds its
    own text — `' ' + n + ' rows'` — carries its own spacing, and the two edges
    are judged separately: `${a ? 'x ' : ''}` pads on the right only."""
    return [m.group()[1:-1] for m in LIT.finditer(expr)]


def _pad_left(expr):
    return any(s[:1].isspace() for s in _lits(expr))


def _pad_right(expr):
    return any(s[-1:].isspace() for s in _lits(expr))


def _texty(expr):
    """True when the field plainly renders words rather than an element: no
    nested template, no tag, no call. Two elements side by side need no space
    between them, and nearly every field in these views is an element — so two
    *words* side by side is the case worth failing on, and `${n}${'rows'}` is
    what that looks like."""
    return not ('`' in expr or '<' in expr or '(' in expr)


def _ws(run):
    """What htm does with the whitespace run between two children: nothing if
    there was none, keeps it if it is inline, removes it if it has a newline."""
    if not run:
        return ('none',)
    return ('kill',) if '\n' in run else ('sp',)


def _segs(text, in_tag):
    """One text chunk cut into the bits inside a tag and the bits that are real
    text. Only the second kind renders, so only the second kind can glue."""
    segs, buf = [], []
    for c in text:
        if c == '<' and not in_tag:
            if buf:
                segs.append((False, ''.join(buf)))
            buf, in_tag = [c], True
        elif c == '>' and in_tag:
            buf.append(c)
            segs.append((True, ''.join(buf)))
            buf, in_tag = [], False
        else:
            buf.append(c)
    if buf:
        segs.append((in_tag, ''.join(buf)))
    return segs, in_tag


def _parts(chunks, src):
    """The template flattened into content, tags, and the whitespace between."""
    out, in_tag = [], False
    for ch in chunks:
        if ch[0] == 'f':
            out.append(('attr',) if in_tag
                       else ('fld', ch[1], src.count('\n', 0, ch[2]) + 1))
            continue
        segs, in_tag = _segs(ch[1], in_tag)
        for tagged, text in segs:
            if tagged:
                out.append(('attr',))
            elif not text.strip():
                out.append(_ws(text))
            else:
                out.append(_ws(text[:len(text) - len(text.lstrip())]))
                out.append(('txt', text.strip(), 0))
                out.append(_ws(text[len(text.rstrip()):]))
    return out


def glue(path):
    """Every place in one file where htm removes the only whitespace between two
    things that render. Returns [(path, line, what)]."""
    src, bad = read(path), []
    for chunks in templates(src):
        parts = _parts(chunks, src)
        for i, p in enumerate(parts):
            if p[0] != 'kill' or i == 0 or i + 1 >= len(parts):
                continue
            a, b = parts[i - 1], parts[i + 1]
            if a[0] not in ('txt', 'fld') or b[0] not in ('txt', 'fld'):
                continue
            if a[0] == 'fld' and _pad_right(a[1]):
                continue
            if b[0] == 'fld' and _pad_left(b[1]):
                continue
            if a[0] == 'fld' and b[0] == 'fld' \
               and not (_texty(a[1]) and _texty(b[1])):
                continue
            show = lambda e: (repr(e[1][-46:]) if e[0] == 'txt'
                              else '${' + ' '.join(e[1].split())[:46] + '}')
            bad.append((path, a[2] or b[2], show(a) + '  ><  ' + show(b)))
    return bad


class Glue(unittest.TestCase):
    def test_nothing_renders_glued(self):
        bad = []
        for p in files():
            bad.extend(glue(p))
        told = '\n'.join('    %s:%d  %s' % (os.path.relpath(p, ROOT), n, s)
                         for p, n, s in bad)
        self.assertEqual(bad, [], 'htm removes the newline run at these '
                         'boundaries, so the two sides render with nothing '
                         'between them. Keep the ${field} on the same source '
                         'line as the word before it, or fold the sentence into '
                         'one expression:\n' + told)


if __name__ == '__main__':
    unittest.main()
