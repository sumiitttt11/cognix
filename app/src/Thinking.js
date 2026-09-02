/* =====================================================================
   Cognix — the thinking animation.

   The reference is a neon audio trace: pink on the left, violet through
   the middle where it swells, amber on the right where it breaks into
   dense spikes, with sparks drifting around it. None of it is a bitmap.
   The trace is four travelling sines under a moving amplitude envelope,
   redrawn every frame, so it loops forever, has no background to remove,
   and re-scales to any width — 318px inside the overlay card, 34px inline
   in the composer.

   It lives on its own requestAnimationFrame loop inside a <canvas>, which
   is what keeps it out of the store: at 60fps an emit() would re-render
   every box on the map sixty times a second.
   ===================================================================== */
import { html, useRef, useEffect, useState } from './h.js';
import { MARK } from './brand.js';

/* the palette, read left to right off the reference */
const STOPS = [[0, '#ff2f87'], [.22, '#e63cc4'], [.42, '#a855f7'],
               [.6, '#7c6cff'], [.78, '#ff7a3d'], [1, '#ffb02e']];
const TAU = Math.PI * 2;
const quiet = () => !!(window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

const hex = c => [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16),
                  parseInt(c.slice(5, 7), 16)];
/* colour of a spark at position xn — same ramp the stroke gradient uses */
function colAt(xn){
  let a = STOPS[0], b = STOPS[STOPS.length - 1];
  for(let i = 0; i < STOPS.length - 1; i++)
    if(xn >= STOPS[i][0] && xn <= STOPS[i + 1][0]){ a = STOPS[i]; b = STOPS[i + 1]; }
  const k = (xn - a[0]) / Math.max(1e-6, b[0] - a[0]);
  const A = hex(a[1]), B = hex(b[1]);
  return 'rgb(' + A.map((v, i) => Math.round(v + (B[i] - v) * k)).join(',') + ')';
}

/* deterministic sparks: no per-frame bookkeeping, position is a pure
   function of time, so a dropped frame never accumulates drift */
function sparks(n){
  const out = [];
  for(let i = 0; i < n; i++){
    const r = Math.sin(i * 12.9898) * 43758.5453;
    const f = x => { const v = Math.sin((i + x) * 78.233) * 12345.6789; return v - Math.floor(v); };
    out.push({ x0: r - Math.floor(r), sp: .012 + f(1) * .055,
               off: (f(2) - .5) * 1.7, rad: .5 + f(3) * 1.7,
               ph: f(4) * TAU, ride: f(5) > .55 });
  }
  return out;
}

/* ---------------- one frame ---------------- */
function paint(cx, w, h, t, sp){
  cx.clearRect(0, 0, w, h);
  const mid = h / 2, amp = h * .40;
  const k = Math.max(.16, w / 300);                 // fewer cycles when small
  const lg = cx.createLinearGradient(0, 0, w, 0);
  STOPS.forEach(s => lg.addColorStop(s[0], s[1]));

  /* the trace: a broad envelope, a burst travelling through it, and spikes
     that get denser and sharper toward the amber end */
  const yAt = xn => {
    const env = Math.exp(-Math.pow((xn - .44) / .32, 2)) * .82 + .18;
    const burst = Math.exp(-Math.pow((xn - ((t * .16) % 1.5 - .25)) / .12, 2));
    const spike = .3 + xn * xn * 1.7;
    return (env + burst * .55) * (
        Math.sin(xn * TAU * 6.1 * k + t * 1.7) * .42
      + Math.sin(xn * TAU * 17.3 * k - t * 2.6) * .21
      + Math.sin(xn * TAU * 41 * k + t * 4.4) * .12 * spike
      + Math.sin(xn * TAU * 97 * k - t * 6.1) * .055 * spike);
  };

  cx.globalCompositeOperation = 'lighter';
  cx.lineCap = 'round'; cx.lineJoin = 'round'; cx.strokeStyle = lg;
  const trace = () => {
    cx.beginPath();
    for(let x = 0; x <= w; x += 1.1){
      const y = mid - yAt(x / w) * amp;
      x ? cx.lineTo(x, y) : cx.moveTo(x, y);
    }
    cx.stroke();
  };
  /* the halo is a blurred copy of the gradient stroke rather than a
     shadowBlur: shadowColor takes one flat colour, which would paint the
     whole glow purple and throw away the pink and amber ends */
  const blur = 'filter' in cx;
  [[6.4 * k, 7 * k, .5], [2.5 * k, 2.4 * k, .72], [1.15 * k, 0, 1]].forEach(p => {
    cx.lineWidth = Math.max(.85, p[0]);
    cx.globalAlpha = p[2];
    if(blur) cx.filter = p[1] ? 'blur(' + p[1].toFixed(1) + 'px)' : 'none';
    else { cx.shadowColor = '#b04bff'; cx.shadowBlur = p[1] * 3.4; }
    trace();
  });
  if(blur) cx.filter = 'none';

  sp.forEach(s => {
    const xn = ((s.x0 + t * s.sp) % 1.14) - .07;
    if(xn < 0 || xn > 1) return;
    const y = s.ride ? mid - yAt(xn) * amp + s.off * h * .12
                     : mid + s.off * h * .42;
    const col = colAt(xn);
    cx.globalAlpha = (.22 + .78 * Math.pow(Math.sin(t * 1.9 + s.ph) * .5 + .5, 2))
      * (1 - Math.abs(xn - .5) * .55);
    cx.fillStyle = col; cx.shadowColor = col; cx.shadowBlur = s.rad * 5 * k;
    cx.beginPath(); cx.arc(xn * w, y, Math.max(.4, s.rad * k), 0, TAU); cx.fill();
  });

  cx.globalCompositeOperation = 'source-over';
  cx.globalAlpha = 1; cx.shadowBlur = 0;
}

/* ---------------- the canvas ---------------- */
export function Wave({ w = 318, h = 62, cls = 'cwave', n = 46 }){
  const ref = useRef(null);
  useEffect(() => {
    const c = ref.current; if(!c) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    const cx = c.getContext('2d'); if(!cx) return;
    cx.scale(dpr, dpr);
    const sp = sparks(n), still = quiet();
    let raf = 0;
    const t0 = performance.now();
    /* paint frame zero synchronously: requestAnimationFrame does not fire in
       a hidden or throttled tab, and an empty canvas would show as a gap */
    paint(cx, w, h, .7, sp);
    if(!still){
      const loop = now => { paint(cx, w, h, (now - t0) / 1000, sp);
        raf = requestAnimationFrame(loop); };
      raf = requestAnimationFrame(loop);
    }
    return () => cancelAnimationFrame(raf);
  }, [w, h, n]);
  return html`<canvas class=${cls} ref=${ref} aria-hidden="true"
    style=${{ width: w + 'px', height: h + 'px' }}/>`;
}

/* ---------------- the two status lines, verbatim ---------------- */
export const THINK = {
  map : 'Cognix is using his brain to deliver a Mindmap',
  plan: 'Cognix doing some advanced type shit, you have to wait lmao'
};
/* the loading state moves on while the one request is in flight, so the
   card says what is actually happening rather than just spinning */
const STAGES = {
  map: ['Reading your idea', 'Splitting it into six branches',
        'Writing three points per branch', 'Fitting it to the locked schema',
        'Laying the boxes out'],
  plan: ['Re-reading every box on the map', 'Sequencing the work into weeks',
         'Pricing the risk', 'Filling the locked schema', 'Tightening the wording']
};

/* ---------------- the overlay card ----------------
   The elapsed counter changes ten times a second. Inside a live region that
   is ten announcements a second, so the region is narrowed to the stage line
   (five changes in a whole call) and everything decorative is hidden from the
   accessibility tree. */
export function Thinking({ mode = 'map', hint }){
  const [tick, setTick] = useState(0);
  useEffect(() => { const id = setInterval(() => setTick(x => x + 1), 100);
    return () => clearInterval(id); }, [mode]);
  const st = STAGES[mode] || STAGES.map;
  const secs = tick / 10, i = Math.min(st.length - 1, Math.floor(secs / 3.4));
  return html`<div class="cog" role="group" aria-label=${THINK[mode]}>
    <div class="cogtop">
      <span class="cogmark" aria-hidden="true"><img src=${MARK} alt=""/></span>
      <span class="coghd">${THINK[mode]}</span>
    </div>
    <${Wave} w=${318} h=${62}/>
    <div class="cogfoot">
      <span class="cogst" key=${i} role="status">${st[i]}</span>
      <span class="cogsp"></span>
      <span class="cogel" aria-hidden="true">${secs.toFixed(1)}s</span>
    </div>
    <div class="cogbar" aria-hidden="true"><i></i></div>
    ${hint ? html`<div class="coghint">${hint}</div>` : null}
  </div>`;
}

/* the inline version: composer footer, and a pending row in the transcript */
export const MiniWave = () => html`<${Wave} w=${34} h=${13} n=${10} cls="miniwave"/>`;
