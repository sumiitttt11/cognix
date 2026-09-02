/* =====================================================================
   accounts — the list, the filters, and the invitation.

   The list is the console's busiest page, so it is the one place that reads
   its state out of the URL: the search box, both filters and the page number
   all live in the route, which means a particular view of the table can be
   sent to another administrator as a link and comes back the same.

   Searching is debounced rather than sent per keystroke, because each one is a
   query against Postgres. Two hundred milliseconds is about one word.
   ===================================================================== */
import { html, useState, useEffect, useRef } from '../src/h.js';
import { Card, Table, Pager, Err, Tag } from './bits.js';
import { aget, apost, useLoad, num, ago, stamp, tidy, why } from './net.js';

const PER = 25;

export function Users({ q, go }){
  /* q is the parsed query out of the route: { q, role, status, page } */
  const [text, setText] = useState(q.q || '');
  const [term, setTerm] = useState(q.q || '');
  const first = useRef(true);

  /* the box follows the route when the route changes from outside — the back
     button, or a link from the overview */
  useEffect(() => { setText(q.q || ''); setTerm(q.q || ''); }, [q.q]);

  /* …and the route follows the box, once the typing stops */
  useEffect(() => {
    if(first.current){ first.current = false; return; }
    if(text === (q.q || '')) return;
    const t = setTimeout(() => setTerm(text), 220);
    return () => clearTimeout(t);
  }, [text]);
  useEffect(() => {
    if(term === (q.q || '')) return;
    go('users', { q: term, role: q.role, status: q.status, page: 1 });
  }, [term]);

  const page = Math.max(1, parseInt(q.page, 10) || 1);
  const { data, err, busy, reload } = useLoad(
    () => aget('users', { page, per: PER, q: q.q || '', role: q.role || '',
                          status: q.status || '' }),
    [page, q.q, q.role, q.status]);

  const set = (k, v) => go('users', { q: q.q, role: q.role, status: q.status,
                                      page: 1, [k]: v });
  const rows = (data && data.users) || [];

  const filters = html`<div class="kfilters">
    <span class="fld"><i>Search</i>
      <input value=${text} maxLength=${80} type="search"
        placeholder="email or name" aria-label="Search accounts"
        onInput=${e => setText(e.target.value)}/></span>
    <span class="fld sel"><select value=${q.role || ''} aria-label="Filter by role"
        onChange=${e => set('role', e.target.value)}>
      <option value="">Any role</option>
      <option value="admin">Administrators</option>
      <option value="user">Users</option>
    </select></span>
    <span class="fld sel"><select value=${q.status || ''} aria-label="Filter by status"
        onChange=${e => set('status', e.target.value)}>
      <option value="">Any status</option>
      <option value="active">Active</option>
      <option value="suspended">Suspended</option>
    </select></span>
    <button type="button" class="cbtn" disabled=${busy} onClick=${reload}>Refresh</button>
  </div>`;

  const cols = [
    { key: 'email', label: 'Account', cell: u => html`<span class="kmail">
        <b>${u.email}</b>
        ${u.display_name ? html`<small>${tidy(u.display_name, 60)}</small>` : null}
      </span>` },
    { key: 'role', label: 'Role', cls: 'kmid', cell: u => u.role === 'admin'
        ? html`<${Tag} kind="acc">admin<//>` : html`<span class="kdim">user</span>` },
    { key: 'status', label: 'Status', cls: 'kmid', cell: u => u.status === 'active'
        ? html`<span class="kdim">active</span>`
        : html`<${Tag} kind="warn">${u.status}<//>` },
    { key: 'chats', label: 'Chats', cls: 'kright',
      cell: u => typeof u.chats === 'number' ? num(u.chats) : '—' },
    { key: 'token_cap', label: 'Own cap', cls: 'kright',
      cell: u => u.token_cap == null ? html`<span class="kdim">default</span>`
        : (u.token_cap === 0 ? html`<${Tag} kind="warn">blocked<//>` : num(u.token_cap)) },
    { key: 'last_seen_at', label: 'Last seen', cls: 'kright',
      cell: u => html`<span title=${stamp(u.last_seen_at)}>${ago(u.last_seen_at)}</span>` },
    { key: 'created_at', label: 'Signed up', cls: 'kright',
      cell: u => html`<span title=${stamp(u.created_at)}>${ago(u.created_at)}</span>` },
  ];

  return html`<div class="kviews">
    <${Invite} onDone=${reload}/>
    <${Card} title="Accounts"
        sub=${data ? data.total + (data.total === 1 ? ' account' : ' accounts') : ''}
        act=${filters} wide=${true}>
      ${err ? html`<${Err} retry=${reload}>${err}<//>`
            : html`<${Table} cols=${cols} rows=${rows} busy=${busy && !data}
                onRow=${u => go('users/' + u.id)}
                empty=${(q.q || q.role || q.status)
                  ? 'No account matches that.' : 'No accounts yet.'}/>`}
      <${Pager} page=${page} per=${(data && data.per) || PER}
        total=${(data && data.total) || 0} busy=${busy}
        onPage=${n => go('users', { q: q.q, role: q.role, status: q.status, page: n })}/>
    <//>
  </div>`;
}

/* ---------------- an invitation ----------------
   The way somebody joins an instance with signups closed. GoTrue sends the
   mail; the address lands on /app/auth/ with an invite token in the fragment
   and picks a password there. Kept collapsed, because it is not what this page
   is for most days. */
function Invite({ onDone }){
  const [open, setOpen] = useState(false);
  const [addr, setAddr] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [ok, setOk] = useState('');
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);

  const send = async () => {
    if(busy) return;
    setBusy(true); setErr(''); setOk('');
    try{
      const out = await apost('invite', { email: addr.trim().toLowerCase() });
      if(!live.current) return;
      setOk(tidy((out && out.message) || 'The invitation is on its way.', 160));
      setAddr('');
      if(onDone) onDone();
    }catch(e){
      if(live.current) setErr(why(e));
    }finally{
      if(live.current) setBusy(false);
    }
  };

  if(!open)
    return html`<p class="kaside">
      <button type="button" class="cbtn" onClick=${() => setOpen(true)}>
        Invite somebody</button>
      <span class="kdim">Sends a mail link. Works even with signups closed.</span>
    </p>`;

  return html`<${Card} title="Invite somebody">
    <form class="kform" onSubmit=${e => { e.preventDefault(); send(); }}>
      <span class="fld"><i>Email</i>
        <input value=${addr} type="email" maxLength=${254} autoComplete="off"
          inputMode="email" autoCapitalize="off" spellCheck=${false}
          placeholder="them@company.com" aria-label="Email to invite"
          onInput=${e => setAddr(e.target.value)}/></span>
      <button type="submit" class="cbtn pri" disabled=${busy || !addr.trim()}>
        ${busy ? 'Sending…' : 'Send the invitation'}</button>
      <button type="button" class="cbtn" disabled=${busy}
        onClick=${() => { setOpen(false); setErr(''); setOk(''); }}>Close</button>
    </form>
    <p class="khint2">They set their own password from the link. Nothing is
      created here until they follow it.</p>
    ${err ? html`<p class="aerr" role="alert">${err}</p>` : null}
    ${ok ? html`<p class="aok" role="status">${ok}</p>` : null}
  <//>`;
}
