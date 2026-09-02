/* =====================================================================
   the seam between one browser and an account.

   In local mode store.js writes one localStorage blob and this file does
   nothing. In cloud mode it installs itself as the store's sink and owns
   durability instead: every chat is a row, every save is a request, and
   nothing is left in localStorage — a shared machine should not still have
   somebody's maps in it after they sign out.

   Three rules earn their keep here:

   * A chat is never pushed before it has been read. The tray lists four
     hundred chats as titles and fetches a map only when one is opened, so
     for a moment a session exists locally with an empty sheet. Pushing that
     would replace the real thing with nothing.
   * A save carries the version it last saw. The server compares it inside
     the UPDATE, so the second of two tabs is told rather than obeyed.
   * A failure is visible. Silently dropping a save is the one outcome worse
     than a slow one.
   ===================================================================== */
import { S, emit, note, blankSheet, cur, setSink, reflow, KEYS } from './store.js';
import * as cloud from './cloud.js';
import * as ses from './session.js';
import * as V from './sanitize.js';
import { clone, mergeDefaults, uid } from './util.js';
import { DEFAULT_STYLE } from './tokens.js';

const SETTLE = 900;          // after the store's own 180 ms debounce
const ASKED = 'cognix-import-offered';

const dirty = new Map();     // local id -> session, waiting to be pushed
const gone = [];             // server ids of chats deleted while offline-ish
let timer = 0, busy = false, stopped = false;

const alive = s => S.sessions.indexOf(s) >= 0;
const empty = s => !s.sheet.map && !s.sheet.log.length;
/* a chat we hold in full: either it was born here or it has been read back */
const whole = s => s.full !== false;

/* ---------------- the wire shape ----------------
   The database has three columns for a conversation turn and one jsonb for
   whatever else it carries; the app has six fields per message. So `meta`
   holds the difference. Same trick for the sheet: maps.style is one column,
   and the four scalars beside the style tree ride inside it under `sheet`. */
const ROLE = { you: 'user', ai: 'assistant', err: 'assistant' };
const KIND = { you: 'chat', ai: 'chat', err: 'error' };
const META_MAX = 7000;       // the server refuses a message meta over 8 KB

function msgOut(m){
  const row = { role: ROLE[m.role] || 'assistant', kind: KIND[m.role] || 'chat',
                text: String(m.text == null ? '' : m.text),
                ts: Math.round(m.ts || 0) };
  const meta = {};
  if(m.think) meta.think = String(m.think).slice(0, 200);
  if(m.meta) meta.note = String(m.meta).slice(0, 200);
  if(m.model) meta.model = String(m.model).slice(0, 60);
  if(m.patch) meta.patch = m.patch;
  if(!Object.keys(meta).length) return row;
  /* a patch over a large selection is the only thing that gets near the cap,
     and the rollback button is worth less than the message it hangs off */
  try{
    if(JSON.stringify(meta).length > META_MAX) delete meta.patch;
  }catch(e){ delete meta.patch; }
  row.meta = meta;
  return row;
}

function msgIn(m){
  const meta = (m && m.meta && typeof m.meta === 'object') ? m.meta : {};
  return { role: m.role === 'user' ? 'you' : (m.kind === 'error' ? 'err' : 'ai'),
           text: m.text || '', ts: m.ts || 0,
           think: meta.think || null, meta: meta.note || null,
           model: meta.model || undefined, patch: meta.patch || null };
}

function styleOut(sheet){
  const out = clone(sheet.style || {});
  const extra = { preset: sheet.preset || 'default' };
  if(sheet.brief) extra.brief = sheet.brief;
  if(sheet.topic) extra.topic = sheet.topic;
  if(sheet.baseline) extra.baseline = sheet.baseline;
  out.sheet = extra;
  return out;
}

function snapOf(s){
  const out = {
    title: s.title || 'Untitled',
    tab: s.id === S.curId ? (S.ui.tab === 'plan' ? 'plan' : 'map') : (s.tab || 'map'),
    model: (s.sheet.map && s.sheet.map.source) || '',
    version: s.version || 0,
    messages: (s.sheet.log || []).map(msgOut),
    style: styleOut(s.sheet)
  };
  if(s.sheet.map) out.map = s.sheet.map;
  if(s.lid) out.local_id = s.lid;
  return out;
}

/* ---------------- from the server into a session ----------------
   Everything arriving here goes through sanitize.js, the same door a
   localStorage blob comes in by. The server checked it on the way in; this
   is about shape rather than trust — mergeDefaults fills a style key that
   did not exist when the row was written, and a map is re-parented rather
   than allowed to crash the Layers tree. */
function sheetIn(got){
  const raw = (got.style && typeof got.style === 'object') ? got.style : {};
  const extra = (raw.sheet && typeof raw.sheet === 'object') ? raw.sheet : {};
  const tree = {};
  Object.keys(raw).forEach(k => { if(k !== 'sheet') tree[k] = raw[k]; });
  const one = V.sessions({ sessions: [{ id: 'x', sheet: {
    style: tree, preset: extra.preset, brief: extra.brief, topic: extra.topic,
    baseline: extra.baseline, map: got.map,
    log: (got.messages || []).map(msgIn)
  } }] }).sessions[0];
  const sheet = one ? one.sheet : blankSheet();
  if(!sheet.style || !Object.keys(sheet.style).length) sheet.style = clone(DEFAULT_STYLE);
  mergeDefaults(DEFAULT_STYLE, sheet.style);
  return sheet;
}

function stub(row){
  const lid = row.local_id || '';
  return { id: lid || row.id, lid, cloud: row.id,
           version: row.version || 1, full: false, tab: row.tab || 'map',
           count: row.message_count || 0,
           title: String(row.title || 'Untitled').slice(0, 60),
           ts: Date.parse(row.created_at || '') || Date.now(),
           updated: Date.parse(row.updated_at || '') || Date.now(),
           sheet: blankSheet() };
}

function fresh(){
  const id = uid('s-');
  return { id, lid: '', cloud: null, version: 0, full: true, tab: 'map',
           title: 'New chat', ts: Date.now(), updated: Date.now(),
           sheet: blankSheet() };
}

/* ---------------- first paint ----------------
   One request: who you are, what this instance allows, and the titles of
   every chat you have. Not the chats themselves — four hundred maps is not
   a page load. The one being opened is fetched here, the rest when clicked. */
export async function hydrate(){
  const boot = await cloud.bootstrap();
  S.me = boot.user || null;
  S.limits = boot.limits || null;
  S.settings = boot.settings || null;
  const seen = {}, list = [];
  (Array.isArray(boot.chats) ? boot.chats : []).forEach(r => {
    if(!r || !r.id) return;
    const s = stub(r);
    if(seen[s.id]) return;
    seen[s.id] = 1;
    list.push(s);
  });
  S.sessions = list.length ? list : [fresh()];
  S.curId = S.sessions[0].id;
  S.sheet = cur().sheet;
  S.past = []; S.future = [];
  await fill(cur());
  if(S.sheet.map) reflow();
  offerImport();
  return S.me;
}

/* one chat, in full, on demand. Two clicks in a row must not race, so the
   promise is parked on the session and the second caller waits for it. */
export function fill(s){
  if(!s || !s.cloud || s.full) return Promise.resolve();
  if(s.filling) return s.filling;
  S.ui.loading = s.id;
  emit();
  s.filling = (async () => {
    try{
      const got = await cloud.read(s.cloud);
      Object.assign(s.sheet, sheetIn(got));
      const row = got.chat || {};
      s.version = row.version || s.version;
      if(row.title) s.title = row.title;
      if(row.tab) s.tab = row.tab;
      s.full = true;
      dirty.delete(s.id);            // nothing in it is ours to push
      if(s.id === S.curId && s.sheet.map) reflow();
    }catch(e){
      if(e.status === 404){ s.full = true; s.cloud = null; }
      else trouble(e, 'That chat could not be opened');
    }finally{
      s.filling = null;
      if(S.ui.loading === s.id) S.ui.loading = null;
      emit();
    }
  })();
  return s.filling;
}

/* ---------------- trouble ---------------- */
function trouble(e, what){
  if(e && e.status === 401) return wall();
  S.ui.saveErr = what + ' — ' + ((e && e.message) || 'the server did not say why');
  note(what);
}
/* the session ran out. Nothing can be saved from here, so the app says so
   instead of taking edits it will drop. */
function wall(){
  stopped = true;
  dirty.clear();
  S.ui.authWall = true;
  S.ui.saveErr = 'Your sign-in ran out, so nothing is being saved.';
  emit();
}

/* ---------------- pushing ---------------- */
let fails = 0;

function schedule(ms){
  if(timer || stopped) return;
  timer = setTimeout(run, ms == null ? SETTLE : ms);
}
function mark(s){
  if(!s || stopped) return;
  dirty.set(s.id, s);
  S.ui.syncing = true;
  schedule();
}

async function one(s){
  if(!whole(s)) return;                       // never push what we have not read
  if(empty(s) && !s.cloud) return;            // an untouched new chat is not a row
  const snap = snapOf(s);
  if(!s.cloud){
    const out = await cloud.create(snap);
    const row = out.chat || {};
    if(!row.id) throw ses.fail('The server did not say where that chat went.', 0);
    s.cloud = row.id;
    s.version = row.version || 1;
    return;
  }
  const out = await cloud.write(s.cloud, snap);
  s.version = out.version || (out.chat && out.chat.version) || (s.version + 1);
}

/* A 409 with a version on it means another tab saved this chat while we were
   looking at an older copy. The server copy wins — it is the newer one — but
   what this tab holds is not thrown away for it: it is kept as its own chat,
   so the answer to "which of the two survived" is both. */
async function fork(s){
  const mine = (s.sheet.map || (s.sheet.log || []).length) ? clone(s.sheet) : null;
  s.full = false;
  await fill(s);
  if(!mine) return note('That chat was newer on the server, so it was reloaded');
  const kept = fresh();
  kept.title = ('This tab · ' + (s.title || 'chat')).slice(0, 60);
  kept.sheet = mine;
  S.sessions.unshift(kept);
  mark(kept);
  note('That chat had changed elsewhere. The newer copy is open and what this '
     + 'tab held is kept beside it.');
}

/* true when the chat is settled, false when it is refused for good */
async function attempt(s, again){
  try{
    await one(s);
    return true;
  }catch(e){
    if(e.status === 409 && e.body && e.body.version){ await fork(s); return true; }
    if(e.status === 404 && s.cloud && !again){
      s.cloud = null;                    // deleted elsewhere; write it back
      return attempt(s, true);
    }
    if(e.status === 401){ wall(); return false; }
    if(e.status === 409 || e.status === 402 || e.status === 403 || e.status === 400){
      trouble(e, 'That chat was not saved');
      return false;
    }
    throw e;                             // network, 5xx: worth trying again
  }
}

async function run(){
  timer = 0;
  if(stopped){ dirty.clear(); return; }
  if(busy) return schedule(300);
  busy = true;
  let hurt = false;

  while(gone.length){
    const id = gone[0];
    try{ await cloud.remove(id); gone.shift(); }
    catch(e){
      if(e.status === 401){ wall(); break; }
      if(e.status === 404 || e.status === 400){ gone.shift(); continue; }
      hurt = true;                        // still gone locally; try again later
      break;
    }
  }

  const batch = Array.from(dirty.values());
  dirty.clear();
  for(let i = 0; i < batch.length; i++){
    const s = batch[i];
    if(stopped) break;
    if(!alive(s)) continue;
    try{
      if(!(await attempt(s))) hurt = true;
    }catch(e){
      dirty.set(s.id, s);                 // keep it: this was the network
      trouble(e, 'That chat is not saved yet');
      hurt = true;
      break;
    }
  }

  busy = false;
  fails = hurt ? Math.min(fails + 1, 5) : 0;
  if(!hurt && !dirty.size && S.ui.saveErr) S.ui.saveErr = null;
  S.ui.syncing = !!(dirty.size || gone.length);
  if(dirty.size || gone.length) schedule(hurt ? 1500 * Math.pow(2, fails) : SETTLE);
  emit();
}

/* the debounce is a data-loss window if the tab goes away inside it. A
   keepalive PUT outlives the page; version 0 tells the server to take what it
   is given, because a beacon cannot read the reply that would tell us the new
   version and a stale one would look like a conflict later. */
function hide(){
  if(!dirty.size || stopped) return;
  clearTimeout(timer);
  timer = 0;
  dirty.forEach(s => {
    if(!whole(s) || !s.cloud || empty(s)) return;
    const snap = snapOf(s);
    snap.version = 0;
    if(cloud.beacon(s.cloud, snap)) s.version = 0;
  });
}

/* ---------------- what the store calls ----------------
   store.js knows four things happen to a chat and nothing about how they are
   stored. This is the whole of the contract between the two files. */
export function attach(){
  S.cloud = true;
  setSink({
    save(){ mark(cur()); },
    touched(s){ mark(s); },
    removed(s){
      dirty.delete(s.id);
      if(s.cloud){ gone.push(s.cloud); schedule(0); }
    },
    opened(s){ fill(s); }
  });
  ses.onUser(u => { S.me = u; emit(); });
  window.addEventListener('pagehide', hide);
  document.addEventListener('visibilitychange', () => {
    if(document.visibilityState === 'hidden') hide();
  });
  /* a model call spends somebody's monthly allowance; api.js says so and the
     footer stops showing yesterday's number */
  window.addEventListener('cognix:spent', spent);
}

let usageAt = 0;
async function spent(){
  const now = Date.now();
  if(now - usageAt < 4000 || !S.me) return;
  usageAt = now;
  try{
    const got = await ses.usage();
    if(S.me && got && typeof got.used === 'number'){ S.me.usage = got; emit(); }
  }catch(e){}
}

/* everything queued, now, and a promise for when it has landed */
export async function flushNow(){
  clearTimeout(timer);
  timer = 0;
  if(!dirty.size && !gone.length) return;
  await run();
}

/* ---------------- the maps that were already on this machine ----------------
   Before there were accounts this app kept everything in one localStorage
   blob. Somebody who used it then and signs in now has real work sitting in a
   key nothing reads any more, so it is offered — once, and per account, because
   two people sharing a laptop must not be asked about each other's maps.

   Nothing local is thrown away until the server has said how many rows it
   took. Then it is thrown away properly: a signed-out shared machine should
   not still have somebody's maps in it. */
function askedFor(id){
  try{
    const raw = localStorage.getItem(ASKED) || '';
    return raw.split(',').indexOf(id) >= 0;
  }catch(e){ return true; }          // no storage to read means nothing to offer
}
function remember(id){
  try{
    const raw = localStorage.getItem(ASKED) || '';
    const list = raw ? raw.split(',') : [];
    if(list.indexOf(id) < 0) list.push(id);
    localStorage.setItem(ASKED, list.slice(-12).join(','));
  }catch(e){}
}

/* the old blob, read through sanitize.js like any other untrusted input, and
   thinned to the sessions that actually hold something */
function localBlob(){
  let raw = null;
  try{ raw = localStorage.getItem(KEYS.cur) || localStorage.getItem(KEYS.old); }
  catch(e){ return []; }
  if(!raw) return [];
  let parsed = null;
  try{ parsed = JSON.parse(raw); }catch(e){ return []; }
  let got = [];
  try{ got = V.sessions(parsed).sessions || []; }catch(e){ return []; }
  return got.filter(s => s.sheet && (s.sheet.map || (s.sheet.log || []).length));
}

function forget(){
  try{
    localStorage.removeItem(KEYS.cur);
    localStorage.removeItem(KEYS.old);
  }catch(e){}
}

export function offerImport(){
  if(!S.me || !S.me.id || askedFor(S.me.id)) return;
  const found = localBlob();
  if(!found.length) return;
  S.ui.offer = { n: found.length, done: 0, busy: false, err: null };
  emit();
}

export function declineImport(){
  if(S.me && S.me.id) remember(S.me.id);
  S.ui.offer = null;
  emit();
}

/* One request per batch, because that is what the server takes at a time.
   `remaining` is what the loop turns on rather than our own arithmetic — the
   server is the one that knows how much room the account had left. */
const BATCH = 40;

export async function runImport(){
  const o = S.ui.offer;
  if(!o || o.busy) return;
  o.busy = true; o.err = null; emit();
  let queue = localBlob().map(s => {
    const snap = snapOf(s);
    snap.local_id = s.id;             // what the server matches on, so twice is once
    snap.version = 0;
    return snap;
  });
  let full = false;
  try{
    while(queue.length){
      const batch = queue.slice(0, BATCH);
      const out = await cloud.upload(batch);
      o.done += (out.added || 0) + (out.skipped || 0);
      emit();
      const left = typeof out.remaining === 'number'
        ? Math.min(out.remaining, batch.length) : 0;
      if(left >= batch.length) break;          // no progress; do not spin
      queue = queue.slice(batch.length - left);
      if(out.full){ full = true; break; }
    }
    if(!queue.length && !full) forget();
    if(S.me && S.me.id) remember(S.me.id);
    S.ui.offer = null;
    await refresh();
    note(full ? 'Some were brought over — this account is at its chat limit'
              : 'Your maps from this browser are in your account now');
  }catch(e){
    o.busy = false;
    o.err = (e && e.message) || 'That did not go through.';
    if(e && e.status === 401) wall();
    emit();
  }
}

/* ---------------- the tray, after rows changed behind its back ----------------
   Only the list is re-read, never the chats themselves: a stub for anything
   new, the session object we already hold for anything we have seen, and
   whatever has not reached the server yet kept at the top. Never drops the
   chat that is on screen — cur() falling back to sessions[0] while S.sheet
   still points at the old one is how an app starts editing the wrong map. */
export async function refresh(){
  let got;
  try{ got = await cloud.list(); }
  catch(e){
    if(e.status === 401) wall(); else trouble(e, 'That list could not be refreshed');
    return;
  }
  const have = {};
  S.sessions.forEach(s => { if(s.cloud) have[s.cloud] = s; });
  const rows = [];
  const seen = {};
  (Array.isArray(got.chats) ? got.chats : []).forEach(r => {
    if(!r || !r.id || seen[r.id]) return;
    seen[r.id] = 1;
    const was = have[r.id];
    if(was){
      was.title = String(r.title || was.title).slice(0, 60);
      was.count = r.message_count || was.count;
      /* a different version means another tab wrote it. Forget our copy so
         opening it reads the newer one; the open chat is left alone, since a
         save from here is what the 409 path is for. */
      if(!dirty.has(was.id) && (r.version || 0) !== (was.version || 0)){
        was.version = r.version || was.version;
        if(was.id !== S.curId) was.full = false;
      }
      rows.push(was);
      return;
    }
    rows.push(stub(r));
  });
  const mine = S.sessions.filter(s =>
    !s.cloud || (!seen[s.cloud] && s.id === S.curId));
  S.sessions = mine.concat(rows);
  if(!S.sessions.length) S.sessions = [fresh()];
  if(!S.sessions.some(s => s.id === S.curId)){
    S.curId = S.sessions[0].id;
    S.sheet = cur().sheet;
    S.past = []; S.future = [];
    await fill(cur());
    if(S.sheet.map) reflow();
  }
  emit();
}
