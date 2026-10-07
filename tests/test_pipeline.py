import pytest
from conftest import DAY, HOUR, T0, FakeFetchers, RecordingNotifier, chart, song, video

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
