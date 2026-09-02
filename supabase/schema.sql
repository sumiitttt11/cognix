-- Cognix, in Postgres. Tables only — functions.sql, policies.sql and seed.sql
-- follow, in that order.
--
--   1. schema.sql      tables, indexes, grants, row-level security switched on
--   2. functions.sql   is_admin(), the signup trigger, the RPCs the app calls
--   3. policies.sql    who may read and write which rows
--   4. seed.sql        the one app_settings row
--
-- Two rules shape all of it.
--
-- The first: row-level security is the boundary, not the server. Every read
-- and write the app makes for a signed-in person carries that person's own
-- access token, so a mistake in server/api.py cannot hand over somebody
-- else's chat — there is no query in this project that runs with more
-- authority than the person who asked for it. The service key never touches a
-- table except in the one-time promotion of the first administrator.
--
-- The second: RLS is switched on in this file, before a single policy exists.
-- A table with RLS on and no policy answers every request with nothing, so
-- the gap between running this file and running policies.sql is a closed door
-- rather than an open one.
--
-- Safe to run twice; every statement is `if not exists`.

create extension if not exists pgcrypto;             -- gen_random_uuid()


-- ------------------------------------------------------------------ profiles
-- One row per account, created by the trigger in functions.sql at the moment
-- GoTrue creates the login. Role and status live here, and the server re-reads
-- them on every request rather than trusting the session cookie: an
-- administrator demoted a minute ago still holds a cookie that says admin.
create table if not exists public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  email         text not null default '',
  display_name  text not null default '',
  role          text not null default 'user'
                     check (role in ('user', 'admin')),
  status        text not null default 'active'
                     check (status in ('active', 'suspended')),
  -- Tokens per calendar month. NULL means 'whatever COGNIX_TOKEN_CAP says',
  -- and 0 means no ceiling at all — which is how an administrator says 'let
  -- them work'. server/api.py reads it in exactly those three ways.
  token_cap     integer,
  notes         text not null default '',      -- support notes, admin-only
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  last_seen_at  timestamptz
);

-- --------------------------------------------------------------------- chats
-- One conversation: a mind map or a plan. The user_id column points at
-- profiles rather than at auth.users, and that is deliberate — PostgREST can
-- only embed across a foreign key it can see, and the admin user list asks for
-- `profiles(…, chats(count))` in one query instead of one query per person.
create table if not exists public.chats (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null default auth.uid()
                     references public.profiles (id) on delete cascade,
  -- The id the browser gave this chat before there was an account. Import
  -- matches on it, which is what makes pressing the button twice harmless.
  local_id      text,
  title         text not null default 'Untitled',
  tab           text not null default 'map' check (tab in ('map', 'plan')),
  model         text not null default '',
  -- Optimistic concurrency. A save carries the version it last saw and lands
  -- only if that is still the version in the row; the loser is told to reload
  -- rather than quietly overwriting the other tab's work.
  version       integer not null default 1,
  message_count integer not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);


-- ------------------------------------------------------------------ messages
-- Replaced as a set, never edited in place: replace_messages() in
-- functions.sql deletes and re-inserts inside one transaction, because a chat
-- half-written by two HTTP calls is a chat somebody has lost.
create table if not exists public.messages (
  id         bigserial primary key,
  chat_id    uuid not null references public.chats (id) on delete cascade,
  user_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  seq        integer not null,                -- position in the conversation
  role       text not null default 'assistant'
                  check (role in ('user', 'assistant', 'system')),
  kind       text not null default 'chat'
                  check (kind in ('chat', 'map', 'plan', 'note', 'error')),
  text       text not null default '',
  meta       jsonb not null default '{}'::jsonb,
  ts         bigint not null default 0,        -- the browser's clock, in ms
  created_at timestamptz not null default now(),
  unique (chat_id, seq)
);

-- ---------------------------------------------------------------------- maps
-- The canvas: nodes, edges and the styling that goes with them, one row per
-- chat. `chat_id` is unique because the app upserts on it — a save is
-- insert-or-replace, and there is no history to keep here.
create table if not exists public.maps (
  id         uuid primary key default gen_random_uuid(),
  chat_id    uuid not null unique references public.chats (id) on delete cascade,
  user_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  data       jsonb,                            -- the map itself
  style      jsonb,                            -- the customise panel's state
  version    integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);


-- -------------------------------------------------------------- usage_events
-- One row per model call. This is the table the monthly ceiling counts and the
-- admin charts read, and it is append-only on purpose: there is no update or
-- delete policy for anybody, because a person who could delete their own rows
-- could spend without limit.
create table if not exists public.usage_events (
  id                bigserial primary key,
  user_id           uuid not null default auth.uid()
                         references public.profiles (id) on delete cascade,
  kind              text not null default '',   -- 'map' or 'plan'
  model             text not null default '',
  prompt_tokens     integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens      integer not null default 0,
  ms                integer not null default 0, -- how long the call took
  ok                boolean not null default true,
  note              text not null default '',   -- why it failed, when it did
  created_at        timestamptz not null default now()
);

-- ----------------------------------------------------------------- audit_log
-- Everything an administrator did, one row each. Append-only for the same
-- reason as usage_events, and `actor` survives the actor: deleting an account
-- sets it to NULL rather than taking the log with it, and actor_email is kept
-- as text so the row still says who.
create table if not exists public.audit_log (
  id           bigserial primary key,
  actor        uuid references public.profiles (id) on delete set null,
  actor_email  text not null default '',
  action       text not null,                  -- 'user.change', 'user.delete', …
  target       uuid,                           -- the account acted on, if one
  target_email text,
  detail       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);


-- -------------------------------------------------------------- app_settings
-- Exactly one row, id = 1. The check constraint is what keeps it exactly one:
-- the app upserts on the primary key and there is nothing else to be.
--
-- Readable by anybody, including a browser that has not signed in, because the
-- sign-in page needs to know whether signups are open and whether there is a
-- maintenance notice to show.
--
-- Nothing in this table is read in the clear by anybody but this server, and
-- nothing in it reaches a browser except the four fields server/api.py names
-- one by one in _public_settings(). The gateway key is the only value here that
-- would be a secret, and it is not stored as one: `gateway_sealed` is
-- ciphertext under COGNIX_SESSION_SECRET, which lives in the environment and
-- never in Postgres. Somebody holding this whole row still cannot spend it.
create table if not exists public.app_settings (
  id                integer primary key default 1 check (id = 1),
  signups_open      boolean not null default true,
  maintenance       boolean not null default false,
  announcement      text not null default '',
  -- The agents the app may use, by the only names it has for them. The vendor
  -- model behind either one is in server/config.py, or in gateway_models below
  -- when an administrator has repointed it, and nowhere else; a row written
  -- before they were named holds a model id here instead, and the server turns
  -- that into the name on the way out.
  allowed_models    jsonb not null
                    default '["cognix-mind-v1", "cognix-apex-v2"]'::jsonb,
  default_token_cap integer not null default 400000,
  updated_by        uuid references public.profiles (id) on delete set null,
  updated_at        timestamptz not null default now()
);

-- The gateway the model calls go to, set from the admin console instead of from
-- the environment. Added as an alter rather than written into the create above,
-- because on any project that has already run this file the create is a no-op
-- and these columns would never appear.
--
--   gateway_base    the origin, e.g. https://api.example.com — not a secret
--   gateway_sealed  the API key, encrypt-then-MAC under the session secret
--   gateway_hint    'set · 51 chars · …2M8' — what the console prints, and all
--                   of the key anybody ever gets back out of the API
--   gateway_models  {"cognix-mind-v1": "some-vendor-id"} — which model each
--                   agent asks for, when this deployment's gateway spells it
--                   differently from the pair in server/config.py. Not sealed:
--                   a model id is not a credential, and this row is read as
--                   `anon` on a guest model call, where a value that has to be
--                   decrypted is a value that fails. An agent missing from here
--                   asks for the id the build ships with.
--
-- Empty string means "not configured here", which is how the server knows to
-- fall back to COGNIX_BASE / COGNIX_KEY. An empty object means the same for the
-- models: ask for what server/config.py says.
alter table public.app_settings
  add column if not exists gateway_base       text not null default '';
alter table public.app_settings
  add column if not exists gateway_sealed     text not null default '';
alter table public.app_settings
  add column if not exists gateway_hint       text not null default '';
alter table public.app_settings
  add column if not exists gateway_updated_at timestamptz;
alter table public.app_settings
  add column if not exists gateway_models     jsonb not null default '{}'::jsonb;

-- ------------------------------------------------------------------- indexes
-- Every one of these exists because a query in server/api.py or
-- server/admin.py orders or filters on exactly these columns.
create index if not exists profiles_role_idx       on public.profiles (role);
create index if not exists profiles_status_idx     on public.profiles (status);
create index if not exists profiles_created_idx    on public.profiles (created_at desc);
create index if not exists profiles_email_idx      on public.profiles (lower(email));

create index if not exists chats_user_idx     on public.chats (user_id, updated_at desc);
create index if not exists chats_updated_idx  on public.chats (updated_at desc);
-- Import dedupes on local_id before inserting; this is what makes that hold
-- even when two tabs press the button at the same moment.
create unique index if not exists chats_local_idx
  on public.chats (user_id, local_id) where local_id is not null;

create index if not exists messages_chat_idx on public.messages (chat_id, seq);
create index if not exists messages_user_idx on public.messages (user_id);

create index if not exists maps_user_idx on public.maps (user_id);

create index if not exists usage_user_idx  on public.usage_events (user_id, created_at desc);
create index if not exists usage_when_idx  on public.usage_events (created_at desc);

create index if not exists audit_when_idx   on public.audit_log (created_at desc);
create index if not exists audit_action_idx on public.audit_log (action);
create index if not exists audit_actor_idx  on public.audit_log (actor);

-- ------------------------------------------------------- row-level security
-- On for every table, and on before there is a policy to soften it. Until
-- policies.sql runs, these tables are readable by nobody but the owner of the
-- database — which is the right state for a half-finished install to be in.
alter table public.profiles     enable row level security;
alter table public.chats        enable row level security;
alter table public.messages     enable row level security;
alter table public.maps         enable row level security;
alter table public.usage_events enable row level security;
alter table public.audit_log    enable row level security;
alter table public.app_settings enable row level security;


-- -------------------------------------------------------------------- grants
-- The coarse gate. RLS decides which rows; these decide whether the role may
-- reach the table at all, and the two together are why a missing policy fails
-- closed. Supabase grants these to new tables by default — they are spelled
-- out anyway, so this schema also stands up on a self-hosted instance.
grant usage on schema public to anon, authenticated, service_role;

grant select, insert, update, delete
  on public.profiles, public.chats, public.messages, public.maps
  to authenticated;

-- Append-only from the app's side: no update, no delete, for anybody.
grant select, insert on public.usage_events to authenticated;
grant select, insert on public.audit_log    to authenticated;

-- The sign-in page reads this before anybody has signed in.
grant select                 on public.app_settings to anon, authenticated;
grant insert, update         on public.app_settings to authenticated;

grant usage, select on all sequences in schema public to authenticated, service_role;
grant all on all tables in schema public to service_role;

