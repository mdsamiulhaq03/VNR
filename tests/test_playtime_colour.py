"""Play time, play log summaries and name colours."""
from datetime import date, datetime

from PIL import Image, ImageDraw

from app import name_colour as nc
from app.playtime import PlayClock, PlayLog, fmt_duration, milestones


# --------------------------------------------------------------- play clock
def test_clock_counts_only_while_counting(tmp_path):
    log = PlayLog(tmp_path / "p.json")
    t = datetime(2026, 11, 14, 22, 0).timestamp()
    c = PlayClock(log, "G", now=t)
    c.tick(t, True)
    c.tick(t + 4, True)           # +4 s
    c.tick(t + 8, True)           # +4 s
    c.tick(t + 20, False)         # away: not counted
    c.tick(t + 30, False)
    c.tick(t + 31, True)          # back: the step that started away isn't counted
    c.tick(t + 33, True)          # +2 s
    c.tick(t + 1000, True)        # hang / sleep: capped at MAX_STEP
    assert abs(c.session.seconds - (8 + 2 + PlayClock.MAX_STEP)) < 1e-6
    assert c.session.days == {"2026-11-14": c.session.seconds}


def test_clock_lines_delays_and_persistence(tmp_path):
    log = PlayLog(tmp_path / "p.json")
    c = PlayClock(log, "G", now=1000.0)
    c.line("Hello there friend.", "hook")
    c.line("Hi.", "ocr", new_char=True, fixed=True)
    c.delay(0.5)
    c.delay(0.7)
    c.flush(2000.0)
    again = PlayLog(tmp_path / "p.json")
    (s,) = again.sessions("G")
    assert (s.lines, s.words, s.new_chars, s.fixed) == (2, 4, 1, 1)
    assert s.sources == {"hook": 1, "ocr": 1}
    assert abs(s.avg_delay - 0.6) < 1e-9 and s.end == 2000.0
    c.flush(3000.0)                                   # same session: updated, not added
    assert len(PlayLog(tmp_path / "p.json").sessions("G")) == 1


def test_log_records_and_days(tmp_path):
    log = PlayLog(tmp_path / "p.json")
    for day, secs in ((10, 600), (11, 1200), (12, 300), (14, 60)):
        t = datetime(2026, 11, day, 20, 0).timestamp()
        c = PlayClock(log, "G", now=t)
        c.session.seconds = secs
        c.session.days = {date(2026, 11, day).isoformat(): secs}
        c.line("one two three", "ocr")
        c.flush(t + secs)
    r = log.records("G")
    assert r["sessions"] == 4 and r["total"] == 2160 and r["longest"] == 1200
    assert r["best_day"] == ("2026-11-11", 1200) and r["streak"] == 3
    assert r["days_played"] == 4 and r["words"] == 12
    days = log.last_days("G", 7, today=date(2026, 11, 14))
    assert days[0][0] == "2026-11-08" and days[-1] == ("2026-11-14", 60)


def test_seen_notes_hidden(tmp_path):
    log = PlayLog(tmp_path / "p.json")
    log.mark_seen("G", "a.rpy:1")
    log.mark_seen("G", "a.rpy:1")
    log.mark_seen("G", "a.rpy:2")
    log.set_notes("G", "Day 8, Aida route")
    log.set_hidden("G", True)
    log.save()
    again = PlayLog(tmp_path / "p.json")
    assert again.seen_count("G") == 2
    assert again.game("G")["notes"] == "Day 8, Aida route" and again.game("G")["hidden"]


def test_milestones_and_format():
    assert milestones(1200, 12, 600, 7) == ["100 lines", "1,000 lines", "1 hour", "10 hours",
                                             "500 lines with one character", "3 days in a row",
                                             "7 days in a row"]
    assert fmt_duration(3725) == "1:02:05" and fmt_duration(3725, short=True) == "1 h 02 m"
    assert fmt_duration(300, short=True) == "5 m"


# ------------------------------------------------------------- name colours
def _name_img(rgb, bg=(60, 50, 40)):
    im = Image.new("RGB", (160, 40), bg)
    ImageDraw.Draw(im).rectangle((10, 8, 90, 30), fill=rgb)
    return im


def test_measure_coloured_and_white_names():
    assert nc.dist(nc.measure(_name_img((38, 213, 92))), (38, 213, 92)) < 20
    assert nc.dist(nc.measure(_name_img((250, 250, 250))), (250, 250, 250)) < 20


def test_guess_only_when_colours_differ():
    chars = {"Mila": {}, "Aida": {}}
    for _ in range(3):
        nc.learn(chars["Mila"], [38, 213, 92])
        nc.learn(chars["Aida"], [230, 119, 218])
    assert nc.colour_coded(chars)
    assert nc.guess(chars, [40, 205, 95]) == "Mila"
    assert nc.guess(chars, [10, 10, 250]) is None
    same = {"A": {"name_colour": [250, 250, 250]}, "B": {"name_colour": [245, 248, 250]}}
    assert not nc.colour_coded(same) and nc.guess(same, [250, 250, 250]) is None


def test_script_colour_is_not_overwritten():
    rec = {"name_colour": [38, 213, 92], "name_colour_source": "script"}
    nc.learn(rec, [0, 0, 0])
    assert rec["name_colour"] == [38, 213, 92]
    assert nc.hex_to_rgb("#26d55c") == [38, 213, 92] and nc.hex_to_rgb("#fff") == [255] * 3
