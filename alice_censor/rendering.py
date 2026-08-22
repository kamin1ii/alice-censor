"""Applies CensorLayer definitions to an image.

This is the single source of truth for turning (image + non-destructive
layer definitions) into a final rendered image. Used by the editor's live
preview, the gallery thumbnails and the export pipeline alike, so "what
you saw in the editor" and "what got exported" can never drift apart.

Layer `rect` is stored as (x, y, w, h) fractions of the image's width/height
(0.0-1.0) rather than pixels, specifically so the same layer definition can
be re-applied to a different image (batch apply across a scene group, or a
re-export after the source PNG changed size).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from . import fonts
from .project import CensorLayer, LayerType

RectPx = tuple[int, int, int, int]  # left, top, right, bottom


class RenderError(ValueError):
    pass


def rect_to_pixels_unclamped(
    rect: tuple[float, float, float, float], image_size: tuple[int, int]
) -> RectPx:
    """Convert a fractional (x, y, w, h) rect to a pixel box (left, top,
    right, bottom) WITHOUT clamping to the image bounds, so the box may
    extend past the edges with a negative left/top or a right/bottom
    beyond width/height. Used for the overlay layer type so a region
    placed partly out of bounds fits its sticker to the full intended size
    rather than squeezing it into the smaller on-canvas remainder. The
    caller clips to the actual canvas afterward.
    """
    width, height = image_size
    x, y, w, h = rect
    return (
        round(x * width),
        round(y * height),
        round((x + w) * width),
        round((y + h) * height),
    )


def rect_to_pixels(rect: tuple[float, float, float, float], image_size: tuple[int, int]) -> RectPx:
    """Convert a fractional (x, y, w, h) rect to a pixel box (left, top,
    right, bottom), clamped to the image bounds. Degenerate or
    out-of-bounds input collapses to an empty, zero-area box rather than
    raising, so a stale layer on a resized image degrades gracefully
    instead of crashing an export.
    """
    width, height = image_size
    left, top, right, bottom = rect_to_pixels_unclamped(rect, image_size)

    left = max(0, min(left, width))
    top = max(0, min(top, height))
    right = max(left, min(right, width))
    bottom = max(top, min(bottom, height))
    return left, top, right, bottom


# Defaults for a text layer, as fractions so they survive a re-render at a
# different resolution the way the rects do.
TEXT_SIZE = 0.05      # of image height
TEXT_PADDING = 0.06   # of the shorter side of the box


def _parse_color(value: str) -> tuple[int, int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) == 6:
        r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
        return r, g, b, 255
    if len(text) == 8:
        r, g, b, a = (int(text[i : i + 2], 16) for i in (0, 2, 4, 6))
        return r, g, b, a
    raise RenderError(f"Invalid color {value!r}: expected #RRGGBB or #RRGGBBAA")


def _apply_solid(image: Image.Image, box: RectPx, params: dict) -> None:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    r, g, b, a = _parse_color(params.get("color", "#000000"))
    opacity = float(params.get("opacity", 1.0))
    a = round(a * max(0.0, min(1.0, opacity)))

    overlay = Image.new("RGBA", (right - left, bottom - top), (r, g, b, a))
    region = image.crop(box).convert("RGBA")
    composited = Image.alpha_composite(region, overlay)
    image.paste(composited.convert(image.mode) if image.mode != "RGBA" else composited, box)


def _apply_blur(image: Image.Image, box: RectPx, params: dict) -> None:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    radius = float(params.get("radius", 12))
    region = image.crop(box)
    blurred = region.filter(ImageFilter.GaussianBlur(radius=max(0.0, radius)))
    image.paste(blurred, box)


def _apply_pixelate(image: Image.Image, box: RectPx, params: dict) -> None:
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return
    block_size = max(1, int(params.get("block_size", 12)))
    region = image.crop(box)
    small_w = max(1, w // block_size)
    small_h = max(1, h // block_size)
    small = region.resize((small_w, small_h), Image.NEAREST)
    mosaic = small.resize((w, h), Image.NEAREST)
    image.paste(mosaic, box)


def _fit_overlay(sticker: Image.Image, box_size: tuple[int, int], fit: str) -> Image.Image:
    box_w, box_h = box_size
    if box_w <= 0 or box_h <= 0:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    sticker_w, sticker_h = sticker.size

    if fit == "stretch":
        return sticker.resize((box_w, box_h), Image.LANCZOS)

    if fit == "tile":
        tiled = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        if sticker_w > 0 and sticker_h > 0:
            for tile_y in range(0, box_h, sticker_h):
                for tile_x in range(0, box_w, sticker_w):
                    tiled.paste(sticker, (tile_x, tile_y), sticker)
        return tiled

    # "contain", the default. Scale to fit inside the box, preserving aspect
    # ratio, centered on a transparent canvas the size of the box.
    scale = min(box_w / sticker_w, box_h / sticker_h) if sticker_w and sticker_h else 1.0
    scaled_size = (max(1, round(sticker_w * scale)), max(1, round(sticker_h * scale)))
    scaled = sticker.resize(scaled_size, Image.LANCZOS)
    canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    offset = ((box_w - scaled_size[0]) // 2, (box_h - scaled_size[1]) // 2)
    canvas.paste(scaled, offset, scaled)
    return canvas


def _apply_overlay(image: Image.Image, nominal_box: RectPx, params: dict, sticker_resolver) -> None:
    """`nominal_box` is the layer's UNCLAMPED pixel box, so it may extend
    past the image edges. The sticker is fit to that full nominal size
    (so it's never squeezed just because part of it is off-image), then
    cropped down to only the portion that overlaps the actual canvas
    before compositing. A region that's entirely off-canvas draws nothing."""
    left, top, right, bottom = nominal_box
    nominal_w, nominal_h = right - left, bottom - top
    if nominal_w <= 0 or nominal_h <= 0:
        return

    img_w, img_h = image.size
    visible_left = max(0, min(left, img_w))
    visible_top = max(0, min(top, img_h))
    visible_right = max(visible_left, min(right, img_w))
    visible_bottom = max(visible_top, min(bottom, img_h))
    if visible_right <= visible_left or visible_bottom <= visible_top:
        return

    sticker_ref = params.get("sticker")
    if not sticker_ref:
        return
    sticker_path = sticker_resolver(sticker_ref) if sticker_resolver else Path(sticker_ref)
    try:
        with Image.open(sticker_path) as raw:
            sticker = raw.convert("RGBA")
    except (OSError, FileNotFoundError) as e:
        raise RenderError(f"Can't load overlay sticker {sticker_ref!r}: {e}") from e

    rotation = float(params.get("rotation", 0))
    if rotation % 360:
        sticker = sticker.rotate(rotation, expand=True, resample=Image.BICUBIC)

    fit = params.get("fit", "contain")
    fitted = _fit_overlay(sticker, (nominal_w, nominal_h), fit)

    opacity = float(params.get("opacity", 1.0))
    if opacity < 1.0:
        r, g, b, a = fitted.split()
        a = a.point(lambda px: round(px * max(0.0, opacity)))
        fitted = Image.merge("RGBA", (r, g, b, a))

    crop_box = (
        visible_left - left,
        visible_top - top,
        visible_right - left,
        visible_bottom - top,
    )
    if crop_box != (0, 0, nominal_w, nominal_h):
        fitted = fitted.crop(crop_box)

    visible_box = (visible_left, visible_top, visible_right, visible_bottom)
    region = image.crop(visible_box).convert("RGBA")
    composited = Image.alpha_composite(region, fitted)
    image.paste(composited.convert(image.mode) if image.mode != "RGBA" else composited, visible_box)


def _apply_text(image: Image.Image, box: RectPx, params: dict) -> None:
    """Draw a caption in the layer's box, over an optional filled panel.

    The box is what the user dragged, so the panel fills it exactly and the
    text is laid out inside. Size is stored as a fraction of image height
    rather than in points, so a caption keeps its proportions if the same
    project is applied to a differently sized image, the same way the rects
    themselves do.

    Text that does not fit is wrapped, and then shrunk until it does. The
    alternative is letting it run outside the box the user drew, which is
    worse than a slightly smaller caption.
    """
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return

    region = image.crop(box).convert("RGBA")
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)

    opacity = max(0.0, min(1.0, float(params.get("opacity", 1.0))))
    if params.get("background", True):
        r, g, b, a = _parse_color(params.get("background_color", "#000000"))
        draw.rectangle((0, 0, width - 1, height - 1), fill=(r, g, b, round(a * opacity)))

    text = str(params.get("text", ""))
    if text.strip():
        try:
            _draw_caption(draw, (width, height), text, params, opacity, image.size[1])
        except UnicodeEncodeError:
            # No real font could be loaded and Pillow fell back to its own
            # bitmap one, which only knows latin-1. Nothing can be drawn for
            # text outside that, and losing the caption is a great deal
            # better than losing the repack it was part of.
            pass

    composited = Image.alpha_composite(region, panel)
    image.paste(composited.convert(image.mode) if image.mode != "RGBA" else composited, box)


def _draw_caption(draw, box_size, text: str, params: dict, opacity: float,
                  image_height: int) -> None:
    box_width, box_height = box_size
    padding = max(0, round(float(params.get("padding", TEXT_PADDING)) * min(box_size)))
    inner_width = max(1, box_width - padding * 2)
    inner_height = max(1, box_height - padding * 2)

    family = str(params.get("font", fonts.DEFAULT_FAMILY))
    wanted = max(fonts.MIN_SIZE, round(float(params.get("size", TEXT_SIZE)) * image_height))
    font, lines, text_height = _fit_text(text, family, wanted, inner_width, inner_height, draw)

    r, g, b, a = _parse_color(params.get("color", "#FFFFFF"))
    fill = (r, g, b, round(a * opacity))
    align = str(params.get("align", "center"))

    y = padding + max(0, (inner_height - text_height) // 2)
    for line in lines:
        line_left, _, line_right, _ = draw.textbbox((0, 0), line, font=font)
        line_width = line_right - line_left
        if align == "left":
            x = padding
        elif align == "right":
            x = box_width - padding - line_width
        else:
            x = (box_width - line_width) // 2
        draw.text((x - line_left, y), line, font=font, fill=fill)
        y += _line_height(draw, font)


def _fit_text(text, family, wanted, inner_width, inner_height, draw):
    """The largest size at or below `wanted` whose wrapped text fits."""
    size = wanted
    while True:
        font = fonts.load(family, size)
        lines = _wrap(text, font, inner_width, draw)
        height = _line_height(draw, font) * len(lines)
        widest = max(
            (draw.textbbox((0, 0), line, font=font)[2] for line in lines), default=0
        )
        if size <= fonts.MIN_SIZE or (height <= inner_height and widest <= inner_width):
            return font, lines, height
        size = max(fonts.MIN_SIZE, int(size * 0.9))


def _line_height(draw, font) -> int:
    """Line spacing, measured rather than guessed.

    From a sample with an ascender, a descender and a full width character,
    so every line in a block gets the same spacing whatever happens to be
    on it. A bitmap fallback font knows only latin-1 and refuses the last
    of those, hence the simpler samples behind it.
    """
    for sample in ("Agあ", "Ag", "A"):
        try:
            top, bottom = draw.textbbox((0, 0), sample, font=font)[1::2]
        except UnicodeEncodeError:
            continue
        if bottom > top:
            return bottom - top + 2
    return max(1, int(getattr(font, "size", 10))) + 2


def _wrap(text: str, font, width: int, draw) -> list[str]:
    """Break text to fit a width, on spaces where there are any.

    Japanese does not use them, so a run with no spaces is broken between
    characters instead. Explicit line breaks in the text are kept.
    """
    lines = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if current and draw.textlength(candidate, font=font) > width:
                lines.extend(_break_run(current, font, width, draw))
                current = word
            else:
                current = candidate
        lines.extend(_break_run(current, font, width, draw))
    return lines or [""]


def _break_run(run: str, font, width: int, draw) -> list[str]:
    if not run or draw.textlength(run, font=font) <= width:
        return [run]
    out, current = [], ""
    for character in run:
        if current and draw.textlength(current + character, font=font) > width:
            out.append(current)
            current = character
        else:
            current += character
    if current:
        out.append(current)
    return out


@dataclass(frozen=True)
class _LayerRenderer:
    """How one layer type turns into pixels.

    `clamped` is the only axis the four types actually differ on besides
    the paint itself. Solid, blur and pixelate all read the region they
    are about to overwrite, so their box must lie inside the image. An
    overlay instead fits its sticker to the layer's full intended size and
    clips afterward, so it needs the unclamped box. Keeping that as data
    rather than a branch is what lets every type go through one dispatch.
    """

    paint: Callable[[Image.Image, RectPx, dict, Any], None]
    clamped: bool = True


_RENDERERS: dict[LayerType, _LayerRenderer] = {
    LayerType.SOLID: _LayerRenderer(lambda img, box, params, _resolver: _apply_solid(img, box, params)),
    LayerType.BLUR: _LayerRenderer(lambda img, box, params, _resolver: _apply_blur(img, box, params)),
    LayerType.PIXELATE: _LayerRenderer(
        lambda img, box, params, _resolver: _apply_pixelate(img, box, params)
    ),
    LayerType.OVERLAY: _LayerRenderer(_apply_overlay, clamped=False),
    LayerType.TEXT: _LayerRenderer(
        lambda img, box, params, _resolver: _apply_text(img, box, params)
    ),
}


def render_layers(
    base_image: Image.Image,
    layers: list[CensorLayer],
    *,
    sticker_resolver=None,
) -> Image.Image:
    """Render `layers` on top of `base_image`, in order. Returns a new
    image, leaving `base_image` untouched. Disabled layers are skipped.

    `sticker_resolver`, if given, maps an overlay layer's `params["sticker"]`
    value, such as a name or a relative path, to an absolute filesystem
    Path. Defaults to treating it as a path directly.
    """
    result = base_image.convert("RGBA") if base_image.mode != "RGBA" else base_image.copy()
    for layer in layers:
        if not layer.enabled:
            continue
        renderer = _RENDERERS[layer.type]
        to_box = rect_to_pixels if renderer.clamped else rect_to_pixels_unclamped
        renderer.paint(result, to_box(layer.rect, result.size), layer.params, sticker_resolver)
    return result if base_image.mode == "RGBA" else result.convert(base_image.mode)
