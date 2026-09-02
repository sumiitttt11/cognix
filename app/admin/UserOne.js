/* =====================================================================
   one account.

   Everything the console knows about a person, in one request: the profile
   row, this month's usage, the twenty-five most recent chats by title, and
   what GoTrue says about the login itself — when it was made, when it last
   signed in, whether the address was ever confirmed.

   Three refusals live in the server and are mirrored here as disabled
   controls with a reason: you cannot demote yourself, you cannot suspend
   yourself, and you cannot remove the last administrator. Mirroring them is
   not the check — server/admin.py is, and Postgres is behind that. It is so
   the button that cannot work does not look like one that can.
   ===================================================================== */
import { html, useState, useEffect, useRef } from '../src/h.js';
import { Card, Table, Err, Skel, Tag, Row, Stat } from './bits.js';
import { aget, apatch, apost, adel, useLoad, num, exact, ago, stamp, tidy, why }
  from './net.js';
import * as ses from '../src/session.js';

const mine = id => !!(ses.ME.user && ses.ME.user.id === id);

export function UserOne({ id, go }){
  const { data, err, busy, reload } = useLoad(() => aget('users/' + id), [id]);
  const back = html`<button type="button" class="cbtn"
    onClick=${() => go('users')}>All accounts</button>`;

  if(err) return html`<div class="kviews">
    <p class="kaside">${back}</p>
    <${Err} retry=${reload}>${err}<//>
  </div>`;
  if(busy && !data) return html`<div class="kviews">
    <p class="kaside">${back}</p><${Card}><${Skel} rows=${7}/><//></div>`;

  const u = (data && data.user) || {};
  const use = (data && data.usage) || {};
  const login = data && data.login;
  const self = mine(u.id);

  const cap = use.unlimited ? 0 : (use.cap || 0);
  const pct = cap > 0 ? Math.min(100, Math.round((use.used / cap) * 100)) : 0;
  const low = cap > 0 && use.used >= cap * 0.85;

  return html`<div class="kviews">
    <p class="kaside">${back}${self
      ? html`<span class="kdim">This is your own account.</span>` : null}</p>

    <${Card} wide=${true}>
      <div class="kwho">
        <span class="kmail big">
          <b>${u.email || '—'}</b>
          <small>${tidy(u.display_name, 80) || 'no display name'}</small>
        </span>
        ${u.role === 'admin' ? html`<${Tag} kind="acc">administrator<//>` : null}
        ${u.status && u.status !== 'active'
          ? html`<${Tag} kind="warn">${u.status}<//>` : null}
        ${login && !login.confirmed
          ? html`<${Tag} kind="warn">unconfirmed address<//>` : null}
        <span class="ksp"></span>
        <span class="kdim mono" title="Account id">${u.id || ''}</span>
      </div>
    <//>

    <${Card} title="This month" sub=${use.month || ''}>
      <div class="kgrid">
        <${Stat} label="Tokens used" value=${num(use.used)}
          title=${exact(use.used)}
          hint=${use.unlimited ? 'no ceiling on this account'
            : (cap ? 'of ' + num(cap) : 'no ceiling set')}/>
        <${Stat} label="Model calls" value=${num(use.calls)}
          hint=${use.failed ? use.failed + ' failed' : 'none failed'}
          tone=${use.failed ? 'warn' : ''}/>
        <${Stat} label="Tokens all time" value=${num(use.used_all)}
          title=${exact(use.used_all)}/>
        <${Stat} label="Last call" value=${use.last_call ? ago(use.last_call) : '—'}
          title=${stamp(use.last_call)}/>
      </div>
      ${cap > 0 ? html`<div class="ubar" role="img"
        aria-label=${use.used + ' of ' + cap + ' tokens used this month'}>
        <i class=${low ? 'hot' : null} style=${{ width: pct + '%' }}></i></div>` : null}
      <div class="kgrid tight">
        <${Stat} label="Chats" value=${num(use.chats)}/>
        <${Stat} label="Messages" value=${num(use.messages)}/>
        <${Stat} label="Maps" value=${num(use.maps)}/>
      </div>
    <//>

    <${Card} title="The login itself" sub="from the authentication service">
      ${login ? html`<dl class="kfacts">
        <dt>Created</dt><dd title=${stamp(login.created_at)}>
          ${stamp(login.created_at)}</dd>
        <dt>Last signed in</dt><dd>${login.last_sign_in_at
          ? stamp(login.last_sign_in_at) + ' · ' + ago(login.last_sign_in_at)
          : 'never'}</dd>
        <dt>Address</dt><dd>${login.confirmed ? 'confirmed'
          : 'not confirmed — they cannot sign in with a password yet'}</dd>
        <dt>Signs in with</dt><dd>${(login.providers || []).join(', ') || 'email'}</dd>
        <dt>Profile seen</dt><dd title=${stamp(u.last_seen_at)}>
          ${ago(u.last_seen_at)}</dd>
      </dl>` : html`<p class="kempty">The authentication service did not answer
        for this account, so only the profile row is shown. The rest of this
        page is unaffected.</p>`}
    <//>

    <${Edit} u=${u} self=${self} onSaved=${reload}/>
    <${Acts} u=${u} self=${self} login=${login} onDone=${reload} go=${go}/>

    <${Card} title="Chats" sub=${(data && data.chats || []).length
        + ' most recent' } wide=${true}>
      <${Table} rows=${(data && data.chats) || []} empty="No chats on this account."
        cols=${[
          { key: 'title', label: 'Title', cell: c => tidy(c.title, 80) || 'Untitled' },
          { key: 'tab', label: 'Tab', cls: 'kmid',
            cell: c => html`<span class="kdim">${c.tab || '—'}</span>` },
          { key: 'model', label: 'Model', cls: 'kmid',
            cell: c => html`<span class="kdim mono">${tidy(c.model, 34) || '—'}</span>` },
          { key: 'message_count', label: 'Messages', cls: 'kright',
            cell: c => num(c.message_count) },
          { key: 'updated_at', label: 'Changed', cls: 'kright',
            cell: c => html`<span title=${stamp(c.updated_at)}>${ago(c.updated_at)}</span>` },
        ]}/>
      <p class="khint2">Titles and sizes only. The messages and the maps
        themselves are not readable from this console — the database policies
        grant an administrator no access to either.</p>
    <//>
  </div>`;
}

/* ---------------- what can be changed ----------------
   One PATCH carrying only the fields that actually differ, so two
   administrators editing different things about the same person do not
   overwrite each other's work. The boxes follow the saved row: whenever a
   reload brings a new updated_at — this save, or somebody else's — they are
   set back to what is stored. That is an effect rather than a key on the
   element, because a remount would throw away the "Saved." line at the same
   moment it earned it. */
function Edit({ u, self, onSaved }){
  const [name, setName] = useState(u.display_name || '');
  const [role, setRole] = useState(u.role || 'user');
  const [status, setStatus] = useState(u.status || 'active');
  const [cap, setCap] = useState(u.token_cap == null ? '' : String(u.token_cap));
  const [notes, setNotes] = useState(u.notes || '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  useEffect(() => {
    setName(u.display_name || '');
    setRole(u.role || 'user');
    setStatus(u.status || 'active');
    setCap(u.token_cap == null ? '' : String(u.token_cap));
    setNotes(u.notes || '');
  }, [u.id, u.updated_at]);
  /* and a different account is a different page, so its "Saved." line does not
     come along. Kept apart from the resync above, which also runs on this
     account's own save — the moment the line is supposed to appear. */
  useEffect(() => { setErr(''); setOk(''); }, [u.id]);

  const capNow = u.token_cap == null ? '' : String(u.token_cap);
  const diff = {};
  if(name.trim() !== (u.display_name || '')) diff.name = name.trim();
  if(role !== (u.role || 'user')) diff.role = role;
  if(status !== (u.status || 'active')) diff.status = status;
  if(cap.trim() !== capNow)
    diff.token_cap = cap.trim() === '' ? null : parseInt(cap.trim(), 10);
  if(notes !== (u.notes || '')) diff.notes = notes;
  const n = Object.keys(diff).length;

  /* the two the server refuses for this account, refused here as well so the
     control says why instead of the reply saying it afterwards */
  const lockRole = self && (u.role === 'admin');
  const lockStop = self;

  const save = async () => {
    if(busy || !n) return;
    setBusy(true); setErr(''); setOk('');
    try{
      await apatch('users/' + u.id, diff);
      if(!live.current) return;
      setOk('Saved.');
      if(onSaved) onSaved();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  return html`<${Card} title="Change this account"
      sub=${n ? n + (n === 1 ? ' change to save' : ' changes to save') : 'nothing changed'}>
    <form class="kedit" onSubmit=${e => { e.preventDefault(); save(); }}>
      <${Row} id="u-name" label="Display name"
          hint="What they see in the app. They can change it themselves.">
        <span class="fld"><input id="u-name" value=${name} maxLength=${80}
          onInput=${e => setName(e.target.value)}/></span>
      <//>
      <${Row} id="u-role" label="Role"
          hint=${lockRole
            ? 'You cannot take your own administrator role away. Another administrator can.'
            : 'An administrator can open this console and see every account.'}>
        <span class="fld sel"><select id="u-role" value=${role} disabled=${lockRole}
            onChange=${e => setRole(e.target.value)}>
          <option value="user">User</option>
          <option value="admin">Administrator</option>
        </select></span>
      <//>
      <${Row} id="u-status" label="Status"
          hint=${lockStop
            ? 'You cannot suspend your own account.'
            : 'A suspended account keeps its data and cannot sign in or spend calls. It bites within seconds, not at the next sign-in.'}>
        <span class="fld sel"><select id="u-status" value=${status} disabled=${lockStop}
            onChange=${e => setStatus(e.target.value)}>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
        </select></span>
      <//>
      <${Row} id="u-cap" label="Monthly token ceiling"
          hint="Empty means this account follows the instance default. 0 stops it spending anything at all.">
        <span class="fld num"><input id="u-cap" value=${cap} inputMode="numeric"
          maxLength=${10} placeholder="default"
          onInput=${e => setCap(e.target.value.replace(/[^0-9]/g, ''))}/></span>
      <//>
      <${Row} id="u-notes" label="Notes" wide=${true}
          hint="For administrators only. Never shown to the account.">
        <span class="fld"><textarea id="u-notes" value=${notes} rows=${3}
          maxLength=${2000} onInput=${e => setNotes(e.target.value)}></textarea></span>
      <//>
      <div class="kacts">
        <button type="submit" class="cbtn pri" disabled=${busy || !n}>
          ${busy ? 'Saving…' : (n ? 'Save ' + n + (n === 1 ? ' change' : ' changes')
                                 : 'Save')}</button>
        ${n ? html`<button type="button" class="cbtn" disabled=${busy}
          onClick=${() => { setName(u.display_name || ''); setRole(u.role || 'user');
            setStatus(u.status || 'active'); setCap(capNow); setNotes(u.notes || '');
            setErr(''); setOk(''); }}>Undo</button>` : null}
      </div>
    </form>
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}

/* ---------------- the four things that are not edits ----------------
   Three of them send mail through the authentication service and are safe to
   press twice. The fourth is not: deleting an account takes its chats, its
   messages and its maps with it, and there is no copy anywhere else. So it
   asks for the address to be typed — not as a formality, but because the one
   mistake this page can make that cannot be undone is deleting the wrong row
   in a list of rows that look alike. */
function Acts({ u, self, login, onDone, go }){
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const [arm, setArm] = useState(false);
  const [typed, setTyped] = useState('');
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);
  /* an armed delete belongs to the account it was armed on. This card is the
     same card when the route moves to the next account, so without this the
     red box arrives on somebody else's page with the previous address still
     typed into it. The typed address would no longer match, so nothing could
     have been deleted — but a page that looks armed is not one to leave. */
  useEffect(() => {
    setArm(false); setTyped(''); setErr(''); setOk('');
  }, [u.id]);

  const go1 = async (what, fn) => {
    if(busy) return;
    setBusy(what); setErr(''); setOk('');
    try{
      const out = await fn();
      if(!live.current) return;
      setOk(tidy((out && out.message) || 'Done.', 200));
      if(what !== 'delete' && onDone) onDone();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy('');
    }
  };

  const mail = (what, label, working) => html`<button type="button" class="cbtn"
    disabled=${!!busy} onClick=${() => go1(what, () => apost('users/' + u.id + '/' + what))}>
    ${busy === what ? working : label}</button>`;

  const wipe = () => go1('delete', async () => {
    const out = await adel('users/' + u.id);
    if(live.current) go('users');
    return out;
  });

  return html`<${Card} title="Actions" sub="mail links, and the one that is final">
    <div class="kacts">
      ${mail('reset', 'Send a password reset link', 'Sending…')}
      ${login && !login.confirmed
        ? mail('resend', 'Resend the confirmation link', 'Sending…') : null}
      ${login && !login.confirmed
        ? mail('confirm', 'Mark the address confirmed', 'Confirming…') : null}
    </div>
    <p class="khint2">The reset link goes to ${u.email} and is good for one
      hour. Marking an address confirmed by hand is for an instance whose mail
      is not set up yet — it lets somebody in without their having clicked
      anything.</p>
    <div class="ksep"></div>
    ${self
      ? html`<p class="khint2">You cannot delete your own account from this
          console. Another administrator can, or you can do it from the app.</p>`
      : (!arm
        ? html`<button type="button" class="cbtn dang"
            onClick=${() => { setArm(true); setErr(''); setOk(''); }}>
            Delete this account</button>`
        : html`<div class="kdanger">
            <p><b>This cannot be undone.</b> Everything goes with the account:
              every chat, every message and every map. Type
              <span class="mono">${u.email}</span> to confirm.</p>
            <div class="kacts">
              <span class="fld"><input value=${typed} maxLength=${254}
                autoComplete="off" spellCheck=${false}
                aria-label="Type the email address to confirm deletion"
                onInput=${e => setTyped(e.target.value)}/></span>
              <button type="button" class="cbtn dang"
                disabled=${!!busy || typed.trim().toLowerCase() !== (u.email || '').toLowerCase()}
                onClick=${wipe}>${busy === 'delete' ? 'Deleting…' : 'Delete it'}</button>
              <button type="button" class="cbtn" disabled=${!!busy}
                onClick=${() => { setArm(false); setTyped(''); }}>Cancel</button>
            </div>
          </div>`)}
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}

