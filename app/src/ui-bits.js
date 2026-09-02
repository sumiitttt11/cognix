/* =====================================================================
   inspector controls. Every row is the same shape: a draggable label, a
   control, and an override dot that resets a per-box value back to the
   sheet. Rows read and write through the store so a change from the chat
   and a change from a slider are indistinguishable afterwards.
   ===================================================================== */
import { html, useState, useEffect, useRef, Fragment } from './h.js';
import { clamp, round, HEX } from './util.js';
import { OPT, PRESETS, BRANCHES } from './tokens.js';
import { S, readVal, isOver, writeVal, resetVal, snap, applyPreset, note, emit } from './store.js';

/* the rows are labelled by a sibling <span>, which a screen reader will not
   read as the field's name. Until the panel grows real <label for> pairs the
   dotted path is the honest fallback: "node shadow blur, spin button". */
const spoken = p => String(p || '').replace(/[.]/g, ' ');

/* --- the label doubles as a scrub handle, like Figma's field labels --- */
function useScrub(path, scope, step, min, max){
  const st = useRef(null);
  const stop = () => { if(st.current){ st.current = null; emit(); } };
  return {
    onPointerDown(e){
      if(e.button !== 0) return;
      const start = +readVal(path, scope) || 0;
      st.current = { x: e.clientX, start, last: start };
      snap('Scrub ' + path);
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    onPointerMove(e){
      if(!st.current) return;
      const dx = e.clientX - st.current.x;
      const v = clamp(round(st.current.start + dx * (step || 1), step || 1),
                      min != null ? min : -9999, max != null ? max : 9999);
      if(v === st.current.last) return;      // several moves land on one step
      st.current.last = v;
      writeVal(path, v, scope, true);        // one render per frame while dragging
    },
    onPointerUp: stop,
    onPointerCancel: stop,
    onLostPointerCapture: stop
  };
}

export function Row({ label, path, scope, over, scrub, children, onReset }){
  const h = useScrub(path, scope, scrub && scrub.step, scrub && scrub.min, scrub && scrub.max);
  return html`<div class=${'r' + (over ? ' over' : '')}>
    <span class="rl" style=${scrub ? null : { cursor:'default' }} ...${scrub ? h : {}}>${label}</span>
    ${children}
    <button type="button" class="ovr" title="Reset to sheet style"
      aria-label=${'Reset ' + spoken(path) + ' to the sheet style'} tabIndex=${over ? 0 : -1}
      onClick=${() => { if(onReset) onReset(); else if(over){ resetVal(path); note('Override cleared'); } }}></button>
  </div>`;
}

export function NumFld({ path, scope, r, unit }){
  const val = readVal(path, scope);
  return html`<span class="fld num" style=${r.wide ? { maxWidth:'none' } : null}>
    ${unit || r.u ? html`<i>${unit || r.u}</i>` : null}
    <input type="number" step=${r.step || 1} aria-label=${spoken(path)}
      min=${r.min != null ? r.min : -9999} max=${r.max != null ? r.max : 9999}
      value=${val == null ? '' : val} placeholder=${val == null ? 'Mixed' : ''}
      onChange=${e => {
        const v = e.target.value === '' ? null : +e.target.value;
        if(v == null || !isFinite(v)) return;
        snap('Set ' + path);
        writeVal(path, clamp(v, r.min != null ? r.min : -9999, r.max != null ? r.max : 9999), scope);
      }}/></span>`;
}

export function ColFld({ path, scope }){
  const val = readVal(path, scope);
  const [txt, setTxt] = useState(val == null ? '' : String(val));
  useEffect(() => setTxt(val == null ? '' : String(val)), [val]);
  const put = v => { snap('Set ' + path); writeVal(path, v, scope); };
  return html`<span class="fld">
    <span class="swatch" style=${{ background: val == null ? '#555' : val }}>
      <input type="color" value=${HEX(val)} aria-label=${spoken(path) + ' colour'}
        onChange=${e => put(e.target.value)}/></span>
    <input value=${txt} placeholder=${val == null ? 'Mixed' : ''} maxLength=${9}
      aria-label=${spoken(path) + ' hex'}
      onInput=${e => { setTxt(e.target.value);
        if(/^#[0-9a-fA-F]{6}$/.test(e.target.value.trim())) put(e.target.value.trim()); }}
      onBlur=${() => setTxt(val == null ? '' : String(val))}/></span>`;
}

export function SelFld({ path, scope, opts }){
  const val = readVal(path, scope);
  return html`<span class="fld sel"><select value=${val == null ? '' : String(val)}
    aria-label=${spoken(path)}
    onChange=${e => { const o = opts.find(x => String(x[0]) === e.target.value);
      snap('Set ' + path); writeVal(path, o ? o[0] : e.target.value, scope); }}>
    ${val == null ? html`<option value="">Mixed</option>` : null}
    ${opts.map(o => html`<option key=${o[0]} value=${String(o[0])}>${o[1]}</option>`)}
  </select></span>`;
}

export function SegFld({ path, scope, opts }){
  const val = readVal(path, scope);
  return html`<span class="seg" role="group" aria-label=${spoken(path)}>${opts.map(o => html`<button
    key=${o[0]} type="button" aria-pressed=${String(o[0]) === String(val)}
    class=${String(o[0]) === String(val) ? 'on' : ''} title=${o[2] || o[1]}
    onClick=${() => { snap('Set ' + path); writeVal(path, o[0], scope); }}>${o[1]}</button>`)}</span>`;
}

export function ChkFld({ path, scope }){
  const val = readVal(path, scope);
  return html`<${Fragment}>
    <span style=${{ flex:1 }}></span>
    <button type="button" role="switch" aria-checked=${!!val} aria-label=${spoken(path)}
      class=${'chk' + (val ? ' on' : '')}
      onClick=${() => { snap('Toggle ' + path); writeVal(path, !val, scope); }}></button>
  <//>`;
}

/* shadow offset as a 3x3 direction pad */
const DIR = { '-1,-1':'up and left', '0,-1':'up', '1,-1':'up and right',
  '-1,0':'left', '0,0':'centred', '1,0':'right',
  '-1,1':'down and left', '0,1':'down', '1,1':'down and right' };
export function AlignPad({ scope }){
  const x = readVal('node.shadow.x', scope), y = readVal('node.shadow.y', scope);
  const cells = [];
  [-1, 0, 1].forEach(ry => [-1, 0, 1].forEach(rx => cells.push([rx, ry])));
  return html`<div class="align" role="group" aria-label="Shadow direction">${cells.map(([rx, ry]) => html`<button
    key=${rx + ',' + ry} type="button" aria-label=${'Shadow ' + DIR[rx + ',' + ry]}
    aria-pressed=${Math.sign(x || 0) === rx && Math.sign(y || 0) === ry}
    class=${Math.sign(x || 0) === rx && Math.sign(y || 0) === ry ? 'on' : ''}
    onClick=${() => { snap('Shadow direction');
      const d = Math.max(1, Math.abs(+readVal('node.shadow.blur', scope) || 3) / 3);
      writeVal('node.shadow.x', +(rx * d).toFixed(1), scope);
      writeVal('node.shadow.y', +(ry * d).toFixed(1), scope); }}></button>`)}</div>`;
}

export function Presets(){
  return html`<div class="presets">${PRESETS.map(p => html`<button key=${p.id} type="button"
    class=${'preset' + (S.sheet.preset === p.id ? ' on' : '')}
    aria-pressed=${S.sheet.preset === p.id}
    onClick=${() => { applyPreset(p.id); note(p.name + ' applied'); }}>
    <span class="ps" style=${{ background: p.chip }} aria-hidden="true"></span>${p.name}</button>`)}</div>`;
}

export function Tokens(){
  return html`<div class="tokens">${BRANCHES.map(b => html`<span key=${b.key} class="tok"
    title=${b.label} style=${{ background: S.sheet.style.branch[b.key] }}>
    <input type="color" value=${HEX(S.sheet.style.branch[b.key])} aria-label=${b.label + ' colour'}
      onChange=${e => { snap('Branch colour'); writeVal('branch.' + b.key, e.target.value, 'global'); }}/>
  </span>`)}</div>`;
}

export const Hint = ({ children }) => html`<div class="hint">${children}</div>`;
export const Code = ({ v }) => html`<pre class="code">${v}</pre>`;

export function Btn({ label, onClick, style }){
  return html`<div class="r"><button type="button" class="preset"
    style=${Object.assign({ flex:1, justifyContent:'center' }, style || {})}
    onClick=${onClick}>${label}</button></div>`;
}

/* a collapsible section — same markup the HTML prototype used, so the
   existing stylesheet needs no changes */
export function Section({ id, name, scope, children }){
  const shut = S.ui.shut[id];
  return html`<div class=${'sec' + (shut ? ' shut' : '')}>
    <button type="button" class="sech" aria-expanded=${!shut}
      onClick=${() => { S.ui.shut[id] = !shut; emit(); }}>
      <svg class="cv" viewBox="0 0 24 24" width="9" height="9" fill="none" aria-hidden="true"
        stroke="currentColor" stroke-width="2.6"><path d="M6 9l6 6 6-6"/></svg>
      ${name}
      ${scope === 'sel' ? html`<em style=${{ fontStyle:'normal', color:'var(--acc)' }}>${' · selection'}</em>` : null}
    </button>
    <div class="secb">${children}</div>
  </div>`;
}

export { OPT };
