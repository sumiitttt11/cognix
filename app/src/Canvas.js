/* =====================================================================
   the canvas: boxes, wires, drag, marquee, snap guides, toolbar.

   Wires need the *rendered* width of each box (they stop at its edge), so
   after every commit a layout effect measures the DOM and stores widths in
   S.ui.wmap. Align/distribute read the same map.
   ===================================================================== */
import { html, useRef, useEffect, useLayoutEffect, useState } from './h.js';
import { clamp } from './util.js';
import { nodeCss, canvasBg } from './tokens.js';
import { NODE } from './model.js';
import { S, snap, save, emit, emitSoon, note, select, selectMany, setZoom, selNodes } from './store.js';
import { Mark } from './brand.js';
import { addChild, delSel, toggleLock, nudge, dupSel } from './edits.js';
import { str, TEXT_MAX } from './sanitize.js';


/* ---------------- wires ---------------- */
function Wires({ map, style, zoom, CW, CH, wmap }){
  if(!map) return null;
  const W = style.wire, out = [];
  map.nodes.forEach(n => {
    const p = n.parent ? NODE(map, n.parent) : null; if(!p) return;
    const pw = wmap[p.id], nw = wmap[n.id];
    if(pw == null || nw == null) return;
    const right = n.x >= p.x, s = right ? 1 : -1;
    const x1 = p.x + s * (pw / 2 + 2), y1 = p.y;
    const x2 = n.x - s * (nw / 2 + (W.arrow ? 7 : 2)), y2 = n.y;
    const col = W.mode === 'branch' ? (style.branch[n.branch] || W.color) : W.color;
    let d;
    if(W.type === 'straight') d = 'M' + x1 + ' ' + y1 + 'L' + x2 + ' ' + y2;
    else if(W.type === 'elbow'){ const mx = (x1 + x2) / 2;
      d = 'M' + x1 + ' ' + y1 + 'L' + mx + ' ' + y1 + 'L' + mx + ' ' + y2 + 'L' + x2 + ' ' + y2; }
    else { const k = Math.abs(x2 - x1) * (W.curve / 100);
      d = 'M' + x1 + ' ' + y1 + 'C' + (x1 + s * k) + ' ' + y1 + ',' + (x2 - s * k) + ' ' + y2
        + ',' + x2 + ' ' + y2; }
    out.push(html`<path key=${n.id} d=${d} fill="none" stroke=${col} stroke-width=${W.width}
      stroke-opacity=${W.opacity / 100} stroke-linecap="round" stroke-linejoin="round"
      stroke-dasharray=${W.dash ? W.dash + ' ' + (W.dash * 1.7).toFixed(1) : null}/>`);
    if(W.arrow) out.push(html`<path key=${n.id + '-a'} fill=${col} fill-opacity=${W.opacity / 100}
      d=${'M' + (x2 + s * 6) + ' ' + y2 + 'L' + x2 + ' ' + (y2 - 3.4) + 'L' + x2 + ' ' + (y2 + 3.4) + 'Z'}/>`);
  });
  return html`<svg viewBox=${'0 0 ' + CW + ' ' + CH} style=${{
    position:'absolute', left:0, top:0, width:CW + 'px', height:CH + 'px',
    overflow:'visible', pointerEvents:'none', transformOrigin:'0 0',
    transform:'scale(' + zoom + ')' }}>${out}</svg>`;
}

/* ---------------- one box ---------------- */
function Box({ n, style, sel, multi, editing, onDone }){
  const ref = useRef(null);
  useEffect(() => {
    if(!editing || !ref.current) return;
    const el = ref.current;
    el.contentEditable = 'true'; el.focus();
    const r = document.createRange(); r.selectNodeContents(el);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  }, [editing]);
  const cls = ['node'];
  if(n.moved) cls.push('moved');
  if(sel) cls.push('sel'); else if(multi) cls.push('multi');
  /* a paste into a contentEditable arrives as markup by default — images,
     styled spans, a whole document. Only the text is wanted, and only as much
     of it as a box may hold. */
  const onPaste = e => {
    e.preventDefault();
    const t = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
    const doc = e.target.ownerDocument;
    const room = TEXT_MAX - (e.target.textContent || '').length;
    const ins = t.replace(/\s+/g, ' ').slice(0, Math.max(0, room));
    if(!ins) return;
    const sel = doc.defaultView.getSelection();
    if(sel && sel.rangeCount){
      const r = sel.getRangeAt(0); r.deleteContents();
      r.insertNode(doc.createTextNode(ins)); r.collapse(false);
      sel.removeAllRanges(); sel.addRange(r);
    } else e.target.textContent = (e.target.textContent || '') + ins;
  };
  const onBefore = e => {
    if(e.data && (e.target.textContent || '').length >= TEXT_MAX) e.preventDefault();
  };
  return html`<div class=${cls.join(' ')} data-id=${n.id} style=${nodeCss(style, n)}>
    <span class="lbl" ref=${ref} role=${editing ? 'textbox' : null}
      onBlur=${e => { if(editing) onDone(e.target.textContent); }}
      onPaste=${onPaste} onBeforeInput=${onBefore}
      onKeyDown=${e => { e.stopPropagation();
        if(e.key === 'Enter'){ e.preventDefault(); e.target.blur(); }
        if(e.key === 'Escape'){ e.target.textContent = n.text; e.target.blur(); } }}>${n.text}</span>
    ${n.locked ? html`<span style=${{ marginLeft:'6px', fontSize:'9px', opacity:.55 }}>🔒</span>` : null}
    <span class="pin">moved</span>
    <span class="hnd nw"></span><span class="hnd ne"></span>
    <span class="hnd sw"></span><span class="hnd se"></span>
  </div>`;
}

/* ---------------- the canvas ---------------- */
export function Canvas({ onPlan, onExample }){
  const u = S.ui, map = S.sheet.map, style = S.sheet.style;
  const wrapRef = useRef(null), cvRef = useRef(null);
  const [wmap, setWmap] = useState({});
  const [band, setBand] = useState(null);      // marquee rect in canvas coords
  const [guides, setGuides] = useState([]);
  const drag = useRef(null);
  const guideKey = useRef('');
  const [ctx, setCtx] = useState(null);

  /* measure every box once the DOM settles — wires and align need widths */
  useLayoutEffect(() => {
    if(!cvRef.current) return;
    const w = {}, h = {};
    cvRef.current.querySelectorAll('.node').forEach(el => {
      w[el.dataset.id] = el.offsetWidth; h[el.dataset.id] = el.offsetHeight;
    });
    u.wmap = w; u.hmap = h;
    const same = Object.keys(w).length === Object.keys(wmap).length
      && Object.keys(w).every(k => wmap[k] === w[k]);
    if(!same) setWmap(w);
  });

  /* auto-fit until the user takes over the zoom */
  useEffect(() => {
    const el = wrapRef.current; if(!el) return;
    const doFit = () => {
      if(u.userZoom || !S.sheet.map) return;
      if(el.clientWidth < 60 || el.clientHeight < 60) return;
      const z = clamp(Math.min((el.clientWidth - 26) / u.CW, (el.clientHeight - 26) / u.CH), .3, 1.4);
      if(Math.abs(z - u.zoom) > .005){ u.zoom = z; emit(); }
      el.scrollLeft = (u.CW * z - el.clientWidth) / 2;
      el.scrollTop  = (u.CH * z - el.clientHeight) / 2;
    };
    doFit();
    /* ResizeObserver is everywhere we support, but a missing one must degrade
       to "fits on load" rather than throwing on the way up */
    if(typeof ResizeObserver !== 'function'){
      window.addEventListener('resize', doFit);
      return () => window.removeEventListener('resize', doFit);
    }
    const ro = new ResizeObserver(doFit); ro.observe(el);
    return () => ro.disconnect();
  }, [map && map.map_id, map && map.version, u.CW, u.CH, u.userZoom, u.insp, u.showLog]);

  const toCanvas = e => {
    const el = cvRef.current;
    if(!el) return { x:0, y:0 };
    const r = el.getBoundingClientRect();
    return { x: (e.clientX - r.left) / u.zoom, y: (e.clientY - r.top) / u.zoom };
  };

  /* ---- drag a box, or rubber-band the background ---- */
  function onDown(e){
    if(e.button === 2 || e.target.isContentEditable) return;
    const hit = e.target.closest('.node');
    if(hit){
      const n = NODE(map, hit.dataset.id); if(!n) return;
      const add = e.shiftKey || e.metaKey || e.ctrlKey;
      if(!(u.selSet.has(n.id) && !add)) select(n.id, add);
      drag.current = { sx:e.clientX, sy:e.clientY, moved:false,
        items:(u.selSet.has(n.id) ? selNodes() : [n]).map(m => ({ n:m, x:m.x, y:m.y })) };
      e.currentTarget.setPointerCapture(e.pointerId);
      e.stopPropagation();
      return;
    }
    if(!map) return;
    const p = toCanvas(e);
    drag.current = { band:true, x0:p.x, y0:p.y, add: e.shiftKey };
    setBand({ x:p.x, y:p.y, w:0, h:0 });
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onMove(e){
    const d = drag.current; if(!d) return;
    if(d.band){
      const p = toCanvas(e);
      setBand({ x:Math.min(d.x0, p.x), y:Math.min(d.y0, p.y),
                w:Math.abs(p.x - d.x0), h:Math.abs(p.y - d.y0) });
      return;
    }
    const dx = (e.clientX - d.sx) / u.zoom, dy = (e.clientY - d.sy) / u.zoom;
    if(!d.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    if(!d.moved){ d.moved = true; snap('Move box'); }
    const sn = style.canvas.snap;
    const ids = new Set(d.items.map(it => it.n.id));
    const others = map.nodes.filter(n => !ids.has(n.id));
    const gs = [];
    d.items.forEach(it => {
      let x = it.x + dx, y = it.y + dy;
      if(sn > 0){ x = Math.round(x / sn) * sn; y = Math.round(y / sn) * sn; }
      /* snap to a neighbour's centre line when we get close — Figma's guides */
      const nx = others.find(o => Math.abs(o.x - x) < 5);
      const ny = others.find(o => Math.abs(o.y - y) < 5);
      if(nx){ x = nx.x; gs.push({ v:true, at:x }); }
      if(ny){ y = ny.y; gs.push({ v:false, at:y }); }
      it.n.x = clamp(x, 40, u.CW - 40); it.n.y = clamp(y, 26, u.CH - 26); it.n.moved = true;
    });
    /* a pointermove can fire several times per frame. setGuides only when the
       guides actually changed, and one render per frame for the move itself. */
    const key = gs.map(g => (g.v ? 'v' : 'h') + g.at).join(',');
    if(key !== guideKey.current){ guideKey.current = key; setGuides(gs); }
    emitSoon();
  }

  function onUp(){
    const d = drag.current; drag.current = null;
    guideKey.current = '';
    setGuides([]);
    if(d && d.band){
      const b = band;
      setBand(null);
      if(b && (b.w > 6 || b.h > 6) && map){
        const inside = map.nodes.filter(n => n.x >= b.x && n.x <= b.x + b.w
                                          && n.y >= b.y && n.y <= b.y + b.h).map(n => n.id);
        if(inside.length) selectMany(d.add ? [...new Set([...u.selSet, ...inside])] : inside);
        else if(!d.add) select(null);
      } else if(!d.add) select(null);
      return;
    }
    if(d && d.moved) save();
  }

  const EX = [
    ["A private AI hub that answers only from a company's own documents, for banks and hospitals that cannot use public AI tools",
     '▶  A private AI hub for banks that cannot use public AI'],
    ['A same-day medicine delivery app for tier-2 Indian cities',
     '▶  Same-day medicine delivery for tier-2 cities'],
    ['A tool that turns support tickets into product roadmap items',
     '▶  Turn support tickets into roadmap items']
  ];
  const n = NODE(map, u.sel);

  return html`<div class="view on">
    <div class="cwrap" ref=${wrapRef} style=${{ background: style.canvas.bg }}
      onPointerDown=${onDown} onPointerMove=${onMove} onPointerUp=${onUp}
      onContextMenu=${e => { if(!map) return; e.preventDefault();
        const hit = e.target.closest('.node');
        if(hit) select(hit.dataset.id, false);
        setCtx({ x: Math.min(e.clientX, innerWidth - 190), y: Math.min(e.clientY, innerHeight - 230) }); }}
      onDoubleClick=${e => { const hit = e.target.closest('.node');
        if(hit){ select(hit.dataset.id, false); u.editing = hit.dataset.id; emit(); } }}>
      <div id="zoomWrap" style=${{ width:(map ? u.CW * u.zoom : 0) + 'px',
                                   height:(map ? u.CH * u.zoom : 0) + 'px' }}>
        <${Wires} map=${map} style=${style} zoom=${u.zoom} CW=${u.CW} CH=${u.CH} wmap=${wmap}/>
        <div id="canvas" ref=${cvRef} style=${Object.assign({
            position:'absolute', left:0, top:0, width:u.CW + 'px', height:u.CH + 'px',
            transformOrigin:'0 0', transform:'scale(' + u.zoom + ')' }, canvasBg(style))}>
          ${map ? map.nodes.map(nd => html`<${Box} key=${nd.id} n=${nd} style=${style}
            sel=${u.sel === nd.id} multi=${u.sel !== nd.id && u.selSet.has(nd.id)}
            editing=${u.editing === nd.id}
            onDone=${t => { u.editing = null;
              const v = str(t, TEXT_MAX);
              if(v && v !== nd.text){ snap('Rename'); nd.text = v;
                if(nd.id === 'n-root') S.sheet.map.title = v; save(); }
              emit(); }}/>`) : null}
          ${band ? html`<div class="marquee" style=${{ left:band.x + 'px', top:band.y + 'px',
            width:band.w + 'px', height:band.h + 'px' }}></div>` : null}
          ${guides.map((g, i) => html`<div key=${i} class=${'guide ' + (g.v ? 'v' : 'h')}
            style=${g.v ? { left:g.at + 'px', top:0, height:u.CH + 'px' }
                        : { top:g.at + 'px', left:0, width:u.CW + 'px' }}></div>`)}
        </div>
      </div>
    </div>

    ${!map ? html`<div class="empty" style=${{ display:'grid' }}><div class="card">
      <${Mark} cls="emark"/>
      <h2>Describe an idea. Cognix draws the map.</h2>
      <p>Six fixed branches, three points each, written by <b>Cognix</b> against a
         locked schema. Every box is yours to move, rename, restyle or lock —
         and <b>Customize</b> gives you Figma-level control over the whole sheet.</p>
      <div class="ex">${EX.map(x => html`<button type="button" key=${x[0]}
        onClick=${() => onExample(x[0])}>${x[1]}</button>`)}</div>
    </div></div>` : null}

    ${map ? html`<div class="ctools" role="toolbar" aria-label="Map tools">
      <button type="button" title="Add child" onClick=${addChild}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>Point</button>
      <button type="button" title="Delete" aria-label="Delete box"
        disabled=${!n || n.kind === 'root'} onClick=${delSel}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>
      <button type="button" title=${n && n.locked ? 'Unlock this box' : 'Lock this box'}
        aria-label=${n && n.locked ? 'Unlock this box' : 'Lock this box'}
        aria-pressed=${!!(n && n.locked)} disabled=${!n} onClick=${toggleLock}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/>
          <path d="M8 11V8a4 4 0 018 0v3"/></svg></button>
      <span class="sepv"></span>
      <button type="button" title="Zoom out" aria-label="Zoom out"
        onClick=${() => setZoom(u.zoom - .1, true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg></button>
      <span class="zm">${Math.round(u.zoom * 100)}%</span>
      <button type="button" title="Zoom in" aria-label="Zoom in"
        onClick=${() => setZoom(u.zoom + .1, true)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>
      <button type="button" title="Fit the map to the window"
        onClick=${() => { u.userZoom = false; emit(); }}>Fit</button>
      <span class="sepv"></span>
      <button type="button" onClick=${() => { u.insp = true; u.itab = 'design'; emit(); }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4v16"/></svg>Customize</button>
      <button type="button" class="pri" onClick=${onPlan}
        disabled=${!!(u.planBusy || u.busy)}>Plan →</button>
    </div>` : null}

    ${ctx ? html`<${CtxMenu} at=${ctx} node=${n} close=${() => setCtx(null)}/>` : null}
  </div>`;
}

/* ---------------- right-click menu ---------------- */
function CtxMenu({ at, node, close }){
  const box = useRef(null);
  useEffect(() => {
    const off = e => { if(!e.target.closest('.ctx')) close(); };
    const esc = e => { if(e.key === 'Escape'){ e.stopPropagation(); close(); } };
    document.addEventListener('pointerdown', off, true);
    document.addEventListener('keydown', esc, true);
    /* opened from the keyboard as well as the mouse, so it has to take focus */
    const first = box.current && box.current.querySelector('button');
    if(first) first.focus();
    return () => { document.removeEventListener('pointerdown', off, true);
                   document.removeEventListener('keydown', esc, true); };
  }, []);
  const b = (label, kbd, on) => html`<button key=${label} type="button" role="menuitem"
    onClick=${() => { close(); on(); }}>${label}${kbd ? html`<kbd>${kbd}</kbd>` : null}</button>`;
  return html`<div class="ctx on" ref=${box} role="menu" aria-label="Box actions"
    style=${{ left:at.x + 'px', top:at.y + 'px' }}>
    ${b('Rename', 'Dbl-click', () => { S.ui.editing = S.ui.sel; emit(); })}
    ${b('Add a point', 'Tab', addChild)}
    ${b(node && node.locked ? 'Unlock' : 'Lock this box', '', toggleLock)}
    ${b('Duplicate', 'Ctrl D', dupSel)}
    <div class="sep"></div>
    ${b('Customize this box', 'Ctrl K', () => { S.ui.insp = true; S.ui.itab = 'design'; emit(); })}
    ${b('Clear its overrides', '', () => { snap('Clear overrides');
      selNodes().forEach(x => x.style = {}); save(); emit(); note('Overrides cleared'); })}
    <div class="sep"></div>
    ${b('Delete', 'Del', delSel)}
  </div>`;
}

