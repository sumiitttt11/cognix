/* =====================================================================
   Cognix gateway.

   Two calls, two agents, both pinned to a schema:

     mind map  -> Cognix mind v1, forced `map`  tool
     plan      -> Cognix apex v2, forced `plan` tool

   Those two names are what this page asks for and all it is ever told.
   serve.py turns each into the vendor model it stands for, on the far side
   of the proxy, so which model answers is not in this file, not in the
   network tab, and not in anything a browser stores.

   Forced tool use is what "locked output structure" means here: the model
   cannot answer in prose, it can only fill the schema, so there is no
   parsing and no ```json fence to strip. Prompts are deliberately terse —
   note though that this gateway adds a fixed ~6.8k input tokens to every
   request regardless of prompt size, so only the small remainder is ours
   to minimise.

   The browser never talks to the gateway directly: it answers the CORS
   preflight with 403, so any request carrying x-api-key is blocked before
   it leaves. `serve.py` proxies same-origin /gw/* instead — which also
   keeps the API key out of the page.
   ===================================================================== */
export const CFG = {
  base: '/gw',
  mapModel : 'cognix-mind-v1',
  planModel: 'cognix-apex-v2'
};

const S = (max, d) => ({ type:'string', description: d ? d + ', max ' + max + ' chars' : 'max ' + max + ' chars' });

/* ---------------- the map schema: six fixed keys, three points each ----------------
   Fixed keys rather than an array of branch objects, so order and
   completeness are guaranteed by the schema instead of by instruction. */
const three = label => ({
  type:'array', minItems:3, maxItems:3, description: label,
  items:{ type:'string', description:'max 58 chars' }
});
const MAP_TOOL = {
  name:'map',
  description:'Emit the mind map.',
  input_schema:{
    type:'object',
    properties:{
      title   : S(38, 'Name of the idea'),
      problem : three('Problem Statement'),
      solution: three('Solution'),
      audience: three('Target Audience'),
      model   : three('Business Model'),
      market  : three('Market Opportunity'),
      exec    : three('Execution Plan')
    },
    required:['title','problem','solution','audience','model','market','exec']
  }
};
const MAP_SYS = 'Mind-map writer. Call `map` exactly once, nothing else. '
  + 'Six branches, three points each, in the schema order. Every point must be concrete '
  + 'and specific to THIS idea — no filler, no restating the idea. Obey the length caps.';

/* ---------------- the plan schema ---------------- */
const PLAN_TOOL = {
  name:'plan',
  description:'Emit the build plan.',
  input_schema:{
    type:'object',
    properties:{
      summary : S(240, 'One paragraph'),
      sections:{ type:'array', minItems:4, maxItems:5, items:{ type:'object',
        properties:{ h: S(34, 'Heading'),
          b:{ type:'array', minItems:2, maxItems:4, items: S(120) } },
        required:['h','b'] } },
      weeks   :{ type:'array', minItems:3, maxItems:5, items:{ type:'object',
        properties:{ w: S(12, 'Week range'), t: S(90, 'What ships') },
        required:['w','t'] } },
      risks   :{ type:'array', minItems:2, maxItems:3, items:{ type:'object',
        properties:{ r: S(80, 'Risk'), m: S(90, 'Mitigation') },
        required:['r','m'] } },
      next    :{ type:'array', minItems:3, maxItems:3, items: S(90, 'Immediate action') }
    },
    required:['summary','sections','weeks','risks','next']
  }
};
const PLAN_SYS = 'Turn the mind map into a build plan. Call `plan` exactly once. '
  + "Ground every line in the map's own points; add sequencing, numbers and risk the map "
  + 'only implies. No filler, no restating headings.';

/* ---------------- transport ----------------
   Everything here is about the request failing, because over a home
   connection it will: the gateway is a third party behind Cloudflare, the
   thinking models take tens of seconds, and a laptop lid closing mid-call
   looks exactly like a hang. So: a hard timeout (fetch has none of its own —
   without this a dropped connection leaves the veil up forever), a couple of
   retries on the statuses that mean "try again", and one plain sentence for
   the user while the raw body goes to the console for us. */
const TIMEOUT = 120000;                 // longer than any answer we have seen
const TRIES   = 3;
const AGAIN   = [408, 409, 425, 429, 500, 502, 503, 504, 522, 524];

/* the key lives on the server, but an upstream error body can quote it back */
export const redact = s => String(s == null ? '' : s)
  .replace(/sk-[A-Za-z0-9_-]{6,}/g, 'sk-…redacted');

/* Two different things can refuse a call now, and only one of them is the
   gateway. Our own server checks first — a session, an account that is not
   suspended, a hand that is not hammering the button, room under the monthly
   ceiling — and when it refuses it answers {"error": "one sentence"} already
   written for a person. The upstream's `error` is an object, not a string, so
   that is the whole test. Getting this wrong is how a user who has run out of
   their own allowance is told the gateway is out of credit. */
function ours(body){
  let o = null;
  try{ o = JSON.parse(body); }catch(e){ return ''; }
  if(!o || typeof o.error !== 'string' || !o.error.trim()) return '';
  return redact(o.error).slice(0, 240);
}

/* what the user reads. The status is kept on the error so the caller can
   tell a dead proxy from a refused request. */
function reason(status, body){
  const mine = ours(body);
  if(mine) return mine;
  const b = String(body || '').toLowerCase();
  if(status === 401 || (status === 403 && b.indexOf('key') >= 0))
    return 'the gateway rejected our key — check COGNIX_KEY where serve.py runs';
  if(status === 402 || b.indexOf('额度') >= 0 || b.indexOf('credit') >= 0
     || b.indexOf('quota') >= 0 || b.indexOf('billing') >= 0)
    return 'the gateway account is out of credit';
  if(status === 429) return 'the gateway is rate-limiting us — try again in a moment';
  if(status === 413) return 'that idea is too long for one request — shorten it';
  if(status >= 500)  return 'the gateway is having trouble (' + status + ') — try again';
  if(status === 400) return 'the gateway refused the request (400)';
  return 'the gateway answered ' + status;
}
const fail = (msg, status, raw) => {
  const e = new Error(msg);
  e.status = status || 0;
  if(raw) e.raw = redact(raw).slice(0, 400);
  return e;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* the caller's signal (Stop button) and our own timeout, as one signal */
function deadline(outer, ms){
  const ac = new AbortController();
  const st = { late: false };
  const t = setTimeout(() => { st.late = true; ac.abort(); }, ms);
  const relay = () => ac.abort();
  if(outer){
    if(outer.aborted) ac.abort();
    else outer.addEventListener('abort', relay, { once: true });
  }
  st.signal = ac.signal;
  st.off = () => { clearTimeout(t); if(outer) outer.removeEventListener('abort', relay); };
  return st;
}

async function call(model, system, tool, user, maxTokens, signal){
  const body = JSON.stringify({
    model, max_tokens: maxTokens, system,
    tools:[tool], tool_choice:{ type:'tool', name: tool.name },
    messages:[{ role:'user', content: user }]
  });
  let last = null;
  for(let attempt = 1; attempt <= TRIES; attempt++){
    if(signal && signal.aborted) throw fail('cancelled', 0);
    const d = deadline(signal, TIMEOUT);
    let r, txt;
    try {
      r = await fetch(CFG.base + '/v1/messages', {
        method:'POST', signal: d.signal, cache:'no-store',
        headers:{ 'content-type':'application/json' },
        body
      });
      txt = await r.text();
    } catch(e){
      if(d.late) last = fail('the gateway did not answer within ' + (TIMEOUT / 1000) + 's', 0);
      else if(signal && signal.aborted) throw fail('cancelled', 0);
      else last = fail('no route to the gateway — start the app with `python serve.py`', 0);
      if(attempt < TRIES){ await sleep(700 * attempt); continue; }
      throw last;
    } finally { d.off(); }

    if(r.status === 404 || r.status === 501)
      throw fail('the /gw proxy is not running — start the app with `python serve.py`', r.status);
    if(!r.ok){
      /* the body is the only place the real cause is written, and it is the
         one thing we must not put on screen verbatim */
      if(typeof console !== 'undefined' && console.error)
        console.error('[cognix] gateway ' + r.status + ' ' + model + ' — ' + redact(txt).slice(0, 600));
      const e = fail(reason(r.status, txt), r.status, txt);
      /* A 402 written by our own server is an allowance running out — a guest's
         free trial, or an account's month — not the gateway's bill. Whoever is
         showing that number wants to know; the sentence is already on the error
         either way, so nothing here depends on the listener existing. */
      if(r.status === 402 && ours(txt)){
        try{ window.dispatchEvent(new CustomEvent('cognix:capped')); }catch(e2){}
      }
      if(AGAIN.indexOf(r.status) >= 0 && attempt < TRIES){
        last = e;
        const ra = parseFloat(r.headers.get('retry-after') || '0');
        await sleep(Math.min(8000, (ra > 0 ? ra * 1000 : 700 * Math.pow(2, attempt))));
        continue;
      }
      throw e;
    }

    let j;
    try{ j = JSON.parse(txt); }catch(e){ throw fail('the gateway sent something that is not JSON', r.status, txt); }
    const use = (j.content || []).find(c => c && c.type === 'tool_use' && c.name === tool.name);
    if(!use || !use.input || typeof use.input !== 'object' || Array.isArray(use.input)){
      /* a truncated answer is the usual cause and retrying it is worth one go */
      const e = fail(j.stop_reason === 'max_tokens'
        ? 'the answer was cut off before the schema was filled'
        : 'the model did not fill the schema (stop: ' + String(j.stop_reason) + ')', r.status);
      if(attempt < TRIES){ last = e; await sleep(500); continue; }
      throw e;
    }
    /* a model call spends somebody's monthly allowance in cloud mode, so
       whoever is showing that number is told it moved */
    try{ window.dispatchEvent(new CustomEvent('cognix:spent')); }catch(e){}
    return { data: use.input, usage: j.usage || {}, model, tries: attempt };
  }
  throw last || fail('the gateway could not be reached', 0);
}

/* one task, one shape: an idea in, six branches out.
   `notes` carries an edit request on a regenerate ("harsher on pricing"). */
export function genMap(idea, notes, signal){
  const user = notes ? idea + '\nRevise: ' + notes : idea;
  return call(CFG.mapModel, MAP_SYS, MAP_TOOL, user, 1500, signal);
}

/* the map is sent as compact lines, not JSON — fewer tokens, same content */
export function genPlan(map, signal){
  const by = k => map.nodes.filter(n => n.branch === k && n.kind === 'leaf').map(n => n.text);
  const lines = ['problem','solution','audience','model','market','exec']
    .map(k => k + ': ' + by(k).join(' | ')).join('\n');
  const user = map.title + '\n' + lines;
  return call(CFG.planModel, PLAN_SYS, PLAN_TOOL, user, 2200, signal);
}

/* What either agent is called on screen, and the only names this app has for
   them. There is no entry for any vendor model id, and that is the point: the
   two ids exist on the far side of the proxy and nowhere in anything a browser
   is handed. A map saved by an earlier version recorded one in `source`, and
   sanitize.js turns it into the name below on the way out of storage — only one
   agent has ever drawn a map, so nothing has to be looked up to know which. */
export const MODEL_LABEL = {
  [CFG.mapModel] : 'Cognix mind v1',
  [CFG.planModel]: 'Cognix apex v2'
};
