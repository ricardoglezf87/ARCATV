import re
import unicodedata
import warnings
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup


class MangaOniError(RuntimeError):
    pass


MANGA_ONI_HOSTS = {"manga-oni.com", "www.manga-oni.com"}
MANGA_ONI_HEADERS = {
    "Accept-Language": "es-ES,es;q=0.9",
    "User-Agent": "Mozilla/5.0 ARCATV/2.0",
}


class MangaOniClient:
    def __init__(self, base_url="https://manga-oni.com", session=None, timeout=20, enabled=True):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self._enabled = enabled

    @property
    def enabled(self):
        return bool(self._enabled)

    def get_manga_chapters(self, manga_url):
        if not self.enabled:
            return []
        manga_url = normalize_manga_url(manga_url, self.base_url)
        if not manga_url:
            return []

        try:
            response = get_with_ssl_fallback(
                self.session,
                manga_url,
                headers=MANGA_ONI_HEADERS,
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MangaOniError("Manga Oni no respondio correctamente.") from exc

        return parse_manga_chapters(response.text, manga_url)


def get_with_ssl_fallback(session, url, **kwargs):
    try:
        return session.get(url, **kwargs)
    except requests.exceptions.SSLError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            return session.get(url, verify=False, **kwargs)


def normalize_manga_url(url, base_url="https://manga-oni.com"):
    absolute_url = urljoin(f"{base_url.rstrip('/')}/", (url or "").strip())
    parsed = urlparse(absolute_url)
    if parsed.hostname not in MANGA_ONI_HOSTS:
        return ""
    match = re.search(r"/manga/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not match:
        return ""
    return f"https://manga-oni.com/manga/{match.group(1)}/"


def default_manga_url(title):
    folded = unicodedata.normalize("NFKD", str(title or ""))
    ascii_title = "".join(character for character in folded if not unicodedata.combining(character))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.casefold()).strip("-")
    return f"https://manga-oni.com/manga/{slug}/" if slug else ""


def parse_manga_chapters(html, manga_url):
    soup = BeautifulSoup(html or "", "html.parser")
    chapters = {}
    for link in soup.select('a[href*="/lector/"]'):
        href = urljoin(manga_url, link.get("href") or "")
        reader_match = re.search(r"/lector/([^/?#]+)/([0-9]+)/?", href, flags=re.IGNORECASE)
        if not reader_match:
            continue

        text = " ".join(link.get_text(" ", strip=True).split())
        chapter_match = re.search(
            r"Cap[i\u00ed]tulo\s+([0-9]+(?:\.[0-9]+)?)(?:\s*[\u2014\u2013-]\s*(.*?))?(?:\s+Reciente)?$",
            text,
            flags=re.IGNORECASE,
        )
        if not chapter_match:
            continue

        chapter = chapter_match.group(1)
        title = (chapter_match.group(2) or "").strip()
        reader_url = f"https://manga-oni.com/lector/{reader_match.group(1)}/{reader_match.group(2)}/"
        chapters[chapter] = {
            "id": f"manga-oni:{reader_match.group(2)}",
            "chapter": chapter,
            "chapter_number": float(chapter),
            "title": title,
            "volume": "",
            "language": "es",
            "group": "Manga Oni",
            "publish_at": None,
            "pages": 0,
            "official_url": reader_url,
            "download_url": f"{reader_url}cascada/",
            "source": "manga_oni",
        }

    return sorted(chapters.values(), key=lambda item: item["chapter_number"])
