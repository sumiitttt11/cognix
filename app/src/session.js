/* =====================================================================
   the session client.

   One rule holds this file together: the browser never sees a Supabase
   key or a Supabase token. It has two cookies it did not choose — a
   signed HttpOnly session it cannot read, and a CSRF value it can — and
   it speaks only to this server's own /api/*. Nothing in this file knows
   that Supabase exists.

   Every call goes through req(), which does four things worth naming: it
   sends the CSRF header on anything that changes state, it gives up after
   a timeout because fetch has none of its own, it turns a JSON error body
   into an Error with .status on it, and it notices the moment a 401 means
   the session is over so the interface can stop pretending otherwise.
   ===================================================================== */

export const CSRF_COOKIE = 'cx_csrf';
export const CSRF_HEADER = 'x-cx-csrf';
const TIMEOUT = 25000;
const SAFE = { GET: 1, HEAD: 1 };
const RETRY = [0, 502, 503, 504];
const OFFLINE = 'This app cannot reach its own server. Check that it is '
  + 'running, then try again.';

/* what we have been told about this instance and this person. `config` comes
   from /api/config once; `user` is kept current by every reply that carries
   one, which is most of them. */
export const ME = { config: null, user: null, ready: false };

const subs = new Set();
export function onUser(fn){ subs.add(fn); return () => subs.delete(fn); }
function announce(){ subs.forEach(f => { try{ f(ME.user); }catch(e){} }); }

export function setUser(u){
  const was = ME.user && ME.user.id;
  ME.user = u && u.id ? u : null;
  if(was !== (ME.user && ME.user.id) || u) announce();
  return ME.user;
}
export const signedIn = () => !!ME.user;
export const isAdmin = () => !!(ME.user && ME.user.role === 'admin');
export const cloud = () => !!(ME.config && ME.config.cloud);
export const needsAuth = () => !!(ME.config && ME.config.auth_required);

/* ---------------- the two cookies ----------------
   The session cookie is HttpOnly, so this reads exactly one thing: the CSRF
   value the server minted, which is readable on purpose — echoing it back in
   a header is what proves the request came from this page and not from a form
   on somebody else's site. */
export function cookie(name){
  const all = String(document.cookie || '').split(';');
  for(let i = 0; i < all.length; i++){
    const bit = all[i].trim();
    if(bit.indexOf(name + '=') === 0)
      return decodeURIComponent(bit.slice(name.length + 1));
  }
  return '';
}

/* ---------------- errors ----------------
   .status is what callers branch on: 401 means sign in again, 402 means the
   monthly ceiling, 409 means somebody else got there first, 0 means the
   request never arrived. */
export function fail(msg, status, body){
  const e = new Error(msg || 'That did not work.');
  e.status = status || 0;
  e.field = (body && body.field) || '';
  e.body = body || null;
  return e;
}

/* ---------------- one attempt ---------------- */
async function once(method, path, body, ms){
  const head = {};
  let payload;
  if(body !== undefined && body !== null){
    head['content-type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if(!SAFE[method]){
    const tok = cookie(CSRF_COOKIE);
    if(tok) head[CSRF_HEADER] = tok;
  }
  const ac = new AbortController();
  const late = { yes: false };
  const t = setTimeout(() => { late.yes = true; ac.abort(); }, ms || TIMEOUT);
  let r, txt;
  try{
    r = await fetch(path, { method, headers: head, body: payload,
      credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      signal: ac.signal });
    txt = await r.text();
  }catch(e){
    throw fail(late.yes ? 'The server did not answer in time — it may still be '
                        + 'saving. Give it a moment.' : OFFLINE, 0);
  }finally{ clearTimeout(t); }

  let obj = null;
  if(txt){ try{ obj = JSON.parse(txt); }catch(e){ obj = null; } }
  if(!r.ok){
    const said = obj && (obj.error || obj.message);
    throw fail(String(said || 'The server answered ' + r.status + '.'),
               r.status, obj);
  }
  return obj === null ? {} : obj;
}

/* ---------------- the call the app makes ----------------
   Two recoveries live here and both are things that happen to real people
   rather than bugs: a CSRF cookie that expired or was cleared while the tab
   sat open, and a read that arrives while the server is restarting. */
export async function req(method, path, body, opts){
  const o = opts || {};
  const url = path.charAt(0) === '/' ? path : '/api/' + path;
  let tries = 0;
  for(;;){
    tries++;
    try{
      const out = await once(method, url, body, o.timeout);
      if(out && Object.prototype.hasOwnProperty.call(out, 'user') && !o.quiet)
        setUser(out.user);
      return out;
    }catch(e){
      if(e.status === 403 && tries === 1 && !SAFE[method]
         && /csrf/i.test(String(e.message))){
        /* one silent re-mint; a second would be a loop */
        try{ await once('GET', '/api/config', null, 8000); }catch(e2){}
        continue;
      }
      if(e.status === 401 && !o.keep) setUser(null);
      if(SAFE[method] && tries < 2 && RETRY.indexOf(e.status) >= 0){
        await new Promise(r => setTimeout(r, 400));
        continue;
      }
      throw e;
    }
  }
}

export const get = (p, o) => req('GET', p, null, o);
export const post = (p, b, o) => req('POST', p, b == null ? {} : b, o);
export const put = (p, b, o) => req('PUT', p, b == null ? {} : b, o);
export const del = (p, o) => req('DELETE', p, null, o);

/* ---------------- boot ----------------
   Asked before anything is drawn, because the answer decides which of two
   applications this is: one browser with its own storage, or an account with
   a database behind it. Calling it is also what mints the CSRF cookie, so it
   has to happen before the first write either way. */
export async function boot(){
  const cfg = await get('/api/config');
  ME.config = cfg;
  if(cfg && cfg.cloud){
    /* /api/auth/me never 401s — it answers {user: null} for a visitor — so a
       failure here is the network, not a missing session. */
    try{ await get('/api/auth/me'); }
    catch(e){ ME.user = null; }
  } else {
    ME.user = null;
  }
  ME.ready = true;
  announce();
  return cfg;
}

/* ---------------- accounts ---------------- */
export const signup = (email, password, name) =>
  post('/api/auth/signup', { email, password, name });
export const login = (email, password) =>
  post('/api/auth/login', { email, password });
export const recover = email => post('/api/auth/recover', { email });
export const resend = email => post('/api/auth/resend', { email });
export const reset = (access_token, password) =>
  post('/api/auth/reset', { access_token, password });
export const adopt = (access_token, refresh_token) =>
  post('/api/auth/adopt', { access_token, refresh_token });
export const changePassword = (current, next) =>
  post('/api/auth/password', { current, next });
export const profile = () => get('/api/profile');
export const rename = name => put('/api/profile', { name });
export const usage = () => get('/api/usage');

export async function logout(){
  try{ await post('/api/auth/logout'); }
  finally{ setUser(null); }
}

/* ---------------- the tokens in a mail link ----------------
   GoTrue answers a confirmation, reset or invite link by redirecting here
   with its tokens in the URL fragment. A fragment never reaches a server,
   which is the point, but it does sit in the address bar and in history —
   so the page reads it once, trades it for a cookie at /api/auth/adopt,
   and replaces the history entry so a back button cannot re-use it.

   A reset link arrives as `#reset&access_token=…`, because the redirect we
   asked for already had a fragment on it. Bare words like that one are not
   pairs; they come back in `marks`. */
export function fragment(){
  const raw = String(location.hash || '').replace(/^#\/?/, '');
  const out = { marks: [] };
  if(!raw) return out;
  raw.split('&').forEach(bit => {
    if(!bit) return;
    const i = bit.indexOf('=');
    if(i <= 0){ out.marks.push(decodeURIComponent(bit)); return; }
    const k = decodeURIComponent(bit.slice(0, i));
    if(k === 'marks') return;
    out[k] = decodeURIComponent(bit.slice(i + 1).replace(/\+/g, ' '));
  });
  return out;
}
export function clearFragment(){
  try{ history.replaceState(null, '', location.pathname + location.search); }
  catch(e){ location.hash = ''; }
}

/* Where to send somebody after a successful sign-in. Taken from ?next= so a
   link to the admin panel survives the detour, but only ever a path on this
   origin — an open redirect is how a sign-in page ends up in a phishing mail. */
export function nextPath(fallback){
  let raw = '';
  try{ raw = new URLSearchParams(location.search).get('next') || ''; }catch(e){}
  if(raw.charAt(0) !== '/' || raw.charAt(1) === '/' || raw.indexOf('\\') >= 0)
    return fallback || '/app/';
  return raw.slice(0, 300);
}
