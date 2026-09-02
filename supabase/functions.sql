-- Cognix — the functions the app actually calls, and the triggers that keep
-- the tables honest. Run this after schema.sql and before policies.sql: the
-- policies are written in terms of is_admin(), which is defined here.
--
-- Everything is `create or replace`, so this file can be run again after an
-- edit without dropping anything.
--
-- Three kinds of thing live here:
--
--   * two small predicates, is_admin() and acting_role(), which the policies
--     and the guard triggers are written in terms of;
--   * triggers: the profile row that appears when a login does, updated_at,
--     and the two guards that stop a signed-in browser editing columns that
--     are not its business;
--   * the RPCs. Each exists because the alternative was pulling a month of
--     rows across the wire to add them up in Python, or writing a chat over
--     two HTTP calls and losing it if the second failed.
--
-- The admin_* functions are SECURITY DEFINER and check is_admin() on their
-- first line. They return counts and sums, never the contents of anybody's
-- map — an administrator can see that somebody has forty maps and when they
-- last touched one, which is what support needs, and no more than that.


-- ---------------------------------------------------------------- predicates
-- Which role the current request arrived as: 'authenticated' for a signed-in
-- browser, 'anon' for one that is not, 'service_role' for this server's own
-- admin calls, and '' for anything that is not a PostgREST request at all —
-- GoTrue's own connection, a migration, the SQL editor.
--
-- The guard triggers below use it to answer one question: is this a request
-- from somebody's browser, and therefore something to be suspicious of?
create or replace function public.acting_role()
returns text
language sql
stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role',
    nullif(current_setting('request.jwt.claim.role', true), ''),
    ''
  );
$$;

-- Is the person behind this request an administrator?
--
-- SECURITY DEFINER for a reason that is easy to get wrong: the policies on
-- profiles are written in terms of this function, and a function that read
-- profiles under those same policies would call itself for ever. Owned by the
-- role that owns the tables, it reads the row directly and the recursion never
-- starts. It answers about the caller and nothing else, so it leaks nothing.
--
-- A suspended administrator is not one. The server refuses suspended accounts
-- a step earlier as well; this is the half of that which the database enforces.
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
      from public.profiles p
     where p.id = auth.uid()
       and p.role = 'admin'
       and p.status = 'active'
  );
$$;


-- ------------------------------------------------------------------ triggers
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

drop trigger if exists chats_touch on public.chats;
create trigger chats_touch before update on public.chats
  for each row execute function public.touch_updated_at();

drop trigger if exists maps_touch on public.maps;
create trigger maps_touch before update on public.maps
  for each row execute function public.touch_updated_at();

drop trigger if exists app_settings_touch on public.app_settings;
create trigger app_settings_touch before update on public.app_settings
  for each row execute function public.touch_updated_at();


-- The profile row, made the moment GoTrue makes the login. Without this every
-- new account would arrive with nothing to hold its role, and the server would
-- have to repair it on first sight — which it can, but on a good day it never
-- has to.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    lower(coalesce(new.email, '')),
    coalesce(nullif(trim(coalesce(new.raw_user_meta_data ->> 'display_name',
                                  new.raw_user_meta_data ->> 'name', '')), ''), '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- GoTrue owns the address. When it changes there — a confirmation, or somebody
-- changing their email — the copy here follows, so the admin list never shows
-- an address that is no longer the one that signs in.
create or replace function public.sync_user_email()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if coalesce(new.email, '') <> coalesce(old.email, '') then
    update public.profiles
       set email = lower(coalesce(new.email, ''))
     where id = new.id;
  end if;
  return new;
end;
$$;

drop trigger if exists on_auth_user_email on auth.users;
create trigger on_auth_user_email
  after update of email on auth.users
  for each row execute function public.sync_user_email();


-- Which columns a signed-in browser may not touch on its own row.
--
-- An UPDATE policy can see the row that is arriving but not the row it
-- replaces, so 'you may change your name but not your role' cannot be written
-- as a policy at all. It is written here instead: anything privileged is put
-- back the way it was unless the caller is an administrator.
--
-- The first line matters as much as the rest. A request that did not arrive as
-- `authenticated` is GoTrue syncing an address, this server promoting the first
-- administrator with the service key, or somebody in the SQL editor — none of
-- which this trigger is for.
create or replace function public.guard_profile_columns()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if public.acting_role() <> 'authenticated' then
    return new;
  end if;
  if public.is_admin() then
    return new;
  end if;
  new.id         := old.id;
  new.email      := old.email;          -- GoTrue owns this
  new.role       := old.role;
  new.status     := old.status;
  new.token_cap  := old.token_cap;
  new.notes      := old.notes;          -- an administrator's notes, about them
  new.created_at := old.created_at;
  return new;
end;
$$;

drop trigger if exists profiles_guard_columns on public.profiles;
create trigger profiles_guard_columns before update on public.profiles
  for each row execute function public.guard_profile_columns();


-- The panel refuses the two changes that would leave nobody able to open it,
-- and so does the table. Belt and braces on purpose: the check in
-- server/admin.py is the one that produces a readable sentence, and this is the
-- one that holds when a request finds a way past it.
--
-- The service key is let through, because that is the hand you need free if
-- this ever does lock somebody out.
create or replace function public.keep_one_admin()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  others integer;
  keep   public.profiles;
begin
  -- NEW does not exist on a DELETE, and PL/pgSQL will not let it be read there
  -- even inside a branch that cannot run, so the row to hand back is chosen
  -- once, up front.
  if tg_op = 'DELETE' then
    keep := old;
  else
    keep := new;
  end if;
  if public.acting_role() <> 'authenticated' then
    return keep;
  end if;
  -- Not an administrator's row: nothing to protect, and no query to run on the
  -- path that every ordinary account takes.
  if old.role <> 'admin' or old.status <> 'active' then
    return keep;
  end if;
  if tg_op = 'UPDATE' then
    if new.role = 'admin' and new.status = 'active' then
      return keep;                 -- still an administrator afterwards
    end if;
  end if;
  select count(*) into others
    from public.profiles p
   where p.role = 'admin' and p.status = 'active' and p.id <> old.id;
  if others = 0 then
    raise exception 'That is the only administrator left. Promote somebody '
                    'else first.'
      using errcode = 'PT409';
  end if;
  return keep;
end;
$$;

drop trigger if exists profiles_keep_one_admin on public.profiles;
create trigger profiles_keep_one_admin before update or delete on public.profiles
  for each row execute function public.keep_one_admin();


-- ---------------------------------------------------------------- the app's
-- This month's tokens for the person asking. Not SECURITY DEFINER: it runs
-- under the caller's own policies, which is what makes 'their own' true rather
-- than merely intended. The month is UTC, and so is the one the server prints.
create or replace function public.usage_this_month()
returns bigint
language sql
stable
as $$
  select coalesce(sum(e.total_tokens), 0)::bigint
    from public.usage_events e
   where e.user_id = auth.uid()
     and e.created_at >= date_trunc('month', now());
$$;


-- A chat's messages, replaced as one set.
--
-- The alternative was a DELETE and then an INSERT over two HTTP calls, where a
-- failure between them loses a conversation. Here the two statements are one
-- transaction, and either the new set is there or the old one still is.
--
-- SECURITY DEFINER, so the ownership check is this function's own job and it is
-- the first thing it does. The rows are written as the chat's owner rather than
-- as whoever called, which is the same thing on every path the app has.
create or replace function public.replace_messages(p_chat uuid, p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  owner uuid;
  n     integer;
begin
  select c.user_id into owner from public.chats c where c.id = p_chat;
  if owner is null then
    raise exception 'That chat is not there.' using errcode = 'PT404';
  end if;
  if owner is distinct from auth.uid() then
    raise exception 'That chat is not yours.' using errcode = 'PT403';
  end if;
  if jsonb_typeof(coalesce(p_rows, '[]'::jsonb)) <> 'array' then
    raise exception 'p_rows has to be an array of messages.' using errcode = 'PT400';
  end if;
  if jsonb_array_length(coalesce(p_rows, '[]'::jsonb)) > 2000 then
    raise exception 'That is more messages than one chat holds.'
      using errcode = 'PT413';
  end if;
  delete from public.messages where chat_id = p_chat;
  -- Every value is either recognised or replaced. server/shape.py has already
  -- checked all of it; this is the copy of those rules that holds when a row
  -- arrives from somewhere else.
  insert into public.messages (chat_id, user_id, seq, role, kind, text, meta, ts)
  select
    p_chat,
    owner,
    case when r.value ->> 'seq' ~ '^[0-9]{1,9}$'
         then (r.value ->> 'seq')::integer
         else (r.pos - 1)::integer end,
    case when r.value ->> 'role' in ('user', 'assistant', 'system')
         then r.value ->> 'role' else 'assistant' end,
    case when r.value ->> 'kind' in ('chat', 'map', 'plan', 'note', 'error')
         then r.value ->> 'kind' else 'chat' end,
    coalesce(r.value ->> 'text', ''),
    case when jsonb_typeof(r.value -> 'meta') = 'object'
         then r.value -> 'meta' else '{}'::jsonb end,
    case when r.value ->> 'ts' ~ '^[0-9]{1,15}$'
         then (r.value ->> 'ts')::bigint else 0 end
    from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb))
         with ordinality as r(value, pos);
  select count(*) into n from public.messages where chat_id = p_chat;
  -- The count in the chat row is what the list in the sidebar shows, so it is
  -- kept true here rather than trusted from the request that sent the rows.
  update public.chats c set message_count = n where c.id = p_chat;
  return n;
end;
$$;

-- ------------------------------------------------------------- the panel's
-- The numbers on the front page of the admin panel, in one round trip.
--
-- SECURITY DEFINER because two of these counts are over tables an
-- administrator has no policy on — messages and maps — and counting rows is
-- not reading them. The guard on the first line is what stands in for the
-- policies here, and it is the same predicate the policies use.
create or replace function public.admin_overview()
returns json
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  out_json json;
  day_0    timestamptz := date_trunc('day', now());
  month_0  timestamptz := date_trunc('month', now());
begin
  if not public.is_admin() then
    raise exception 'That is an administrator page.' using errcode = 'PT403';
  end if;
  select json_build_object(
    'month',        to_char(now(), 'YYYY-MM'),
    'users',        (select count(*) from public.profiles),
    'admins',       (select count(*) from public.profiles where role = 'admin'),
    'suspended',    (select count(*) from public.profiles where status = 'suspended'),
    'new_7d',       (select count(*) from public.profiles
                      where created_at >= now() - interval '7 days'),
    'seen_7d',      (select count(*) from public.profiles
                      where last_seen_at >= now() - interval '7 days'),
    'chats',        (select count(*) from public.chats),
    'maps',         (select count(*) from public.maps),
    'messages',     (select count(*) from public.messages),
    'calls_month',  (select count(*) from public.usage_events where created_at >= month_0),
    'calls_today',  (select count(*) from public.usage_events where created_at >= day_0),
    'failed_today', (select count(*) from public.usage_events
                      where created_at >= day_0 and not ok),
    'tokens_month', (select coalesce(sum(total_tokens), 0) from public.usage_events
                      where created_at >= month_0),
    'tokens_today', (select coalesce(sum(total_tokens), 0) from public.usage_events
                      where created_at >= day_0),
    'ms_median',    (select coalesce(percentile_disc(0.5)
                                     within group (order by ms), 0)
                       from public.usage_events where created_at >= day_0 and ok)
  ) into out_json;
  return out_json;
end;
$$;

-- One person's usage, for the page about them. Same shape of guard, same
-- reason: it counts and sums, it does not read what they wrote.
--
-- `cap` falls back to app_settings.default_token_cap when the profile does not
-- set one; the server falls back to COGNIX_TOKEN_CAP in the same case, so keep
-- the two agreeing if you change either. 0 means no ceiling.
create or replace function public.admin_user_usage(p_user uuid)
returns json
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  out_json json;
  month_0  timestamptz := date_trunc('month', now());
begin
  if not public.is_admin() then
    raise exception 'That is an administrator page.' using errcode = 'PT403';
  end if;
  select json_build_object(
    'month', to_char(now(), 'YYYY-MM'),
    'cap', coalesce(p.token_cap, s.default_token_cap, 0),
    'unlimited', coalesce(p.token_cap, s.default_token_cap, 0) <= 0,
    'used', (select coalesce(sum(e.total_tokens), 0) from public.usage_events e
              where e.user_id = p_user and e.created_at >= month_0),
    'used_all', (select coalesce(sum(e.total_tokens), 0) from public.usage_events e
                  where e.user_id = p_user),
    'calls', (select count(*) from public.usage_events e
               where e.user_id = p_user and e.created_at >= month_0),
    'failed', (select count(*) from public.usage_events e
                where e.user_id = p_user and e.created_at >= month_0 and not e.ok),
    'last_call', (select max(e.created_at) from public.usage_events e
                   where e.user_id = p_user),
    'chats', (select count(*) from public.chats c where c.user_id = p_user),
    'messages', (select count(*) from public.messages m where m.user_id = p_user),
    'maps', (select count(*) from public.maps m where m.user_id = p_user)
  ) into out_json
    from public.profiles p
    left join public.app_settings s on s.id = 1
   where p.id = p_user;
  return coalesce(out_json, json_build_object('month', to_char(now(), 'YYYY-MM'),
                                              'used', 0, 'calls', 0));
end;
$$;

-- Tokens by day, for the chart. The days come from generate_series and the
-- events are joined onto them, so a quiet Tuesday is a zero rather than a gap —
-- a chart with holes in it reads as missing data, which is a different worry.
create or replace function public.admin_usage_daily(p_days integer default 30)
returns table (day date, tokens bigint, calls bigint, failed bigint, people bigint)
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  span integer := greatest(1, least(365, coalesce(p_days, 30)));
begin
  if not public.is_admin() then
    raise exception 'That is an administrator page.' using errcode = 'PT403';
  end if;
  return query
    select g.ts::date,
           coalesce(sum(e.total_tokens), 0)::bigint,
           count(e.id)::bigint,
           (count(e.id) filter (where not e.ok))::bigint,
           count(distinct e.user_id)::bigint
      from generate_series(date_trunc('day', now()) - ((span - 1) * interval '1 day'),
                           date_trunc('day', now()),
                           interval '1 day') as g(ts)
      left join public.usage_events e
             on e.created_at >= g.ts
            and e.created_at <  g.ts + interval '1 day'
     group by g.ts
     order by g.ts;
end;
$$;

-- Who spent what, biggest first. Fifty rows: this is a table on a page, not a
-- report, and the page has a date filter for the rest.
create or replace function public.admin_usage_by_user(p_days integer default 30)
returns table (user_id uuid, email text, display_name text,
               tokens bigint, calls bigint, last_call timestamptz)
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  span integer := greatest(1, least(365, coalesce(p_days, 30)));
begin
  if not public.is_admin() then
    raise exception 'That is an administrator page.' using errcode = 'PT403';
  end if;
  return query
    select p.id, p.email, p.display_name,
           coalesce(sum(e.total_tokens), 0)::bigint,
           count(e.id)::bigint,
           max(e.created_at)
      from public.usage_events e
      join public.profiles p on p.id = e.user_id
     where e.created_at >= now() - (span * interval '1 day')
     group by p.id, p.email, p.display_name
     order by coalesce(sum(e.total_tokens), 0) desc, max(e.created_at) desc
     limit 50;
end;
$$;


-- ------------------------------------------------------------------- execute
-- The admin_* functions check is_admin() themselves; this is the second lock
-- on the same door. `anon` is not in either list, so a request with no session
-- cannot so much as call them.
revoke execute on function public.admin_overview()                  from public;
revoke execute on function public.admin_user_usage(uuid)            from public;
revoke execute on function public.admin_usage_daily(integer)        from public;
revoke execute on function public.admin_usage_by_user(integer)      from public;
revoke execute on function public.replace_messages(uuid, jsonb)     from public;
revoke execute on function public.usage_this_month()                from public;

grant execute on function public.admin_overview()              to authenticated, service_role;
grant execute on function public.admin_user_usage(uuid)        to authenticated, service_role;
grant execute on function public.admin_usage_daily(integer)    to authenticated, service_role;
grant execute on function public.admin_usage_by_user(integer)  to authenticated, service_role;
grant execute on function public.replace_messages(uuid, jsonb) to authenticated, service_role;
grant execute on function public.usage_this_month()            to authenticated, service_role;
grant execute on function public.is_admin()                    to authenticated, service_role;
grant execute on function public.acting_role()                 to authenticated, service_role;

