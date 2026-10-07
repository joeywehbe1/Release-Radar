import pytest
from conftest import DAY, HOUR, T0, FakeFetchers, RecordingNotifier, chart, preorder, song, video

from radar import notify, pipeline, state
from radar.models import Candidate


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)


def hot_charts():
    """Artist 1 ('Star') is all over the charts with an older song."""
    return [chart("Star", "Old Hit", 3), chart("Star", "Old Hit", 1, source="genre:Pop")]


def bootstrapped(cfg, fetchers, now=T0 - DAY):
    st = state.empty()
    result = pipeline.run_cycle(cfg, st, fetchers, now)
    assert result.bootstrap
    pipeline.deliver(result, st, RecordingNotifier(), now, cfg)
    return st


def run(cfg, st, fetchers, now, notifier=None):
    notifier = notifier or RecordingNotifier()
    result = pipeline.run_cycle(cfg, st, fetchers, now)
    pipeline.deliver(result, st, notifier, now, cfg)
    return result, notifier


def test_bootstrap_posts_one_summary_and_marks_current_drops(cfg):
    fetchers = FakeFetchers(charts=hot_charts(), watch=[song("Star", "Brand New")])
    st = state.empty()
    result, notifier = run(cfg, st, fetchers, T0)
    assert result.bootstrap
    assert [a.kind for a in result.eligible] == ["new"]
    assert len(notifier.payloads) == 1
    assert notifier.payloads[0]["embeds"][0]["title"] == "✅ Release Radar is online"
    assert st["created"] == T0
    # the drop that was already out at bootstrap is never posted
    result2, notifier2 = run(cfg, st, fetchers, T0 + 15 * 60)
    assert not result2.bootstrap
    assert notifier2.payloads == []


def test_new_drop_is_posted_once(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    fetchers = FakeFetchers(charts=hot_charts(), watch=[song("Star", "Brand New")])
    result, notifier = run(cfg, st, fetchers, T0)
    assert [a.kind for a in result.alerts] == ["new"]
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["author"]["name"].startswith("🆕 NEW DROP")
    assert embed["title"] == "Star — Brand New"
    assert st["sent"][-1][1] == "new"
    _, notifier2 = run(cfg, st, fetchers, T0 + 15 * 60)
    assert notifier2.payloads == []


def test_friday_release_with_placeholder_time_is_posted_immediately(cfg):
    # Out at 04:00 UTC, but Apple stamps it 12:00 UTC the same day.
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    fetchers = FakeFetchers(charts=hot_charts(), watch=[song("Star", "Friday Drop", release=T0 + 8 * HOUR)])
    result, notifier = run(cfg, st, fetchers, T0)
    assert [a.kind for a in result.alerts] == ["new"]
    assert st["tracks"]["star|friday drop"]["release"] == T0   # "released just now", not "in 8 hours"
    assert f"<t:{T0}:R>" in str(notifier.payloads[0])
    # later runs keep the first-seen time instead of jumping to Apple's placeholder
    run(cfg, st, fetchers, T0 + 9 * HOUR)
    assert st["tracks"]["star|friday drop"]["release"] == T0


def test_fast_check_only_looks_for_new_drops(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    positions_before = {k: dict(t["pos"]) for k, t in st["tracks"].items()}
    fetchers = FakeFetchers(charts=hot_charts(), watch=[song("Star", "Brand New")],
                            upcoming=[preorder("Star", "Next Album")])
    result = pipeline.run_cycle(cfg, st, fetchers, T0, fast=True)
    assert fetchers.calls == []                       # no charts, no pre-order lookups
    assert [a.kind for a in result.eligible] == ["new"]
    assert {k: st["tracks"][k]["pos"] for k in positions_before} == positions_before


def test_new_zealand_release_posts_early_and_only_once(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    nz = song("Star", "Global Drop", release=T0 + 20 * HOUR)    # Apple's placeholder: tomorrow
    nz.extra["storefront"] = "nz"
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), watch=[nz]), T0)
    assert [a.kind for a in result.alerts] == ["new"]
    assert "Out now in New Zealand" in notifier.payloads[0]["embeds"][0]["description"]
    # 17 hours later it reaches the US store: no second alert
    us = song("Star", "Global Drop", release=T0 + 20 * HOUR)
    us.extra["storefront"] = "us"
    _, notifier2 = run(cfg, st, FakeFetchers(charts=hot_charts(), watch=[us]), T0 + 17 * HOUR)
    assert notifier2.payloads == []
    assert set(st["tracks"]["star|global drop"]["live"]) == {"nz", "us"}


def test_coming_soon_skips_existing_preorders_then_alerts_new_ones(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    listed = [preorder("Star", "Old Announcement"), preorder("Star", "Hits (Deluxe Edition)")]
    _, first = run(cfg, st, FakeFetchers(charts=hot_charts(), upcoming=listed), T0)
    assert first.payloads == []                          # first check only records what's listed
    assert st["upcoming_seeded"]
    fresh = [*listed, preorder("Star", "Brand New Album"), preorder("Star", "Brand New Album", collection_id="clean")]
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), upcoming=fresh), T0 + 31 * 60)
    assert [a.kind for a in result.alerts] == ["soon"]   # clean + explicit editions alert once
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["author"]["name"].startswith("📅 COMING SOON")
    assert embed["title"] == "Star — Brand New Album"
    assert f"<t:{T0 + 14 * DAY}:D>" in embed["description"]
    # not again, and pre-order lookups wait 30 minutes between checks
    fetchers = FakeFetchers(charts=hot_charts(), upcoming=fresh)
    _, again = run(cfg, st, fetchers, T0 + 32 * 60)
    assert again.payloads == [] and "upcoming" not in fetchers.calls


def test_cold_artists_and_old_or_junk_songs_are_ignored(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    cold = chart("Nobody", "Deep Cut", 95, source="genre:Jazz", artist_id="2")
    fetchers = FakeFetchers(charts=hot_charts() + [cold], watch=[
        song("Nobody", "New Song", artist_id="2"),              # artist heat too low
        song("Star", "Last Week", release=T0 - 5 * DAY),       # not new enough for NEW DROP
        song("Star", "Brand New (Mixed)"),                      # junk
        song("Star", "Brand New (Remix)"),                      # new-drop-only junk
    ])
    result, _ = run(cfg, st, fetchers, T0)
    assert [a for a in result.eligible if a.kind == "new"] == []


def test_album_tracks_become_one_alert(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    album = [song("Star", f"Track {i}", collection="Big Album", collection_id="alb", track_count=14) for i in range(1, 6)]
    single = song("Star", "Side Single")
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), watch=album + [single]), T0)
    assert len(result.alerts) == 1
    alert = result.alerts[0]
    assert len(alert.tracks) == 5 and [t["title"] for t in alert.also] == ["Side Single"]
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["title"] == "Star — Big Album"
    assert "**Album** · 14 tracks" in embed["description"] and "Side Single" in embed["description"]
    assert {"rel:alb", "rel:c-1-Side Single"} <= set(st["releases"])


def test_breakout_song_goes_viral(cfg):
    start = T0 - 13 * HOUR
    early = FakeFetchers(charts=hot_charts() + [chart("Newcomer", "Breakout", 60, artist_id="9", release=start - DAY)])
    st = bootstrapped(cfg, early, now=start)
    later = FakeFetchers(
        charts=hot_charts() + [chart("Newcomer", "Breakout", 5, artist_id="9", release=start - DAY),
                               chart("Newcomer", "Breakout", 2, source="genre:Pop", artist_id="9", release=start - DAY)],
        yt=[video("Newcomer", "Breakout (Official Video)", 2_000_000, published=start - DAY)],
        deezer_ranks={("Newcomer", "Breakout"): 900_000},
    )
    result, notifier = run(cfg, st, later, T0)
    viral = [a for a in result.alerts if a.kind == "viral"]
    assert len(viral) == 1 and viral[0].track["title"] == "Breakout"
    assert viral[0].score >= cfg["alerts"]["viral_threshold"]
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["author"]["name"].startswith("🔥 GOING VIRAL")
    signals = next(f["value"] for f in embed["fields"] if f["name"] == "Signals")
    assert "▲55" in signals and "YouTube" in signals and "Deezer" in signals
    # posted once only
    _, notifier2 = run(cfg, st, later, T0 + 15 * 60)
    assert not any("GOING VIRAL" in p["embeds"][0]["author"]["name"] for p in notifier2.payloads)


def test_caps_limit_alerts_per_run_and_per_day(cfg):
    cfg["alerts"]["max_per_run"] = 3
    cfg["alerts"]["max_new_drops_per_day"] = 4
    charts, watch = [], []
    for i in range(10):
        charts.append(chart(f"Artist{i}", "Hit", i + 1, artist_id=f"a{i}"))
        watch.append(song(f"Artist{i}", f"New {i}", artist_id=f"a{i}"))
    st = bootstrapped(cfg, FakeFetchers(charts=charts))
    fetchers = FakeFetchers(charts=charts, watch=watch)
    r1, _ = run(cfg, st, fetchers, T0)
    r2, _ = run(cfg, st, fetchers, T0 + 900)
    r3, _ = run(cfg, st, fetchers, T0 + 1800)
    assert [len(r.alerts) for r in (r1, r2, r3)] == [3, 1, 0]
    # the hottest artists go first
    assert [a.track["artist"] for a in r1.alerts] == ["Artist0", "Artist1", "Artist2"]


def test_failed_post_is_retried_next_run(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    fetchers = FakeFetchers(charts=hot_charts(), watch=[song("Star", "Brand New")])
    run(cfg, st, fetchers, T0, notifier=RecordingNotifier(ok=False))
    assert st["releases"] == {}
    _, notifier = run(cfg, st, fetchers, T0 + 900)
    assert len(notifier.payloads) == 1


def test_youtube_only_song_is_checked_on_apple(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    old_on_apple = Candidate(source="apple_search", artist="Oldie", title="Classic", release=T0 - 400 * DAY,
                             artist_id="5", apple_id="x")
    fetchers = FakeFetchers(charts=hot_charts(), yt=[video("Oldie", "Classic (Lyric Video)", 5_000_000, published=T0 - DAY)],
                            apple_search={("Oldie", "Classic"): old_on_apple})
    result, _ = run(cfg, st, fetchers, T0)
    assert st["tracks"]["oldie|classic"]["old"] is True
    assert all(t["title"] != "Classic" for _, t in result.ranked)


def test_state_roundtrip_and_prune(cfg, tmp_path):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts(), watch=[song("Star", "Brand New")]))
    path = tmp_path / "state.json"
    state.save(path, st)
    loaded = state.load(path)
    assert loaded["tracks"].keys() == st["tracks"].keys()
    state.prune(loaded, T0 + 30 * DAY, cfg)
    assert loaded["tracks"] == {}


def test_alert_payload_respects_discord_limits(cfg):
    track = {"artist": "A" * 300, "title": "B" * 300, "release": T0, "pos": {}, "key": "a|b", "anorm": "a"}
    payload = notify.alert_payload(pipeline.Alert("viral", "a|b", 70, 50, track, [track]), T0, cfg)
    embed = payload["embeds"][0]
    assert len(embed["title"]) <= 256
    assert payload["allowed_mentions"] == {"parse": []}
