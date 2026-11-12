# Applying for Reddit Data API access

Reddit's API has required approval since Reddit introduced the **Responsible Builder
Policy** (announced on r/redditdev, Nov 2025). Self-service app creation no longer
gets you working credentials: a person at Reddit reviews each request.

> **Check these two things first, in your own browser.** Reddit's help and developer pages block
> automated readers, so neither was confirmed from Reddit's own pages when this was written (Nov 2026):
>
> 1. **Is Reddit still taking new requests?** Third-party blogs report that Reddit stopped accepting
>    new public-API requests on 31 Oct 2026, and that remaining public access ends in Mar 2027. If
>    Reddit's own pages confirm that, stop here: the manual `rumors add` path is the fallback.
> 2. **Where is the current request form?** Reddit Help → "Developer Platform & Accessing Reddit Data",
>    or the links in the pinned Responsible Builder Policy post on r/redditdev. Researchers and
>    developers had separate links.

## Steps

1. **Use your normal Reddit account**, not a new throwaway. The review looks at who is asking.
   (You create and submit everything yourself. Claude can't create accounts or submit forms for you.)
2. **Read the Responsible Builder Policy** and the Data API Terms in full. Pay attention to the
   sections on AI/ML, deleted content and commercial use. The draft below makes promises
   about each of these, so make sure they're promises you can keep.
3. **Open the access request form** you found in check 2 above. Choose the
   **developer / non-commercial** category, not researcher: this is a personal project, not
   institutional research.
4. **Paste the answers below**, adjusting anything that's no longer true. Open each subreddit
   named in the draft first and remove any that don't exist or are inactive. The list wasn't
   verified.
5. **Wait.** Reddit says it aims for about 7 days, but reports range from days to weeks of silence,
   and Reddit can deny a request without saying why.
6. **If approved:** create a *script* app at reddit.com/prefs/apps. Put `REDDIT_CLIENT_ID`,
   `REDDIT_CLIENT_SECRET` and `REDDIT_USERNAME` in `.env`, never in code or chat. Then tell Claude,
   and we'll build `rumors/reddit.py` within the limits you promised.
7. **If denied:** keep using `rift-rumors rumors add` for Reddit posts you see yourself.

## Draft answers

**App / project name:** Rift Rumors

**Category:** Developer: personal, non-commercial

**What are you building?**

> Rift Rumors is a personal, non-commercial learning project: a model that estimates the
> probability that each professional League of Legends player in the LCK, LEC and LCS changes
> teams during the offseason. Its inputs are contract data, past roster history and public
> roster-rumor reports. I'm a software engineer learning AI engineering, and the project is
> documented as a series of lessons in a private GitHub repository. I want to measure whether
> roster rumors discussed on Reddit appear earlier than, or in addition to, those in
> news articles.

**How will you use Reddit data?**

> Read-only. Several times a day during the offseason (September–February), the app would read
> new posts in a few esports subreddits (r/leagueoflegends, r/lck, r/LEC, r/LCS) and run
> keyword searches within them for roster news ("roster", "signs", "contract", team names).
> For each matching post I would store only the post ID, subreddit, title, permalink and
> creation time. I would not store usernames, comment text, post bodies, images or any user
> profile data.
>
> Post titles would be matched against a list of pro player names. Titles may also be sent to
> an LLM API (Anthropic's Claude) for *classification*, labeling whether a title claims a
> specific player move. They would **not** be used to train or fine-tune any model, and the
> data would not be sold, shared or redistributed. Anything published from the project would
> contain only aggregate statistics (for example "x% of moves were rumored on Reddit first"),
> never Reddit content.

**Expected volume:**

> Roughly 10–20 requests per run, about 4 runs per day: under 100 requests per day, far
> below the free-tier rate limit. One OAuth client, one user agent identifying the app and
> my Reddit username.

**How will you handle deleted content?**

> Before each analysis run, stored post IDs are re-checked, and any post that has been deleted
> or removed is deleted from my database.

**Commercial use?**

> None. No ads, no paid product, no revenue.

## Before you submit, make sure these stay true

- **No training.** The tiny-GPT project (Repo C) trains on casting transcripts. Never add Reddit
  data to it.
- **Sending titles to Claude.** Classifying titles with Claude is disclosed above. If the policy
  says data may not be sent to third-party AI services at all, remove that sentence. Lesson 03
  would then run classification locally with Ollama only.
- **Deleted posts.** We'd have to build the deletion check if approved. It's a promise to Reddit,
  so it can't be skipped.
