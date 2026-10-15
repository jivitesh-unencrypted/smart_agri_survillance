# Migration / isolation tests

`isolation_harness.js` runs the project's **real** migration files
(`../migrations/0001` … `0005`) against a **real Postgres engine** and then
asserts that per-user data isolation actually holds for two accounts.

It exists because the only real database available is a live Supabase project
you cannot safely mutate, and because "the SQL looks right" is not the same
claim as "the SQL runs and isolates data".

## What engine this uses

[PGlite](https://pglite.dev) — Postgres compiled to WebAssembly, running
in-process in Node. It is the real Postgres query planner, RLS implementation,
plpgsql engine and role system, so RLS policies behave exactly as they do on
Supabase. It is **not** a mock and it **not** stub out any of the SQL under
test.

Two things are bootstrapped before the migrations run, because they are
platform features Supabase provides rather than things a migration creates:

| Bootstrapped | Mirrors |
| --- | --- |
| `anon`, `authenticated`, `service_role` roles + default table grants | Supabase's default privileges |
| `auth.users` + `auth.uid()` reading the JWT claim GUC | Supabase Auth |
| `storage.buckets`, `storage.objects`, `storage.foldername()` | Supabase Storage |

Everything the migrations actually create — tables, owner columns, indexes,
RLS policies, the private bucket policy, the analytics RPCs — is created by
your own `.sql` files, unmodified.

## Running it

```bash
cd supabase/tests
npm install          # only dependency is @electric-sql/pglite (~24 MB)
npm test
```

Exit code `0` means every assertion held. Exit `1` prints each failure.

Point it at a different migrations folder if you like:

```bash
node isolation_harness.js /path/to/other/migrations
```

## What it asserts

The harness deliberately starts from the state your live database is in
**right now**: it applies `0001`–`0004`, seeds rows with no owner column
(1 camera, 2 detections, 2 alerts, 1 job, the single global `settings` row
with `id = 1`), proves that state really *is* shared between two accounts,
and only then applies `0005`. That ordering is what makes a later pass
meaningful — it proves the migration is what fixed the leak, not the seed.

- migrations `0001`–`0005` all apply without error
- the legacy backfill gives every pre-existing row an owner, and assigns them
  to the earliest-created profile
- no `using (true)` policy survives on any user-owned table
- as the `authenticated` role, B sees **0 rows** in every user-owned table
  while A still sees its own
- cross-user `UPDATE` / `DELETE` leaves rows untouched
- cross-user `INSERT` (planting a row owned by someone else) is refused
- each user can own its own `CAM-001`, and still only one
- `settings` is one row per user, primary key `user_id`
- analytics RPCs return each account's own numbers, and stay separate when
  the other account adds data
- the service role with **no** `p_user_id` gets nothing (fails closed);
  with `p_user_id` it gets exactly that user's data
- `camera_secrets` is unreadable from a browser even when it holds a secret
- the snapshots bucket is private and its read policy keys on
  `storage.foldername(name)[1]`
- deleting a row from `auth.users` cascades away that user's cameras

## Note on two Postgres behaviours this harness relies on

**A cross-user `UPDATE` does not raise.** RLS hides the row from the
attacker's snapshot, so the statement matches nothing and affects 0 rows
without error. Asserting "it returned an error" would therefore be a *false*
pass. Every write check in this harness asserts the resulting **row state**
instead, which is the property that actually matters.

**`SET ROLE` does not clear the JWT claim.** The harness clears
`request.jwt.claim.sub` explicitly before simulating a service-role request,
because a real service-role PostgREST request carries no user `sub` — which
is exactly why the RPCs fail closed.