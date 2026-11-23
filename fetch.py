import re
from typing import Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import MAX_PAGE_CHARS, REQUEST_TIMEOUT, USER_AGENT

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def get(url: str) -> requests.Response:
    resp = _session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    return resp


def fetch_html(url: str) -> Tuple[str, str]:
    """Return (final_url, html)."""
    resp = get(url)
    return resp.url, resp.text


def is_image(url: str) -> bool:
    """True if the URL responds 200 with an image content type (headers only, body not downloaded)."""
    try:
        with _session.get(url, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True) as resp:
            return resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image")
    except requests.RequestException:
        return False


def find_ics_link(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".ics" in href or "ical" in href.lower() or href.startswith("webcal:"):
            href = href.replace("webcal://", "https://", 1)
            return urljoin(base_url, href)
    return None


def html_to_text(html: str, base_url: str) -> str:
    """Flatten a page into text the LLM can read, keeping links and image URLs inline."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if src and not src.startswith("data:"):
            img.replace_with(f" [image: {urljoin(base_url, src)}] ")
        else:
            img.decompose()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        if href.startswith(("mailto:", "tel:", "#", "javascript:")) or not text:
            continue
        a.replace_with(f" {text} [link: {urljoin(base_url, href)}] ")

    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:MAX_PAGE_CHARS]
