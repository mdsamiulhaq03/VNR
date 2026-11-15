"""Speaker-name mood tags: "Zalton (thinking)" is Zalton, said as a thought."""
from app import emotion
from app.emotion import split_speaker_tag
from app.speech_queue import Line, SpeechQueue


def test_tags_become_moods():
    assert split_speaker_tag("Zalton (thinking)") == ("Zalton", "thought")
    assert split_speaker_tag("Zalton (Thoughts)") == ("Zalton", "thought")
    assert split_speaker_tag("Zalton (whispering)") == ("Zalton", "whisper")
    assert split_speaker_tag("Mika [angrily]") == ("Mika", "angry")
    assert split_speaker_tag("Mika (quietly)") == ("Mika", "soft")
    assert split_speaker_tag("Dr. Askarova （whispers）") == ("Dr. Askarova", "whisper")
    assert split_speaker_tag("Ann (off-screen)") == ("Ann", "")
    assert split_speaker_tag("Ann (on the phone)") == ("Ann", "")


def test_unknown_tags_and_plain_names_stay():
    for name in ("Bro (Mark)", "??? (Mika)", "(thinking)", "???", "Zalton", "", "Seo-yeon"):
        assert split_speaker_tag(name) == (name, "")


def test_hint_sets_the_mood():
    d = emotion.parse("Hello there.", hint="whisper")
    assert d.mood == "whisper" and d.variant == "whisper"
    assert emotion.parse("Hello there.", hint="thought").mood == "thought"
    assert emotion.parse("Hello there.").mood == ""


def test_directions_in_the_text_win_over_the_hint():
    assert emotion.parse("*laughs* Hi!", hint="whisper").mood == "laugh"


def test_queue_uses_the_line_hint():
    q = SpeechQueue.__new__(SpeechQueue)
    q.expressive = lambda: True
    q.vocalize = lambda: True
    q.strength = lambda: "normal"
    q.expression_tags = set()
    d = q.direct(Line("g", "Zalton", "Come closer.", mood_hint="whisper"))
    assert d.variant == "whisper"
