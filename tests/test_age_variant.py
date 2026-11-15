""""Younger Zalton" / "Old Zalton": the same voice a step younger or older."""
import tempfile
from pathlib import Path

from app import presets, voices
from app.config import Config


def _cfg():
    c = Config(Path(tempfile.mkdtemp()) / "s.json")
    z = c.character("G", "Zalton")
    z["gender"] = "male"
    return c, z


def test_detects_age_variants():
    others = {"Zalton": {}, "Mila": {}}
    assert voices.age_variant("Younger Zalton", others) == ("Zalton", -1)
    assert voices.age_variant("little mila", others) == ("Mila", -1)
    assert voices.age_variant("Old Zalton", others) == ("Zalton", 1)
    assert voices.age_variant("Zalton (young)", others) == ("Zalton", -1)
    assert voices.age_variant("Younger Orion", others) is None          # no such character
    assert voices.age_variant("Oldman", others) is None


def test_younger_keeps_the_voice_higher_and_still_adult():
    c, z = _cfg()
    y = c.character("G", "Younger Zalton")
    assert y["preset"] == z["preset"] and y.get("seed") == z.get("seed")
    assert y["variant_of"] == "Zalton"
    by = presets.by_id(c.settings.presets)
    dz, dy = voices.effective(z, by), voices.effective(y, by)
    assert dy["age"] in voices.ADULT_AGES                                # never a child
    assert voices.PITCHES.index(dy["pitch"]) >= voices.PITCHES.index(dz["pitch"])
    assert voices.voice_key(dy) != voices.voice_key(dz) or dz["pitch"] == "very high pitch"


def test_older_is_older_and_lower():
    c, z = _cfg()
    o = c.character("G", "Old Zalton")
    by = presets.by_id(c.settings.presets)
    dz, do = voices.effective(z, by), voices.effective(o, by)
    assert voices.ADULT_AGES.index(do["age"]) > voices.ADULT_AGES.index(
        dz["age"] if dz["age"] in voices.ADULT_AGES else "young adult") or \
        dz["age"] == "elderly"
    assert voices.PITCHES.index(do["pitch"]) <= voices.PITCHES.index(dz["pitch"])
