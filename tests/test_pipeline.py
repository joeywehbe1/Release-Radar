import pytest
from conftest import DAY, HOUR, T0, FakeFetchers, RecordingNotifier, chart, out, release, song, video

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


def run(cfg, st, fetchers, now, notifier=None, fast=False):
    notifier = notifier or RecordingNotifier()
    result = pipeline.run_cycle(cfg, st, fetchers, now, fast=fast)
    pipeline.deliver(result, st, notifier, now, cfg)
    return result, notifier


def test_bootstrap_posts_one_summary_and_records_what_is_already_out(cfg):
    fetchers = FakeFetchers(charts=hot_charts(), releases=[release("Star", "Already Out - Single")])
    st = state.empty()
    result, notifier = run(cfg, st, fetchers, T0)
    assert result.bootstrap and [a.kind for a in result.eligible] == ["new"]
    assert len(notifier.payloads) == 1
    assert notifier.payloads[0]["embeds"][0]["title"] == "✅ Release Radar is online"
    assert st["created"] == T0
    assert "rel:1|already out" in st["releases"]
    _, notifier2 = run(cfg, st, fetchers, T0 + 60)
    assert notifier2.payloads == []


def test_new_drop_is_posted_once(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    fetchers = FakeFetchers(charts=hot_charts(), releases=[release("Star", "Brand New - Single")])
    result, notifier = run(cfg, st, fetchers, T0, fast=True)
    assert [a.kind for a in result.alerts] == ["new"]
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["author"]["name"].startswith("🆕 NEW DROP")
    assert embed["title"] == "Star — Brand New"
    assert "**Single**" in embed["description"]
    assert [f["name"] for f in embed["fields"]][:2] == ["Released", "Artist heat"]   # no viral score yet
    _, notifier2 = run(cfg, st, fetchers, T0 + 20, fast=True)
    assert notifier2.payloads == []


def test_fast_check_only_looks_up_releases(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    positions_before = {k: dict(t["pos"]) for k, t in st["tracks"].items()}
    fetchers = FakeFetchers(charts=hot_charts(), releases=[release("Star", "Brand New - Single")])
    result = pipeline.run_cycle(cfg, st, fetchers, T0, fast=True)
    assert fetchers.calls == ["releases", "playability"]
    assert [a.kind for a in result.eligible] == ["new"]
    assert {k: st["tracks"][k]["pos"] for k in positions_before} == positions_before


def test_preorder_waits_until_playable_and_new_zealand_goes_first(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    album = release("Star", "Big Album", release=T0 + 20 * HOUR, track_count=13)   # Apple's placeholder date
    status = {"nz": {album.collection_id: out(2, 13)}, "us": {album.collection_id: out(2, 13)}}
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), releases=[album], status=status), T0, fast=True)
    assert result.alerts == [] and notifier.payloads == []        # only the pre-release singles are out
    # New Zealand reaches release-day midnight: the whole album unlocks there first
    status["nz"][album.collection_id] = out(13, 13, ["Intro", "Big Song", "Ballad"])
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), releases=[album], status=status), T0 + 60, fast=True)
    assert [a.kind for a in result.alerts] == ["new"]
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["title"] == "Star — Big Album"
    assert "**Album** · 13 tracks" in embed["description"]
    assert "Includes *Intro* · *Big Song* · *Ballad*" in embed["description"]
    assert "Out now in New Zealand" in embed["description"]
    assert f"<t:{T0 + 60}:R>" in str(embed)                       # "released just now", not "in 20 hours"
    # ~17 hours later it unlocks in the US: no second alert
    status["us"][album.collection_id] = out(13, 13)
    _, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), releases=[album], status=status), T0 + 17 * HOUR, fast=True)
    assert notifier.payloads == []


def test_clean_and_explicit_editions_alert_once(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    editions = [release("Star", "Big Album", collection_id="explicit", track_count=12),
                release("Star", "Big Album", collection_id="clean", track_count=12)]
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), releases=editions), T0, fast=True)
    assert len(result.alerts) == 1 and len(notifier.payloads) == 1


def test_album_and_single_from_one_artist_become_one_alert(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    drops = [release("Star", "Side Single - Single"), release("Star", "Big Album", track_count=14)]
    result, notifier = run(cfg, st, FakeFetchers(charts=hot_charts(), releases=drops), T0, fast=True)
    assert len(result.alerts) == 1
    embed = notifier.payloads[0]["embeds"][0]
    assert embed["title"] == "Star — Big Album"
    assert "Also out: *Side Single*" in embed["description"]
    assert {"rel:1|big album", "rel:1|side single"} <= set(st["releases"])


def test_cold_artists_and_old_or_junk_releases_are_ignored(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    cold = chart("Nobody", "Deep Cut", 95, source="genre:Jazz", artist_id="2")
    fetchers = FakeFetchers(charts=hot_charts() + [cold], releases=[
        release("Nobody", "New Song - Single", artist_id="2"),    # artist heat too low
        release("Star", "Last Week - Single", release=T0 - 5 * DAY),
        release("Star", "Brand New (Remix) - Single"),
        release("Star", "Hits (Live at the Garden)", track_count=20),
    ])
    result, _ = run(cfg, st, fetchers, T0)
    assert [a for a in result.eligible if a.kind == "new"] == []


def test_release_posted_by_an_earlier_version_is_not_posted_again(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    st["releases"]["rel:c-1-Posted Earlier - Single"] = T0 - HOUR   # earlier versions keyed by collection id
    fetchers = FakeFetchers(charts=hot_charts(), releases=[release("Star", "Posted Earlier - Single"),
                                                           release("Star", "Actually New - Single")])
    _, notifier = run(cfg, st, fetchers, T0, fast=True)
    assert len(notifier.payloads) == 1 and "Actually New" in notifier.payloads[0]["embeds"][0]["title"]


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
    charts, drops = [], []
    for i in range(10):
        charts.append(chart(f"Artist{i}", "Hit", i + 1, artist_id=f"a{i}"))
        drops.append(release(f"Artist{i}", f"New {i} - Single", artist_id=f"a{i}"))
    st = bootstrapped(cfg, FakeFetchers(charts=charts))
    fetchers = FakeFetchers(charts=charts, releases=drops)
    r1, _ = run(cfg, st, fetchers, T0, fast=True)
    r2, _ = run(cfg, st, fetchers, T0 + 20, fast=True)
    r3, _ = run(cfg, st, fetchers, T0 + 40, fast=True)
    assert [len(r.alerts) for r in (r1, r2, r3)] == [3, 1, 0]
    # the hottest artists go first
    assert [a.track["artist"] for a in r1.alerts] == ["Artist0", "Artist1", "Artist2"]


def test_failed_post_is_retried_next_check(cfg):
    st = bootstrapped(cfg, FakeFetchers(charts=hot_charts()))
    fetchers = FakeFetchers(charts=hot_charts(), releases=[release("Star", "Brand New - Single")])
    run(cfg, st, fetchers, T0, notifier=RecordingNotifier(ok=False), fast=True)
    assert "rel:1|brand new" not in st["releases"]
    _, notifier = run(cfg, st, fetchers, T0 + 20, fast=True)
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


def test_retired_coming_soon_state_is_dropped(tmp_path):
    path = tmp_path / "state.json"
    state.save(path, {**state.empty(), "upcoming": {"up:x": [1, 2]}, "upcoming_seeded": True})
    loaded = state.load(path)
    assert "upcoming" not in loaded and "upcoming_seeded" not in loaded


def test_alert_payload_respects_discord_limits(cfg):
    track = {"artist": "A" * 300, "title": "B" * 300, "release": T0, "pos": {}, "key": "a|b", "anorm": "a"}
    payload = notify.alert_payload(pipeline.Alert("viral", "a|b", 70, 50, track, [track]), T0, cfg)
    embed = payload["embeds"][0]
    assert len(embed["title"]) <= 256
    assert payload["allowed_mentions"] == {"parse": []}
