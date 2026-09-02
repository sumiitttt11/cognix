/* =====================================================================
   overview — the page an administrator opens first.

   Everything here comes from one request: server/admin.py builds it from the
   admin_overview RPC plus the ten newest audit rows and the eight newest
   accounts, so this view makes exactly one call and never fans out.

   The figures are grouped the way the questions are asked: who is here, what
   they have made, what it cost this month, and whether anything is wrong right
   now. The last group is the only one that is ever red.
   ===================================================================== */
import { html } from '../src/h.js';
import { Stat, Card, Table, Err, Skel, Tag } from './bits.js';
import { aget, useLoad, num, exact, ago, stamp, tidy, short } from './net.js';

/* An audit action, as a sentence. The verbs are known — server/admin.py writes
   them — and anything unrecognised falls through as its own name rather than
   being hidden, because an action nobody can read is still an action. */
const SAID = {
  'admin.bootstrap': 'became the first administrator',
  'user.change': 'changed an account',
  'user.delete': 'deleted an account',
  'user.invite': 'invited',
  'user.reset': 'sent a reset link to',
  'user.resend': 'resent a confirmation to',
  'user.confirm': 'confirmed the address of',
  'settings.change': 'changed the settings',
};
export const said = a => SAID[a] || a;

/* The detail column of an audit row: {role: 'admin'} reads as `role → admin`,
   which is what the row is actually about. */
export function detail(d){
  if(!d || typeof d !== 'object') return '';
  const keys = Object.keys(d);
  if(!keys.length) return '';
  return keys.slice(0, 4).map(k => {
    let v = d[k];
    if(v === true) v = 'on';
    else if(v === false) v = 'off';
    else if(v === null) v = 'none';
    else if(Array.isArray(v)) v = v.length + ' items';
    else if(typeof v === 'object') v = '…';
    return k + ' → ' + tidy(v, 40);
  }).join(', ');
}

export function Overview({ go }){
  const { data, err, busy, reload } = useLoad(() => aget('overview'), []);
  if(err) return html`<${Err} retry=${reload}>${err}<//>`;
  if(busy && !data) return html`<div class="kgrid"><${Skel} rows=${8}/></div>`;
  const d = data || {};
  const rows = d.recent || [];

  const people = [
    { label: 'Accounts', value: num(d.users), hint: d.admins
        + (d.admins === 1 ? ' administrator' : ' administrators') },
    { label: 'New this week', value: num(d.new_7d), hint: 'signed up in 7 days' },
    { label: 'Active this week', value: num(d.seen_7d), hint: 'signed in in 7 days' },
    { label: 'Suspended', value: num(d.suspended), tone: d.suspended ? 'warn' : '',
      hint: d.suspended ? 'cannot sign in' : 'none' },
  ];
  const made = [
    { label: 'Chats', value: num(d.chats), hint: 'across every account' },
    { label: 'Messages', value: num(d.messages), hint: 'user and assistant' },
    { label: 'Maps', value: num(d.maps), hint: 'saved mind maps' },
  ];
  const spent = [
    { label: 'Tokens this month', value: num(d.tokens_month),
      hint: d.month || '', title: exact(d.tokens_month) },
    { label: 'Tokens today', value: num(d.tokens_today), title: exact(d.tokens_today) },
    { label: 'Calls this month', value: num(d.calls_month), hint: 'model calls' },
    { label: 'Median call', value: d.ms_median ? num(d.ms_median) + ' ms' : '—',
      hint: 'this month' },
  ];
  const now = [
    { label: 'Calls today', value: num(d.calls_today) },
    { label: 'Failed today', value: num(d.failed_today),
      tone: d.failed_today ? 'bad' : '', hint: d.failed_today
        ? 'gateway refusals and timeouts' : 'nothing has failed' },
  ];

  const band = (title, list) => html`<${Card} title=${title}>
    <div class="kgrid">${list.map(s => html`<${Stat} key=${s.label} ...${s}/>`)}</div>
  <//>`;

  return html`<div class="kviews">
    ${band('People', people)}
    ${band('This month', spent)}
    ${band('Right now', now)}
    ${band('What has been made', made)}

    <${Card} title="Newest accounts" sub=${(d.newest || []).length + ' shown'}
        act=${html`<button type="button" class="cbtn"
          onClick=${() => go('users')}>All accounts</button>`}>
      <${Table} rows=${d.newest || []} onRow=${u => go('users/' + u.id)}
        empty="No accounts yet."
        cols=${[
          { key: 'email', label: 'Email', cell: u => html`<span class="kmail">
              <b>${u.email}</b>${u.display_name
                ? html`<small>${tidy(u.display_name, 60)}</small>` : null}</span>` },
          { key: 'role', label: 'Role', cls: 'kmid', cell: u => u.role === 'admin'
              ? html`<${Tag} kind="acc">admin<//>` : html`<span class="kdim">user</span>` },
          { key: 'status', label: 'Status', cls: 'kmid',
            cell: u => u.status === 'active'
              ? html`<span class="kdim">active</span>`
              : html`<${Tag} kind="warn">${u.status}<//>` },
          { key: 'created_at', label: 'Signed up', cls: 'kright',
            cell: u => html`<span title=${stamp(u.created_at)}>${ago(u.created_at)}</span>` },
        ]}/>
    <//>

    <${Card} title="Latest administrator actions"
        act=${html`<button type="button" class="cbtn"
          onClick=${() => go('audit')}>Full log</button>`}>
      <${Table} rows=${rows} keyOf=${r => r.id}
        empty="Nothing has been done from this console yet."
        cols=${[
          { key: 'created_at', label: 'When',
            cell: r => html`<span title=${stamp(r.created_at)}>${ago(r.created_at)}</span>` },
          { key: 'actor_email', label: 'Who', cell: r => r.actor_email
              || short(r.actor) || '—' },
          { key: 'action', label: 'Did', cell: r => said(r.action) },
          { key: 'target_email', label: 'To', cell: r => r.target_email
              || (r.target ? short(r.target) : '—') },
          { key: 'detail', label: 'Detail', cls: 'kcell',
            cell: r => detail(r.detail) || html`<span class="kdim">—</span>` },
        ]}/>
    <//>
  </div>`;
}
