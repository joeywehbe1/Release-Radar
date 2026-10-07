# Release Radar

Posts the **newest music releases across all genres** to a Discord channel, and flags new songs
with **potential to go viral**. A Discord webhook only *receives* messages; this project is the part
that finds releases and sends them.

Every 15 minutes (on GitHub Actions, free) it:

1. Reads **21 Apple Music US charts**: the streaming top 100 plus 20 genre charts (Pop, Hip-Hop,
   R&B, Country, Latin, Dance, Electronic, Alternative, Rock, Metal, K-Pop, Afrobeats, Reggae,
   Christian, Jazz, Soundtrack and more).
2. Turns everyone on those charts into a **hot-artist watchlist** (up to 400, hottest first) and
   checks each artist's newest songs on Apple Music.
3. Cross-checks songs on **Deezer** (popularity), **YouTube** trending music (views per hour) and
   Reddit `[FRESH]` posts.
4. Posts two kinds of alerts to one channel:
   - 🆕 **NEW DROP**: a release from the last 48h by an artist who is charting right now.
     Albums and same-day singles are grouped into one message.
   - 🔥 **GOING VIRAL**: a song from the last 14 days that is *accelerating*. It must be climbing
     the charts, be a new chart entry, be racking up YouTube views fast, or be getting Reddit
     buzz, and its viral score (0–100) must clear the threshold.

Each alert includes the artwork, release time, viral score, artist heat, the signals behind it
(e.g. "Apple Music US Top 100 #12 · ▲34", "YouTube trending #8 · 85K/hr"), and links to Apple
Music, Spotify, YouTube and Deezer.

## Setup (about 15 minutes)

### 1. Discord webhook
Channel → ⚙️ Edit Channel → Integrations → Webhooks → New Webhook → **Copy Webhook URL**.
Treat this URL like a password: anyone who has it can post to the channel.

### 2. YouTube API key (free)
1. Go to <https://console.cloud.google.com/> and create a project.
2. APIs & Services → Library → enable **YouTube Data API v3**.
3. APIs & Services → Credentials → Create credentials → **API key**. Then click the key and, under
   API restrictions, restrict it to YouTube Data API v3.

The trending music chart returns about 30 videos per call (1 quota unit), so the radar uses about
100 of the 10,000 free daily units. It still works without a key, but viral detection is much
weaker.

### 3. GitHub
1. Create a **public** repository; Actions minutes are free and unlimited for public repos. Your
   secrets stay encrypted and hidden.
   If you make it private, change the cron in `.github/workflows/radar.yml` to `*/30 * * * *` to stay
   inside the 2,000 free minutes/month.
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
One real run. State is saved to `state/state.json`.

```bash
py -m pytest
```
Runs the tests.

## Tuning (`config.toml`)

| Setting | Default | Effect |
|---|---|---|
| `new_drop_min_heat` | 38 | Artist heat (0–100) needed for NEW DROP. 38 ≈ top 5 of any genre chart or anywhere on the Apple top 100. Raise it to 45+ for mainstream-only. |
| `new_drop_max_age_hours` | 48 | How fresh a NEW DROP must be. |
| `viral_threshold` | 55 | Viral score needed for GOING VIRAL. Raise it for fewer, bigger alerts. |
| `max_new_drops_per_day` / `max_viral_per_day` | 18 / 12 | Daily caps (about 10–25 alerts/day in practice). |
| `max_viral_per_artist_per_day` | 1 | Stops one album from taking over the channel. |
| `[weights]` | 30/30/20/15/5 | Viral score mix: artist heat, chart momentum, YouTube velocity, cross-platform, Reddit. |
| `[genres]` | 20 genres | Add or remove Apple genre charts. |
| `[filters]` | | Regexes that drop DJ mixes, sped-up/slowed edits, karaoke, remixes (new drops only), and so on. |

Always want a specific artist? Add their Apple Music artist id to `artists.txt`.

## Limits

- **Spotify isn't a data source.** Spotify's February 2026 API changes removed new releases and
  popularity data for hobby apps. Alerts link to a Spotify search instead.
- **No TikTok.** TikTok has no public API for trending sounds.
- **Charts are US-only**, and Apple's charts update about once a day. Set `country` in `config.toml`
  for another storefront.
- **Reddit sometimes blocks** requests from GitHub's servers. That signal is optional and the run
  continues without it.
- **GitHub pauses scheduled workflows** in repos with no activity for 60 days. The state commits
  should keep it active; if it ever pauses, re-enable it from the Actions tab.
