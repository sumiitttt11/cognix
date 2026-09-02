/* =====================================================================
   the store.

   Deliberately not a reducer: dragging, zooming and scrubbing sliders are
   high-frequency mutations of a fairly deep object, and the prototype's
   logic is already written that way. So this is one mutable object plus a
   subscribe/emit pair, and `useStore()` re-renders whoever reads it.

   Everything a user could lose lives in one localStorage blob: every
   session (a chat + its sheet), so the tray can list real previous chats.
   ===================================================================== */
import { clone, mergeDefaults, uid, getPath, setPath, safePath } from './util.js';
import { DEFAULT_STYLE, presetStyle } from './tokens.js';
import { relayout, NODE } from './model.js';
import * as V from './sanitize.js';

const LS = 'cognix-mindmaps-v1';
const LS_OLD = 'noderels-mindmaps-react-v1';   // pre-rename blob, read once
const LS_BAD = 'cognix-mindmaps-broken';       // a blob we could not read, kept
/* the crash screen needs to reach the same blob without importing the store's
   state, which is what it is recovering from */
export const KEYS = Object.freeze({ cur: LS, old: LS_OLD, bad: LS_BAD });

export function blankSheet(){
  return { style: clone(DEFAULT_STYLE), preset:'default', map:null, log:[], brief:'' };
}
function newSessionObj(){
  return { id: uid('s-'), title:'New chat', ts: Date.now(), updated: Date.now(),
           sheet: blankSheet() };
}

/* ---------------- the one breakpoint ----------------
   Below this the tray is a drawer over the document rather than a column beside
   it. Asked through matchMedia rather than innerWidth, so the answer is the
   stylesheet's own answer to the same question — one line of agreement instead
   of two numbers that have to be kept equal — and so there is no reading to
   take from a window that has not been laid out yet. */
const PHONE = 760;
const drawer = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(max-width:' + PHONE + 'px)') : null;
export function isPhone(){ return !!(drawer && drawer.matches); }

export const S = {
  sessions: [],
  curId   : null,
  sheet   : null,          // === current session's sheet (same object)
  past: [], future: [],
  /* cloud mode only: who is signed in, what this instance allows, and what the
     ceilings are. All null in local mode, which is how the UI tells them apart.
     `guest` is the third case — accounts exist but nobody has one here — and
     holds the free allowance /api/config handed out. */
  cloud: false, me: null, limits: null, settings: null, guest: null,
  ui: {
    sel: null, selSet: new Set(),
    zoom: 1, userZoom: false, CW: 1240, CH: 700,
    /* on a phone the tray is a drawer over the document, so it starts shut.
       Decided here rather than in an effect so the first paint is already right
       — a tray that covers the whole screen for one frame reads as a bug. */
    tab: 'map', itab: 'design', insp: false, showLog: false,
    nosid: isPhone(),
    shut: {}, busy: null, err: null, note: null,
    plan: null, planBusy: false, planErr: null, abort: null,
    query: '', clip: null, editing: null, saveErr: null,
    /* cloud mode: the chat being fetched, whether a push is in flight, the
       sign-in-ran-out wall, the free-trial-is-over wall, the account popover,
       and the one-time offer to bring maps that were already in this browser
       over */
    loading: null, syncing: false, authWall: false, guestWall: false,
    acct: false, offer: null
  },
  subs: new Set()
};

export function subscribe(fn){ S.subs.add(fn); return () => S.subs.delete(fn); }
export function emit(){ S.subs.forEach(f => f()); }
/* drag, scrub and wheel-zoom fire faster than the screen refreshes. emit()
   re-renders every box, so those paths call emitSoon() instead and get one
   render per frame. Anything the user waits for still calls emit() directly. */
let frame = 0;
export function emitSoon(){
  if(frame) return;
  frame = requestAnimationFrame(() => { frame = 0; emit(); });
}

/* Which side of the breakpoint we are on has to survive the window changing
   size. A tab that loads narrow and is then widened — or a phone turned to
   landscape — would otherwise keep the state it was born with: on the desktop
   side that is a 0-width column where the chat list should be, with nothing to
   say that the toggle in the title bar brings it back. So a crossing sets the
   tray to what that side means, and inside one side whatever the user chose
   stands. */
if(drawer){
  drawer.addEventListener('change', () => {
    S.ui.nosid = drawer.matches;
    emit();
  });
}

/* ---------------- persistence ----------------
   One blob, written 180 ms after the last change. Two things can go wrong and
   both are visible: the quota can be full (a long transcript plus a big map),
   and the blob on disk can be unreadable. Neither is allowed to throw into a
   render, and neither is allowed to pass silently — losing a session quietly
   is worse than a toast that says so.

   In cloud mode persist.js installs a sink here and durability becomes its
   problem instead: the same debounce, but nothing is written to localStorage,
   because a shared machine should not still have somebody's maps in it after
   they sign out. The four calls below are the whole contract. */
let sink = null;
export function setSink(s){ sink = s || null; }
function tell(what, arg){
  if(!sink || typeof sink[what] !== 'function') return;
  try{ sink[what](arg); }catch(e){}
}

function serialize(){
  return JSON.stringify({
    curId: S.curId,
    sessions: S.sessions.map(s => ({ id:s.id, title:s.title, ts:s.ts,
      updated:s.updated, sheet:s.sheet }))
  });
}
/* one attempt, then a second with the transcripts of every other session
   dropped — the map is the work, the chat log is the cheapest thing to shed */
function write(){
  if(sink){ tell('save'); return true; }
  try{
    localStorage.setItem(LS, serialize());
    if(S.ui.saveErr){ S.ui.saveErr = null; emit(); }
    return true;
  }catch(e){
    try{
      const keep = S.curId;
      localStorage.setItem(LS, JSON.stringify({
        curId: keep,
        sessions: S.sessions.map(s => ({ id:s.id, title:s.title, ts:s.ts,
          updated:s.updated,
          sheet: s.id === keep ? s.sheet : Object.assign({}, s.sheet, { log: [] }) }))
      }));
      S.ui.saveErr = 'Storage is nearly full — older chat transcripts were dropped.';
      note(S.ui.saveErr);
      return true;
    }catch(e2){
      S.ui.saveErr = 'This browser will not save any more — export your map to keep it.';
      note(S.ui.saveErr); emit();
      return false;
    }
  }
}

let saveT = 0;
export function save(){
  clearTimeout(saveT);
  saveT = setTimeout(() => { saveT = 0; write(); }, 180);
}
/* the debounce is a data-loss window if the tab goes away inside it */
export function flush(){
  if(!saveT) return;
  clearTimeout(saveT); saveT = 0; write();
}
if(typeof window !== 'undefined'){
  window.addEventListener('pagehide', flush);
  window.addEventListener('beforeunload', flush);
  document.addEventListener('visibilitychange', () => {
    if(document.visibilityState === 'hidden') flush();
  });
}

/* every session that survives sanitising is kept; one that does not is
   dropped rather than half-repaired, and the raw blob is parked under
   another key so nothing is actually destroyed. */
function readBlob(){
  let raw = null, from = LS;
  try{ raw = localStorage.getItem(LS); }catch(e){ return null; }
  if(!raw){ try{ raw = localStorage.getItem(LS_OLD); from = LS_OLD; }catch(e){} }
  if(!raw) return null;
  let parsed = null;
  try{ parsed = JSON.parse(raw); }catch(e){
    try{ localStorage.setItem(LS_BAD, raw); localStorage.removeItem(LS); }catch(e2){}
    return { corrupt: true };
  }
  return { parsed, moved: from === LS_OLD };
}

export function load(){
  const got = readBlob();
  let saved = null, moved = false, corrupt = !!(got && got.corrupt);
  if(got && got.parsed){
    saved = V.sessions(got.parsed);
    moved = got.moved;
    if(!saved.sessions.length && Object.keys(got.parsed || {}).length){
      corrupt = true;
      try{ localStorage.setItem(LS_BAD, JSON.stringify(got.parsed)); }catch(e){}
    }
  }
  if(saved && saved.sessions.length){
    S.sessions = saved.sessions;
    S.sessions.forEach(s => {
      s.sheet = s.sheet || blankSheet();
      if(!s.sheet.style) s.sheet.style = clone(DEFAULT_STYLE);
      mergeDefaults(DEFAULT_STYLE, s.sheet.style);   // old blobs still load
      s.sheet.log = s.sheet.log || [];
      if(typeof s.sheet.brief !== 'string') s.sheet.brief = '';
      if(s.sheet.preset !== 'custom' && !s.sheet.preset) s.sheet.preset = 'default';
    });
    S.curId = saved.curId || S.sessions[0].id;
  } else {
    const s = newSessionObj();
    S.sessions = [s]; S.curId = s.id;
  }
  S.sheet = cur().sheet;
  if(moved) save();
  if(corrupt) setTimeout(() => note('A saved session could not be read and was set aside'), 400);
  if(S.sheet.map) reflow();
}

export function cur(){ return S.sessions.find(s => s.id === S.curId) || S.sessions[0]; }

/* ---------------- guest mode ----------------
   A visitor on an instance that has accounts, who does not have one. Storage is
   this browser, exactly as in local mode; what is different is that the app is
   spending somebody else's money, so there is a ceiling.

   Two numbers, and they are not the same kind of thing. `chats` is enforced here
   and is what the person actually feels — a wall at the fourth new chat, before
   any work is lost. `left` is the server's count of model calls, kept in a cookie
   it signed, and it is the one that cannot be argued with: when it runs out the
   next call comes back 402 and the same wall goes up. This half of it is a
   courtesy, so it is deliberately the gentler of the two. */
export function beGuest(info){
  S.guest = Object.assign({ chats: 3, calls: 0, used: 0, left: 0 }, info || {});
  if(typeof window === 'undefined') return;
  /* api.js fires these on every model call. For an account persist.js is
     listening; for a guest this is the only listener there is. */
  window.addEventListener('cognix:spent', () => {
    if(!S.guest) return;
    S.guest.used += 1;
    S.guest.left = Math.max(0, S.guest.left - 1);
    emit();
  });
  window.addEventListener('cognix:capped', () => guestWall('calls'));
}
export function chatsLeft(){
  return S.guest ? Math.max(0, S.guest.chats - S.sessions.length) : Infinity;
}
/* `why` is which ceiling was reached: 'chats' is this browser's count, 'calls'
   is the server saying the trial is spent. The wall is the same either way; only
   the first line differs, because being told the wrong one is worse than being
   told nothing. 'invite' is neither — it is the footer button, where somebody has
   asked about their account rather than run into a limit, so it must not claim a
   limit was reached. */
export function guestWall(why){
  if(!S.guest) return;
  const want = why || 'chats';
  if(S.ui.guestWall === want) return;
  S.ui.guestWall = want;
  emit();
}
export function closeWall(){ S.ui.guestWall = false; emit(); }

/* ---------------- sessions = the previous-chat list ---------------- */
export function touch(){
  const c = cur(); if(!c) return;
  c.updated = Date.now();
  if(c.title === 'New chat'){
    const first = (S.sheet.log.find(m => m.role === 'you') || {}).text;
    if(first) c.title = first.replace(/\s+/g, ' ').trim().slice(0, 46);
  }
  save();
}
export function newChat(){
  /* the guest ceiling, in the one place every button that starts a chat goes
     through. Refusing here rather than in each of them is also why none of the
     callers has to know a trial exists. */
  if(chatsLeft() <= 0) return guestWall('chats');
  const s = newSessionObj();
  S.sessions.unshift(s); S.curId = s.id; S.sheet = s.sheet;
  resetView(); S.past = []; S.future = [];
  save(); emit();
}
export function openChat(id){
  if(id === S.curId) return;
  const s = S.sessions.find(x => x.id === id); if(!s) return;
  S.curId = id; S.sheet = s.sheet;
  resetView(); S.past = []; S.future = [];
  if(S.sheet.map) reflow();
  tell('opened', s);            // cloud mode: this is where it gets fetched
  save(); emit();
}
export function renameChat(id, title){
  const s = S.sessions.find(x => x.id === id); if(!s) return;
  s.title = String(title || '').trim().slice(0, 60) || 'Untitled';
  tell('touched', s);           // may not be the chat that is on screen
  save(); emit();
}
export function deleteChat(id){
  const i = S.sessions.findIndex(x => x.id === id); if(i < 0) return;
  tell('removed', S.sessions[i]);
  S.sessions.splice(i, 1);
  if(!S.sessions.length) S.sessions.push(newSessionObj());
  if(id === S.curId){
    S.curId = S.sessions[Math.min(i, S.sessions.length - 1)].id;
    S.sheet = cur().sheet; resetView(); S.past = []; S.future = [];
    if(S.sheet.map) reflow();
    tell('opened', cur());
  }
  save(); emit();
}

function resetView(){
  const u = S.ui;
  u.sel = null; u.selSet = new Set(); u.userZoom = false; u.zoom = 1;
  u.tab = 'map'; u.insp = false; u.plan = null; u.planErr = null;
  u.err = null; u.editing = null;
}

/* ---------------- layout / view ---------------- */
export function reflow(){
  if(!S.sheet || !S.sheet.map) return;
  const d = relayout(S.sheet.map, S.sheet.style);
  S.ui.CW = d.CW; S.ui.CH = d.CH;
}
export function setZoom(z, byUser){
  S.ui.zoom = Math.max(0.2, Math.min(2.4, z));
  if(byUser) S.ui.userZoom = true;
  emit();
}

/* ---------------- undo / redo ---------------- */
function shot(){ return JSON.stringify({ s: S.sheet.style, m: S.sheet.map, p: S.sheet.preset }); }
export function snap(label){
  S.past.push({ label: label || 'Change', ts: Date.now(), data: shot() });
  if(S.past.length > 60) S.past.shift();
  S.future = [];
}
function restore(entry){
  let o = null;
  try{ o = JSON.parse(entry.data); }catch(e){ return note('That history step could not be read'); }
  S.sheet.style = o.s || S.sheet.style; S.sheet.map = o.m || null;
  S.sheet.preset = o.p || S.sheet.preset;
  S.ui.sel = null; S.ui.selSet = new Set();
  reflow(); save(); emit();
}
export function undo(){
  if(!S.past.length) return note('Nothing to undo');
  const e = S.past.pop();
  S.future.push({ label: e.label, ts: Date.now(), data: shot() });
  restore(e); note('Undone · ' + e.label);
}
export function redo(){
  if(!S.future.length) return note('Nothing to redo');
  const e = S.future.pop();
  S.past.push({ label: e.label, ts: Date.now(), data: shot() });
  restore(e); note('Redone · ' + e.label);
}
export function jumpTo(i){            // history list in the Inspect tab
  if(i < 0 || i >= S.past.length) return;
  const e = S.past[i];
  const tail = S.past.splice(i);
  S.future = tail.slice(1).reverse().concat(S.future);
  S.future.unshift({ label: e.label, ts: Date.now(), data: shot() });
  restore(e); note('Back to · ' + e.label);
}

/* ---------------- toast ---------------- */
let noteT;
export function note(m){
  S.ui.note = m; emit();
  clearTimeout(noteT);
  noteT = setTimeout(() => { S.ui.note = null; emit(); }, 2100);
}

/* ---------------- selection ---------------- */
export function selNodes(){
  return [...S.ui.selSet].map(i => NODE(S.sheet.map, i)).filter(Boolean);
}
export function select(id, additive){
  const u = S.ui;
  if(id == null){ u.sel = null; u.selSet = new Set(); }
  else if(additive){
    const s = new Set(u.selSet);
    if(s.has(id) && s.size > 1){ s.delete(id); if(u.sel === id) u.sel = [...s][0]; }
    else { s.add(id); u.sel = id; }
    u.selSet = s;
  } else { u.sel = id; u.selSet = new Set([id]); }
  emit();
}
export function selectMany(ids){
  S.ui.selSet = new Set(ids);
  S.ui.sel = ids.length ? ids[ids.length - 1] : null;
  emit();
}

/* ---------------- token writes ----------------
   scope 'sheet' writes the shared style object; scope 'sel' writes a flat
   dotted override onto every selected node.                          */
export function readVal(path, scope){
  if(scope === 'sel'){
    const ns = selNodes(); if(!ns.length) return getPath(S.sheet.style, path);
    const first = ns[0].style && ns[0].style[path] !== undefined
      ? ns[0].style[path] : tokenFor(ns[0], path);
    const same = ns.every(n => (n.style && n.style[path] !== undefined
      ? n.style[path] : tokenFor(n, path)) === first);
    return same ? first : null;                    // null renders as "Mixed"
  }
  return getPath(S.sheet.style, path);
}
function tokenFor(n, path){
  const bits = path.split('.'), g = bits[0], k = bits.slice(1).join('.');
  if(g === 'text'){
    const grp = n.kind === 'root' ? 'root' : n.kind === 'leaf' ? 'leaf' : 'text';
    const gv = getPath(S.sheet.style, grp + '.' + k);
    if(gv !== undefined) return gv;
  }
  if(n.kind === 'root' && g === 'node'){
    const rv = getPath(S.sheet.style, 'root.' + k);
    if(rv !== undefined) return rv;
  }
  return getPath(S.sheet.style, path);
}
export function isOver(path){
  const ns = selNodes();
  return ns.length > 0 && ns.some(n => n.style && n.style[path] !== undefined);
}
/* `soft` is for a slider being dragged: same write, but one render per frame
   instead of one per pointermove */
export function writeVal(path, val, scope, soft){
  if(!safePath(path)) return note('That style key is not allowed');
  if(scope === 'sel'){
    selNodes().forEach(n => { n.style = n.style || {}; n.style[path] = val; });
  } else {
    setPath(S.sheet.style, path, val);
    S.sheet.preset = 'custom';
  }
  if(path.indexOf('layout.') === 0) reflow();
  save(); soft ? emitSoon() : emit();
}
export function resetVal(path){
  selNodes().forEach(n => { if(n.style) delete n.style[path]; });
  save(); emit();
}
export function resetNodeStyle(){
  snap('Reset node style');
  selNodes().forEach(n => n.style = {});
  save(); emit();
}
export function applyPreset(id){
  snap('Preset');
  S.sheet.style = presetStyle(id);
  S.sheet.preset = id;
  reflow(); save(); emit();
}

/* ---------------- transcript ---------------- */
export function logAdd(m){
  const e = Object.assign({ id: uid('m-'), ts: Date.now() }, m);
  S.sheet.log.push(e);
  if(S.sheet.log.length > 200) S.sheet.log.shift();
  touch(); emit();
  return e;
}
export function logPatch(id, fields){
  const e = S.sheet.log.find(x => x.id === id);
  if(e) Object.assign(e, fields);
  save(); emit();
}
