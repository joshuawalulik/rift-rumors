-- Exercise 6: the live board, 2026 offseason (as of 2026-11-10)
-- Players whose contract expires this year AND who have no roster change yet:
-- the most open seats, and where a smarter v1 should focus first.
--
-- Gotcha: use `IS NULL`, never `= NULL`. In SQL, `x = NULL` is never true, so it
-- silently matches nothing.
--
-- Prediction before querying: Doran, Zeus, Kanavi would be on it.
-- Result: 69 players. Doran (T1), Zeus and Kanavi (HLE) are all on it.
--   Five teams have ALL 5 starters open: BNK FEARX, Nongshim RedForce (LCK),
--   Disguised, Shopify Rebellion (LCS), GIANTX (LEC). That's 25 of the 69 seats.
--
-- My own calls (written 2026-11-10, before outcomes are known):
--   ShowMaker (Dplus Kia)  -> STAYS   (v0: 66% leave, so we disagree)
--   Sharvel   (DN SOOPers) -> MOVES   (v0: 66% leave)
--   Doran     (T1)         -> MOVES   (v0: 66% leave)
-- v0 gives every expiring LCK player the same 66%; it can't tell ShowMaker from
-- a rookie. My calls use what contracts don't show (tenure, status, public talk).
-- That gap is what v1 (stats + rumors) has to close.

SELECT league, team_before, player, role, contract_end
FROM offseason_outcomes
WHERE year = 2026
  AND contract_end <= '2026-12-31'
  AND first_move_date IS NULL
ORDER BY league, team_before;

-- Bonus: teams with the most open seats
SELECT league, team_before, COUNT(*) AS open_seats
FROM offseason_outcomes
WHERE year = 2026
  AND contract_end <= '2026-12-31'
  AND first_move_date IS NULL
GROUP BY league, team_before
ORDER BY open_seats DESC, league, team_before;
