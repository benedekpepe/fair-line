-- ============================================================
--  Fair Line — Supabase schema (COMPLETE, single file)
--  Run in: Supabase Dashboard -> SQL Editor -> New query -> Run
--  Safe to re-run (idempotent where possible).
-- ============================================================

-- ------------------------------------------------------------
-- 1) PROFILES — one row per user, holds the subscription state
--    and the sign-up fields (name / 18+ / terms acceptance).
-- ------------------------------------------------------------
create table if not exists public.profiles (
  id                  uuid primary key references auth.users (id) on delete cascade,
  email               text,
  full_name           text,                            -- captured at sign-up
  age_confirmed       boolean not null default false,  -- 18+ checkbox at sign-up
  terms_accepted_at   timestamptz,                     -- when the user accepted the Terms
  subscription_status text not null default 'free',    -- 'free' | 'active' | 'canceled'
  subscription_tier   text,                            -- e.g. 'pro' (nullable)
  current_period_end  timestamptz,                     -- when the paid period ends
  portal_url          text,                            -- Lemon Squeezy customer-portal URL ("Manage subscription")
  created_at          timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- a user may read and update only their OWN profile
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select using (auth.uid() = id);

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update using (auth.uid() = id);

-- auto-create a profile row whenever a new auth user signs up,
-- copying the sign-up metadata (full_name / 18+ / terms) from options.data
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email, full_name, age_confirmed, terms_accepted_at)
  values (
    new.id,
    new.email,
    new.raw_user_meta_data->>'full_name',
    coalesce((new.raw_user_meta_data->>'age_confirmed')::boolean, false),
    case when (new.raw_user_meta_data->>'terms_accepted')::boolean then now() else null end
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- helper: is the CURRENT user an active subscriber?
create or replace function public.is_subscriber()
returns boolean
language sql
stable
security definer set search_path = public
as $$
  select exists (
    select 1 from public.profiles p
    where p.id = auth.uid()
      and p.subscription_status = 'active'
      and (p.current_period_end is null or p.current_period_end > now())
  );
$$;

-- ------------------------------------------------------------
-- 2) MATCHES — FREE tier: the basic card + base outcome
--    (1X2 for football, match winner for others) + headline market odds.
--    Readable by everyone, including anonymous visitors.
-- ------------------------------------------------------------
create table if not exists public.matches (
  id          bigint generated always as identity primary key,
  sport       text not null,            -- internal key: 'foci' | 'tenisz' | ...
  sport_label text not null,            -- 'Football' (English base; frontend localises)
  league      text not null,
  match_date  text,                     -- e.g. 'Sun 16:30' (weekday localised on frontend)
  home        text not null,
  away        text not null,
  base        jsonb not null,           -- base market: [{ "k": "Home (1)", "p": 0.48 }, ...]
  kickoff     timestamptz,              -- precise UTC kickoff (frontend shows local time, hides past games)
  mkt         jsonb,                    -- real 1X2 market odds, e.g. {"1":1.85,"X":3.6,"2":4.2}  (value chip)
  mkt_ou      jsonb,                    -- real Over/Under 2.5 odds, e.g. {"line":2.5,"over":1.95,"under":1.85}
  mkt_ah      jsonb,                    -- real Asian-handicap odds, e.g. {"line":-1.5,"home":1.95,"away":1.85}
  updated_at  timestamptz not null default now(),
  unique (sport, league, home, away, match_date)
);

alter table public.matches enable row level security;

-- free cards are public
drop policy if exists matches_public_read on public.matches;
create policy matches_public_read on public.matches
  for select using (true);

-- ------------------------------------------------------------
-- 3) MATCH_DETAILS — PAID tier: all extra markets + model internals
--    (powers the extra markets, the "why" layer and the combo finder)
--    Readable ONLY by active subscribers (enforced in the DB).
-- ------------------------------------------------------------
create table if not exists public.match_details (
  match_id     bigint primary key references public.matches (id) on delete cascade,
  lam          numeric,                 -- football expected goals (home)
  mu           numeric,                 -- football expected goals (away)
  extra        jsonb,                   -- [{ "name": "...", "grid": "c3", "outs": [{k,p}] }, ...]
  insight_key  text,                    -- structured insight id, e.g. 'group_stage' | 'tennis'
  insight_vars jsonb                    -- { "lam": 1.7, "mu": 1.25 }  (frontend formats per language)
);

alter table public.match_details enable row level security;

-- only active subscribers can read the full detail
drop policy if exists details_subscriber_read on public.match_details;
create policy details_subscriber_read on public.match_details
  for select using (public.is_subscriber());

-- ------------------------------------------------------------
-- NOTE on writes:
--   The data pipeline writes with the SERVICE ROLE key, which bypasses RLS —
--   so no INSERT/UPDATE policies are needed here.
--   Never expose the service role key in the frontend; only the anon key.
-- ------------------------------------------------------------
