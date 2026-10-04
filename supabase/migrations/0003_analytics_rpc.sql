-- =====================================================================
-- Smart Agri Surveillance - RPC functions (migration 0003)
-- ---------------------------------------------------------------------
-- These return EXACTLY the JSON shapes the original FastAPI
-- `analytics_service.get_analytics()` and
-- `GET /api/detections/summary` produced, so the existing Analytics and
-- Dashboard screens work untouched.
--
-- All functions are SECURITY INVOKER (the default), which means the
-- caller's RLS policies are enforced: an anonymous browser key gets
-- nothing, a signed-in user gets the rows they are allowed to read.
-- Aggregating inside Postgres also keeps large detection histories off
-- the wire entirely, which is what makes the dashboard cheap.
-- =====================================================================

-- ---------------------------------------------------------------------
-- GET /api/detections/summary
-- { total_events, human_events, animal_events, vehicle_events, other_events }
-- ---------------------------------------------------------------------
create or replace function public.get_detection_summary()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    select jsonb_build_object(
        'total_events',  count(*),
        'human_events',   count(*) filter (where category = 'Human'),
        'animal_events',  count(*) filter (where category = 'Animals'),
        'vehicle_events', count(*) filter (where category = 'Vehicles'),
        'other_events',   count(*) filter (where category = 'Others')
    )
    from public.detections;
$$;

comment on function public.get_detection_summary() is
    'Detection totals by category - backs GET /api/detections/summary.';

-- ---------------------------------------------------------------------
-- GET /api/analytics
-- ---------------------------------------------------------------------
create or replace function public.get_analytics()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
with now_utc as (
    select now() at time zone 'utc' as ts
),
-- Same window boundaries the original Python service used.
windows as (
    select
        (select ts from now_utc) - interval '24 hours' as daily_since,
        (select ts from now_utc) - interval '7 days'   as weekly_since,
        (select ts from now_utc) - interval '30 days'  as monthly_since
),
-- created_at is timestamptz; the original app stored naive-UTC values and
-- always read them back as UTC, so every bucket is computed in UTC here
-- too and the result is indistinguishable from the old behaviour.
detections_utc as (
    select
        category,
        (created_at at time zone 'utc') as created_utc
    from public.detections
),
series as (
    -- "daily": last 24 hours bucketed by hour
    select
        'daily'::text    as series,
        to_char(d.created_utc, 'YYYY-MM-DD HH24:00') as bucket,
        d.category,
        count(*)::bigint as n
    from detections_utc d, windows w
    where d.created_utc >= w.daily_since
    group by 1, 2, 3

    union all

    -- "weekly": last 7 days bucketed by day
    select
        'weekly'::text,
        to_char(d.created_utc, 'YYYY-MM-DD'),
        d.category,
        count(*)::bigint
    from detections_utc d, windows w
    where d.created_utc >= w.weekly_since
    group by 1, 2, 3

    union all

    -- "monthly": last 30 days bucketed by day
    select
        'monthly'::text,
        to_char(d.created_utc, 'YYYY-MM-DD'),
        d.category,
        count(*)::bigint
    from detections_utc d, windows w
    where d.created_utc >= w.monthly_since
    group by 1, 2, 3
),
pivoted as (
    select
        series,
        bucket,
        count(*) filter (where category = 'Human')::int   as "Human",
        count(*) filter (where category = 'Animals')::int  as "Animals",
        count(*) filter (where category = 'Vehicles')::int as "Vehicles",
        count(*) filter (where category = 'Others')::int   as "Others"
    from series
    group by series, bucket
),
by_category as (
    select
        coalesce(jsonb_object_agg(coalesce(d.category, 'Others'), n), '{}'::jsonb) as obj,
        sum(n) as total
    from (
        select category, count(*)::int as n
        from public.detections
        group by category
    ) d
),
by_camera as (
    select coalesce(jsonb_agg(jsonb_build_object('camera_name', camera_name, 'count', n) order by n desc), '[]'::jsonb) as obj
    from (
        select coalesce(camera_name, 'Unknown') as camera_name, count(*)::int as n
        from public.detections
        where camera_name is not null
        group by 1
        order by n desc
        limit 10
    ) c
),
peak_hours as (
    select coalesce(
        jsonb_agg(jsonb_build_object('hour', h.hour, 'count', h.n) order by h.hour),
        '[]'::jsonb
    ) as obj
    from (
        select gs::int as hour, count(d.id)::int as n
        from generate_series(0, 23) gs
        left join public.detections d
               on extract(hour from (d.created_at at time zone 'utc'))::int = gs::int
        group by gs
    ) h
),
combined as (
    -- Category list is seeded from CATEGORY_ORDER so the chart always
    -- shows all four categories, even at zero.
    select
        coalesce((
            select jsonb_object_agg(c, coalesce((bc.obj ->> c)::int, 0))
            from unnest(array['Human', 'Animals', 'Vehicles', 'Others']) as c
        ), '{}'::jsonb) as categories
    from by_category bc
)
select jsonb_build_object(
    'daily',       coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'daily'),   '[]'::jsonb),
    'weekly',      coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'weekly'),  '[]'::jsonb),
    'monthly',     coalesce((select jsonb_agg(jsonb_build_object('bucket', bucket, 'Human', "Human", 'Animals', "Animals", 'Vehicles', "Vehicles", 'Others', "Others") order by bucket) from pivoted where series = 'monthly'), '[]'::jsonb),
    'by_category', categories,
    'by_camera',   (select obj from by_camera),
    'peak_hours',  (select obj from peak_hours)
)
from combined;
$$;

comment on function public.get_analytics() is
    'Detection trends (daily/weekly/monthly), by-category, by-camera and peak hours - backs GET /api/analytics.';

-- ---------------------------------------------------------------------
-- Lightweight dashboard health probe used by the frontend when the local
-- AI server cannot be reached, so the dashboard degrades instead of
-- breaking. Returns { database_ok, total_cameras }.
-- ---------------------------------------------------------------------
create or replace function public.get_system_health()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
    select jsonb_build_object(
        'database_ok',    true,
        'total_cameras',  (select count(*)::int from public.cameras),
        'active_cameras', (select count(*)::int from public.cameras where status = 'online')
    );
$$;

comment on function public.get_system_health() is
    'Cheap cloud-side health/camera-count probe used by the dashboard fallback.';
