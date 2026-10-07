from conftest import DAY, HOUR, T0, chart

from radar import scoring, state


def test_heat_counts_each_chart_once():
    one = state.empty()
    scoring.update_artist_heat(one, [chart("A", "Song 1", 1, source="genre:Soundtrack")], T0, 60)
    many = state.empty()
    scoring.update_artist_heat(many, [chart("A", f"Song {i}", i, source="genre:Soundtrack") for i in range(1, 11)], T0, 60)
    assert one["artists"]["1"]["heat"] == many["artists"]["1"]["heat"]


def test_heat_scale():
    st = state.empty()
    scoring.update_artist_heat(st, [
        chart("Star", "Hit", 1, artist_id="star"),
        chart("Star", "Hit", 1, source="genre:Pop", artist_id="star"),
        chart("Niche", "Song", 1, source="genre:Jazz", artist_id="niche"),
        chart("Deep", "Song", 90, source="genre:Jazz", artist_id="deep"),
    ], T0, 60)
    heat = {aid: a["heat"] for aid, a in st["artists"].items()}
    assert heat["star"] > 80
    assert 38 <= heat["niche"] < 40     # a genre-chart #1 just clears the default NEW DROP bar (38)
    assert heat["deep"] < 30


def test_heat_decays_but_manual_artists_keep_a_floor():
    artist = {"heat": 80.0, "heat_ts": T0}
    assert scoring.effective_heat(artist, T0 + 7 * DAY, 60) == 40.0
    assert scoring.effective_heat({**artist, "manual": True}, T0 + 70 * DAY, 60) == 60


def test_momentum_detects_climbs_and_new_entries():
    track = {"pos": {"apple_top": 20, "genre:Pop": 3},
             "hist": [[T0 - 13 * HOUR, {"apple_top": 50}, None], [T0 - HOUR, {"apple_top": 20}, None]]}
    value, climbs = scoring.momentum(track, T0, history_ok=True)
    assert climbs == {"apple_top": 30, "genre:Pop": "new"}
    # 0.4 * best position (#20 on Apple -> 0.81) + 0.3 * climb (maxed) + 0.3 * breadth (2 of 4 charts)
    assert abs(value - (0.4 * 0.81 + 0.3 + 0.15)) < 1e-9
    # without enough history we don't claim anything is climbing
    assert scoring.momentum(track, T0, history_ok=False)[1] == {}


def test_small_moves_are_noise():
    track = {"pos": {"apple_top": 18}, "hist": [[T0 - 13 * HOUR, {"apple_top": 20}, None]]}
    assert scoring.momentum(track, T0, history_ok=True)[1] == {}


def test_youtube_velocity_scale():
    def vel(views, hours):
        return scoring.youtube_velocity({"youtube": {"views": views, "published": T0 - hours * HOUR, "ts": T0}}, T0)[0]
    assert vel(1_000 * 10, 10) == 0.0          # 1K views/hr
    assert abs(vel(10_000 * 10, 10) - 0.5) < 1e-9
    assert vel(500_000 * 10, 10) == 1.0        # capped
    stale = {"youtube": {"views": 10**9, "published": T0 - HOUR, "ts": T0 - 7 * HOUR}}
    assert scoring.youtube_velocity(stale, T0)[0] == 0.0


def test_youtube_velocity_prefers_recent_delta():
    track = {"youtube": {"views": 1_200_000, "published": T0 - 100 * DAY, "ts": T0},
             "hist": [[T0 - 4 * HOUR, {}, 800_000]]}
    value, vph = scoring.youtube_velocity(track, T0)
    assert vph == 100_000
    assert value == 1.0


def test_recency():
    assert scoring.recency(T0 - 2 * DAY, T0, 14) == 1.0
    assert scoring.recency(T0 - 14 * DAY, T0, 14) == 0.5
    assert 0.5 < scoring.recency(T0 - 8 * DAY, T0, 14) < 1.0


def test_score_range_and_moving_flag(cfg):
    still = {"release": T0 - DAY, "pos": {"apple_top": 1}, "hist": [[T0 - 13 * HOUR, {"apple_top": 1}, None]]}
    score, parts = scoring.score_track(still, 100, T0, True, cfg)
    assert 0 <= score <= 100
    assert parts["moving"] is False      # #1 but not moving: not "going viral"
    climbing = {"release": T0 - DAY, "pos": {"apple_top": 5}, "hist": [[T0 - 13 * HOUR, {"apple_top": 40}, None]]}
    score2, parts2 = scoring.score_track(climbing, 50, T0, True, cfg)
    assert parts2["moving"] is True
