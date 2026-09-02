-- Cognix — who may read and write which rows. Run this after functions.sql.
--
-- This file is the security model. Not server/api.py: that file decides which
-- endpoints exist and what a sensible error message says, but every query it
-- makes for a signed-in person carries that person's own access token, so what
-- comes back is decided here. A mistake in the server cannot hand over
-- somebody else's chat unless the same mistake is also written below.
--
-- Read it as four sentences:
--
--   * You may read and write your own rows. Ownership is `auth.uid()`, which
--     comes out of a signed token and cannot be asked for politely.
--   * An administrator may read every account, and every chat's title, size
--     and dates. Not the messages. Not the maps. Support needs to see that
--     somebody has forty maps and when they last touched one; it does not need
--     to read them, so there is no policy that lets it.
--   * usage_events and audit_log are append-only. Nobody has an update or a
--     delete policy on either, because somebody who could delete their own
--     usage rows could spend without a ceiling, and a log an administrator can
--     edit is not a log.
--   * app_settings is readable by anybody, including a browser with no
--     session, because the sign-in page has to know whether signups are open.
--     Only an administrator writes it. Nothing secret goes in that table.
--
-- Safe to run twice: every policy is dropped by name first.


-- ------------------------------------------------------------------ profiles
-- Two read policies rather than one with an `or`: they are separate rules,
-- they change for separate reasons, and PostgreSQL ORs them together anyway.
drop policy if exists profiles_read_own on public.profiles;
create policy profiles_read_own on public.profiles
  for select to authenticated
  using (id = auth.uid());

drop policy if exists profiles_read_admin on public.profiles;
create policy profiles_read_admin on public.profiles
  for select to authenticated
  using (public.is_admin());

-- The signup trigger normally makes this row. This is what lets the server
-- repair an account that predates the trigger, and it can only make its own.
drop policy if exists profiles_insert_own on public.profiles;
create policy profiles_insert_own on public.profiles
  for insert to authenticated
  with check (id = auth.uid());
-- 'You may change your name, and nothing else that matters' is not something a
-- policy can say: an UPDATE policy sees the row arriving but not the row it
-- replaces, so it cannot tell a name change from a promotion. This policy
-- therefore says only 'your own row', and guard_profile_columns() in
-- functions.sql puts role, status, token_cap, notes and email back.
drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

drop policy if exists profiles_update_admin on public.profiles;
create policy profiles_update_admin on public.profiles
  for update to authenticated
  using (public.is_admin())
  with check (public.is_admin());

-- Deleting the login in GoTrue takes this row with it through the foreign key.
-- This is the other case: a row left behind by a login that is already gone.
drop policy if exists profiles_delete_admin on public.profiles;
create policy profiles_delete_admin on public.profiles
  for delete to authenticated
  using (public.is_admin());


-- --------------------------------------------------------------------- chats
drop policy if exists chats_own on public.chats;
create policy chats_own on public.chats
  for all to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Titles, sizes and dates, across every account. The admin user list also
-- reads this table one level down, as profiles(…, chats(count)).
drop policy if exists chats_read_admin on public.chats;
create policy chats_read_admin on public.chats
  for select to authenticated
  using (public.is_admin());

-- ------------------------------------------------------------------ messages
-- Owner only, and no administrator policy at all. Both halves of the check are
-- kept: the chat has to be yours as well as the row, so a row cannot be filed
-- against somebody else's conversation by sending a different chat_id.
--
-- The app writes these through replace_messages(), which does its own check
-- for the same reason and inside one transaction. These policies are what
-- answer a direct read, and what would answer a direct write.
drop policy if exists messages_own on public.messages;
create policy messages_own on public.messages
  for all to authenticated
  using (
    user_id = auth.uid()
    and exists (select 1 from public.chats c
                 where c.id = messages.chat_id and c.user_id = auth.uid())
  )
  with check (
    user_id = auth.uid()
    and exists (select 1 from public.chats c
                 where c.id = messages.chat_id and c.user_id = auth.uid())
  );


-- ---------------------------------------------------------------------- maps
drop policy if exists maps_own on public.maps;
create policy maps_own on public.maps
  for all to authenticated
  using (
    user_id = auth.uid()
    and exists (select 1 from public.chats c
                 where c.id = maps.chat_id and c.user_id = auth.uid())
  )
  with check (
    user_id = auth.uid()
    and exists (select 1 from public.chats c
                 where c.id = maps.chat_id and c.user_id = auth.uid())
  );

-- -------------------------------------------------------------- usage_events
-- A person may see what they have spent and add to it. There is no update and
-- no delete policy, for anybody: a row that can be removed is not a ceiling.
drop policy if exists usage_read_own on public.usage_events;
create policy usage_read_own on public.usage_events
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists usage_insert_own on public.usage_events;
create policy usage_insert_own on public.usage_events
  for insert to authenticated
  with check (user_id = auth.uid());

drop policy if exists usage_read_admin on public.usage_events;
create policy usage_read_admin on public.usage_events
  for select to authenticated
  using (public.is_admin());


-- ----------------------------------------------------------------- audit_log
-- Append-only, and the actor cannot be somebody else. That second clause is
-- what makes the actor column worth reading: the row is written with the
-- administrator's own token, and the policy refuses any other value.
drop policy if exists audit_read_admin on public.audit_log;
create policy audit_read_admin on public.audit_log
  for select to authenticated
  using (public.is_admin());

drop policy if exists audit_insert_admin on public.audit_log;
create policy audit_insert_admin on public.audit_log
  for insert to authenticated
  with check (public.is_admin() and actor = auth.uid());

-- -------------------------------------------------------------- app_settings
-- The one table a browser with no session may read. It holds whether signups
-- are open, whether there is a maintenance notice, and which models the app
-- offers — all of it public by nature, none of it a secret.
drop policy if exists settings_read_all on public.app_settings;
create policy settings_read_all on public.app_settings
  for select to anon, authenticated
  using (true);

drop policy if exists settings_write_admin on public.app_settings;
create policy settings_write_admin on public.app_settings
  for insert to authenticated
  with check (public.is_admin());

drop policy if exists settings_update_admin on public.app_settings;
create policy settings_update_admin on public.app_settings
  for update to authenticated
  using (public.is_admin())
  with check (public.is_admin());


-- PostgREST keeps a cache of the schema and the policies. The Supabase editor
-- sends this for you; it is here so that running these files from psql, or from
-- a migration, does not leave the API answering from a stale picture.
notify pgrst, 'reload schema';

