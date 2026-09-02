/* =====================================================================
   transcript + composer + the router.

   Order matters: an explicit structural imperative beats the style parser,
   the style parser beats "treat it as a brief", and only a brief reaches
   the gateway. So restyling never spends a token, and generation always
   comes back inside the schema.
   ===================================================================== */
import { html, useRef, useEffect, useState, Fragment } from './h.js';
import { setPath, isCol, safePath } from './util.js';
import { NODE, buildMap, generateLocal, readIdea } from './model.js';
import { BKEYS, PRESETS } from './tokens.js';
import { S, snap, save, emit, note, logAdd, logPatch, select, reflow,
         applyPreset } from './store.js';
import { styleIntent, structIntent, makeNode, removeNode } from './intents.js';
import { genMap, MODEL_LABEL, CFG } from './api.js';
import { MiniWave, THINK } from './Thinking.js';
import * as V from './sanitize.js';

/* what one composer line may carry into a request. Long enough for a real
   brief, short enough that a paste of a whole document cannot become a
   6-figure-token call. */
const BRIEF_MAX = 2000;

/* ---- the tiniest possible rich text: **bold** and _italic_ ---- */
function rt(s){
  const out = [];
  String(s == null ? '' : s).split(/(\*\*[^*]+\*\*|_[^_]+_)/g).forEach((p, i) => {
    if(/^\*\*/.test(p)) out.push(html`<b key=${i}>${p.slice(2, -2)}</b>`);
    else if(/^_/.test(p)) out.push(html`<i key=${i}>${p.slice(1, -1)}</i>`);
    else if(p) out.push(p);
  });
  return out;
}
const Sw = ({ v }) => isCol(v) ? html`<span class="sw" style=${{ background:v }}></span>` : null;

/* ---- a patch table with its own Undo ---- */
function Patch({ m, i }){
  const p = m.patch;
  return html`<div class="patch">
    <div class="ph">${p.label}<span class="psp"></span>
      ${p.rows.length + (p.rows.length === 1 ? ' change' : ' changes')}
      <button onClick=${() => rollback(m)}>Undo</button></div>
    <table>${p.rows.map((r, j) => html`<tr key=${j}>
      <td>${r.l || r.p}</td>
      <td><span class="old"><${Sw} v=${r.from}/>${String(r.from)}</span>
          <span class="new"><${Sw} v=${r.to}/>${String(r.to)}</span></td></tr>`)}</table>
  </div>`;
}
function rollback(m){
  const p = m.patch; if(!p || !p.rows) return;
  snap('Roll back');
  let ok = 0;
  p.rows.forEach(r => {
    if(!safePath(r.p)) return;
    ok++;
    if(p.scope === 'sel' && p.ids) p.ids.forEach(id => {
      const n = NODE(S.sheet.map, id); if(!n) return;
      n.style = n.style || {};
      if(r.had) n.style[r.p] = r.from; else delete n.style[r.p];
    });
    else setPath(S.sheet.style, r.p, r.from);
  });
  logPatch(m.id, { patch:null, text: (m.text || '') + '\n_Rolled back._' });
  reflow(); save(); emit();
  note(ok === p.rows.length ? 'Rolled back'
    : 'Rolled back ' + ok + ' of ' + p.rows.length + ' changes');
}

/* ---- transcript ---- */
function Log(){
  const box = useRef(null);
  const on = S.ui.showLog, log = S.sheet.log;
  useEffect(() => { if(on && box.current) box.current.scrollTop = box.current.scrollHeight; },
    [on, log.length]);
  return html`<div class="logwrap">
    <div class="loghead">
      <b>${log.length ? log.length + (log.length === 1 ? ' message' : ' messages') : 'No messages yet'}</b>
      <span class="lsp"></span>
      <button onClick=${() => { S.ui.showLog = !on; emit(); }}>
        ${on ? 'Hide transcript' : 'Show transcript'}
        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor"
          stroke-width="2" style=${{ transform:'rotate(' + (on ? 180 : 0) + 'deg)' }}>
          <path d="M6 15l6-6 6 6"/></svg></button>
    </div>
    <div class=${'log' + (on ? ' on' : '')} ref=${box}><div class="inner">
      ${log.map((m, i) => html`<div class=${'m ' + m.role} key=${m.id}>
        <div class="who">${m.role === 'you' ? 'You' : m.role === 'err' ? 'Gateway' : 'Cognix'}</div>
        <div class="tx">${String(m.text == null ? '' : m.text).split('\n')
          .map((ln, j) => html`<div key=${j}>${rt(ln)}</div>`)}</div>
        ${m.think ? html`<div class="think"><${MiniWave}/>${m.think}</div>` : null}
        ${m.patch ? html`<${Patch} m=${m} i=${i}/>` : null}
        ${m.meta ? html`<div class="meta">${m.meta}</div>` : null}
      </div>`)}
    </div></div>
  </div>`;
}

/* ---- a style patch, applied and kept undoable from its own row ---- */
function applyStyle(pt){
  snap(pt.label + ' — chat');
  pt.rows.forEach(r => {
    if(!safePath(r.p)) return;
    if(pt.scope === 'sel' && pt.ids) pt.ids.forEach(id => {
      const n = NODE(S.sheet.map, id); if(!n) return;
      n.style = n.style || {}; n.style[r.p] = r.to;
    });
    else setPath(S.sheet.style, r.p, r.to);
  });
  if(pt.scope !== 'sel') S.sheet.preset = 'custom';
  reflow(); save(); emit();
}

/* ---- generation: the only path that spends a token ---- */
export async function build(brief0, notes){
  const u = S.ui;
  if(u.busy) return;
  /* the brief is user text on its way into a paid request: one line, capped */
  const brief = V.para(brief0, BRIEF_MAX);
  if(!brief) return note('Describe the idea first');
  const prev = S.sheet.map;
  u.busy = true; u.err = null; u.tab = 'map';
  const pend = logAdd({ role:'ai', text:'',
    think: THINK.map + (prev ? ' — rewriting this one' : '') });
  const ac = new AbortController(); u.abort = ac;
  const t0 = Date.now();
  let res = null, why = null;
  try { res = await genMap(brief, notes ? V.str(notes, 300) : null, ac.signal); }
  catch(e){ why = String((e && e.message) || e); }
  finally { u.busy = false; u.abort = null; }
  const secs = ((Date.now() - t0) / 1000).toFixed(1);

  snap('Generate map');
  let map, meta;
  if(res){
    /* the schema was forced, but the answer is still someone else's JSON:
       shape it before the layout addresses it by index */
    const d = V.mapContent(res.data, readIdea(brief).title);
    const content = {};
    BKEYS.forEach(k => content[k] = d[k]);
    map = buildMap(d.title, brief, content, prev, CFG.mapModel);
    meta = MODEL_LABEL[CFG.mapModel] + ' · ' + secs + 's · '
      + (res.usage.output_tokens || 0) + ' out / ' + (res.usage.input_tokens || 0) + ' in tokens'
      + (res.tries > 1 ? ' · ' + res.tries + ' attempts' : '');
  } else {
    map = generateLocal(brief, prev);
    meta = 'Gateway unreachable after ' + secs + 's — built offline instead';
    u.err = why;
  }
  S.sheet.map = map; S.sheet.topic = brief;
  u.plan = null; u.planErr = null; u.userZoom = false;
  select(null); reflow(); save();

  const leaves = map.nodes.filter(n => n.kind === 'leaf').length;
  logPatch(pend.id, { think:null, meta, model: res ? CFG.mapModel : 'local',
    text: '**' + map.title + '**' + (res ? '' : ' _(offline fallback)_')
      + '\n6 branches, ' + leaves + ' points, ' + map.nodes.length + ' boxes.'
      + (map.kept && map.kept.length
          ? '\nKept your locked wording: ' + map.kept.map(x => '“' + x + '”').join(', ') : '')
      + (res ? '' : '\n' + why) });
  emit();
}

/* ---- structural edits asked for in words ---- */
function doStruct(si){
  const map = S.sheet.map;
  if(si.op === 'miss') return logAdd({ role:'ai',
    text:'No box matches “' + si.q + '”. Try a few words from the box itself.' });
  if(si.op === 'nope') return logAdd({ role:'ai', text: si.why });
  if(si.op === 'add'){
    snap('Add point'); const n = makeNode(si.host, si.text);
    reflow(); save(); select(n.id);
    return logAdd({ role:'ai', text:'Added **' + n.text + '** under ' + si.host.text + '.' });
  }
  if(si.op === 'rename'){
    snap('Rename'); const old = si.node.text; si.node.text = si.text;
    if(si.node.id === 'n-root') map.title = si.text;
    save(); select(si.node.id);
    return logAdd({ role:'ai', text:'“' + old + '” → **' + si.text + '**.' });
  }
  if(si.op === 'del'){
    snap('Delete'); const t = si.node.text, k = removeNode(si.node.id);
    select(null); reflow(); save();
    return logAdd({ role:'ai',
      text:'Deleted **' + t + '**' + (k > 1 ? ' and the ' + (k - 1) + ' under it' : '') + '.' });
  }
  if(si.op === 'lock'){
    snap(si.want ? 'Lock' : 'Unlock'); si.node.locked = si.want; save(); select(si.node.id);
    return logAdd({ role:'ai', text:(si.want ? 'Locked' : 'Unlocked') + ' **' + si.node.text + '**'
      + (si.want ? ' — this wording now survives a rebuild.' : '.') });
  }
}

const HELP = 'I can do three things:\n'
  + '**Describe an idea** and Cognix writes the six branches with '
  + MODEL_LABEL[CFG.mapModel] + '.\n'
  + '**Restyle in words** — _navy boxes, dashed lines, mono theme, tighter_. That never leaves the browser.\n'
  + '**Edit the structure** — _add a point under Market_, _rename X to Y_, _lock the centre box_.';

/* ---- the router: cheapest match wins ---- */
export function handle(raw){
  const t = V.str(raw, BRIEF_MAX);
  if(!t) return;
  const map = S.sheet.map;
  logAdd({ role:'you', text:t });

  /* an explicit rebuild, with anything else in the line kept as a revision note */
  if(map && /^(regenerate|rebuild|redo|revise|try again|another take)\b/i.test(t)){
    const notes = t.replace(/^(regenerate|rebuild|redo|revise|try again|another take)\b[\s:—-]*/i, '');
    return build(S.sheet.topic || map.topic || map.title, notes || null);
  }
  if(/^(new map|new idea|start over)\b/i.test(t)){
    const rest = t.replace(/^(new map|new idea|start over)\b[\s:—-]*/i, '');
    if(rest.split(' ').length > 3) return build(rest);
  }
  /* a structural imperative beats the style parser */
  if(map && /^(add|new|rename|call|delete|remove|drop|lock|unlock)\b/i.test(t)){
    const si = structIntent(t); if(si) return doStruct(si);
  }
  const sp = styleIntent(t);
  if(sp && sp.preset){
    applyPreset(sp.preset);
    const nm = (PRESETS.find(p => p.id === sp.preset) || {}).name || sp.preset;
    return logAdd({ role:'ai', text:'Theme → **' + nm + '**. Per-box overrides kept.' });
  }
  if(sp){
    applyStyle(sp);
    return logAdd({ role:'ai',
      text: sp.rows.length + (sp.rows.length === 1 ? ' change' : ' changes') + ' — '
        + sp.label.toLowerCase() + '. No tokens spent.', patch: sp });
  }
  if(map){ const si = structIntent(t); if(si) return doStruct(si); }

  const words = t.split(' ').length;
  if(!map || words > 6
     || /\b(idea|startup|app|platform|tool|website|service|marketplace|hub|assistant|map)\b/i.test(t))
    return build(t);
  logAdd({ role:'ai', text: HELP });
}

/* ---- composer ---- */
const CHIPS = [
  ['Mono theme', 'switch to the mono theme'],
  ['Navy boxes', 'make the boxes navy with white text'],
  ['One side', 'lay it out on one side, left to right'],
  ['Tighter', 'tighter'],
  ['Add a point', 'add a point under Market: pilot with two hospitals']
];

export function Chat(){
  const u = S.ui, ta = useRef(null);
  const [txt, setTxt] = useState('');
  const busy = !!(u.busy || u.planBusy);
  const grow = el => { if(!el) return; el.style.height = 'auto';
    el.style.height = Math.min(120, el.scrollHeight) + 'px'; };
  useEffect(() => grow(ta.current), [txt]);

  const send = () => {
    const t = txt.trim(); if(!t || busy) return;
    setTxt(''); if(ta.current) ta.current.style.height = 'auto';
    handle(t);
  };
  const chip = q => { if(busy) return; handle(q); };

  return html`<${Fragment}>
    ${u.err ? html`<div class="banner" role="alert">
      <span>Gateway error — the map was built offline. ${u.err}</span>
      <button onClick=${() => { u.err = null; emit(); }}>Dismiss</button></div>` : null}
    <${Log}/>
    <div class="composer">
      <div class="qchips">${CHIPS.map(c => html`<button class="qchip" key=${c[0]}
        disabled=${busy} onClick=${() => chip(c[1])}>${c[0]}</button>`)}</div>
      <div class="cbox">
        <label class="sr" for="cognix-ask">Ask Cognix</label>
        <textarea id="cognix-ask" ref=${ta} rows="1" value=${txt} maxLength=${BRIEF_MAX}
          placeholder=${S.sheet.map ? 'Restyle, edit a box, or describe a new idea…'
                                    : 'Describe an idea — one line is enough.'}
          onInput=${e => setTxt(e.target.value)}
          onKeyDown=${e => { e.stopPropagation();
            if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); } }}/>
        <button class="ent" title=${busy ? 'Cognix is working…' : 'Send'}
          aria-label=${busy ? 'Cognix is working' : 'Send'}
          disabled=${busy || !txt.trim()} onClick=${send}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M12 6l6 6-6 6"/></svg></button>
      </div>
      <div class="cfoot">
        <button class=${'cz' + (u.insp ? ' on' : '')}
          onClick=${() => { u.insp = !u.insp; emit(); }}>
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 4v16"/></svg>
          Customize</button>
        <button class="plus" title="Attachments are not part of this prototype"
          onClick=${() => note('Attachments are not part of this prototype')}>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
            stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg></button>
        <span class="csp"></span>
        <span class=${'model' + (u.busy || u.planBusy ? ' busy' : '')}>
          ${u.busy || u.planBusy ? html`<${MiniWave}/>` : null}
          ${u.busy ? html`<b>${THINK.map}</b>`
            : u.planBusy ? html`<b>${THINK.plan}</b>`
            : html`<${Fragment}><b>Cognix</b>${' · ' + MODEL_LABEL[
                u.tab === 'plan' ? CFG.planModel : CFG.mapModel] + ' · locked schema'}<//>`}
        </span>
      </div>
    </div>
  <//>`;
}
