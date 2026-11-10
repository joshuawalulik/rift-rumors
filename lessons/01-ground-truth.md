# Lesson 01: Ground truth from Leaguepedia

**Goal:** build the labels every prediction will be graded against. For every LCK, LEC and LCS player on a roster at the end of a season: where were they when the next season started?
**Ends with:** `data/ground_truth.db` (SQLite) holding 582 player-seasons. 445 are labeled backtest examples (2023–2025 offseasons) and 137 are pending for the live 2026 offseason. Plus the first real finding about what predicts a move.

> Numbers below reflect the **identity fix** (§8), made after exercise 3 found players missing from G2. Before it: 559 rows, 426 labeled.

```bash
uv run rift-rumors ground-truth   # first run ~90 API requests; later runs 0 (disk cache)
sqlite3 data/ground_truth.db      # explore it yourself
```

---

## 1. Why ground truth comes first

Every number this project reports ("68% accurate", "Brier 0.18") is a comparison against a label. If a label is wrong, every metric built on it is wrong, and you won't be able to tell. So before any model or LLM, we build the answer key and audit it by hand. In ML terms, that's the **evaluation set**.

## 2. The source: Leaguepedia's Cargo tables

Leaguepedia (lol.fandom.com) stores esports data in **Cargo** tables, queryable through an SQL-like API (`tables`, `fields`, `where`, `join_on`). The ones we use:

| Table | What it gives us | Used for |
|---|---|---|
| `Tournaments` | Every event, with league, dates, playoffs flag | Finding each season's second half |
| `TournamentPlayers` | Who was on each team's roster per event | Defining the player population |
| `TenuresUnbroken` | One row per player-team stint: join date, leave date | **The labels** |
| `RosterChanges` | Every individual move, dated | When a move became known |
| `Contracts` | Contract end dates, each tied to a dated news item | Point-in-time contract status |
| `PlayerRedirects` | Every name a player has used → one canonical page | Player identity (§8) |
| `TeamRenames` | Dated renames, rebrands, acquisitions | Not mistaking a rebrand for a move (§8) |
| Riot **GCD** (Google Sheet, not Leaguepedia) | Official current contracts | Cross-check, plus live contract status (§7) |

## 3. Access: what went wrong, and the fix

1. **We got rate-limited after ~10 exploratory requests**, and stayed blocked for 25+ minutes despite exponential backoff.
2. **Diagnosis:** one cheap request to the normal API worked, while a one-row data query was refused. So the block applied only to anonymous data queries, not to our IP. Leaguepedia's help page confirms that anonymous Cargo queries are "practically impossible".
3. **Fix:** log in with a **bot password** that has only the Cargo permission (least privilege: the bot can read data but can't edit the wiki). Credentials live in `.env`, never in code.

The client (`src/rift_rumors/data/leaguepedia.py`) layers four protections:
- **Login**, only on a cache miss.
- **Throttling**: at least 3 s between requests.
- **Backoff**: 30 s → 60 s → 120 s… on rate-limit errors.
- **A disk cache** keyed by a hash of the query.

History never expires from the cache; current-offseason data refreshes every 6 h. The result: the first build cost 46 requests, and every rebuild since has cost **zero**. The raw responses are kept, so any number we report can be traced back to the exact data behind it.

> Lesson: **your data source's rules can change under you.** Cache everything you fetch, so you depend on access once per piece of data, not once per run.

## 4. Defining the population: two bugs caught by looking at the output

**Bug 1: regex too loose.** Matching leagues with `r"Championship Series"` also matched the *Vietnam* and *Pacific* Championship Series, so "LCS 2023" briefly meant VCS. Fix: an exact allow-list of league names. It's boring and it's correct.

**Bug 2: playoffs-only rosters.** "Last tournament of the year" is the playoffs, and playoffs only include the top teams. LEC "Season Finals" has 4 teams. That would silently drop every non-playoff team's players, *the players most likely to move*. That's a biased sample. Fix: take every tournament from June 1 on (summer split, groups, playoffs). A player's season-end team is the team in the **latest** event they appeared in.

**Then filter:** keep a player only if their stint on that team was still active on the league's last match day. That now excludes **41** player-seasons: short-term subs whose stint ended mid-season (Poby: T1 from 2023-07-05 to 08-01), and academy players called up whose stint is on the academy team (Feisty: listed under KT Rolster, stint on KT Rolster Challengers). Neither is an *offseason move from a main roster*.

> ⚠️ The first version excluded **64**, and I called them all legitimate. They weren't. Exercise 3 found Hans Sama missing from G2, and the trace showed ~20 of those 64 were **identity bugs**, not departures (§8). A filter that "removes bad rows" can hide a join bug. Always check *who* got excluded.

## 5. The labels

For each player, `outcome` describes where they were on **February 1** of the next year (`src/rift_rumors/data/outcomes.py`):

- `stayed`: kept their seat. Staying through a rename, rebrand, **acquisition or merger** counts (decided 2026-11-10: same seat, new owner).
- Only **playing** stints count. Joining the broadcast desk, coaching staff or streaming team isn't a team to play for (§8b).
- **When:** on the day the player's league starts its **next season** (`cutoff`; LCK ~Jan 15, LEC ~Jan 18, LCS ~Jan 24). `outcome_feb1` keeps the original Feb 1 rule, which v0's grading was pre-registered on.
- `moved` carries a `move_type`: `transfer`, or `demotion` (sent down to your own org's academy).
- `moved`: a different team. If the player is listed on two teams, the continuing team wins, otherwise the most recent join.
- `no_team`: no active stint (free agent, retired, other role). This is a real outcome to predict, not missing data.
- `pending`: the 2026 offseason, which hasn't happened yet.

Spot check against offseasons I know: Canyon DK → Gen.G (2023), Kiin KT → Gen.G (2023), Peanut Gen.G → HLE (2023), Doran Gen.G → HLE (2023) → T1 (2024), Zeus T1 → HLE (2024), Faker stays every year. **All correct.**

## 6. ⚠️ Temporal leakage: the Faker contract

The first build showed Faker's contract at the end of **2023** ending in **2029**. That deal was announced in **November 2025**. `TenuresUnbroken.ContractEnd` stores a stint's *latest* contract, not the one known at the time.

A model given that column would "know" who re-signs before it happens. It would score brilliantly on the backtest and fail live. That's **temporal leakage**, the most common way forecasting projects fool themselves.

**Fix:** every row in `Contracts` has a dated `news_id` (`News/2022-11-29_28`). `contract_end_as_of(player, team, date)` uses only announcements published **on or before** the as-of date, and skips undated ones because we can't prove they were known. Faker at the end of 2023 → **2025-11-17**, which is correct. Tests cover this exact case with the real rows.

> Rule for the rest of the project: **every feature is computed "as of" a date, and that date is `season_end`.** Lesson 06 adds a test that fails if any feature row is dated after it.

## 7. Cross-checking against Riot's Global Contract Database

Riot publishes the official **Global Contract Database (GCD)** as a public Google Sheet, one tab per region. `src/rift_rumors/data/gcd.py` reads the AME, EMEA and KR tabs as CSV. Four things shaped how we use it:

1. **It's a snapshot, not a history.** It shows contracts *today*, with no record of 2023. So it **can't** feed the backtest, but it's the authority for the live offseason. Since the source keeps no history, we keep it ourselves: every fetch is saved to `data/raw/gcd/<UTC timestamp>/`, so future seasons get genuine point-in-time contract data.
2. **Each tab is updated separately.** On Nov 10, 2026: Korea was updated Nov 3, Americas Oct 27, and **EMEA Sep 9**, two months stale. Every row carries its tab's `sheet_updated` stamp, so staleness is visible instead of silent.
3. **The layouts differ by region** (`Position` vs `Main Role` vs no role column at all; extra columns in different places). The parser finds columns **by header name**, not position, and each layout has a test.
4. **It contains personal data we don't need**: legal names and team staff emails. The parser keeps an allow-list of columns, and a test asserts no name or email ever reaches disk. *Collect only what the task needs.*

**Leaguepedia vs GCD, for the 2026 population (contract end as known today):**

| Result | Players |
|---|---|
| Both have a date and they **agree** | **120 / 121 (99%)** |
| GCD has a date, Leaguepedia doesn't | 8 |
| Disagree | 1 |

- **That validates the backtest.** The 2023–25 contract features can only come from Leaguepedia's dated announcements. Leaguepedia matching the official source 99% of the time today is strong evidence those historical values are trustworthy too.
- **The one disagreement:** Lyncas (Vitality). Leaguepedia says Nov **26** from an announcement in May 2024, while the GCD tab (updated Sep 2026) says Nov **16**, like every other LEC contract. Almost certainly a typo on Leaguepedia. Rule for conflicts: **the more recent evidence wins.** For live features: use the GCD, fall back to Leaguepedia, unless Leaguepedia has an announcement dated after the GCD tab's last update.

## 8. Identity: who is who (found in exercise 3)

Exercise 3 asked: find a label you know is wrong. **G2's 2025 roster showed 3 players instead of 5.** Tracing it found three identity problems in Leaguepedia's tables, which no test had caught:

| Problem | Example | Scale |
|---|---|---|
| Player names differ in **capitalization** between tables | Roster: `Hans Sama`; stints: `Hans sama` | 37 players |
| Players **rename themselves** and old tables keep the old name | `SamD` → `Hans SamD`; `JeongHoon` → `Execute`; `Exakick` → `3XA` | several |
| Stints recorded under a **typo alias** | BrokenBlade's stints are under `BrokenBIade`: capital **I** for lowercase **l** | 1 (that we know of) |
| Team renames **incomplete** (`Teams.RenamedTo` had no dates and missed links) | `FearX → BNK FEARX`, `Kwangdong Freecs → DN Freecs` missing | dozens of links |

Exact-match joins silently dropped those players, or mislabeled their outcomes.

**Fix** (`src/rift_rumors/data/identity.py`):
- **Players:** every name resolves through `PlayerRedirects` (every alias → one canonical page), compared case-insensitively. Data is fetched under *all* of a player's names, because old tables keep old names.
- **Teams:** a union-find over dated `TeamRenames` events plus a hand-curated [`config/team_continuity.csv`](../config/team_continuity.csv) for gaps (Excel → GIANTX). Union-find instead of chain-following, because names loop: Rogue → KOI (2022) → Rogue (2023).
- **Timing:** acquisitions and mergers count only **from their date** (NAVI buying Rogue's spot in June 2025 doesn't link them for 2023). Renames and rebrands apply at **any** date, because Leaguepedia writes old stints under the team's *current* name: Liiv SANDBOX's 2023 players appear as `BNK FEARX`, a May 2024 rebrand. A dated rule broke exactly that case on the first try.
- **Fallback:** for players with no stint rows at all, stints are rebuilt from roster changes, where a same-day Leave + Join is a re-signing.

**Effect on the labels** (before → after):

| | Before | After |
|---|---|---|
| Player-seasons | 559 | **582** (26 recovered, 3 re-keyed under new names) |
| Excluded as "left mid-season" | 64 | **41** |
| Labels corrected | | **14**: 13 moved → stayed (TSM → Shopify Rebellion, KOI → Rogue, Excel → GIANTX, Liiv SANDBOX → BNK FEARX, Kwangdong Freecs → DN Freecs), 1 no_team → moved (Jojopyun → Cloud9) |
| v0 backtest leave-Brier (base rate) | 0.218 (0.256) | **0.213 (0.249)**, still ~15% better |

v0's **frozen** predictions are unchanged: frozen means frozen. v0 was *trained* on the noisier labels and will be *graded* on the corrected ones. That's the honest order: fix the answer key, never the submitted answers.

## 8b. Label rules from exercise 4

Exercise 4 (moving the cutoff to Jan 1) surfaced three more issues. Two of them were definition questions, decided 2026-11-10:

| Issue | Example | Rule now | Labels affected |
|---|---|---|---|
| **Non-playing stints counted as teams** | Finn, Treatz, Trymbi "moved" to *LoL EMEA Championship*: they joined the **broadcast desk** as Analysts | A stint counts only if its role includes a position (`Mid/Part-Owner` and player-coach `Coach/Support` count; Analyst, Coach, Streamer, Caster don't). 40 non-playing stints ignored | **8** |
| ↳ same fix caught silent retirements | Bible (DK), Spica (FlyQuest), Th3Antonio (GIANTX) **rejoined their team as streamers or content creators**: labeled "stayed". Rascal joined KT as a streamer: labeled "moved" | now `no_team` | (in the 8) |
| **Academy demotions** | Calix, Sylvie, Vital: Nongshim RedForce → Nongshim Esports Academy | still `moved` (they lost their seat), tagged `move_type = demotion`. Detected by name (`DRX` → `DRX Challengers`) or org page. Another org's academy (Quad → FlyQuest Challengers) is a `transfer` | 7 demotions tagged |
| **Arbitrary calendar cutoff** | Jan 1 vs Feb 1 flipped 33 labels | `cutoff` = **the day the league's next season starts** (an event, not a date someone picked). `outcome_feb1` kept alongside | **3** vs Feb 1 |

The season-start cutoff turned out **stable**: it differs from Feb 1 on only 3 labels, versus 33 for Jan 1. League openers fall *after* January's registration paperwork, which is where the instability was.

**Pre-registration:** v0's freeze metadata says it will be graded "as of Feb 1". Changing a frozen model's grading rule afterwards is how results get quietly flattered. So v0 is graded on `outcome_feb1` as the primary result, with the season-start score reported alongside. New models use `outcome`. (The playing-stint fix applies to both columns: it corrects wrong data, it doesn't redefine the question.)

v0 backtest on the final labels: leave-Brier **0.211** vs base rate **0.249**.

## 9. Results

**Backtest population:** 445 labeled player-seasons, 2023–2025 offseasons, LCK + LEC + LCS.

| Contract status at season end | n | Stayed | Moved | No team |
|---|---|---|---|---|
| Expiring (≤ 120 days after season end) | 201 | 33% | **50%** | 17% |
| Under contract | 206 | **68%** | 25% | 7% |
| Unknown | 38 | 42% | 47% | 11% |
| **All** | **445** | **50%** | | |

**When moves become known:** of players who didn't stay, **72%** had their first roster change recorded by **Nov 20**, and **97%** by Dec 31.

**The live 2026 offseason, as of Nov 10:** 137 players (133 when v0 was frozen, before the identity fix; 12 of those already had a recorded move).

### What to notice
1. **Churn is the norm.** Only 50% of tier-one players keep their seat through an offseason (46% before the fixes in §8: rebrands were counted as moves, and retirements into streaming were counted as staying). "Everyone stays" is a weak baseline, and that's good news: there's real signal to find.
2. **Contract status is a strong signal.** Expiring → 67% leave (moved + no team). Under contract → 68% stay. Any model must beat a *contract-only* rule, not just "everyone stays".
3. **But 25% of under-contract players moved anyway** (buyouts, trades). Contracts alone won't get there, and rumors should help most for exactly these players.
4. **The live window is closing.** 72% of moves are recorded by Nov 20, ten days from now. To grade predictions live, a v0 must be **frozen this week**. That moves the "contract-only baseline" from Lesson 06 to *now*.
5. **The data needed auditing.** Six real bugs (loose regex, playoffs-only, leaked contracts, player-name mismatches, incomplete team renames, retroactive rebrand names) were each caught by *looking at the output*, not by the code crashing. None raised an error. Three were found by the person who knows the leagues, not by the code author.

## 10. Exercises

Answer with SQL against `data/ground_truth.db`. Predict first, then query.

**My answers, with predictions, mistakes and findings:** [`01-queries/`](01-queries/README.md). Exercises 3 and 4 found real bugs, fixed in §8 or listed there as open issues.

1. **Turnover by team:** which team had the most players *leave* in the 2024 offseason? Is that what you remember?
2. **Turnover by role:** do supports move more or less than mids? (`GROUP BY role`)
3. **Find a wrong label.** You follow these leagues. Scan `SELECT * FROM offseason_outcomes WHERE year=2025` and find one row you believe is wrong, then work out *why* from the `tenures` and `roster_changes` tables. (This is how real eval sets get cleaned.)
4. **Cutoff sensitivity:** change `CUTOFF_MONTH_DAY` in `ground_truth.py` to `"01-01"` and rebuild (it's free, thanks to the cache). How many labels change? What does that tell you about how arbitrary the cutoff is?
5. **Staleness:** `SELECT tab, sheet_updated, COUNT(*) FROM gcd_contracts GROUP BY 1, 2`. Which region's contract data would you trust least for a move announced last week, and why?
6. **The live board:** list the 2026 players whose contracts expire this year *and* who have no roster change yet. That's the list we'll predict first.

## 11. Interview talking points

- "My labels came from an external wiki. I audited them by hand, found three silent bugs (a loose regex, playoffs-only sampling that dropped the players most likely to move, and leaked contract dates), and fixed each with a test."
- "Domain knowledge found what tests didn't: G2 showing 3 of 5 players exposed that the source's tables disagree on player names (case variants, in-game renames, even a capital-I-for-lowercase-l typo). I added a canonical identity layer and re-measured: 14 labels corrected, 26 players recovered."
- "I pre-registered how v0 would be graded, and when I later improved the label definition, I kept grading v0 on the original rule. Changing the grading rule after the fact is how results get quietly flattered."
- "I caught temporal leakage in the source data itself: a contract field reflected re-signings made years later. I rebuilt contract status point-in-time from dated announcements."
- "I validated the third-party contract data against Riot's official database: 99% agreement. I resolved the conflicts by recency of evidence, and snapshot the official sheet because it keeps no history."
- "The fetcher logs in with least privilege, throttles, backs off, and caches every response, so the dataset rebuilds with zero network calls and every metric traces back to the raw data."

**Next:** freeze a contract-only baseline for the live 2026 offseason *before* Nov 17, then return to stats features (Lesson 02).
