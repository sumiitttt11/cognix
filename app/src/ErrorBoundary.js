/* =====================================================================
   the crash screen.

   A thrown render used to leave a blank page: React unmounts the whole
   tree and nothing says why, while the user's maps are still sitting in
   localStorage. This catches it, says what broke, and offers the three
   things that are actually useful — try again, take the data out, start
   clean. It deliberately imports nothing but the storage keys: anything
   it renders could be the thing that just threw.
   ===================================================================== */
import React, { html } from './h.js';
import { KEYS } from './store.js';
import { downloadBackup } from './backup.js';

/* park the unreadable blob rather than deleting it — "start fresh" should
   cost the user nothing they have not already lost */
function wipe(){
  try{
    const raw = localStorage.getItem(KEYS.cur);
    if(raw != null){
      try{ localStorage.setItem(KEYS.bad, raw); }catch(e){}
    }
    localStorage.removeItem(KEYS.cur);
    localStorage.removeItem(KEYS.old);
  }catch(e){}
}

const detail = (err, stack) => {
  const head = String((err && (err.stack || err.message)) || err || 'Unknown error');
  return (head + (stack ? '\n\ncomponent stack:' + stack : '')).slice(0, 1600);
};

export class ErrorBoundary extends React.Component {
  constructor(props){
    super(props);
    this.state = { err: null, stack: null, armed: false, saved: null, open: false };
  }
  static getDerivedStateFromError(err){ return { err }; }
  componentDidCatch(err, info){
    this.setState({ stack: info && info.componentStack });
    try{ console.error('Cognix crashed:', err, info); }catch(e){}
  }
  render(){
    const st = this.state;
    if(!st.err) return this.props.children;
    const msg = String((st.err && st.err.message) || st.err || 'Unknown error').slice(0, 300);
    return html`<div class="crash" role="alert">
      <div class="crashcard">
        <svg class="crashi" viewBox="0 0 24 24" width="26" height="26" fill="none"
          stroke="currentColor" stroke-width="1.8" aria-hidden="true">
          <path d="M12 3l9 16H3z"/><path d="M12 9v5"/><circle cx="12" cy="17" r=".9" fill="currentColor"/>
        </svg>
        <h1>Cognix hit an error and stopped drawing</h1>
        <p>Your maps are still saved in this browser — this screen has not touched them.
           Reloading fixes most of these. If it comes back, download a copy first,
           then start fresh.</p>
        <p class="crashmsg">${msg}</p>
        <div class="crashacts">
          <button type="button" class="cbtn pri" onClick=${() => location.reload()}>Reload</button>
          <button type="button" class="cbtn"
            onClick=${() => this.setState({ saved: downloadBackup() ? 'ok' : 'no' })}>Download my chats</button>
          ${st.armed
            ? html`<button type="button" class="cbtn dang"
                onClick=${() => { wipe(); location.reload(); }}>Yes — clear and reload</button>`
            : html`<button type="button" class="cbtn"
                onClick=${() => this.setState({ armed: true })}>Start fresh…</button>`}
        </div>
        ${st.saved === 'ok' ? html`<p class="crashok">Backup file written.</p>` : null}
        ${st.saved === 'no' ? html`<p class="crashok">This browser blocked the download — copy the
          details below instead.</p>` : null}
        ${st.armed ? html`<p class="crashok">Start fresh empties the chat list. The old data is kept
          under a separate key, not deleted.</p>` : null}
        <button type="button" class="crashmore" aria-expanded=${!!st.open}
          onClick=${() => this.setState({ open: !st.open })}>
          ${st.open ? 'Hide details' : 'Show details'}</button>
        ${st.open ? html`<pre class="crashdet">${detail(st.err, st.stack)}</pre>` : null}
      </div>
    </div>`;
  }
}

export default ErrorBoundary;
