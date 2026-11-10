-- Exercise 3: find a label you know is wrong, then trace WHY.
--
-- What I noticed (2025 offseason, LEC):
--   1. G2 Esports has only 3 players in offseason_outcomes; the real roster is 5.
--   2. "Hans SamD" looks like a renamed player.
--   3. A team shows up as "LYON (2024 American Team)".
--
-- What the trace found:
--   1. PLAYER IDENTITY BUG (systemic). The 2025 roster lists Hans Sama and
--      BrokenBlade, but `tenures` stores Hans as "Hans sama" (lowercase s), and
--      BrokenBlade has no tenure rows at all. The pipeline joins names exactly,
--      so both were dropped as "left mid-season". 37 players have case-mismatched
--      names across tables (Kingen/kingen, Perkz/PerkZ, Labrov/labrov, ...) and
--      4 have no tenures. So many of Lesson 01's 64 "legitimate" exclusions are
--      really this bug.
--   2. Hans SamD (NAVI) is a DIFFERENT player from Hans Sama (G2). He IS the
--      former "SamD": SamD's career ends at HLE in Nov 2022, and Hans SamD's
--      starts at Estral in Dec 2022. One career, two names. Old tables keep old names.
--      FIXED (2026-11-10): every name now resolves through Leaguepedia's
--      `PlayerRedirects` table (all names -> one canonical page), case-insensitively.
--      BrokenBlade's stints turned out to be under the alias "BrokenBIade"
--      (capital I for lowercase l). Effect: 26 players recovered, 14 labels
--      corrected, exclusions 64 -> 41. See lesson 01, section 8.
--   3. "LYON (2024 American Team)" is a page-name disambiguator, not a data bug.
--      Display issue only; show the team's short name instead.
--
-- Lesson: a filter that "excludes bad rows" can hide a join bug. Always check
-- WHO got excluded against what you know.

-- NOTE: queries 1b/1c found the bug in the ORIGINAL database. After the fix,
-- every `player` column holds canonical names, so they now return few or no rows.
-- That's the fix working. Query 4 shows the alias table that resolves them.

-- 1a. G2's 2025 second-half roster vs who made it into the labels (now 5 of 5)
SELECT r.player, r.role,
       (SELECT outcome FROM offseason_outcomes o
         WHERE o.year = 2025 AND o.player = r.player) AS label
FROM tournament_rosters r
JOIN tournaments t ON t.page = r.tournament
WHERE r.team = 'G2 Esports' AND t.page = 'LEC/2025 Season/Summer Season'
ORDER BY r.role;

-- 1b. Every roster name whose tenures exist only under different capitalization
SELECT DISTINCT r.player AS roster_name, t.player AS tenure_name
FROM tournament_rosters r
JOIN tenures t ON LOWER(t.player) = LOWER(r.player) AND t.player != r.player
ORDER BY 1;

-- 1c. Roster players with no tenure rows under ANY capitalization
SELECT DISTINCT r.player
FROM tournament_rosters r
WHERE NOT EXISTS (SELECT 1 FROM tenures t WHERE LOWER(t.player) = LOWER(r.player));

-- 2. Hans Sama vs Hans SamD: different careers, so different people
SELECT player, team, date_join, date_leave
FROM tenures
WHERE LOWER(player) IN ('hans sama', 'hans samd', 'samd')
ORDER BY player, date_join;

-- 4. After the fix: the aliases that resolve these players to one identity
SELECT player AS canonical, alias
FROM player_aliases
WHERE player IN ('Hans Sama', 'SamD', 'BrokenBlade', 'Execute', '3XA')
ORDER BY player, alias;
