-- Exercise 5: how fresh is Riot's contract database, region by region?
--
-- Result (as of 2026-11-10): each tab has its own "last updated" stamp.
--   KR    Nov 3, 2026   (freshest)
--   AME   Oct 27, 2026
--   EMEA  Sep 9, 2026   (two months stale, before Worlds and the offseason)
--
-- Q1: Would a European contract signed last week show up?  No. The EMEA tab
--     hasn't been updated since Sep 9, and most offseason moves happen after Worlds.
-- Q2: Which region to trust least?  EMEA. KR the most.
--     What we can do about it, given we can't make Riot update the sheet:
--       - recency rule (built): a Leaguepedia announcement dated after the tab's
--         update wins over the stale sheet (the Lyncas case)
--       - Leaguepedia roster changes are fresher; the freeze uses them to flag
--         players whose move is already public
--       - every build saves a snapshot, so diffing snapshots shows WHEN a tab
--         updates and what changed (a batch of changes is itself a signal)
--       - record staleness (built): the v0 freeze metadata stores each tab's stamp
--       - rumors (Lesson 03) are the freshest source and fill exactly this gap
--
-- GOTCHA found here: Datasette showed 190/350/160 rows, but the real database has
-- 380/699/320 (two snapshots). Every rebuild deletes and rewrites the .db file,
-- and a running Datasette keeps reading the old deleted file. Restart Datasette
-- after every rebuild. (The sqlite3 CLI reopens the file each time, so it's fine.)

SELECT tab, sheet_updated,
       COUNT(*) AS rows,
       COUNT(DISTINCT snapshot_at) AS snapshots
FROM gcd_contracts
GROUP BY tab, sheet_updated
ORDER BY tab;
