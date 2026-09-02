/* =====================================================================
   the audit log.

   Every row is one thing an administrator did, written as that administrator —
   the insert policy allows it only when actor = auth.uid(), which is what
   makes the actor columns worth reading at all. Nothing here can be edited or
   deleted from this console, and there is no endpoint that could.

   The action filter is a plain equality on one known verb rather than a search
   box, because these are a closed set and a free-text filter over them would
   mostly answer "no rows" to a typo.
   ===================================================================== */
import { html } from '../src/h.js';
import { Card, Table, Pager, Err } from './bits.js';
import { aget, useLoad, ago, stamp, short } from './net.js';
import { said, detail } from './Overview.js';

const PER = 50;
const KINDS = ['user.change', 'user.delete', 'user.invite', 'user.reset',
               'user.resend', 'user.confirm', 'settings.change',
               'admin.bootstrap'];

export function Audit({ q, go }){
  const page = Math.max(1, parseInt(q.page, 10) || 1);
  const action = KINDS.indexOf(q.action) >= 0 ? q.action : '';
  const { data, err, busy, reload } = useLoad(
    () => aget('audit', { page, per: PER, action }), [page, action]);

  const filters = html`<div class="kfilters">
    <span class="fld sel"><select value=${action} aria-label="Filter by action"
        onChange=${e => go('audit', { action: e.target.value, page: 1 })}>
      <option value="">Everything</option>
      ${KINDS.map(k => html`<option key=${k} value=${k}>${said(k)}</option>`)}
    </select></span>
    <button type="button" class="cbtn" disabled=${busy} onClick=${reload}>Refresh</button>
  </div>`;

  const cols = [
    { key: 'created_at', label: 'When', cell: r => html`<span
        title=${stamp(r.created_at)}>${ago(r.created_at)}</span>` },
    { key: 'actor_email', label: 'Administrator', cell: r => r.actor
        ? html`<button type="button" class="klink"
            onClick=${() => go('users/' + r.actor)}>
            ${r.actor_email || short(r.actor)}</button>`
        : (r.actor_email || '—') },
    { key: 'action', label: 'Did', cell: r => said(r.action) },
    { key: 'target_email', label: 'To', cell: r => r.target
        ? html`<button type="button" class="klink"
            onClick=${() => go('users/' + r.target)}>
            ${r.target_email || short(r.target)}</button>`
        : (r.target_email || '—') },
    { key: 'detail', label: 'Detail', cls: 'kcell',
      cell: r => detail(r.detail) || html`<span class="kdim">—</span>` },
  ];

  return html`<div class="kviews">
    <${Card} title="Audit log" wide=${true}
        sub=${data ? data.total + (data.total === 1 ? ' entry' : ' entries') : ''}
        act=${filters}>
      ${err ? html`<${Err} retry=${reload}>${err}<//>`
            : html`<${Table} cols=${cols} rows=${(data && data.rows) || []}
                busy=${busy && !data}
                empty=${action ? 'Nothing of that kind has been done yet.'
                               : 'Nothing has been done from this console yet.'}/>`}
      <${Pager} page=${page} per=${(data && data.per) || PER}
        total=${(data && data.total) || 0} busy=${busy}
        onPage=${n => go('audit', { action, page: n })}/>
      <p class="khint2">Written by the server, as the administrator who acted.
        It cannot be edited or cleared from here.</p>
    <//>
  </div>`;
}
