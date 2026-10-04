-- =====================================================================
-- Smart Agri Surveillance - Row Level Security (migration 0002)
-- ---------------------------------------------------------------------
-- Goal: the Cloudflare Pages frontend holds ONLY the anon key, so it can
-- never read or write anything directly. Every request must carry a
-- Supabase Auth session, i.e. the user must be logged in.
--
-- Authorization model is deliberately UNCHANGED from the original app:
-- the README documented "open self-registration, no admin approval, every
-- account has equal access to the full app". Therefore every authenticated
-- user gets the same full access to app data. The `role` column is kept
-- (and readable) so role-gated features can be layered on later without a
-- schema change - but no rule depends on it today, exactly as before.
--
-- camera_secrets is intentionally left WITHOUT any policy: with RLS
-- enabled and zero policies, it is completely unreachable using the
-- anon/authenticated keys. Only the service-role key (held solely by the
-- local AI server) can read or write camera passwords.
-- =====================================================================

alter table public.users                 enable row level security;
alter table public.cameras               enable row level security;
alter table public.camera_secrets        enable row level security;
alter table public.detections            enable row level security;
alter table public.alerts                enable row level security;
alter table public.settings              enable row level security;
alter table public.video_analysis_jobs   enable row level security;
alter table public.camera_status_history enable row level security;

-- ---------------------------------------------------------------------
-- users
-- Any signed-in user can read the user list (there is no admin screen in
-- this app); a user can only modify their own profile row.
-- ---------------------------------------------------------------------
drop policy if exists users_select_authenticated on public.users;
create policy users_select_authenticated
    on public.users for select
    to authenticated
    using (true);

drop policy if exists users_insert_self on public.users;
create policy users_insert_self
    on public.users for insert
    to authenticated
    with check (auth.uid() = id);

drop policy if exists users_update_self on public.users;
create policy users_update_self
    on public.users for update
    to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);

-- ---------------------------------------------------------------------
-- cameras - readable (without the password, which lives in camera_secrets)
-- and manageable by any signed-in user, matching the previous behaviour.
-- ---------------------------------------------------------------------
drop policy if exists cameras_all_authenticated on public.cameras;
create policy cameras_all_authenticated
    on public.cameras for all
    to authenticated
    using (true)
    with check (true);

-- ---------------------------------------------------------------------
-- detections / alerts / jobs / settings / status history
-- ---------------------------------------------------------------------
drop policy if exists detections_all_authenticated on public.detections;
create policy detections_all_authenticated
    on public.detections for all
    to authenticated
    using (true)
    with check (true);

drop policy if exists alerts_all_authenticated on public.alerts;
create policy alerts_all_authenticated
    on public.alerts for all
    to authenticated
    using (true)
    with check (true);

drop policy if exists settings_all_authenticated on public.settings;
create policy settings_all_authenticated
    on public.settings for all
    to authenticated
    using (true)
    with check (true);

drop policy if exists jobs_all_authenticated on public.video_analysis_jobs;
create policy jobs_all_authenticated
    on public.video_analysis_jobs for all
    to authenticated
    using (true)
    with check (true);

drop policy if exists camera_status_history_all_authenticated on public.camera_status_history;
create policy camera_status_history_all_authenticated
    on public.camera_status_history for all
    to authenticated
    using (true)
    with check (true);

-- ---------------------------------------------------------------------
-- camera_secrets: NO POLICIES.
-- RLS is enabled above, so with no policy matching, every request made
-- with the anon or authenticated key is rejected. The service role
-- bypasses RLS, which is how the local AI server reaches camera
-- credentials without them ever being exposed to a browser.
--   * service role key  -> full access
--   * anon key          -> rejected (no policy)
--   * user JWT          -> rejected (no policy)
-- ---------------------------------------------------------------------
