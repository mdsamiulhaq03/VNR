"""Old "Zalton (thinking)" records merged into Zalton in one go."""
import tempfile
from pathlib import Path

from app.config import Config


def _cfg():
    c = Config(Path(tempfile.mkdtemp()) / "s.json")
    g = "G"
    for name, n in (("Zalton", 10), ("Zalton (thinking)", 4), ("Zalton (whispering)", 2),
                    ("Mila (thinking)", 3), ("Bro (Mark)", 5)):
        c.character(g, name)["lines"] = n
    return c, g


def test_pairs_found():
    c, g = _cfg()
    assert sorted(c.tagged_pairs(g)) == [("Mila (thinking)", "Mila"),
                                         ("Zalton (thinking)", "Zalton"),
                                         ("Zalton (whispering)", "Zalton")]


def test_fold_merges_and_renames():
    c, g = _cfg()
    mila_voice = c.characters(g)["Mila (thinking)"].get("preset")
    done = c.fold_tagged(g)
    chars = c.characters(g)
    assert len(done) == 3
    assert chars["Zalton"]["lines"] == 16                     # 10 + 4 + 2
    assert "Zalton (thinking)" not in chars and "Zalton (whispering)" not in chars
    assert "Mila" in chars and chars["Mila"]["lines"] == 3    # renamed, keeps its voice
    assert chars["Mila"].get("preset") == mila_voice
    assert "Bro (Mark)" in chars                               # not a mood tag


def test_tag_only_aliases_and_fixups_are_removed():
    c, g = _cfg()
    c.characters(g)["Zalton"]["aliases"] = ["Zalton (angrily)", "Zal"]
    c.game_fixups(g).extend([{"find": "Zalton (thinking)", "replace": "Zalton"},
                             {"find": "Zaltn", "replace": "Zalton"}])
    c.fold_tagged(g)
    assert c.characters(g)["Zalton"]["aliases"] == ["Zal"]
    assert [r["find"] for r in c.game_fixups(g)] == ["Zaltn"]
