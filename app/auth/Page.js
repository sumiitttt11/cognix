/* =====================================================================
   the way in.

   One page and five things it can be doing; the URL decides which.

     in        sign in — the default, and where everything else returns to
     up        sign up — hidden entirely when signups are closed
     forgot    one field, and the same answer whether or not that address has
               an account, because any other answer is a list of who does
     reset     a new password, proved by the token in a mail link
     landing   a confirmation or invite link arriving with tokens in the URL
               fragment: they are traded for a cookie and wiped from history
               before anything is drawn

   Those tokens are the only secret this page ever holds, and it holds them for
   as long as one request takes. Everything else goes through session.js, which
   speaks only to this server's own /api/auth/* — no Supabase key and no
   Supabase token is ever in this document.

   The page's own policy says form-action 'none', so no form here can post
   anywhere at all. Every one is handled with preventDefault and a fetch, which
   is also how the errors end up as sentences instead of a new page.
   ===================================================================== */
import { html, useState, useEffect, useRef } from '../src/h.js';
import * as ses from '../src/session.js';
import { Mark, NAME } from '../src/brand.js';

const MIN = 10;                        /* mirrors crypto.weak on the server */

/* One line, capped, with the control characters out. This text can arrive from
   GoTrue by way of the address bar, so it is not ours until it is cleaned —
   and it is cleaned by code point rather than by a regex, because a literal
   control character in a source file is invisible in every editor. */
function tidy(s){
  const raw = String(s || '');
  let out = '';
  for(let i = 0; i < raw.length; i++){
    const c = raw.charCodeAt(i);
    out += (c < 32 || c === 127) ? ' ' : raw.charAt(i);
  }
  return out.replace(/\s+/g, ' ').trim().slice(0, 240);
}

/* ---------------- what the URL says to do ----------------
   Read once, at import, and then erased: a fragment never reaches a server but
   it does sit in the address bar and in history, and a reset token in history
   is a reset token somebody else can press back to. */
function firstRoute(){
  const f = ses.fragment(), marks = f.marks || [];
  const has = k => typeof f[k] === 'string' && !!f[k];
  const out = { view: 'in', token: '', refresh: '', err: '', kind: '' };
  /* ?new=1 means "I came here to make one" — the app's free-trial wall sends
     people here, and landing them on the sign-in form would be one wrong click
     for everybody who has no account by definition. */
  if(/[?&]new=1(?:&|$)/.test(location.search)) out.view = 'up';
  if(has('error_description') || has('error')){
    /* an expired or already-used link comes back as an error in the fragment */
    out.err = tidy(f.error_description || f.error)
      || 'That link is no longer valid. Ask for a new one.';
  } else if(marks.indexOf('reset') >= 0 || f.type === 'recovery'){
    if(has('access_token')){ out.view = 'reset'; out.token = f.access_token;
                             out.kind = 'recovery'; }
    else out.err = 'That reset link did not carry a token. Ask for a new one.';
  } else if(f.type === 'invite' && has('access_token')){
    out.view = 'reset'; out.token = f.access_token; out.kind = 'invite';
  } else if(has('access_token')){
    out.view = 'landing'; out.token = f.access_token;
    out.refresh = f.refresh_token || ''; out.kind = f.type || 'signup';
  }
  if(location.hash) ses.clearFragment();
  return out;
}
export const ROUTE = firstRoute();

/* the same rule the server applies, so the answer arrives while typing rather
   than after a round trip. The server is still the one that decides. */
function weak(pw, addr){
  const p = String(pw || '');
  if(p.length < MIN) return 'Use at least ' + MIN + ' characters.';
  if(p.length > 200) return 'That is longer than 200 characters.';
  if(!p.trim()) return 'A password of spaces is not a password.';
  const local = String(addr || '').split('@')[0].toLowerCase();
  if(local.length > 3 && p.toLowerCase().indexOf(local) >= 0)
    return 'Do not put your email address in your password.';
  const seen = {};
  let n = 0;
  for(let i = 0; i < p.length; i++){
    const c = p.charAt(i);
    if(!seen[c]){ seen[c] = 1; n++; }
  }
  if(n < 5) return 'That is too few different characters.';
  return '';
}

const looksEmail = s => /^[^\s@]+@[^\s@.]+\.[^\s@]+$/.test(String(s || '').trim());

/* ---------------- the page ----------------
   One component holds every field, because the fields are the page: five views
   over one email box and one password box, and carrying the address from the
   sign-in form into the reset form is the whole reason not to split them up. */
export function Page({ cfg }){
  const [want, setView] = useState(ROUTE.view);
  const [busy, setBusy] = useState(ROUTE.view === 'landing' ? 'landing' : '');
  const [err, setErr] = useState(ROUTE.err);
  const [ok, setOk] = useState('');
  const [field, setField] = useState('');       // which box the error belongs to
  const [done, setDone] = useState(null);       // the terminal card, if any
  const [addr, setAddr] = useState('');
  const [name, setName] = useState('');
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [show, setShow] = useState(false);
  const [again, setAgain] = useState('');       // an unconfirmed address, if any
  const gate = useRef({ busy: '', leaving: false, live: true });
  useEffect(() => () => { gate.current.live = false; }, []);

  const signups = !!(cfg && cfg.signups);
  const min = (cfg && cfg.password_min) || MIN;
  /* ?new=1 asked for the sign-up form. If this instance is not taking new
     accounts, that form would only be refused by the server, so the sign-in
     one is what gets drawn — with the sentence that says why. */
  const view = want === 'up' && !signups ? 'in' : want;

  /* the sign-in succeeded, so this page is over. Nothing is reset on the way
     out — a form that flickers back to life while the browser is navigating
     looks like the click did not take. */
  const leave = () => {
    gate.current.leaving = true;
    setBusy('leaving');
    location.replace(ses.nextPath('/app/'));
  };

  const go = async (label, fn) => {
    if(gate.current.busy) return;
    gate.current.busy = label;
    setBusy(label); setErr(''); setOk(''); setField('');
    try{
      await fn();
    }catch(e){
      if(gate.current.live){
        setErr(tidy((e && e.message) || 'That did not work.'));
        setField((e && e.field) || '');
      }
    }finally{
      gate.current.busy = '';
      if(gate.current.live && !gate.current.leaving) setBusy('');
    }
  };

  const swap = v => { setView(v); setErr(''); setOk(''); setField('');
                      setPw2(''); setAgain(''); };

  /* ---------------- the five things it can do ----------------
     Each one checks what it can before spending a request, because "enter your
     password" is a better answer than a round trip that says the same thing. */
  const signIn = () => go('in', async () => {
    const who = addr.trim().toLowerCase();
    if(!looksEmail(who))
      throw ses.fail('That does not look like an email address.', 0, { field: 'email' });
    if(!pw) throw ses.fail('Enter your password.', 0, { field: 'password' });
    try{
      await ses.login(who, pw);
    }catch(e){
      /* the one refusal with a way out of it: offer the link again rather than
         leave somebody guessing at a password that was never the problem */
      if(e && e.status === 403 && /confirm/i.test(String(e.message))) setAgain(who);
      throw e;
    }
    leave();
  });

  const signUp = () => go('up', async () => {
    const who = addr.trim().toLowerCase();
    if(!looksEmail(who))
      throw ses.fail('That does not look like an email address.', 0, { field: 'email' });
    const why = weak(pw, who);
    if(why) throw ses.fail(why, 0, { field: 'password' });
    const out = await ses.signup(who, pw, name.trim());
    if(out && out.user){ leave(); return; }      // confirmations are off
    if(!gate.current.live) return;
    setDone({ h: 'Check your email', addr: who, resend: true,
      p: tidy((out && out.message) || ('A confirmation link is on its way to '
        + who + '. Open it, then sign in.')) });
    setView('done');
  });

  const forgot = () => go('forgot', async () => {
    const who = addr.trim().toLowerCase();
    if(!looksEmail(who))
      throw ses.fail('Enter the address you signed up with.', 0, { field: 'email' });
    const out = await ses.recover(who);
    if(!gate.current.live) return;
    setDone({ h: 'Check your email', addr: who,
      p: tidy((out && out.message) || 'If that address has an account, a reset '
        + 'link is on its way.') });
    setView('done');
  });

  /* the token in the mail link is the proof; it can do this one thing and then
     it is spent, so the reply clears the cookie and asks for a sign-in */
  const setNew = () => go('reset', async () => {
    const why = weak(pw, addr);
    if(why) throw ses.fail(why, 0, { field: 'password' });
    if(pw2 !== pw) throw ses.fail('Those two do not match.', 0, { field: 'again' });
    const out = await ses.reset(ROUTE.token, pw);
    if(!gate.current.live) return;
    if(out && out.email) setAddr(out.email);
    setPw(''); setPw2('');
    setDone({ h: ROUTE.kind === 'invite' ? 'Password set' : 'Password changed',
      p: tidy((out && out.message) || 'Sign in with it.'), signin: true });
    setView('done');
  });

  const resend = () => go('resend', async () => {
    const who = ((done && done.addr) || again || addr).trim().toLowerCase();
    if(!looksEmail(who))
      throw ses.fail('Enter your email address first.', 0, { field: 'email' });
    const out = await ses.resend(who);
    if(!gate.current.live) return;
    setAgain('');
    setOk(tidy((out && out.message) || 'Another link is on its way.'));
  });

  /* a confirmation or invite link: the tokens are traded for a cookie here, and
     this is the only view that starts busy */
  useEffect(() => {
    if(ROUTE.view !== 'landing') return;
    go('landing', async () => {
      const out = await ses.adopt(ROUTE.token, ROUTE.refresh);
      if(out && out.user){ leave(); return; }
      /* a link with no refresh token confirms the address but cannot keep
         anybody signed in past the hour, so it ends at the sign-in form */
      if(!gate.current.live) return;
      setView('in');
      setOk(tidy((out && out.message) || 'That link is confirmed. Please sign in.'));
    }).then(() => { if(gate.current.live && !gate.current.leaving) setView(v =>
      v === 'landing' ? 'in' : v); });
  }, []);

  /* ---------------- the parts every form is made of ---------------- */
  const bad = k => (field === k ? ' wrong' : '');
  const EYE = show
    ? html`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/>
        <path d="M10.6 6.2A9.6 9.6 0 0112 6c5 0 9 6 9 6a15 15 0 01-2.4 2.9"/>
        <path d="M6.5 8.1A15.6 15.6 0 003 12s4 6 9 6a9 9 0 003.2-.6"/>
        <path d="M9.9 9.9a3 3 0 004.2 4.2"/></svg>`
    : html`<svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 12s4-6 9-6 9 6 9 6-4 6-9 6-9-6-9-6z"/>
        <circle cx="12" cy="12" r="2.6"/></svg>`;

  const mail = (id, auto) => html`<span class=${'fld' + bad('email')}>
    <input id=${id} type="email" value=${addr} autoComplete=${auto}
      inputMode="email" autoCapitalize="off" spellCheck=${false} maxLength=${254}
      placeholder="you@company.com" onInput=${e => setAddr(e.target.value)}/>
  </span>`;

  const secret = (id, auto, key, val, set, eye) => html`<span class=${'fld' + bad(key)}>
    <input id=${id} type=${show ? 'text' : 'password'} value=${val}
      autoComplete=${auto} maxLength=${200} onInput=${e => set(e.target.value)}/>
    ${eye ? html`<button type="button" class="aeye" title=${show ? 'Hide' : 'Show'}
      aria-label=${show ? 'Hide password' : 'Show password'}
      onClick=${() => setShow(!show)}>${EYE}</button>` : null}
  </span>`;

  const send = (label, working, what) => html`<button type="submit"
    class="cbtn pri wide" disabled=${!!busy}>${busy === what ? working : label}</button>`;

  const tabs = html`<div class="authtabs">
    <button type="button" class=${'atab' + (view === 'in' ? ' on' : '')}
      aria-pressed=${view === 'in'} onClick=${() => swap('in')}>Sign in</button>
    <button type="button" class=${'atab' + (view === 'up' ? ' on' : '')}
      aria-pressed=${view === 'up'} onClick=${() => swap('up')}>Create account</button>
  </div>`;

  /* ---------------- the five views ---------------- */
  const forms = {};

  forms.in = html`<form class="aform" onSubmit=${e => { e.preventDefault(); signIn(); }}>
    ${signups ? tabs : null}
    <label class="alab" for="au-mail">Email</label>
    ${mail('au-mail', 'username')}
    <div class="alabrow">
      <label class="alab" for="au-pw">Password</label>
      <button type="button" class="atiny" onClick=${() => swap('forgot')}>Forgot it?</button>
    </div>
    ${secret('au-pw', 'current-password', 'password', pw, setPw, true)}
    ${send('Sign in', 'Signing in…', 'in')}
    ${!signups ? html`<p class="ahint">New accounts are closed on this instance.
      An administrator can send you an invite.</p>` : null}
  </form>`;

  forms.up = html`<form class="aform" onSubmit=${e => { e.preventDefault(); signUp(); }}>
    ${tabs}
    <label class="alab" for="au-name">Your name</label>
    <span class="fld"><input id="au-name" value=${name} maxLength=${80}
      autoComplete="name" placeholder="Optional"
      onInput=${e => setName(e.target.value)}/></span>
    <label class="alab" for="au-mail2">Email</label>
    ${mail('au-mail2', 'username')}
    <label class="alab" for="au-pw2">Password</label>
    ${secret('au-pw2', 'new-password', 'password', pw, setPw, true)}
    <p class="ahint">At least ${min} characters, and not your email address.
      This account can spend model calls, so it is worth a real one.</p>
    ${send('Create account', 'Creating…', 'up')}
  </form>`;

  forms.forgot = html`<form class="aform" onSubmit=${e => { e.preventDefault(); forgot(); }}>
    <h2 class="ah2">Reset your password</h2>
    <p class="ap">Give the address you signed up with and a link comes back to
      it. The link is good for one hour.</p>
    <label class="alab" for="au-mail3">Email</label>
    ${mail('au-mail3', 'username')}
    ${send('Send the link', 'Sending…', 'forgot')}
    <button type="button" class="atiny mid" onClick=${() => swap('in')}>
      Back to sign in</button>
  </form>`;

  forms.reset = html`<form class="aform" onSubmit=${e => { e.preventDefault(); setNew(); }}>
    <h2 class="ah2">${ROUTE.kind === 'invite' ? 'Choose a password' : 'Set a new password'}</h2>
    <p class="ap">${ROUTE.kind === 'invite'
      ? 'You were invited to Cognix. Pick a password and the account is yours.'
      : 'This link proves the address is yours. It works once.'}</p>
    <label class="alab" for="au-new">New password</label>
    ${secret('au-new', 'new-password', 'password', pw, setPw, true)}
    <label class="alab" for="au-again">Type it again</label>
    ${secret('au-again', 'new-password', 'again', pw2, setPw2, false)}
    <p class="ahint">At least ${min} characters.</p>
    ${send(ROUTE.kind === 'invite' ? 'Set it and finish' : 'Change it',
           'Saving…', 'reset')}
  </form>`;

  /* the landing view has no form: it is one request, and either it succeeds and
     the browser leaves, or it fails and this becomes the sign-in page */
  forms.landing = html`<div class="aform">
    <h2 class="ah2">One moment</h2>
    <p class="ap">Checking the link you followed…</p>
    <div class="abar" role="progressbar" aria-label="Checking your link"><i></i></div>
  </div>`;

  forms.done = html`<div class="aform">
    <h2 class="ah2">${(done && done.h) || 'Done'}</h2>
    <p class="ap">${(done && done.p) || ''}</p>
    <button type="button" class="cbtn pri wide" onClick=${() => swap('in')}>
      ${done && done.signin ? 'Sign in' : 'Back to sign in'}</button>
    ${done && done.resend
      ? html`<button type="button" class="atiny mid" disabled=${!!busy}
          onClick=${resend}>${busy === 'resend' ? 'Sending…'
            : 'Nothing arrived — send it again'}</button>`
      : null}
  </div>`;

  /* ---------------- the card around them ----------------
     Two things an administrator can say to everybody, and they belong here as
     much as in the app: somebody whose account works but whose model calls are
     switched off should find that out before typing a prompt, not after. */
  const st = cfg || {};
  const strip = st.maintenance
    ? { kind: 'work', text: 'Cognix is in maintenance. Signing in works; new maps '
        + 'cannot be generated right now.' }
    : (st.announcement ? { kind: 'say', text: tidy(st.announcement) } : null);
  const sub = { up: 'Make an account', forgot: 'Reset your password',
                reset: 'Almost there', landing: 'Checking your link',
                done: 'One more step' }[view] || 'Sign in to your maps';

  return html`<main class="authwrap">
    <div class="authcard">
      <div class="authtop">
        <${Mark} cls="amark" size=${34}/>
        <b>${NAME}</b>
        <small>${sub}</small>
      </div>
      ${strip ? html`<div class=${'ann ' + strip.kind} role="status">
        <span class="annt">${strip.text}</span></div>` : null}
      ${forms[view] || forms.in}
      ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
      ${again ? html`<button type="button" class="atiny mid" disabled=${!!busy}
        onClick=${resend}>${busy === 'resend' ? 'Sending…'
          : 'Send a new confirmation link'}</button>` : null}
      ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
      ${st.guest && (view === 'in' || view === 'up')
        ? html`<a class="atiny mid" href=${ses.nextPath('/app/')}>Keep looking
            around without an account</a>`
        : null}
    </div>
    <p class="authfoot">One account holds every chat and every map. You can take
      all of it out as a file whenever you like, and delete the account with it.</p>
  </main>`;
}

/* ---------------- no accounts here ----------------
   Reached by anybody who types the URL on an instance running without Supabase.
   It is not an error: local mode is a real way to run this, and the honest
   answer is that there is nothing to sign in to. */
export function Local(){
  return html`<main class="authwrap">
    <div class="authcard">
      <div class="authtop">
        <${Mark} cls="amark" size=${34}/>
        <b>${NAME}</b>
        <small>No accounts on this instance</small>
      </div>
      <div class="aform">
        <p class="ap">This copy of Cognix runs without a database, so it has no
          accounts and nothing to sign in to. Your maps are kept in this browser
          instead, and the app works exactly as it does with one.</p>
        <a class="cbtn pri wide" href="/app/">Open Cognix</a>
      </div>
    </div>
  </main>`;
}






