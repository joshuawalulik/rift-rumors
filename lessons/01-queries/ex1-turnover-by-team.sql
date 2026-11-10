-- Exercise 1: which team had the most players leave in the 2024 offseason?
-- (year = the season that just ended, so 2024 = the 2024 -> 2025 offseason)
--
-- First attempt (WRONG): no GROUP BY.
--   SELECT team_before, COUNT(*) AS left_count, GROUP_CONCAT(player, ', ')
--   FROM offseason_outcomes WHERE year = 2024 AND outcome != 'stayed';
-- Returned one row: "Team BDS | 75". 75 is every 2024 leaver across all teams;
-- "Team BDS" was just an arbitrary row's value. SQLite allows that silently.
--
-- Result: NRG and Immortals (LCS) lost 5 each, the entire roster. Both orgs
-- left the league after 2024. Six teams tie next with 4: Team Heretics,
-- Shopify Rebellion, Rogue (EU), OKSavingsBank BRION, Nongshim RedForce, Dignitas.
--
-- What it means for the model:
--   1. Some outcomes are decided by the ORG, not the player (league exits,
--      slot sales, disbanding). Contract status can't see that, but news about
--      the org can -> a candidate v1 feature.
--   2. Five players from one exiting org aren't five independent examples.
--      Outcomes are clustered by team, so the backtest's effective sample size
--      is smaller than its player count.

SELECT
  team_before,
  league,
  COUNT(*) AS left_count,
  GROUP_CONCAT(player, ', ') AS players
FROM offseason_outcomes
WHERE year = 2024
  AND outcome != 'stayed'
GROUP BY team_before
ORDER BY left_count DESC
LIMIT 10;
