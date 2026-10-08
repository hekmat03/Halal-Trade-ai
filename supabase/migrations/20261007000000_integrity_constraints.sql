PATH: supabase/migrations/20261007000000_integrity_constraints.sql

-- RunFlow: integrity hardening on top of drizzle/migrations/0000_migration.sql
-- Run in the Supabase SQL editor (or `supabase db push`). Safe to run more than once.
--
-- 1. Every row belongs to a real auth user, and deleting an account deletes its data.
-- 2. The database rejects impossible values even if a client bug sends them.
-- Constraints are added NOT VALID first (enforced for new writes immediately) and then
-- validated, so existing rows are checked too. If a VALIDATE step fails, the error names
-- the constraint; fix or remove the offending rows and run the script again.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_user_id_fkey') THEN
    ALTER TABLE public.profiles
      ADD CONSTRAINT profiles_user_id_fkey
      FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runs_user_id_fkey') THEN
    ALTER TABLE public.runs
      ADD CONSTRAINT runs_user_id_fkey
      FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE NOT VALID;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_values_check') THEN
    ALTER TABLE public.profiles
      ADD CONSTRAINT profiles_values_check CHECK (
        char_length(name) BETWEEN 1 AND 100
        AND weekly_goal > 0 AND weekly_goal <= 1000000
        AND (body_weight IS NULL OR body_weight BETWEEN 20 AND 500)
      ) NOT VALID;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runs_values_check') THEN
    ALTER TABLE public.runs
      ADD CONSTRAINT runs_values_check CHECK (
        distance >= 0 AND duration >= 0 AND average_pace >= 0 AND calories >= 0
        AND ended_at >= started_at
      ) NOT VALID;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'run_route_points_coords_check') THEN
    ALTER TABLE public.run_route_points
      ADD CONSTRAINT run_route_points_coords_check CHECK (
        latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180
        AND (accuracy IS NULL OR accuracy >= 0)
      ) NOT VALID;
  END IF;
END $$;

ALTER TABLE public.profiles
  VALIDATE CONSTRAINT profiles_user_id_fkey;

ALTER TABLE public.runs
  VALIDATE CONSTRAINT runs_user_id_fkey;

ALTER TABLE public.profiles
  VALIDATE CONSTRAINT profiles_values_check;

ALTER TABLE public.runs
  VALIDATE CONSTRAINT runs_values_check;

ALTER TABLE public.run_route_points
  VALIDATE CONSTRAINT run_route_points_coords_check;
