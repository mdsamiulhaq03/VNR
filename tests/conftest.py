import os

# Test windows listen for the game hook on a free port of their own, so a real game
# running on this PC (sending to the normal port) can't reach them.
os.environ.setdefault("VNR_HOOK_PORT", "0")
# Test windows never start the real VoiceStudio, nor open the first-launch guide.
os.environ.setdefault("VNR_NO_AUTOSTART", "1")
os.environ.setdefault("VNR_NO_SETUP_GUIDE", "1")
