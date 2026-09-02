/* =====================================================================
   boot.

   Three things can go wrong before the app exists, and each used to leave
   a blank page: a vendored file that did not load, a saved blob that
   throws on the way in, and a render that throws. So this module imports
   nothing statically — h.js touches window.React the moment it is
   evaluated — checks the globals first, and only then pulls the app in.

   It also decides which application this is. /api/config says whether this
   instance has accounts on it. If it does and somebody is signed in, their
   chats come from the database; if it does and nobody is, they get a short
   free trial with the work kept in this browser, and the app asks for an
   account when that runs out. Only an instance that allows no guests at all
   sends the browser to /app/auth/ before drawing anything.
   ===================================================================== */

const VENDOR = [['React', 'react.js'], ['ReactDOM', 'react-dom.js'], ['htm', 'htm.js']];

/* built with DOM calls, not htm: at this point htm may be the thing missing */
function bare(title, lines, list){
  const host = document.getElementById('root') || document.body;
  host.textContent = '';
  const card = document.createElement('div');
  card.className = 'crashcard';
  const h = document.createElement('h1');
  h.textContent = title;
  card.appendChild(h);
  lines.forEach(t => {
    const p = document.createElement('p');
    p.textContent = t;
    card.appendChild(p);
  });
  if(list && list.length){
    const ul = document.createElement('ul');
    ul.className = 'crashlist';
    list.forEach(t => {
      const li = document.createElement('li');
      li.textContent = t;
      ul.appendChild(li);
    });
    card.appendChild(ul);
  }
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'cbtn pri';
  b.textContent = 'Reload';
  b.addEventListener('click', () => location.reload());
  card.appendChild(b);
  const wrap = document.createElement('div');
  wrap.className = 'crash';
  wrap.setAttribute('role', 'alert');
  wrap.appendChild(card);
  host.appendChild(wrap);
}

const missing = VENDOR.filter(v => !window[v[0]]);
if(missing.length){
  bare('Cognix could not start', [
    'Its React files did not load, so there is nothing to draw. This almost always '
      + 'means the page was opened straight off the disk (file://) or the server is not '
      + 'serving the app folder.',
    'Start the local server and open http://localhost:8778/app/ — these files are '
      + 'vendored, so nothing is fetched from the internet.'
  ], missing.map(v => 'missing: app/vendor/' + v[1] + '  (window.' + v[0] + ')'));
} else {
  start();
}

async function start(){
  let html, store, App, ErrorBoundary;
  try {
    const mods = await Promise.all([
      import('./h.js'), import('./store.js'), import('./App.js'), import('./ErrorBoundary.js')
    ]);
    html = mods[0].html; store = mods[1]; App = mods[2].App; ErrorBoundary = mods[3].ErrorBoundary;
  } catch(e){
    try{ console.error('Cognix failed to load its own modules:', e); }catch(e2){}
    bare('Cognix could not start', [
      'One of its own files failed to load. If you edited the app, the browser console '
        + 'has the file and the line.',
      String((e && e.message) || e).slice(0, 300)
    ], null);
    return;
  }

  /* Which of two applications is this? One request answers it — a browser with
     its own storage, or an account with a database behind it — and asking also
     mints the CSRF cookie that every later write needs. */
  let cfg = null, ses = null;
  try{
    ses = await import('./session.js');
    cfg = await ses.boot();
  }catch(e){
    /* nothing arrived, or the server is broken: say so rather than quietly
       starting a second, local copy of somebody's account */
    if(!e || !e.status || e.status >= 500){
      bare('Cognix cannot reach its own server', [
        'The page loaded but /api/config did not answer, so it is not known yet '
          + 'whether this instance has accounts on it. Nothing has been opened.',
        String((e && e.message) || e).slice(0, 300)
      ], null);
      return;
    }
    /* a 404 means there is no API here at all — a static host with the app on
       it. That is the prototype, and it still works. */
    setTimeout(() => store.note('No server account — this browser is the storage'), 600);
  }

  /* Three ways to open, and the difference is only ever where the chats live.
     An account: the database. A guest on an instance that has accounts: this
     browser, with a small number of model calls. No accounts at all: this
     browser, with no ceiling. The only case that draws nothing is an instance
     that has accounts and allows no guests. */
  const guest = !!(cfg && cfg.cloud && !ses.signedIn());
  if(guest && ses.needsAuth()){
    const here = location.pathname + location.search + location.hash;
    location.replace('/app/auth/?next=' + encodeURIComponent(here));
    return;
  }

  if(cfg && cfg.cloud && !guest){
    try{
      const persist = await import('./persist.js');
      persist.attach();              // durability moves out of localStorage
      await persist.hydrate();       // titles now, each map when it is opened
    }catch(e){
      try{ console.error('Cognix could not open the account:', e); }catch(e2){}
      if(e && e.status === 401){
        location.replace('/app/auth/?next=' + encodeURIComponent(location.pathname));
        return;
      }
      /* 503 here is the server saying this instance has accounts but no tables
         yet. That is not this person's account failing to open — it is the
         deployment being half finished, and the message already says which
         files to run, so the card should not talk over it. */
      const setup = e && e.status === 503;
      bare(setup ? 'Cognix is not finished setting up'
                 : 'Cognix could not open your account', [
        setup ? 'Sign-in works, so the accounts half is connected. The database '
                  + 'half is not there yet, and nothing has been opened.'
              : 'Your chats did not load. Nothing is being shown rather than something '
                  + 'that looks like an empty account.',
        String((e && e.message) || e).slice(0, 400)
      ], null);
      return;
    }
  } else {
    if(guest){
      store.beGuest(cfg.guest);
      /* the same two sentences an administrator can put on the auth page. They
         are in /api/config, so a guest can be told the instance is in
         maintenance instead of waiting 40 seconds to be refused. */
      store.S.settings = { maintenance: !!cfg.maintenance,
                           announcement: cfg.announcement || '' };
    }
    /* a blob this browser cannot read must not stop the app from opening — load()
       already parks what it cannot parse, this is the belt for anything else */
    try { store.load(); }
    catch(e){
      try{ console.error('Cognix could not restore saved chats:', e); }catch(e2){}
      setTimeout(() => store.note('Saved chats could not be restored — starting a new one'), 500);
    }
  }
  try { if(store.S.sheet && store.S.sheet.map) store.reflow(); } catch(e){}

  const el = document.getElementById('root');
  if(!el){
    bare('Cognix could not start', ['This page is missing its #root element.'], null);
    return;
  }
  window.ReactDOM.createRoot(el).render(html`<${ErrorBoundary}><${App}/><//>`);

  /* async failures never reach a React boundary. One toast, rate-limited, so a
     loop in a timer cannot bury the UI under its own error messages. */
  let last = 0;
  const surface = what => {
    const now = Date.now();
    if(now - last < 4000) return;
    last = now;
    try{ store.note(what); }catch(e){}
  };
  window.addEventListener('error', e => {
    if(e && e.target && e.target !== window && e.target.tagName) return;  // a failed <img>
    surface('Something went wrong — the console has the details');
  });
  window.addEventListener('unhandledrejection', () => {
    surface('A background request failed — the console has the details');
  });
}
