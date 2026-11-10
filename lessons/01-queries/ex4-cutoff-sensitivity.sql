-- Exercise 4: how much do the labels depend on the Feb 1 cutoff?
--
-- Every label answers "which team was the player on, on Feb 1?". That date is a
-- judgment call, so test it: rebuild with another date and compare.
--
-- Procedure (needs a rebuild, so it's steps rather than one query):
--   cp data/ground_truth.db data/gt_feb1.db
--   # edit src/rift_rumors/data/ground_truth.py: CUTOFF_MONTH_DAY = "01-01"
--   uv run rift-rumors ground-truth           # 0 API requests: disk cache
--   sqlite3 -header -box data/ground_truth.db  # then run the queries below
--   # UNDO afterwards:
--   git restore src/rift_rumors/data/ground_truth.py && uv run rift-rumors ground-truth && rm data/gt_feb1.db
--
-- Result: 33 of 445 labels change (~1 in 14) with Jan 1. With Mar 1: 13 change.
--   24 x moved -> no_team. E.g. Jankos left Heretics 2024-11-13 and joined Rogue
--        2025-01-16: on Jan 1 he was between teams. January is when new signings
--        become official, so an earlier cutoff catches players mid-transfer.
--   moved -> stayed. E.g. Taeyoon was sent down to Kwangdong Freecs' own ACADEMY
--        on 2024-01-22. Open question: is a demotion to your org's academy a "move"?
--   NEW BUG: Finn's Feb 1 label is "moved" to "LoL EMEA Championship", which is
--        the league itself, not a team (a non-team role logged by Leaguepedia).
--        4 labels have this. He was really teamless until Rogue on 2024-02-15.
--
-- What it means:
--   ~7% of labels depend on an arbitrary date, so "v0 beats the base rate by
--   0.036 leave-Brier" should be re-checked under other cutoffs (robustness check).
--   A better definition than any date: the team the player played for in the
--   FIRST OFFICIAL MATCH of the next season (event-based, not calendar-based).
--   Until then, Feb 1 sits in a more stable spot (Mar 1 moves 13 labels, Jan 1 moves 33).
--
-- FIXED (2026-11-10, lesson 01 section 8b): labels now use the day the league's
-- next season starts (`cutoff`), and only PLAYING stints count. The season-start
-- cutoff differs from Feb 1 on just 3 labels. `outcome_feb1` is kept because v0's
-- grading was pre-registered on Feb 1.

-- After the rebuild with "01-01": which labels changed?
ATTACH 'data/gt_feb1.db' AS feb;

SELECT n.year, n.player, f.outcome AS feb_1, n.outcome AS jan_1, n.team_after
FROM offseason_outcomes AS n
JOIN feb.offseason_outcomes AS f
  ON f.year = n.year AND f.player = n.player
WHERE n.outcome != f.outcome;

-- Count by kind of change
SELECT f.outcome AS feb_1, n.outcome AS jan_1, COUNT(*) AS n
FROM offseason_outcomes AS n
JOIN feb.offseason_outcomes AS f
  ON f.year = n.year AND f.player = n.player
WHERE n.outcome != f.outcome
GROUP BY f.outcome, n.outcome
ORDER BY n DESC;

-- The league-as-a-team bug (works on the normal database, no rebuild needed)
SELECT year, player, team_before, team_after
FROM offseason_outcomes
WHERE team_after LIKE '%Championship%';
