/* =====================================================================
   the Plan tab. Cognix writes it with Cognix apex v2 against a locked
   schema, from the map's own boxes — so a box edit and a regenerate
   produce a plan that still traces back to what is on the canvas.
   ===================================================================== */
import { html } from './h.js';
import { BRANCHES } from './tokens.js';
import { S, emit } from './store.js';
import { MODEL_LABEL, CFG } from './api.js';

export function PlanView({ onGenerate }){
  const m = S.sheet.map, u = S.ui, p = u.plan;
  /* the plan is sanitised on arrival, so these four are arrays — the guard is
     here so that a future caller cannot blank the tab by skipping that */
  const A = x => Array.isArray(x) ? x : [];

  if(!m) return html`<div class="view on"><div class="plan"><div class="inner">
    <h1>No plan yet</h1>
    <p class="sub">Describe an idea on the Mind Maps tab — the plan is written from the
      boxes in the map.</p></div></div></div>`;

  const locked = m.nodes.filter(n => n.locked);
  const edited = m.nodes.filter(n => Object.keys(n.style || {}).length);
  const leaves = k => m.nodes.filter(n => n.branch === k && n.kind === 'leaf');

  const head = html`<${'div'}>
    <h1>${m.title}</h1>
    <p class="sub">Written from map ${m.map_id} · v${m.version} · ${m.nodes.length} boxes${
      locked.length ? ' · ' + locked.length + ' locked' : ''}${
      p ? ' · ' + MODEL_LABEL[CFG.planModel] : ''}</p>
  <//>`;

  /* ---- the model's plan ---- */
  if(p) return html`<div class="view on"><div class="plan"><div class="inner">
    ${head}
    <div class="note"><b>Where this came from.</b> Cognix was given
      the ${m.nodes.filter(n => n.kind === 'leaf').length} points on this map
      and nothing else, and had to answer inside a fixed
      schema — ${MODEL_LABEL[CFG.planModel]} wrote it.
      Edit a box and press <b>Plan</b> again to rewrite it.
      ${locked.length ? ' ' + locked.length + ' locked box(es) were quoted word for word.' : ''}</div>
    <p>${p.summary}</p>
    ${A(p.sections).map((s, i) => html`<${'div'} key=${i}>
      <h3>${s.h}</h3><ul>${A(s.b).map((b, j) => html`<li key=${j}>${b}</li>`)}</ul><//>`)}
    <h3>Timeline</h3>
    ${A(p.weeks).map((w, i) => html`<div class="wk" key=${i}><b>${w.w}</b><span>${w.t}</span></div>`)}
    <h3>Risks</h3>
    <ul>${A(p.risks).map((r, i) => html`<li key=${i}><b>${r.r}</b> — ${r.m}</li>`)}</ul>
    <h3>Do next</h3>
    <ul>${A(p.next).map((x, i) => html`<li key=${i}>${x}</li>`)}</ul>
    <h3>Hand-back to Cognix</h3>
    <ul>
      <li>Map JSON — ${m.nodes.length} boxes, ${m.nodes.length - 1} links</li>
      <li>This plan page, rewritten on demand from the current boxes</li>
      <li>Style sheet — theme <b>${S.sheet.preset}</b>${
        edited.length ? ', ' + edited.length + ' box(es) with their own look' : ''}</li>
      <li>Only the map's text is sent to the gateway — no files, no attachments</li>
    </ul>
    ${u.planErr ? html`<div class="note" role="alert"><b>The last rewrite failed.</b>
      ${' ' + u.planErr}</div>` : null}
    <p class="sub" style=${{ marginTop:'18px' }}>
      <button type="button" class="preset" onClick=${onGenerate} disabled=${!!u.planBusy}>
        ${u.planBusy ? 'Writing…' : 'Rewrite this plan'}</button></p>
  </div></div></div>`;

  /* ---- nothing generated yet: the map's own structure, plus the button ---- */
  return html`<div class="view on"><div class="plan"><div class="inner">
    ${head}
    <div class="note"><b>This is the map, not a plan yet.</b> Press
      <b>Write the plan</b> and Cognix turns these boxes into sequencing,
      numbers and risk — inside a locked schema, so it always comes back in this shape.
      ${u.planErr ? ' Last attempt failed: ' + u.planErr : ''}</div>
    <p class="sub"><button type="button" class="preset" onClick=${onGenerate} disabled=${!!u.planBusy}>
      ${u.planBusy ? 'Writing…' : 'Write the plan'}</button></p>
    ${BRANCHES.map(b => html`<${'div'} key=${b.key}>
      <h3>${b.label}</h3>
      <ul>${leaves(b.key).map(n => html`<li key=${n.id}>${n.text}${
        n.locked ? html` <b>(locked)</b>` : null}</li>`)}</ul><//>`)}
  </div></div></div>`;
}
