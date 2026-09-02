/* =====================================================================
   the chat is an editing surface: plain sentences become token patches or
   structural edits. Nothing here calls the gateway — these are the local
   deterministic intents, tried before a message is treated as a brief.
   ===================================================================== */
import { clamp, uid, isDark } from './util.js';
import { NODE } from './model.js';
import { S, readVal, isOver, selNodes } from './store.js';

const COLORS = { navy:'#1e3a5f', blue:'#3b7dd8', green:'#2f9e6f', red:'#d1483f',
  orange:'#d97757', amber:'#d1962b', yellow:'#e2b93b', purple:'#8b5cf6', violet:'#8b5cf6',
  pink:'#d15b8f', teal:'#0f9ba8', cyan:'#12a8c4', black:'#141414', white:'#ffffff',
  grey:'#8b8580', gray:'#8b8580', cream:'#faf7f0', mint:'#e6f6ee', slate:'#48566b',
  charcoal:'#2b2b2b', ivory:'#fffdf7' };

function findColor(t){
  const hex = t.match(/#[0-9a-fA-F]{6}\b/); if(hex) return hex[0];
  const k = Object.keys(COLORS).find(c => new RegExp('\\b' + c + '(?![\\w-])', 'i').test(t));
  return k ? COLORS[k] : null;
}
function num(t, re){ const m = t.match(re); return m ? parseFloat(m[1]) : null; }

export function styleIntent(raw){
  const st = S.sheet.style, selSet = S.ui.selSet;
  const t = ' ' + raw.toLowerCase().replace(/\s+/g, ' ') + ' ';
  const useSel = selSet.size && /\b(this|these|that|it|selected|the selection)\b/.test(t);
  const scope = useSel ? 'sel' : 'global';
  const rows = [];
  const P = (p, l, to) => {
    const from = readVal(p, scope);
    if(from === to) return;
    rows.push({ p, l, from, to, had: scope === 'sel' ? isOver(p) : true });
  };
  const col = findColor(t);
  const has = re => re.test(t);

  /* preset / theme */
  const pm = t.match(/\b(mono|paper|neon|navy|default)\b/);
  const wantsTheme = /\b(theme|preset|palette|look and feel|style sheet)\b/.test(t)
    || /\b(?:switch to|apply)\b/.test(t);
  if(pm && wantsTheme && !useSel) return { preset: pm[1] };

  /* colour targets — an explicit box word beats a connector word in the same
     sentence, so "navy boxes with dashed lines" paints the boxes, not the wires */
  if(col){
    const boxWord = has(/\b(box|boxes|node|nodes|card|cards|bubble|bubbles|shape|shapes)\b/);
    if(has(/\b(background|canvas|sheet|paper|backdrop)\b/)) P('canvas.bg', 'Canvas background', col);
    else if(!boxWord && has(/\b(line|lines|wire|wires|connector|connectors|link|links|edge|edges|branches lines)\b/)){
      P('wire.mode', 'Connector colour mode', 'single'); P('wire.color', 'Connector colour', col);
    }
    else if(!boxWord && has(/\b(text|font|words|type|label|labels|writing)\b/)){
      P('text.color', 'Branch text', col);
      if(!useSel) P('leaf.color', 'Point text', col);
    }
    else if(has(/\b(centre|center|root|middle|main) (box|node|bubble)?\b/) && !useSel)
      P('root.fill', 'Centre box fill', col);
    else if(has(/\b(border|stroke|outline)\b/)) P('node.stroke', 'Box stroke', col);
    else {
      P('node.fill', 'Box fill', col);
      if(isDark(col)){                       /* keep it readable, Figma-style */
        P('node.tint', 'Branch accent', 'none');
        P('node.stroke', 'Box stroke', col);
        P('text.color', 'Branch text', '#ffffff');
        if(!useSel) P('leaf.color', 'Point text', '#e8ecf2');
      }
    }
  }
  /* corners */
  const r1 = num(t, /(\d+(?:\.\d+)?)\s*(?:px)?\s*(?:corner|corners|radius|rounded corners)/)
        || num(t, /(?:corner|corners|radius)\s*(?:of|to|=)?\s*(\d+(?:\.\d+)?)/);
  if(r1 != null) P('node.radius', 'Corner radius', clamp(r1, 0, 40));
  else if(has(/\b(pill|fully rounded|very round)\b/)) P('node.radius', 'Corner radius', 24);
  else if(has(/\b(round(er|ed)?)\b/) && !has(/\bcorners? (of|to)\b/))
    P('node.radius', 'Corner radius', clamp((+readVal('node.radius', scope) || 0) + 6, 0, 40));
  if(has(/\b(square|sharp|straight) corners?\b/) || has(/\bno rounding\b/))
    P('node.radius', 'Corner radius', 0);

  /* type */
  const fs = num(t, /(?:font|text)\s*size\s*(?:of|to|=)?\s*(\d+(?:\.\d+)?)/)
        || num(t, /(\d+(?:\.\d+)?)\s*px\s*(?:font|text|type)/);
  if(fs != null) P('text.size', 'Text size', clamp(fs, 8, 34));
  else if(has(/\b(bigger|larger|increase the) (text|font|type)\b/) || has(/\btext bigger\b/)){
    P('text.size', 'Text size', clamp((+readVal('text.size', scope) || 12) + 1.5, 8, 34));
    if(!useSel) P('leaf.size', 'Point size', clamp((+st.leaf.size || 11) + 1.5, 8, 24));
  } else if(has(/\b(smaller|tinier|reduce the) (text|font|type)\b/) || has(/\btext smaller\b/)){
    P('text.size', 'Text size', clamp((+readVal('text.size', scope) || 12) - 1.5, 8, 34));
    if(!useSel) P('leaf.size', 'Point size', clamp((+st.leaf.size || 11) - 1.5, 8, 24));
  }
  if(has(/\b(bold|heavier|heavy)\b/)) P('text.weight', 'Text weight', 700);
  if(has(/\b(light|thinner|regular weight)\b/)) P('text.weight', 'Text weight', 400);
  if(has(/\b(uppercase|all caps|caps)\b/)) P('text.case', 'Letter case', 'upper');
  if(has(/\b(no caps|normal case|sentence case)\b/)) P('text.case', 'Letter case', 'none');
  if(has(/\bcent(er|re)( the)? (text|labels|words)\b/) || has(/\btext cent(er|re)ed\b/))
    P('text.align', 'Text align', 'center');
  if(has(/\bleft align/)) P('text.align', 'Text align', 'left');
  if(has(/\b(mono|monospace|code font|typewriter)\b/)) P('text.family', 'Font', 'ui-monospace');
  if(has(/\b(serif|georgia|book)\b/)) P('text.family', 'Font', 'Georgia');
  if(has(/\b(inter|sans|clean font)\b/)) P('text.family', 'Font', 'Inter');

  /* connectors */
  if(has(/\b(dashed|dotted)\b/)) P('wire.dash', 'Connector dash', 4);
  if(has(/\bsolid lines?\b/)) P('wire.dash', 'Connector dash', 0);
  if(has(/\bstraight lines?\b/)) P('wire.type', 'Connector shape', 'straight');
  if(has(/\b(elbow|right ?angle|square lines|orthogonal)\b/)) P('wire.type', 'Connector shape', 'elbow');
  if(has(/\b(curved|curvy|curve)\b/)) P('wire.type', 'Connector shape', 'curve');
  if(has(/\barrows?\b/)) P('wire.arrow', 'Arrowheads', !has(/\b(no|remove|without) arrows?\b/));
  const wt = num(t, /(?:line|lines|connector|wire)s?\s*(?:weight|thickness|width)?\s*(?:of|to|=)?\s*(\d+(?:\.\d+)?)\s*px/);
  if(wt != null) P('wire.width', 'Connector weight', clamp(wt, .5, 6));
  else if(has(/\b(thicker|bolder) lines?\b/))
    P('wire.width', 'Connector weight', clamp(st.wire.width + .8, .5, 6));
  else if(has(/\b(thinner|finer) lines?\b/))
    P('wire.width', 'Connector weight', clamp(st.wire.width - .5, .5, 6));
  if(has(/\bone colou?r\b/)) P('wire.mode', 'Connector colour mode', 'single');
  if(has(/\b(per branch|branch colou?rs?|colou?r by branch)\b/)) P('wire.mode', 'Connector colour mode', 'branch');

  /* effects */
  if(has(/\b(no|remove|without|drop the|kill the) (shadow|shadows)\b/) || has(/\bflat\b/))
    P('node.shadow.on', 'Shadow', false);
  else if(has(/\b(shadow|shadows|lift|depth|raised)\b/)) P('node.shadow.on', 'Shadow', true);
  const op = num(t, /opacity\s*(?:of|to|=)?\s*(\d+)/);
  if(op != null) P('node.opacity', 'Box opacity', clamp(op, 10, 100));

  /* accent + canvas + spacing */
  if(has(/\b(wash|tinted|tint the boxes|colou?r the boxes by branch|filled by branch)\b/))
    P('node.tint', 'Accent', 'fill');
  if(has(/\b(bar|left bar|stripe)\b/)) P('node.tint', 'Accent', 'bar');
  if(has(/\b(colou?red (border|outline|edge)|edge accent)\b/)) P('node.tint', 'Accent', 'stroke');
  if(has(/\b(no accent|plain boxes|remove the (bar|stripe|accent))\b/)) P('node.tint', 'Accent', 'none');
  if(has(/\b(dot grid|dots|graph paper)\b/)) P('canvas.grid', 'Grid', 'dots');
  if(has(/\b(grid lines|line grid|squared paper)\b/)) P('canvas.grid', 'Grid', 'lines');
  if(has(/\b(no grid|hide the grid|plain canvas)\b/)) P('canvas.grid', 'Grid', 'none');
  if(has(/\b(more (space|room|breathing)|wider|spread (it )?out|looser)\b/)){
    P('layout.gapX', 'Gap X', clamp(st.layout.gapX + 30, 120, 420));
    P('layout.gapY', 'Gap Y', clamp(st.layout.gapY + 8, 34, 130));
  }
  if(has(/\b(tighter|closer together|less space|compact|condense)\b/)){
    P('layout.gapX', 'Gap X', clamp(st.layout.gapX - 30, 120, 420));
    P('layout.gapY', 'Gap Y', clamp(st.layout.gapY - 8, 34, 130));
  }
  if(has(/\b(one side|all (on )?the right|left to right|single side|org ?chart)\b/))
    P('layout.dir', 'Spread', 'right');
  if(has(/\bboth sides\b/)) P('layout.dir', 'Spread', 'both');
  const pd = num(t, /padding\s*(?:of|to|=)?\s*(\d+)/);
  if(pd != null){ P('node.padX', 'Padding X', clamp(pd + 4, 0, 40)); P('node.padY', 'Padding Y', clamp(pd, 0, 40)); }

  if(!rows.length) return null;
  return { rows, scope, ids: useSel ? [...selSet] : null,
    label: useSel ? (selSet.size > 1 ? selSet.size + ' boxes' : 'This box') : 'Whole sheet' };
}

/* ---------------- fuzzy "which box did they mean" ---------------- */
export function findNode(q){
  const map = S.sheet.map;
  if(!map || !q) return null;
  q = q.toLowerCase().trim().replace(/^["']|["'.?]$/g, '');
  let best = null, bs = 0;
  map.nodes.forEach(n => {
    const s = n.text.toLowerCase();
    let sc = s === q ? 100 : s.indexOf(q) >= 0 ? 60 : q.indexOf(s) >= 0 ? 40 : 0;
    if(!sc){
      const w = q.split(/\s+/).filter(x => x.length > 3);
      sc = w.filter(x => s.indexOf(x) >= 0).length * 12;
    }
    if(sc > bs){ bs = sc; best = n; }
  });
  return bs >= 12 ? best : null;
}

/* the structural half of the parser. Returns a plan object rather than
   mutating, so Chat.js owns the snapshot and the re-render. */
export function structIntent(raw){
  const map = S.sheet.map;
  const t = raw.trim(); let m;
  if(!map) return null;

  if(/^(add|new)\b/i.test(t) && !/^new map\b/i.test(t)){
    let rest = t.replace(/^(add|new)\s+(a\s+|an\s+|another\s+)?(point|box|node|item|bullet|child)?\s*/i, '');
    let target = null, um;
    /* "under Business Model: usage-based tier for pilots" — parent named first */
    if(um = rest.match(/^(?:under|to|in|below|inside|on)\s+(.+?)\s*(?::|—|,)\s*(.+)$/i)){
      target = findNode(um[1]); rest = um[2];
    }
    /* "usage-based tier for pilots under Business Model" — parent named last */
    else if(um = rest.match(/^(.+?)\s+(?:under|to|in|below|inside)\s+(.+)$/i)){
      const cand = findNode(um[2]);
      if(cand){ target = cand; rest = um[1]; }
    }
    /* "under Business Model" — no wording given */
    else if(um = rest.match(/^(?:under|to|in|below|inside|on)\s+(.+)$/i)){
      target = findNode(um[1]); if(target) rest = '';
    }
    const text = rest.replace(/^(called|saying|that says|:)\s*/i, '')
      .replace(/^["']|["'.]+$/g, '').trim();
    const host0 = target || NODE(map, S.ui.sel) || NODE(map, 'n-root');
    const host = host0.kind === 'leaf' ? NODE(map, host0.parent) : host0;
    return { op:'add', host, text: text || 'New point' };
  }
  if(m = t.match(/^(?:rename|call)\s+"?(.+?)"?\s+(?:to|as)\s+"?(.+?)"?[.]?$/i)){
    const n = findNode(m[1]);
    return n ? { op:'rename', node:n, text:m[2].trim() } : { op:'miss', q:m[1] };
  }
  if(m = t.match(/^(?:delete|remove|drop)\s+(?:the\s+)?(?:box|point|node)?\s*"?(.+?)"?[.]?$/i)){
    const n = findNode(m[1]);
    if(!n) return { op:'miss', q:m[1] };
    if(n.kind === 'root') return { op:'nope', why:'The centre box has to stay.' };
    return { op:'del', node:n };
  }
  if(m = t.match(/^(lock|unlock)\s+(?:the\s+)?(?:box|point)?\s*"?(.+?)"?[.]?$/i)){
    const n = findNode(m[2]);
    return n ? { op:'lock', node:n, want: m[1].toLowerCase() === 'lock' } : { op:'miss', q:m[2] };
  }
  return null;
}

/* ---------------- structural edits (used by chat AND the inspector) ---------------- */
export function makeNode(host, text){
  const map = S.sheet.map, u = S.ui;
  const kind = host.kind === 'root' ? 'branch' : 'leaf';
  const side = host.side === 'L' ? 'L' : 'R';
  const n = { id: uid('n-'), kind, branch: host.branch || 'problem', side,
    text: text || 'New point', parent: host.id, locked:false, moved:true,
    x: clamp(host.x + (side === 'L' ? -170 : 170), 60, u.CW - 60),
    y: clamp(host.y + 44, 40, u.CH - 40), style:{} };
  map.nodes.push(n); return n;
}
export function removeNode(id){
  const map = S.sheet.map;
  const kill = new Set([id]);
  let grew = true;
  while(grew){
    grew = false;
    map.nodes.forEach(n => { if(n.parent && kill.has(n.parent) && !kill.has(n.id)){ kill.add(n.id); grew = true; } });
  }
  map.nodes = map.nodes.filter(n => !kill.has(n.id));
  return kill.size;
}
