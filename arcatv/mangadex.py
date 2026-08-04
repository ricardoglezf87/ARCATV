import re
import uuid

import requests

from .anilist import clean_anilist_text

try:
    import truststore
except ImportError:  # pragma: no cover - dependency is installed from requirements.
    truststore = None
else:
    truststore.inject_into_ssl()


MANGADEX_ID_OFFSET = 4_000_000_000
MANGADEX_COVER_BASE_URL = "https://uploads.mangadex.org/covers"
MANGADEX_GENRE_TRANSLATIONS = {
    "Action": "Acción",
    "Adventure": "Aventura",
    "Award Winning": "Premiado",
    "Comedy": "Comedia",
    "Crime": "Crimen",
    "Drama": "Drama",
    "Fantasy": "Fantasía",
    "Historical": "Historia",
    "Horror": "Terror",
    "Mystery": "Misterio",
    "Psychological": "Psicológico",
    "Romance": "Romance",
    "Sci-Fi": "Ciencia ficción",
    "Slice of Life": "Slice of Life",
    "Sports": "Deportes",
    "Supernatural": "Sobrenatural",
    "Thriller": "Suspense",
}
MANGADEX_STATUS_TRANSLATIONS = {
    "ongoing": "En publicación",
    "completed": "Finalizado",
    "hiatus": "En pausa",
    "cancelled": "Cancelado",
}
MANGADEX_DEMOGRAPHIC_TRANSLATIONS = {
    "shounen": "Shounen",
    "shoujo": "Shoujo",
    "josei": "Josei",
    "seinen": "Seinen",
}


class MangaDexError(RuntimeError):
    pass


def synthetic_mangadex_manga_id(mangadex_id):
    value = uuid.UUID(str(mangadex_id)).int % 4_000_000_000_000_000_000
    return MANGADEX_ID_OFFSET + value


def first_localized_value(values, preferred=("es", "es-la", "en", "ja-ro", "ja")):
    values = values or {}
    for language in preferred:
        if values.get(language):
            return values[language]
    for value in values.values():
        if value:
            return value
    return ""


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


class MangaDexClient:
    def __init__(
        self,
        base_url="https://api.mangadex.org",
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
            "/manga",
            [
                ("title", query),
                ("limit", limit),
                ("contentRating[]", "safe"),
                ("contentRating[]", "suggestive"),
                ("contentRating[]", "erotica"),
                ("includes[]", "cover_art"),
                ("includes[]", "author"),
                ("includes[]", "artist"),
                ("order[relevance]", "desc"),
            ],
        ).get("data", [])

    def get_manga(self, manga_id):
        return self._get(
            f"/manga/{manga_id}",
            [
                ("includes[]", "cover_art"),
                ("includes[]", "author"),
                ("includes[]", "artist"),
            ],
        ).get("data") or {}

    def get_author(self, author_id):
        return self._get(f"/author/{author_id}").get("data") or {}

    def get_manga_feed(self, manga_id, languages=None, offset=0, limit=100):
        params = [
            ("limit", limit),
            ("offset", offset),
            ("includes[]", "scanlation_group"),
            ("order[chapter]", "asc"),
        ]
        for language in languages or ["es", "es-la", "en"]:
            params.append(("translatedLanguage[]", language))
        return self._get(f"/manga/{manga_id}/feed", params)

    def _get(self, path, params=None):
        if not self.enabled:
            raise MangaDexError("MangaDex no está configurado.")

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
            payload = response.json()
        except requests.exceptions.SSLError as exc:
            raise MangaDexError(
                "No se pudo validar el certificado SSL de MangaDex. "
                "Revisa los certificados de Windows."
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise MangaDexError("MangaDex no respondió correctamente.") from exc

        if payload.get("result") == "error":
            errors = payload.get("errors") or []
            detail = errors[0].get("detail") if errors else None
            raise MangaDexError(detail or "MangaDex devolvió un error.")
        return payload


def mangadex_relationships(item, relationship_type):
    return [
        relationship
        for relationship in item.get("relationships") or []
        if relationship.get("type") == relationship_type
    ]


def normalize_mangadex_manga(item):
    attributes = item.get("attributes") or {}
    mangadex_id = item["id"]
    title = first_localized_value(attributes.get("title")) or "Sin título"
    original_name = first_localized_value(attributes.get("title"), preferred=("ja-ro", "ja", "en", "es")) or title
    cover = next(iter(mangadex_relationships(item, "cover_art")), {})
    cover_file = (cover.get("attributes") or {}).get("fileName")
    tags = []
    demographic = MANGADEX_DEMOGRAPHIC_TRANSLATIONS.get(attributes.get("publicationDemographic"))
    if demographic:
        tags.append(demographic)
    for tag in attributes.get("tags") or []:
        tag_name = first_localized_value((tag.get("attributes") or {}).get("name"), preferred=("es", "en"))
        if tag_name:
            tags.append(MANGADEX_GENRE_TRANSLATIONS.get(tag_name, tag_name))

    genres = []
    for tag in tags:
        if tag and tag not in genres:
            genres.append(tag)

    last_chapter = numeric_text(attributes.get("lastChapter"))
    last_volume = numeric_text(attributes.get("lastVolume"))

    return {
        "id": synthetic_mangadex_manga_id(mangadex_id),
        "name": title,
        "original_name": original_name,
        "premiered": f"{attributes['year']}-01-01" if attributes.get("year") else None,
        "status": MANGADEX_STATUS_TRANSLATIONS.get(attributes.get("status"), attributes.get("status") or "Sin estado"),
        "language": (attributes.get("originalLanguage") or "").upper() or "Sin idioma",
        "genres": genres[:8],
        "summary": clean_anilist_text(first_localized_value(attributes.get("description"))),
        "image_url": f"/mangas/portadas/{mangadex_id}/{cover_file}" if cover_file else None,
        "official_url": f"https://mangadex.org/title/{mangadex_id}",
        "network": "MangaDex",
        "chapters": parse_number(last_chapter),
        "volumes": parse_number(last_volume),
        "latest_chapter": last_chapter,
        "mangadex_id": mangadex_id,
        "source": "mangadex",
        "source_label": "MangaDex",
        "_normalized": True,
    }


def normalize_mangadex_author(relationship):
    attributes = relationship.get("attributes") or {}
    name = attributes.get("name") or "Sin nombre"
    return {
        "id": relationship["id"],
        "name": name,
        "native_name": None,
        "role": "Autor" if relationship.get("type") == "author" else "Arte",
        "biography": "",
        "image_url": None,
        "official_url": f"https://mangadex.org/author/{relationship['id']}",
    }


def normalize_mangadex_chapter(item):
    attributes = item.get("attributes") or {}
    group = next(iter(mangadex_relationships(item, "scanlation_group")), {})
    group_name = (group.get("attributes") or {}).get("name")
    chapter = numeric_text(attributes.get("chapter"))
    title = attributes.get("title") or ""
    language = attributes.get("translatedLanguage") or ""
    official_url = attributes.get("externalUrl") or f"https://mangadex.org/chapter/{item['id']}"
    return {
        "id": item["id"],
        "chapter": chapter,
        "chapter_number": parse_number(chapter),
        "title": title,
        "volume": numeric_text(attributes.get("volume")),
        "language": language,
        "group": group_name,
        "publish_at": attributes.get("publishAt"),
        "pages": attributes.get("pages") or 0,
        "official_url": official_url,
        "source": "mangadex",
    }


def dedupe_chapters_by_number(chapters):
    language_priority = {"es": 0, "es-la": 1, "en": 2}
    best = {}
    without_number = []
    for chapter in chapters:
        number = chapter.get("chapter")
        if not number:
            without_number.append(chapter)
            continue
        current = best.get(number)
        if not current:
            best[number] = chapter
            continue
        if language_priority.get(chapter.get("language"), 99) < language_priority.get(current.get("language"), 99):
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
