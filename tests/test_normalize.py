from radar.models import (
    artwork_600, clean_channel, compile_patterns, make_key, matches_any, parse_time, primary_artist,
    split_artist_title, title_norm, titles_match,
)


def test_featured_artists_collapse_to_one_key():
    expected = "drake|solar eclipse"
    assert make_key("Drake & Don Toliver", "Solar Eclipse") == expected
    assert make_key("Drake", "Solar Eclipse (feat. Don Toliver)") == expected
    assert make_key("Drake, Don Toliver & Yebba", "Solar Eclipse [Explicit]") == expected
    assert make_key("Drake", "Solar Eclipse ft. Don Toliver") == expected


def test_with_credit_and_single_suffix_removed():
    assert title_norm("Dracula (with JENNIE)") == "dracula"
    assert title_norm("Dracula - Single") == "dracula"


def test_youtube_title_parsing():
    artist, title = split_artist_title("Drake - Solar Eclipse (Official Music Video) ft. Don Toliver")
    assert artist == "Drake"
    assert title_norm(title) == "solar eclipse"
    # no dash: fall back to the channel name
    assert split_artist_title("Solar Eclipse", fallback_artist="Drake") == ("Drake", "Solar Eclipse")
    assert clean_channel("DrakeVEVO") == "Drake"
    assert clean_channel("Drake - Topic") == "Drake"


def test_real_youtube_titles():
    # handle mentions + invisible bidi marks, Spanish tags, mismatched brackets
    assert title_norm("Amor Factura (Video Oficial)  ⁨@ArjonaOficial @GrupoFirmeOficial") == "amor factura"
    assert title_norm("OLIVIA LA FLAKA [Oficial Video]") == "olivia la flaka"
    assert title_norm("Marlboro Rojo (Letra/Lyrics)") == "marlboro rojo"
    assert title_norm("James 1:17 (Jesus Is King) [Official Music Video}") == "james 1 17 jesus is king"


def test_youtube_song_artist_order_is_detected():
    from radar.sources.youtube import trending_music

    class Resp:
        status_code = 200

        def json(self):
            return {"items": [
                {"id": "a", "snippet": {"title": "James 1:17 (Jesus Is King) - Kid Rock [Official Music Video}",
                                        "channelTitle": "Kid Rock", "publishedAt": "2026-10-02T00:00:00Z"},
                 "statistics": {"viewCount": "1500000"}},
                {"id": "b", "snippet": {"title": "Solar Eclipse", "channelTitle": "Drake - Topic",
                                        "publishedAt": "2026-10-02T00:00:00Z"}, "statistics": {}},
            ]}

    class Session:
        def get(self, *args, **kwargs):
            return Resp()

    kid_rock, drake = trending_music(Session(), "key", "us", 50)
    assert (kid_rock.artist, kid_rock.title) == ("Kid Rock", "James 1:17 (Jesus Is King)")
    assert (drake.artist, drake.title) == ("Drake", "Solar Eclipse")
    assert kid_rock.extra["views"] == 1_500_000 and drake.extra["views"] is None


def test_accents_and_case_fold():
    assert primary_artist("Beyoncé") == primary_artist("BEYONCE") == "beyonce"
    assert primary_artist("Rosalía x Bad Bunny") == "rosalia"


def test_titles_match_rules():
    assert titles_match("the fate of ophelia", "fate of ophelia")
    assert not titles_match("solar eclipse", "solar eclipse remix")   # remix is a different song
    assert not titles_match("go", "go go go")                         # too short for fuzzy matching
    assert titles_match("go", "go")


def test_junk_filters_from_config(cfg):
    junk = compile_patterns(cfg["filters"]["exclude_title"])
    for title in ["Green Light (Mixed)", "Kitty Kat [Mixed]", "Song (Sped Up)", "Song (Slowed + Reverb)",
                  "Song - Instrumental", "01a0ed3d be9a 73cc 9b80 db19e747aecc", "Inside My Soul (Slow)"]:
        assert matches_any(title, junk), title
    for title in ["Solar Eclipse", "Slow Down", "Live Forever", "Nightmare"]:
        assert not matches_any(title, junk), title


def test_parse_time_formats():
    assert parse_time("2026-10-02") == 1790899200
    assert parse_time("2026-10-02T07:00:00Z") == 1790924400
    assert parse_time("2026-10-02T00:00:00-07:00") == 1790924400
    assert parse_time(None) is None
    assert parse_time("not a date") is None


def test_artwork_upscale():
    url = "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/a/b.rgb.jpg/170x170bb.png"
    assert artwork_600(url).endswith("/600x600bb.jpg")
    deezer = "https://cdn-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg"
    assert artwork_600(deezer) == deezer
