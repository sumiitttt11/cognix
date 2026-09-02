# Cognix

One shell, two agents. **Mind Maps** turns a sentence into a 25-box map — six
fixed branches, three points each — that you can then move, rename, restyle
and lock like a Figma file. **Plan** takes the same idea and writes the plan
behind it. Both are locked to a fixed output schema, so a map is the same
shape whether the gateway wrote it or the offline composer did.

It runs two ways, and the difference is one environment variable.

| | **local mode** | **cloud mode** |
| --- | --- | --- |
| Accounts | none | Supabase Auth: sign up, sign in, reset |
| No account yet | there is nothing to have | a guest: straight in, three free chats |
| Where the maps live | this browser's `localStorage` | Postgres, per account |
| Admin console | not reachable | `/app/admin/` |
| Turned on by | nothing | `COGNIX_SUPABASE_URL` + `COGNIX_SUPABASE_ANON_KEY` |

Local mode is the prototype behaviour and it is kept on purpose: it is a
genuinely nice way to run this on your own laptop, and it needs no account and
no database. Cloud mode is what gets deployed.

```bash
python serve.py
```

Then open <http://localhost:8778/app/>. The startup banner says which mode it
came up in and what is missing to reach the other one.

## What you need

Python 3.12 or newer, and nothing else. There is **no Node, no npm and no
build step** — React 18.3.1 and htm 3.1.1 are vendored under `app/vendor/`,
the app is plain ES modules, and the browser loads the same files you edit.
The test suite is the standard library too. A browser from 2021 or later (ES
modules + dynamic `import()`) is required; there is no transpiled fallback.

## Going to cloud mode

Two documents, in this order, both written to be followed rather than read:

1. **[`supabase/README.md`](supabase/README.md)** — make the project, run the
   four SQL files, point Auth at the app, copy the keys into `.env`. About
   twenty minutes, most of it waiting for the SQL editor.
2. **[`deploy/cloudrun.md`](deploy/cloudrun.md)** — five secrets in Secret
   Manager, one `gcloud builds submit`, and the URL back into both the service
   and Supabase. The build runs the test suite before it deploys anything.

The SQL is four files and the order matters: `schema.sql`, `functions.sql`,
`policies.sql`, `seed.sql`. All four are safe to run twice. If you would rather
paste once than four times:

```bash
python tools/sql.py setup.sql
```

Until those files have been run, the app says so: signing in works, and then
every page answers *this deployment has an account system but no database yet*
with the four filenames in it, rather than PostgREST's own `could not find the
table 'public.chats' in the schema cache`.

## Guests

A visitor with no account is not sent to a sign-in page. They land in the app,
their chats are kept in their own browser exactly as in local mode, and they get
a few model calls before it asks them to make an account. The countdown is in
the strip under the title bar and in the footer from the first second, so the
ceiling is never a surprise; when it arrives nothing is lost — the maps stay
open, and signing in offers to copy them across.

Three numbers bound it, and they are worth different amounts. Read them in this
order:

| | Where it is counted | Worth |
| --- | --- | --- |
| `GUEST_CHATS` | the page | what the visitor sees and feels; a page can be lied to |
| `GUEST_CALLS` | a cookie this server signs | the real one — survives a restart, cannot be edited, starts over if cookies are cleared |
| `GUEST_PER_IP` | memory, one hour | the backstop for that; per instance, forgotten on restart |

Together they bound casual use. None of them is a security boundary, which is
the point of saying so here rather than implying otherwise. `COGNIX_GUEST=0`
puts the sign-in page back in front of everything.

## Settings

Every one of these is read from the environment first, then from a `.env` file
next to `serve.py`, then from the default below. `NODERELS_*` works everywhere
`COGNIX_*` does, so an old shell alias or launch entry still starts the right
server. `PORT`, `HOST` and `K_SERVICE` are also read unprefixed, because those
are the names the platform sets.

Copy `.env.example` to `.env` to start. `.env` is git-ignored, refused by the
request handler, kept out of the container image, and redacted out of every log
line and every error body — but it is still a file with keys in it.

| Variable | Default | What it does |
| --- | --- | --- |
| `COGNIX_KEY` | *(none)* | gateway key; server-side only, never sent to a browser. An administrator can set this from the console instead |
| `COGNIX_BASE` | `https://api.justwoker.icu` | gateway origin; likewise settable from the console |
| `COGNIX_PORT` | `8778`, or `8080` on Cloud Run | also `python serve.py 8779` |
| `COGNIX_HOST` | `127.0.0.1`, or `0.0.0.0` on Cloud Run | see the warning below |
| `COGNIX_SUPABASE_URL` | *(none)* | `https://<project>.supabase.co` — this is the switch |
| `COGNIX_SUPABASE_ANON_KEY` | *(none)* | the publishable key. Stays on the server |
| `COGNIX_SUPABASE_SERVICE_KEY` | *(none)* | the secret key. Two uses only, both below |
| `COGNIX_SESSION_SECRET` | *(per process)* | signs the session cookie |
| `COGNIX_ADMIN_EMAILS` | *(none)* | promoted to administrator on first sign-in |
| `COGNIX_PUBLIC_URL` | *(none)* | the app's own address, for reset and invite mail |
| `COGNIX_TOKEN_CAP` | `400000` | tokens per account per month |
| `COGNIX_MAX_CHATS` | `400` | chats per account |
| `COGNIX_MAX_MSGS` | `400` | messages per chat |
| `COGNIX_GW_PER_MIN` | `12` | model calls a minute, per account |
| `COGNIX_LOGIN_TRIES` | `10` | sign-in attempts per IP… |
| `COGNIX_LOGIN_WINDOW` | `900` | …per this many seconds |
| `COGNIX_SESSION_DAYS` | `30` | how long a sign-in lasts |
| `COGNIX_SIGNUPS` | `1` | `0` closes the create-account form |
| `COGNIX_GUEST` | `1` | `0` puts the sign-in page back in front of the app |
| `COGNIX_GUEST_CHATS` | `3` | chats a visitor may start without an account |
| `COGNIX_GUEST_CALLS` | `9` | model calls per visitor, counted in a signed cookie |
| `COGNIX_GUEST_PER_IP` | `40` | …and per address per hour |
| `COGNIX_GUEST_DAYS` | `7` | how long one visitor's tally lasts |
| `COGNIX_TRUST_PROXY` | on if `K_SERVICE` | read the client IP from `x-forwarded-for` |
| `COGNIX_LOG_JSON` | on if `K_SERVICE` | one JSON object per log line |
| `COGNIX_ALLOW_OPEN` | `0` | the one deliberate foot-gun; see below |

Three of these refuse to be got wrong rather than going wrong quietly.
`SUPABASE_URL` without `SUPABASE_ANON_KEY` is a refusal to start. The secret
key in the anon line is a refusal to start, because that key bypasses every
policy in `supabase/policies.sql`. And in local mode, binding to anything but
loopback makes a process that holds an API key reachable from the network with
no accounts in front of it — `serve.py` refuses that too, unless
`COGNIX_ALLOW_OPEN=1` says you meant it.

### The gateway, from the console

`/app/admin/#/gateway` sets the gateway URL, the API key and which vendor model
each agent asks for, at runtime, with no code edit, no `.env` change and no
restart. It lives on the `app_settings` row as five columns — `gateway_base`,
`gateway_sealed`, `gateway_hint`, `gateway_updated_at` and `gateway_models` —
added by `supabase/schema.sql`, which is safe to run again on a project that
already has the rest.

Four things about it are worth knowing before you use it:

* **The environment still wins when the row says nothing**, per field. Save the
  URL and leave the key alone and you get the stored URL with `COGNIX_KEY`. Empty
  a field to go back to the environment for it. The screen prints which is in
  force and where it came from — `panel`, `env` or `mixed`.
* **The key is stored sealed**, encrypted under `COGNIX_SESSION_SECRET`, because
  `app_settings` is the one table a signed-out browser may read. A dump of this
  database is not a working key. That also means **`COGNIX_SESSION_SECRET` has to
  be set** before a key can be stored: without it each restart invents a new
  secret and could not read the key back. The server refuses rather than storing
  something it will not be able to open, and the screen says so first.
* **Nothing gives the key back.** What comes out of the API is
  `set · 51 chars · …2M8`. Every save writes an audit row with the masked form,
  never the value, and *Check it* — a free `GET /v1/models` with the key that
  would be used — answers with one sentence, not the gateway's own body. That
  sentence says whether both agents are in the list it got back, because a
  gateway can answer this check perfectly and still not serve what the app asks
  for, and the count on its own would not tell you.
* **Each agent can be pointed at whatever id the gateway serves.** Move to a
  gateway that spells a model differently, or serves another one altogether, and
  that is a row rather than a release: one box per agent, empty meaning "the id
  this build ships with", and the ids *Check it* got back offered as one-click
  choices under the agent that would use one. `gateway_models` is deliberately
  **not** sealed — a model id is not a credential, and this row is read as `anon`
  on a guest model call, where a value that has to be decrypted is a value that
  fails on it.

## URLs

| Path | What it is |
| --- | --- |
| `/app/` | the app |
| `/app/auth/` | sign in, sign up, reset — cloud mode only |
| `/app/admin/` | the administrator console — cloud mode, administrators only |
| `/app/selftest/` | the in-browser test page |
| `/` | redirects to `/app/` |
| `/api/*` | accounts, chats, usage, settings. Same-origin, CSRF-checked |
| `/gw/*` | the model proxy. Signed-in only, in cloud mode |
| `/gw/health` | key present? which agents? (never the key, never a vendor id) |
| `/healthz`, `/readyz` | for the platform. `/readyz` is 503 while anything is fatal |

## Tests

```bash
python -m unittest discover -s tests
```

**298 tests**, no network, no Node, nothing that spends money. Eight files:

| File | Tests | What it holds down |
| --- | --- | --- |
| `test_admin.py` | 76 | the console: the 403 for everybody else, the first-administrator bootstrap, every audit row, the two self-lockout refusals, the gateway screen — what it seals, what it never gives back, which model each agent then asks for, and what a call for one spends — and that only GoTrue's own admin endpoints ever see the service key |
| `test_api.py` | 74 | cloud mode end to end: two servers on ephemeral ports, a cookie jar, and a stub whose row filter *is* `policies.sql`. Proves a request can only reach the rows it owns |
| `test_session.py` | 37 | the session cookie — what signs it, what it refuses, which flags it goes out with. Pure functions, no socket |
| `test_server.py` | 35 | `serve.py` against a real socket: the proxy's refusals, the static rules, the headers |
| `test_deploy.py` | 32 | the Dockerfile, `.dockerignore`, `cloudbuild.yaml` and the SQL, read as documents: no key in the image, no RPC the database has never heard of, no table with RLS on and no policy, no gateway column stranded inside a `create table if not exists`, no deploy step that overtakes the tests, and no vendor model name anywhere a browser can reach |
| `test_static.py` | 26 | the tree: no key literal outside `.env`, every `import` resolves across all four pages, no orphan modules, no injection sink, no inline `style="`, four strict CSPs, production React |
| `test_wire.py` | 17 | what a failed Supabase reply becomes on the way out: a table that was never created reads as *run these four files*, and every real refusal keeps its own status |
| `test_htm.py` | 1 | htm's whitespace rule over every template, because it glued two words together twice |

The other 67 run in the browser, because the modules they cover (`util.js`,
`sanitize.js`, `tokens.js`, `model.js`) have to work in the engine and under
the policy the app actually runs under. Open `/app/selftest/`. It touches no
storage, and `window.__RESULTS__` holds the counts afterwards:

```js
__RESULTS__.fail === 0
```

## How it fits together

```
browser ── /app/*      static files, four pages, strict CSP, no inline anything
        ── /api/*      serve.py ──► Supabase  GoTrue + PostgREST, caller's token
        ── /gw/v1/*    serve.py ──► gateway   (holds the key)
```

The browser has no Supabase client: no SDK, no CDN, no key in a script tag. It
has two cookies it did not choose and a set of same-origin paths, and it never
learns that Supabase exists. That is deliberate — the anon key plus a browser
is a direct line to PostgREST, and then the policies are the *only* thing
between a visitor and every table. They are good policies. They are not meant
to be the only ones.

The proxy exists for two more reasons: the gateway answers the CORS preflight
with 403, so a browser could never send `x-api-key` to it directly; and a key
that reaches a browser is a key that has leaked. Because this process is the
only thing that can spend money, it is not a transparent relay — it proxies
three upstream paths and one method, allows two models, caps `max_tokens`,
refuses streaming, refuses a body over 512 KiB by its declared length before
reading it, and refuses a request the browser labelled cross-site. Its own
source, `.env`, `tests/` and anything hidden are not servable content.

It is also where the two agents get their names. The app asks for
`cognix-mind-v1` (maps) and `cognix-apex-v2` (plans); the mapping to the vendor
model each one stands for — `claude-opus-4-8-thinking` and
`claude-opus-5-thinking`, or whatever the console has pointed an agent at — is in
`server/config.py` and `app_settings.gateway_models`, and `serve.py` applies it in
both directions at the edge of the proxy: the name becomes the model on the way
up, and the model becomes the name again on the way back, in the reply body and
in an upstream error sentence alike. Anything else that could name a model does
the same — the settings row's `allowed_models` is turned back into the agents'
names before it reaches a page, and a `source` recorded by an older version is
turned into one as it comes out of storage, including an id nothing is pointed at
any more. So no page, no stored chat and no network tab says which model
answered, and neither does `/gw/health`, which reports the agents rather than a
translation of the ids in force.

Two places see the real name, and both are operator information rather than
product surface: the usage rows, which is what a bill has to be read against, and
the gateway screen in the console, which is behind three gates and a role read
from the caller's own `profiles` row and would be useless without them — an
administrator who cannot see which id an agent asks for cannot repoint it. Even
there no id is written down in the front end; the screen prints what the server
hands it. The vendor names are still accepted on the way in, because chats saved
by an earlier version recorded them and re-running one of those must work.

## Who can see what

Every `/api/*` call is made with the caller's own access token, so every read
and write goes through `supabase/policies.sql` as that person. The server has no
way to ask for a row on somebody else's behalf and does not have a mode where it
tries.

An administrator is somebody whose own `profiles` row says so. The role comes
from that row — cached for twenty seconds, so a demotion bites within twenty
seconds — and never from the cookie, whose copy of it dresses the interface and
is not trusted by `server/admin.py`. It is checked twice in two systems: this
server refuses the request, and the policies refuse the tables, because every
call carries the administrator's own token. A mistake in one is not enough.

What the role buys is the account list, the usage totals and the settings. The
audit log records who did each of those, and it is insert-and-select only in
`policies.sql` — nobody can edit or delete a row, including the administrator
who caused it.

It does not buy your chats. `messages` and `maps` are owner-only in
`policies.sql`, with no administrator policy at all, so no administrator read
reaches a message body or a map. What the console can list is the row *about* a
chat — its title, tab, model and message count — which is enough to moderate and
to find the account behind a bill, and is worth knowing about, because a title
is usually the first thing that was typed into the chat.
`tests/test_deploy.py` asserts that `messages` and `maps` have exactly their two
owner policies and no `is_admin` anywhere near them, so the property survives
somebody adding a helpful policy later.

The service key skips all of that, which is why it has exactly two uses:
GoTrue's own admin endpoints — invite, delete, password reset for another
account — and promoting the first address in `COGNIX_ADMIN_EMAILS` on its first
sign-in, because at that point there is no administrator to do it. It is never
used for a table read. Leave it unset and everything except those two things
still works.
