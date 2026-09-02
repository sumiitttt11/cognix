/* =====================================================================
   Customize — the inspector. Design / Layout / Inspect, driven by a
   section list so a row is described once and rendered the same way
   whether it targets the whole sheet or just the selection.
   ===================================================================== */
import { html, useState, useEffect, useRef, Fragment } from './h.js';
import { shorten, clone } from './util.js';
import { OPT, BRANCHES, nodeCss, DEFAULT_STYLE } from './tokens.js';
import { NODE } from './model.js';
import { S, readVal, isOver, writeVal, snap, save, emit, note,
         selNodes, select, resetNodeStyle, undo, redo, jumpTo, reflow } from './store.js';
import { Row, NumFld, ColFld, SelFld, SegFld, ChkFld, AlignPad, Presets, Tokens,
         Hint, Code, Btn, Section } from './ui-bits.js';
import { alignSel, distributeSel, nudge, dupSel, copyStyle, pasteStyle,
         setNodeText, reparent, tidy, toggleLock, parentOptions } from './edits.js';
import { TEXT_MAX } from './sanitize.js';
import { downloadBackup, backupSize } from './backup.js';

/* ---------------- one row ---------------- */
function RowOf({ r, scope }){
  if(r.t === 'hint')  return html`<${Hint}>${r.l}<//>`;
  if(r.t === 'code')  return html`<${Code} v=${r.v}/>`;
  if(r.t === 'presets') return html`<${Presets}/>`;
  if(r.t === 'tokens')  return html`<${Tokens}/>`;
  if(r.t === 'node')    return r.el;
  if(r.t === 'btn')     return html`<${Btn} label=${r.l} onClick=${r.on}/>`;
  if(r.t === 'align')   return html`<${Row} label="Direction" scope=${scope}>
      <${AlignPad} scope=${scope}/><//>`;
  if(r.t === 'xy')      return html`<${XY}/>`;

  const over = scope === 'sel' && S.ui.selSet.size > 0 && isOver(r.p);
  const scrub = (r.t === 'num' || r.t === 'pair')
    ? { step: r.step || 1, min: r.min, max: r.max } : null;
  let ctl = null;
  if(r.t === 'num') ctl = html`<${NumFld} path=${r.p} scope=${scope} r=${r}/>`;
  if(r.t === 'pair') ctl = html`<span class="pad" style=${{ flex:1 }}>
      <${NumFld} path=${r.p}  scope=${scope} r=${Object.assign({}, r, { u:'X', wide:1 })}/>
      <${NumFld} path=${r.p2} scope=${scope} r=${Object.assign({}, r, { u:'Y', wide:1 })}/>
      <span style=${{ width:'22px' }}></span></span>`;
  if(r.t === 'col') ctl = html`<${ColFld} path=${r.p} scope=${scope}/>`;
  if(r.t === 'sel') ctl = html`<${SelFld} path=${r.p} scope=${scope} opts=${r.opts}/>`;
  if(r.t === 'seg') ctl = html`<${SegFld} path=${r.p} scope=${scope} opts=${r.opts}/>`;
  if(r.t === 'chk') ctl = html`<${ChkFld} path=${r.p} scope=${scope}/>`;
  return html`<${Row} label=${r.l} path=${r.p} scope=${scope} over=${over} scrub=${scrub}>
    ${ctl}<//>`;
}

/* ---------------- position pair ---------------- */
function XY(){
  const ns = selNodes();
  const g = k => ns.length && ns.every(n => Math.round(n[k]) === Math.round(ns[0][k]))
    ? Math.round(ns[0][k]) : null;
  const put = (k, v) => { snap('Move'); ns.forEach(n => { n[k] = v; n.moved = true; }); save(); emit(); };
  const f = (k, lab) => html`<span class="fld num" style=${{ maxWidth:'none' }}><i>${lab}</i>
    <input type="number" step="1" value=${g(k) == null ? '' : g(k)}
      aria-label=${k === 'x' ? 'Position X' : 'Position Y'}
      placeholder=${g(k) == null ? 'Mixed' : ''}
      onChange=${e => e.target.value !== '' && put(k, +e.target.value)}/></span>`;
  return html`<div class="r"><span class="rl" style=${{ cursor:'default' }}>Position</span>
    <span class="pad" style=${{ flex:1 }}>${f('x','X')}${f('y','Y')}
      <button type="button" class="gact" title="Snap back to auto layout"
        aria-label="Snap back to auto layout"
        style=${{ width:'22px', height:'22px' }} onClick=${tidy}>
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
          stroke-width="1.8" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h16"/></svg></button></span></div>`;
}

/* ---------------- arrange: align, distribute, nudge ---------------- */
const AICON = {
  left  : 'M4 3v18M8 8h9M8 16h6',
  cx    : 'M12 3v18M7 8h10M9 16h6',
  right : 'M20 3v18M7 8h9M11 16h5',
  top   : 'M3 4h18M8 8v9M16 8v6',
  cy    : 'M3 12h18M8 7v10M16 9v6',
  bottom: 'M3 20h18M8 7v9M16 11v5'
};
function Arrange(){
  const k = S.ui.selSet.size;
  const btn = (m, t) => html`<button key=${m} type="button" class="preset" title=${t}
    aria-label=${t} disabled=${k < 2}
    style=${{ justifyContent:'center', padding:'0', height:'24px', opacity: k < 2 ? .4 : 1 }}
    onClick=${() => alignSel(m)}>
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
      stroke-width="1.7" aria-hidden="true"><path d=${AICON[m]}/></svg></button>`;
  const nud = (dx, dy, glyph, label) => html`<button type="button" aria-label=${label}
    title=${label} onClick=${() => nudge(dx, dy)}>${glyph}</button>`;
  return html`<${Fragment}>
    <div class="r"><span class="rl" style=${{ cursor:'default' }}>Align</span>
      <div class="agrid" role="group" aria-label="Align selected boxes">
        ${btn('left','Left edges')}${btn('cx','Centres, vertical axis')}
        ${btn('right','Right edges')}${btn('top','Top edges')}
        ${btn('cy','Centres, horizontal axis')}${btn('bottom','Bottom edges')}</div></div>
    <div class="r"><span class="rl" style=${{ cursor:'default' }}>Distribute</span>
      <span class="seg" style=${{ flex:1 }} role="group" aria-label="Distribute selected boxes">
        <button type="button" disabled=${k < 3} onClick=${() => distributeSel('x')}>Horizontal</button>
        <button type="button" disabled=${k < 3} onClick=${() => distributeSel('y')}>Vertical</button></span></div>
    <div class="r"><span class="rl" style=${{ cursor:'default' }}>Nudge</span>
      <span class="seg" style=${{ flex:1 }} role="group" aria-label="Nudge selected boxes">
        ${nud(-1, 0, '←', 'Nudge left')}${nud(0, -1, '↑', 'Nudge up')}
        ${nud(0, 1, '↓', 'Nudge down')}${nud(1, 0, '→', 'Nudge right')}</span></div>
    <div class="r"><span class="rl" style=${{ cursor:'default' }}>Copy</span>
      <span class="seg" style=${{ flex:1 }}>
        <button type="button" onClick=${dupSel}>Duplicate</button>
        <button type="button" onClick=${copyStyle}>Copy style</button>
        <button type="button" onClick=${pasteStyle} disabled=${!S.ui.clip}>Paste style</button></span></div>
    <${Hint}>Arrow keys nudge by 1px on the canvas, Shift+arrows by 10. Ctrl+D duplicates.<//>
  <//>`;
}

/* ---------------- content: wording + reparenting ---------------- */
function Content(){
  const n = NODE(S.sheet.map, S.ui.sel);
  const [txt, setTxt] = useState(n ? n.text : '');
  useEffect(() => setTxt(n ? n.text : ''), [n && n.id, n && n.text]);
  if(!n) return null;
  const opts = parentOptions().filter(o => o[0] !== n.id);
  return html`<${Fragment}>
    <div class="r" style=${{ alignItems:'flex-start' }}>
      <span class="rl" style=${{ cursor:'default', paddingTop:'4px' }}><label for="insp-text">Text</label></span>
      <span class="fld" style=${{ flex:1 }}><textarea id="insp-text" value=${txt} rows="2"
        maxLength=${TEXT_MAX}
        onInput=${e => setTxt(e.target.value)}
        onBlur=${() => setNodeText(n.id, txt.trim() || n.text)}
        onKeyDown=${e => { e.stopPropagation();
          if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); e.target.blur(); } }}
      /></span></div>
    ${n.kind !== 'root' ? html`<div class="r">
      <span class="rl" style=${{ cursor:'default' }}><label for="insp-parent">Under</label></span>
      <span class="fld sel"><select id="insp-parent" value=${n.parent}
        onChange=${e => reparent(n.id, e.target.value)}>
        ${opts.map(o => html`<option key=${o[0]} value=${o[0]}>${o[1]}</option>`)}
      </select></span></div>` : null}
    <div class="r"><span class="rl" style=${{ cursor:'default' }}>Locked</span>
      <span style=${{ flex:1 }}></span>
      <button type="button" role="switch" aria-checked=${!!n.locked}
        aria-label="Lock this box" class=${'chk' + (n.locked ? ' on' : '')}
        onClick=${toggleLock}></button></div>
    <${Hint}>${n.locked
      ? 'This wording survives a rebuild.'
      : 'Lock a box to keep its wording when the map is regenerated.'}<//>
  <//>`;
}

/* ---------------- layers: the whole tree, Figma-style ---------------- */
function Layers(){
  const map = S.sheet.map; if(!map) return null;
  const kids = id => map.nodes.filter(n => n.parent === id);
  const rows = [];
  const walk = (n, d) => {
    /* these were <div onClick>, so the whole tree was mouse-only */
    rows.push(html`<button key=${n.id} type="button"
      class=${'lr' + (S.ui.selSet.has(n.id) ? ' on' : '') + (n.kind === 'leaf' ? ' leaf' : '')}
      style=${{ paddingLeft: (6 + d * 12) + 'px' }}
      aria-pressed=${S.ui.selSet.has(n.id)} title=${n.text}
      onClick=${e => select(n.id, e.shiftKey || e.metaKey || e.ctrlKey)}>
      <span class="sq" aria-hidden="true" style=${{ background: n.kind === 'root' ? '#1f1e1d'
        : (S.sheet.style.branch[n.branch] || '#c9c4be') }}></span>
      <span class="lt">${shorten(n.text, 30)}</span>
      ${n.locked ? html`<span class="lk">lock</span>` : null}
      ${Object.keys(n.style || {}).length ? html`<span class="lk" title="Has overrides">·</span>` : null}
    </button>`);
    kids(n.id).forEach(c => walk(c, d + 1));
  };
  const root = NODE(map, 'n-root'); if(root) walk(root, 0);
  return html`<div class="lay" role="group" aria-label="All boxes">${rows}</div>`;
}

/* ---------------- history: the undo stack as a list ---------------- */
function History(){
  const past = S.past, fut = S.future;
  return html`<div class="hist">
    <div class="kv"><span>${past.length} undo</span><span>${fut.length} redo</span></div>
    <div class="r" style=${{ padding:'2px 0' }}><span class="seg" style=${{ flex:1 }}>
      <button type="button" onClick=${undo} disabled=${!past.length}>Undo</button>
      <button type="button" onClick=${redo} disabled=${!fut.length}>Redo</button></span></div>
    ${past.slice().reverse().slice(0, 14).map((e, i) => html`<button key=${e.ts + '' + i}
      type="button" aria-label=${'Step back to before: ' + e.label}
      onClick=${() => jumpTo(past.length - 1 - i)}>
      <span>${e.label}</span>
      <i>${new Date(e.ts).toLocaleTimeString('en', { hour:'2-digit', minute:'2-digit' })}</i>
    </button>`)}
    ${!past.length ? html`<${Hint}>Nothing to step back through yet.<//>` : null}
  </div>`;
}

/* ---------------- the escape hatch ----------------
   localStorage is the only copy of anything made here, and a browser may
   clear it without asking. The store's quota message tells the user to
   export; this is the thing that does it. */
function Backup(){
  const [done, setDone] = useState(null);
  const kb = Math.max(1, Math.round(backupSize() / 1024));
  return html`<${Fragment}>
    <${Hint}>Everything you make is stored in this browser only — clearing site data
      removes it. A backup is one JSON file holding every chat and map.<//>
    <${Btn} label=${'Download a backup · ~' + kb + ' KB'}
      onClick=${() => setDone(downloadBackup() ? 'ok' : 'no')}/>
    ${done ? html`<${Hint}>${done === 'ok'
      ? 'Saved to your downloads folder.'
      : 'This browser blocked the download — check its download settings.'}<//>` : null}
  <//>`;
}

/* ---------------- what Cognix hands back ---------------- */
function cssDump(){
  const fake = { kind:'branch', branch:'solution', x:0, y:0, style:{} };
  const css = nodeCss(S.sheet.style, fake);
  const kebab = k => k.replace(/[A-Z]/g, m => '-' + m.toLowerCase());
  const decl = Object.keys(css).filter(k => k !== 'left' && k !== 'top')
    .map(k => '  ' + kebab(k) + ':' + css[k] + ';').join('\n');
  const W = S.sheet.style.wire, C = S.sheet.style.canvas;
  return '/* generated from Customize */\n.mm-box{\n' + decl + '\n}\n'
    + '.mm-wire{ stroke-width:' + W.width + 'px; opacity:' + (W.opacity / 100)
    + '; shape:' + W.type + '; colour:' + (W.mode === 'branch' ? 'per branch' : W.color) + '; }\n'
    + '.mm-canvas{ background:' + C.bg + '; grid:' + C.grid + ' ' + C.gridSize
    + 'px; snap:' + C.snap + 'px; }';
}
function jsonDump(){
  const m = S.sheet.map; if(!m) return '{ "map": null }';
  return JSON.stringify({ map_id:m.map_id, title:m.title, version:m.version,
    source:m.source, theme:S.sheet.preset, boxes:m.nodes.length,
    locked:m.nodes.filter(n => n.locked).map(n => n.text),
    styled:m.nodes.filter(n => Object.keys(n.style || {}).length).map(n =>
      ({ text:shorten(n.text, 22), overrides:n.style })),
    nodes:m.nodes.map(n => ({ id:n.id, kind:n.kind, branch:n.branch,
      text:n.text, x:Math.round(n.x), y:Math.round(n.y), locked:n.locked, moved:n.moved }))
  }, null, 2);
}

/* ---------------- the section list ---------------- */
function SECTIONS(){
  const out = [], k = S.ui.selSet.size, n = NODE(S.sheet.map, S.ui.sel), itab = S.ui.itab;

  if(itab === 'inspect'){
    out.push({ id:'ghist', name:'History', rows:[{ t:'node', el: html`<${History}/>` }] });
    out.push({ id:'gback', name:'Your data', rows:[{ t:'node', el: html`<${Backup}/>` }] });
    out.push({ id:'gcss', name:'Generated CSS', rows:[{ t:'code', v:cssDump() }] });
    out.push({ id:'gjson', name:'Map JSON', rows:[
      { t:'hint', l:'This is what Cognix hands back — a structure, not a picture.' },
      { t:'code', v:jsonDump() }] });
    return out;
  }
  if(itab === 'layout'){
    if(k) out.push({ id:'pos', name:'Selection', scope:'sel', rows:[
      { t:'xy' },
      { t:'hint', l:'Drag on the canvas to place a box by hand; locked boxes keep their wording when the map is rebuilt.' }] });
    out.push({ id:'arr', name:'Arrange', scope: k ? 'sel' : null,
      rows:[{ t:'node', el: html`<${Arrange}/>` }] });
    out.push({ id:'auto', name:'Auto layout', rows:[
      { t:'seg', l:'Spread', p:'layout.dir', opts:[['both','Both'],['right','One side']] },
      { t:'num', l:'Gap X', p:'layout.gapX', min:120, max:420, step:5, u:'px' },
      { t:'num', l:'Gap Y', p:'layout.gapY', min:34, max:130, step:2, u:'px' },
      { t:'btn', l:'Tidy up — drop manual positions', on: tidy }] });
    out.push({ id:'canv', name:'Canvas', rows:[
      { t:'col', l:'Background', p:'canvas.bg' },
      { t:'seg', l:'Grid', p:'canvas.grid', opts:[['none','Off'],['dots','Dots'],['lines','Lines']] },
      { t:'num', l:'Grid size', p:'canvas.gridSize', min:8, max:80, step:2, u:'px' },
      { t:'num', l:'Snap', p:'canvas.snap', min:0, max:40, step:1, u:'px' },
      { t:'hint', l:'Sheet is ' + Math.round(S.ui.CW) + ' × ' + Math.round(S.ui.CH) + ' px at 100%.' }] });
    out.push({ id:'lay', name:'Layers', rows:[{ t:'node', el: html`<${Layers}/>` }] });
    return out;
  }

  /* ---- design tab ---- */
  if(k === 1) out.push({ id:'scontent', name:'Content', scope:'sel',
    rows:[{ t:'node', el: html`<${Content}/>` }] });
  if(k){
    out.push({ id:'sfill', name:'Fill & opacity', scope:'sel', rows:[
      { t:'col', l:'Fill', p:'node.fill' },
      { t:'num', l:'Opacity', p:'node.opacity', min:10, max:100, step:5, u:'%' },
      { t:'seg', l:'Accent', p:'node.tint',
        opts:[['none','None'],['bar','Bar'],['stroke','Edge'],['fill','Wash']] }] });
    out.push({ id:'sstroke', name:'Stroke & corner', scope:'sel', rows:[
      { t:'col', l:'Stroke', p:'node.stroke' },
      { t:'num', l:'Weight', p:'node.strokeW', min:0, max:6, step:.5, u:'px' },
      { t:'num', l:'Radius', p:'node.radius', min:0, max:40, step:1, u:'px' },
      { t:'pair', l:'Padding', p:'node.padX', p2:'node.padY', min:0, max:40, step:1 },
      { t:'num', l:'Width', p:'node.width', min:0, max:420, step:5, u:'px' },
      { t:'hint', l:'Width 0 = hug the text.' }] });
    out.push({ id:'stext', name:'Text', scope:'sel', rows:[
      { t:'sel', l:'Font', p:'text.family', opts:OPT.family },
      { t:'sel', l:'Weight', p:'text.weight', opts:OPT.weight },
      { t:'num', l:'Size', p:'text.size', min:8, max:34, step:.5, u:'px' },
      { t:'num', l:'Line', p:'text.lh', min:1, max:2.4, step:.05, u:'×' },
      { t:'num', l:'Letter', p:'text.ls', min:-1, max:4, step:.1, u:'px' },
      { t:'seg', l:'Align', p:'text.align', opts:[['left','L'],['center','C'],['right','R']] },
      { t:'seg', l:'Case', p:'text.case',
        opts:[['none','Aa'],['upper','AA'],['title','Tt'],['lower','aa']] },
      { t:'col', l:'Colour', p:'text.color' }] });
    out.push({ id:'sshadow', name:'Shadow', scope:'sel', rows:[
      { t:'chk', l:'Enabled', p:'node.shadow.on' },
      { t:'align' },
      { t:'num', l:'Blur', p:'node.shadow.blur', min:0, max:40, step:1, u:'px' },
      { t:'col', l:'Colour', p:'node.shadow.color' }] });
  }
  out.push({ id:'theme', name:'Theme', rows:[{ t:'presets' },
    { t:'hint', l:'A preset rewrites every sheet token. Per-box overrides survive it.' }] });
  out.push({ id:'gbox', name:'Boxes — all', rows:[
    { t:'col', l:'Fill', p:'node.fill' },
    { t:'col', l:'Stroke', p:'node.stroke' },
    { t:'num', l:'Weight', p:'node.strokeW', min:0, max:6, step:.5, u:'px' },
    { t:'num', l:'Radius', p:'node.radius', min:0, max:40, step:1, u:'px' },
    { t:'pair', l:'Padding', p:'node.padX', p2:'node.padY', min:0, max:40, step:1 },
    { t:'seg', l:'Accent', p:'node.tint',
      opts:[['none','None'],['bar','Bar'],['stroke','Edge'],['fill','Wash']] },
    { t:'num', l:'Opacity', p:'node.opacity', min:10, max:100, step:5, u:'%' }] });
  out.push({ id:'gtype', name:'Type — branches', rows:[
    { t:'sel', l:'Font', p:'text.family', opts:OPT.family },
    { t:'sel', l:'Weight', p:'text.weight', opts:OPT.weight },
    { t:'num', l:'Size', p:'text.size', min:8, max:34, step:.5, u:'px' },
    { t:'num', l:'Line', p:'text.lh', min:1, max:2.4, step:.05, u:'×' },
    { t:'num', l:'Letter', p:'text.ls', min:-1, max:4, step:.1, u:'px' },
    { t:'seg', l:'Align', p:'text.align', opts:[['left','L'],['center','C'],['right','R']] },
    { t:'seg', l:'Case', p:'text.case',
      opts:[['none','Aa'],['upper','AA'],['title','Tt'],['lower','aa']] },
    { t:'col', l:'Colour', p:'text.color' }] });
  out.push({ id:'gleaf', name:'Points', rows:[
    { t:'num', l:'Size', p:'leaf.size', min:8, max:24, step:.5, u:'px' },
    { t:'sel', l:'Weight', p:'leaf.weight', opts:OPT.weight },
    { t:'col', l:'Colour', p:'leaf.color' }] });
  out.push({ id:'groot', name:'Centre box', rows:[
    { t:'col', l:'Fill', p:'root.fill' },
    { t:'col', l:'Text', p:'root.color' },
    { t:'num', l:'Size', p:'root.size', min:10, max:34, step:.5, u:'px' },
    { t:'num', l:'Radius', p:'root.radius', min:0, max:40, step:1, u:'px' },
    { t:'pair', l:'Padding', p:'root.padX', p2:'root.padY', min:2, max:40, step:1 }] });
  out.push({ id:'gwire', name:'Connectors', rows:[
    { t:'seg', l:'Shape', p:'wire.type',
      opts:[['curve','Curve'],['elbow','Elbow'],['straight','Line']] },
    { t:'num', l:'Weight', p:'wire.width', min:.5, max:6, step:.1, u:'px' },
    { t:'num', l:'Bend', p:'wire.curve', min:0, max:100, step:5, u:'%' },
    { t:'num', l:'Dash', p:'wire.dash', min:0, max:14, step:1, u:'px' },
    { t:'num', l:'Opacity', p:'wire.opacity', min:10, max:100, step:5, u:'%' },
    { t:'seg', l:'Colour', p:'wire.mode', opts:[['branch','Per branch'],['single','One colour']] },
    { t:'col', l:'Line', p:'wire.color' },
    { t:'chk', l:'Arrows', p:'wire.arrow' }] });
  out.push({ id:'gshadow', name:'Shadow — all', rows:[
    { t:'chk', l:'Enabled', p:'node.shadow.on' },
    { t:'align' },
    { t:'num', l:'Blur', p:'node.shadow.blur', min:0, max:40, step:1, u:'px' },
    { t:'col', l:'Colour', p:'node.shadow.color' }] });
  out.push({ id:'gtok', name:'Branch colours', rows:[{ t:'tokens' },
    { t:'hint', l:'Problem · Solution · Audience · Model · Market · Execution' }] });
  return out;
}

/* ---------------- the panel ---------------- */
export function Inspector(){
  const u = S.ui, k = u.selSet.size, n = NODE(S.sheet.map, u.sel);
  const name = !k ? 'Whole sheet'
    : (k > 1 ? k + ' boxes selected' : shorten(n ? n.text : '', 24));
  const count = !k ? ((S.sheet.map ? S.sheet.map.nodes.length : 0) + ' boxes')
    : (k > 1 ? 'multi' : (n ? (n.kind === 'root' ? 'centre' : n.kind) + (n.locked ? ' · locked' : '') : ''));
  const TAB = [['design','Design'],['layout','Layout'],['inspect','Inspect']];
  return html`<aside class="insp" aria-label="Customize">
    <div class="itabs" role="tablist" aria-label="Customize sections">
      ${TAB.map(t => html`<button key=${t[0]} type="button" role="tab"
        aria-selected=${u.itab === t[0]} aria-controls="insp-body"
        class=${'itab' + (u.itab === t[0] ? ' on' : '')}
        onClick=${() => { u.itab = t[0]; emit(); }}>${t[1]}</button>`)}
      <span class="isp"></span>
      <button type="button" class="itab" title="Close" aria-label="Close the Customize panel"
        onClick=${() => { u.insp = false; emit(); }}>×</button>
    </div>
    <div class="selbar">
      <b>${name}</b><span class="ssp"></span><span>${count}</span>
      ${k ? html`<button type="button" class="rst"
        aria-label="Reset this box to the sheet style" onClick=${resetNodeStyle}>Reset</button>` : null}
    </div>
    <div class="ibody" id="insp-body" role="tabpanel">
      ${SECTIONS().map(s => html`<${Section} key=${s.id} id=${s.id} name=${s.name} scope=${s.scope}>
        ${s.rows.map((r, i) => html`<${RowOf} key=${i} r=${r} scope=${s.scope || 'global'}/>`)}
      <//>`)}
    </div>
    <div class="ifoot">
      <button type="button" onClick=${() => {
        snap('Reset all');
        S.sheet.style = clone(S.sheet.baseline || DEFAULT_STYLE);
        S.sheet.preset = S.sheet.baseline ? 'custom' : 'default';
        if(S.sheet.map) S.sheet.map.nodes.forEach(x => x.style = {});
        reflow(); save(); emit();
        note(S.sheet.baseline ? 'Back to your saved preset' : 'Everything back to default');
      }}>Reset all</button>
      <button type="button" class="pri" onClick=${() => {
        S.sheet.baseline = clone(S.sheet.style); save();
        note('Saved — Reset all now returns here');
      }}>Save as preset</button>
    </div>
  </aside>`;
}


