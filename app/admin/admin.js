/* =====================================================================
   boot for the administrator console.

   The same shape as the app's main.js and the sign-in page's auth.js, and for
   the same reason: h.js reads window.React the moment it is evaluated, so the
   vendored files are checked with plain DOM calls before any module that needs
   React is imported.

   This page is behind three separate checks and this file is none of them.
   serve.py refuses to serve the path to anybody whose profile row does not say
   admin; server/admin.py refuses every call; the policies in Postgres refuse
   the rows underneath. What happens here is only the fourth thing: drawing
   something honest for the two cases where the console cannot be the answer —
   an instance with no database, and an account that is not an administrator.
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
  bare('The console could not start', [
    'Its React files did not load, so this page cannot draw itself.',
    'Start the local server and open http://localhost:8778/app/admin/ — these '
      + 'files are vendored, so nothing is fetched from the internet.'
  ], missing.map(v => 'missing: app/vendor/' + v[1] + '  (window.' + v[0] + ')'));
} else {
  start();
}

async function start(){
  let html, ses, mod;
  try{
    const mods = await Promise.all([
      import('../src/h.js'), import('../src/session.js'), import('./Console.js')
    ]);
    html = mods[0].html;
    ses = mods[1];
    mod = mods[2];
  }catch(e){
    try{ console.error('The Cognix console could not load:', e); }catch(e2){}
    bare('The console could not start', [
      'One of its own files failed to load. The browser console has the file '
        + 'and the line.',
      String((e && e.message) || e).slice(0, 300)
    ], null);
    return;
  }

  /* /api/config first, exactly as everywhere else: it says whether this
     instance has accounts at all, and asking is what mints the CSRF cookie
     every write from this page has to echo back. */
  let cfg = null;
  try{
    cfg = await ses.boot();
  }catch(e){
    bare('The console cannot reach its own server', [
      'The page loaded but /api/config did not answer, so nothing here can be '
        + 'trusted to be current. Nothing has been changed.',
      String((e && e.message) || e).slice(0, 300)
    ], null);
    return;
  }

  const el = document.getElementById('root');
  if(!el){
    bare('The console could not start',
         ['This page is missing its #root element.'], null);
    return;
  }
  const root = window.ReactDOM.createRoot(el);

  if(!cfg || !cfg.cloud) return root.render(html`<${mod.Local}/>`);
  /* the cookie is gone or ran out while the tab sat open: send them to sign in
     and come back here, rather than draw a console of failed requests */
  if(!ses.signedIn()){
    location.replace('/app/auth/?next=' + encodeURIComponent('/app/admin/'));
    return;
  }
  if(!ses.isAdmin()) return root.render(html`<${mod.NotYours}/>`);
  root.render(html`<${mod.Console}/>`);
}
