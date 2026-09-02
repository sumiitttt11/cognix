/* =====================================================================
   structural + arrange edits shared by the canvas, the toolbar, the
   inspector and the chat. Every one of these snapshots first, so undo
   covers the whole editing surface uniformly.
   ===================================================================== */
import { clamp, clone, uid } from './util.js';
import { NODE } from './model.js';
import { BRANCHES } from './tokens.js';
import { S, snap, save, emit, note, selNodes, select, selectMany, reflow } from './store.js';
import { makeNode, removeNode } from './intents.js';
import { str, TEXT_MAX } from './sanitize.js';

const W = id => (S.ui.wmap && S.ui.wmap[id]) || 120;
const Hh = id => (S.ui.hmap && S.ui.hmap[id]) || 30;

/* ---------------- align ----------------
   x/y are centres, so left/right need the measured box width */
export function alignSel(mode){
  const ns = selNodes(); if(ns.length < 2) return note('Select two or more boxes');
  snap('Align ' + mode);
  const L = n => n.x - W(n.id) / 2, R = n => n.x + W(n.id) / 2;
  const T = n => n.y - Hh(n.id) / 2, B = n => n.y + Hh(n.id) / 2;
  if(mode === 'left'){   const m = Math.min(...ns.map(L)); ns.forEach(n => n.x = m + W(n.id) / 2); }
  if(mode === 'right'){  const m = Math.max(...ns.map(R)); ns.forEach(n => n.x = m - W(n.id) / 2); }
  if(mode === 'cx'){     const m = ns.reduce((a, n) => a + n.x, 0) / ns.length; ns.forEach(n => n.x = m); }
  if(mode === 'top'){    const m = Math.min(...ns.map(T)); ns.forEach(n => n.y = m + Hh(n.id) / 2); }
  if(mode === 'bottom'){ const m = Math.max(...ns.map(B)); ns.forEach(n => n.y = m - Hh(n.id) / 2); }
  if(mode === 'cy'){     const m = ns.reduce((a, n) => a + n.y, 0) / ns.length; ns.forEach(n => n.y = m); }
  ns.forEach(n => n.moved = true);
  save(); emit();
}

/* even gaps between the outermost two, measured edge to edge */
export function distributeSel(axis){
  const ns = selNodes(); if(ns.length < 3) return note('Select three or more boxes');
  snap('Distribute ' + axis);
  const size = axis === 'x' ? (n => W(n.id)) : (n => Hh(n.id));
  const key = axis === 'x' ? 'x' : 'y';
  const s = ns.slice().sort((a, b) => a[key] - b[key]);
  const first = s[0], last = s[s.length - 1];
  const span = (last[key] + size(last) / 2) - (first[key] - size(first) / 2);
  const used = s.reduce((a, n) => a + size(n), 0);
  const gap = (span - used) / (s.length - 1);
  let run = first[key] - size(first) / 2;
  s.forEach(n => { n[key] = run + size(n) / 2; n.moved = true; run += size(n) + gap; });
  save(); emit();
}

export function nudge(dx, dy, big){
  const ns = selNodes(); if(!ns.length) return;
  const k = big ? 10 : 1;
  snap('Nudge');
  ns.forEach(n => { n.x = clamp(n.x + dx * k, 40, S.ui.CW - 40);
                    n.y = clamp(n.y + dy * k, 30, S.ui.CH - 30); n.moved = true; });
  save(); emit();
}

/* ---------------- duplicate / style clipboard ---------------- */
export function dupSel(){
  const ns = selNodes(); if(!ns.length) return note('Nothing selected');
  snap('Duplicate');
  const made = ns.filter(n => n.kind !== 'root').map(n => {
    const c = clone(n);
    c.id = uid('n-'); c.moved = true; c.locked = false;
    c.x = n.x + 24; c.y = n.y + 26;
    S.sheet.map.nodes.push(c); return c.id;
  });
  if(!made.length) return note('The centre box cannot be duplicated');
  selectMany(made); save(); note(made.length + ' copied');
}
export function copyStyle(){
  const n = NODE(S.sheet.map, S.ui.sel);
  if(!n) return note('Select a box first');
  S.ui.clip = clone(n.style || {});
  note(Object.keys(S.ui.clip).length + ' overrides copied');
}
export function pasteStyle(){
  if(!S.ui.clip) return note('Nothing copied');
  const ns = selNodes(); if(!ns.length) return note('Nothing selected');
  snap('Paste style');
  ns.forEach(n => n.style = clone(S.ui.clip));
  save(); emit(); note('Style pasted onto ' + ns.length);
}

/* ---------------- content ---------------- */
export function setNodeText(id, text){
  const n = NODE(S.sheet.map, id); if(!n) return;
  /* the inspector's textarea and the chat both land here, so this is where a
     label is cleaned and capped — not at each call site */
  const v = str(text, TEXT_MAX);
  if(!v || n.text === v) return;
  snap('Edit text');
  n.text = v;
  if(id === 'n-root') S.sheet.map.title = v;
  save(); emit();
}
/* move a box (and its children) to another branch — changes side + colour */
export function reparent(id, parentId){
  const map = S.sheet.map, n = NODE(map, id), p = NODE(map, parentId);
  if(!n || !p || n.id === p.id) return;
  let up = p; while(up){ if(up.id === n.id) return note('That would make a loop'); up = NODE(map, up.parent); }
  snap('Move box');
  n.parent = p.id;
  n.branch = p.kind === 'root' ? n.branch : (p.branch || n.branch);
  n.side = p.side === 'L' ? 'L' : 'R';
  n.kind = p.kind === 'root' ? 'branch' : 'leaf';
  const paint = x => S.sheet.map.nodes.filter(c => c.parent === x.id)
    .forEach(c => { c.branch = x.branch; c.side = x.side; paint(c); });
  paint(n);
  n.moved = false; reflow(); save(); emit();
}

/* ---------------- toolbar-level ops ---------------- */
export function addChild(){
  const map = S.sheet.map; if(!map) return;
  const host0 = NODE(map, S.ui.sel) || NODE(map, 'n-root');
  const host = host0.kind === 'leaf' ? NODE(map, host0.parent) : host0;
  snap('Add point');
  const n = makeNode(host, 'New point');
  select(n.id, false); S.ui.editing = n.id; save(); emit();
  return n;
}
export function delSel(){
  const ns = selNodes().filter(n => n.kind !== 'root');
  if(!ns.length) return note('The centre box has to stay');
  snap('Delete');
  let k = 0; ns.forEach(n => k += removeNode(n.id));
  select(null); save(); emit(); note(k + ' box' + (k > 1 ? 'es' : '') + ' deleted');
}
export function toggleLock(){
  const ns = selNodes(); if(!ns.length) return note('Select a box first');
  snap('Lock');
  const want = !ns[0].locked;
  ns.forEach(n => n.locked = want);
  save(); emit();
  note(want ? 'Locked — this wording survives a rebuild' : 'Unlocked');
}
export function tidy(){
  if(!S.sheet.map) return;
  snap('Tidy up');
  S.sheet.map.nodes.forEach(n => n.moved = false);
  reflow(); save(); emit(); note('Auto layout restored');
}

/* branch options for the "move to" select */
export function parentOptions(){
  const map = S.sheet.map; if(!map) return [];
  const out = [['n-root', map.title || 'Centre']];
  BRANCHES.forEach(b => { if(NODE(map, 'n-' + b.key)) out.push(['n-' + b.key, b.label]); });
  return out;
}
