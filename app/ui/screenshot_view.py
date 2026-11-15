"""Screenshot tab: drop or paste a screenshot and it's read aloud by itself."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .components import Avatar, button, icon_button, label, section
from .settings_view import pil_to_pixmap

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}


def qimage_to_pil(qimg) -> Image.Image | None:
    if qimg is None or qimg.isNull():
        return None
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.ReadWrite)
    qimg.save(buf, "PNG")
    import io
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


class DropZone(QLabel):
    """Shows the screenshot (with the found name/dialogue outlined) and accepts drops."""
    dropped = Signal(list)          # [PIL images]

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._img: Image.Image | None = None
        self._boxes: list = []
        self._name_box = None
        self._hover = False
        self._empty()

    def _empty(self):
        self.setText("")
        self._empty_mode = True
        self._style()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._img is not None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        pm = theme.pixmap("image-plus", "ACCENT" if self._hover else "TEXT_2", 28)
        p.drawPixmap(int(cx - 14), int(cy - 52), pm)
        f = p.font()
        f.setPointSizeF(11)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(0, cy - 14, self.width(), 24), Qt.AlignCenter,
                   "Drop a screenshot, or press Ctrl+V")
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_2))
        p.drawText(QRectF(0, cy + 12, self.width(), 20), Qt.AlignCenter,
                   "Take one with Win+Shift+S or PrtScn. It's read aloud by itself.")
        p.end()

    def _style(self):
        self.setObjectName("dropArea")
        self.setProperty("hover", self._hover)
        self.style().unpolish(self)
        self.style().polish(self)

    # ---- drag & drop ----
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasImage() or any(Path(u.toLocalFile()).suffix.lower() in IMAGE_EXT
                                for u in md.urls()):
            self._hover = True
            self._style()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hover = False
        self._style()

    def dropEvent(self, e):
        self._hover = False
        self._style()
        self.dropped.emit(images_from_mime(e.mimeData()))
        e.acceptProposedAction()

    # ---- showing a result ----
    def show_image(self, img: Image.Image, boxes=None, name_box=None):
        self._img, self._boxes, self._name_box = img, list(boxes or []), name_box
        self.setText("")
        self._render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()

    def _render(self):
        if self._img is None:
            return
        pm = pil_to_pixmap(self._img)
        w, h = max(80, self.width() - 16), max(60, self.height() - 16)
        scaled = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        k = scaled.width() / max(1, pm.width())
        p = QPainter(scaled)
        p.setRenderHint(QPainter.Antialiasing)
        if self._name_box:
            l, t, bw, bh = self._name_box
            p.setPen(QPen(QColor(theme.NAME), 2))
            p.drawRoundedRect(QRectF((l - 4) * k, (t - 4) * k, (bw + 8) * k, (bh + 8) * k), 5, 5)
        for b in self._boxes:
            bh, top, left, bw = b
            if not bw:
                continue
            p.setPen(QPen(QColor(theme.DIALOGUE), 1.5))
            p.drawRoundedRect(QRectF((left - 3) * k, (top - 3) * k, (bw + 6) * k, (bh + 6) * k),
                              4, 4)
        p.end()
        self.setPixmap(scaled)


def images_from_mime(md) -> list[Image.Image]:
    out = []
    for u in md.urls():
        f = Path(u.toLocalFile())
        if f.suffix.lower() in IMAGE_EXT and f.exists():
            try:
                out.append(Image.open(f).convert("RGB"))
            except Exception:
                pass
    if not out and md.hasImage():
        img = qimage_to_pil(md.imageData())
        if img is not None:
            out.append(img)
    return out


class ScreenshotView(QWidget):
    read_request = Signal(object)      # PIL image -> MainWindow reads and speaks it
    replay = Signal(str, str)          # speaker, text

    def __init__(self, game_getter):
        super().__init__()
        self.game_getter = game_getter
        self._queue: list[Image.Image] = []
        self.busy = False
        self._last = ("", "")
        self.setObjectName("page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        self.lbl_game = label("", "mono")

        self.zone = DropZone()
        self.zone.dropped.connect(self.add_images)
        lay.addWidget(self.zone, 1)

        res = QFrame()
        res.setObjectName("panel")
        rl = QVBoxLayout(res)
        rl.setContentsMargins(16, 12, 16, 12)
        who = QHBoxLayout()
        who.setSpacing(10)
        self.av = Avatar("?", 30)
        who.addWidget(self.av)
        self.lbl_speaker = QLabel("No screenshot read yet")
        self.lbl_speaker.setObjectName("speaker")
        who.addWidget(self.lbl_speaker, 1)
        self.lbl_method = label("", "mono")
        who.addWidget(self.lbl_method)
        rl.addLayout(who)
        self.lbl_text = QLabel("")
        self.lbl_text.setObjectName("dialogue")
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self.lbl_text)
        br = QHBoxLayout()
        self.b_again = button("Read again", "play",
                              slot=lambda: self._last[1] and self.replay.emit(*self._last))
        self.b_copy = button("Copy text", "copy", "ghost",
                             slot=lambda: QGuiApplication.clipboard().setText(self._last[1]))
        self.lbl_queue = label("", "mono")
        br.addWidget(self.b_again)
        br.addWidget(self.b_copy)
        br.addStretch(1)
        br.addWidget(self.lbl_queue)
        rl.addLayout(br)
        lay.addWidget(res)
        self._buttons(False)

        sc = QShortcut(QKeySequence.Paste, self)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self.paste)
        self.refresh_game()

    def install_toolbar(self, header):
        header.add(self.lbl_game, None,
                   button("Paste", "clipboard-paste", "primary",
                          "Read the screenshot on the clipboard   Ctrl+V", slot=self.paste))

    def refresh_game(self):
        self.lbl_game.setText(f"as {self.game_getter()}")
        self.lbl_game.setToolTip("Characters, voices and word memory of this game are used")

    def _buttons(self, on: bool):
        self.b_again.setEnabled(on)
        self.b_copy.setEnabled(on)

    def paste(self):
        cb = QApplication.clipboard()
        imgs = images_from_mime(cb.mimeData())
        if not imgs:
            self.lbl_method.setText("The clipboard has no image: take a screenshot first "
                                    "(Win+Shift+S or PrtScn)")
            return
        self.add_images(imgs)

    def add_images(self, imgs: list):
        self._queue += [i for i in imgs if i is not None]
        self._next()

    def _next(self):
        if self.busy or not self._queue:
            self._show_queue()
            return
        img = self._queue.pop(0)
        self._show_queue()
        self.busy = True
        self.zone.show_image(img)
        self.lbl_speaker.setText("Reading…")
        self.lbl_text.setText("")
        self.lbl_method.setText("")
        self._buttons(False)
        self.read_request.emit(img)

    def _show_queue(self):
        n = len(self._queue)
        self.lbl_queue.setText(f"{n} more screenshot{'s' if n != 1 else ''} waiting"
                               if n else "")

    def show_result(self, img, result, speaker: str, text: str):
        """Called by MainWindow when the screenshot has been read (and is being spoken)."""
        self.zone.show_image(img, result.boxes if result else [],
                             result.name_box if result else None)
        if text:
            from ..config import NARRATOR
            self.av.set_name(speaker)
            self.lbl_speaker.setText("Narrator" if speaker == NARRATOR else speaker)
            self.lbl_text.setText(text)
            self.lbl_method.setText({"saved areas": "read from this game's saved areas",
                                     "auto-detected": "found automatically"}.get(
                result.method, "") if result else "")
            self._last = (speaker, text)
            self._buttons(True)
        else:
            self.lbl_speaker.setText("No dialogue found")
            self.lbl_text.setText("Try a screenshot where the dialogue box is visible, or set "
                                  "this game's areas (F6) so they're used for screenshots too.")
            self.lbl_method.setText("")

    def done(self):
        """The current screenshot has been spoken: read the next one."""
        self.busy = False
        self._next()
