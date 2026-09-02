/* =====================================================================
   the console shell: who you are, where you are, and the seven places to be.

   The route lives in the fragment. That is not a stylistic choice — this
   server hands out static files, so /app/admin/users is a path with no file
   behind it, while /app/admin/#/users is one page that can hold a hundred
   views and still survive a reload and a back button.

   A fragment also never reaches a server, which is exactly right for a search
   term typed into an administrator's box: it stays out of the access log.
   ===================================================================== */
import { html, useState, useEffect } from '../src/h.js';
import * as ses from '../src/session.js';
import { Mark, NAME } from '../src/brand.js';
import { qs, tidy } from './net.js';
import { Overview } from './Overview.js';
import { Users } from './Users.js';
import { UserOne } from './UserOne.js';
import { Usage } from './Usage.js';
import { Audit } from './Audit.js';
import { Chats } from './Chats.js';
import { Settings } from './Settings.js';
import { Gateway } from './Gateway.js';

const TABS = [
  ['overview', 'Overview'], ['users', 'Accounts'], ['usage', 'Usage'],
  ['chats', 'Chats'], ['audit', 'Audit'], ['settings', 'Settings'],
  ['gateway', 'Gateway'],
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/* ---------------- the fragment, both ways ----------------
   Read defensively: this string is in the address bar, so it is whatever
   somebody last pasted there, not whatever this page last wrote. */
export function readHash(){
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const cut = raw.indexOf('?');
  const path = (cut < 0 ? raw : raw.slice(0, cut)).split('/').filter(Boolean);
  const q = {};
  if(cut >= 0){
    new URLSearchParams(raw.slice(cut + 1)).forEach((v, k) => {
      q[k] = tidy(v, 120);
    });
  }
  const top = path[0] || 'overview';
  const known = TABS.some(t => t[0] === top);
  return { top: known ? top : 'overview', id: path[1] || '', q };
}

export function go(path, query){
  const next = '#/' + String(path || 'overview') + qs(query || {});
  if(next === location.hash) return;
  location.hash = next;
}

export function Console(){
  const [route, setRoute] = useState(readHash);
  const [me, setMe] = useState(ses.ME.user);
  useEffect(() => {
    const read = () => setRoute(readHash());
    window.addEventListener('hashchange', read);
    const off = ses.onUser(setMe);
    return () => { window.removeEventListener('hashchange', read); off(); };
  }, []);

  /* the whole page scrolls to the top on a view change; a table left scrolled
     halfway down while a different table renders reads as a broken page */
  useEffect(() => { window.scrollTo(0, 0); }, [route.top, route.id]);

  const out = async () => {
    try{ await ses.logout(); }
    finally{ location.replace('/app/auth/'); }
  };

  const body = () => {
    if(route.top === 'users' && UUID.test(route.id))
      return html`<${UserOne} id=${route.id} go=${go}/>`;
    if(route.top === 'users') return html`<${Users} q=${route.q} go=${go}/>`;
    if(route.top === 'usage') return html`<${Usage} q=${route.q} go=${go}/>`;
    if(route.top === 'chats') return html`<${Chats} q=${route.q} go=${go}/>`;
    if(route.top === 'audit') return html`<${Audit} q=${route.q} go=${go}/>`;
    if(route.top === 'settings') return html`<${Settings}/>`;
    if(route.top === 'gateway') return html`<${Gateway}/>`;
    return html`<${Overview} go=${go}/>`;
  };

  return html`<div class="kwrap">
    <header class="khead">
      <a class="kbrand" href="/app/" title="Back to Cognix">
        <${Mark} size=${20}/><b>${NAME}</b><span class="kdim">console</span>
      </a>
      <span class="ksp"></span>
      ${me ? html`<span class="kme" title=${me.email || ''}>
        <b>${tidy(me.name || me.email, 40)}</b>
        <small>${me.role === 'admin' ? 'administrator' : (me.role || '')}</small>
      </span>` : null}
      <a class="cbtn" href="/app/">Open the app</a>
      <button type="button" class="cbtn" onClick=${out}>Sign out</button>
    </header>

    <nav class="knav" aria-label="Console sections">
      ${TABS.map(t => html`<button key=${t[0]} type="button"
        class=${'ktab' + (route.top === t[0] ? ' on' : '')}
        aria-current=${route.top === t[0] ? 'page' : null}
        onClick=${() => go(t[0])}>${t[1]}</button>`)}
    </nav>

    <main class="kmain">${body()}</main>

    <footer class="kfoot">
      <span>Every change made here is written to the audit log as your
        account.</span>
      <button type="button" class="klink" onClick=${() => go('audit')}>
        See the log</button>
    </footer>
  </div>`;
}

/* ---------------- the two pages that are not the console ----------------
   Reached by typing the URL. Neither is an error: local mode is a real way to
   run this, and a signed-in person who is not an administrator is a normal
   person who followed a link. */
export const Local = () => html`<div class="kwrap">
  <main class="kmain"><div class="kcard"><div class="kcbody">
    <h1 class="kh1">${NAME} runs without a database here</h1>
    <p class="kp">This instance has no Supabase behind it, so there are no
      accounts, no usage to add up and nothing to administer. Maps are kept in
      each browser instead.</p>
    <p class="kp">Set the Supabase environment variables and restart the server
      and this console starts working — nothing in the app needs changing.</p>
    <a class="cbtn pri" href="/app/">Open ${NAME}</a>
  </div></div></main>
</div>`;

export const NotYours = () => html`<div class="kwrap">
  <main class="kmain"><div class="kcard"><div class="kcbody">
    <h1 class="kh1">That is the administrator console</h1>
    <p class="kp">This account is not an administrator, so there is nothing here
      for it. Nothing was refused quietly: the server, this page and the
      database all say the same thing.</p>
    <a class="cbtn pri" href="/app/">Back to ${NAME}</a>
  </div></div></main>
</div>`;
