"""Make a digest HTML self-contained: replace <img src="local.jpg"> with an
inline base64 data URI, so the конспект is a single portable file (Егор's
choice: one HTML). data:/http(s): sources and missing files are left as-is."""
from __future__ import annotations
import base64
import mimetypes
import re
import sys
from pathlib import Path

# Кавычки любые: HTML пишет модель, а одинарные оставляли ссылку на локальный файл
# и ломали «самодостаточность» молча.
_IMG_SRC = re.compile(r"""<img[^>]*?\ssrc=(["'])([^"']+)\1""")


def inline(html_in: str, html_out: str, base_dir: str) -> None:
    html = Path(html_in).read_text(encoding="utf-8")
    base = Path(base_dir)

    def repl(m: re.Match) -> str:
        tag, quote, src = m.group(0), m.group(1), m.group(2)
        if src.startswith(("data:", "http:", "https:")):
            return tag
        fp = base / src
        if not fp.exists():
            print(f"[inline] warning: image not found, left as-is: {fp}")
            return tag
        mime = mimetypes.guess_type(str(fp))[0] or "image/jpeg"
        b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        return tag.replace(f"{quote}{src}{quote}", f'"data:{mime};base64,{b64}"')

    html = _IMG_SRC.sub(repl, html)
    Path(html_out).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # Windows cp1251 console
    if len(sys.argv) != 4:
        raise SystemExit("usage: inline_images.py <html_in> <html_out> <base_dir>")
    inline(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"inlined -> {sys.argv[2]}")
