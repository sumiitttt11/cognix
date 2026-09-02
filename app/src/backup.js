/* =====================================================================
   the way out.

   Everything a user makes here lives in one localStorage blob, which the
   browser may clear without asking. The store already tells them to
   "export your map to keep it" when the quota runs out, so there has to
   be something that does it — from the Inspect tab in normal use, and
   from the crash screen when nothing else works.
   ===================================================================== */
import { KEYS } from './store.js';

/* raw text, not re-serialised state: a backup taken while the app is
   confused should still hold exactly what is on disk */
export function collect(){
  const out = {};
  Object.keys(KEYS).forEach(k => {
    try{
      const v = localStorage.getItem(KEYS[k]);
      if(v != null) out[KEYS[k]] = v;
    }catch(e){}
  });
  return out;
}

export function backupText(){
  return JSON.stringify({
    exported: new Date().toISOString(),
    app: 'Cognix',
    note: 'Raw localStorage blobs. Keep this file; it holds your maps.',
    storage: collect()
  }, null, 2);
}

/* returns true if the file left the building, false if the browser refused
   and the caller should offer the text some other way. Used for the raw
   localStorage backup below and for the whole-account export in cloud mode. */
export function download(name, body){
  try{
    const url = URL.createObjectURL(new Blob([body], { type:'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return true;
  }catch(e){
    /* no Blob/URL, or a download the browser blocked: a plain window with the
       text in it is still a way to get the data out */
    try{
      const w = window.open('', '_blank');
      if(!w) return false;
      w.document.title = name;
      const pre = w.document.createElement('pre');
      pre.textContent = body;
      w.document.body.appendChild(pre);
      return true;
    }catch(e2){ return false; }
  }
}

export function downloadBackup(){
  return download('cognix-backup-' + new Date().toISOString().slice(0, 10) + '.json',
                  backupText());
}

export function backupSize(){
  let n = 0;
  const got = collect();
  Object.keys(got).forEach(k => n += got[k].length);
  return n;
}
