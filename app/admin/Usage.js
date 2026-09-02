/* =====================================================================
   usage — what the model calls cost, by day and by person.

   Two RPCs behind one request, and the reason they are RPCs is worth keeping
   in mind while reading this: adding up a month of usage rows in the browser
   would mean shipping a month of usage rows to the browser. What arrives here
   is already summed — one row per day, and one row per person.

   The chart is divs. A canvas would be sharper and would also be a second
   drawing model to maintain, and this is a bar chart with at most 365 bars.
   ===================================================================== */
import { html } from '../src/h.js';
import { Card, Table, Err, Skel, Stat } from './bits.js';
import { aget, useLoad, num, exact, ago, stamp, day, tidy } from './net.js';

const SPANS = [[7, '7 days'], [30, '30 days'], [90, '90 days'], [365, 'A year']];

/* One bar per day, scaled to the busiest day in the window. A day with calls
   but no recorded tokens still gets a sliver, because a bar of zero height in a
   row of bars reads as "no data" when it means "nothing was spent". */
function Chart({ rows }){
  const top = rows.reduce((m, r) => Math.max(m, r.tokens || 0), 0);
  if(!top)
    return html`<p class="kempty">No tokens were spent in this window.</p>`;
  return html`<div class="kchart" role="img"
      aria-label=${'Tokens per day. Highest: ' + num(top) + ' tokens.'}>
    ${rows.map(r => {
      const h = Math.max(2, Math.round(((r.tokens || 0) / top) * 100));
      const said = day(r.day) + ' · ' + exact(r.tokens) + ' tokens · '
        + (r.calls || 0) + (r.calls === 1 ? ' call' : ' calls')
        + (r.failed ? ' · ' + r.failed + ' failed' : '');
      return html`<span key=${r.day} class="kbarwrap" title=${said}>
        <i class=${'kbarv' + (r.failed ? ' bad' : '')}
          style=${{ height: h + '%' }}></i>
      </span>`;
    })}
  </div>`;
}

export function Usage({ q, go }){
  const days = SPANS.map(s => s[0]).indexOf(parseInt(q.days, 10)) >= 0
    ? parseInt(q.days, 10) : 30;
  const { data, err, busy, reload } = useLoad(() => aget('usage', { days }), [days]);

  const picker = html`<div class="kfilters">
    <div class="seg">${SPANS.map(s => html`<button key=${s[0]} type="button"
      class=${days === s[0] ? 'on' : null} aria-pressed=${days === s[0]}
      onClick=${() => go('usage', { days: s[0] })}>${s[1]}</button>`)}</div>
    <button type="button" class="cbtn" disabled=${busy} onClick=${reload}>Refresh</button>
  </div>`;

  if(err) return html`<div class="kviews"><p class="kaside">${picker}</p>
    <${Err} retry=${reload}>${err}<//></div>`;

  const daily = (data && data.daily) || [];
  const people = (data && data.users) || [];
  const sum = k => daily.reduce((n, r) => n + (r[k] || 0), 0);
  const busiest = daily.reduce((b, r) =>
    (!b || (r.tokens || 0) > (b.tokens || 0)) ? r : b, null);

  return html`<div class="kviews">
    <${Card} title=${'Tokens over the last ' + days + ' days'} act=${picker}
        wide=${true}>
      ${busy && !data ? html`<${Skel} rows=${6}/>` : html`<${'div'}>
        <div class="kgrid">
          <${Stat} label="Tokens" value=${num(sum('tokens'))}
            title=${exact(sum('tokens'))} hint=${'over ' + days + ' days'}/>
          <${Stat} label="Model calls" value=${num(sum('calls'))}/>
          <${Stat} label="Failed" value=${num(sum('failed'))}
            tone=${sum('failed') ? 'warn' : ''}
            hint=${sum('failed') ? 'refused, or timed out' : 'none'}/>
          <${Stat} label="Busiest day"
            value=${busiest && busiest.tokens ? num(busiest.tokens) : '—'}
            hint=${busiest && busiest.tokens ? day(busiest.day) : ''}/>
        </div>
        <${Chart} rows=${daily}/>
        <p class="khint2">${'One bar per day, tallest is '
          + (busiest && busiest.tokens ? num(busiest.tokens) + ' tokens'
                                       : 'nothing yet')
          + '. A red bar had at least one failed call. '
          + 'Hover a bar for the day.'}</p>
      <//>`}
    <//>

    <${Card} title="By account" sub=${people.length
        ? people.length + ' with usage in this window' : ''} wide=${true}>
      <${Table} rows=${people} busy=${busy && !data} keyOf=${r => r.user_id}
        onRow=${r => go('users/' + r.user_id)}
        empty="Nobody has spent a model call in this window."
        cols=${[
          { key: 'email', label: 'Account', cell: r => html`<span class="kmail">
              <b>${r.email}</b>${r.display_name
                ? html`<small>${tidy(r.display_name, 60)}</small>` : null}</span>` },
          { key: 'tokens', label: 'Tokens', cls: 'kright',
            cell: r => html`<span title=${exact(r.tokens)}>${num(r.tokens)}</span>` },
          { key: 'calls', label: 'Calls', cls: 'kright', cell: r => num(r.calls) },
          { key: 'last_call', label: 'Last call', cls: 'kright',
            cell: r => html`<span title=${stamp(r.last_call)}>${ago(r.last_call)}</span>` },
        ]}/>
      <p class="khint2">Fifty accounts at most, heaviest first. Open one to
        change its ceiling.</p>
    <//>
  </div>`;
}
