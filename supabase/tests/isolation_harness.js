/**
 * Runs the project's real migrations (0001 -> 0005) inside a PGlite
 * (WASM Postgres) instance bootstrapped to look like Supabase, then asserts
 * that per-user isolation actually holds.
 *
 * This exists because the only real database available is the owner's live
 * Supabase project, and applying a schema migration to that without their
 * say-so is not acceptable. PGlite gives a real Postgres engine - RLS,
 * generated columns, plpgsql, roles, security invoker functions - so the
 * migration SQL is genuinely executed and genuinely tested rather than
 * eyeballed.
 *
 *   node isolation_harness.js
 *
 * Exit 0 = every assertion held.
 */
const fs = require('fs');
const path = require('path');
const { PGlite } = require('@electric-sql/pglite');

// Default to the sibling ../migrations folder, so this works from a clone at
// any path. Pass a directory as argv[2] to test a different migration set.
const MIGRATIONS = process.argv[2] ||
  path.join(__dirname, '..', 'migrations');

const A = 'aaaaaaaa-0000-4000-8000-000000000001';
const B = 'bbbbbbbb-0000-4000-8000-000000000002';

let pass = 0;
const failures = [];
const G = '\x1b[32m', R = '\x1b[31m', Y = '\x1b[33m', B_ = '\x1b[1m', D = '\x1b[2m', X = '\x1b[0m';

function ok(d) { pass++; console.log(`    ${G}PASS${X} ${d}`); }
function bad(d) { failures.push(d); console.log(`    ${R}FAIL${X} ${d}`); }
function info(d) { console.log(`    ${D}${d}${X}`); }
function head(t) { console.log(`\n${B_}${t}${X}`); }
function check(cond, d, why = '') { cond ? ok(d) : bad(`${d}${why ? ' -- ' + why : ''}`); }

// PGlite returns rows as objects; for the many single-column catalogue queries
// below the column name is not interesting, only its value.
const col = (r) => Object.values(r)[0];

// Impersonate a signed-in browser session: set the JWT claim Supabase would
// set, then switch to the `authenticated` role - the only role RLS applies to.
let dbRef = null;
const asUser = async (uuid, sql) => {
  await dbRef.exec(`set role authenticated; set request.jwt.claim.sub = '${uuid}';`);
  try { return (await dbRef.query(sql)).rows; }
  finally { await dbRef.exec(`reset role;`); }
};
const tryAsUser = async (uuid, sql) => {
  await dbRef.exec(`set role authenticated; set request.jwt.claim.sub = '${uuid}';`);
  try { await dbRef.exec(sql); return null; }
  catch (e) { return e.message; }
  finally { await dbRef.exec(`reset role;`); }
};

(async () => {
  const db = await PGlite.create();
  dbRef = db;
  const exec = (s) => db.exec(s);
  const rows = async (s) => (await db.query(s)).rows;

  // ---------------------------------------------------------------- bootstrap
  head('Bootstrap a Supabase-shaped Postgres');
  await exec(`
    do $$ begin
      if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role anon nologin;
        create role authenticated nologin;
        create role service_role nologin bypassrls;
      end if;
    end $$;

    create schema if not exists auth;
    create table if not exists auth.users (
      id uuid primary key,
      email text,
      created_at timestamptz not null default now()
    );

    -- Supabase defines auth.uid() by reading the JWT claim off the connection.
    create or replace function auth.uid() returns uuid
      language sql stable
    as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

    create or replace function auth.jwt() returns jsonb
      language sql stable
    as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb) $$;

    -- The storage schema exists in Supabase; mimic the parts the migrations use.
    create schema if not exists storage;
    create table if not exists storage.buckets (
      id text primary key,
      name text not null unique,
      public boolean not null default false,
      file_size_limit bigint,
      allowed_mime_types text[]
    );
    create table if not exists storage.objects (
      id uuid primary key default gen_random_uuid(),
      bucket_id text references storage.buckets(id),
      name text not null,
      owner uuid
    );
    create or replace function storage.foldername(name text) returns text[]
      language sql immutable
    as $$ select string_to_array(regexp_replace(name, '^/+', ''), '/') $$;

    grant usage on schema public to anon, authenticated, service_role;
    -- Real Supabase lets the client-facing roles call auth.uid(); without this
    -- every auth.uid() policy raises "permission denied for schema auth".
    grant usage on schema auth to anon, authenticated, service_role;
    grant all on all tables in schema storage to anon, authenticated, service_role;
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
    alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
    alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
  `);
  ok('roles, auth schema, auth.uid() and storage schema created');

  // --------------------------------------------------------------- migrations
  // The ORDER matters: 0005's legacy backfill can only run against rows that
  // already exist, so the pre-migration data is seeded between 0004 and 0005 -
  // exactly the state the owner's live database is in right now.
  head('Apply migrations 0001-0004 (the pre-isolation schema)');
  const files = fs.readdirSync(MIGRATIONS).filter(f => f.endsWith('.sql')).sort();
  if (!files.length) {
    console.log(`\n${R}No .sql files found in ${MIGRATIONS}${X}`);
    process.exit(2);
  }
  for (const f of files) {
    if (f.startsWith('0005')) break;
    const sql = fs.readFileSync(path.join(MIGRATIONS, f), 'utf8');
    try {
      await exec(sql);
      info(`${f} applied`);
    } catch (e) {
      bad(`${f} FAILED TO APPLY: ${e.message}`);
      console.log(`\n${R}Cannot continue without a valid migration chain.${X}`);
      process.exit(1);
    }
  }

  head("Seed the owner's pre-existing rows (1 camera, 2 detections, 2 alerts, 1 job)");
  await exec(`
    insert into auth.users (id, email, created_at) values
      ('${A}', 'a@test.local', '2026-10-04 09:20:31+00'),
      ('${B}', 'b@test.local', '2026-10-04 16:16:26+00');

    insert into public.users (id, username, email, name, role, is_active, created_at) values
      ('${A}', 'user_a', 'a@test.local', 'User A', 'user', true, '2026-10-04 09:20:31+00'),
      ('${B}', 'user_b', 'b@test.local', 'User B', 'user', true, '2026-10-04 16:16:26+00');

    -- Camera + detections + alerts all predate B signing up: they are A's.
    insert into public.cameras (camera_code, name, stream_url, camera_type, connection_type, enabled, created_at)
    values ('CAM-001', 'cam 1', 'http://x', 'ip_camera', 'http_mjpeg', true, '2026-10-04 09:21:30+00');

    insert into public.detections
      (client_event_id, source, camera_id, camera_name, object_name, category,
       severity, confidence, avg_confidence, frame_count, start_time, end_time,
       duration_seconds, snapshot_path, created_at)
    values
      (gen_random_uuid(), 'Live Camera', 1, 'cam 1', 'person', 'Human', 'critical',
       0.9, 0.9, 120, now(), now(), 120, 'snapshots/2026-10-04/legacy1.jpg', '2026-10-04 09:21:48+00'),
      (gen_random_uuid(), 'Live Camera', 1, 'cam 1', 'dog', 'Animals', 'warning',
       0.8, 0.8, 60, now(), now(), 60, 'snapshots/2026-10-04/legacy2.jpg', '2026-10-04 16:15:16+00');

    insert into public.alerts
      (client_alert_id, detection_id, camera_id, camera_name, category, severity,
       alert_type, title, message, acknowledged, resolved, created_at)
    select gen_random_uuid(), d.id, d.camera_id, d.camera_name, d.category, d.severity,
           'intrusion', 'Intrusion Alert', 'legacy alert', false, false, d.created_at
    from public.detections d;

    insert into public.video_analysis_jobs (original_filename, status, client_job_id, created_at)
    values ('legacy.mp4', 'completed', gen_random_uuid(), '2026-10-04 09:22:18+00');
  `);
  ok('seeded 2 users, 1 camera, 2 detections, 2 alerts, 1 job');

  // 0001 already creates the single global settings row, so confirm it is there
  // rather than inserting a second one.
  const seedSettings = await rows(`select count(*)::int as n from public.settings`);
  check(col(seedSettings[0]) === 1,
    'the pre-migration database has exactly 1 global settings row (id = 1)',
    `found ${col(seedSettings[0])}`);

  // Prove the pre-0005 state really is shared, so a later PASS is meaningful.
  head('PRE-CHECK: before 0005 the data is genuinely shared');
  const preB = await asUser(B, `select count(*)::int as n from public.cameras`);
  check(col(preB[0]) === 1,
    'B can see A\'s camera before the migration (that is the reported bug)',
    `B sees ${col(preB[0])} cameras`);
  const prePol = await rows(`select count(*)::int as n from pg_policies
    where schemaname='public' and tablename='cameras' and qual='true'`);
  check(col(prePol[0]) >= 1, 'the pre-0005 cameras policy really is `using (true)`');

  head('Apply migration 0005 (per-user isolation)');
  {
    const f = '0005_per_user_isolation.sql';
    const sql = fs.readFileSync(path.join(MIGRATIONS, f), 'utf8');
    try {
      await exec(sql);
      ok(`${f} applied`);
    } catch (e) {
      bad(`${f} FAILED TO APPLY: ${e.message}`);
      console.log(`\n${R}Migration 0005 does not apply. Nothing below can be trusted.${X}`);
      process.exit(1);
    }
  }

  // --------------------------------------------------------- schema assertions
  head('Owner columns exist on every user-owned table');
  const owned = ['cameras', 'detections', 'alerts', 'video_analysis_jobs',
                 'camera_status_history', 'settings'];
  for (const t of owned) {
    const r = await rows(`select data_type from information_schema.columns
                          where table_schema='public' and table_name='${t}' and column_name='user_id'`);
    check(r.length === 1, `${t}.user_id exists`, r.length === 0 ? 'column missing' : '');
  }

  head('settings became one row per user');
  const sCols = await rows(`select column_name from information_schema.columns
    where table_schema='public' and table_name='settings' order by ordinal_position`);
  const names = sCols.map(col);
  check(!names.includes('id'), 'the global `id` column is gone', `still has ${names}`);
  const pk = await rows(`select a.attname from pg_index i
    join pg_attribute a on a.attrelid=i.indrelid and a.attnum=any(i.indkey)
    where i.indrelid='public.settings'::regclass and i.indisprimary`);
  check(pk.length === 1 && col(pk[0]) === 'user_id',
    'settings primary key is user_id', `pk is ${pk.map(col)}`);

  head('camera_code is unique PER USER, not globally');
  const idx = await rows(`select indexname, indexdef from pg_indexes
    where schemaname='public' and tablename='cameras' and indexdef ilike '%unique%'`);
  const perUser = idx.filter(i => /user_id/.test(i.indexdef) && /camera_code/.test(i.indexdef));
  const globalOnly = idx.filter(i => /camera_code/.test(i.indexdef) && !/user_id/.test(i.indexdef));
  check(perUser.length >= 1, 'a UNIQUE index on (camera_code, user_id) exists',
    idx.map(i => i.indexname).join(', '));
  check(globalOnly.length === 0, 'the globally-unique camera_code constraint is gone',
    globalOnly.map(i => i.indexname).join(', '));

  // Did the migration's backfill fill them in?
  head('Legacy backfill assigned every row an owner');
  for (const t of ['cameras', 'detections', 'alerts', 'video_analysis_jobs']) {
    const n = await rows(`select count(*)::int as n from public.${t} where user_id is null`);
    check(col(n[0]) === 0, `${t}: no row left without an owner`, `${col(n[0])} still null`);
  }
  const ownedByA = await rows(`select
      (select count(*) from public.cameras where user_id='${A}')::int c,
      (select count(*) from public.cameras where user_id='${B}')::int cb,
      (select count(*) from public.detections where user_id='${A}')::int d,
      (select count(*) from public.video_analysis_jobs where user_id='${A}')::int j`);
  check(ownedByA[0].c === 1 && ownedByA[0].cb === 0,
    'the single legacy camera went to the earliest profile (A)',
    `A=${ownedByA[0].c} B=${ownedByA[0].cb}`);
  check(ownedByA[0].d === 2, 'both legacy detections went to A', `A has ${ownedByA[0].d}`);
  check(ownedByA[0].j === 1, 'the legacy job went to A', `A has ${ownedByA[0].j}`);

  // ------------------------------------------------------------------- policies
  head('No `using (true)` policy survives on a user-owned table');
  const pols = await rows(`select tablename, policyname, roles::text, qual, with_check
    from pg_policies where schemaname='public' order by tablename, policyname`);
  for (const p of pols) {
    const q = (p.qual || '').trim().toLowerCase();
    const w = (p.with_check || '').trim().toLowerCase();
    check(q !== 'true' && w !== 'true',
      `${p.tablename}.${p.policyname} is not a blanket grant`, `qual=${p.qual} with_check=${p.with_check}`);
  }
  const scoped = pols.filter(p => /auth\.uid\(\)/.test(p.qual || ''));
  check(scoped.length >= owned.length - 1,
    `${scoped.length} policies compare against auth.uid()`);

  // ==================================================== THE ISOLATION ASSERTIONS
  // `asUser` / `tryAsUser` impersonate a signed-in browser session by setting
  // the JWT claim Supabase would set, then switching to the `authenticated`
  // role - which is the only role RLS applies to.

  head('CROSS-USER READ ISOLATION (as the `authenticated` role, i.e. RLS)');
  const T = owned;

  for (const t of T) {
    const r = await asUser(B, `select count(*)::int as n from public.${t}`);
    const bSees = col(r[0]);
    check(bSees === 0, `B sees 0 rows in ${t}`, `B sees ${bSees} (expected 0 - B owns nothing yet)`);
  }
  for (const t of T) {
    const r = await asUser(A, `select count(*)::int as n from public.${t}`);
    info(`A sees ${col(r[0])} row(s) in ${t}`);
  }
  const aCams = await asUser(A, `select id, camera_code, user_id from public.cameras`);
  check(aCams.length === 1 && aCams[0].user_id === A, 'A still sees its own camera');

  // NOTE ON WHAT "REFUSED" MEANS HERE.
  // A cross-user UPDATE/DELETE does not raise an error in Postgres: RLS hides
  // the row from B's snapshot, so the statement simply matches nothing and
  // affects 0 rows. That is safe, but it means "did it error" is the wrong
  // question - the right one is "is the row still intact". The assertions below
  // check the resulting row state, which is what actually matters. (INSERT is
  // the exception: planting a row owned by someone else violates the WITH
  // CHECK clause and does raise, so that one is asserted as an error.)
  head('CROSS-USER WRITE ATTEMPTS HAVE NO EFFECT');
  await tryAsUser(B, `update public.cameras set name='hijacked' where user_id='${A}'`);
  const stillCam = await rows(`select name from public.cameras where user_id='${A}'`);
  check(stillCam[0].name === 'cam 1', "B's rename of A's camera had no effect",
    `the name is now "${stillCam[0].name}"`);

  await tryAsUser(B, `update public.detections set object_name='mine' where user_id='${A}'`);
  const detNames = await rows(`select distinct object_name from public.detections where user_id='${A}'`);
  check(detNames.length === 2 && !detNames.some(d => d.object_name === 'mine'),
    "B's rewrite of A's detections had no effect",
    `object names are now ${detNames.map(d => d.object_name)}`);

  await tryAsUser(B, `update public.alerts set acknowledged=true, resolved=true where user_id='${A}'`);
  const alertState = await rows(`select count(*)::int as n from public.alerts
                                 where user_id='${A}' and acknowledged`);
  check(col(alertState[0]) === 0, "B cannot acknowledge or resolve A's alerts",
    `${col(alertState[0])} of A's alerts came back acknowledged`);

  await tryAsUser(B, `delete from public.detections where user_id='${A}'`);
  const detLeft = await rows(`select count(*)::int as n from public.detections where user_id='${A}'`);
  check(col(detLeft[0]) === 2, "B cannot delete A's detections", `${col(detLeft[0])} of 2 remain`);

  const plantCam = await tryAsUser(B, `insert into public.cameras (camera_code,name,stream_url,camera_type,connection_type,enabled,user_id)
     values ('CAM-002','planted','http://x','ip_camera','http_mjpeg',true,'${A}')`);
  check(plantCam !== null, "B cannot insert a camera owned by A", 'the insert was ALLOWED');

  const plantAlert = await tryAsUser(B, `insert into public.alerts (client_alert_id,detection_id,camera_id,camera_name,category,severity,alert_type,title,message,acknowledged,resolved,user_id)
     values (gen_random_uuid(), null, null, 'planted','Human','critical','intrusion','t','t',false,false,'${A}')`);
  check(plantAlert !== null, "B cannot insert an alert owned by A", 'the insert was ALLOWED');

  const anonSel = await asUser('00000000-0000-0000-0000-000000000000', `select id from public.cameras`);
  check(anonSel.length === 0, 'a signed-out / zero-uid session reads no cameras',
    `it read ${anonSel.length} rows`);

  head('EACH USER GETS ITS OWN CAM-001');
  const bIns = await asUser(B, `insert into public.cameras (camera_code,name,stream_url,camera_type,connection_type,enabled,user_id)
     values ('CAM-001','B cam','http://y','ip_camera','http_mjpeg',true,'${B}')
     returning id, camera_code, user_id`);
  check(bIns.length === 1, 'B can create its own CAM-001', 'the insert returned nothing');
  if (bIns.length === 1) {
    info(`B's camera id=${bIns[0].id} code=${bIns[0].camera_code}`);
    const aAfter = await asUser(A, `select count(*)::int as n from public.cameras`);
    check(col(aAfter[0]) === 1, 'A still sees exactly 1 camera after B added one',
      `A sees ${col(aAfter[0])}`);
    const dup = await tryAsUser(B, `insert into public.cameras (camera_code,name,stream_url,camera_type,connection_type,enabled,user_id)
       values ('CAM-001','B dup','http://z','ip_camera','http_mjpeg',true,'${B}')`);
    check(dup !== null, 'B cannot create a SECOND CAM-001 (still unique per user)', 'duplicate was ALLOWED');
  }

  head('SETTINGS ARE PER USER');
  await asUser(A, `insert into public.settings (user_id, confidence_threshold, updated_at)
     values ('${A}', 0.11, now()) on conflict (user_id) do update set confidence_threshold=0.11`);
  await asUser(B, `insert into public.settings (user_id, confidence_threshold, updated_at)
     values ('${B}', 0.99, now()) on conflict (user_id) do update set confidence_threshold=0.99`);
  const sA = await asUser(A, `select confidence_threshold from public.settings`);
  const sB = await asUser(B, `select confidence_threshold from public.settings`);
  check(sA.length === 1 && Number(sA[0].confidence_threshold) === 0.11,
    'A reads back its own settings row', JSON.stringify(sA));
  check(sB.length === 1 && Number(sB[0].confidence_threshold) === 0.99,
    'B reads back its own settings row', JSON.stringify(sB));

  head('ANALYTICS RPCs SCOPE TO ONE ACCOUNT');
  // Browser path: no argument, auth.uid() is used.
  const sumA = await asUser(A, `select get_detection_summary() as j`);
  const sumB = await asUser(B, `select get_detection_summary() as j`);
  const jA = sumA[0].j, jB = sumB[0].j;
  info(`A summary ${JSON.stringify(jA)}`);
  info(`B summary ${JSON.stringify(jB)}`);
  check(jA.total_events === 2, 'A\'s summary counts only A\'s 2 detections', JSON.stringify(jA));
  check(jB.total_events === 0, 'B\'s summary counts nothing yet', JSON.stringify(jB));

  const anA = await asUser(A, `select get_analytics() as j`);
  const anB = await asUser(B, `select get_analytics() as j`);
  const totalA = Object.values(anA[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  const totalB = Object.values(anB[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  check(totalA === 2, 'A\'s analytics aggregates only A\'s detections', `total ${totalA}`);
  check(totalB === 0, 'B\'s analytics excludes every one of A\'s detections', `total ${totalB}`);

  // Now B creates a detection, and the two totals must stay separate.
  await asUser(B, `insert into public.detections (client_event_id,source,camera_id,camera_name,object_name,category,
       severity,confidence,avg_confidence,frame_count,start_time,end_time,duration_seconds,user_id)
     values (gen_random_uuid(),'Live Camera',null,'B cam','cat','Animals','warning',0.7,0.7,10,now(),now(),10,'${B}')`);
  const anA2 = await asUser(A, `select get_analytics() as j`);
  const anB2 = await asUser(B, `select get_analytics() as j`);
  const totalA2 = Object.values(anA2[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  const totalB2 = Object.values(anB2[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  check(totalA2 === 2, 'A\'s analytics is unmoved by B adding a detection', `now ${totalA2}`);
  check(totalB2 === 1, 'B\'s analytics counts exactly its own detection', `now ${totalB2}`);

  // Service-role path: RLS is bypassed, so the explicit p_user_id is the guard.
  //
  // `set role` does not clear the JWT-claim GUC, so it is cleared explicitly
  // here. That is also what really happens in production: a service-role
  // PostgREST request carries the service_role key, which has no user `sub`,
  // so auth.uid() is null and the function fails closed.
  await exec(`reset role; set request.jwt.claim.sub = ''; set role service_role;`);
  const svcAll = await rows(`select get_analytics() as j`);
  const svcA = await rows(`select get_analytics('${A}'::uuid) as j`);
  const svcB = await rows(`select get_analytics('${B}'::uuid) as j`);
  const svcAllTotal = Object.values(svcAll[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  const svcATotal = Object.values(svcA[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  const svcBTotal = Object.values(svcB[0].j.by_category || {}).reduce((a, b) => a + b, 0);
  await exec(`reset role;`);
  check(svcAllTotal === 0, 'service role with NO p_user_id gets nothing (auth.uid() is null)',
    `it returned ${svcAllTotal} events - that would be the global leak`);
  check(svcATotal === 2, 'service role + p_user_id=A returns exactly A\'s 2 events', `${svcATotal}`);
  check(svcBTotal === 1, 'service role + p_user_id=B returns exactly B\'s 1 event', `${svcBTotal}`);

  head('SYSTEM HEALTH RPC IS PER ACCOUNT');
  const hA = await asUser(A, `select get_system_health() as j`);
  const hB = await asUser(B, `select get_system_health() as j`);
  check(hA[0].j.total_cameras === 1, 'A\'s health reports 1 camera', JSON.stringify(hA[0].j));
  check(hB[0].j.total_cameras === 1, 'B\'s health reports 1 camera (its own)', JSON.stringify(hB[0].j));

  head('STORAGE: BUCKET IS PRIVATE AND FOLDERS ARE OWNER-SCOPED');
  const bucket = await rows(`select name, public from storage.buckets where name='snapshots'`);
  check(bucket.length === 1 && bucket[0].public === false,
    'the snapshots bucket is private', `public=${bucket[0] && bucket[0].public}`);
  const spol = await rows(`select policyname, roles::text, qual from pg_policies
    where schemaname='storage' and tablename='objects'`);
  for (const p of spol) info(`storage.objects.${p.policyname} roles=${p.roles} qual=${p.qual}`);
  const own = spol.find(p => /foldername/.test(p.qual || ''));
  check(!!own, 'a storage policy keys on storage.foldername(name)[1]');
  const oldPublic = spol.find(p => p.policyname === 'snapshots_read_public');
  check(!oldPublic, 'the old world-readable policy is gone');

  head('CAMERA_SECRETS STAYS UNREACHABLE FROM A BROWSER');
  const secPol = await rows(`select count(*)::int as n from pg_policies
    where schemaname='public' and tablename='camera_secrets'`);
  check(col(secPol[0]) === 0, 'camera_secrets has no anon/authenticated policy', `${col(secPol[0])} policies`);
  // Put a real secret in first, so "A read 0 rows" proves something rather than
  // passing because the table happens to be empty.
  await exec(`insert into public.camera_secrets (camera_id, password)
    select id, 'super-secret' from public.cameras where user_id='${A}' limit 1
    on conflict do nothing`);
  const secTotal = await rows(`select count(*)::int as n from public.camera_secrets`);
  check(col(secTotal[0]) >= 1, 'a real secret exists to protect',
    `${col(secTotal[0])} secrets present`);
  const secSeen = await asUser(A, `select * from public.camera_secrets`);
  check(secSeen.length === 0, 'A cannot read any camera secret from the browser',
    `A read ${secSeen.length} rows`);

  head('ON DELETE CASCADE CLEANS UP PER USER');
  const before = await rows(`select count(*)::int as n from public.cameras where user_id='${A}'`);
  check(col(before[0]) === 1, 'A has 1 camera before the delete');
  await exec(`delete from auth.users where id='${A}'`);
  const after = await rows(`select count(*)::int as n from public.cameras where user_id='${A}'`);
  check(col(after[0]) === 0, 'deleting A from auth.users removed A\'s cameras',
    `${col(after[0])} remain - orphan rows`);

  // ------------------------------------------------------------------- verdict
  console.log(`\n${B_}${'='.repeat(64)}${X}`);
  if (failures.length === 0) {
    console.log(`${G}${B_}MIGRATION 0005 VALIDATED - ${pass} assertions passed.${X}`);
    console.log(`${G}Per-user isolation holds at the database layer for two real accounts.${X}`);
    process.exit(0);
  }
  console.log(`${R}${B_}${failures.length} FAILURE(S)${X} (${pass} passed):`);
  for (const f of failures) console.log(`  ${R}x${X} ${f}`);
  process.exit(1);
})().catch(e => { console.error('\nHARNESS CRASHED:', e); process.exit(1); });