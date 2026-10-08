# Release Radar

Posts the **newest music releases across all genres** to a Discord channel, and flags new songs
with **potential to go viral**. A Discord webhook only *receives* messages; this project is the part
that finds releases and sends them.

It posts two kinds of alerts to one channel:

- 🆕 **NEW DROP**: a release from the last 24 hours by an artist who is charting right now, posted
  the moment its tracks become playable. An artist's album and same-day singles are grouped into
  one message.
- 🔥 **GOING VIRAL**: a song from the last 3 days that is *accelerating*. It must be climbing the
  charts, be a new chart entry, be racking up YouTube views fast, or be getting Reddit buzz, and
  its viral score (0–100) must clear the threshold.

Each alert includes artwork, release time, artist heat, the signals behind it (e.g. "Apple Music US
Top 100 #12 · ▲34", "YouTube trending #8 · 85K/hr"), and links to Apple Music, Spotify, YouTube and
Deezer.

## How fast it is

Releases go live at **midnight local time** (most of all on Fridays, Global Release Day), and
**New Zealand gets there first**, about 17 hours before the US East Coast. For the ~230 artists hot
enough for a NEW DROP, the radar looks up their newest releases on Apple Music (including announced
ones) and checks whether each release's tracks are **playable yet** in the New Zealand and US
stores. As soon as a release unlocks, it's posted; if it unlocked in NZ first, the alert says
"🌏 Out now in New Zealand", hours before US listeners can play it.

| When (UTC, follows daylight saving) | What runs |
|---|---|
| Every day, ~50 min before to 40 min after **New Zealand midnight** (11:00 or 12:00 UTC) | A quick new-drop check **every 20 seconds** |
| Every day, the same around **US Eastern midnight** (04:00 or 05:00 UTC) | Same; on Fridays both windows run 100 min past midnight |
| The rest of the time | A full check (all sources) every 5 minutes; GitHub usually starts these every 10–20 minutes |

Messages are only sent when something qualifies, never on a timer.

## Sources

| Source | Used for | Checked |
|---|---|---|
| Apple Music: hot artists' newest releases + whether they're playable in NZ / US | **Finding NEW DROPs** | Every check, including the 20-second ones |
| Apple Music US top 100 (streaming) | Who's hot; whether a song is climbing | Every full check (Apple updates it daily) |
| 20 iTunes genre charts (Pop, Hip-Hop, R&B, Country, Latin, K-Pop, Afrobeats, Rock, Metal…) | Covers all genres | Every full check |
| Apple Music: hot artists' newest songs | Feeding the viral score | Every full check |
| YouTube trending music (top ~30 videos) | Views per hour, the strongest viral signal | Every full check |
| Deezer | Popularity score and chart position on a second platform | Every full check |
| Reddit `[FRESH]` posts | Fan-community buzz; often blocked from GitHub | Every full check |

Apple's servers cache identical requests for up to a day, so every lookup is made unique to get
fresh data.

## Setup (about 15 minutes)

### 1. Discord webhook
Channel → ⚙️ Edit Channel → Integrations → Webhooks → New Webhook → **Copy Webhook URL**.
Treat this URL like a password: anyone who has it can post to the channel.

### 2. YouTube API key (free)
1. Go to <https://console.cloud.google.com/> and create a project.
2. APIs & Services → Library → enable **YouTube Data API v3**.
3. APIs & Services → Credentials → Create credentials → **API key**. Then click the key and, under
   API restrictions, restrict it to YouTube Data API v3.

The trending music chart returns about 30 videos per call (1 quota unit), so checking every 5
minutes uses about 300 of the 10,000 free daily units. It still works without a key, but viral detection is much
weaker.

### 3. GitHub
1. Create a **public** repository. Actions minutes are free and unlimited for public repos, and your
   secrets stay encrypted and hidden. A private repo only gets 2,000 free minutes a month, which a
   5-minute schedule would use up in about a week.
2. Push this folder to it:
   ```bash
   git init -b main
   git add .
   git commit -m "Release Radar"
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
3. Repo → Settings → Secrets and variables → Actions → **New repository secret**. Add:
   - `DISCORD_WEBHOOK_URL`: the webhook URL from step 1
   - `YOUTUBE_API_KEY`: the key from step 2
4. Actions tab → **Release Radar** → **Run workflow**.

The first run is a **bootstrap**. It posts one "✅ Release Radar is online" message listing today's
hottest new releases, and quietly marks everything already out as seen, so your channel isn't
flooded. Alerts start on the next runs. Climb detection needs about 12 hours of history to kick in.

## Running it on your PC

```bash
py -m pip install -r requirements.txt pytest
```
Copy `.env.example` to `.env` and fill in your webhook URL and YouTube key (`.env` is git-ignored).

```bash
py -m radar test-webhook
```
Posts a test message to check the webhook.

```bash
py -m radar run --dry-run
```
Fetches live data and prints what *would* be posted. It posts nothing and saves no state.

```bash
py -m radar run
```
One real full check. State is saved to `state/state.json`.

```bash
py -m radar watch --minutes 10
```
Quick new-drop checks every 20 seconds for 10 minutes. This posts for real. Without `--minutes` it
only runs inside a release window.

```bash
py -m pytest
```
Runs the tests.

## Tuning (`config.toml`)

| Setting | Default | Effect |
|---|---|---|
| `new_drop_min_heat` | 38 | Artist heat (0–100) needed for NEW DROP. 38 ≈ top 5 of any genre chart or anywhere on the Apple top 100. Raise it to 45+ for mainstream-only. |
| `new_drop_max_age_hours` | 24 | How fresh a NEW DROP must be. |
| `viral_max_age_days` | 3 | How recent a song must be to go viral. |
| `viral_threshold` | 55 | Viral score needed for GOING VIRAL. Raise it for fewer, bigger alerts. |
| `max_new_drops_per_day` / `max_viral_per_day` | 25 / 12 | Daily caps. Most days stay well under these; release days can hit the NEW DROP cap. |
| `early_storefronts` | `["nz"]` | Extra Apple stores checked for early releases. Add `"au"` for Australia too. |
| `[bursts]` | NZ + US Eastern, every 20s | Release windows: time zones, how long before/after midnight, check interval. |
| `max_viral_per_artist_per_day` | 1 | Stops one album from taking over the channel. |
| `[weights]` | 30/30/20/15/5 | Viral score mix: artist heat, chart momentum, YouTube velocity, cross-platform, Reddit. |
| `[genres]` | 20 genres | Add or remove Apple genre charts. |
| `[filters]` | | Regexes that drop DJ mixes, sped-up/slowed edits, karaoke, remixes (new drops only), and so on. |

Always want a specific artist? Add their Apple Music artist id to `artists.txt`.

## Limits

- **Spotify isn't a data source.** Spotify's February 2026 API changes removed new releases and
  popularity data for hobby apps. Alerts link to a Spotify search instead.
- **No TikTok.** TikTok has no public API for trending sounds.
- **New Zealand only helps for Global Release Day drops.** Some big releases come out at the same
  moment worldwide (often midnight US Eastern), and US-only releases appear at US midnight. The radar
  still catches those at the US release.
- **Charts are US-only**, and Apple's charts update about once a day. Set `country` in `config.toml`
  for another storefront.
- **Reddit sometimes blocks** requests from GitHub's servers. That signal is optional and the run
  continues without it.
- **GitHub pauses scheduled workflows** in repos with no activity for 60 days. The state commits
  should keep it active; if it ever pauses, re-enable it from the Actions tab.
