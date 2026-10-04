-- =====================================================================
-- Smart Agri Surveillance - Storage setup (migration 0004)
-- ---------------------------------------------------------------------
-- Only ONE bucket is needed. Raw camera frames are never uploaded - the
-- local AI server captures frames locally, runs YOLO locally, and only
-- pushes the small JPEG snapshot it saves on a *detection event*.
-- Object key layout (unchanged in spirit from the original local folder
-- layout, so nothing that reads `snapshot_path` breaks):
--
--     snapshots/YYYY-MM-DD/cam<id>_<object>_<HHMMSS_micro>.jpg
--
-- `detections.snapshot_path` stores exactly this key (relative to the
-- bucket root). The frontend turns it into a public URL with
-- VITE_SUPABASE_URL. Image bytes never touch Postgres.
--
-- Run this migration as the project owner (Dashboard > SQL editor), the
-- service_role key, or `supabase db push` - it touches the `storage`
-- schema, which the anon/authenticated roles cannot modify.
-- =====================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'snapshots',
    'snapshots',
    true,
    10485760,  -- 10 MB per object: plenty for a JPEG snapshot, and a hard
                -- guard against someone trying to push video into the bucket
    array['image/jpeg', 'image/png']
)
on conflict (id) do update
    set public                = excluded.public,
        file_size_limit       = excluded.file_size_limit,
        allowed_mime_types    = excluded.allowed_mime_types;

comment on storage.buckets is
    'Snapshots bucket for Smart Agri Surveillance. Public read so the browser can render <img src> without a backend round-trip; uploads are service-role only.';

-- Anyone may READ objects in this bucket (it is public anyway).
drop policy if exists snapshots_read_public on storage.objects;
create policy snapshots_read_public
    on storage.objects for select
    to public
    using (bucket_id = 'snapshots');

-- NO insert/update/delete policy is created for anon or authenticated.
-- Snapshots can therefore only be written with the service-role key,
-- i.e. by the local AI server.
