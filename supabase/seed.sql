-- Cognix — the one row that has to exist, and two checks that tell you whether
-- the other three files did what they said. Run this last.
--
-- Safe to run twice: the insert does nothing if the row is already there, and
-- the selects only read.

insert into public.app_settings (id) values (1)
on conflict (id) do nothing;


-- ------------------------------------------------- accounts that came first
-- The trigger in functions.sql makes a profile row when GoTrue makes a login,
-- so from here on every signup has one. Anybody who signed up *before* these
-- files were run does not — their signup happened when the trigger did not
-- exist yet. The server repairs that on their next sign-in, but only for
-- somebody who does sign in again, and until then the admin list cannot see
-- them at all. One statement settles it, with the same column mapping as
-- handle_new_user().
insert into public.profiles (id, email, display_name)
select u.id,
       lower(coalesce(u.email, '')),
       coalesce(nullif(trim(coalesce(u.raw_user_meta_data ->> 'display_name',
                                     u.raw_user_meta_data ->> 'name', '')), ''), '')
  from auth.users u
 where not exists (select 1 from public.profiles p where p.id = u.id)
on conflict (id) do nothing;


-- ---------------------------------------------------------- the first admin
-- Two ways, pick one.
--
-- The easy way: put your address in COGNIX_ADMIN_EMAILS in the app's .env,
-- sign up through the app like anybody else, and open /app/admin/. The server
-- promotes that account once, writes a line in the audit log saying it did, and
-- never needs the service key for it again.
--
-- The other way, if you would rather not name yourself in a file: sign up
-- first, then run this by hand with your own address.
--
-- update public.profiles set role = 'admin'
--  where email = lower('you@example.com');


-- ------------------------------------------------------------------- checks
-- Every table should say true and at least one policy. A table with RLS on and
-- zero policies answers every request with nothing — which is safe, but it is
-- also how 'my chats do not load' looks from the outside.
select c.relname                as table_name,
       c.relrowsecurity         as rls_on,
       count(p.polname)         as policies
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  left join pg_policy p on p.polrelid = c.oid
 where n.nspname = 'public'
   and c.relkind = 'r'
 group by c.relname, c.relrowsecurity
 order by c.relname;


-- Every one of these should say true. They are the functions the app calls by
-- name; a false here is a 500 in the app later, on one page only.
with want (name) as (
  values ('acting_role'), ('is_admin'), ('touch_updated_at'),
         ('handle_new_user'), ('sync_user_email'), ('guard_profile_columns'),
         ('keep_one_admin'), ('usage_this_month'), ('replace_messages'),
         ('admin_overview'), ('admin_user_usage'), ('admin_usage_daily'),
         ('admin_usage_by_user')
)
select w.name,
       exists (select 1 from pg_proc p
               join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = w.name) as present
  from want w
 order by w.name;
