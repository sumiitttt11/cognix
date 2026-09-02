/* =====================================================================
   the window: chrome, shell, and every keyboard shortcut.

   One subscriber at the top re-renders the whole tree on emit(). That is
   cheap here (a few hundred nodes) and keeps drag, scrub and undo honest —
   there is exactly one source of truth and no stale copies.
   ===================================================================== */
import { html, useEffect, Fragment } from './h.js';
import { useStore } from './useStore.js';
import { S, emit, save, note, undo, redo, newChat, select, reflow,
         chatsLeft } from './store.js';
import { Tray } from './Tray.js';
import { Canvas } from './Canvas.js';
import { PlanView } from './PlanView.js';
import { Inspector } from './Inspector.js';
import { Chat, handle } from './Chat.js';
import { Thinking } from './Thinking.js';
import { Account, Offer, Wall, GuestWall } from './Account.js';
import { Mark, Brand } from './brand.js';
import { addChild, delSel, dupSel, nudge } from './edits.js';
import { genPlan, MODEL_LABEL, CFG } from './api.js';
import * as V from './sanitize.js';

/* ---------------- the plan call ---------------- */
export async function writePlan(){
  const u = S.ui, map = S.sheet.map;
  if(!map || u.planBusy || u.busy) return;
  u.planBusy = true; u.planErr = null; u.tab = 'plan'; emit();
  const t0 = Date.now();
  const ac = new AbortController(); u.planAbort = ac;
  try {
    const res = await genPlan(map, ac.signal);
    /* PlanView walks four arrays by index; the schema was forced but the
       answer is still someone else's JSON */
    u.plan = V.plan(res.data);
    u.planErr = null;
    note('Cognix wrote the plan in ' + ((Date.now() - t0) / 1000).toFixed(1) + 's'
      + ' · ' + MODEL_LABEL[CFG.planModel]);
  } catch(e){
    u.planErr = String((e && e.message) || e);
    note('The gateway could not write the plan');
  } finally {
    u.planBusy = false; u.planAbort = null; save(); emit();
  }
}

/* ---------------- toast ---------------- */
function Toast(){
  const t = S.ui.note;
  return html`<div class=${'toast' + (t ? ' on' : '')}>${t || ''}</div>`;
}

/* ---------------- the strip under the titlebar ----------------
   Three things worth one line at the top of the window. Two are an
   administrator talking to everybody at once: that the instance is in
   maintenance (the server is already refusing model calls, so the app should
   not let somebody find that out by waiting 40 seconds for it), and one line of
   announcement. The third is a free trial counting down, which is here rather
   than in a modal because a number somebody can watch is the opposite of a
   surprise — by the time the wall goes up they already knew.

   Dismissible, and only drawn when there is something to draw — hence the class
   on .app rather than an always-present empty row. */
export function notice(){
  const st = S.settings;
  if(S.ui.shut.ann) return null;
  if(st && st.maintenance) return { kind: 'work', text: 'Cognix is in maintenance — '
    + 'your chats are safe, but new maps cannot be generated right now.' };
  if(st && st.announcement) return { kind: 'say', text: st.announcement };
  /* a guest, and only while there is something left to say: at zero the wall is
     what they are looking at, and a strip repeating it is noise. */
  if(S.guest){
    const left = chatsLeft();
    if(left > 0 && left !== Infinity)
      return { kind: 'say', text: 'You are looking around without an account. '
        + (left === 1 ? 'One more free chat' : left + ' more free chats')
        + ' on this browser — an account is free and keeps every one of them.' };
  }
  return null;
}
function Notice({ n }){
  return html`<div class=${'ann ' + n.kind} role="status">
    <span class="anni" aria-hidden="true">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5"/>
        <circle cx="12" cy="16.4" r="1" fill="currentColor" stroke="none"/></svg></span>
    <span class="annt">${n.text}</span>
    <button type="button" class="gact" title="Dismiss" aria-label="Dismiss this notice"
      onClick=${() => { S.ui.shut.ann = 1; emit(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
  </div>`;
}

/* ---------------- window chrome ---------------- */
const T = (id, title, d, on) => html`<button key=${id} type="button" class="tbtn"
  title=${title} aria-label=${title} onClick=${on}>
  <svg viewBox="0 0 24 24" aria-hidden="true">${d.split('|').map((p, i) => html`<path key=${i} d=${p}/>`)}</svg></button>`;

/* the three dots at top right are the desktop-window frame from the design.
   In a browser tab there is nothing for them to do, and a button that looks
   live but is inert is worse than one that says so. */
const W = (cls, d, what) => html`<button type="button" class=${'wbtn' + cls}
  title=${what} aria-label=${what} onClick=${() => note(what)}>
  <svg viewBox="0 0 12 12" aria-hidden="true"><path d=${d}/></svg></button>`;

function Titlebar(){
  const u = S.ui;
  return html`<header class="titlebar">
    <${Brand}/>
    ${T('m', 'Menu', 'M4 7h16M4 12h16M4 17h10',
      () => note('This prototype has no app menu'))}
    <button type="button" class="tbtn" title="Toggle left tray" aria-label="Toggle left tray"
      aria-pressed=${!u.nosid}
      onClick=${() => { u.nosid = !u.nosid; emit(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/>
        <path d="M9 4v16"/></svg></button>
    <button type="button" class="tbtn" title="Search chats" aria-label="Search chats"
      onClick=${() => { u.nosid = false; emit();
        const el = document.querySelector('.search input'); if(el) el.focus(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg></button>
    <span class="tbgap"></span>
    ${T('b', 'Undo — Ctrl Z', 'M15 5l-7 7 7 7', undo)}
    ${T('f', 'Redo — Ctrl Y', 'M9 5l7 7-7 7', redo)}
    <span class="tbspace"></span>
    ${W('', 'M2 6h8', 'Minimise is up to your browser here')}
    ${W('', 'M2.5 2.5h7v7h-7z', 'Use your browser to resize this window')}
    ${W(' x', 'M3 3l6 6M9 3l-6 6', 'Close the tab to leave Cognix')}
  </header>`;
}

/* ---------------- document header ---------------- */
function DocHead(){
  const u = S.ui, map = S.sheet.map;
  const title = map ? map.title : 'New mind map';
  return html`<div class="dhead">
    <${Mark} cls="dmark"/>
    <span class="dt">${title}</span>
    <span class="dchip">${u.tab === 'plan' ? 'Plan' : 'Mind Maps'}</span>
    ${map ? html`<span class="dchip">v${map.version}</span>` : null}
    ${map && map.source !== 'local' ? html`<span class="dchip">${MODEL_LABEL[map.source] || 'gateway'}</span>`
      : map ? html`<span class="dchip">offline</span>` : null}
    <span class="dsp"></span>
    <button type="button" class="dbtn" title="Map JSON" aria-label="Show the map JSON"
      onClick=${() => { u.insp = true; u.itab = 'inspect'; emit(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l-4 7 4 7M16 5l4 7-4 7"/></svg></button>
    <button type="button" class="dbtn" title="Customize panel — Ctrl K"
      aria-label="Customize panel" aria-pressed=${!!u.insp}
      onClick=${() => { u.insp = !u.insp; emit(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/>
        <path d="M15 4v16"/></svg></button>
    <button type="button" class="dbtn" title="Send this map back to Cognix"
      aria-label="Send this map back to Cognix"
      onClick=${() => note(map ? 'Handed ' + map.nodes.length + ' boxes back to Cognix'
                               : 'Nothing to hand back yet')}>
      <span class="live"></span>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6"/></svg></button>
    <button type="button" class="dbtn" title="More" aria-label="More options"
      onClick=${() => note('Nothing else in this menu yet')}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/>
        <circle cx="12" cy="19" r="1.4"/></svg></button>
  </div>`;
}

/* ---------------- the app ---------------- */
export function App(){
  useStore();
  const u = S.ui;

  /* keyboard. Ctrl N and Ctrl K are app-level and work from inside a field;
     undo/redo are not — with the caret in a text box the browser's own undo
     stack is the one the user means, so the typing guard sits above them. */
  useEffect(() => {
    const onKey = e => {
      const t = e.target || {}, tag = (t.tagName || '').toLowerCase();
      const typing = tag === 'input' || tag === 'textarea' || !!t.isContentEditable;
      const mod = e.ctrlKey || e.metaKey;
      const key = typeof e.key === 'string' ? e.key : '';
      const k = key.toLowerCase();
      if(mod && k === 'n'){ e.preventDefault(); newChat(); note('New chat'); return; }
      if(mod && k === 'k'){ e.preventDefault(); u.insp = !u.insp; emit(); return; }
      if(typing) return;
      if(mod && k === 'z'){ e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if(mod && k === 'y'){ e.preventDefault(); redo(); return; }
      if(mod && k === 'd'){ e.preventDefault(); dupSel(); return; }
      if(!S.sheet.map) return;
      if(key === 'Delete' || key === 'Backspace'){ e.preventDefault(); delSel(); return; }
      if(key === 'Tab'){ e.preventDefault(); addChild(); return; }
      if(key === 'Escape'){ select(null); return; }
      if(key.indexOf('Arrow') === 0){
        e.preventDefault();
        const big = e.shiftKey;
        if(key === 'ArrowLeft')  nudge(-1, 0, big);
        if(key === 'ArrowRight') nudge(1, 0, big);
        if(key === 'ArrowUp')    nudge(0, -1, big);
        if(key === 'ArrowDown')  nudge(0, 1, big);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* the sheet size depends on the style tokens, so re-flow once on mount */
  useEffect(() => { if(S.sheet.map){ reflow(); emit(); } }, []);

  const shell = 'shell' + (u.nosid ? ' nosid' : '') + (u.insp ? ' showinsp' : '');
  const n = notice();

  return html`<${Fragment}>
    <div class=${'app' + (n ? ' hasann' : '')}>
      <${Titlebar}/>
      ${n ? html`<${Notice} n=${n}/>` : null}
      <div class=${shell}>
        <${Tray}/>
        ${u.nosid ? null
          : html`<button type="button" class="scrim" aria-label="Close the chat list"
              onClick=${() => { u.nosid = true; emit(); }}></button>`}
        <section class="doc">
          <${DocHead}/>
          <div class="stage">
            ${u.tab === 'map'
              ? html`<${Canvas} onPlan=${writePlan} onExample=${q => handle(q)}/>`
              : html`<${PlanView} onGenerate=${writePlan}/>`}
            ${u.busy || u.planBusy
              ? html`<div class="cogveil"><${Thinking} mode=${u.planBusy ? 'plan' : 'map'}
                  hint=${MODEL_LABEL[u.planBusy ? CFG.planModel : CFG.mapModel]
                    + ' · locked schema · usually 15–40s'}/></div>`
              : null}
          </div>
          <${Chat}/>
        </section>
        <${Inspector}/>
      </div>
    </div>
    <${Account}/>
    <${Offer}/>
    <${GuestWall}/>
    <${Wall}/>
    <${Toast}/>
  <//>`;
}
