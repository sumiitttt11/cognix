/* =====================================================================
   the trust boundary.

   Three kinds of data reach this app from outside its own code: whatever
   the gateway puts in a tool_use block, whatever is in localStorage, and
   whatever the user types. None of it is trusted here. Every value is
   coerced to the type the rest of the app assumes, capped to the length
   the schema promised, and stripped of control characters before it is
   rendered or persisted.

   Nothing in this file throws on bad input. A malformed field becomes a
   visible placeholder, because a map with one dull line beats a blank
   screen — and the same rule applies to a corrupted saved session.

   Markup is not a concern: the app builds React elements and never uses
   dangerouslySetInnerHTML, so text is escaped by construction. What is a
   concern is shape (PlanView iterating a non-array), size (a 2 MB "title"
   in a box), and dotted keys that could reach Object.prototype.
   ===================================================================== */
import { safeKey, safePath, uid } from './util.js';
import { BRANCHES, BKEYS } from './tokens.js';

/* C0/C1 controls, zero-width marks and the bidi overrides. Stripped rather
   than escaped: none of them belong in a box label, and a stray U+202E
   silently reverses the rest of the line. \t and \n are left alone here and
   handled per-field by str() and para(). */
const CTRL_RANGES = [[0x00, 0x08], [0x0b, 0x1f], [0x7f, 0x9f], [0x200b, 0x200f],
                     [0x2028, 0x2029], [0x202a, 0x202e], [0x2066, 0x2069], [0xfeff, 0xfeff]];
const CTRL = new RegExp('[' + CTRL_RANGES.map(r =>
  String.fromCharCode(r[0]) + '-' + String.fromCharCode(r[1])).join('') + ']', 'g');

/* how much text a single box may hold. The schema asks the model for short
   lines; this is the hard ceiling every editing surface shares. */
export const TEXT_MAX = 240;

/* one line: whitespace collapsed, length capped */
export function str(v, max, fallback){
  let s = typeof v === 'string' ? v : (v == null ? '' : String(v));
  s = s.replace(CTRL, ' ').replace(/\s+/g, ' ').trim();
  if(max > 0 && s.length > max) s = s.slice(0, max).replace(/\s+\S*$/, '').trim() || s.slice(0, max);
  return s || (fallback == null ? '' : fallback);
}
/* several lines: blank runs collapsed, length capped, newlines kept */
export function para(v, max, fallback){
  let s = typeof v === 'string' ? v : (v == null ? '' : String(v));
  s = s.replace(/\r\n?/g, '\n').replace(CTRL, ' ')
       .replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  if(max > 0 && s.length > max) s = s.slice(0, max).trim() + '…';
  return s || (fallback == null ? '' : fallback);
}
export function num(v, min, max, fallback){
  const n = typeof v === 'number' ? v : parseFloat(v);
  if(!isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}
export const bool = v => !!v;
const arr = v => Array.isArray(v) ? v : [];
const obj = v => (v && typeof v === 'object' && !Array.isArray(v)) ? v : {};

/* exactly n non-empty lines, padded by `filler` so callers can index freely */
export function lines(v, n, cap, filler){
  const src = arr(v), out = [];
  for(let i = 0; i < src.length && out.length < n; i++){
    const s = str(src[i], cap);
    if(s) out.push(s);
  }
  while(out.length < n) out.push(filler(out.length));
  return out;
}

/* ---------------- what the gateway sends back ---------------- */

/* the map tool's six fixed keys, three points each. A missing branch is
   filled with a visible placeholder rather than left short, because the
   layout addresses points by index. */
export function mapContent(d, fallbackTitle){
  const o = obj(d);
  const out = { title: str(o.title, 38, fallbackTitle || 'Untitled map') };
  BRANCHES.forEach(b => {
    out[b.key] = lines(o[b.key], 3, 58,
      () => b.label + ' — the model left this point empty');
  });
  return out;
}

/* the plan tool. PlanView maps over four arrays, so each one has to exist. */
export function plan(p){
  const o = obj(p);
  return {
    summary : para(o.summary, 600, 'The gateway returned a plan with no summary.'),
    sections: arr(o.sections).slice(0, 6).map(s => {
      const x = obj(s), body = arr(x.b);
      return { h: str(x.h, 40, 'Section'),
        b: body.map(t => str(t, 200)).filter(Boolean).slice(0, 5) };
    }).filter(s => s.b.length),
    weeks: arr(o.weeks).slice(0, 8).map(w => ({
      w: str(obj(w).w, 20, '—'), t: str(obj(w).t, 160, '—') })),
    risks: arr(o.risks).slice(0, 6).map(r => ({
      r: str(obj(r).r, 120, '—'), m: str(obj(r).m, 160, '—') })),
    next : arr(o.next).slice(0, 6).map(x => str(x, 160)).filter(Boolean)
  };
}

/* ---------------- what localStorage holds ---------------- */

/* per-box overrides are a flat map of dotted path -> primitive */
export function overrides(s){
  const src = obj(s), out = {};
  Object.keys(src).slice(0, 120).forEach(k => {
    if(!safePath(k)) return;
    const v = src[k];
    const t = typeof v;
    if(t === 'string') out[k] = str(v, 120);
    else if(t === 'number') out[k] = isFinite(v) ? v : 0;
    else if(t === 'boolean') out[k] = v;
  });
  return out;
}

/* a style sheet: nested groups of primitives, four levels at most.
   mergeDefaults() fills whatever this drops. */
function plain(v, depth){
  if(v === null) return null;
  const t = typeof v;
  if(t === 'string') return str(v, 120);
  if(t === 'number') return isFinite(v) ? v : 0;
  if(t === 'boolean') return v;
  if(t !== 'object' || Array.isArray(v) || depth > 3) return undefined;
  const out = {};
  Object.keys(v).slice(0, 60).forEach(k => {
    if(!safeKey(k)) return;
    const w = plain(v[k], depth + 1);
    if(w !== undefined) out[k] = w;
  });
  return out;
}
export const style = s => plain(s, 0) || {};

/* Where a map came from. Two answers only: the offline builder, or the one
   agent that draws maps — nothing else has ever written this field, so an
   unrecognised value is a map from an older version, which recorded the vendor
   model id there. That id is the one thing this app does not repeat: it is
   turned into the agent's name here, at the edge where saved data comes in, so
   no screen, no map JSON and nothing written back to the database carries it.
   The name is api.js's CFG.mapModel, spelled out because the trust boundary
   does not import the gateway. */
const MAP_AGENT = 'cognix-mind-v1';
const source = v => (str(v, 60, 'local') === 'local' ? 'local' : MAP_AGENT);

const KINDS = ['root', 'branch', 'leaf'];
function node(n){
  const o = obj(n);
  const id = str(o.id, 60);
  if(!id) return null;
  const kind = KINDS.indexOf(o.kind) >= 0 ? o.kind : 'leaf';
  return { id, kind,
    branch: BKEYS.indexOf(o.branch) >= 0 ? o.branch : null,
    side  : o.side === 'L' ? 'L' : (o.side === 'C' ? 'C' : 'R'),
    text  : str(o.text, 240, '(empty)'),
    parent: kind === 'root' ? null : (str(o.parent, 60) || 'n-root'),
    locked: bool(o.locked), moved: bool(o.moved),
    x: num(o.x, -40000, 40000, 0), y: num(o.y, -40000, 40000, 0),
    style: overrides(o.style) };
}

export function map(m){
  const o = obj(m);
  if(!Array.isArray(o.nodes)) return null;
  const nodes = o.nodes.slice(0, 600).map(node).filter(Boolean);
  if(!nodes.length) return null;

  /* ids have to be unique — NODE() finds the first, everything else edits
     whichever copy it happened to hold */
  const seen = {}, uniq = [];
  nodes.forEach(n => { if(!seen[n.id]){ seen[n.id] = n; uniq.push(n); } });

  /* a parent that does not exist, or a parent cycle, would make the Layers
     tree walk recurse until the stack gives out */
  uniq.forEach(n => {
    if(n.kind === 'root') return;
    if(n.parent === n.id || !seen[n.parent]) n.parent = seen['n-root'] ? 'n-root' : null;
    if(!n.parent) n.kind = 'root';
  });
  uniq.forEach(n => {
    let up = seen[n.parent], hops = 0;
    while(up && hops++ <= uniq.length) up = seen[up.parent];
    if(up){ n.parent = seen['n-root'] ? 'n-root' : null; if(!n.parent) n.kind = 'root'; }
  });

  return { map_id: str(o.map_id, 40) || uid('map-'),
    title  : str(o.title, 120, 'Untitled map'),
    topic  : para(o.topic, 2000),
    version: Math.round(num(o.version, 1, 9999, 1)),
    source : source(o.source),
    kept   : arr(o.kept).slice(0, 60).map(x => str(x, 240)).filter(Boolean),
    nodes  : uniq };
}

/* a patch row carries the path rollback() will write back, so the path is
   checked here rather than trusted at click time */
function patch(p){
  const o = obj(p);
  const rows = arr(o.rows).slice(0, 60).map(r => {
    const x = obj(r);
    if(!safePath(x.p)) return null;
    const prim = v => (v === null || ['string', 'number', 'boolean'].indexOf(typeof v) >= 0);
    if(!prim(x.from) || !prim(x.to)) return null;
    return { p: x.p, l: str(x.l, 60), from: x.from, to: x.to, had: bool(x.had) };
  }).filter(Boolean);
  if(!rows.length) return null;
  return { rows, label: str(o.label, 60, 'Change'),
    scope: o.scope === 'sel' ? 'sel' : 'global',
    ids: Array.isArray(o.ids) ? o.ids.slice(0, 200).map(i => str(i, 60)).filter(Boolean) : null };
}

const ROLES = ['you', 'ai', 'err'];
function message(m){
  const o = obj(m);
  return { id: str(o.id, 40) || uid('m-'),
    ts   : num(o.ts, 0, 1e15, Date.now()),
    role : ROLES.indexOf(o.role) >= 0 ? o.role : 'ai',
    text : para(o.text, 6000),
    think: o.think ? str(o.think, 200) : null,
    meta : o.meta ? str(o.meta, 200) : null,
    model: o.model ? str(o.model, 60) : undefined,
    patch: patch(o.patch) };
}

function sheet(s){
  const o = obj(s);
  return { style: style(o.style), preset: str(o.preset, 24, 'default'),
    map: map(o.map), topic: para(o.topic, 2000), brief: para(o.brief, 2000),
    baseline: o.baseline ? style(o.baseline) : null,
    log: arr(o.log).slice(-200).map(message) };
}

/* the whole persisted blob. Returns the shape load() expects, with every
   session that could not be understood dropped rather than repaired. */
export function sessions(raw){
  const o = obj(raw);
  const seen = {}, out = [];
  arr(o.sessions).slice(0, 300).forEach(s => {
    const x = obj(s);
    const id = str(x.id, 40);
    if(!id || seen[id]) return;
    seen[id] = 1;
    out.push({ id, title: str(x.title, 60, 'Untitled'),
      ts: num(x.ts, 0, 1e15, Date.now()),
      updated: num(x.updated, 0, 1e15, Date.now()),
      sheet: sheet(x.sheet) });
  });
  const curId = str(o.curId, 40);
  return { curId: seen[curId] ? curId : (out.length ? out[0].id : null), sessions: out };
}
