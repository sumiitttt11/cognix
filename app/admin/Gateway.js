/* =====================================================================
   the gateway — where the model calls go, with which key, and asking
   for which model.

   This is the one screen in the console that writes a secret, so it is the
   one screen that never reads one back. What the server returns is a masked
   hint — "set · 51 chars · …2M8" — and everything else here is a boolean or a
   word. The key box is always empty on arrival: there is nothing to prefill
   it with, and a box that looked prefilled would be a box that lied.

   It is also the one screen in this product that prints a vendor model id.
   Everywhere else an agent is only ever "Cognix mind v1", and that holds:
   this page is behind three gates and a role read from the caller's own
   profiles row, and an operator who cannot see which id an agent asks for
   cannot change it. The ids are not written down in this file either — they
   come from the server with the rest of the row, which is what keeps a
   default in one place.

   Two states are worth naming rather than leaving to be discovered:

     * `sealable` false means COGNIX_SESSION_SECRET is not set. A key stored
       under a secret this process invented at startup cannot be read after a
       restart, so the server refuses to store one and this page says why
       before anybody types it in.
     * `unreadable` true means one is stored and *this* process cannot open it.
       It looks like "the key is set" everywhere else and behaves like "there
       is no key", which is the worst pair of appearances to leave unexplained.

   The environment keeps working. Empty fields here mean "use COGNIX_BASE /
   COGNIX_KEY", per field, which is why the second card prints both what is in
   force and where it came from. An empty model box means the same thing one
   step along: ask for the id this build ships with.

   Check's answer is shared between the two cards, and that is the point of
   lifting it here rather than keeping it in the form. A gateway is asked for
   its model list, it says which ids it serves, and those ids are offered
   under the agent that would use one — so "that agent is not in its list"
   ends in a click rather than in a search through somebody's dashboard.
   ===================================================================== */
import { html, useState, useEffect, useRef } from '../src/h.js';
import { Card, Err, Skel, Row, Stat, Tag } from './bits.js';
import { aget, aput, apost, useLoad, stamp, why } from './net.js';

/* `source` is a word from the server, never a value. */
const WHENCE = {
  panel: 'saved here, in the console',
  env: 'from the environment this was deployed with',
  mixed: 'part saved here, part from the environment',
};

export function Gateway(){
  const { data, err, busy, reload } = useLoad(() => aget('gateway'), []);
  /* Check's answer, and the mapping as typed but not yet saved. Both live up
     here because both cards need them: the form does the checking and the
     models card is where the answer is useful, and the check has to judge the
     mapping on screen rather than the one in the database. */
  const [said, setSaid] = useState(null);
  const [want, setWant] = useState({});
  if(err) return html`<${Err} retry=${reload}>${err}<//>`;
  if(busy && !data) return html`<${Card}><${Skel} rows=${6}/><//>`;
  const g = (data && data.gateway) || {};
  const env = (data && data.env) || {};
  return html`<div class="kviews">
    <${Form} g=${g} env=${env} onSaved=${reload}
      want=${want} said=${said} onSaid=${setSaid}/>
    <${Models} g=${g} onSaved=${reload} want=${want} onWant=${setWant}
      offered=${(said && said.ok && said.models) || []}/>
    <${InUse} g=${g}/>
  </div>`;
}

/* ---------------- what the next call will actually use ----------------
   Not a description of the row: a description of the resolver's answer. An
   administrator who has just saved a URL and is still being refused wants to
   see which key is in force, and the two are decided per field. */
function InUse({ g }){
  return html`<${Card} title="In force right now"
      sub="what the next model call would use"
      act=${html`<${Tag} kind=${g.source === 'env' ? '' : 'acc'}>${
        g.source || 'env'}<//>`}>
    <div class="kgrid">
      <${Stat} label="Gateway" value=${g.in_use || 'not set'}
        tone=${g.in_use ? '' : 'warn'} hint=${WHENCE[g.source] || ''}/>
      <${Stat} label="API key" value=${g.in_use_key ? 'set' : 'missing'}
        tone=${g.in_use_key ? '' : 'warn'}
        hint=${g.in_use_key
          ? (g.key_set ? 'the one saved here' : 'the environment’s')
          : 'nothing to call the gateway with'}/>
      <${Stat} label="COGNIX_BASE" value=${g.env_base || 'not set'}
        hint="the fallback, from the environment"/>
      <${Stat} label="COGNIX_KEY" value=${g.env_key ? 'set' : 'not set'}
        hint="used when nothing is saved here"/>
    </div>
    <p class="khint2">A change here takes effect within a minute — the settings
      row is cached that long, and saving clears it, so in practice the next
      call uses it. Nothing restarts and no deploy is needed.</p>
  <//>`;
}

/* ---------------- the form ----------------
   Three actions, and they are three because they mean three different things:
   Save writes what changed, Forget removes the stored key and goes back to the
   environment, and Check spends nothing — GET /v1/models with the key that
   would be used, answered as one sentence.

   The key box being empty is "leave the stored key alone", not "clear it". A
   text box cannot express both, so clearing is its own button.

   Check sends the mapping from the card below along with the URL and the key,
   so the sentence it gets back judges what is on screen. Checking a gateway
   and then being told about an agent you have just repointed would be a
   sentence about the past. */
function Form({ g, env, onSaved, want, said, onSaid }){
  const [url, setUrl] = useState(g.base || '');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const [arm, setArm] = useState(false);      // Forget, asked twice
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  /* follow the stored row: a save reloads it, and so does another
     administrator's change. The key box is emptied either way. */
  useEffect(() => {
    setUrl(g.base || '');
    setKey('');
    setArm(false);
  }, [g.updated_at, g.base, g.hint]);

  const diff = {};
  if(url.trim() !== (g.base || '')) diff.base = url.trim();
  if(key.trim()) diff.key = key.trim();
  const n = Object.keys(diff).length;

  const send = async (body, note) => {
    if(busy) return;
    setBusy(true); setErr(''); setOk(''); onSaid(null); setArm(false);
    try{
      await aput('gateway', body);
      if(!live.current) return;
      setKey('');
      setOk(note);
      if(onSaved) onSaved();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  const check = async () => {
    if(busy) return;
    setBusy(true); setErr(''); setOk(''); onSaid(null);
    try{
      const body = { base: url.trim(), key: key.trim() };
      if(want && Object.keys(want).length) body.models = want;
      const out = await apost('gateway/check', body);
      if(live.current) onSaid(out || {});
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  const save = () => { if(n) send(diff, 'Saved. The next model call uses it.'); };
  const forget = () => send({ key: '' }, 'The stored key is gone — calls use '
    + 'the environment’s key now.');
  /* Both say the same thing; the server's word and the environment's word are
     read together so a page cannot claim a key is storable when it is not. */
  const sealable = g.sealable !== false && env.secret_given !== false;
  const keyHint = g.key_set
    ? 'One is stored: ' + (g.hint || 'set')
      + '. Type a new one to replace it; leaving this empty keeps it.'
    : (sealable
      ? 'Sealed before it is stored, so a copy of the database is not a working '
        + 'key. It is never shown again — what comes back is the masked hint.'
      : 'COGNIX_SESSION_SECRET is not set, so this cannot be stored yet.');

  return html`<${Card} title="Claude API gateway"
      sub=${g.updated_at ? 'last changed ' + stamp(g.updated_at)
                         : 'never changed from here'}>
    ${g.unreadable ? html`<p class="aerr" role="alert">A key is stored here and
      this server cannot read it: COGNIX_SESSION_SECRET is not the one that
      sealed it, so model calls are falling back to the environment. Paste the
      key in again to replace it, or forget the stored one.</p>` : null}
    ${!sealable ? html`<p class="kwarn">COGNIX_SESSION_SECRET is not set. This
      process invented one at startup, so a key stored now could not be read
      back after a restart — the server will refuse to store it. Set that
      variable, restart, and this box starts working. The URL saves either
      way.</p>` : null}

    <form class="kedit" onSubmit=${e => { e.preventDefault(); save(); }}>
      <${Row} id="g-url" label="Gateway URL" wide=${true}
          hint=${'The API origin, e.g. https://api.example.com — no query and no '
            + 'trailing path unless the gateway needs one. Empty means use '
            + (g.env_base ? 'COGNIX_BASE (' + g.env_base + ').'
                          : 'COGNIX_BASE, which is not set here.')}>
        <span class="fld"><input id="g-url" value=${url} inputMode="url"
          maxLength=${200} spellcheck="false" autocomplete="off"
          placeholder=${g.env_base || 'https://api.example.com'}
          onInput=${e => setUrl(e.target.value)}/></span>
      <//>
      <${Row} id="g-key" label="API key" wide=${true} hint=${keyHint}>
        <span class="fld"><input id="g-key" value=${key} type="password"
          maxLength=${300} spellcheck="false" autocomplete="new-password"
          placeholder=${g.key_set ? (g.hint || 'stored') : 'sk-…'}
          onInput=${e => setKey(e.target.value)}/></span>
      <//>
      <div class="kacts">
        <button type="submit" class="cbtn pri" disabled=${busy || !n}>
          ${busy ? 'Working…' : (n ? 'Save ' + n + (n === 1 ? ' change'
                                                            : ' changes')
                                   : 'Save')}</button>
        ${n ? html`<button type="button" class="cbtn" disabled=${busy}
          onClick=${() => { setUrl(g.base || ''); setKey('');
            setErr(''); setOk(''); onSaid(null); }}>Undo</button>` : null}
        <button type="button" class="cbtn" disabled=${busy} onClick=${check}>
          Check it</button>
        <span class="kdim">Check asks the gateway for its model list with the
          key that would be used. It costs nothing and saves nothing, and what
          it lists shows up under the agents below.</span>
      </div>
    </form>

    ${g.key_set ? html`<div>
      <div class="ksep"></div>
      <div class="kacts">
        <button type="button" class=${'cbtn' + (arm ? ' dang' : '')}
          disabled=${busy} onClick=${arm ? forget : () => setArm(true)}>
          ${arm ? 'Yes — forget it' : 'Forget the stored key'}</button>
        ${arm ? html`<button type="button" class="cbtn" disabled=${busy}
          onClick=${() => setArm(false)}>Keep it</button>` : null}
        <span class=${arm ? 'kwarn' : 'kdim'}>${arm
          ? (g.env_key
            ? 'The stored key is deleted and calls go back to COGNIX_KEY.'
            : 'The stored key is deleted, and COGNIX_KEY is not set — model '
              + 'calls would stop until a key is set again.')
          : 'Deletes it here and goes back to the environment’s key.'}</span>
      </div>
    </div>` : null}

    ${said ? html`<p class=${said.ok ? 'aok' : 'aerr'} role="status">${
      said.message || (said.ok ? 'It answered.' : 'It did not answer.')}</p>`
      : null}
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}

/* ---------------- which model each agent asks for ----------------
   The agents are the product; the ids behind them are a default. A gateway
   that spells the same model differently — or a move to a gateway that serves
   another one altogether — is this card rather than a code change.

   Empty means the id this build ships with, and that id is the placeholder, so
   the box always shows what the agent will ask for. It is the same rule as the
   URL and the key one card up, which is why there is no "reset" state to get
   stuck in: clearing the box *is* the reset, and typing the built-in id back in
   by hand clears the override too rather than pinning the agent to an id a
   later version has moved on from.

   The buttons under a field are what the gateway said it serves. They are here
   only after Check has asked one, because until something has asked, this page
   has no idea what any gateway's list looks like. */
const PICKS = 24;          // a gateway can serve fifty; a card cannot show them

/* 'cognix-mind-v1' -> 'Cognix mind v1'. Derived rather than listed, so an
   agent added to server/config.py needs nothing here. */
const shown = name => {
  const got = String(name || '').replace(/-/g, ' ');
  return got.charAt(0).toUpperCase() + got.slice(1);
};

function Models({ g, onSaved, want, onWant, offered }){
  const rows = Array.isArray(g.models) ? g.models : [];
  const ids = Array.isArray(offered) ? offered : [];
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  /* Follow the row: each box holds that agent's stored override, and is empty
     where the agent is on the built-in id. A save reloads, so this re-runs —
     and so another administrator's change lands here as well. */
  const sig = rows.map(m => m.name + '=' + (m.source === 'panel' ? m.id : ''))
    .join('|');
  useEffect(() => {
    const from = {};
    for(const m of rows) from[m.name] = m.source === 'panel' ? (m.id || '') : '';
    onWant(from);
  }, [sig]);

  /* The draft, agent by agent. Before that effect has run there is no draft
     yet, and the stored row is the answer — which is also what makes this safe
     against a server too old to send `models` at all. */
  const stored = m => (m.source === 'panel' ? (m.id || '') : '');
  const val = m => {
    const got = want ? want[m.name] : undefined;
    return got === undefined ? stored(m) : got;
  };
  const put = (name, v) => onWant(Object.assign({}, want || {}, { [name]: v }));

  const diff = {};
  for(const m of rows){
    const typed = val(m).trim();
    if(typed !== stored(m)) diff[m.name] = typed;
  }
  const names = Object.keys(diff);
  const n = names.length;

  const save = async () => {
    if(busy || !n) return;
    setBusy(true); setErr(''); setOk('');
    try{
      await aput('gateway', { models: diff });
      if(!live.current) return;
      setOk('Saved. The next call for ' + names.map(shown).join(' and ')
        + ' asks for that.');
      if(onSaved) onSaved();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  const undo = () => {
    const from = {};
    for(const m of rows) from[m.name] = stored(m);
    onWant(from);
    setErr(''); setOk('');
  };

  /* One row per agent. The hint is written for somebody who has just been told
     an agent is not in a gateway's list: what it asks for now, where that came
     from, and what happens if the box is emptied. */
  const hint = m => {
    const typed = val(m).trim();
    const now = typed || m.built_in || '';
    return (m.source === 'panel'
      ? 'Pointed here, in the console. '
      : 'On the id this build ships with. ')
      + (typed
        ? 'Asks for ' + now + (typed === m.built_in
          ? ' — which is the built-in one, so this saves as "use the built-in".'
          : '. Empty the box to go back to ' + (m.built_in || 'the built-in') + '.')
        : 'Empty, so it asks for ' + (m.built_in || 'the built-in id') + '.');
  };

  if(!rows.length) return html`<${Card} title="Which model each agent asks for"
      sub="not available from this server">
    <p class="khint2">This server did not say which models its agents ask for,
      which means it is older than this screen. Deploy the current build and
      this card fills in.</p>
  <//>`;

  return html`<${Card} title="Which model each agent asks for"
      sub=${g.models_stored
        ? 'at least one agent is pointed at a model set here'
        : 'both agents are on the ids this build ships with'}
      act=${html`<${Tag} kind=${g.models_stored ? 'acc' : ''}>${
        g.models_stored ? 'panel' : 'built-in'}<//>`}>
    <form class="kedit" onSubmit=${e => { e.preventDefault(); save(); }}>
      ${rows.map(m => html`<${Row} key=${m.name} id=${'g-m-' + m.name}
          label=${shown(m.name)} wide=${true} hint=${hint(m)}>
        <span class="fld"><input id=${'g-m-' + m.name} value=${val(m)}
          maxLength=${80} spellcheck="false" autocomplete="off"
          placeholder=${m.built_in || 'the built-in id'}
          onInput=${e => put(m.name, e.target.value)}/></span>
        ${(ids.length || val(m).trim()) ? html`<span class="kpick">
          ${ids.length ? html`<span>it serves:</span>` : null}
          ${ids.slice(0, PICKS).map(id => html`<button type="button" key=${id}
            class=${'cbtn' + (id === val(m).trim() ? ' on' : '')}
            disabled=${busy} title=${id}
            onClick=${() => put(m.name, id)}>${id}</button>`)}
          ${ids.length > PICKS
            ? html`<span>and ${ids.length - PICKS} more</span>` : null}
          ${val(m).trim() ? html`<button type="button" class="cbtn"
            disabled=${busy} onClick=${() => put(m.name, '')}>use the
            built-in</button>` : null}
        </span>` : null}
      <//>`)}
      <div class="kacts">
        <button type="submit" class="cbtn pri" disabled=${busy || !n}>
          ${busy ? 'Working…' : (n ? 'Save ' + n + (n === 1 ? ' agent'
                                                            : ' agents')
                                   : 'Save')}</button>
        ${n ? html`<button type="button" class="cbtn" disabled=${busy}
          onClick=${undo}>Undo</button>` : null}
        <span class="kdim">Nothing here is a secret and nothing here is
          cached longer than a minute. Check first if you are not sure the
          gateway serves it — a name it does not know fails every call for
          that agent.</span>
      </div>
    </form>
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}

