/* =====================================================================
   Cognix branding.

   One module owns the mark so every surface points at the same file: the
   titlebar wordmark, the tray footer avatar, the document header, the
   empty-state card, the thinking overlay and the favicon. The PNG is the
   supplied logo with its transparent margin cropped off and centred in a
   square, so it stays optically the same size at 14px and at 44px.
   ===================================================================== */
import { html } from './h.js';

export const NAME = 'Cognix';
/* root-absolute on purpose: this module is imported by the app at /app/, by the
   sign-in page at /app/auth/ and by the console at /app/admin/, and a relative
   path would point at three different files. */
export const MARK = '/app/assets/cognix-mark-256.png';

/* alt="" by default: next to the wordmark the image is decoration, and a
   screen reader reading "Cognix Cognix" is worse than silence */
export const Mark = ({ cls, size, alt }) => html`<img class=${cls} src=${MARK}
  alt=${alt || ''} width=${size || null} height=${size || null}
  style=${size ? { width: size + 'px', height: size + 'px' } : null}/>`;

/* the wordmark, used once — top left of the window */
export const Brand = () => html`<div class="brand" title=${NAME}>
  <${Mark}/><b>${NAME}</b></div>`;
