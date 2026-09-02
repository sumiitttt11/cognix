/* =====================================================================
   the console's request layer, and the small formatters every table needs.

   Everything here goes through session.js, so the console gets the CSRF
   header, the timeout, the retry and the 401 handling for free — and, like
   the rest of the app, it never learns that Supabase exists.

   One thing it must add. session.js keeps the signed-in person current by
   watching for a `user` key in any reply:

       if(out && hasOwnProperty(out, 'user') && !o.quiet) setUser(out.user)

   and /api/admin/users/<id> replies with exactly that key — the account being
   looked at. Without `quiet` the act of opening somebody else's row would
   replace the administrator's own identity with theirs, and the header would
   start claiming to be them. So every call from this page passes it, and that
   is the only reason this module exists rather than calling session.js's own
   get/post from every view.
   ===================================================================== */
import { useState, useEffect, useRef } from '../src/h.js';
import * as ses from '../src/session.js';

const QUIET = { quiet: true };
const base = p => '/api/admin/' + p;

/* A query string built from an object, with the empty values dropped so a
   blank search box does not become `&q=`. */
export function qs(obj){
  const bits = [];
  Object.keys(obj || {}).forEach(k => {
    const v = obj[k];
    if(v === '' || v === null || v === undefined) return;
    bits.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(v)));
  });
  return bits.length ? '?' + bits.join('&') : '';
}

export const aget = (p, q) => ses.get(base(p) + qs(q), QUIET);
export const apost = (p, b) => ses.req('POST', base(p), b == null ? {} : b, QUIET);
export const apatch = (p, b) => ses.req('PATCH', base(p), b == null ? {} : b, QUIET);
export const aput = (p, b) => ses.req('PUT', base(p), b == null ? {} : b, QUIET);
export const adel = p => ses.req('DELETE', base(p), null, QUIET);

/* ---------------- numbers ----------------
   Token counts run to eight digits and a table of them is unreadable, so
   anything over ten thousand is rounded and suffixed. The exact figure is
   still available: every cell that shortens a number carries the full one in
   its title attribute. */
export function num(n){
  const v = Number(n);
  if(!isFinite(v)) return '—';
  if(Math.abs(v) < 10000) return String(Math.round(v));
  if(Math.abs(v) < 1e6) return (v / 1000).toFixed(v < 1e5 ? 1 : 0) + 'k';
  return (v / 1e6).toFixed(v < 1e7 ? 2 : 1) + 'M';
}
export const exact = n => (typeof n === 'number' && isFinite(n))
  ? n.toLocaleString('en-US') : '';

/* ---------------- dates ----------------
   Every timestamp out of Postgres is UTC. These render in the reader's own
   zone, because an administrator deciding whether somebody signed in "today"
   is asking about their own today. */
function when(s){
  if(!s) return null;
  const d = new Date(String(s));
  return isFinite(d.getTime()) ? d : null;
}

export function day(s){
  const d = when(s);
  return d ? d.toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
}

export function stamp(s){
  const d = when(s);
  return d ? d.toLocaleString(undefined, { year: 'numeric', month: 'short',
    day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
}

/* "3 days ago" is the column somebody actually reads; the exact stamp goes in
   the title. Under a minute is "just now" rather than "0 minutes ago". */
export function ago(s){
  const d = when(s);
  if(!d) return '—';
  const secs = Math.round((Date.now() - d.getTime()) / 1000);
  if(secs < 0) return 'just now';
  const steps = [[60, 'sec'], [60, 'min'], [24, 'hour'], [7, 'day'],
                 [4.35, 'week'], [12, 'month']];
  let n = secs, unit = 'sec';
  for(let i = 0; i < steps.length; i++){
    if(n < steps[i][0]) break;
    n = n / steps[i][0];
    unit = steps[i + 1] ? steps[i + 1][1] : 'year';
  }
  n = Math.floor(n);
  if(unit === 'sec') return 'just now';
  return n + ' ' + unit + (n === 1 ? '' : 's') + ' ago';
}

/* The short form of a uuid, for a column that has to show one. Never the only
   copy: the full id sits in the title and in the row's own link. */
export const short = id => String(id || '').slice(0, 8);

/* One line, capped, control characters out. Notes and announcements are typed
   by people and read back into a table cell; a stray newline in a cell is a
   row that pushes the ones beside it out of line. Cleaned by code point
   because a literal control character in a source file is invisible. */
export function tidy(s, n){
  const raw = String(s == null ? '' : s);
  let out = '';
  for(let i = 0; i < raw.length; i++){
    const c = raw.charCodeAt(i);
    out += (c < 32 || c === 127) ? ' ' : raw.charAt(i);
  }
  return out.replace(/\s+/g, ' ').trim().slice(0, n || 200);
}

/* What went wrong, as a sentence. A 401 is already handled by session.js
   dropping the user; this only has to explain the rest. */
export function why(e){
  const said = tidy((e && e.message) || '', 240);
  if(e && e.status === 401)
    return 'Your sign-in ran out. Reload this page to sign in again.';
  if(e && e.status === 403 && !said)
    return 'That is not allowed for this account.';
  return said || 'That did not work.';
}

/* ---------------- one GET, with its state ----------------
   Every view of this console is the same three things: ask, show a shape while
   waiting, show either rows or a sentence. This is that, once.

   Two hazards it closes. A reply that arrives after the component is gone must
   not set state, and a reply to a query the user has already moved on from —
   page 2 answering after they clicked page 3 — must not overwrite the newer
   one. Both are handled by a serial number captured per run: only the newest
   run is allowed to speak. */
export function useLoad(make, deps){
  const [state, set] = useState({ data: null, err: '', busy: true });
  const run = useRef(0);
  const live = useRef(true);
  const [nonce, again] = useState(0);
  const asked = useRef(null);
  useEffect(() => () => { live.current = false; }, []);
  useEffect(() => {
    const mine = ++run.current;
    /* Asking the same question again keeps what is on screen: a table that
       blinks into a skeleton every time it refreshes is worse to read than a
       number that is one round trip old. Asking a *different* one does not —
       those rows belong to another account, and a page showing one person's
       name above another person's Delete button is how the wrong row goes. */
    const sig = JSON.stringify(deps || []);
    const moved = asked.current !== null && asked.current !== sig;
    asked.current = sig;
    let cancelled = false;
    set(s => ({ data: moved ? null : s.data, err: '', busy: true }));
    Promise.resolve().then(make).then(got => {
      if(cancelled || !live.current || mine !== run.current) return;
      set({ data: got, err: '', busy: false });
    }).catch(e => {
      if(cancelled || !live.current || mine !== run.current) return;
      set({ data: null, err: why(e), busy: false });
    });
    return () => { cancelled = true; };
  }, (deps || []).concat([nonce]));
  return { data: state.data, err: state.err, busy: state.busy,
           reload: () => again(n => n + 1) };
}

