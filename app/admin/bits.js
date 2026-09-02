/* =====================================================================
   the parts every view of the console is made of.

   Six of them, and they exist because a console is mostly the same four
   shapes repeated: a figure in a box, a table with a pager under it, a field
   with a label, and something to look at while a request is in flight. Each
   one is deliberately small — the interesting code is in the views.

   Nothing here holds state that outlives a render. `Table` takes columns and
   rows; deciding what a row means is the view's job.
   ===================================================================== */
import { html } from '../src/h.js';
import { exact } from './net.js';

/* ---------------- a figure ----------------
   `hint` is the second line: last month's number, or what the figure counts.
   `tone` tints it — one red number in a row of grey is how "3 failed today"
   gets noticed without a paragraph about it. */
export const Stat = ({ label, value, hint, tone, title }) =>
  html`<div class=${'kstat' + (tone ? ' ' + tone : '')}>
    <span class="klab">${label}</span>
    <b class="kval" title=${title || exact(value) || null}>${value}</b>
    ${hint ? html`<small class="khint">${hint}</small>` : null}
  </div>`;

/* ---------------- a box with a heading ---------------- */
export const Card = ({ title, sub, act, wide, children }) =>
  html`<section class=${'kcard' + (wide ? ' wide' : '')}>
    ${title ? html`<header class="kchead">
      <span class="kct"><b>${title}</b>${sub ? html`<small>${sub}</small>` : null}</span>
      ${act || null}
    </header>` : null}
    <div class="kcbody">${children}</div>
  </section>`;

/* ---------------- waiting, and the two ways a table can be empty ----------
   A spinner that appears for 80ms is a flicker, so the first load of a view
   shows bars shaped like the rows that are coming instead. */
export const Skel = ({ rows }) => html`<div class="kskel" aria-hidden="true">
  ${Array.from({ length: rows || 5 }).map((_, i) =>
    html`<i key=${i} class="kbar"></i>`)}
</div>`;

export const Empty = ({ children }) =>
  html`<p class="kempty">${children}</p>`;

export const Err = ({ children, retry }) => html`<div class="kerr" role="alert">
  <span>${children}</span>
  ${retry ? html`<button type="button" class="cbtn" onClick=${retry}>Try again</button>`
          : null}
</div>`;

/* ---------------- a table ----------------
   cols: [{ key, label, cls, cell(row) }]. `cell` gets the row and returns
   whatever should be in the cell; without one the value at `key` is printed.
   `onRow` makes the whole row clickable, and when it is present the row is a
   real button for the keyboard — a div with a click handler is a row nobody
   can reach with Tab. */
export function Table({ cols, rows, onRow, keyOf, empty, busy }){
  if(busy) return html`<${Skel} rows=${6}/>`;
  if(!rows || !rows.length)
    return html`<${Empty}>${empty || 'Nothing here yet.'}<//>`;
  const key = keyOf || (r => r.id);
  return html`<div class="ktwrap"><table class="ktable">
    <thead><tr>
      ${cols.map(c => html`<th key=${c.key} class=${c.cls || null}
        scope="col">${c.label}</th>`)}
    </tr></thead>
    <tbody>
      ${rows.map((r, i) => html`<tr key=${key(r, i)}
          class=${onRow ? 'kclick' : null}
          tabIndex=${onRow ? 0 : null}
          role=${onRow ? 'button' : null}
          onClick=${onRow ? () => onRow(r) : null}
          onKeyDown=${onRow ? (e => {
            if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); onRow(r); }
          }) : null}>
        ${cols.map(c => html`<td key=${c.key} class=${c.cls || null}>
          ${c.cell ? c.cell(r) : (r[c.key] == null ? '—' : String(r[c.key]))}
        </td>`)}
      </tr>`)}
    </tbody>
  </table></div>`;
}

/* ---------------- the pager ----------------
   Says which rows these are out of how many, because "page 3" on its own does
   not tell an administrator whether it is worth clicking again. */
export function Pager({ page, per, total, onPage, busy }){
  const last = Math.max(1, Math.ceil((total || 0) / (per || 25)));
  if(last <= 1 && (total || 0) <= (per || 25))
    return total ? html`<p class="kpage"><span>${
      total + (total === 1 ? ' row' : ' rows')}</span></p>` : null;
  const from = ((page - 1) * per) + 1;
  const to = Math.min(total || 0, page * per);
  return html`<p class="kpage">
    <span>${from}–${to} of ${total}</span>
    <button type="button" class="cbtn" disabled=${busy || page <= 1}
      onClick=${() => onPage(page - 1)}>Back</button>
    <button type="button" class="cbtn" disabled=${busy || page >= last}
      onClick=${() => onPage(page + 1)}>Next</button>
  </p>`;
}

/* ---------------- a labelled control ----------------
   The label is a real <label for>, so clicking the word focuses the box. */
export const Row = ({ id, label, hint, wide, children }) =>
  html`<div class=${'krow' + (wide ? ' wide' : '')}>
    <label class="klab2" for=${id}>${label}</label>
    <span class="kctl">${children}</span>
    ${hint ? html`<small class="khint2">${hint}</small>` : null}
  </div>`;

/* A switch that is a <button aria-pressed>, not a checkbox: the app's .chk is
   drawn with CSS on a button and this is the same control in the same sheet. */
export const Toggle = ({ id, on, label, onChange, busy }) =>
  html`<button type="button" id=${id} class=${'chk' + (on ? ' on' : '')}
    role="switch" aria-checked=${!!on} aria-label=${label}
    disabled=${!!busy} onClick=${() => onChange(!on)}></button>`;

/* A chip. Roles and statuses are the two things read most in the user table,
   so they are shapes rather than words to be parsed. */
export const Tag = ({ kind, children }) =>
  html`<span class=${'ktag' + (kind ? ' ' + kind : '')}>${children}</span>`;
