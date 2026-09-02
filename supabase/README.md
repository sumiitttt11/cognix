# Connecting Supabase

Four files, in order, then five lines in `.env`. Twenty minutes, most of it
waiting for a project to finish creating itself.

Until you do this the app runs in **local mode**: no accounts, maps live in the
browser's own storage, and `/api/data/*` says so plainly instead of
half-working. Nothing below changes that behaviour — it adds the other mode.

---

## 1. Make the project

1. [supabase.com/dashboard](https://supabase.com/dashboard) → **New project**.
2. Pick a region near the people who will use it. Save the database password
   somewhere; you will not need it for this app, but you will want it later.
3. Wait for it to say it is ready.

## 2. Run the SQL

**SQL Editor** → **New query**. Paste each file whole, run it, wait for
*Success*, then move to the next. The order matters — the policies are written
in terms of a function that file 2 creates.

| # | File | What it does |
|---|------|--------------|
| 1 | `schema.sql` | Tables, indexes, grants, row-level security switched **on** |
| 2 | `functions.sql` | `is_admin()`, the signup trigger, the RPCs the app calls |
| 3 | `policies.sql` | Who may read and write which rows |
| 4 | `seed.sql` | The one settings row, a profile for anybody who signed up already, plus two checks |

The last file prints two tables. Read them:

* every table should say `rls_on = true` and at least one policy;
* every function name should say `present = true`.

All of these files are safe to run again. If you edit one, re-run it.

If four pastes is three too many, this writes them in order as one script:

```bash
python tools/sql.py setup.sql
```

Nothing is generated — the files are written out exactly as they are on disk, so
there is no second copy of the schema to fall behind. (Give it a filename rather
than redirecting with `>`; a Windows console redirect writes cp1252, and the em
dashes in the comments come out as bytes Postgres will not read.)

**If people signed up before you got here**, they have a login and no profile
row: the trigger that makes one arrives in file 2, after their signup. `seed.sql`
fills those in, so run it even on a project that already has accounts. (The
server also repairs a missing profile the next time that person signs in — this
is just the half that does not wait for them.)

## 3. Point Auth at the app

**Authentication → URL Configuration**:

* **Site URL** — where the app lives. `https://cognix-xxxx.run.app` once it is
  on Cloud Run; `http://localhost:8778` while you are on your laptop.
* **Redirect URLs** — add both, one per line:

```
http://localhost:8778/app/auth/
https://YOUR-APP-URL/app/auth/
```

That path is the one page in this app that knows what to do with the tokens
GoTrue puts in a link. Without it, a confirmation link lands on a blank page.

**Authentication → Providers → Email**: leave **Confirm email** on. The app
expects it — a signup with confirmations on returns no session, and the app
says "check your inbox" rather than pretending to sign you in.

**Authentication → Policies** (or *Auth → Settings*): set the minimum password
length to **10**, which is what the server enforces anyway. Matching them means
the two never disagree in front of somebody who is trying to sign up.

### Mail, before you invite anybody

Supabase's built-in mail is for development: a handful of messages an hour,
from an address that is not yours. **Authentication → Emails → SMTP Settings**,
and put a real sender in — Resend, Postmark, SES, whatever you already have.
Until you do, invitations and reset links will quietly stop arriving after the
first few, and the admin panel's **Confirm** button is there for exactly that
morning.

## 4. Copy the keys

**Project Settings → API Keys**. Three things to copy:

| In the dashboard | Into `.env` as | Who ever sees it |
|---|---|---|
| Project URL (under *Data API*) | `COGNIX_SUPABASE_URL` | The server |
| Publishable key — `sb_publishable_…` | `COGNIX_SUPABASE_ANON_KEY` | The server |
| Secret key — `sb_secret_…`, behind *reveal* | `COGNIX_SUPABASE_SERVICE_KEY` | The server, twice |

Older projects show a **Legacy API keys** tab instead, with `anon` `public` and
`service_role` `secret` — both JWTs beginning `eyJ`. Either pair works: the
server sends whichever it is given in both the `apikey` and `authorization`
headers, and Supabase accepts both shapes in both places. Do not mix a
publishable key with a legacy `service_role` one; take both from the same tab.

Both keys stay on the server. This app has no Supabase client in the browser —
no SDK, no CDN, no key in a `<script>` tag — because the browser talks only to
this server's own `/api/*`, over a cookie it cannot read. That is why the strict
Content-Security-Policy in `app/index.html` has no exceptions in it.

The secret key bypasses every policy in `policies.sql`. It is used in exactly
two places: GoTrue's admin endpoints (invite, delete, confirm), and the one-time
promotion of the first administrator. If you paste it into the anon line by
mistake the server refuses to start and says so, because that swap is otherwise
silent — everything would keep working, with nothing bounded by a policy any
more. If you are deploying to Cloud Run, put it in Secret Manager rather than in
an environment variable — `deploy/cloudrun.md` has the command.

## 5. Fill in `.env`

```
COGNIX_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
COGNIX_SUPABASE_ANON_KEY=sb_publishable_...
COGNIX_SUPABASE_SERVICE_KEY=sb_secret_...
COGNIX_SESSION_SECRET=paste-a-long-random-string
COGNIX_ADMIN_EMAILS=you@example.com
COGNIX_PUBLIC_URL=http://localhost:8778
```

`SESSION_SECRET` signs the session cookie. Make one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Without it the server invents one per process, which is fine on a laptop and
wrong anywhere with more than one instance: two instances would reject each
other's cookies and people would be signed out at random. The server refuses to
start without one when it is answering on anything but localhost.

`PUBLIC_URL` is the app's own address. Password-reset and invitation mail is
built from it, so if it is wrong the links point at Supabase's default page
instead of at this app.

## 6. Start it and look

```bash
python serve.py
```

The banner says `mode: cloud`. Then:

* `http://localhost:8778/readyz` — `{"ok": true}`. If it lists problems, they
  are sentences, and each one names the thing to fix.
* `http://localhost:8778/app/` — a sign-in page rather than the app.
* Sign up with the address you put in `ADMIN_EMAILS`, confirm the mail, sign in.
* `http://localhost:8778/app/admin/` — the panel. Opening it is what promotes
  your account the first time, and it writes a line in the audit log saying so.
* Make a map, reload the page, and it is still there. That is Postgres now, not
  the browser.

If you already had maps in that browser from local mode, the app offers to
upload them once, matched on the id they already had, so pressing the button
twice does not double anything.

---

## When something does not work

| What you see | What it is |
|---|---|
| `SUPABASE_URL is set but SUPABASE_ANON_KEY is not` at startup | One of the two is missing. Both, or neither. |
| *Cognix is not finished setting up*, naming four files | None of the SQL has been run against this project. Step 2. It is the same message whether it is all four files or one. |
| Sign-in works, chats do not load | `policies.sql` did not run, or ran before `functions.sql`. Re-run both, in order. |
| `Could not read the overview` in the panel | The RPCs are missing — `functions.sql`. Check the second table `seed.sql` prints. |
| The panel says *That is an administrator page* | Your row is not `role = 'admin'`. Either your address is not in `COGNIX_ADMIN_EMAILS`, or you signed up with a different one. |
| Invite and Delete fail, everything else works | `SUPABASE_SERVICE_KEY` is not set. Those two go to GoTrue's admin API, which the anon key cannot open. |
| Confirmation mail never arrives | Supabase's development mailer, rate-limited. Set up SMTP, and use **Confirm** in the panel for the person who is stuck. |
| Signed out at random after deploying | `SESSION_SECRET` differs between instances, or is not set. |
| A confirmation link opens a blank page | The redirect URL is not on the allow-list in step 3. It has to end in `/app/auth/`. |

## What is stored, and where

| Table | What is in it | Who can read it |
|---|---|---|
| `profiles` | Address, name, role, status, monthly ceiling, support notes | You, and an administrator |
| `chats` | Title, tab, model, version, message count | You, and an administrator |
| `messages` | Every turn of the conversation | **You only** |
| `maps` | The map and its styling | **You only** |
| `usage_events` | One row per model call: tokens, duration, whether it worked | You, and an administrator |
| `audit_log` | Every administrative action, and who took it | Administrators |
| `app_settings` | Signups open, maintenance notice, models offered | Anybody; only an administrator writes it |

An administrator can see that you have forty maps and when you last touched
one. They cannot read them. That is not a promise about how the interface is
built — there is no policy in `policies.sql` that would let the query succeed.
