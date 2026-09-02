/* =====================================================================
   small shared helpers — no React in here
   ===================================================================== */
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const uid   = p => p + Math.random().toString(36).slice(2, 8);
export const cap   = s => (s ? s[0].toUpperCase() + s.slice(1) : s);
/* the reviver drops any own "__proto__"/"constructor"/"prototype" key that a
   saved blob may carry: JSON.parse creates those as real own properties */
export const clone = o => JSON.parse(JSON.stringify(o),
  (k, v) => (k === '' || safeKey(k)) ? v : undefined);

/* snap v to the nearest multiple of the step s, keeping s's decimal places */
export const round = (v, s) => {
  const d = (String(s).split('.')[1] || '').length;
  return +(Math.round(v / s) * s).toFixed(d);
};

export function shorten(s, n){
  s = String(s || '').trim();
  return s.length > n ? s.slice(0, n - 1).replace(/[\s,;:.]+$/, '') + '…' : s;
}

/* ---------------------------------------------------------------------
   dotted paths.

   Paths do not all come from our own code: a style override key can arrive
   from a saved localStorage blob or from a patch row in a restored
   transcript. `__proto__.x` down setPath() would write to Object.prototype
   and poison every object on the page, so the segment allow-list below is
   the gate every path walker goes through.
   --------------------------------------------------------------------- */
const BADKEY = ['__proto__', 'prototype', 'constructor'];
export const safeKey  = k => typeof k === 'string' && k.length > 0
  && BADKEY.indexOf(k) < 0;
export const safePath = p => typeof p === 'string' && p.length > 0
  && p.split('.').every(safeKey);
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

export function getPath(o, p){
  if(!safePath(p)) return undefined;
  return p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
}
/* returns true when the write happened, so a caller can report a refusal */
export function setPath(o, p, v){
  if(!o || typeof o !== 'object' || !safePath(p)) return false;
  const k = p.split('.'), last = k.pop();
  let t = o;
  for(const x of k){
    if(t[x] == null || typeof t[x] !== 'object') t[x] = {};
    t = t[x];
  }
  t[last] = v;
  return true;
}
export function flatten(o, pre, out){
  out = out || {}; pre = pre || '';
  Object.keys(o).forEach(k => {
    if(!safeKey(k)) return;
    const v = o[k];
    if(v && typeof v === 'object' && !Array.isArray(v)) flatten(v, pre + k + '.', out);
    else out[pre + k] = v;
  });
  return out;
}
/* fill in tokens a saved sheet is missing, so old localStorage still loads.
   `into` may have come from disk, so its own keys are what count — a
   poisoned prototype must not be able to make a default look "already set". */
export function mergeDefaults(def, into){
  Object.keys(def).forEach(k => {
    if(!safeKey(k)) return;
    if(def[k] && typeof def[k] === 'object' && !Array.isArray(def[k])){
      if(!own(into, k) || !into[k] || typeof into[k] !== 'object') into[k] = {};
      mergeDefaults(def[k], into[k]);
    } else if(!own(into, k) || into[k] === undefined) into[k] = def[k];
  });
  return into;
}

export const HEX = c => (String(c || '#000000').match(/^#[0-9a-fA-F]{6}/) || ['#000000'])[0];
export const isCol = x => typeof x === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(x);

/* rough perceptual lightness of a #rrggbb, 0 = black, 1 = white */
export function isDark(hex){
  const h = String(hex || '').replace('#', '');
  if(h.length < 6) return false;
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16),
        b = parseInt(h.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.55;
}

/* "2 hours ago" / "Mon" / "12 Aug" — the tray needs it for chat history */
export function ago(ts){
  const s = (Date.now() - ts) / 1000;
  if(s < 60)    return 'now';
  if(s < 3600)  return Math.floor(s / 60) + 'm';
  if(s < 86400) return Math.floor(s / 3600) + 'h';
  const d = new Date(ts);
  if(s < 7 * 86400) return ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getDay()];
  return d.getDate() + ' ' + ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
}

/* the date buckets Claude and ChatGPT use down the side */
export function bucket(ts){
  const d = new Date(ts), n = new Date();
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = (day(n) - day(d)) / 86400000;
  if(diff <= 0) return 'Today';
  if(diff === 1) return 'Yesterday';
  if(diff < 7)  return 'Previous 7 days';
  if(diff < 30) return 'Previous 30 days';
  return d.getFullYear() === n.getFullYear()
    ? d.toLocaleString('en', { month:'long' })
    : String(d.getFullYear());
}
export const BUCKETS = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days'];
