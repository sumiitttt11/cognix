/* =====================================================================
   boot for the sign-in page.

   Same shape as the app's own main.js and for the same reason: h.js reads
   window.React the moment it is evaluated, so the vendored files are checked
   with plain DOM calls before any module that needs React is imported.

   One request decides what this page even is. /api/config says whether this
   instance has accounts on it — and asking is also what mints the CSRF cookie
   that every sign-in POST has to echo back, so it has to happen first either
   way. Somebody who is already signed in never sees the form.
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
    'Its React files did not load, so this page cannot draw its own form.',
    'Start the local server and open http://localhost:8778/app/ — these files '
      + 'are vendored, so nothing is fetched from the internet.'
  ], missing.map(v => 'missing: app/vendor/' + v[1] + '  (window.' + v[0] + ')'));
} else {
  start();
}

async function start(){
  let html, ses, Page, Local, ROUTE;
  try{
    const mods = await Promise.all([
      import('../src/h.js'), import('../src/session.js'), import('./Page.js')
    ]);
    html = mods[0].html;
    ses = mods[1];
    Page = mods[2].Page;
    Local = mods[2].Local;
    ROUTE = mods[2].ROUTE;
  }catch(e){
    try{ console.error('Cognix could not load the sign-in page:', e); }catch(e2){}
    bare('Cognix could not start', [
      'One of its own files failed to load. The browser console has the file '
        + 'and the line.',
      String((e && e.message) || e).slice(0, 300)
    ], null);
    return;
  }

  let cfg = null;
  try{
    cfg = await ses.boot();
  }catch(e){
    bare('Cognix cannot reach its own server', [
      'The page loaded but /api/config did not answer, so it is not known yet '
        + 'whether this instance has accounts on it. Nothing has been sent.',
      String((e && e.message) || e).slice(0, 300)
    ], null);
    return;
  }

  const el = document.getElementById('root');
  if(!el){
    bare('Cognix could not start', ['This page is missing its #root element.'], null);
    return;
  }

  if(!cfg || !cfg.cloud){
    window.ReactDOM.createRoot(el).render(html`<${Local}/>`);
    return;
  }
  /* already signed in, and not here to spend a mail link: there is nothing to
     ask, so go where the ?next= said — the app, or the admin console */
  if(ses.signedIn() && ROUTE.view === 'in' && !ROUTE.err){
    location.replace(ses.nextPath('/app/'));
    return;
  }
  /* A mail link followed in a tab already sitting on this page changes only the
     fragment, which is a same-document navigation: nothing reloads and the
     route was read at import. Reload so the link is actually spent. The empty
     hash clearFragment() may leave behind is not matched, so this cannot loop. */
  window.addEventListener('hashchange', () => {
    if(/access_token=|error=|error_description=/.test(location.hash || ''))
      location.reload();
  });
  window.ReactDOM.createRoot(el).render(html`<${Page} cfg=${cfg}/>`);
}
