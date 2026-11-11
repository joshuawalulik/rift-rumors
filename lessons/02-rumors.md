# Lesson 02: Rumor ingestion

**Goal:** collect public roster rumors for the 2023–2026 offseasons, tie each one to the players it's about, and measure whether rumors tell us anything the contract-only v0 model doesn't. Only use rumors that were public *before* the prediction date.
**Ends with:** `data/rumors.db`, an append-only store of 5,984 rumor items from three sources. Plus coverage, signal and lead-time numbers per league, and a clear job for Lesson 03.

```bash
uv run rift-rumors rumors leaguepedia 2022 2023 2024 2025 2026  # structured wiki rumors
uv run rift-rumors rumors backfill 2025 2024 2023                # past offseasons (cached forever)
uv run rift-rumors rumors collect --year 2026                    # live offseason (refreshes every 3 h)
uv run rift-rumors rumors ko-handles --check                     # Hangul handle map: support report
uv run rift-rumors rumors stats
uv run rift-rumors rumors coverage [--source gnews|gnews_ko|leaguepedia]
```

---

## 1. What a rumor feature has to satisfy

A rumor helps a prediction made on Nov 10 only if it was **public on or before Nov 10**. That gives three rules, and every design choice below follows from them:

1. **Every item needs a trustworthy timestamp**: when it was published, and when *we* first saw it.
2. **No answers stored as features.** "This rumor was confirmed" is the label, not an input (§6).
3. **Measure before modeling.** Before any LLM reads a headline, check whether the headlines even mention the players who move (*coverage*), and whether mentioned players move more often (*signal*).

## 2. Sources: what we tried

| Source | Result | Why |
|---|---|---|
| Reddit API | ❌ | Needs approval under the Responsible Builder Policy; new requests reportedly closed Oct 31, 2026 |
| Reddit `.json` / RSS | ❌ | 403, or 429 after one request (`x-ratelimit-remaining: 0`) |
| X (Twitter) API | ❌ | Paid |
| Sheep Esports / Dot Esports RSS | ❌ | 403 to non-browser clients |
| Dexerto RSS | ⚠️ | Works, but few roster rumors |
| **Google News RSS search** | ✅ | Aggregates Sheep Esports and others. `after:`/`before:` work, so **past offseasons can be searched**, which makes rumors backtestable |
| **Leaguepedia `RosterRumors`** | ✅ | Rumors that wiki editors already structured, including ~1,000 sourced to X/Twitter, Weibo and Facebook |
| **Google News, Korean edition** | ✅ | Korean outlets (Daily Esports, Inven, Nate) report LCK moves first |

We **don't get around blocks** (no rotating IPs, no fake browser identity). A 429 or 403 is the site saying no. For Reddit there's a manual path (`rumors add`) and a drafted API application ([docs/reddit-api-application.md](../docs/reddit-api-application.md)).

> Lesson: the best "X data" turned out to be a wiki whose editors already read X for us. Look for someone who has already structured the data before you scrape it.

## 3. An append-only store

`ground_truth.db` is rebuilt from cache on every run. `rumors.db` is **never rebuilt**, because it records `first_seen_at`: the moment *we* first fetched an item. Nobody can reconstruct that later. When v1 is frozen live, "we had this rumor before the freeze" is only provable if it was written down at the time.

- `rumor_items`: one row per headline or rumor: source, outlet, title, URL, `published_at`, `first_seen_at`, `last_seen_at`. **Headlines only, never article bodies.**
- `rumor_query_hits`: which searches found each item, so coverage can be traced to queries.
- `rumor_claims`: structured fields for Leaguepedia rows (§6).
- `rumor_mentions`: *derived* (rebuilt by `link`): which players and teams each item mentions.

## 4. Searching Google News: three surprises

**Surprise 1: the ~25-result cap.** One search ("LEC roster") returns about 25 items for a whole offseason. Fix: **one search per team per month** (Sep–Jan), plus league-wide searches.

**Surprise 2: team names are ambiguous.** "LYON" matched cricket, "NAVI" matched Navi Mumbai, "Disguised" matched finance stories, and that noise filled the 25 slots. Fix: every team search also requires `(esports OR "League of Legends" OR LoL OR LCK OR LEC OR LCS)`.

**Surprise 3: hitting the cap doesn't mean results were cut off.** Adaptive splitting halves a window whenever a search returns the full ~25, down to 4-day windows. Each backfilled year then ran ~450 searches and saw **~12,000 results, but only 500–770 new headlines**. Google News fills every result list up to the cap with older matches. Smaller windows found little that was new. Splitting is safe but expensive, and the 25-result count is a poor signal that something was missed.

> Lesson: check a heuristic's assumption against the data. "Full page ⇒ more results exist" was false here, and the cost showed up as about 1,400 requests to backfill three offseasons.

## 5. Linking: candidate generation, not understanding

`link.py` connects headlines to players with plain string matching. Its job is **high recall**: find every headline that *might* be about a player. Lesson 03's LLM will decide what each one claims.

- **Year-scoped**: an item belongs to one offseason (Sep 1 → Feb 1) and matches only that year's players.
- **Case-sensitive, 4+ characters, on word boundaries**, using handles plus every redirect alias from Leaguepedia.
- **Ambiguous names are skipped** (two players with the same name in one year).
- **English words are flagged** `player_common_word`, using `/usr/share/dict/words`. A spot check showed why: Tactical, Vulcan and Duro were real player mentions, but "Quad" and "Upset" were not. Coverage counts only clean matches.
- **Word boundaries only consider Latin letters and digits.** Korean attaches particles directly to names ("T1이", "젠지는"), and Python's Unicode `\w` treats Hangul as letters, so a normal word boundary would never match.

## 6. Leaguepedia `RosterRumors`, and its leakage trap

Each row has a date, player, team they're leaving, team they're rumored to join, role, and source. Since Aug 2022 that's **2,845 rows**, and editors keep rumors that didn't happen too (252 are marked "Disproven"). That suggests they don't only record rumors that came true.

⚠️ **The trap:** `Status`, `IsOver` and `NotHappening` are filled in *after the outcome is known*. Use them as features and the backtest would look excellent for the wrong reason. **We never store them.** A test checks that "Confirmed" never ends up in the database.

A smaller caveat: editors sometimes add a row days after the rumor's `Date`. Backtests filter on `Date`, so they're slightly optimistic. Live predictions should filter on `first_seen_at`.

Rows are linked **structurally** (`match_kind = structured`): the row names the player, and we resolve it through `player_aliases`. No text matching. A row about a player outside our population (an ERL player joining an LEC team) still counts as a team-level mention.

## 7. Korean headlines: an LLM drafts, code and a human check

Korean outlets write handles in Hangul inside quotes: "'제우스' 영입, 한화생명…" ('Zeus' signed by Hanwha Life). After adding the Korean Google News edition we had 951 Korean headlines. 703 linked to a team, and **only 4 linked to a player**.

The fix is a Hangul handle map ([config/ko_handles.csv](../config/ko_handles.csv)), built in four steps:

1. **Collect** the quoted Hangul words in our headlines: 209 distinct. Most are handles; some are ordinary words (롤드컵 "Worlds", 주목 "spotlight").
2. **Draft**: Claude Opus 5.5 gets our player list and those words, and proposes spellings. It may only answer with handles from our list, and **never legal names**. One call, $0.13, logged in the cost ledger.
3. **Validate in code**: the handle must exist, the spelling must be at least 2 Hangul syllables, and one spelling can't map to two players. 90 mappings passed. Then **check them against the headlines**: 64 of the 90 appear in our headlines, mostly next to the player's own team. The ones that didn't appear next to their team were all correct when read by hand. They were players changing teams, or headlines using short team names ("kt", 한화, 광동, DNS) that we then added for matching.
4. **Human review**: the `verified` column. `no` rows are ignored, and re-running never overwrites a review.

Hangul names only match when they **stand alone**: quoted, in a "·" list, or followed only by a particle. Otherwise the handle 지우 (Jiwoo) would match 지우다 ("to erase").

> Lesson: an LLM is a fast first draft for reference data. You make it trustworthy by constraining what it may answer (our handle list), validating its output in code, checking it against real data, and having a person review the rows nothing else could check.

## 8. Results

`rift-rumors rumors coverage` on Nov 11. **Leavers covered** is the share of players who left (and whose move wasn't already public) that had at least one rumor naming them on or before Nov 11:

| Leavers covered | Google News (EN) | Leaguepedia | All sources* |
|---|---|---|---|
| LEC 2023 / 2024 / 2025 | 0% / 21% / 8% | 35% / 74% / 85% | 35% / 74% / **85%** |
| LCS 2023 / 2024 / 2025 | 6% / 20% / 11% | 39% / 40% / 33% | 44% / 40% / 33% |
| LCK 2023 / 2024 / 2025 | 8% / 4% / 11% | 0% / 4% / 6% | 27% / 12% / 17% |

\* all = English and Korean Google News, Leaguepedia, and Hangul handles.

**Signal: share of players who left, split by whether they had a rumor before Nov 11**

| | With a rumor | Without |
|---|---|---|
| LEC 2025 | 55% | **8%** |
| LCS 2025 | 75% | 50% |
| LCK 2025 | 25% | 41% |

Median lead time from first rumor to the move: 16–52 days.

### What to notice
- **Leaguepedia dominates for LEC and LCS.** In 2025, an LEC player with no rumor by Nov 11 left only 8% of the time.
- **English Google News adds little on its own.** It surfaces the same Sheep Esports reports that Leaguepedia editors already log, so it's mostly a redundant, timestamped second record.
- **LCK is the weak spot, and the reason is instructive.** Korean news before Nov 11 is mostly **Worlds coverage**: interviews and match previews. Players on Worlds teams get the headlines and tend to stay. So for LCK, *being mentioned* points slightly the wrong way. **A mention is not a rumor.** This is the job for Lesson 03: read each headline and label what it claims (leaving, re-signing, or not about a move).
- **Live 2026:** 61 of 137 players already have at least one player-level mention.

## 9. Exercises

1. **Precision by match kind.** Sample 30 linked items, 10 each of `player`, `player_common_word` and `player_ko`. Label each "is this headline about this player?" and compute precision per kind. This becomes Lesson 03's first hand-labeled set.
   ```sql
   -- sqlite3 data/rumors.db
   SELECT m.match_kind, m.matched, m.player, i.title FROM rumor_mentions m
   JOIN rumor_items i ON i.id = m.item_id
   WHERE m.match_kind = 'player_common_word' ORDER BY random() LIMIT 10;
   ```
2. **Review the Hangul map.** Fill in `verified` in `config/ko_handles.csv`. Start with the 26 rows that never appear in a headline (`rumors ko-handles --check` lists those that do).
3. **The rumor you missed.** Find three 2025 leavers with no rumor before Nov 11. Search for them by hand: was there a public rumor, and where (Reddit, X, a Korean outlet)? That tells you which source to add next.
4. **Move the as-of date.** Call `coverage(rumors, gt, "10-01")` and `"12-01"` from Python. How do coverage and signal change? What does that say about *when* a model with rumor features should be frozen?
5. **Reddit.** Read [docs/reddit-api-application.md](../docs/reddit-api-application.md), check whether Reddit still accepts requests, and decide whether to apply.

## 10. Interview talking points

- "I measured coverage and signal *before* building the NLP. That's how I learned the best X-rumor source was a structured wiki, and that Korean headlines were mostly noise without claim extraction."
- "Leaguepedia's rumor table has status fields filled in after the fact. Using them would have leaked the label, so the pipeline never stores them, and a test enforces that."
- "The rumor store is append-only because *when I first saw a rumor* can't be reconstructed later, and the live evaluation depends on it."
- "I used an LLM to draft a Hangul transliteration table, but constrained it to known handles, validated it in code, checked it against real headlines, and kept a human review column. LLM output is a draft, not ground truth."
- "Adaptive query splitting assumed a full result page meant results were missing. The data showed Google pads results, so 12k results produced ~600 new items. I documented the cost instead of hiding it."
