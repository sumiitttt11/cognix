/* =====================================================================
   In-browser unit tests for the pure modules.

   There is no Node here and no bundler, so these run in the same engine and
   under the same Content-Security-Policy the app runs under. Nothing imports
   store.js: it owns the localStorage key, and a test that touched it could
   overwrite real maps.

   Open /app/selftest/ . window.__RESULTS__ holds the counts afterwards, so a
   headless check can read them without scraping the page.
   ===================================================================== */
import * as U from '../src/util.js';
import * as San from '../src/sanitize.js';
import * as T from '../src/tokens.js';
import * as M from '../src/model.js';

const R = { total: 0, pass: 0, fail: 0, failures: [] };
const groups = [];
let cur = null;

function group(name, fn){
  cur = { name, tests: [] };
  groups.push(cur);
  fn();
}
function test(name, fn){
  R.total++;
  try {
    fn();
    R.pass++;
    cur.tests.push({ name, ok: true });
  } catch (e) {
    R.fail++;
    const why = (e && e.message) || String(e);
    cur.tests.push({ name, ok: false, why });
    R.failures.push(cur.name + ' › ' + name + ' — ' + why);
  }
}

const show = v => {
  if (typeof v === 'string') return JSON.stringify(v);
  if (v && typeof v === 'object') { try { return JSON.stringify(v); } catch (e) { return '[object]'; } }
  return String(v);
};
function ok(v, m){ if (!v) throw new Error(m || 'expected truthy, got ' + show(v)); }
function no(v, m){ if (v) throw new Error(m || 'expected falsy, got ' + show(v)); }
function eq(a, b, m){
  if (a !== b) throw new Error((m ? m + ': ' : '') + show(a) + ' !== ' + show(b));
}
function deep(a, b, m){ eq(JSON.stringify(a), JSON.stringify(b), m); }
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);


/* ------------------------------------------------------------------ util */
group('util — numbers and text', () => {
  test('clamp holds both ends', () => {
    eq(U.clamp(5, 1, 3), 3);
    eq(U.clamp(-1, 1, 3), 1);
    eq(U.clamp(2, 1, 3), 2);
  });
  test('round keeps the step’s decimal places', () => {
    eq(U.round(7, 5), 5);
    eq(U.round(8, 5), 10);
    eq(U.round(0.34, 0.05), 0.35);
    eq(U.round(1.24, 0.1), 1.2);
  });
  test('cap survives an empty string', () => {
    eq(U.cap('abc'), 'Abc');
    eq(U.cap(''), '');
  });
  test('shorten cuts on a boundary and marks it', () => {
    eq(U.shorten('abc', 5), 'abc');
    eq(U.shorten('hello world', 5), 'hell…');
    eq(U.shorten('hello, world', 8), 'hello…');
  });
  test('uid is prefixed and does not repeat', () => {
    const a = U.uid('m-'), b = U.uid('m-');
    ok(a.indexOf('m-') === 0);
    ok(a !== b);
  });
  test('HEX and isCol only accept a colour', () => {
    eq(U.HEX('#abcdef'), '#abcdef');
    eq(U.HEX('bogus'), '#000000');
    eq(U.HEX(null), '#000000');
    ok(U.isCol('#fff'));
    no(U.isCol('red'));
    no(U.isCol(null));
  });
  test('isDark reads lightness, not the string', () => {
    ok(U.isDark('#000000'));
    no(U.isDark('#ffffff'));
    no(U.isDark('nope'));
  });
  test('ago and bucket label the recent past', () => {
    eq(U.ago(Date.now()), 'now');
    eq(U.ago(Date.now() - 3 * 3600e3), '3h');
    eq(U.bucket(Date.now()), 'Today');
    eq(U.bucket(Date.now() - 86400e3), 'Yesterday');
  });
});


/* The one class of bug in here that would be silent and global: a dotted path
   out of a saved blob walking into Object.prototype. Every walker is gated. */
group('util — paths cannot reach Object.prototype', () => {
  test('safeKey rejects the three that matter', () => {
    no(U.safeKey('__proto__'));
    no(U.safeKey('prototype'));
    no(U.safeKey('constructor'));
    no(U.safeKey(''));
    no(U.safeKey(null));
    ok(U.safeKey('fill'));
  });
  test('safePath checks every segment', () => {
    ok(U.safePath('node.shadow.blur'));
    no(U.safePath('node.__proto__'));
    no(U.safePath('__proto__.x'));
    no(U.safePath('a.constructor.b'));
    no(U.safePath(''));
  });
  test('setPath refuses the write and says so', () => {
    const o = {};
    no(U.setPath(o, '__proto__.pwned', 1));
    eq(({}).pwned, undefined, 'Object.prototype untouched');
    ok(U.setPath(o, 'a.b.c', 3));
    eq(o.a.b.c, 3);
    no(U.setPath(null, 'a', 1));
  });
  test('getPath returns nothing for a bad path', () => {
    eq(U.getPath({ a: { b: 2 } }, 'a.b'), 2);
    eq(U.getPath({}, '__proto__.x'), undefined);
    eq(U.getPath({}, 'a.b.c'), undefined);
  });
  test('clone drops a __proto__ key that JSON.parse made real', () => {
    const evil = JSON.parse('{"__proto__":{"pwned":1},"keep":2}');
    ok(has(evil, '__proto__'), 'fixture really has the own key');
    const c = U.clone(evil);
    no(has(c, '__proto__'));
    eq(c.keep, 2);
    eq(({}).pwned, undefined);
  });
  test('flatten skips the keys it must not emit', () => {
    const o = JSON.parse('{"__proto__":{"z":1},"a":{"b":1},"c":2}');
    deep(U.flatten(o), { 'a.b': 1, c: 2 });
  });
  test('mergeDefaults counts own keys only', () => {
    const inherited = Object.create({ a: 5 });
    const r = U.mergeDefaults({ a: 1, b: { c: 2 } }, inherited);
    ok(has(r, 'a'), 'an inherited value must not mask a default');
    eq(r.a, 1);
    eq(r.b.c, 2);
  });
});


/* ------------------------------------------------------------- sanitize */
group('sanitize — one line, one paragraph, one number', () => {
  test('str collapses whitespace and caps length', () => {
    eq(San.str('  a   b  '), 'a b');
    eq(San.str('a\nb'), 'a b');
    ok(San.str('x'.repeat(300), 240).length <= 240);
    eq(San.str(null, 10, 'fallback'), 'fallback');
    eq(San.str(42), '42');
  });
  test('str strips what would corrupt a label', () => {
    eq(San.str('a' + String.fromCharCode(0) + 'b'), 'a b');
    eq(San.str('a' + String.fromCharCode(0x200b) + 'b'), 'a b');
    eq(San.str('a' + String.fromCharCode(0x202e) + 'b'), 'a b');
  });
  test('str cuts on a word boundary when it can', () => {
    eq(San.str('alpha beta gamma', 12), 'alpha beta');
    eq(San.str('abcdefghijklmnop', 8).length, 8, 'no boundary: hard cut');
  });
  test('para keeps newlines and collapses blank runs', () => {
    eq(San.para('a\n\n\n\nb'), 'a\n\nb');
    eq(San.para('a\r\nb'), 'a\nb');
    eq(San.para('  a  \n  b  '), 'a\nb');
    eq(San.para('', 0, 'none'), 'none');
  });
  test('num clamps and falls back rather than yielding NaN', () => {
    eq(San.num('12', 0, 10, 5), 10);
    eq(San.num(-3, 0, 10, 5), 0);
    eq(San.num('x', 0, 10, 5), 5);
    eq(San.num(NaN, 0, 10, 5), 5);
    eq(San.num(Infinity, 0, 10, 5), 5);
  });
  test('lines pads to exactly n so callers can index', () => {
    deep(San.lines(['a', '', 'b'], 3, 10, i => 'f' + i), ['a', 'b', 'f2']);
    deep(San.lines(null, 2, 10, () => 'z'), ['z', 'z']);
    eq(San.lines(['a', 'b', 'c', 'd'], 3, 10, () => 'z').length, 3);
  });
  test('TEXT_MAX is the one shared ceiling', () => {
    eq(San.TEXT_MAX, 240);
  });
});

group('sanitize — what the gateway sends back', () => {
  test('mapContent always has six branches of three', () => {
    const c = San.mapContent({});
    eq(c.title, 'Untitled map');
    T.BKEYS.forEach(k => eq(c[k].length, 3, k));
    ok(c.problem[0].indexOf('left this point empty') > 0);
  });
  test('mapContent keeps what is there and fills the rest', () => {
    const c = San.mapContent({ title: 'T', problem: ['p1'] }, 'fb');
    eq(c.title, 'T');
    eq(c.problem[0], 'p1');
    eq(c.problem.length, 3);
    eq(San.mapContent({}, 'fb').title, 'fb');
  });
  test('plan gives PlanView four arrays whatever arrives', () => {
    const p = San.plan(undefined);
    ok(Array.isArray(p.sections) && Array.isArray(p.weeks));
    ok(Array.isArray(p.risks) && Array.isArray(p.next));
    ok(p.summary.length > 0);
    const q = San.plan({ sections: [{ h: 'H', b: ['x'] }, { h: 'empty', b: [] }] });
    eq(q.sections.length, 1, 'a section with no body is dropped');
    eq(q.sections[0].h, 'H');
  });
  test('plan truncates rather than trusting a count', () => {
    const many = { next: Array.from({ length: 40 }, (x, i) => 'step ' + i) };
    eq(San.plan(many).next.length, 6);
  });
});

group('sanitize — what localStorage holds', () => {
  test('overrides keeps primitives on safe paths only', () => {
    const o = San.overrides({ 'node.fill': '#fff', 'node.radius': 4, 'node.on': true,
      '__proto__.x': 1, 'a.constructor': 2, nested: { a: 1 }, bad: undefined });
    deep(o, { 'node.fill': '#fff', 'node.radius': 4, 'node.on': true });
  });
  test('overrides repairs a non-finite number', () => {
    eq(San.overrides({ 'node.radius': Infinity })['node.radius'], 0);
  });
  test('style drops anything that is not a plain tree', () => {
    deep(San.style({ node: { fill: '#fff' } }), { node: { fill: '#fff' } });
    deep(San.style(null), {});
    deep(San.style(undefined), {});
    deep(San.style({ list: [1, 2] }), {});
    deep(San.style(JSON.parse('{"__proto__":{"z":1},"node":{"fill":"#fff"}}')),
         { node: { fill: '#fff' } });
  });
});

group('sanitize — a saved map cannot hang the tree walk', () => {
  test('a map with no usable nodes is null, not half a map', () => {
    eq(San.map(null), null);
    eq(San.map({}), null);
    eq(San.map({ nodes: [] }), null);
    eq(San.map({ nodes: [{ text: 'no id' }] }), null);
  });
  test('a root keeps its shape and gets its defaults', () => {
    const m = San.map({ nodes: [{ id: 'n-root', kind: 'root', text: 'T' }] });
    eq(m.nodes.length, 1);
    eq(m.nodes[0].parent, null);
    eq(m.nodes[0].side, 'R');
    eq(m.title, 'Untitled map');
    eq(m.version, 1);
    ok(m.map_id.length > 0);
  });
  test('duplicate ids collapse to the first', () => {
    const m = San.map({ nodes: [{ id: 'a', kind: 'root', text: 'first' },
                                { id: 'a', kind: 'leaf', text: 'second' }] });
    eq(m.nodes.length, 1);
    eq(m.nodes[0].text, 'first');
  });
  test('a missing parent is reattached', () => {
    const m = San.map({ nodes: [{ id: 'n-root', kind: 'root' },
                                { id: 'x', kind: 'leaf', parent: 'ghost' }] });
    eq(m.nodes[1].parent, 'n-root');
  });
  test('a parent cycle is broken, so Layers terminates', () => {
    const m = San.map({ nodes: [{ id: 'n-root', kind: 'root' },
                                { id: 'a', kind: 'leaf', parent: 'b' },
                                { id: 'b', kind: 'leaf', parent: 'a' }] });
    eq(m.nodes.length, 3);
    const by = {};
    m.nodes.forEach(n => by[n.id] = n);
    m.nodes.forEach(n => {
      let up = by[n.parent], hops = 0;
      while (up) { up = by[up.parent]; ok(hops++ <= m.nodes.length, 'cycle from ' + n.id); }
    });
  });
  test('a node keeps only overrides it is allowed to keep', () => {
    const m = San.map({ nodes: [{ id: 'n-root', kind: 'root',
      style: { 'node.fill': '#fff', '__proto__.x': 1 } }] });
    deep(m.nodes[0].style, { 'node.fill': '#fff' });
  });
  test('a saved map says which agent drew it, whatever it recorded', () => {
    /* Maps saved by an older version put the vendor model id in `source`, and
       that id is the one thing this app does not repeat. Only one agent draws
       maps, so anything that is not the offline builder is that agent — no
       lookup, and no list of ids in a file the browser is handed. */
    const of = v => San.map({ source: v, nodes: [{ id: 'n-root', kind: 'root' }] }).source;
    eq(of('local'), 'local');
    eq(of(''), 'local');
    eq(of(undefined), 'local');
    eq(of('cognix-mind-v1'), 'cognix-mind-v1');
    eq(of('some-model-nobody-here-has-heard-of'), 'cognix-mind-v1');
  });
});

group('sanitize — the session list', () => {
  test('nothing readable yields an empty list, not a crash', () => {
    deep(San.sessions(null), { curId: null, sessions: [] });
    deep(San.sessions({ sessions: 'not an array' }), { curId: null, sessions: [] });
  });
  test('curId has to name a session that survived', () => {
    const s = San.sessions({ curId: 'gone', sessions: [{ id: 's1', sheet: {} }] });
    eq(s.curId, 's1');
    eq(San.sessions({ curId: 's1', sessions: [{ id: 's1', sheet: {} }] }).curId, 's1');
  });
  test('a session with no id is dropped rather than repaired', () => {
    eq(San.sessions({ sessions: [{ sheet: {} }, { id: 'ok', sheet: {} }] }).sessions.length, 1);
  });
});


/* -------------------------------------------------------------- tokens */
group('tokens — resolution order', () => {
  test('a plain token comes from the sheet', () => {
    eq(T.v(T.DEFAULT_STYLE, null, 'node.fill'), '#ffffff');
    eq(T.v(T.DEFAULT_STYLE, null, 'node.shadow.blur'), 3);
  });
  test('a node override wins over the sheet', () => {
    eq(T.v(T.DEFAULT_STYLE, { style: { 'node.fill': '#000000' } }, 'node.fill'), '#000000');
  });
  test('text.* reads the group that matches the kind', () => {
    eq(T.v(T.DEFAULT_STYLE, { kind: 'leaf' }, 'text.size'), 11.5);
    eq(T.v(T.DEFAULT_STYLE, { kind: 'root' }, 'text.size'), 14);
    eq(T.v(T.DEFAULT_STYLE, { kind: 'branch' }, 'text.size'), 12.5);
  });
  test('a root reads node.* from the root group', () => {
    eq(T.v(T.DEFAULT_STYLE, { kind: 'root' }, 'node.fill'), '#1f1e1d');
    eq(T.v(T.DEFAULT_STYLE, { kind: 'root' }, 'node.strokeW'), 1, 'falls through when root has none');
  });
  test('an unsafe path resolves to nothing', () => {
    eq(T.v(T.DEFAULT_STYLE, null, '__proto__.x'), undefined);
    eq(T.v(T.DEFAULT_STYLE, null, 'node.constructor'), undefined);
  });
  test('the six branches are fixed and unique', () => {
    eq(T.BRANCHES.length, 6);
    eq(T.BKEYS.length, 6);
    eq(new Set(T.BKEYS).size, 6);
    T.BKEYS.forEach(k => ok(T.DEFAULT_STYLE.branch[k], 'colour for ' + k));
  });
});

group('tokens — the inline style a box gets', () => {
  const leaf = { kind: 'leaf', branch: 'problem', x: 10, y: 20, style: {} };
  const root = { kind: 'root', branch: null, x: 0, y: 0, style: {} };
  test('position and typography come out as CSS values', () => {
    const s = T.nodeCss(T.DEFAULT_STYLE, leaf);
    eq(s.left, '10px');
    eq(s.top, '20px');
    eq(s.fontSize, '11.5px');
    eq(s.opacity, 1);
    ok(s.fontFamily.indexOf('Inter') === 0);
  });
  test('the branch tint is a left bar on a leaf and never on the root', () => {
    eq(T.nodeCss(T.DEFAULT_STYLE, leaf).borderLeft,
       '3px solid ' + T.DEFAULT_STYLE.branch.problem);
    eq(T.nodeCss(T.DEFAULT_STYLE, root).borderLeft, undefined);
  });
  test('a width of 0 means auto, so no width is emitted', () => {
    no(has(T.nodeCss(T.DEFAULT_STYLE, leaf), 'width'));
    const wide = { kind: 'leaf', branch: 'problem', x: 0, y: 0, style: { 'node.width': 180 } };
    eq(T.nodeCss(T.DEFAULT_STYLE, wide).width, '180px');
  });
  test('canvasBg only draws a grid when one is asked for', () => {
    no(has(T.canvasBg({ canvas: { bg: '#fff', grid: 'none', gridSize: 24 } }), 'backgroundImage'));
    const d = T.canvasBg({ canvas: { bg: '#fff', grid: 'dots', gridSize: 24 } });
    eq(d.backgroundSize, '24px 24px');
    ok(T.canvasBg({ canvas: { bg: '#fff', grid: 'lines', gridSize: 8 } }).backgroundImage);
  });
});

group('tokens — presets', () => {
  test('a preset patches a copy and never the defaults', () => {
    eq(T.presetStyle('neon').canvas.bg, '#101015');
    eq(T.DEFAULT_STYLE.canvas.bg, '#ffffff', 'DEFAULT_STYLE was mutated');
    eq(T.presetStyle('neon').branch.problem, '#4da3ff');
    eq(T.DEFAULT_STYLE.branch.problem, '#3b7dd8');
  });
  test('an unknown preset falls back to the defaults', () => {
    deep(T.presetStyle('nope'), T.presetStyle('default'));
    deep(T.presetStyle('default'), T.DEFAULT_STYLE);
  });
  test('every preset id is unique and resolves', () => {
    eq(new Set(T.PRESETS.map(p => p.id)).size, T.PRESETS.length);
    T.PRESETS.forEach(p => ok(T.presetStyle(p.id).node.fill, p.id));
  });
});


/* --------------------------------------------------------------- model */
group('model — reading the idea', () => {
  test('the noun is picked out of the sentence', () => {
    eq(M.readIdea('A private AI hub for banks that cannot use public AI').noun, 'hub');
    eq(M.readIdea('a same-day medicine delivery app for tier-2 cities').noun, 'app');
    eq(M.readIdea('something with no keyword in it at all').noun, 'product');
  });
  test('the audience is the text after the last "for"', () => {
    eq(M.readIdea('an app for tracking workouts for busy parents').who, 'busy parents');
    eq(M.readIdea('a tool with no audience').who, 'small teams');
  });
  test('an "idea:" prefix is stripped', () => {
    eq(M.readIdea('Idea: a portal for schools').full, 'a portal for schools');
  });
  test('a title is short enough to fit the root box', () => {
    const i = M.readIdea('x'.repeat(400) + ' platform for everyone');
    ok(i.title.length <= 38);
  });
  test('nothing at all still produces a usable shape', () => {
    const i = M.readIdea('');
    ok(i.full.length > 0);
    ok(i.who.length > 0);
    eq(i.noun, 'product');
  });
});

group('model — building the map', () => {
  const content = M.compose(M.readIdea('A hub for banks'));
  const fresh = () => M.buildMap('T', 'topic', content, null, 'local');

  test('compose fills all six branches with three lines', () => {
    T.BKEYS.forEach(k => eq(content[k].length, 3, k));
  });
  test('a map is one root, six branches and eighteen leaves', () => {
    const m = fresh();
    eq(m.nodes.length, 25);
    eq(m.nodes.filter(n => n.kind === 'root').length, 1);
    eq(m.nodes.filter(n => n.kind === 'branch').length, 6);
    eq(m.nodes.filter(n => n.kind === 'leaf').length, 18);
    eq(m.version, 1);
    eq(m.source, 'local');
  });
  test('every parent points at a node that exists', () => {
    const m = fresh(), by = {};
    m.nodes.forEach(n => by[n.id] = n);
    m.nodes.forEach(n => { if (n.kind !== 'root') ok(by[n.parent], n.id + ' -> ' + n.parent); });
  });

  test('a lock keeps the text through a regeneration — what the icon promises', () => {
    const first = fresh();
    const leaf = M.NODE(first, 'n-problem-0');
    leaf.locked = true;
    leaf.text = 'my own wording';
    const second = M.buildMap('T', 'topic', content, first, 'gateway');
    eq(M.NODE(second, 'n-problem-0').text, 'my own wording');
    eq(M.NODE(second, 'n-problem-0').locked, true);
    eq(second.version, 2);
    eq(second.map_id, first.map_id);
    ok(second.kept.indexOf('my own wording') >= 0);
  });
  test('a hand-dragged box keeps its position', () => {
    const first = fresh();
    const leaf = M.NODE(first, 'n-market-2');
    leaf.moved = true; leaf.x = 777; leaf.y = 333;
    const second = M.buildMap('T', 'topic', content, first, 'gateway');
    const again = M.NODE(second, 'n-market-2');
    eq(again.x, 777);
    eq(again.y, 333);
    eq(again.moved, true);
  });
  test('a box the user added survives only if locked and still parented', () => {
    const first = fresh();
    first.nodes.push({ id: 'mine', kind: 'leaf', branch: 'exec', side: 'R', text: 'extra',
      parent: 'n-exec', locked: true, moved: true, x: 5, y: 6, style: {} });
    first.nodes.push({ id: 'loose', kind: 'leaf', branch: 'exec', side: 'R', text: 'unlocked',
      parent: 'n-exec', locked: false, moved: true, x: 7, y: 8, style: {} });
    const second = M.buildMap('T', 'topic', content, first, 'gateway');
    ok(M.NODE(second, 'mine'), 'locked extra box kept');
    eq(M.NODE(second, 'loose'), undefined, 'unlocked extra box is regenerated away');
  });
});

group('model — layout', () => {
  test('no map still returns a canvas size', () => {
    deep(M.relayout(null, T.DEFAULT_STYLE), { CW: 1240, CH: 700 });
  });
  test('the root lands in the middle and the sheet has room', () => {
    const m = M.buildMap('T', 't', M.compose(M.readIdea('A hub for banks')), null, 'local');
    const { CW, CH } = M.relayout(m, T.DEFAULT_STYLE);
    ok(CW > 0 && CH > 0);
    const root = M.NODE(m, 'n-root');
    eq(root.x, CW / 2);
    eq(root.y, CH / 2);
    m.nodes.forEach(n => ok(isFinite(n.x) && isFinite(n.y), n.id));
  });
  test('a moved box is left where the user put it', () => {
    const m = M.buildMap('T', 't', M.compose(M.readIdea('A hub for banks')), null, 'local');
    const leaf = M.NODE(m, 'n-solution-1');
    leaf.moved = true; leaf.x = 111; leaf.y = 222;
    M.relayout(m, T.DEFAULT_STYLE);
    eq(leaf.x, 111);
    eq(leaf.y, 222);
  });
  test('NODE and KIDS agree with the skeleton', () => {
    const m = M.buildMap('T', 't', M.compose(M.readIdea('A hub for banks')), null, 'local');
    eq(M.NODE(m, 'n-root').kind, 'root');
    eq(M.NODE(m, 'nope'), undefined);
    eq(M.NODE(null, 'n-root'), null);
    eq(M.KIDS(m, 'n-root').length, 6);
    eq(M.KIDS(m, 'n-problem').length, 3);
  });
});


/* ---------------------------------------------------------------- report
   Built node by node rather than as a string of HTML: a failure message
   contains whatever value broke the test, and that is not markup.        */
function el(tag, cls, text){
  const n = document.createElement(tag);
  if(cls) n.className = cls;
  if(text != null) n.textContent = text;
  return n;
}

const out = document.getElementById('out');
groups.forEach(g => {
  const box = el('section', 'grp');
  box.appendChild(el('h2', null, g.name));
  const list = el('ol');
  g.tests.forEach(t => {
    const li = el('li', t.ok ? 'pass' : 'fail');
    li.appendChild(el('span', null, t.ok ? '✓' : '✕'));
    li.appendChild(el('span', null, t.name));
    list.appendChild(li);
    if(!t.ok) list.appendChild(el('li', 'why', t.why));
  });
  box.appendChild(list);
  out.appendChild(box);
});

const sum = document.getElementById('sum');
sum.className = 'sum ' + (R.fail ? 'bad' : 'ok');
sum.textContent = (R.fail ? R.fail + ' failed · ' : '')
  + R.pass + ' passed · ' + R.total + ' assertions in ' + groups.length + ' groups';
document.title = (R.fail ? 'FAIL ' + R.fail + '/' + R.total : 'PASS ' + R.total)
  + ' · Cognix self-test';

/* the counts, so `window.__RESULTS__` answers "is it green" in one call */
window.__RESULTS__ = R;
if(R.fail) console.error('self-test: ' + R.fail + ' failed\n' + R.failures.join('\n'));
else console.log('self-test: ' + R.pass + '/' + R.total + ' passed');
