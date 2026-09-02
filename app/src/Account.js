/* =====================================================================
   the account.

   Everything that is true of the person rather than of a map: who they are,
   how much of the month is left, the way out (an export), and the way off
   (sign out). Four cards live here, because all four are about the account —
   or about not having one — and none of them belongs to a mind map:

   * the popover behind the footer button,
   * the one-time offer to bring maps that were already in this browser into
     the account, since somebody who used this app before it had accounts has
     real work sitting in a key nothing reads any more,
   * the invitation a guest gets — when their free trial is spent, or whenever
     they ask the footer who they are,
   * and the wall that goes up when a session runs out — an app that keeps
     taking edits it cannot save is lying to the person making them.

   None of it renders in local mode: every card is behind a flag that only
   persist.js — or, for the guest one, /api/config — sets.
   ===================================================================== */
import { html, useState, useRef, useEffect } from './h.js';
import { S, emit, note, closeWall, chatsLeft } from './store.js';
import * as ses from './session.js';
import * as cloud from './cloud.js';
import { runImport, declineImport, flushNow } from './persist.js';
import { download } from './backup.js';
import { Mark } from './brand.js';

const stamp = () => new Date().toISOString().slice(0, 10);

/* used / cap for the month. `unlimited` is a real state — an admin can lift
   the ceiling on one account — and it must not render as "0 left". */
function Meter({ u }){
  if(!u || typeof u.used !== 'number') return null;
  if(u.unlimited)
    return html`<p class="ameter"><span>Calls this month</span><b>${u.used} · no limit</b></p>`;
  const cap = u.cap || 0;
  const pct = cap > 0 ? Math.min(100, Math.round((u.used / cap) * 100)) : 0;
  const low = cap > 0 && u.used >= cap * 0.85;
  return html`<${'div'} class="ameter">
    <p><span>Calls this month</span><b>${u.used} of ${cap}</b></p>
    <div class="ubar" role="img"
      aria-label=${u.used + ' of ' + cap + ' model calls used this month'}>
      <i class=${low ? 'hot' : null} style=${{ width: pct + '%' }}></i></div>
  <//>`;
}

/* ---------------- the popover ----------------
   Opened by the footer button. Everything in it is one request, so each has
   the same three outcomes: a spinner on the button that started it, one line
   of green, or one line of red that says what the server said.

   The guard is a separate component from the card so that the card's own
   listeners exist only while it is open — a document-level pointerdown
   handler that lives for the whole session is how a click anywhere in the app
   ends up re-rendering it. */
export function Account(){
  if(!S.cloud || !S.ui.acct || !S.me) return null;
  return html`<${Card} me=${S.me}/>`;
}

function Card({ me }){
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const [name, setName] = useState(me.name || '');
  const [pw, setPw] = useState(false);
  const [cur, setCur] = useState('');
  const [nxt, setNxt] = useState('');
  const box = useRef(null);
  const flags = useRef({ busy: false, live: true });

  /* click-away and Escape. The footer button is exempt, or closing here and
     the button's own toggle would cancel each other out and it would look
     stuck open. A request in flight holds the card open too — a popover that
     vanishes mid-save leaves the user with no idea what happened. */
  useEffect(() => {
    const shut = () => { S.ui.acct = false; emit(); };
    const away = e => {
      if(flags.current.busy) return;
      const t = e.target;
      if(t && t.closest && t.closest('.sfoot .who')) return;
      if(box.current && t && box.current.contains(t)) return;
      shut();
    };
    const key = e => {
      if(e.key !== 'Escape' || flags.current.busy) return;
      e.stopPropagation();
      shut();
    };
    document.addEventListener('pointerdown', away, true);
    window.addEventListener('keydown', key, true);
    return () => {
      flags.current.live = false;
      document.removeEventListener('pointerdown', away, true);
      window.removeEventListener('keydown', key, true);
    };
  }, []);

  const go = async (label, fn) => {
    if(flags.current.busy) return;
    flags.current.busy = true;
    setBusy(label); setErr(''); setOk('');
    try{
      const said = await fn();
      if(flags.current.live && said) setOk(said);
    }catch(e){
      if(flags.current.live) setErr(String((e && e.message) || 'That did not work.'));
    }finally{
      flags.current.busy = false;
      if(flags.current.live) setBusy('');
    }
  };

  const rename = () => go('name', async () => {
    const want = name.trim().slice(0, 80);
    if(!want) throw new Error('A name cannot be empty.');
    if(want === (me.name || '')) return '';
    await ses.rename(want);          // the reply carries the user, so S.me follows
    return 'Name saved';
  });

  const change = () => go('pw', async () => {
    await ses.changePassword(cur, nxt);
    if(flags.current.live){ setCur(''); setNxt(''); setPw(false); }
    return 'Password changed';
  });

  /* every chat, every message, every map, in one file. Not a courtesy: it is
     the answer to "what happens to my work if I stop paying for this". */
  const grab = () => go('exp', async () => {
    const got = await cloud.dump();
    const file = 'cognix-' + stamp() + '.json';
    return download(file, JSON.stringify(got, null, 2))
      ? 'Saved ' + file : 'The browser would not start that download';
  });

  const out = () => go('out', async () => {
    try{ await flushNow(); }catch(e){}      // one last push, then the door
    try{ await ses.logout(); }
    finally{ location.replace('/app/auth/'); }
    return '';
  });

  const min = (ses.ME.config && ses.ME.config.password_min) || 8;
  const cap = S.limits && S.limits.chats;

  return html`<div class="apop" ref=${box} role="dialog" aria-label="Account">
    <div class="ahead">
      <span class="aav"><${Mark} size=${18}/></span>
      <span class="awho">
        <b>${me.name || 'Your account'}</b>
        <small>${me.email || ''}</small>
      </span>
      ${me.role === 'admin' ? html`<span class="achip">Admin</span>` : null}
      ${me.status && me.status !== 'active'
        ? html`<span class="achip warn">${me.status}</span>` : null}
      ${me.verified === false ? html`<span class="achip warn">unverified</span>` : null}
    </div>

    <${Meter} u=${me.usage}/>
    <p class="ameter"><span>Chats</span>
      <b>${S.sessions.length}${cap ? ' of ' + cap : ''}</b></p>

    <label class="alab" for="acct-name">Display name</label>
    <div class="arow">
      <span class="fld"><input id="acct-name" value=${name} maxLength=${80}
        autoComplete="name" onKeyDown=${e => { e.stopPropagation();
          if(e.key === 'Enter') rename(); }}
        onInput=${e => setName(e.target.value)}/></span>
      <button type="button" class="cbtn"
        disabled=${busy === 'name' || name.trim() === (me.name || '')}
        onClick=${rename}>${busy === 'name' ? 'Saving…' : 'Save'}</button>
    </div>

    ${!pw
      ? html`<button type="button" class="alink" onClick=${() => setPw(true)}>
          Change password</button>`
      : html`<div class="apw">
          <label class="alab" for="acct-cur">Current password</label>
          <span class="fld"><input id="acct-cur" type="password" value=${cur}
            autoComplete="current-password" onKeyDown=${e => e.stopPropagation()}
            onInput=${e => setCur(e.target.value)}/></span>
          <label class="alab" for="acct-new">New password</label>
          <span class="fld"><input id="acct-new" type="password" value=${nxt}
            autoComplete="new-password" onKeyDown=${e => { e.stopPropagation();
              if(e.key === 'Enter') change(); }}
            onInput=${e => setNxt(e.target.value)}/></span>
          <small class="ahint">At least ${min} characters. Signing in again
            everywhere else is up to you.</small>
          <div class="arow">
            <button type="button" class="cbtn"
              onClick=${() => { setPw(false); setCur(''); setNxt(''); }}>Cancel</button>
            <button type="button" class="cbtn pri"
              disabled=${busy === 'pw' || !cur || nxt.length < min}
              onClick=${change}>${busy === 'pw' ? 'Changing…' : 'Change it'}</button>
          </div>
        </div>`}

    <div class="asep"></div>
    <button type="button" class="alink" disabled=${busy === 'exp'} onClick=${grab}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M8 11l4 4 4-4"/>
        <path d="M5 19h14"/></svg>
      ${busy === 'exp' ? 'Collecting everything…' : 'Download all my data'}</button>
    ${me.role === 'admin'
      ? html`<a class="alink" href="/app/admin/">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V9m5 10V5m5 14v-7m5 7V8"/></svg>
          Admin panel</a>`
      : null}
    <button type="button" class="alink out" disabled=${busy === 'out'} onClick=${out}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5H6v14h8"/>
        <path d="M12 12h8M17 9l3 3-3 3"/></svg>
      ${busy === 'out' ? 'Signing out…' : 'Sign out'}</button>

    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  </div>`;
}

/* ---------------- maps that were here before the account ----------------
   Shown once, and only if this browser actually has something in it. The
   second paragraph is not decoration: people are right to want to know what
   happens to the copy they already have. */
export function Offer(){
  const o = S.ui.offer;
  if(!o) return null;
  const n = o.n, many = n !== 1;
  return html`<div class="modal" role="dialog" aria-modal="true"
      aria-labelledby="offer-h">
    <div class="mcard">
      <h2 id="offer-h">${n} ${many ? 'maps' : 'map'} already in this browser</h2>
      <p>${many ? 'They were' : 'It was'} made before you signed in,
        so ${many ? 'they are' : 'it is'} only on this machine.
        Copy ${many ? 'them' : 'it'} into your account?</p>
      <p class="mnote">Once ${many ? 'they are' : 'it is'} in your account this
        browser's copy is cleared, so a shared computer keeps nothing.</p>
      ${o.err ? html`<p class="aerr" role="alert">${o.err}</p>` : null}
      <div class="mrow">
        <button type="button" class="cbtn" disabled=${o.busy}
          onClick=${declineImport}>Leave ${many ? 'them' : 'it'} here</button>
        <button type="button" class="cbtn pri" disabled=${o.busy}
          onClick=${runImport}>${o.busy
            ? 'Copying ' + o.done + ' of ' + n + '…'
            : 'Copy ' + (many ? 'them' : 'it') + ' over'}</button>
      </div>
    </div>
  </div>`;
}

/* ---------------- the free trial ----------------
   A guest, at one of two ceilings: the number of chats this browser will start,
   or the number of model calls the server will pay for. Neither is an error and
   neither loses anything, so unlike Wall this one closes — what they have made
   is still on screen, still editable, still exportable. It is an invitation, and
   the last paragraph is the part that matters: the work follows them in.

   'invite' is the third way in, from the footer button, and it is why the
   heading is computed rather than written: somebody who clicked their own name
   has not hit a limit, and telling them they have would be a lie they can check
   by starting another chat. */
export function GuestWall(){
  const why = S.ui.guestWall;
  if(!why || !S.guest) return null;
  const n = S.guest.chats, free = chatsLeft(), one = S.sessions.length === 1;
  const to = q => '/app/auth/?next=' + encodeURIComponent('/app/') + q;
  const head = why === 'calls' ? 'That is the whole free trial'
    : why === 'invite' ? 'You are here as a guest'
    : n === 1 ? 'That was the free chat' : 'That is all ' + n + ' free chats';
  const body = why === 'calls'
    ? 'Every free mind map and plan on this browser has been used. An account '
      + 'is free and carries on from here.'
    : why === 'invite'
    ? 'Nothing here needs an account, and '
      + (free === 0 ? 'the free chats on this browser are used up'
         : free === 1 ? 'there is one free chat left on this browser'
         : 'there are ' + free + ' free chats left on this browser')
      + '. An account is free, lifts the limit, and keeps every chat so they are '
      + 'still here on your next machine.'
    : 'An account lifts the limit, and keeps every chat so they are still '
      + 'here on your next machine.';
  return html`<div class="modal" role="dialog" aria-modal="true"
      aria-labelledby="gw-h">
    <div class="mcard">
      <h2 id="gw-h">${head}</h2>
      <p>${body}</p>
      <p class="mnote">${one
        ? 'Nothing is lost by waiting: the map you have made is still open, and '
          + 'when you do sign in Cognix offers to copy it across.'
        : 'Nothing is lost by waiting: the maps you have made are still open, and '
          + 'when you do sign in Cognix offers to copy them across.'}</p>
      <div class="mrow">
        <button type="button" class="cbtn" onClick=${closeWall}>Keep looking</button>
        <a class="cbtn pri" href=${to('&new=1')}>Create an account</a>
      </div>
      <button type="button" class="alink mid"
        onClick=${() => location.assign(to(''))}>I already have one — sign in</button>
    </div>
  </div>`;
}

/* ---------------- the session ran out ----------------
   persist.js stops pushing the moment the server says 401, so from here on
   nothing typed would be kept. The honest thing is to say so, offer the way
   back in, and offer a file for whatever is on screen — signing in again
   reloads the app, and anything since the last save goes with it. */
export function Wall(){
  if(!S.ui.authWall) return null;
  const back = () => {
    const here = location.pathname + location.search;
    location.replace('/app/auth/?next=' + encodeURIComponent(here));
  };
  const rescue = () => {
    const keep = S.sessions.filter(s => s.full !== false).map(s => ({
      title: s.title, updated: s.updated, sheet: s.sheet }));
    const file = 'cognix-unsaved-' + stamp() + '.json';
    note(download(file, JSON.stringify({ app: 'cognix', unsaved: true,
      exported: new Date().toISOString(), sessions: keep }, null, 2))
      ? 'Saved ' + file : 'The browser would not start that download');
  };
  return html`<div class="modal hard" role="alertdialog" aria-modal="true"
      aria-labelledby="wall-h">
    <div class="mcard">
      <h2 id="wall-h">Your sign-in ran out</h2>
      <p>Nothing more can be saved from this tab. Sign in again to carry on —
        anything changed since the last save will need doing again, so take the
        file first if it matters.</p>
      <div class="mrow">
        <button type="button" class="cbtn" onClick=${rescue}>Save a file first</button>
        <button type="button" class="cbtn pri" onClick=${back}>Sign in again</button>
      </div>
    </div>
  </div>`;
}
