# Cognix on Google Cloud Run

Ten commands, in order. Copy them one at a time; each one prints something
you can check before the next.

You need: a Google Cloud project with billing on, the `gcloud` CLI signed in
(`gcloud auth login`), and — if you want accounts — a Supabase project set up
first. `supabase/README.md` is that half, and it takes about twenty minutes.

Cloud Run is a good fit for this app for one reason: there is nothing to keep
on disk. The session is a signed cookie, everything else is in Supabase, so an
instance can be created and destroyed between two requests and nobody notices.

---

## 0. Names, once

```bash
export PROJECT=your-project-id
export REGION=asia-south1          # pick the one nearest your users
export SERVICE=cognix
gcloud config set project "$PROJECT"
```

`gcloud run regions list` prints them all. Put it near the *people*, not near
Supabase — every model call is far slower than a database round trip.

## 1. Turn on the four APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

## 2. Somewhere to put the image

```bash
gcloud artifacts repositories create cognix \
  --repository-format=docker --location="$REGION" \
  --description="Cognix container images"
```

## 3. The secrets

Five values, five secrets. Four come out of Supabase (`supabase/README.md`
step 4); the session secret you make here. Never paste any of them into a
command that ends up in your shell history with `--data`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))" \
  | gcloud secrets create cognix-session-secret --data-file=-
```

Then the four from Supabase and the gateway. `read -s` keeps them off the
screen and out of history:

```bash
for s in cognix-gateway-key cognix-supabase-url \
         cognix-supabase-anon-key cognix-supabase-service-key; do
  read -rsp "$s: " v && echo
  printf '%s' "$v" | gcloud secrets create "$s" --data-file=-
done
unset v
```

`printf` rather than `echo`: a trailing newline inside a JWT is a token that
does not work, and the error it produces says nothing about a newline.

To change one later — a rotated key, a new project:

```bash
printf '%s' "NEW-VALUE" | gcloud secrets versions add cognix-gateway-key --data-file=-
```

`:latest` in the deploy means the next request picks it up on the next
instance. Restart the service to make that *now*: step 8.

## 4. Let the service read them

Cloud Run runs as a service account, and it can read nothing by default.

```bash
export SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

for s in cognix-gateway-key cognix-supabase-url cognix-supabase-anon-key \
         cognix-supabase-service-key cognix-session-secret; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done
```

That is five grants of one role on one secret each, which is the whole point
of doing it this way: the service account can read exactly these five values
and nothing else in the project.

## 5. Deploy

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions="_REGION=$REGION,_SERVICE=$SERVICE,_ADMIN_EMAILS=you@example.com"
```

The build runs the test suite first and stops if anything fails. Then it
builds the image, pushes it, and deploys.

`_PUBLIC_URL` is left out on purpose: the first deploy is what creates the URL,
so nobody knows it yet. Step 6 fills it in.

## 6. The URL, back into the service

```bash
export URL="$(gcloud run services describe "$SERVICE" --region="$REGION" \
  --format='value(status.url)')"
echo "$URL"

gcloud run services update "$SERVICE" --region="$REGION" \
  --update-env-vars="COGNIX_PUBLIC_URL=$URL"
```

Then paste that URL into Supabase in two places — **Authentication → URL
Configuration**:

* **Site URL**: `$URL`
* **Redirect URLs**: `$URL/app/auth/`

Mail that GoTrue sends is built from those. Skip this and confirmation links
land on Supabase's own page instead of the app.

From now on the deploy command carries it, so redeploys keep it:

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions="_REGION=$REGION,_SERVICE=$SERVICE,_PUBLIC_URL=$URL,_ADMIN_EMAILS=you@example.com"
```

## 7. Check it

```bash
curl -s "$URL/readyz"          # {"ok": true, "mode": "cloud", ...}
curl -s "$URL/gw/health"       # key present? which models? never the key
```

`readyz` answers from configuration alone — it never calls Supabase, so it
cannot fail because Supabase was slow. If `ok` is `false`, `problems` is a
list of sentences and each one names the thing to fix.

Then open `$URL/app/` in a browser. You should get a sign-in page, not the
app. Sign up with the address in `_ADMIN_EMAILS`, confirm the mail, sign in,
and open `$URL/app/admin/` — opening it is what promotes your account the
first time, and it writes a line in the audit log saying so.

## 8. Restarting, rolling back, watching

A restart (after changing a secret's value):

```bash
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-env-vars="COGNIX_RESTARTED_AT=$(date -u +%FT%TZ)"
```

Cloud Run has no restart button; a new revision is the restart. Setting a
harmless variable is the least surprising way to ask for one.

Back to the previous revision:

```bash
gcloud run revisions list --service="$SERVICE" --region="$REGION" --limit=5
gcloud run services update-traffic "$SERVICE" --region="$REGION" \
  --to-revisions=REVISION-NAME=100
```

Logs, as they happen:

```bash
gcloud beta run services logs tail "$SERVICE" --region="$REGION"
```

`COGNIX_LOG_JSON=1` is set by the deploy, so every line is structured and
Cloud Logging can be queried by field. No line contains a key: everything on
its way to a log goes through `config.redact` first.

---

## What the deploy decided for you

| Flag | Why |
| --- | --- |
| `--allow-unauthenticated` | The app does its own auth. IAM in front of it would mean a Google account per user. |
| `--timeout=300` | A map is one model call, and slow. 300s is Cloud Run's own default; it can go to 3600, but a request still waiting after five minutes is a request that failed. |
| `--concurrency=40` | One threaded Python process. Model calls are I/O, so threads are mostly waiting, but 40 is where a single CPU stops being able to answer static files promptly. |
| `--memory=512Mi` | Nothing here loads a model or a framework — the floor is the interpreter. 512Mi is headroom for concurrent request bodies (512 KiB each, refused above that) rather than a measured need. |
| `--min-instances=0` | Costs nothing when nobody is using it, and the cold start is interpreter startup plus a file read — there is no dependency tree to import. |
| `--max-instances=4` | A ceiling on both the bill and the number of instances that can spend the gateway key at once. Raise it when you have users. |
| `--cpu-boost` | More CPU during startup, which is the only part of this that is CPU-bound. |

## The filesystem

Cloud Run gives the container a writable filesystem that lives in memory, so
anything written to it is charged against `--memory` and is gone when the
instance is. This app writes nothing: no uploads, no cache, no log file, no
session store. The one thing that would have is Python's bytecode cache, and
`PYTHONDONTWRITEBYTECODE=1` in the Dockerfile turns it off.

That is worth knowing mainly as a thing you do not have to plan for. There is
no volume to attach and no disk to size.

## A custom domain

```bash
gcloud beta run domain-mappings create --service="$SERVICE" \
  --domain=cognix.example.com --region="$REGION"
```

Then update `COGNIX_PUBLIC_URL` and the two Supabase URL settings to the new
name. Certificates are issued and renewed by Google; there is nothing to do.

## When something does not work

| What you see | What it is |
| --- | --- |
| Build fails at the `tests` step | The suite is red. Read the failure — it is the same one `python -m unittest discover -s tests` gives on your laptop. |
| `PERMISSION_DENIED` on a secret at deploy | Step 4 was skipped, or the project number in `$SA` is wrong. `gcloud run services describe` names the service account it is actually using. |
| Container fails to start, logs say *Refusing to start* | A fatal configuration problem, named in the line above it. Usually `SESSION_SECRET` missing or `SUPABASE_URL` set without `SUPABASE_ANON_KEY`. |
| The revision is healthy but `/app/` shows local mode | The Supabase secrets are not reaching it. `gcloud run services describe "$SERVICE" --region="$REGION"` lists which are attached. |
| Everybody is signed out after a deploy | `COGNIX_SESSION_SECRET` changed. Sessions are signed with it; a new value invalidates every cookie. |
| Model calls stopped, and the console says a stored key cannot be read | `COGNIX_SESSION_SECRET` changed, and the gateway key saved at `/app/admin/#/gateway` is sealed under the old one. Paste the key in again, or forget it and let `COGNIX_KEY` answer. |
| The gateway needs a new URL or key, now | `/app/admin/#/gateway`, as an administrator. It writes the settings row, takes effect within a minute and needs no redeploy — the environment stays as the fallback. |
| Confirmation mail points at `supabase.co` | Step 6, second half. |
| 429s from the app under load | `GW_PER_MIN` (12 model calls a minute per account) or `LOGIN_TRIES` (10 per IP per 15 minutes). Both are environment variables. |
