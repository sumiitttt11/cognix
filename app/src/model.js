/* =====================================================================
   the map model: idea -> six branches -> 25 boxes -> x/y.
   Pure data. `buildMap` takes the six branch arrays from wherever they
   came (the LLM, or the local fallback composer below) so the shape of a
   map is identical either way.
   ===================================================================== */
import { uid, cap, shorten } from './util.js';
import { BRANCHES } from './tokens.js';

export const NODE = (map, id) => map ? map.nodes.find(n => n.id === id) : null;
export const KIDS = (map, id) => map ? map.nodes.filter(n => n.parent === id) : [];

/* ---------------- idea -> title / audience / noun ---------------- */
export function readIdea(t){
  const clean = String(t || '').replace(/\s+/g, ' ').trim();
  const full  = (clean.match(/idea\s*:\s*(.+)$/i) || [, clean])[1] || 'this idea';
  /* the audience is the text after the LAST "for" — in
     "an app for tracking workouts for busy parents" that is "busy parents" */
  const parts = full.split(/\bfor\b/i);
  const who   = shorten((parts.length > 1 ? parts[parts.length - 1] : 'small teams')
                  .replace(/\b(who|that|which)\b[\s\S]*$/i, '').replace(/^[\s,]+/, '').trim() || 'small teams', 40);
  const noun  = (full.match(/\b(hub|app|platform|tool|website|service|marketplace|dashboard|assistant|network|extension|api|portal)\b/i)
                  || [, 'product'])[1].toLowerCase();
  let core    = parts[0].replace(/^(a|an|the)\s+/i, '').trim();
  if(core.length < 14) core = (parts.length > 2 ? parts.slice(0, -1).join('for') : full)
    .replace(/^(a|an|the)\s+/i, '').trim();
  core = shorten(core, 52);
  return { full, who, noun, core, title: shorten(core, 38) };
}

function clause(i){
  const m = String(i.full || '').replace(/^(a|an|the)\s+/i, '')
    .match(/^[^,]*?\b(?:hub|app|platform|tool|website|service|marketplace|dashboard|assistant|network|extension|api|portal|system)\b\s+(?:that|which|to)\s+([\s\S]+)$/i);
  if(!m) return '';
  let t = m[1].trim();
  const c = t.indexOf(',');
  if(t.length > 58 && c > 16) t = t.slice(0, c);
  return shorten(t, 58);
}
function solutionLine(i){
  const art = /^[aeiou]/i.test(i.noun) ? 'An ' : 'A ', c = clause(i);
  return c ? art + i.noun + ' that ' + c : art + i.noun + ' built for ' + i.who;
}

/* local fallback: used only when the gateway is unreachable, so a map
   still appears instead of an error screen */
export function compose(i){
  return {
    problem : [ cap(i.who) + ' still handle this by hand',
                'Existing tools were built for someone else',
                'Getting it wrong is expensive to undo' ],
    solution: [ solutionLine(i),
                'Set up in a day, no new hardware',
                'Works with the files they already keep' ],
    audience: [ 'Primary: ' + i.who,
                'Champion: whoever owns the risk',
                'Gatekeepers: legal and procurement' ],
    model   : [ 'Per-seat subscription, billed yearly',
                'One-time setup fee for the first rollout',
                'Add-ons: extra capacity, priority support' ],
    market  : [ 'Everyone living with the same constraint',
                'Growing as the rules keep tightening',
                'Beachhead first, adjacent segments next' ],
    exec    : [ 'Weeks 1–3: working prototype',
                'Weeks 4–7: two paid pilots',
                'Weeks 8–12: pricing and first invoice' ]
  };
}

/* ---------------- branch content -> nodes ----------------
   `prev` is the map being regenerated: locked text and hand-moved
   positions survive, which is what the lock icon promises.            */
export function buildMap(title, topic, content, prev, source){
  const old = {}; if(prev) prev.nodes.forEach(n => old[n.id] = n);
  const nodes = [];
  const add = (id, kind, branch, side, text) => {
    const o = old[id] || null;
    const n = { id, kind, branch, side, text,
      parent: kind === 'root' ? null : (kind === 'branch' ? 'n-root' : 'n-' + branch),
      locked: false, moved: false, x: 0, y: 0, style: {} };
    if(o){
      n.style = o.style || {};
      if(o.locked){ n.locked = true; n.text = o.text; }
      if(o.moved){ n.moved = true; n.x = o.x; n.y = o.y; }
    }
    nodes.push(n); return n;
  };
  add('n-root', 'root', null, 'C', title);
  BRANCHES.forEach(b => {
    add('n-' + b.key, 'branch', b.key, b.side, b.label);
    (content[b.key] || []).forEach((t, k) => add('n-' + b.key + '-' + k, 'leaf', b.key, b.side, t));
  });
  /* boxes the user added by hand are not part of the fixed skeleton — keep the
     locked ones as long as their parent survives */
  const ids = {}; nodes.forEach(n => ids[n.id] = true);
  if(prev) prev.nodes.forEach(o => {
    if(ids[o.id] || !o.locked || !ids[o.parent]) return;
    nodes.push({ id:o.id, kind:o.kind, branch:o.branch, side:o.side, text:o.text,
      parent:o.parent, locked:true, moved:true, x:o.x, y:o.y, style:o.style || {} });
  });
  return { map_id: (prev && prev.map_id) || uid('map-'), title, topic,
    version: prev ? prev.version + 1 : 1, source: source || 'local',
    kept: prev ? prev.nodes.filter(n => n.locked).map(n => n.text) : [], nodes };
}

/* the offline path, kept so the app never dead-ends on a network error */
export function generateLocal(brief, prev){
  const info = readIdea(brief);
  const map = buildMap(info.title, info.full, compose(info), prev, 'local');
  return map;
}

/* ---------------- layout ----------------
   returns the sheet size; mutates node x/y for every box the user has
   not dragged (`moved`)                                              */
export function relayout(map, style){
  if(!map) return { CW: 1240, CH: 700 };
  const L = style.layout, gx = L.gapX, gy = L.gapY, MX = 132, MY = 66;
  let CW, CH;
  const put = (id, x, y) => { const n = NODE(map, id); if(n && !n.moved){ n.x = x; n.y = y; } };
  if(L.dir === 'both'){
    CW = 4 * gx + 2 * MX; CH = 8 * gy + 2 * MY;
    const cx = CW / 2, cy = CH / 2;
    put('n-root', cx, cy);
    BRANCHES.forEach(b => {
      const s = b.side === 'L' ? -1 : 1;
      const i = BRANCHES.filter(x => x.side === b.side).indexOf(b);
      const ly = k => cy + (i * 3 + k - 4) * gy;
      put('n-' + b.key, cx + s * gx, ly(1));
      for(let k = 0; k < 3; k++) put('n-' + b.key + '-' + k, cx + s * 2 * gx, ly(k));
    });
  } else {
    CW = 2 * gx + MX + 330; CH = 18 * gy + 2 * MY;
    const x0 = 170, cy = CH / 2;
    put('n-root', x0, cy);
    BRANCHES.forEach((b, j) => {
      const ly = k => cy + (j * 3 + k - 8.5) * gy;
      put('n-' + b.key, x0 + gx, ly(1));
      for(let k = 0; k < 3; k++) put('n-' + b.key + '-' + k, x0 + 2 * gx, ly(k));
    });
  }
  /* hand-added children sit under their parent if they have never been placed */
  map.nodes.forEach(n => {
    if(n.x || n.y) return;
    const p = NODE(map, n.parent);
    if(p){ n.x = p.x + (n.side === 'L' ? -gx : gx); n.y = p.y + gy; n.moved = true; }
  });
  return { CW, CH };
}
