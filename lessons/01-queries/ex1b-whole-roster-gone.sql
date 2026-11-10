-- Follow-up to exercise 1: which teams had NOBODY stay, in any offseason?
--
-- Most are real org exits (TSM, Evil Geniuses, Golden Guardians in 2023;
-- NRG, Immortals in 2024). But two are LABEL BUGS, rebrands counted as moves:
--   2023 Liiv SANDBOX -> BNK FEARX: 3 players labeled "moved". We had the rename
--        Liiv SANDBOX -> FearX, but not FearX -> BNK FEARX, so the chain broke.
--   2024 Kwangdong Freecs -> DN Freecs: no rename recorded at all.
-- Cause: renames came from the single `Teams.RenamedTo` field. Fix (pending):
-- use Leaguepedia's dated `TeamRenames` table instead.
--
-- Open labeling question (my call, as the domain expert): is staying through a
-- MERGER (Excel -> GIANTX) or a SLOT SALE (Astralis) "stayed" or "moved"?
--
-- The second query shows where each such team's players went.

WITH team_years AS (
  SELECT year, league, team_before,
         COUNT(*) AS n,
         SUM(outcome = 'stayed') AS stayed
  FROM offseason_outcomes
  WHERE outcome != 'pending'
  GROUP BY year, league, team_before
)
SELECT year, league, team_before, n AS players_lost
FROM team_years
WHERE stayed = 0
ORDER BY year, league, team_before;

SELECT o.year, o.team_before, o.team_after, COUNT(*) AS n,
       (SELECT new_name FROM team_renames r WHERE r.old_name = o.team_before) AS rename_recorded
FROM offseason_outcomes o
WHERE (o.year, o.team_before) IN (
  SELECT year, team_before FROM offseason_outcomes
  WHERE outcome != 'pending'
  GROUP BY year, team_before
  HAVING SUM(outcome = 'stayed') = 0
)
GROUP BY o.year, o.team_before, o.team_after
ORDER BY o.year, o.team_before, n DESC;
