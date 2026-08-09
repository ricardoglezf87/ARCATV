import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from arcatv import db as store
from arcatv import manga_downloads
from arcatv import build_recommendation_sections, cached_json, create_app, get_comick_manga_chapters
from arcatv.anilist import normalize_anilist_manga, synthetic_anilist_manga_id
from arcatv.comick import ComicKError, normalize_comick_manga, synthetic_comick_manga_id
from arcatv.mangadex import synthetic_mangadex_manga_id
from arcatv.manga_oni import parse_manga_chapters
from arcatv.recommendations import add_recommendation_reasons, rank_recommendations
from arcatv.tmdb import (
    TMDbClient,
    normalize_tmdb_episode,
    normalize_tmdb_movie,
    normalize_tmdb_show,
    synthetic_tmdb_episode_id,
    synthetic_tmdb_movie_id,
    synthetic_tmdb_show_id,
)
from arcatv.utils import build_show_state, episode_code


TMDB_SHOW = {
    "id": 42,
    "name": "La Serie Perdida",
    "original_name": "Lost Show",
    "first_air_date": "2025-03-01",
    "last_air_date": None,
    "status": "Returning Series",
    "original_language": "es",
    "genres": [{"name": "Drama"}],
    "genre_ids": [18],
    "overview": "Una serie disponible directamente en español.",
    "poster_path": None,
    "homepage": "",
    "networks": [{"name": "Netflix"}],
    "vote_average": 7.9,
    "seasons": [{"season_number": 1}],
}

TMDB_EPISODES = [
    {
        "id": 4201,
        "name": "El hallazgo",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2025-03-01",
        "runtime": 45,
        "overview": "La historia empieza.",
        "still_path": None,
    },
    {
        "id": 4202,
        "name": "El futuro",
        "season_number": 1,
        "episode_number": 2,
        "air_date": "2099-02-01",
        "runtime": 45,
        "overview": "Un episodio por venir.",
        "still_path": None,
    },
]


def candidate(show_id, name, rating, year="2024", genres=None):
    return {
        **TMDB_SHOW,
        "id": show_id,
        "name": name,
        "original_name": name,
        "first_air_date": f"{year}-01-15",
        "genres": [],
        "genre_ids": genres or [18],
        "vote_average": rating,
        "seasons": [],
    }


TMDB_RECOMMENDATION = candidate(43, "Drama de Moda", 8.6, "2026")
TMDB_ACTOR_SHOW = candidate(44, "Drama del Actor", 8.9)
TMDB_FINALIZED_SHOW = {
    **candidate(45, "Serie Finalizada", 7.4, "2021"),
    "status": "Ended",
    "last_air_date": "2021-01-01",
    "seasons": [{"season_number": 1}],
}
TMDB_LOW_RATED = candidate(47, "Drama Correcto", 6.1)
TMDB_OLD_HIGH_RATED = candidate(48, "Drama Antiguo", 9.9, "2005")
TMDB_TELENOVELA = candidate(49, "Telenovela Demo", 7.3, genres=[10766, 18])
TMDB_MOVIE = {
    "id": 77,
    "title": "Pelicula Base",
    "original_title": "Base Movie",
    "release_date": "2024-05-01",
    "status": "Released",
    "original_language": "es",
    "genres": [{"name": "Drama"}],
    "genre_ids": [18],
    "overview": "Una pelicula guardada como vista.",
    "poster_path": None,
    "homepage": "",
    "production_companies": [{"name": "Estudio Demo"}],
    "vote_average": 8.2,
    "runtime": 122,
}
TMDB_MOVIE_RECOMMENDATION = {
    **TMDB_MOVIE,
    "id": 78,
    "title": "Pelicula Recomendada",
    "original_title": "Recommended Movie",
    "release_date": "2026-03-10",
    "vote_average": 8.8,
}
TMDB_ACTOR_MOVIE = {
    **TMDB_MOVIE,
    "id": 79,
    "title": "Pelicula del Actor",
    "original_title": "Actor Movie",
    "release_date": "2025-02-20",
    "vote_average": 8.7,
}
TMDB_FINAL_EPISODE = {
    **TMDB_EPISODES[0],
    "id": 4501,
    "name": "Final pendiente",
    "air_date": "2021-01-01",
}
TMDB_CAST = [
    {
        "id": 501,
        "name": "Actor Demo",
        "character": "Detective",
        "profile_path": None,
        "order": 0,
        "popularity": 8,
    }
]
TMDB_PERSON = {
    "id": 501,
    "name": "Actor Demo",
    "biography": "Biografía disponible en español.",
    "birthday": "1980-01-01",
    "deathday": None,
    "place_of_birth": "Madrid, España",
    "known_for_department": "Acting",
    "profile_path": None,
    "popularity": 8,
}


def anilist_manga(manga_id, title, score=82, year=2024, genres=None):
    return {
        "id": manga_id,
        "title": {
            "romaji": title,
            "english": title,
            "native": title,
        },
        "description": f"Sinopsis de {title}.",
        "countryOfOrigin": "JP",
        "format": "MANGA",
        "status": "FINISHED",
        "chapters": 24,
        "volumes": 4,
        "startDate": {"year": year, "month": 1, "day": 1},
        "genres": genres or ["Drama", "Mystery"],
        "tags": [
            {
                "name": "Seinen",
                "rank": 90,
                "isGeneralSpoiler": False,
                "isMediaSpoiler": False,
            }
        ],
        "averageScore": score,
        "isAdult": False,
        "coverImage": {"large": None},
        "siteUrl": f"https://anilist.co/manga/{manga_id}",
    }


ANILIST_MANGA = anilist_manga(30002, "Manga Base", 84, 2024)
ANILIST_MANGA_RECOMMENDATION = anilist_manga(30003, "Manga Recomendado", 88, 2026)
ANILIST_AUTHOR_MANGA = anilist_manga(30004, "Manga del Autor", 87, 2025)
ANILIST_DUPLICATE_MANGA = anilist_manga(87178, "Manga Base", 90, 1997)
ANILIST_STAFF = {
    "id": 900,
    "name": {"full": "Autor Demo", "native": "Autor Demo"},
    "image": {"large": None},
    "siteUrl": "https://anilist.co/staff/900",
    "description": "Biografia de autor.",
}
ANILIST_STAFF_EDGE = {
    "role": "Story & Art",
    "node": ANILIST_STAFF,
}

MANGADEX_MANGA_ID = "a1c7c817-4e59-43b7-9365-09675a149a6f"
MANGADEX_AUTHOR_ID = "b6045e2c-28f4-4ce0-b4dd-b14070f2f5ae"
MANGADEX_COVER_ID = "3b441fd3-023d-4f96-9e58-e11312421f45"
MANGADEX_MANGA = {
    "id": MANGADEX_MANGA_ID,
    "type": "manga",
    "attributes": {
        "title": {"en": "Manga Base", "ja-ro": "Manga Base"},
        "description": {"es": "Sinopsis de Manga Base."},
        "originalLanguage": "ja",
        "lastVolume": "100",
        "lastChapter": "1037",
        "publicationDemographic": "shounen",
        "status": "ongoing",
        "year": 1997,
        "tags": [
            {
                "id": "tag-drama",
                "type": "tag",
                "attributes": {"name": {"en": "Drama"}, "group": "genre"},
            },
            {
                "id": "tag-mystery",
                "type": "tag",
                "attributes": {"name": {"en": "Mystery"}, "group": "genre"},
            },
        ],
    },
    "relationships": [
        {
            "id": MANGADEX_AUTHOR_ID,
            "type": "author",
            "attributes": {"name": "Autor Demo"},
        },
        {
            "id": MANGADEX_COVER_ID,
            "type": "cover_art",
            "attributes": {"fileName": "cover.jpg"},
        },
    ],
}
MANGADEX_CHAPTERS = [
    {
        "id": "chapter-1035",
        "type": "chapter",
        "attributes": {
            "volume": "100",
            "chapter": "1035",
            "title": "Capitulo base",
            "translatedLanguage": "es",
            "externalUrl": "https://mangaplus.shueisha.co.jp/viewer/1035",
            "publishAt": "2024-01-01T00:00:00+00:00",
            "pages": 0,
        },
        "relationships": [
            {"id": "group-1", "type": "scanlation_group", "attributes": {"name": "MangaPlus"}}
        ],
    },
    {
        "id": "chapter-1037",
        "type": "chapter",
        "attributes": {
            "volume": "100",
            "chapter": "1037",
            "title": "Capitulo nuevo",
            "translatedLanguage": "es",
            "externalUrl": "https://mangaplus.shueisha.co.jp/viewer/1037",
            "publishAt": "2024-01-15T00:00:00+00:00",
            "pages": 0,
        },
        "relationships": [
            {"id": "group-1", "type": "scanlation_group", "attributes": {"name": "MangaPlus"}}
        ],
    },
]

COMICK_MANGA_ID = "CzcseUMi"
COMICK_MANGA = {
    "comic": {
        "id": 112,
        "hid": COMICK_MANGA_ID,
        "slug": "02-one-piece",
        "title": "Manga Base",
        "country": "jp",
        "status": 1,
        "last_chapter": 1037,
        "final_volume": 100,
        "desc": "Sinopsis de Manga Base.",
        "year": 1997,
        "content_rating": "safe",
        "demographic": 1,
        "md_titles": [
            {"title": "Manga Base", "lang": "en", "is_default": True},
            {"title": "Manga Base", "lang": "ja-ro", "is_default": False},
        ],
        "md_covers": [{"b2key": "cover.jpg", "w": 600, "h": 900}],
        "md_comic_md_genres": [
            {"md_genres": {"name": "Drama", "group": "Genre"}},
            {"md_genres": {"name": "Mystery", "group": "Genre"}},
        ],
    }
}
COMICK_SEARCH_MANGA = COMICK_MANGA["comic"]
COMICK_CHAPTERS = [
    {
        "id": 1,
        "hid": "chapter-1035",
        "chap": "1035",
        "title": "Capitulo base",
        "vol": "100",
        "lang": "es",
        "publish_at": "2024-01-01T00:00:00+00:00",
        "group_name": ["MangaPlus"],
    },
    {
        "id": 2,
        "hid": "chapter-1037",
        "chap": "1037",
        "title": "Capitulo nuevo",
        "vol": "100",
        "lang": "es",
        "publish_at": "2024-01-15T00:00:00+00:00",
        "group_name": ["MangaPlus"],
    },
]


class FakeTMDbClient:
    enabled = True

    shows = {
        42: TMDB_SHOW,
        43: TMDB_RECOMMENDATION,
        44: TMDB_ACTOR_SHOW,
        45: TMDB_FINALIZED_SHOW,
        47: TMDB_LOW_RATED,
        48: TMDB_OLD_HIGH_RATED,
        49: TMDB_TELENOVELA,
    }
    movies = {
        77: TMDB_MOVIE,
        78: TMDB_MOVIE_RECOMMENDATION,
        79: TMDB_ACTOR_MOVIE,
    }

    def search_tv(self, query):
        assert query
        if "finalizada" in query.casefold():
            return [TMDB_FINALIZED_SHOW]
        return [TMDB_SHOW]

    def search_movie(self, query):
        assert query
        return [TMDB_MOVIE]

    def get_tv(self, series_id):
        return self.shows.get(series_id, {})

    def get_movie(self, movie_id):
        return self.movies.get(movie_id, {})

    def get_season(self, series_id, season_number):
        assert season_number == 1
        if series_id == 42:
            return {"episodes": TMDB_EPISODES}
        if series_id == 45:
            return {"episodes": [TMDB_FINAL_EPISODE]}
        return {"episodes": []}

    def get_trending_tv(self, time_window="week"):
        return [TMDB_TELENOVELA]

    def get_trending_movies(self, time_window="week"):
        return [TMDB_MOVIE_RECOMMENDATION]

    def get_recommendations(self, series_id):
        if series_id == 42:
            return [TMDB_RECOMMENDATION, TMDB_LOW_RATED, TMDB_OLD_HIGH_RATED]
        return []

    def get_movie_recommendations(self, movie_id):
        if movie_id == 77:
            return [TMDB_MOVIE_RECOMMENDATION]
        return []

    def get_similar(self, series_id):
        return []

    def get_movie_similar(self, movie_id):
        return []

    def get_tv_credits(self, series_id):
        return {"cast": TMDB_CAST if series_id == 42 else []}

    def get_movie_credits(self, movie_id):
        return {"cast": TMDB_CAST if movie_id == 77 else []}

    def search_people(self, query):
        assert query
        return [{**TMDB_PERSON, "known_for": []}]

    def get_person(self, person_id):
        return TMDB_PERSON if person_id == 501 else {}

    def get_person_tv_credits(self, person_id):
        assert person_id == 501
        return {
            "cast": [
                {**TMDB_ACTOR_SHOW, "character": "Inspectora"},
                {**TMDB_SHOW, "character": "Detective"},
            ]
        }

    def get_person_movie_credits(self, person_id):
        assert person_id == 501
        return {
            "cast": [
                {**TMDB_ACTOR_MOVIE, "character": "Exploradora"},
                {**TMDB_MOVIE, "character": "Mentora"},
            ]
        }


class FakeAniListClient:
    enabled = True

    mangas = {
        30002: ANILIST_MANGA,
        30003: ANILIST_MANGA_RECOMMENDATION,
        30004: ANILIST_AUTHOR_MANGA,
        87178: ANILIST_DUPLICATE_MANGA,
    }

    def search_manga(self, query):
        assert query
        return [ANILIST_MANGA]

    def get_manga(self, manga_id):
        manga = self.mangas.get(manga_id, {})
        if not manga:
            return {}
        return {
            **manga,
            "staff": {"edges": [ANILIST_STAFF_EDGE] if manga_id == 30002 else []},
            "recommendations": {
                "nodes": [
                    {"rating": 100, "mediaRecommendation": ANILIST_MANGA_RECOMMENDATION}
                ]
                if manga_id == 30002
                else []
            },
        }

    def get_manga_recommendations(self, manga_id):
        if manga_id == 30002:
            return [ANILIST_MANGA_RECOMMENDATION]
        return []

    def get_trending_manga(self):
        return [ANILIST_MANGA_RECOMMENDATION]

    def search_staff(self, query):
        assert query
        return [ANILIST_STAFF]

    def get_staff(self, staff_id):
        if staff_id != 900:
            return {}
        return {
            **ANILIST_STAFF,
            "staffMedia": {
                "nodes": [ANILIST_AUTHOR_MANGA, ANILIST_MANGA, ANILIST_DUPLICATE_MANGA],
                "edges": [{"staffRole": "Story"}, {"staffRole": "Story & Art"}, {"staffRole": "Story & Art"}],
            },
        }

    def get_staff_manga(self, staff_id):
        assert staff_id == 900
        return [
            {**ANILIST_AUTHOR_MANGA, "staff_role": "Story"},
            {**ANILIST_MANGA, "staff_role": "Story & Art"},
            {**ANILIST_DUPLICATE_MANGA, "staff_role": "Story & Art"},
        ]


class FakeMangaDexClient:
    enabled = True

    def search_manga(self, query):
        assert query
        return [MANGADEX_MANGA]

    def get_manga(self, manga_id):
        return MANGADEX_MANGA if manga_id == MANGADEX_MANGA_ID else {}

    def get_author(self, author_id):
        if author_id != MANGADEX_AUTHOR_ID:
            return {}
        return {
            "id": MANGADEX_AUTHOR_ID,
            "type": "author",
            "attributes": {"name": "Autor Demo", "biography": {"en": "Bio de MangaDex."}},
        }

    def get_author_manga(self, author_id, relationship="author"):
        assert author_id == MANGADEX_AUTHOR_ID
        return [MANGADEX_MANGA] if relationship == "author" else []

    def get_manga_feed(self, manga_id, languages=None, offset=0, limit=100):
        assert manga_id == MANGADEX_MANGA_ID
        data = MANGADEX_CHAPTERS[offset: offset + limit]
        return {
            "result": "ok",
            "response": "collection",
            "data": data,
            "limit": limit,
            "offset": offset,
            "total": len(MANGADEX_CHAPTERS),
        }


class FakeComicKClient:
    enabled = True

    def search_manga(self, query, limit=12):
        assert query
        return [COMICK_SEARCH_MANGA]

    def get_manga(self, comick_id):
        return COMICK_MANGA if comick_id == COMICK_MANGA_ID else {}

    def get_manga_chapters(self, comick_id, language="es", page=1, limit=1000):
        assert comick_id == COMICK_MANGA_ID
        if language != "es":
            return {
                "chapters": [],
                "limit": limit,
                "page": page,
                "total": 0,
            }
        start = (page - 1) * limit
        data = COMICK_CHAPTERS[start: start + limit]
        return {
            "chapters": data,
            "limit": limit,
            "page": page,
            "total": len(COMICK_CHAPTERS),
        }


class FakeMangaOniClient:
    enabled = True

    def __init__(self, chapters=None):
        self.chapters = chapters or []

    def get_manga_chapters(self, manga_url):
        assert manga_url.startswith("https://manga-oni.com/manga/")
        return self.chapters


class FailingComicKChaptersClient(FakeComicKClient):
    def get_manga_chapters(self, comick_id, language="es", page=1, limit=1000):
        raise ComicKError("ComicK rechazo la peticion desde este servidor (403).")


class RecordingComicKClient(FakeComicKClient):
    def __init__(self):
        self.chapter_calls = []

    def get_manga_chapters(self, comick_id, language="es", page=1, limit=1000):
        self.chapter_calls.append(
            {
                "comick_id": comick_id,
                "language": language,
                "page": page,
                "limit": limit,
            }
        )
        return super().get_manga_chapters(comick_id, language=language, page=page, limit=limit)


class DisabledTMDbClient:
    enabled = False


class AccentSensitiveTMDbClient(FakeTMDbClient):
    def search_tv(self, query):
        if query.casefold() == "los bricen":
            return [
                {
                    **TMDB_SHOW,
                    "id": 96532,
                    "name": "Los Briceño",
                    "original_name": "Los Briceño",
                    "first_air_date": "2019-11-27",
                    "genre_ids": [35],
                }
            ]
        return []


class CapturingSession:
    def __init__(self):
        self.verify = None

    def get(self, _url, **kwargs):
        self.verify = kwargs.get("verify")
        return FakeResponse()


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": []}


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-test.sqlite"),
            "MANGA_DOWNLOAD_ROOT": str(tmp_path / "manga-downloads"),
            "TMDB_CLIENT": FakeTMDbClient(),
            "ANILIST_CLIENT": FakeAniListClient(),
            "COMICK_CLIENT": FakeComicKClient(),
            "MANGADEX_CLIENT": FakeMangaDexClient(),
            "MANGA_ONI_CLIENT": FakeMangaOniClient(),
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def add_main_show(client):
    show_id = synthetic_tmdb_show_id(42)
    response = client.post(f"/series/{show_id}/add", follow_redirects=True)
    assert response.status_code == 200
    return show_id


def mark_first_episode(client, show_id):
    episode_id = synthetic_tmdb_episode_id(4201)
    return client.post(
        f"/episodios/{episode_id}/visto",
        data={
            "show_id": str(show_id),
            "season": "1",
            "number": "1",
            "name": "El hallazgo",
            "watched": "1",
        },
        follow_redirects=True,
    )


def test_search_add_and_cast_are_available(client):
    show_id = synthetic_tmdb_show_id(42)
    html = client.get("/buscar?q=perdida").get_data(as_text=True)

    assert "La Serie Perdida" in html
    assert "Catálogo TMDb en español" in html
    assert f'action="/series/{show_id}/add"' in html

    html = client.post(f"/series/{show_id}/add", follow_redirects=True).get_data(as_text=True)

    assert "El hallazgo" in html
    assert "Actor Demo" in html
    assert 'href="/actores/501"' in html


def test_progress_upcoming_and_watched_visibility(client):
    show_id = add_main_show(client)

    assert "El futuro" in client.get("/proximos").get_data(as_text=True)
    html = mark_first_episode(client, show_id).get_data(as_text=True)
    assert "100%" in html
    assert "El hallazgo" not in client.get(f"/series/{show_id}").get_data(as_text=True)
    assert "El hallazgo" in client.get(f"/series/{show_id}?vistos=1").get_data(as_text=True)


def test_year_based_numbering_uses_absolute_episode_number():
    show = normalize_tmdb_show(TMDB_SHOW)
    episodes = [
        normalize_tmdb_episode({**TMDB_EPISODES[0], "id": 200, "season_number": 2025, "episode_number": 36}, show),
        normalize_tmdb_episode({**TMDB_EPISODES[1], "id": 201, "season_number": 2026, "episode_number": 17}, show),
    ]
    state = build_show_state(show, episodes, watched_ids=set())

    assert episode_code(state["episodes"][1]) == "E2"


def test_recommendations_use_rating_filters_and_multiple_sources(client):
    show_id = add_main_show(client)
    mark_first_episode(client, show_id)

    html = client.get("/recomendaciones").get_data(as_text=True)

    assert "Mejor puntuación" in html
    assert "Drama del Actor" in html
    assert "Drama de Moda" in html
    assert "Drama Antiguo" not in html
    assert "Porque viste" in html
    assert f'name="origen"' in html
    assert "Seleccionar todas" in html

    filtered = client.get("/recomendaciones?genero=Telenovela").get_data(as_text=True)
    assert "Recomendación por Telenovela" in filtered
    assert "Telenovela Demo" in filtered

    old = client.get("/recomendaciones?desde=2000&orden=puntuacion").get_data(as_text=True)
    assert "Drama Antiguo" in old


def test_recommendations_allow_deselecting_every_source(client):
    show_id = add_main_show(client)
    mark_first_episode(client, show_id)

    html = client.get("/recomendaciones?fuentes=seleccionadas").get_data(as_text=True)

    assert "0 seleccionadas" in html
    assert "Sin recomendaciones todavía" in html
    assert "Drama de Moda" not in html


def test_recommendation_can_be_rejected_included_and_restored(client):
    show_id = add_main_show(client)
    mark_first_episode(client, show_id)
    recommendation_id = synthetic_tmdb_show_id(43)

    response = client.post(
        f"/recomendaciones/{recommendation_id}/rechazar",
        data={"name": "Drama de Moda", "next": "/recomendaciones"},
        follow_redirects=True,
    )
    assert "<h2>Drama de Moda</h2>" not in response.get_data(as_text=True)

    rejected_html = client.get("/recomendaciones?rechazadas=1").get_data(as_text=True)
    assert "Drama de Moda" in rejected_html
    assert "Rechazada" in rejected_html
    assert "Restaurar" in rejected_html

    client.post(
        f"/recomendaciones/{recommendation_id}/restaurar",
        data={"next": "/recomendaciones"},
    )
    assert "Drama de Moda" in client.get("/recomendaciones").get_data(as_text=True)


def test_actor_page_and_manual_actor_recommendations(client):
    add_main_show(client)

    actor_html = client.get("/actores/501").get_data(as_text=True)
    assert "Biografía disponible en español" in actor_html
    assert "Drama del Actor" in actor_html
    assert f'action="/series/{synthetic_tmdb_show_id(44)}/add"' in actor_html

    recommendations_html = client.get("/recomendaciones?actor=Actor+Demo").get_data(as_text=True)
    assert "Con Actor Demo" in recommendations_html
    assert "Drama del Actor" in recommendations_html


def test_show_from_actor_can_be_added(client):
    show_id = synthetic_tmdb_show_id(44)
    response = client.post(f"/series/{show_id}/add", follow_redirects=True)

    assert response.status_code == 200
    assert "Drama del Actor" in response.get_data(as_text=True)


def test_movie_search_add_watched_cast_and_recommendations(client):
    movie_id = synthetic_tmdb_movie_id(77)
    html = client.get("/buscar/peliculas?q=base").get_data(as_text=True)

    assert "Pelicula Base" in html
    assert f'action="/peliculas/{movie_id}/add"' in html

    html = client.post(
        f"/peliculas/{movie_id}/add",
        data={"watched": "1"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Pelicula Base" in html
    assert "Vista" in html
    assert "Actor Demo" in html

    default_movies_html = client.get("/peliculas").get_data(as_text=True)
    all_movies_html = client.get("/peliculas?estado=todas").get_data(as_text=True)
    assert "Pelicula Base" not in default_movies_html
    assert "Ver todas" in default_movies_html
    assert "Pelicula Base" in all_movies_html
    assert "Mostrar solo pendientes" in all_movies_html

    recommendations_html = client.get("/recomendaciones/peliculas").get_data(as_text=True)
    assert "Pelicula Recomendada" in recommendations_html
    assert "Pelicula del Actor" in recommendations_html
    assert "Porque viste" in recommendations_html
    assert f'name="origen"' in recommendations_html


def test_movie_actor_page_and_manual_actor_recommendations(client):
    actor_html = client.get("/actores/501").get_data(as_text=True)

    assert "Pelicula del Actor" in actor_html
    assert f'action="/peliculas/{synthetic_tmdb_movie_id(79)}/add"' in actor_html

    recommendations_html = client.get("/recomendaciones/peliculas?actor=Actor+Demo").get_data(as_text=True)
    assert "Con Actor Demo" in recommendations_html
    assert "Pelicula del Actor" in recommendations_html


def test_manga_search_add_chapter_progress_authors_and_recommendations(client):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    html = client.get("/buscar/mangas?q=base").get_data(as_text=True)

    assert "Manga Base" in html
    assert 'action="/mangas/add"' in html
    assert f'name="source_id" value="{COMICK_MANGA_ID}"' in html
    assert 'name="source" value="comick"' in html
    assert "Anadir leido" not in html

    html = client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Manga Base" in html
    assert "Voy por el capitulo" not in html
    assert "Cap. 1035" in html
    assert "Cap. 1036" in html
    assert "Capitulo base" in html
    assert "Sin titulo disponible" in html
    assert "Guardar progreso" not in html
    assert "/mangas/portadas/comick/cover.jpg" in html
    assert "Autor Demo" in html
    assert 'href="/autores/900"' in html

    html = client.post(
        f"/mangas/{manga_id}/leido",
        data={"chapter_read": "1035"},
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Cap. 1036" in html
    assert "Sin titulo disponible" in html
    assert "99%" in html
    assert "Abrir en ComicK" not in html
    assert "Capitulo base" not in client.get(f"/mangas/{manga_id}").get_data(as_text=True)
    read_chapters_html = client.get(f"/mangas/{manga_id}?leidos=1").get_data(as_text=True)
    assert "Capitulo base" in read_chapters_html
    assert "Cap. N/D" not in read_chapters_html
    assert "Visto" in read_chapters_html
    assert f'action="/mangas/{manga_id}/capitulos/1035/leido"' in read_chapters_html
    assert 'name="read" value="0"' in read_chapters_html

    client.post(
        f"/mangas/{manga_id}/leido",
        data={"chapter_read": "1034"},
        follow_redirects=True,
    )
    assert "Capitulo base" in client.get(f"/mangas/{manga_id}").get_data(as_text=True)

    client.post(
        f"/mangas/{manga_id}/leido",
        data={"chapter_read": "1035"},
        follow_redirects=True,
    )

    default_mangas_html = client.get("/mangas").get_data(as_text=True)
    assert "Manga Base" in default_mangas_html
    assert "1035 de 1037 capitulos leidos" in default_mangas_html
    assert "Pendiente:" in default_mangas_html
    assert "Cap. 1036" in default_mangas_html
    assert 'action="/mangas/actualizar"' in default_mangas_html
    assert "Sumar capitulo" not in default_mangas_html

    refreshed_html = client.post("/mangas/actualizar", follow_redirects=True).get_data(as_text=True)
    assert "Se actualizaron 1 mangas y sus capitulos." in refreshed_html
    assert "Manga Base" in refreshed_html

    client.post(
        f"/mangas/{manga_id}/leido",
        data={"chapter_read": "1037"},
        follow_redirects=True,
    )
    default_mangas_html = client.get("/mangas").get_data(as_text=True)
    all_mangas_html = client.get("/mangas?estado=todas").get_data(as_text=True)
    assert "Manga Base" not in default_mangas_html
    assert "Manga Base" in all_mangas_html
    assert "Mostrar solo pendientes" in all_mangas_html

    recommendations_html = client.get("/recomendaciones/mangas").get_data(as_text=True)
    assert "Manga Recomendado" in recommendations_html
    assert "Porque leiste" in recommendations_html
    assert f'name="origen"' in recommendations_html


def test_manga_detail_falls_back_to_mangadex_when_comick_chapters_fail(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "comick-error.sqlite"),
            "MANGA_DOWNLOAD_ROOT": str(tmp_path / "manga-downloads"),
            "TMDB_CLIENT": FakeTMDbClient(),
            "ANILIST_CLIENT": FakeAniListClient(),
            "COMICK_CLIENT": FailingComicKChaptersClient(),
            "MANGADEX_CLIENT": FakeMangaDexClient(),
        }
    )

    html = app.test_client().post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Manga Base" in html
    assert "Cap. 1035" in html
    assert "Capitulo base" in html
    assert "No se pudieron cargar los capitulos" not in html

    with app.app_context():
        manga = store.get_manga(synthetic_comick_manga_id(COMICK_MANGA_ID))
        assert manga["comick_id"] == COMICK_MANGA_ID
        assert manga["mangadex_id"] == MANGADEX_MANGA_ID


def test_comick_chapter_fetch_uses_configured_page_size(tmp_path):
    comick_client = RecordingComicKClient()
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "comick-page-size.sqlite"),
            "COMICK_CLIENT": comick_client,
            "COMICK_CHAPTER_FETCH_LIMIT": 2,
            "COMICK_CHAPTER_PAGE_SIZE": 1,
        }
    )

    with app.app_context():
        manga = normalize_comick_manga(COMICK_MANGA)
        store.upsert_manga(manga)
        get_comick_manga_chapters(manga)

    assert all(call["limit"] <= 1 for call in comick_client.chapter_calls)
    assert [
        call["page"]
        for call in comick_client.chapter_calls
        if call["language"] == "es"
    ] == [1, 2]


def test_cached_json_uses_expired_payload_when_provider_fails(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "expired-cache.sqlite"),
        }
    )

    def fail():
        raise ComicKError("ComicK caido")

    with app.app_context():
        store.cache_set("demo:expired", {"value": "old"}, -1)

        assert cached_json("demo:expired", 60, fail) == {"value": "old"}


def test_manga_chapter_download_reader_progress_and_cleanup(client, app, monkeypatch):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=True,
    )
    client.post(
        f"/mangas/{manga_id}/descarga-base",
        data={"base_url": "https://mangasnosekai.com/manga/una-pieza"},
        follow_redirects=True,
    )

    downloaded_urls = []

    def fake_download(url, chapter_dir, chrome_version=None):
        downloaded_urls.append(url)
        chapter_dir = Path(chapter_dir)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        pages_dir = chapter_dir / "paginas_completas"
        pages_dir.mkdir()
        (chapter_dir / "001.jpg").write_bytes(b"panel-1")
        (chapter_dir / "002.jpg").write_bytes(b"panel-2")
        (pages_dir / "page_01.jpg").write_bytes(b"page-1")
        (chapter_dir / "config.json").write_text('{"1": "page_01.jpg", "2": "page_01.jpg"}')
        return SimpleNamespace(panel_count=2, page_count=1, strategy="prueba")

    monkeypatch.setattr("arcatv.download_manga_chapter", fake_download)

    html = client.post(
        f"/mangas/{manga_id}/capitulos/descargar",
        data={
            "chapter": "1035",
        },
        follow_redirects=True,
    ).get_data(as_text=True)

    assert downloaded_urls == ["https://mangasnosekai.com/manga/una-pieza/capitulo-1035/"]
    assert "Descargado: 2 imagenes" in html
    assert f'href="/mangas/{manga_id}/capitulos/1035/leer"' in html

    reader_html = client.get(f"/mangas/{manga_id}/capitulos/1035/leer").get_data(as_text=True)
    assert "Cap. 1035" in reader_html
    assert f"/mangas/{manga_id}/capitulos/1035/imagenes/001.jpg" in reader_html

    client.post(f"/mangas/{manga_id}/capitulos/1035/progreso", json={"panel": 2})
    reader_html = client.get(f"/mangas/{manga_id}/capitulos/1035/leer").get_data(as_text=True)
    assert "Math.max(2 - 1, 0)" in reader_html

    response = client.post(f"/mangas/{manga_id}/capitulos/1035/terminar", json={"panel": 2})
    assert response.json["ok"] is True

    assert "Capitulo base" not in client.get(f"/mangas/{manga_id}").get_data(as_text=True)
    assert "Capitulo base" in client.get(f"/mangas/{manga_id}?leidos=1").get_data(as_text=True)

    download_folder = Path(app.config["MANGA_DOWNLOAD_ROOT"]) / str(manga_id) / "1035"
    assert download_folder.exists()
    client.post("/mangas/imagenes-vistas/borrar", data={"next": f"/mangas/{manga_id}"})
    assert not download_folder.exists()


def test_anilist_duplicate_manga_reuses_saved_comick_entry(client, app):
    existing_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    duplicate_id = synthetic_anilist_manga_id(87178)

    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=True,
    )

    author_html = client.get("/autores/900").get_data(as_text=True)
    assert f'href="/mangas/{existing_id}"' in author_html
    assert f'action="/mangas/{duplicate_id}/add"' not in author_html

    response = client.post(f"/mangas/{duplicate_id}/add", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/mangas/{existing_id}")

    with app.app_context():
        store.upsert_manga(normalize_anilist_manga(ANILIST_DUPLICATE_MANGA))

    response = client.get(f"/mangas/{duplicate_id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/mangas/{existing_id}")

    with app.app_context():
        assert store.get_manga(duplicate_id) is None


def test_manga_downloader_retries_with_browser_major_version():
    class FakeOptions:
        def set_capability(self, name, value):
            pass

    class FakeUc:
        calls = []
        ChromeOptions = FakeOptions

        @classmethod
        def Chrome(cls, **kwargs):
            version = kwargs.get("version_main")
            cls.calls.append(version)
            if version != 150:
                raise RuntimeError("session not created: Current browser version is 150.0.7871.187")
            return "driver"

    driver = manga_downloads._new_driver(FakeUc, chrome_version="151.0.0")

    assert driver == "driver"
    assert FakeUc.calls == [151, 150]
    assert manga_downloads._coerce_chrome_major_version("150.0.7871.187") == 150
    assert manga_downloads._chrome_version_from_error(
        RuntimeError("Current browser version is 150.0.7871.187")
    ) == 150


def test_manga_author_page_and_manual_author_recommendations(client):
    author_html = client.get("/autores/900").get_data(as_text=True)

    assert "Biografia de autor" in author_html
    assert "Manga del Autor" in author_html
    assert f'action="/mangas/{synthetic_anilist_manga_id(30004)}/add"' in author_html

    client.post(
        "/mangas/add",
        data={"source": "mangadex", "source_id": MANGADEX_MANGA_ID},
        follow_redirects=True,
    )
    mangadex_author_html = client.get(f"/autores/{MANGADEX_AUTHOR_ID}").get_data(as_text=True)
    assert "Bio de MangaDex." in mangadex_author_html
    assert "Manga Base" in mangadex_author_html
    assert "Autor" in mangadex_author_html
    assert "1997" in mangadex_author_html
    assert "Abrir en MangaDex" in mangadex_author_html
    assert f'href="/mangas/{synthetic_mangadex_manga_id(MANGADEX_MANGA_ID)}"' in mangadex_author_html

    recommendations_html = client.get("/recomendaciones/mangas?autor=Autor+Demo").get_data(as_text=True)
    assert "Con Autor Demo" in recommendations_html
    assert "Manga del Autor" in recommendations_html


def test_global_search_can_target_series_or_movies(client):
    series_html = client.get(
        "/buscar/global?q=perdida&tipo=series",
        follow_redirects=True,
    ).get_data(as_text=True)
    movie_html = client.get(
        "/buscar/global?q=base&tipo=peliculas",
        follow_redirects=True,
    ).get_data(as_text=True)
    manga_html = client.get(
        "/buscar/global?q=base&tipo=mangas",
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "La Serie Perdida" in series_html
    assert "Pelicula Base" in movie_html
    assert "Manga Base" in manga_html


def test_manga_oni_parses_spanish_titles_and_cascade_urls():
    chapters = parse_manga_chapters(
        """
        <a href="/lector/one-piece/698105/p1">
            Hace un ano Capitulo 1135 - Copas de amistad
        </a>
        <a href="/lector/one-piece/786360/">
            Hace 15 dias Capitulo 1189 — Rey del mundo
        </a>
        """,
        "https://manga-oni.com/manga/one-piece/",
    )

    assert [chapter["chapter"] for chapter in chapters] == ["1135", "1189"]
    assert chapters[0]["title"] == "Copas de amistad"
    assert chapters[0]["download_url"] == "https://manga-oni.com/lector/one-piece/698105/cascada/"


def test_manga_oni_direct_download_is_first_strategy(tmp_path, monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (40, 60), "white").save(image_buffer, "WEBP")
    image_bytes = image_buffer.getvalue()
    payload = base64.b64encode(b'https://oni.test/||["001.webp"]||').decode("ascii")

    class Response:
        status_code = 200

        def __init__(self, text="", content=b""):
            self.text = text
            self.content = content

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, **_kwargs):
            if url.endswith("/cascada/"):
                return Response(f"<script>var unicap = '{payload}';</script><div id=\"slider\"></div>")
            return Response(content=image_bytes)

    monkeypatch.setattr(manga_downloads.requests, "Session", Session)
    monkeypatch.setattr(
        manga_downloads,
        "_load_image_dependencies",
        lambda: (BeautifulSoup, Image, None, None),
    )

    result = manga_downloads.download_manga_chapter(
        "https://manga-oni.com/lector/one-piece/698105/p1",
        tmp_path / "chapter",
    )

    assert result.strategy == "Manga Oni"
    assert result.page_count == 1
    assert (tmp_path / "chapter" / "001.jpg").exists()


def test_download_until_skips_read_and_downloaded_chapters(client, app, monkeypatch):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    oni_chapters = parse_manga_chapters(
        """
        <a href="/lector/one-piece/1/">Capitulo 1035 — Ya leido</a>
        <a href="/lector/one-piece/2/">Capitulo 1036 — Ya descargado</a>
        <a href="/lector/one-piece/3/">Capitulo 1037 — Titulo preferente</a>
        """,
        "https://manga-oni.com/manga/one-piece/",
    )
    app.config["MANGA_ONI_CLIENT"] = FakeMangaOniClient(oni_chapters)
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=False,
    )
    client.post(f"/mangas/{manga_id}/leido", data={"chapter_read": "1035"})
    with app.app_context():
        store.upsert_manga_download(manga_id, "1036", "saved", "saved", 1, 1)

    downloaded = []

    def fake_download(url, chapter_dir, chrome_version=None):
        downloaded.append(url)
        chapter_dir = Path(chapter_dir)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "001.jpg").write_bytes(b"panel")
        return SimpleNamespace(panel_count=1, page_count=1, strategy="prueba")

    monkeypatch.setattr("arcatv.download_manga_chapter", fake_download)
    html = client.post(
        f"/mangas/{manga_id}/capitulos/descargar-hasta",
        data={"chapter": "1037"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert downloaded == ["https://manga-oni.com/lector/one-piece/3/cascada/"]
    assert "Se descargaron 1 capitulos pendientes." in html
    assert "Titulo preferente" in html


def test_manga_oni_only_chapter_updates_read_state(client, app):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    app.config["MANGA_ONI_CLIENT"] = FakeMangaOniClient(
        parse_manga_chapters(
            '<a href="/lector/manga-base/2/">Capitulo 1036 - Titulo de Manga Oni</a>',
            "https://manga-oni.com/manga/manga-base/",
        )
    )
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=False,
    )

    html = client.post(
        f"/mangas/{manga_id}/leido",
        data={"chapter_read": "1036"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Titulo de Manga Oni" not in html
    read_html = client.get(f"/mangas/{manga_id}?leidos=1").get_data(as_text=True)
    assert "Titulo de Manga Oni" in read_html
    assert f'action="/mangas/{manga_id}/capitulos/1036/leido"' in read_html
    assert 'name="read" value="0"' in read_html
    with app.app_context():
        assert store.get_manga_progress(manga_id)["chapter_read"] == "1036"


def test_mangadex_author_recommendations_keep_same_source(client):
    recommendations_html = client.get(
        f"/recomendaciones/mangas?autor=Autor+Demo&autor_id={MANGADEX_AUTHOR_ID}&autor_fuente=mangadex"
    ).get_data(as_text=True)

    assert "Con Autor Demo" in recommendations_html
    assert "Manga Base" in recommendations_html

    response = client.post(
        f"/mangas/{synthetic_mangadex_manga_id(MANGADEX_MANGA_ID)}/add",
        data={"source": "mangadex", "source_id": MANGADEX_MANGA_ID},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/mangas/{synthetic_mangadex_manga_id(MANGADEX_MANGA_ID)}"
    )


def test_single_manga_chapter_can_be_seen_and_pending_independently(client, app):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=False,
    )
    client.post(f"/mangas/{manga_id}/leido", data={"chapter_read": "1035"})

    html = client.post(
        f"/mangas/{manga_id}/capitulos/1037/leido",
        data={"read": "1"},
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Capitulo nuevo" not in html
    assert "Sin titulo disponible" in html
    with app.app_context():
        assert store.get_manga_progress(manga_id)["chapter_read"] == "1035"
        assert store.get_manga_chapter_read_override(manga_id, "1037") is True

    dashboard_html = client.get("/mangas").get_data(as_text=True)
    assert "1036 de 1037 capitulos leidos" in dashboard_html
    assert "Cap. 1036" in dashboard_html

    html = client.post(
        f"/mangas/{manga_id}/capitulos/1037/leido",
        data={"read": "0"},
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Capitulo nuevo" in html
    with app.app_context():
        assert store.get_manga_progress(manga_id)["chapter_read"] == "1035"
        assert store.get_manga_chapter_read_override(manga_id, "1037") is False

    dashboard_html = client.get("/mangas").get_data(as_text=True)
    assert "1035 de 1037 capitulos leidos" in dashboard_html
    assert "Cap. 1036" in dashboard_html

    client.post(
        f"/mangas/{manga_id}/capitulos/1034/leido",
        data={"read": "0"},
    )
    dashboard_html = client.get("/mangas").get_data(as_text=True)
    assert "1034 de 1037 capitulos leidos" in dashboard_html
    assert "Cap. 1034" in dashboard_html


def test_single_manga_download_images_can_be_deleted(client, app, monkeypatch):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=False,
    )
    client.post(
        f"/mangas/{manga_id}/descarga-base",
        data={"base_url": "https://mangasnosekai.com/manga/manga-base"},
    )

    def fake_download(_url, chapter_dir, chrome_version=None):
        chapter_dir = Path(chapter_dir)
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "001.jpg").write_bytes(b"panel")
        return SimpleNamespace(panel_count=1, page_count=1, strategy="prueba")

    monkeypatch.setattr("arcatv.download_manga_chapter", fake_download)
    client.post(
        f"/mangas/{manga_id}/capitulos/descargar",
        data={"chapter": "1035"},
    )
    folder = Path(app.config["MANGA_DOWNLOAD_ROOT"]) / str(manga_id) / "1035"
    assert folder.exists()

    html = client.post(
        f"/mangas/{manga_id}/capitulos/1035/imagenes/borrar",
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Imagenes descargadas del capitulo 1035 borradas." in html
    assert not folder.exists()
    with app.app_context():
        assert store.get_manga_download(manga_id, "1035") is None


def test_manga_oni_chapters_render_without_comick_or_mangadex(tmp_path):
    oni_chapters = parse_manga_chapters(
        '<a href="/lector/manga-base/2/">Capitulo 12 - Solo en Manga Oni</a>',
        "https://manga-oni.com/manga/manga-base/",
    )
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "oni-only.sqlite"),
            "MANGA_DOWNLOAD_ROOT": str(tmp_path / "downloads"),
            "ANILIST_CLIENT": FakeAniListClient(),
            "COMICK_ENABLED": False,
            "MANGADEX_ENABLED": False,
            "MANGA_ONI_CLIENT": FakeMangaOniClient(oni_chapters),
        }
    )
    manga = normalize_anilist_manga(ANILIST_MANGA)
    with app.app_context():
        store.upsert_manga(manga)

    html = app.test_client().get(f"/mangas/{manga['id']}").get_data(as_text=True)

    assert "Capitulos" in html
    assert "Cap. 12" in html
    assert "Solo en Manga Oni" in html


def test_search_requires_tmdb_configuration(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "disabled.sqlite"),
            "TMDB_CLIENT": DisabledTMDbClient(),
        }
    )

    html = app.test_client().get("/buscar?q=demo").get_data(as_text=True)
    assert "Configura TMDB_API_KEY o TMDB_BEARER_TOKEN" in html
    assert "TVmaze" not in html


def test_tmdb_search_tries_accent_folded_query(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "accent.sqlite"),
            "TMDB_CLIENT": AccentSensitiveTMDbClient(),
        }
    )

    html = app.test_client().get("/buscar?q=los+briceño").get_data(as_text=True)
    assert "Los Briceño" in html


def test_tmdb_search_accepts_direct_url(client):
    html = client.get("/buscar?q=https://www.themoviedb.org/tv/42-demo").get_data(as_text=True)
    assert "La Serie Perdida" in html


def test_tmdb_client_uses_configured_ssl_verification():
    session = CapturingSession()
    client = TMDbClient(api_key="demo", session=session, verify_ssl=False)

    client.search_tv("demo")
    assert session.verify is False


def test_recommendation_sections_do_not_repeat_items():
    source = {
        "id": 10,
        "name": "Serie base",
        "genres": ["Drama", "Misterio"],
        "watched_count": 1,
        "completed": False,
        "progress": 50,
    }
    candidates = [
        {
            "_normalized": True,
            "id": 101,
            "name": "Misterio A",
            "original_name": "Misterio A",
            "premiered": "2026-01-01",
            "genres": ["Drama", "Misterio"],
            "rating": 8.1,
            "source": "tmdb",
            "profile_sources": [{"id": 10, "name": "Serie base", "relation": "recommendation"}],
        },
        {
            "_normalized": True,
            "id": 102,
            "name": "Misterio B",
            "original_name": "Misterio B",
            "premiered": "2026-01-01",
            "genres": ["Drama", "Misterio"],
            "rating": 7.9,
            "source": "tmdb",
        },
    ]

    recommendations, _genres = rank_recommendations([source], candidates, saved_ids=set())
    recommendations = add_recommendation_reasons(recommendations, [source])
    sections = build_recommendation_sections(recommendations, [source])
    ids = [show["id"] for section in sections for show in section["items"]]

    assert len(ids) == len(set(ids))


def test_completed_shows_are_hidden_by_default(client):
    show_id = synthetic_tmdb_show_id(45)
    episode_id = synthetic_tmdb_episode_id(4501)
    client.post(f"/series/{show_id}/add", follow_redirects=True)

    assert "Serie Finalizada" in client.get("/").get_data(as_text=True)
    client.post(
        f"/episodios/{episode_id}/visto",
        data={
            "show_id": str(show_id),
            "season": "1",
            "number": "1",
            "name": "Final pendiente",
            "watched": "1",
        },
    )

    assert "Serie Finalizada" not in client.get("/").get_data(as_text=True)
    assert "Serie Finalizada" in client.get("/?estado=todas").get_data(as_text=True)


def test_manga_split_panels_preference_and_route(client, app):
    manga_id = synthetic_comick_manga_id(COMICK_MANGA_ID)
    client.post(
        "/mangas/add",
        data={"source": "comick", "source_id": COMICK_MANGA_ID},
        follow_redirects=True,
    )

    with app.app_context():
        assert store.get_manga_split_panels(manga_id) is False

    response = client.post(
        f"/mangas/{manga_id}/split-panels",
        data={"split_panels": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Dividir en viñetas activado" in response.get_data(as_text=True)
    with app.app_context():
        assert store.get_manga_split_panels(manga_id) is True

    response = client.post(
        f"/mangas/{manga_id}/split-panels",
        data={},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Dividir en viñetas desactivado" in response.get_data(as_text=True)
    with app.app_context():
        assert store.get_manga_split_panels(manga_id) is False


def test_manga_download_split_panels_behavior(tmp_path, monkeypatch):
    from PIL import Image

    def fake_get(session, url, headers=None, timeout=20):
        class FakeResponse:
            content = b""
            text = '<html><body><div id="slider"><img data-src="https://manga-oni.com/img/1.jpg"></div></body></html>'
            def raise_for_status(self):
                pass
        res = FakeResponse()
        if "img/1.jpg" in url:
            img = Image.new("RGB", (200, 400), color="red")
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            res.content = buf.getvalue()
        return res

    monkeypatch.setattr(manga_downloads, "get_with_ssl_fallback", fake_get)

    dir_disabled = tmp_path / "ch_disabled"
    result_disabled = manga_downloads.download_manga_chapter(
        "https://manga-oni.com/lector/test/1/cascada/",
        dir_disabled,
        split_panels=False,
    )
    assert result_disabled.page_count == 1
    assert (dir_disabled / "001.jpg").exists()
    assert not (dir_disabled / "paginas_completas").exists()
    assert not (dir_disabled / "config.json").exists()

    dir_enabled = tmp_path / "ch_enabled"
    result_enabled = manga_downloads.download_manga_chapter(
        "https://manga-oni.com/lector/test/1/cascada/",
        dir_enabled,
        split_panels=True,
    )
    assert result_enabled.page_count == 1
    assert (dir_enabled / "001.jpg").exists()
    assert (dir_enabled / "paginas_completas" / "page_01.jpg").exists()
    assert (dir_enabled / "config.json").exists()

