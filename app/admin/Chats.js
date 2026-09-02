/* =====================================================================
   chats, across every account.

   Metadata only, and that is a boundary rather than an omission: the policies
   in supabase/policies.sql grant an administrator a select on `chats` and
   nothing at all on `messages` or `maps`. So this page can tell you that
   somebody has forty maps and when they last touched one — which is what
   support actually needs — and cannot show you what is in them.

   The endpoint returns the owner as an id, not an address, because the query
   is one select against one table. Opening the row goes to the account, which
   is where the address is.
   ===================================================================== */
import { html } from '../src/h.js';
import { Card, Table, Pager, Err } from './bits.js';
import { aget, useLoad, num, ago, stamp, tidy, short } from './net.js';

const PER = 50;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function Chats({ q, go }){
  const page = Math.max(1, parseInt(q.page, 10) || 1);
  const who = UUID.test(String(q.user || '')) ? q.user : '';
  const { data, err, busy, reload } = useLoad(
    () => aget('chats', { page, per: PER, user: who }), [page, who]);
  /* only when the list is filtered, and only to put a name on the heading */
  const owner = useLoad(() => who ? aget('users/' + who) : Promise.resolve(null),
                        [who]);
  const named = owner.data && owner.data.user;

  const cols = [
    { key: 'title', label: 'Title', cell: c => html`<span class="kmail">
        <b>${tidy(c.title, 90) || 'Untitled'}</b>
        <small class="mono">${short(c.id)}</small></span>` },
    { key: 'user_id', label: 'Account', cell: c => html`<button type="button"
        class="klink mono" title=${c.user_id}
        onClick=${() => go('users/' + c.user_id)}>${short(c.user_id)}</button>` },
    { key: 'tab', label: 'Tab', cls: 'kmid',
      cell: c => html`<span class="kdim">${c.tab || '—'}</span>` },
    { key: 'model', label: 'Model', cls: 'kmid',
      cell: c => html`<span class="kdim mono">${tidy(c.model, 34) || '—'}</span>` },
    { key: 'message_count', label: 'Messages', cls: 'kright',
      cell: c => num(c.message_count) },
    { key: 'updated_at', label: 'Changed', cls: 'kright',
      cell: c => html`<span title=${stamp(c.updated_at)}>${ago(c.updated_at)}</span>` },
    { key: 'created_at', label: 'Made', cls: 'kright',
      cell: c => html`<span title=${stamp(c.created_at)}>${ago(c.created_at)}</span>` },
  ];

  const act = html`<div class="kfilters">
    ${who ? html`<button type="button" class="cbtn"
      onClick=${() => go('chats')}>Show every account</button>` : null}
    <button type="button" class="cbtn" disabled=${busy} onClick=${reload}>Refresh</button>
  </div>`;

  return html`<div class="kviews">
    <${Card} wide=${true} act=${act}
        title=${who ? 'Chats on one account' : 'Chats'}
        sub=${who
          ? ((named && named.email) || short(who))
          : (data ? data.total + (data.total === 1 ? ' chat' : ' chats') : '')}>
      ${err ? html`<${Err} retry=${reload}>${err}<//>`
            : html`<${Table} cols=${cols} rows=${(data && data.chats) || []}
                busy=${busy && !data}
                empty=${who ? 'No chats on that account.'
                            : 'Nobody has made a chat yet.'}/>`}
      <${Pager} page=${page} per=${(data && data.per) || PER}
        total=${(data && data.total) || 0} busy=${busy}
        onPage=${n => go('chats', { user: who, page: n })}/>
      <p class="khint2">Titles, sizes and dates. The messages and the maps are
        not readable from this console, by policy rather than by omission — an
        administrator's token is refused on both tables.</p>
    <//>
  </div>`;
}
