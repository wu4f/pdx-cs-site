"""HTML cleanup borrowed from gdoc2site.py, plus a text extractor."""
from __future__ import annotations
import re
import urllib.parse
from bs4 import BeautifulSoup


def _filter_bold_italic(css_text: str) -> str:
    pattern = r"([^{]+)\{([^}]+)\}"
    out = []
    for selector, content in re.findall(pattern, css_text):
        selector = selector.strip()
        if "." not in selector:
            continue
        kept = []
        for prop in content.split(";"):
            if ":" not in prop:
                continue
            key, value = prop.split(":", 1)
            if key.strip().lower() in ("font-weight", "font-style", "text-decoration"):
                kept.append(f"{key.strip()}:{value.strip()}")
        if kept:
            if ";" in selector:
                selector = selector.split(";")[-1]
            out.append(f"{selector}{{{';'.join(kept)}}}")
    return "\n".join(out)


def _unwrap_google_url(url: str) -> str:
    if not url.startswith("https://www.google.com/url"):
        return url
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if q.get("q"):
        return urllib.parse.unquote(q["q"][0])
    return url


def clean_exported_html(raw_html: str) -> tuple[str, str, str]:
    """Return (body_html, style_html, plain_text) from a Google-exported page."""
    soup = BeautifulSoup(raw_html, "html.parser")

    # Unwrap google.com/url redirects so all links point at the real target.
    for a in soup.find_all("a", href=True):
        a["href"] = _unwrap_google_url(a["href"])

    # WCAG 2.4.4 / 4.1.2 (link purpose / name, role, value):
    # Drop <a> tags that have no accessible name and no image/SVG inside.
    # Google's export occasionally produces these from invisible characters.
    for a in soup.find_all("a"):
        has_text = bool(a.get_text(strip=True))
        has_image = a.find(["img", "svg"]) is not None
        has_label = a.get("aria-label") or a.get("title")
        if not has_text and not has_image and not has_label:
            a.unwrap()

    body_html = ""
    body_tag = soup.find("body")
    if body_tag:
        body_tag.name = "div"
        body_html = body_tag.prettify()

    style_html = ""
    style_tag = soup.find("style")
    if style_tag:
        style_html = (
            f'<style type="text/css">{_filter_bold_italic(style_tag.prettify())}</style>'
        )

    # Plain text for LLM context: strip everything down to readable text.
    text = ""
    if body_tag:
        text = re.sub(r"\s+", " ", body_tag.get_text(" ", strip=True)).strip()

    return body_html, style_html, text
