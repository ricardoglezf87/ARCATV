import hashlib
import re

import requests

from .anilist import clean_anilist_text

try:
    import truststore
except ImportError:  # pragma: no cover - dependency is installed from requirements.
    truststore = None
else:
    truststore.inject_into_ssl()


COMICK_MANGA_ID_OFFSET = 5_000_000_000
COMICK_COVER_BASE_URL = "https://meo.comick.pictures"
COMICK_STATUS_TRANSLATIONS = {
    1: "En publicacion",
    2: "Finalizado",
    3: "Cancelado",
    4: "En pausa",
}
COMICK_GENRE_TRANSLATIONS = {
    "Action": "Accion",
    "Adventure": "Aventura",
    "Award Winning": "Premiado",
    "Comedy": "Comedia",
    "Drama": "Drama",
    "Fantasy": "Fantasia",
    "Historical": "Historia",
    "Horror": "Terror",
    "Mystery": "Misterio",
    "Psychological": "Psicologico",
    "Romance": "Romance",
    "Sci-Fi": "Ciencia ficcion",
    "Slice of Life": "Slice of Life",
    "Sports": "Deportes",
    "Supernatural": "Sobrenatural",
    "Thriller": "Suspense",
}
COMICK_DEMOGRAPHIC_TRANSLATIONS = {
    1: "Shounen",
    2: "Shoujo",
    3: "Seinen",
    4: "Josei",
}


class ComicKError(RuntimeError):
    pass


def synthetic_comick_manga_id(comick_id):
    digest = hashlib.sha256(str(comick_id).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") % 1_000_000_000
    return COMICK_MANGA_ID_OFFSET + value


def is_comick_manga_id(manga_id):
    manga_id = int(manga_id)
    return COMICK_MANGA_ID_OFFSET <= manga_id < COMICK_MANGA_ID_OFFSET + 1_000_000_000


def numeric_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_number(value):
    text = numeric_text(value)
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def first_title(titles, preferred=("es", "en", "ja-ro", "ja")):
    titles = titles or []
    for language in preferred:
        for title in titles:
            if title.get("lang") == language and title.get("title"):
                return title["title"]
    for title in titles:
        if title.get("title"):
            return title["title"]
    return ""


def cover_proxy_url(cover):
    b2key = (cover or {}).get("b2key")
    if not b2key:
        return None
    return f"/mangas/portadas/comick/{b2key}"


def normalize_slug(value):
    return (value or "").strip("/")


class ComicKClient:
    def __init__(
        self,
        base_url="https://api.comick.dev",
        session=None,
        timeout=10,
        enabled=True,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self._enabled = enabled
        self.user_agent = "ARCATV/0.1 (+local-tv-tracker)"

    @property
    def enabled(self):
        return bool(self._enabled)

    def search_manga(self, query, limit=12):
        return self._get(
            "/v1.0/search",
            {
                "q": query,
                "limit": limit,
                "page": 1,
            },
        )

    def get_manga(self, comick_id):
        payload = self._get(f"/comic/{comick_id}")
        return payload or {}

    def get_manga_chapters(self, comick_id, language="es", page=1, limit=1000):
        return self._get(
            f"/comic/{comick_id}/chapters",
            {
                "lang": language,
                "page": page,
                "limit": limit,
            },
        )

    def _get(self, path, params=None):
        if not self.enabled:
            raise ComicKError("ComicK no esta configurado.")

        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params or {},
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError as exc:
            raise ComicKError(
                "No se pudo validar el certificado SSL de ComicK. "
                "Revisa los certificados de Windows."
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise ComicKError("ComicK no respondio correctamente.") from exc


def coerce_comic(item):
    return (item or {}).get("comic") or item or {}


def normalize_comick_manga(item):
    comic = coerce_comic(item)
    comick_id = comic.get("hid")
    title = comic.get("title") or first_title(comic.get("md_titles")) or "Sin titulo"
    original_name = first_title(comic.get("md_titles"), preferred=("ja-ro", "ja", "en", "es")) or title
    cover = next(iter(comic.get("md_covers") or []), {})
    slug = normalize_slug(comic.get("slug"))

    genres = []
    demographic = COMICK_DEMOGRAPHIC_TRANSLATIONS.get(comic.get("demographic"))
    if demographic:
        genres.append(demographic)
    for genre in comic.get("md_comic_md_genres") or []:
        name = ((genre.get("md_genres") or {}).get("name") or "").strip()
        if name:
            genres.append(COMICK_GENRE_TRANSLATIONS.get(name, name))

    deduped_genres = []
    for genre in genres:
        if genre and genre not in deduped_genres:
            deduped_genres.append(genre)

    return {
        "id": synthetic_comick_manga_id(comick_id),
        "comick_id": comick_id,
        "mangadex_id": None,
        "name": title,
        "original_name": original_name,
        "premiered": f"{comic['year']}-01-01" if comic.get("year") else None,
        "status": COMICK_STATUS_TRANSLATIONS.get(comic.get("status"), "Sin estado"),
        "language": (comic.get("country") or "").upper() or "Sin idioma",
        "genres": deduped_genres[:8],
        "summary": clean_anilist_text(comic.get("desc")),
        "image_url": cover_proxy_url(cover),
        "official_url": f"https://comick.dev/comic/{comick_id}/{slug}" if slug else f"https://comick.dev/comic/{comick_id}",
        "network": "ComicK",
        "chapters": parse_number(comic.get("last_chapter")),
        "volumes": parse_number(comic.get("final_volume")),
        "latest_chapter": numeric_text(comic.get("last_chapter")),
        "source": "comick",
        "source_label": "ComicK",
        "_normalized": True,
    }


def normalize_comick_chapter(item):
    chapter = numeric_text(item.get("chap"))
    groups = item.get("group_name") or []
    group = ", ".join(group for group in groups if group) if isinstance(groups, list) else groups
    return {
        "id": f"comick:{item.get('hid') or item.get('id')}",
        "chapter": chapter,
        "chapter_number": parse_number(chapter),
        "title": item.get("title") or "",
        "volume": numeric_text(item.get("vol")),
        "language": item.get("lang") or "",
        "group": group,
        "publish_at": item.get("publish_at") or item.get("created_at"),
        "pages": 0,
        "official_url": None,
        "source": "comick",
    }


def chapter_score(chapter):
    language_priority = {"es": 0, "es-la": 1, "en": 2}
    groups = (chapter.get("group") or "").casefold()
    official_priority = 0 if any(name in groups for name in ("mangaplus", "official")) else 1
    has_title = 0 if (chapter.get("title") or "").strip() else 1
    return (
        has_title,
        language_priority.get(chapter.get("language"), 99),
        official_priority,
        chapter.get("publish_at") or "",
    )


def dedupe_chapters_by_number(chapters):
    best = {}
    without_number = []
    for chapter in chapters:
        number = chapter.get("chapter")
        if not number:
            without_number.append(chapter)
            continue
        current = best.get(number)
        if current is None or chapter_score(chapter) < chapter_score(current):
            best[number] = chapter

    deduped = list(best.values()) + without_number
    return sorted(
        deduped,
        key=lambda chapter: (
            chapter.get("chapter_number") is None,
            chapter.get("chapter_number") or 0,
            chapter.get("chapter") or "",
        ),
    )
