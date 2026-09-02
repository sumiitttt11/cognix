/* =====================================================================
   left tray. The two lists that used to hold made-up document names now
   hold the real thing: every previous chat, grouped the way Claude and
   ChatGPT group them — Today, Yesterday, Previous 7 days, Previous 30
   days, then by month. Click to open, hover to rename or delete, and
   search across titles and message text.

   With an account behind it the list arrives as titles alone and a chat is
   fetched when it is opened, so a row can be in three states rather than
   two: it has a map, it has none, or it is not known yet. The third one
   gets its own dot — claiming an unread chat is empty is a lie the user
   would only catch by clicking it.
   ===================================================================== */
import { html, useState, useRef, useEffect } from './h.js';
import { bucket, BUCKETS, ago, shorten } from './util.js';
import { S, emit, newChat, openChat, renameChat, deleteChat, note,
         chatsLeft, guestWall, isPhone } from './store.js';
import { Mark } from './brand.js';

function grouped(list){
  const seen = {}, order = [];
  list.forEach(s => {
    const b = bucket(s.updated || s.ts);
    if(!seen[b]){ seen[b] = []; order.push(b); }
    seen[b].push(s);
  });
  /* fixed buckets first in their canonical order, months after */
  const head = BUCKETS.filter(b => seen[b]);
  const tail = order.filter(b => BUCKETS.indexOf(b) < 0);
  return head.concat(tail).map(b => ({ name: b, items: seen[b] }));
}

/* On a phone this tray is a drawer over the document, so anything that changes
   which chat is on screen has to shut it — otherwise the chat somebody just
   picked is behind the list they picked it from. Which width counts as a phone
   is store.js's isPhone(), so this and the media query agree by construction. */
function shut(){
  if(!isPhone() || S.ui.nosid) return false;
  S.ui.nosid = true; return true;
}
/* openChat() does nothing when that chat is already open, and nothing includes
   not re-rendering — so tapping the chat you are already in would shut the
   drawer in the store and leave it on screen. In that one case this emits. */
function pick(id){ const closed = shut(); openChat(id); if(closed && id === S.curId) emit(); }
function start(){ shut(); newChat(); note('New chat'); }

/* The row used to be one <button> with two more inside it. Nested buttons are
   invalid, and the inner ones were unreachable by keyboard — rename and delete
   existed only for a mouse. So the row is a container now, and open / rename /
   delete are three siblings in the tab order. */
function Item({ s, on }){
  const [ren, setRen] = useState(false);
  const [txt, setTxt] = useState(s.title);
  const inp = useRef(null);
  useEffect(() => { if(ren && inp.current){ inp.current.focus(); inp.current.select(); } }, [ren]);
  const boxes = s.sheet && s.sheet.map ? s.sheet.map.nodes.length : 0;
  const unread = s.full === false;              // titles came without the map
  const dot = 'dot' + (unread ? ' pend' : (boxes ? ' fill' : ''))
    + (S.ui.loading === s.id ? ' load' : '');
  const what = boxes ? ' · ' + boxes + ' boxes'
    : (unread ? (s.count ? ' · ' + s.count + ' messages' : ' · not opened yet') : '');

  if(ren) return html`<div class=${'item' + (on ? ' on' : '')}>
    <input class="ren" ref=${inp} value=${txt} maxLength=${60} aria-label="Chat name"
      onInput=${e => setTxt(e.target.value)}
      onKeyDown=${e => { e.stopPropagation();
                         if(e.key === 'Enter'){ renameChat(s.id, txt); setRen(false); }
                         if(e.key === 'Escape'){ setTxt(s.title); setRen(false); } }}
      onBlur=${() => { renameChat(s.id, txt); setRen(false); }}/></div>`;

  return html`<div class=${'item' + (on ? ' on' : '')}>
    <button type="button" class="iopen" onClick=${() => pick(s.id)}
        aria-current=${on ? 'true' : null}
        aria-busy=${S.ui.loading === s.id ? 'true' : null}
        title=${s.title + what}>
      <span class=${dot} aria-hidden="true"></span>
      <span class="nm">${s.title}</span>
    </button>
    <span class="acts">
      <button type="button" title="Rename" aria-label=${'Rename ' + s.title}
        onClick=${() => { setTxt(s.title); setRen(true); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L20 8l-4-4L4 16z"/></svg></button>
      <button type="button" title="Delete" aria-label=${'Delete ' + s.title}
        onClick=${() => {
          if(S.sessions.length === 1 && s.full !== false && !(s.sheet && s.sheet.map))
            return note('Nothing to delete yet');
          deleteChat(s.id); note('Chat deleted'); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>
    </span>
    <span class="when">${ago(s.updated || s.ts)}</span>
  </div>`;
}

export function Tray(){
  const u = S.ui;
  const q = u.query.trim().toLowerCase();
  const hit = s => !q || String(s.title || '').toLowerCase().indexOf(q) >= 0
    || ((s.sheet && s.sheet.log) || []).some(m => String(m.text || '').toLowerCase().indexOf(q) >= 0)
    || !!(s.sheet && s.sheet.map
          && s.sheet.map.nodes.some(n => String(n.text || '').toLowerCase().indexOf(q) >= 0));
  const list = S.sessions.slice().sort((a, b) => (b.updated || b.ts) - (a.updated || a.ts))
    .filter(hit);

  return html`<aside class="side" aria-label="Chats and tabs">
    <div class="tabs" role="tablist">
      <button type="button" role="tab" aria-selected=${u.tab === 'map'}
        class=${'tab' + (u.tab === 'map' ? ' on' : '')}
        onClick=${() => { u.tab = 'map'; emit(); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h6M4 12h6M4 18h6"/><circle cx="17" cy="12" r="3"/>
          <path d="M10 6l4 4M10 18l4-4"/></svg>Mind Maps</button>
      <button type="button" role="tab" aria-selected=${u.tab === 'plan'}
        class=${'tab' + (u.tab === 'plan' ? ' on' : '')}
        onClick=${() => { u.tab = 'plan'; emit(); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h11l3 3v13H5z"/><path d="M8 10h8M8 14h6"/></svg>Plan</button>
    </div>

    <button type="button" class="srow" onClick=${start}>
      <span class="si"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span>
      New<kbd>Ctrl N</kbd></button>
    <button type="button" class="srow" onClick=${() => { shut(); u.insp = !u.insp; emit(); }}>
      <span class="si"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/>
        <circle cx="9" cy="6" r="2" fill="currentColor" stroke="none"/>
        <circle cx="15" cy="12" r="2" fill="currentColor" stroke="none"/>
        <circle cx="8" cy="18" r="2" fill="currentColor" stroke="none"/></svg></span>
      Customize<kbd>Ctrl K</kbd></button>

    <div class="scroll">
      <div class="search">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/>
          <path d="M20 20l-4-4"/></svg>
        <input type="search" aria-label="Search chats" placeholder="Search chats"
          maxLength=${80} value=${u.query}
          onKeyDown=${e => e.stopPropagation()}
          onInput=${e => { u.query = e.target.value; emit(); }}/>
      </div>
      ${grouped(list).map(g => html`<${'div'} key=${g.name}>
        <div class="ghead"><span>${g.name}</span><span class="gsp"></span>
          ${g.name === 'Today' ? html`<button type="button" class="gact" title="New chat"
            aria-label="New chat" onClick=${start}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>` : null}
        </div>
        <div>${g.items.map(s => html`<${Item} key=${s.id} s=${s} on=${s.id === S.curId}/>`)}</div>
      <//>`)}
      ${!list.length ? html`<div class="none">${q ? 'No chat matches that.' : 'No chats yet.'}</div>` : null}
    </div>

    <${Foot}/>
  </aside>`;
}

/* ---------------- the footer ----------------
   Local mode has nobody to name, so it says where the chats are instead. With
   an account it is the account: the name, what is left of this month, and one
   click into everything else. The saving state lives here too, because the
   footer is the one part of the app that is always on screen.

   A guest is the third case, and the reason this matters more than it looks: the
   footer is the only chrome that is always visible, which on a phone makes it
   the way in. So for a visitor it names the trial, counts it down, and opens the
   same invitation the ceiling opens — never the account popover, which has no
   account to draw and would be a button that does nothing. */
function Foot(){
  const me = S.me, g = !me && S.guest;
  const u = me && me.usage;
  const cap = u && !u.unlimited && typeof u.left === 'number'
    ? u.left + ' of ' + u.cap + ' left' : null;
  const free = g ? chatsLeft() : 0;
  const who = me ? (me.name || String(me.email || '').split('@')[0] || 'Account')
    : g ? 'Guest' : 'This browser';
  const state = !S.cloud ? null
    : S.ui.authWall ? 'off'
    : S.ui.saveErr ? 'bad'
    : S.ui.syncing ? 'busy' : 'ok';
  const said = state === 'off' ? 'Signed out — nothing is being saved'
    : state === 'bad' ? S.ui.saveErr
    : state === 'busy' ? 'Saving…'
    : state === 'ok' ? 'Everything is saved to your account'
    : g ? (free === 0 ? 'No free chats left on this browser — sign in to carry on'
      : (free === 1 ? 'One free chat left on this browser' : free
          + ' free chats left on this browser')
        + ' — kept here only, until you make an account')
    : S.sessions.length + ' chats stored in this browser';
  const open = g ? !!S.ui.guestWall : S.ui.acct;

  return html`<div class="sfoot">
    <button type="button" class=${'who' + (open ? ' on' : '')}
      aria-haspopup="dialog" aria-expanded=${open ? 'true' : 'false'}
      title=${me ? (me.email || who)
        : g ? 'Sign in, or make a free account'
        : 'This browser is the storage'}
      onClick=${() => { if(g) return guestWall('invite');
                        S.ui.acct = !S.ui.acct; emit(); }}>
      <span class="av"><${Mark} size=${14}/></span>
      <b>${shorten(who, 18)}</b>
      <small>${g ? '· ' + free + ' free' : cap ? '· ' + cap : '· Cognix'}</small>
    </button>
    <span class="gsp" style=${{ flex:1 }}></span>
    <button type="button" class=${'gact sync' + (state ? ' ' + state : '')}
      aria-label="Storage" title=${said} onClick=${() => note(said)}>
      ${state === 'busy'
        ? html`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.6"/>
            <path d="M20 4v4h-4"/></svg>`
        : state === 'bad' || state === 'off'
        ? html`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5"/>
            <circle cx="12" cy="16.5" r="1" fill="currentColor" stroke="none"/>
            <path d="M12 3l9 17H3z"/></svg>`
        : state === 'ok'
        ? html`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13l4 4 12-12"/></svg>`
        : html`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`}
    </button>
  </div>`;
}
