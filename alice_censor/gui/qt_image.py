"""PIL <-> Qt image conversion.

Always goes through RGBA (4 bytes/pixel) rather than RGB888 (3 bytes/pixel)
deliberately. QImage's 4-argument constructor assumes each scanline is
padded to a 32-bit boundary, but Pillow's tobytes("raw", "RGB") packs rows
tightly with no padding. For image widths where width*3 isn't a multiple of
4 that mismatch causes diagonal shearing and corruption, a well-known
PIL-to-QImage gotcha. RGBA sidesteps it entirely since 4 bytes/pixel is
always aligned regardless of width.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage, QPixmap


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, QImage.Format_RGBA8888)
    # .copy() forces QImage to own its own buffer, since `data` is a local
    # Python bytes object that would otherwise be freed once this function
    # returns, corrupting the image.
    return QPixmap.fromImage(qimage.copy())
