PATH: supabase/tests/rls_isolation.sql

-- RunFlow: proves one user cannot read or change another user's data.
--
-- How to run: paste the whole file into the Supabase SQL editor and run it.
-- Success: the final notice says "ALL RLS CHECKS PASSED".
-- Failure: the run stops with an error starting "FAIL:" that names the broken rule.
-- Everything happens inside a transaction that is rolled back, so no data is kept.

BEGIN;

-- Two throwaway users and one run each (as the table owner, which bypasses RLS).
INSERT INTO auth.users (id, aud, role, email) VALUES
  ('11111111-1111-1111-1111-111111111111', 'authenticated', 'authenticated', 'rls-a@example.test'),
  ('22222222-2222-2222-2222-222222222222', 'authenticated', 'authenticated', 'rls-b@example.test');

INSERT INTO public.profiles (user_id, name) VALUES
  ('11111111-1111-1111-1111-111111111111', 'User A'),
  ('22222222-2222-2222-2222-222222222222', 'User B');

INSERT INTO public.runs (id, user_id, local_id, started_at, ended_at, distance, duration) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'a1', now() - interval '1 hour', now(), 5000, 1500),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'b1', now() - interval '1 hour', now(), 8000, 2400);

INSERT INTO public.run_route_points (run_id, latitude, longitude, "timestamp", accuracy) VALUES
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 51.5000, -0.1000, now(), 5),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 40.7000, -74.0000, now(), 5);

-- From here on, behave exactly like the app: signed in as user A.
SET LOCAL ROLE authenticated;
SELECT set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);
SELECT set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', true);

DO $$
DECLARE n integer;
BEGIN
  IF auth.uid() IS DISTINCT FROM '11111111-1111-1111-1111-111111111111'::uuid THEN
    RAISE EXCEPTION 'FAIL: test setup did not sign in as user A (auth.uid() = %)', auth.uid();
  END IF;

  SELECT count(*) INTO n FROM public.runs;
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL: user A can see % runs, expected only their own 1', n; END IF;

  SELECT count(*) INTO n FROM public.runs WHERE user_id = '22222222-2222-2222-2222-222222222222';
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: user A can read user B''s runs'; END IF;

  SELECT count(*) INTO n FROM public.run_route_points;
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL: user A can see % route points, expected only their own 1', n; END IF;

  SELECT count(*) INTO n FROM public.profiles;
  IF n <> 1 THEN RAISE EXCEPTION 'FAIL: user A can see % profiles, expected only their own 1', n; END IF;

  UPDATE public.runs SET title = 'hacked' WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: user A modified user B''s run'; END IF;

  DELETE FROM public.runs WHERE id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: user A deleted user B''s run'; END IF;

  DELETE FROM public.run_route_points WHERE run_id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: user A deleted user B''s route points'; END IF;

  UPDATE public.profiles SET name = 'hacked' WHERE user_id = '22222222-2222-2222-2222-222222222222';
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: user A modified user B''s profile'; END IF;

  -- Writes that pretend to be user B must be refused outright.
  BEGIN
    INSERT INTO public.runs (user_id, local_id, started_at, ended_at)
    VALUES ('22222222-2222-2222-2222-222222222222', 'evil', now(), now());
    RAISE EXCEPTION 'FAIL: user A inserted a run owned by user B';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;

  BEGIN
    INSERT INTO public.run_route_points (run_id, latitude, longitude, "timestamp")
    VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 0.1, 0.1, now());
    RAISE EXCEPTION 'FAIL: user A added a route point to user B''s run';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;

  BEGIN
    INSERT INTO public.profiles (user_id, name) VALUES ('22222222-2222-2222-2222-222222222222', 'evil');
    RAISE EXCEPTION 'FAIL: user A created a profile for user B';
  EXCEPTION WHEN insufficient_privilege OR unique_violation THEN NULL;
  END;
END $$;

-- Signed-out visitors (anon key only) must get nothing at all.
RESET ROLE;
SET LOCAL ROLE anon;
SELECT set_config('request.jwt.claims', '{"role":"anon"}', true);

DO $$
DECLARE n integer;
BEGIN
  BEGIN
    SELECT count(*) INTO n FROM public.runs;
    IF n <> 0 THEN RAISE EXCEPTION 'FAIL: anonymous visitor can read % runs', n; END IF;
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    SELECT count(*) INTO n FROM public.run_route_points;
    IF n <> 0 THEN RAISE EXCEPTION 'FAIL: anonymous visitor can read % route points', n; END IF;
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;

RESET ROLE;
DO $$ BEGIN RAISE NOTICE 'ALL RLS CHECKS PASSED'; END $$;

ROLLBACK;
