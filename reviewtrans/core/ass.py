from __future__ import annotations

from .geometry import scale_factor, subtitle_text
from .models import Segment, SubtitleStyle

ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}


def ass_color(hex_rgb: str, opacity: float = 1.0) -> str:
    """#RRGGBB + độ đục 0..1 -> &HAABBGGRR (alpha của ASS: 00 = đục)."""
    value = (hex_rgb or "#000000").strip().lstrip("#")
    if len(value) == 8:  # #AARRGGBB
        value = value[2:]
    if len(value) != 6:
        value = "000000"
    r, g, b = value[0:2], value[2:4], value[4:6]
    alpha = int(round(255 * (1.0 - max(0.0, min(1.0, opacity)))))
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    hours, cs = divmod(cs, 360000)
    minutes, cs = divmod(cs, 6000)
    secs, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def escape_text(text: str) -> str:
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _style_line(name: str, style: SubtitleStyle, width: int, height: int, box: bool) -> str:
    s = scale_factor(height)
    font_size = style.font_size * s
    margin_v = int(round(style.margin_v / 100.0 * height))
    margin_h = int(round(style.margin_h / 100.0 * width))
    alignment = ALIGNMENT.get(style.position, 2)
    bold = -1 if style.bold else 0
    italic = -1 if style.italic else 0
    if box:
        primary = "&HFF000000"
        outline_colour = ass_color(style.bg_color, style.bg_opacity / 100.0)
        back = outline_colour
        border_style, outline, shadow = 3, style.bg_padding * s, 0
    else:
        primary = ass_color(style.text_color)
        outline_colour = ass_color(style.outline_color)
        back = ass_color(style.shadow_color, style.shadow_opacity / 100.0)
        border_style, outline, shadow = 1, style.outline_width * s, style.shadow_depth * s
    return (
        f"Style: {name},{style.font_family},{font_size:.2f},{primary},&H000000FF,{outline_colour},{back},"
        f"{bold},{italic},0,0,100,100,0,0,{border_style},{outline:.2f},{shadow:.2f},{alignment},"
        f"{margin_h},{margin_h},{margin_v},1"
    )


def build_ass(segments: list[Segment], style: SubtitleStyle, width: int, height: int) -> str:
    width = width or 1920
    height = height or 1080
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line("Main", style, width, height, box=False),
    ]
    if style.bg_enabled:
        lines.append(_style_line("Box", style, width, height, box=True))
    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for seg in segments:
        text = subtitle_text(seg.display_text(), style)
        if not text or seg.end <= seg.start:
            continue
        body = escape_text(text)
        start, end = ass_time(seg.start), ass_time(seg.end)
        if style.bg_enabled:
            lines.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{body}")
        lines.append(f"Dialogue: 1,{start},{end},Main,,0,0,0,,{body}")
    return "\n".join(lines) + "\n"
