-- ============================================================
--  Fair Line — seed data (FOR TESTING the live fetch)
--  Run in: Supabase SQL Editor.  Safe to re-run (cleans first).
--  Later the Python pipeline writes these tables automatically.
-- ============================================================

-- start clean so re-running doesn't duplicate
delete from public.match_details;
delete from public.matches;

-- ---- FREE tier: the matches + base market (headline odds) ----
insert into public.matches (sport, sport_label, league, match_date, home, away, base) values
('foci','Football','Premier League','Sun 16:30','Arsenal','Tottenham',
 '[{"name":"Match result (1X2)","grid":"c3","outs":[{"k":"Home (1)","p":0.48},{"k":"Draw (X)","p":0.26},{"k":"Away (2)","p":0.26}]}]'::jsonb),
('foci','Football','La Liga','Sun 21:00','Real Madrid','Sevilla',
 '[{"name":"Match result (1X2)","grid":"c3","outs":[{"k":"Home (1)","p":0.63},{"k":"Draw (X)","p":0.22},{"k":"Away (2)","p":0.15}]}]'::jsonb),
('tenisz','Tennis','ATP','Sun 14:00','Alcaraz','Sinner',
 '[{"name":"Match winner","grid":"c2","outs":[{"k":"Alcaraz","p":0.54},{"k":"Sinner","p":0.46}]}]'::jsonb);

-- ---- PAID tier: full model internals (drives all markets + the "why" layer) ----
-- Football: lam/mu (expected goals) — the frontend computes all markets + reasoning from these.
insert into public.match_details (match_id, lam, mu, insight_key, insight_vars)
select id, 1.70, 1.25, 'group_stage', '{"lam":"1.70","mu":"1.25"}'::jsonb
from public.matches where home='Arsenal' and away='Tottenham';

insert into public.match_details (match_id, lam, mu, insight_key, insight_vars)
select id, 2.00, 0.80, 'group_stage', '{"lam":"2.00","mu":"0.80"}'::jsonb
from public.matches where home='Real Madrid' and away='Sevilla';

-- Tennis: no expected goals — store the extra market directly.
insert into public.match_details (match_id, extra, insight_key, insight_vars)
select id,
 '[{"name":"Total games (over/under 22.5)","grid":"c2","outs":[{"k":"Over 22.5","p":0.53},{"k":"Under 22.5","p":0.47}]}]'::jsonb,
 'tennis', '{}'::jsonb
from public.matches where home='Alcaraz' and away='Sinner';
