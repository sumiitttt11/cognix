/* =====================================================================
   settings — the four switches that change the instance from here.

   Two things share the name "signups" and it matters which is which. There is
   an environment flag, set where the process is deployed and readable only by
   whoever can redeploy it, and there is a row in app_settings that this page
   writes. Signups are open when *both* say so — server/api.py takes the AND —
   so the toggle below cannot open signups on an instance whose environment has
   closed them, and the panel says as much rather than lying about the state.

   Every save writes an audit row with exactly the fields that changed.
   ===================================================================== */
import { html, useState, useEffect, useRef } from '../src/h.js';
import { Card, Err, Skel, Row, Toggle, Stat, Tag } from './bits.js';
import { aget, aput, useLoad, num, exact, stamp, why } from './net.js';

/* The two the gateway will actually accept — serve.py refuses anything else
   before it spends the key, so an unknown name here is a dead switch.

   Both columns are the agent's name, because the agent's name is the whole of
   what this app has: the vendor model behind either one is not in this file, not
   in the row this page writes, and not in anything the server hands back. A row
   written before the agents were named holds a model id instead, and the API
   turns it into the name below on the way out, so an old instance ticks the same
   boxes as a new one. */
const KNOWN = [
  ['cognix-apex-v2', 'Cognix apex v2', 'plans and chat'],
  ['cognix-mind-v1', 'Cognix mind v1', 'mind map generation'],
];

export function Settings(){
  const { data, err, busy, reload } = useLoad(() => aget('settings'), []);
  if(err) return html`<${Err} retry=${reload}>${err}<//>`;
  if(busy && !data) return html`<${Card}><${Skel} rows=${7}/><//>`;
  const s = (data && data.settings) || {};
  const env = (data && data.env) || {};
  return html`<div class="kviews">
    <${Form} s=${s} env=${env} onSaved=${reload}/>
    <${Card} title="From the environment" sub="set where this is deployed">
      <div class="kgrid">
        <${Stat} label="Mode" value=${env.mode || '—'}
          hint=${env.mode === 'cloud' ? 'accounts and a database'
                                      : 'this browser only'}/>
        <${Stat} label="Signups" value=${env.signups_env ? 'allowed' : 'closed'}
          tone=${env.signups_env ? '' : 'warn'}
          hint=${env.signups_env ? 'the switch above decides'
                                 : 'the switch above cannot open them'}/>
        <${Stat} label="Named administrators" value=${num(env.admin_emails)}
          hint="promoted on first sign-in"/>
        <${Stat} label="Default ceiling" value=${num(env.token_cap_default)}
          title=${exact(env.token_cap_default)} hint="tokens per account per month"/>
      </div>
      <p class="khint2">These four come from the process environment, not from
        the database, and changing them means a redeploy. The ceiling here is
        the fallback the row below starts from.</p>
    <//>
  </div>`;
}

function Form({ s, env, onSaved }){
  const [open, setOpen] = useState(s.signups_open !== false);
  const [work, setWork] = useState(!!s.maintenance);
  const [say, setSay] = useState(s.announcement || '');
  const [cap, setCap] = useState(String(s.default_token_cap == null
    ? '' : s.default_token_cap));
  const [models, setModels] = useState(() =>
    Array.isArray(s.allowed_models) ? s.allowed_models.slice() : []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  /* the controls follow the stored row: a save reloads it, and so does another
     administrator's change. An effect rather than a key on the element, because
     a remount would take the "Saved." line with it. */
  useEffect(() => {
    setOpen(s.signups_open !== false);
    setWork(!!s.maintenance);
    setSay(s.announcement || '');
    setCap(String(s.default_token_cap == null ? '' : s.default_token_cap));
    setModels(Array.isArray(s.allowed_models) ? s.allowed_models.slice() : []);
  }, [s.updated_at]);

  const capNow = String(s.default_token_cap == null ? '' : s.default_token_cap);
  const sameList = (a, b) => a.length === b.length
    && a.every((x, i) => x === b[i]);
  const diff = {};
  if(open !== (s.signups_open !== false)) diff.signups_open = open;
  if(work !== !!s.maintenance) diff.maintenance = work;
  if(say !== (s.announcement || '')) diff.announcement = say;
  if(cap.trim() !== capNow && cap.trim() !== '')
    diff.default_token_cap = parseInt(cap.trim(), 10);
  if(!sameList(models, Array.isArray(s.allowed_models) ? s.allowed_models : []))
    diff.allowed_models = models;
  const n = Object.keys(diff).length;

  const flip = name => setModels(list => list.indexOf(name) >= 0
    ? list.filter(m => m !== name) : list.concat([name]));

  const save = async () => {
    if(busy || !n) return;
    setBusy(true); setErr(''); setOk('');
    try{
      await aput('settings', diff);
      if(!live.current) return;
      setOk('Saved. Everybody sees it within a minute.');
      if(onSaved) onSaved();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  return html`<${Card} title="This instance"
      sub=${s.updated_at ? 'last changed ' + stamp(s.updated_at) : 'never changed'}>
    <form class="kedit" onSubmit=${e => { e.preventDefault(); save(); }}>
      <${Row} id="s-signups" label="New signups"
          hint=${env.signups_env
            ? 'Off closes the create-account form. Invitations still work.'
            : 'The environment has signups closed, so this switch has no effect until that changes. Invitations still work.'}>
        <span class="kctlrow">
          <${Toggle} id="s-signups" on=${open} busy=${busy}
            label="Allow new signups" onChange=${setOpen}/>
          <span class="kdim">${open
            ? (env.signups_env ? 'anyone can create an account'
                               : 'on here, closed in the environment')
            : 'closed'}</span>
        </span>
      <//>
      <${Row} id="s-work" label="Maintenance"
          hint="Signing in and reading keep working. Model calls are refused with a sentence that says why, and the app says so before anybody types a prompt.">
        <span class="kctlrow">
          <${Toggle} id="s-work" on=${work} busy=${busy}
            label="Maintenance mode" onChange=${setWork}/>
          <span class=${work ? 'kwarn' : 'kdim'}>${work
            ? 'no new maps or plans can be generated' : 'everything is running'}</span>
        </span>
      <//>
      <${Row} id="s-say" label="Announcement" wide=${true}
          hint=${'Shown on the sign-in page and above the app. Empty means no strip. '
            + (600 - say.length) + ' characters left.'}>
        <span class="fld"><textarea id="s-say" value=${say} rows=${2}
          maxLength=${600} placeholder="Nothing is being announced."
          onInput=${e => setSay(e.target.value)}></textarea></span>
      <//>
      <${Row} id="s-cap" label="Default monthly ceiling"
          hint="Tokens per account per month, for every account that has no ceiling of its own. 0 stops all new calls.">
        <span class="fld num"><input id="s-cap" value=${cap} inputMode="numeric"
          maxLength=${10} placeholder=${String(env.token_cap_default || '')}
          onInput=${e => setCap(e.target.value.replace(/[^0-9]/g, ''))}/></span>
      <//>
      <${Row} id="s-models" label="Models the app may use" wide=${true}
          hint="Unticking one hides it from the app. The gateway refuses anything not on this list twice over — here, and in the proxy that holds the key.">
        <span class="kchecks">
          ${KNOWN.map(m => html`<label key=${m[0]} class="kcheck">
            <${Toggle} id=${'s-m-' + m[0]} on=${models.indexOf(m[0]) >= 0}
              busy=${busy} label=${m[1]} onChange=${() => flip(m[0])}/>
            <span><b>${m[1]}</b><small>${m[2]}</small></span>
          </label>`)}
          ${models.filter(m => !KNOWN.some(k => k[0] === m)).map(m =>
            html`<span key=${m} class="kcheck">
              <${Tag} kind="warn">unknown<//>
              <span><b class="mono">${m}</b><small>the proxy will refuse this
                one — untick it</small></span>
              <button type="button" class="cbtn" disabled=${busy}
                onClick=${() => flip(m)}>Remove</button>
            </span>`)}
        </span>
      <//>
      ${!models.length ? html`<p class="aerr">With no model ticked the app can
        generate nothing at all. That is the same as maintenance mode, but
        without the explanation.</p>` : null}
      <div class="kacts">
        <button type="submit" class="cbtn pri" disabled=${busy || !n}>
          ${busy ? 'Saving…' : (n ? 'Save ' + n + (n === 1 ? ' change' : ' changes')
                                  : 'Save')}</button>
        ${n ? html`<button type="button" class="cbtn" disabled=${busy}
          onClick=${() => { setOpen(s.signups_open !== false);
            setWork(!!s.maintenance); setSay(s.announcement || ''); setCap(capNow);
            setModels(Array.isArray(s.allowed_models) ? s.allowed_models.slice() : []);
            setErr(''); setOk(''); }}>Undo</button>` : null}
      </div>
    </form>
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}
