/* =====================================================================
   style tokens — the whole look of a sheet is data, nothing is hardcoded
   in the components. `v()` resolves one token for one node; everything
   else in here is the vocabulary it resolves against.
   ===================================================================== */
import { getPath, setPath, clone } from './util.js';

export const DEFAULT_STYLE = {
  node  : { fill:'#ffffff', stroke:'#e4e0db', strokeW:1, radius:10, padX:12, padY:7,
            width:0, tint:'bar', opacity:100,
            shadow:{ on:true, x:0, y:1, blur:3, color:'#1f1e1d20' } },
  text  : { family:'Inter', weight:600, size:12.5, lh:1.4, ls:0,
            align:'left', case:'none', color:'#1f1e1d' },
  leaf  : { size:11.5, weight:500, color:'#4b4744' },
  root  : { fill:'#1f1e1d', stroke:'#1f1e1d', color:'#ffffff', size:14, radius:12, padX:16, padY:10 },
  wire  : { type:'curve', width:1.6, dash:0, opacity:85, mode:'branch',
            color:'#c9c4be', arrow:false, curve:55 },
  layout: { dir:'both', gapX:195, gapY:54, tidy:true },
  canvas: { bg:'#ffffff', grid:'none', gridSize:24, snap:8 },
  branch: { problem:'#3b7dd8', solution:'#2f9e6f', audience:'#8b5cf6',
            model:'#d1962b', market:'#d15b8f', exec:'#0f9ba8' }
};

/* the fixed six — order here is the order in the schema the model must fill */
export const BRANCHES = [
  { key:'problem',  label:'Problem Statement',  side:'L' },
  { key:'solution', label:'Solution',           side:'L' },
  { key:'audience', label:'Target Audience',    side:'L' },
  { key:'model',    label:'Business Model',     side:'R' },
  { key:'market',   label:'Market Opportunity', side:'R' },
  { key:'exec',     label:'Execution Plan',     side:'R' }
];
export const BKEYS = BRANCHES.map(b => b.key);

export const CASE = { none:'none', upper:'uppercase', title:'capitalize', lower:'lowercase' };

export const OPT = {
  family: [['Inter','Inter'],['Georgia','Georgia'],['"Segoe UI"','Segoe UI'],
           ['ui-monospace','Monospace'],['"Times New Roman"','Times']],
  weight: [[400,'Regular'],[500,'Medium'],[600,'Semibold'],[700,'Bold']]
};

/* ---------------------------------------------------------------------
   token resolution. A node carries flat dotted-path overrides in
   `node.style`, e.g. { 'node.radius': 2 }. Those win. Failing that,
   `text.*` reads from the leaf/root group for those kinds so one slider
   can restyle "all leaf text" without touching branch labels.
   --------------------------------------------------------------------- */
export function v(style, n, path){
  if(n && n.style && Object.prototype.hasOwnProperty.call(n.style, path)) return n.style[path];
  const bits = path.split('.'), g = bits[0], k = bits.slice(1).join('.');
  if(n && g === 'text'){
    const grp = n.kind === 'root' ? 'root' : n.kind === 'leaf' ? 'leaf' : 'text';
    const gv = getPath(style, grp + '.' + k);
    if(gv !== undefined) return gv;
  }
  if(n && n.kind === 'root' && g === 'node'){
    const rv = getPath(style, 'root.' + k);
    if(rv !== undefined) return rv;
  }
  return getPath(style, path);
}

/* inline style object for one box (React wants camelCase keys) */
export function nodeCss(style, n){
  const g = p => v(style, n, p);
  const tone = style.branch[n.branch] || g('node.stroke');
  const tint = g('node.tint'), root = n.kind === 'root';
  const w = g('node.width');
  const s = {
    left: n.x + 'px', top: n.y + 'px',
    background: g('node.fill'),
    border: g('node.strokeW') + 'px solid ' + (tint === 'stroke' && !root ? tone : g('node.stroke')),
    borderRadius: g('node.radius') + 'px',
    padding: g('node.padY') + 'px ' + g('node.padX') + 'px',
    opacity: g('node.opacity') / 100,
    fontFamily: g('text.family') + ',Inter,system-ui,sans-serif',
    fontSize: g('text.size') + 'px',
    fontWeight: g('text.weight'),
    lineHeight: g('text.lh'),
    letterSpacing: g('text.ls') + 'px',
    textAlign: g('text.align'),
    textTransform: CASE[g('text.case')] || 'none',
    color: g('text.color')
  };
  if(w > 0) s.width = w + 'px';
  if(tint === 'bar' && !root)  s.borderLeft = '3px solid ' + tone;
  if(tint === 'fill' && !root){ s.background = tone + '14'; s.borderColor = tone + '55'; }
  if(g('node.shadow.on')) s.boxShadow = g('node.shadow.x') + 'px ' + g('node.shadow.y')
    + 'px ' + g('node.shadow.blur') + 'px ' + g('node.shadow.color');
  return s;
}

export function canvasBg(style){
  const C = style.canvas, s = C.gridSize, out = { backgroundColor: C.bg };
  if(C.grid === 'dots'){
    out.backgroundImage = 'radial-gradient(#00000014 1.2px,transparent 1.2px)';
    out.backgroundSize  = s + 'px ' + s + 'px';
  }
  if(C.grid === 'lines'){
    out.backgroundImage = 'linear-gradient(#0000000d 1px,transparent 1px),'
      + 'linear-gradient(90deg,#0000000d 1px,transparent 1px)';
    out.backgroundSize  = s + 'px ' + s + 'px';
  }
  return out;
}

/* =================== theme presets =================== */
export const PRESETS = [
  { id:'default', name:'Default', chip:'#ffffff', patch:{} },
  { id:'mono', name:'Mono', chip:'#e8e4df', patch:{
      'text.family':'ui-monospace', 'text.size':12, 'leaf.size':11, 'node.radius':4,
      'root.radius':4, 'node.tint':'none', 'node.shadow.on':false, 'wire.type':'elbow',
      'wire.mode':'single', 'wire.color':'#b6b0aa', 'canvas.grid':'lines' } },
  { id:'paper', name:'Paper', chip:'#f2e7cf', patch:{
      'canvas.bg':'#faf7f0', 'node.fill':'#fffdf7', 'node.stroke':'#e2d8c4',
      'text.family':'Georgia', 'text.color':'#39342b', 'leaf.color':'#5d5648',
      'root.fill':'#39342b', 'wire.mode':'single', 'wire.color':'#cdc2a9',
      'node.radius':6, 'node.shadow.on':false } },
  { id:'neon', name:'Neon', chip:'#12121a', patch:{
      'canvas.bg':'#101015', 'node.fill':'#181820', 'node.stroke':'#2b2b36',
      'text.color':'#e9e9f2', 'leaf.color':'#a6a6b6', 'root.fill':'#e9e9f2',
      'root.color':'#101015', 'wire.width':2, 'wire.opacity':100, 'canvas.grid':'dots',
      'node.shadow.color':'#00000055', 'node.tint':'stroke',
      'branch.problem':'#4da3ff', 'branch.solution':'#3ddc97', 'branch.audience':'#b388ff',
      'branch.model':'#ffc857', 'branch.market':'#ff7bb0', 'branch.exec':'#25e0e0' } },
  { id:'navy', name:'Navy', chip:'#1e3a5f', patch:{
      'canvas.bg':'#f5f7fb', 'node.fill':'#ffffff', 'node.stroke':'#d3ddea',
      'root.fill':'#1e3a5f', 'text.color':'#1e3a5f', 'leaf.color':'#4a5b70',
      'node.radius':14, 'node.tint':'fill', 'wire.type':'curve', 'wire.curve':70,
      'node.shadow.on':true, 'node.shadow.y':2, 'node.shadow.blur':6,
      'node.shadow.color':'#1e3a5f18' } }
];

export function presetStyle(id){
  const p = PRESETS.find(x => x.id === id);
  const s = clone(DEFAULT_STYLE);
  if(p) Object.keys(p.patch).forEach(k => setPath(s, k, p.patch[k]));
  return s;
}
