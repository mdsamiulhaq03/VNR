import tempfile
from pathlib import Path

from app import voices
from app.config import NARRATOR, Config
from app.emotion import add_tags, strip_tags
from app.name_split import separated_top, split_name
from app.voice_bank import VoiceBank

TAGS = {"[laughter]", "[sigh]", "[question-ei]", "[question-ah]", "[question-oh]",
        "[surprise-ah]", "[surprise-oh]", "[surprise-wa]", "[dissatisfaction-hnn]"}


def cfg():
    return Config(Path(tempfile.mkdtemp()) / "settings.json")


# ----------------------------------------------------------- delete / merge
def test_delete_game_keeps_others():
    c = cfg()
    c.touch_game("A", "a.exe", "A")
    c.touch_game("B", "b.exe", "B")
    c.character("A", "Sayori")
    c.character("B", "Kenji")
    c.game_fixups("A").append({"find": "x", "replace": "y"})
    c.window_regions("a.exe")["text"] = [0, 0, 1, 1]
    c.settings.ocr_profile = "A"
    c.delete_game("A")
    s = c.settings
    assert "A" not in s.games and "A" not in s.voices and "A" not in s.fixups
    assert "a.exe" not in s.regions_by_exe
    assert "Kenji" in s.voices["B"] and s.ocr_profile == "B"


def test_delete_character_frees_preset():
    c = cfg()
    p1 = c.character("G", "Sayori")["preset"]
    c.character("G", "Natsuki")
    assert c.delete_character("G", "Sayori")
    assert c.character("G", "Monika")["preset"] == p1      # freed preset re-used
    assert not c.delete_character("G", NARRATOR)


def test_merge_character():
    c = cfg()
    for _ in range(3):
        c.count_line("G", "Sayori")
    c.count_line("G", "Sayorl")
    c.merge_character("G", "Sayorl", "Sayori")
    chars = c.characters("G")
    assert "Sayorl" not in chars and chars["Sayori"]["lines"] == 4
    rule = c.game_fixups("G")[0]
    assert rule["find"] == "Sayorl" and rule["replace"] == "Sayori"
    assert c.settings.games["G"]["lines"] == 4


def test_prune_only_orphans():
    c = cfg()

    class VS:
        deleted = []

        def delete_profile(self, pid):
            self.deleted.append(pid)
            return True

    bank = VoiceBank(c, VS())
    used = c.used_voice_keys()
    some_preset_key = next(iter(used))
    c.settings.vs_voice_bank = {some_preset_key: {"id": "keep"}, "gone|1": {"id": "drop"}}
    ids = bank.prune(c.used_voice_keys())
    assert ids == ["drop"] and "gone|1" not in c.settings.vs_voice_bank
    assert some_preset_key in c.settings.vs_voice_bank


# ---------------------------------------------------------------- emotions
def test_emotion_tags():
    assert add_tags("Ahaha! So funny.", TAGS) == "[laughter] Ahaha! So funny."
    assert add_tags("*laughs* You idiot.", TAGS) == "[laughter] You idiot."
    assert add_tags("*sigh* Fine.", TAGS) == "[sigh] Fine."
    assert add_tags("Eh?! Really?", TAGS) == "[question-ei] Really?"
    assert add_tags("Ah! You scared me.", TAGS) == "[surprise-ah] You scared me."
    assert add_tags("Hmph. Whatever.", TAGS) == "[dissatisfaction-hnn] Whatever."


def test_emotion_leaves_normal_text_alone():
    for s in ("The heater was on. Oh, and the cat.", "He said ahead of time it was fine.",
              "I will wash the dishes.", "Ahead, the road split in two."):
        assert add_tags(s, TAGS) == s
    assert add_tags("Ahaha!", set()) == "Ahaha!"               # engine has no tags
    assert strip_tags("[laughter] Ahaha!") == "Ahaha!"


# ------------------------------------------------------------- names
def box(top, h=30, left=20, w=300):
    return (h, top, left, w)


def test_layout_name_detection():
    named = [box(10), box(70), box(110)]                       # big gap under line 1
    flowing = [box(10), box(50), box(90)]                      # even spacing
    assert separated_top(named) is True and separated_top(flowing) is False
    assert split_name(["Mr. Tanaka", "Class is starting.", "Sit down."], [], "auto", named) == \
        ("Mr. Tanaka", "Class is starting. Sit down.")
    assert split_name(["???", "Who goes there?", "Show yourself!"], [], "auto", named) == \
        ("???", "Who goes there? Show yourself!")


def test_no_name_means_narrator():
    flowing = [box(10), box(50), box(90)]
    # Short, title-looking first line that flows with the paragraph: narration, nothing cut.
    assert split_name(["Morning Came", "and with it the rain that", "never seemed to stop."],
                      [], "auto", flowing) == \
        ("", "Morning Came and with it the rain that never seemed to stop.")
    assert split_name(["The next morning", "I woke up late."], [], "auto") == \
        ("", "The next morning I woke up late.")
    assert split_name(["It was quiet."], [], "auto") == ("", "It was quiet.")
    assert split_name(["The bell rang as we reached", "the school gate."], [], "auto") == \
        ("", "The bell rang as we reached the school gate.")


# ------------------------------------------------------------ auto-advance
import threading
import time

from app.speech_queue import Line, SpeechQueue
from app.tts.base import TTSEngine


class _Eng(TTSEngine):
    name = "voicestudio"

    def synth(self, text, voice, speed=1.0, **o):
        time.sleep(0.05)
        return text.encode()


class _Player:
    def __init__(self, play_for=0.3):
        self.until = 0.0
        self.play_for = play_for
        self.paused = False

    def enqueue_bytes(self, b):
        self.until = time.monotonic() + self.play_for

    def is_playing(self):
        return time.monotonic() < self.until

    def stop(self):
        self.until = 0.0


def test_line_done_fires_after_playback():
    pl = _Player(0.3)
    q = SpeechQueue(_Eng(), None, pl)
    done = []
    ev = threading.Event()
    q.on_done = lambda line: (done.append((line.text, time.monotonic())), ev.set())
    t0 = time.monotonic()
    q.say(Line("g", "A", "Hello there my friend."))
    assert ev.wait(3)
    assert done[0][0] == "Hello there my friend." and done[0][1] - t0 >= 0.3
    q.shutdown()


def test_interrupted_line_does_not_advance():
    pl = _Player(0.5)
    q = SpeechQueue(_Eng(), None, pl)
    done = []
    q.on_done = lambda line: done.append(line.text)
    q.say(Line("g", "A", "First line that gets cut off."))
    time.sleep(0.2)
    q.say(Line("g", "A", "Second line."))
    time.sleep(1.2)
    assert done == ["Second line."]
    q.shutdown()
