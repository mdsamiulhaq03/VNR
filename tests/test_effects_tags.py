"""Phone / radio voice effects, and sentence tags from the player."""
import numpy as np

from app import emotion
from app.audio import effects


def _tone(freq, sr=24000, secs=0.5):
    t = np.arange(int(sr * secs)) / sr
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _energy(x):
    return float(np.mean(x * x))


def test_phone_cuts_lows_and_highs_keeps_the_middle():
    sr = 24000
    for fx in (effects.phone, effects.radio):
        low, mid, high = _tone(80, sr), _tone(1000, sr), _tone(8000, sr)
        mix = low + mid + high
        y = fx(mix, sr)
        assert y.shape == mix.shape and y.dtype == np.float32
        assert not np.isnan(y).any() and np.abs(y).max() <= 1.0
        spec = np.abs(np.fft.rfft(y))
        f = np.fft.rfftfreq(len(y), 1 / sr)
        at = lambda hz: spec[np.argmin(np.abs(f - hz))]           # noqa: E731
        assert at(1000) > 10 * at(80) and at(1000) > 10 * at(8000)


def test_effect_names():
    assert effects.get("phone") is effects.phone and effects.get("RADIO") is effects.radio
    assert effects.get("") is None and effects.get("nope") is None


def test_tags_give_effects():
    assert emotion.tag_effect("Mila (on the phone)") == "phone"
    assert emotion.tag_effect("Mila (phone)") == "phone"
    assert emotion.tag_effect("Ops (radio)") == "radio"
    assert emotion.tag_effect("Captain [intercom]") == "radio"
    assert emotion.tag_effect("Zalton (thinking)") == ""
    assert emotion.tag_effect("Zalton") == ""
    assert emotion.split_speaker_tag("Mila (on the phone)") == ("Mila", "")


def test_hint_with_effect_keeps_the_mood():
    assert emotion.split_hint("whisper+phone") == ("whisper", "phone")
    assert emotion.split_hint("+radio") == ("", "radio")
    assert emotion.parse("Hello.", hint="whisper+phone").mood == "whisper"


def test_player_reports_the_playing_sentence(monkeypatch):
    from app.audio import player as player_mod
    p = player_mod.Player()
    monkeypatch.setattr(p, "_ensure_stream", lambda sr: setattr(p, "_sr", sr))
    p.enqueue(np.zeros(1000, np.float32), 24000, tag=(7, 0, 2))
    p.enqueue(np.zeros(1000, np.float32), 24000, tag=(7, 1, 2))
    out = np.zeros((600, 1), np.float32)
    p._callback(out, 600, None, None)
    tag, frac = p.current()
    assert tag == (7, 0, 2) and 0.5 < frac < 0.7
    p._callback(out, 600, None, None)
    assert p.current()[0] == (7, 1, 2)
    p.stop()
    assert p.current() == (None, 0.0)
