"""Explicit, host-supplied Unicode fallback fonts for DANFSe PDFs.

Fonts are embedded as PDF subsets. No font files are bundled or copied by this
package. ReportLab's registry is process-global, so registration is serialized
and names are stable; the active font set remains per-render via ContextVar.
"""

from __future__ import annotations

import hashlib
import os
import threading
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


class FontConfigurationError(ValueError):
    """A configured fallback font could not be loaded or lacks a required glyph."""


@dataclass(frozen=True)
class _RegisteredFont:
    name: str
    face: TTFont

    def supports(self, char: str) -> bool:
        return ord(char) in self.face.face.charWidths


_registry_lock = threading.RLock()
_registered: dict[tuple[str, int], _RegisteredFont] = {}
_active: ContextVar["FontSet | None"] = ContextVar("nfse_danfse_font_set", default=None)


def _registered_font(path: str | os.PathLike[str], index: int) -> _RegisteredFont:
    raw_path = os.fspath(path)
    try:
        resolved = str(Path(raw_path).expanduser().resolve(strict=True))
        stat = os.stat(resolved)
    except (OSError, ValueError):
        raise FontConfigurationError("configured PDF fallback font file is unavailable") from None
    key = (resolved, index)
    with _registry_lock:
        result = _registered.get(key)
        if result is not None:
            return result
        name = "NfseFallback" + hashlib.sha256(f"{resolved}:{index}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]
        try:
            font = TTFont(name, resolved, subfontIndex=index)
            pdfmetrics.registerFont(font)
        except Exception:
            raise FontConfigurationError("configured PDF fallback font is invalid or unsupported") from None
        result = _RegisteredFont(name, font)
        _registered[key] = result
        return result


class FontSet:
    """Ordered explicit TTF/TTC fallbacks used after the built-in Helvetica fonts.

    ``fallbacks`` accepts paths, or ``(path, collection_index)`` pairs for TTC
    collections. Use a regular and bold file where faithful bold text matters;
    otherwise the regular font is used for both styles when it supplies a glyph.
    """

    def __init__(self, fallbacks=(), *, bold_fallbacks=None):
        self._regular = tuple(_registered_font(p[0], p[1]) if isinstance(p, tuple) else _registered_font(p, 0)
                              for p in fallbacks)
        bold = fallbacks if bold_fallbacks is None else bold_fallbacks
        self._bold = tuple(_registered_font(p[0], p[1]) if isinstance(p, tuple) else _registered_font(p, 0)
                           for p in bold)

    @staticmethod
    def _needs_shaping(char: str) -> bool:
        cp = ord(char)
        return (0x0590 <= cp <= 0x08FF or 0x0900 <= cp <= 0x0DFF or
                0x0E00 <= cp <= 0x0EFF or 0x1100 <= cp <= 0x11FF or
                0xA960 <= cp <= 0xA97F)

    def runs(self, text: str, base_font: str):
        """Return (font-name, substring) runs; unsupported glyphs fail closed."""
        if not text:
            return []
        bold = base_font == "Helvetica-Bold"
        fonts = self._bold if bold else self._regular
        selected = []
        for char in text:
            if unicodedata.combining(char):
                raise FontConfigurationError("PDF text contains combining marks that need text shaping; DANFSe rendering was stopped")
            if self._needs_shaping(char):
                raise FontConfigurationError("PDF text uses a script requiring complex shaping; DANFSe rendering was stopped")
            if unicodedata.category(char) == "Cc" and char not in "\n\t\r":
                raise FontConfigurationError("PDF text contains an unsupported control character")
            try:
                code = char.encode("cp1252")[0]
            except UnicodeEncodeError:
                code = None
            if char in "\n\t\r" or (code is not None and pdfmetrics.getFont(base_font).encoding.vector[code]):
                selected.append(base_font)
                continue
            match = next((font.name for font in fonts if font.supports(char)), None)
            if match is None:
                raise FontConfigurationError(f"no configured PDF font supports a required glyph (U+{ord(char):04X})")
            selected.append(match)
        runs = []
        for font, char in zip(selected, text):
            if runs and runs[-1][0] == font:
                runs[-1] = (font, runs[-1][1] + char)
            else:
                runs.append((font, char))
        return runs

    def width(self, text: str, base_font: str, size: float) -> float:
        from reportlab.pdfbase.pdfmetrics import stringWidth
        return sum(stringWidth(chunk, font, size) for font, chunk in self.runs(text, base_font))


def current_font_set() -> FontSet | None:
    return _active.get()


def activate(font_set: FontSet | None):
    return _active.set(font_set)


def deactivate(token) -> None:
    _active.reset(token)


# Default policy rejects glyphs outside the built-in WinAnsi range instead of
# allowing Helvetica's silent .notdef substitution.
DEFAULT_FONT_SET = FontSet()
