-- Exercise 2: do supports move more or less than mids?
--
-- Mistakes along the way:
--   - "near ROUND: syntax error": a missing comma after `AS left_n`.
--     The error names the word AFTER the mistake.
--   - Use 100.0, not 100: integer division truncates (47 / 85 = 0).
--
-- Result (2023-2025 offseasons, labeled players):
--   Support 60.9% (53/87), Bot 55.3%, Jungle 52.9%, Mid 52.8%, Top 50.0% (40/80)
--   Supports leave 8.1 points more often than mids, the most of any role;
--   tops leave the least.
--
-- Is it real? Standard error per role is ~5.4 points at n~85, so the gap between
-- two roles has ~7.6 points of noise. Support vs Mid is ~1.1x that and Support vs
-- Top ~1.4x; a real difference usually needs ~2x. Team-clustered outcomes make
-- the true noise even larger.
-- Conclusion: consistent with chance. Don't add `role` as a model feature on
-- this evidence; it would mostly learn noise.

SELECT
  role,
  COUNT(*) AS n,
  SUM(outcome != 'stayed') AS left_n,
  ROUND(100.0 * SUM(outcome != 'stayed') / COUNT(*), 1) AS pct_left
FROM offseason_outcomes
WHERE outcome != 'pending'
GROUP BY role
ORDER BY pct_left DESC;

-- Same, with a 95% range for each role (normal approximation).
-- Heavily overlapping ranges = the roles can't be told apart with this data.
SELECT
  role,
  ROUND(100.0 * p, 1) AS pct_left,
  ROUND(100.0 * (p - 1.96 * SQRT(p * (1 - p) / n)), 1) AS low,
  ROUND(100.0 * (p + 1.96 * SQRT(p * (1 - p) / n)), 1) AS high
FROM (
  SELECT role, COUNT(*) AS n, 1.0 * SUM(outcome != 'stayed') / COUNT(*) AS p
  FROM offseason_outcomes
  WHERE outcome != 'pending'
  GROUP BY role
)
ORDER BY pct_left DESC;
