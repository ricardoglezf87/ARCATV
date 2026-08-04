import html
import re

import requests

try:
    import truststore
except ImportError:  # pragma: no cover - dependency is installed from requirements.
    truststore = None
else:
    truststore.inject_into_ssl()


ANILIST_MANGA_ID_OFFSET = 3_000_000_000

MANGA_GENRE_TRANSLATIONS = {
    "Action": "Acción",
    "Adventure": "Aventura",
    "Comedy": "Comedia",
    "Drama": "Drama",
    "Ecchi": "Ecchi",
    "Fantasy": "Fantasía",
    "Horror": "Terror",
    "Mahou Shoujo": "Magia",
    "Mecha": "Mecha",
    "Music": "Música",
    "Mystery": "Misterio",
    "Psychological": "Psicológico",
    "Romance": "Romance",
    "Sci-Fi": "Ciencia ficción",
    "Slice of Life": "Slice of Life",
    "Sports": "Deportes",
    "Supernatural": "Sobrenatural",
    "Thriller": "Suspense",
}

MANGA_STATUS_TRANSLATIONS = {
    "FINISHED": "Finalizado",
    "RELEASING": "En publicación",
    "NOT_YET_RELEASED": "Pendiente",
    "CANCELLED": "Cancelado",
    "HIATUS": "En pausa",
}

MANGA_FORMAT_TRANSLATIONS = {
    "MANGA": "Manga",
    "NOVEL": "Novela",
    "ONE_SHOT": "One-shot",
}

MANGA_FIELDS = """
fragment MangaFields on Media {
  id
  title { romaji english native }
  description(asHtml: false)
  countryOfOrigin
  format
  status
  chapters
  volumes
  startDate { year month day }
  genres
  tags { name rank isGeneralSpoiler isMediaSpoiler }
  averageScore
  isAdult
  coverImage { large }
  siteUrl
}
"""

STAFF_FIELDS = """
fragment StaffFields on Staff {
  id
  name { full native }
  image { large }
  siteUrl
  description(asHtml: false)
}
"""

SEARCH_MANGA_QUERY = MANGA_FIELDS + """
query SearchManga($search: String!, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(
      search: $search,
      type: MANGA,
      isAdult: false,
      sort: [SEARCH_MATCH, POPULARITY_DESC]
    ) {
      ...MangaFields
    }
  }
}
"""

GET_MANGA_QUERY = MANGA_FIELDS + STAFF_FIELDS + """
query GetManga($id: Int!) {
  Media(id: $id, type: MANGA) {
    ...MangaFields
    staff(sort: RELEVANCE, perPage: 12) {
      edges {
        role
        node { ...StaffFields }
      }
    }
    recommendations(sort: RATING_DESC, perPage: 24) {
      nodes {
        rating
        mediaRecommendation { ...MangaFields }
      }
    }
  }
}
"""

TRENDING_MANGA_QUERY = MANGA_FIELDS + """
query TrendingManga($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: MANGA, isAdult: false, sort: [TRENDING_DESC, POPULARITY_DESC]) {
      ...MangaFields
    }
  }
}
"""

SEARCH_STAFF_QUERY = STAFF_FIELDS + """
query SearchStaff($search: String!, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    staff(search: $search, sort: SEARCH_MATCH) {
      ...StaffFields
    }
  }
}
"""

GET_STAFF_QUERY = MANGA_FIELDS + STAFF_FIELDS + """
query GetStaff($id: Int!) {
  Staff(id: $id) {
    ...StaffFields
    staffMedia(type: MANGA, sort: [SCORE_DESC, POPULARITY_DESC], perPage: 48) {
      nodes { ...MangaFields }
      edges { staffRole }
    }
  }
}
"""


class AniListError(RuntimeError):
    pass


def synthetic_anilist_manga_id(anilist_id):
    return ANILIST_MANGA_ID_OFFSET + int(anilist_id)


def is_anilist_manga_id(manga_id):
    manga_id = int(manga_id)
    return ANILIST_MANGA_ID_OFFSET <= manga_id < ANILIST_MANGA_ID_OFFSET + 1_000_000_000


def anilist_id_from_manga_id(manga_id):
    return int(manga_id) - ANILIST_MANGA_ID_OFFSET


class AniListClient:
    def __init__(
        self,
        base_url="https://graphql.anilist.co",
        session=None,
        timeout=10,
        enabled=True,
    ):
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout = timeout
        self._enabled = enabled
        self.user_agent = "ARCATV/0.1 (+local-tv-tracker)"

    @property
    def enabled(self):
        return bool(self._enabled)

    def search_manga(self, query, page=1, per_page=12):
        data = self._post(
            SEARCH_MANGA_QUERY,
            {"search": query, "page": page, "perPage": per_page},
        )
        return (((data.get("Page") or {}).get("media")) or [])

    def get_manga(self, manga_id):
        data = self._post(GET_MANGA_QUERY, {"id": int(manga_id)})
        return data.get("Media") or {}

    def get_manga_recommendations(self, manga_id):
        manga = self.get_manga(manga_id)
        nodes = (((manga.get("recommendations") or {}).get("nodes")) or [])
        return [
            node["mediaRecommendation"]
            for node in nodes
            if node.get("mediaRecommendation")
        ]

    def get_trending_manga(self, page=1, per_page=24):
        data = self._post(
            TRENDING_MANGA_QUERY,
            {"page": page, "perPage": per_page},
        )
        return (((data.get("Page") or {}).get("media")) or [])

    def search_staff(self, query, page=1, per_page=8):
        data = self._post(
            SEARCH_STAFF_QUERY,
            {"search": query, "page": page, "perPage": per_page},
        )
        return (((data.get("Page") or {}).get("staff")) or [])

    def get_staff(self, staff_id):
        data = self._post(GET_STAFF_QUERY, {"id": int(staff_id)})
        return data.get("Staff") or {}

    def get_staff_manga(self, staff_id):
        staff = self.get_staff(staff_id)
        staff_media = staff.get("staffMedia") or {}
        nodes = staff_media.get("nodes") or []
        edges = staff_media.get("edges") or []
        manga = []
        for index, node in enumerate(nodes):
            item = dict(node)
            if index < len(edges):
                item["staff_role"] = edges[index].get("staffRole") or ""
            manga.append(item)
        return manga

    def _post(self, query, variables=None):
        if not self.enabled:
            raise AniListError("AniList no está configurado.")

        try:
            response = self.session.post(
                self.base_url,
                json={"query": query, "variables": variables or {}},
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.SSLError as exc:
            raise AniListError(
                "No se pudo validar el certificado SSL de AniList. "
                "Revisa los certificados de Windows."
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise AniListError("AniList no respondió correctamente.") from exc

        errors = payload.get("errors") or []
        if errors:
            message = errors[0].get("message") or "AniList devolvió un error."
            raise AniListError(message)
        return payload.get("data") or {}


def clean_anilist_text(value):
    if not value:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fuzzy_date_to_iso(date_value):
    if not date_value or not date_value.get("year"):
        return None
    year = int(date_value["year"])
    month = int(date_value.get("month") or 1)
    day = int(date_value.get("day") or 1)
    return f"{year:04d}-{month:02d}-{day:02d}"


def translated_manga_genres(media):
    names = []
    for genre in media.get("genres") or []:
        names.append(MANGA_GENRE_TRANSLATIONS.get(genre, genre))

    for tag in media.get("tags") or []:
        if tag.get("isGeneralSpoiler") or tag.get("isMediaSpoiler"):
            continue
        if (tag.get("rank") or 0) < 80:
            continue
        name = tag.get("name")
        if name:
            names.append(MANGA_GENRE_TRANSLATIONS.get(name, name))

    deduped = []
    for name in names:
        if name and name not in deduped:
            deduped.append(name)
    return deduped[:8]


def manga_title(media):
    title = media.get("title") or {}
    return (
        title.get("english")
        or title.get("romaji")
        or title.get("native")
        or "Sin título"
    )


def normalize_anilist_manga(media):
    title = media.get("title") or {}
    name = manga_title(media)
    original_name = title.get("romaji") or title.get("native") or name
    score = media.get("averageScore") or 0
    anilist_id = media["id"]

    return {
        "id": synthetic_anilist_manga_id(anilist_id),
        "name": name,
        "original_name": original_name,
        "premiered": fuzzy_date_to_iso(media.get("startDate")),
        "status": MANGA_STATUS_TRANSLATIONS.get(media.get("status"), media.get("status") or "Sin estado"),
        "language": media.get("countryOfOrigin") or "Sin país",
        "genres": translated_manga_genres(media),
        "summary": clean_anilist_text(media.get("description")),
        "image_url": ((media.get("coverImage") or {}).get("large")),
        "official_url": media.get("siteUrl") or f"https://anilist.co/manga/{anilist_id}",
        "network": MANGA_FORMAT_TRANSLATIONS.get(media.get("format"), media.get("format")),
        "chapters": media.get("chapters"),
        "volumes": media.get("volumes"),
        "rating": round(score / 10, 1) if score else 0,
        "anilist_id": anilist_id,
        "source": "anilist",
        "source_label": "AniList",
        "is_adult": bool(media.get("isAdult")),
        "_normalized": True,
    }


def normalize_anilist_staff_member(edge):
    node = edge.get("node") or edge
    image = node.get("image") or {}
    name = node.get("name") or {}
    return {
        "id": node["id"],
        "name": name.get("full") or name.get("native") or "Sin nombre",
        "native_name": name.get("native"),
        "role": edge.get("role") or edge.get("staffRole") or "",
        "biography": clean_anilist_text(node.get("description")),
        "image_url": image.get("large"),
        "official_url": node.get("siteUrl") or f"https://anilist.co/staff/{node['id']}",
    }
