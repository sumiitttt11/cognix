/* =====================================================================
   the data client — /api/data/*, one function per endpoint.

   Two things here are not obvious from the names.

   `write()` sends the version the browser last saw. The server compares it
   inside the UPDATE, so two tabs saving the same chat cannot silently
   overwrite each other: the second one gets a 409 with the server's version
   on it, and persist.js turns that into a reload rather than a loss.

   `beacon()` exists for the moment a tab is closing. A normal fetch is
   cancelled with the page; keepalive survives it, at the cost of a 64 KB
   ceiling the browser enforces. So it is used when the snapshot is small
   enough to fit and skipped when it is not — a big map has already been
   saved by the ordinary debounce, and pretending a beacon carried it would
   be worse than knowing it did not.
   ===================================================================== */
import { get, post, put, del, cookie, CSRF_COOKIE, CSRF_HEADER } from './session.js';

const chat = id => '/api/data/chats/' + encodeURIComponent(id);

/* a first paint needs one round trip, not one per chat */
export const bootstrap = () => get('/api/data/bootstrap', { timeout: 30000 });
export const list = () => get('/api/data/chats');
export const read = id => get(chat(id), { timeout: 30000 });
export const create = snap => post('/api/data/chats', snap, { timeout: 45000 });
export const write = (id, snap) => put(chat(id), snap, { timeout: 45000 });
export const remove = id => del(chat(id));
export const dump = () => get('/api/data/export', { timeout: 120000 });

/* the localStorage backup, offered once. The server answers with how many
   went in and how many are left, because a hundred chats is more than one
   request should carry; the caller loops while `remaining` is not zero. */
export const upload = chats =>
  post('/api/data/import', { chats }, { timeout: 120000 });

export const BEACON_MAX = 60000;

export function beacon(id, snap){
  let body;
  try{ body = JSON.stringify(snap); }catch(e){ return false; }
  if(body.length > BEACON_MAX) return false;
  const head = { 'content-type': 'application/json' };
  const tok = cookie(CSRF_COOKIE);
  if(!tok) return false;                    // a write without it is a 403
  head[CSRF_HEADER] = tok;
  try{
    fetch(chat(id), { method: 'PUT', headers: head, body,
      credentials: 'same-origin', cache: 'no-store', keepalive: true });
    return true;
  }catch(e){ return false; }
}
