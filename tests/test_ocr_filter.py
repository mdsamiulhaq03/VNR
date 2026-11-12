from app.config import DEFAULT_IGNORE_WORDS
from app.sources.ocr_filter import correct, filter_lines, junk_reason, word_score


def lines(*texts, h=30):
    return [(t, h) for t in texts]


def test_menu_row_dropped():
    r = filter_lines(lines("Sayori", "Hey, wait for me!", "Back History Skip Auto Save Q.Save Q.Load Prefs"),
                     DEFAULT_IGNORE_WORDS)
    assert r.lines == ["Sayori", "Hey, wait for me!"]
    assert r.dropped[0][1] == "menu buttons"


def test_dialogue_with_menu_words_kept():
    r = filter_lines(lines("Let's go back to the history club and save it!"), DEFAULT_IGNORE_WORDS)
    assert r.lines == ["Let's go back to the history club and save it!"]


def test_menu_tail_stripped():
    r = filter_lines(lines("See you tomorrow. Back Skip Auto"), DEFAULT_IGNORE_WORDS)
    assert r.lines == ["See you tomorrow."]


def test_junk_dropped():
    for junk in ("l1 ~ ;:", "| i . ,", "~~ -- ==", "xkcd vbnt"):
        assert junk_reason(junk) is not None, junk
    r = filter_lines(lines("Natsuki", "l1 ~ ;:", "Ugh, you're so slow."), [])
    assert r.lines == ["Natsuki", "Ugh, you're so slow."]


def test_short_real_lines_kept():
    for ok in ("Oh.", "Hm?", "I see.", "Hmm...", "A-ah!", "Shh!", "I"):
        assert junk_reason(ok) is None, ok


def test_tiny_text_dropped():
    r = filter_lines([("Sayori", 32), ("Good morning!", 30), ("ver 1.2 auto", 12)], [])
    assert r.lines == ["Sayori", "Good morning!"]


def test_corrections():
    text, fixes = correct("Tbe rnorning was cold, and | felt tired.", [])
    assert text == "The morning was cold, and I felt tired."
    assert ("rnorning", "morning") in fixes


def test_names_untouched():
    text, _ = correct("Sayori and Natsuki waited for Monika.", ["Sayori", "Natsuki", "Monika"])
    assert text == "Sayori and Natsuki waited for Monika."
    text, _ = correct("Then Kotonoha smiled.", [])          # unknown capitalized name
    assert text == "Then Kotonoha smiled."


def test_word_score():
    assert word_score("The morning was cold.") > word_score("Tbe rnorninq wsa cdol.")
