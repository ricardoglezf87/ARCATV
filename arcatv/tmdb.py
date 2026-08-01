import requests

from .utils import translated_genres, translated_status

try:
    import truststore
except ImportError:  # pragma: no cover - dependency is installed from requirements.
    truststore = None
else:
    truststore.inject_into_ssl()


TMDB_SHOW_ID_OFFSET = 1_000_000_000
TMDB_EPISODE_ID_OFFSET = 1_000_000_000_000
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w342"
TMDB_STILL_BASE_URL = "https://image.tmdb.org/t/p/w500"
TMDB_TV_GENRES = {
    10759: "Action & Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    10762: "Children",
    9648: "Mystery",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
    37: "Western",
}


class TMDbError(RuntimeError):
    pass


def synthetic_tmdb_show_id(tmdb_id):
    return TMDB_SHOW_ID_OFFSET + int(tmdb_id)


def is_tmdb_show_id(show_id):
    return int(show_id) >= TMDB_SHOW_ID_OFFSET


def tmdb_id_from_show_id(show_id):
    return int(show_id) - TMDB_SHOW_ID_OFFSET


def synthetic_tmdb_episode_id(tmdb_episode_id):
    return TMDB_EPISODE_ID_OFFSET + int(tmdb_episode_id)


class TMDbClient:
    def __init__(
        self,
        api_key=None,
        bearer_token=None,
        base_url="https://api.themoviedb.org/3",
        session=None,
        timeout=10,
    ):
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.user_agent = "ARCATV/0.1 (+local-tv-tracker)"

    @property
    def enabled(self):
        return bool(self.api_key or self.bearer_token)

    def search_tv(self, query, language="es-ES", page=1):
        return self._get(
            "/search/tv",
            {"query": query, "language": language, "page": page, "include_adult": "false"},
        ).get("results", [])

    def get_tv(self, series_id, language="es-ES"):
        return self._get(f"/tv/{series_id}", {"language": language})

    def get_season(self, series_id, season_number, language="es-ES"):
        return self._get(f"/tv/{series_id}/season/{season_number}", {"language": language})

    def get_recommendations(self, series_id, language="es-ES", page=1):
        return self._get(
            f"/tv/{series_id}/recommendations",
            {"language": language, "page": page},
        ).get("results", [])

    def get_similar(self, series_id, language="es-ES", page=1):
        return self._get(
            f"/tv/{series_id}/similar",
            {"language": language, "page": page},
        ).get("results", [])

    def get_trending_tv(self, time_window="week", language="es-ES"):
        return self._get(f"/trending/tv/{time_window}", {"language": language}).get("results", [])

    def _get(self, path, params=None):
        if not self.enabled:
            raise TMDbError("TMDb no está configurado.")

        query = dict(params or {})
        headers = {"User-Agent": self.user_agent}
        if self.api_key:
            query["api_key"] = self.api_key
        elif self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"

        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=query,
                headers=headers,
                timeout=self.timeout,
                verify=False,
            )
            print(f"TMDb API request: {response.url} - Status code: {response.status_code}")
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TMDbError("TMDb no respondió correctamente.") from exc


def normalize_tmdb_show(show):
    poster_path = show.get("poster_path")
    networks = show.get("networks") or []
    first_network = networks[0] if networks else {}
    genres = show.get("genres") or []
    if not genres and show.get("genre_names"):
        genres = [{"name": genre} for genre in show["genre_names"]]
    if not genres and show.get("genre_ids"):
        genres = [
            {"name": TMDB_TV_GENRES[genre_id]}
            for genre_id in show["genre_ids"]
            if genre_id in TMDB_TV_GENRES
        ]

    genre_names = [genre["name"] if isinstance(genre, dict) else genre for genre in genres]
    title = show.get("name") or show.get("original_name") or "Sin título"
    original_title = show.get("original_name") or title

    return {
        "id": synthetic_tmdb_show_id(show["id"]),
        "name": title,
        "original_name": original_title,
        "premiered": show.get("first_air_date"),
        "ended": show.get("last_air_date") if show.get("status") in {"Ended", "Canceled"} else None,
        "status": translated_status(show.get("status")),
        "language": (show.get("original_language") or "").upper() or "Sin idioma",
        "genres": translated_genres(genre_names, show=show, network=first_network),
        "summary": show.get("overview") or "",
        "image_url": f"{TMDB_IMAGE_BASE_URL}{poster_path}" if poster_path else None,
        "official_url": show.get("homepage") or f"https://www.themoviedb.org/tv/{show['id']}",
        "network": first_network.get("name") if first_network else None,
        "rating": round(show.get("vote_average") or 0, 1),
        "tmdb_id": show["id"],
        "source": "tmdb",
        "source_label": "TMDb",
        "_normalized": True,
    }


def normalize_tmdb_episode(episode, show):
    still_path = episode.get("still_path")
    airdate = episode.get("air_date")

    return {
        "id": synthetic_tmdb_episode_id(episode["id"]),
        "show_id": show["id"],
        "show_name": show["name"],
        "show_image_url": show.get("image_url"),
        "name": episode.get("name") or "Sin título",
        "season": episode.get("season_number"),
        "number": episode.get("episode_number"),
        "airdate": airdate,
        "airtime": None,
        "runtime": episode.get("runtime"),
        "summary": episode.get("overview") or "",
        "image_url": f"{TMDB_STILL_BASE_URL}{still_path}" if still_path else None,
    }
