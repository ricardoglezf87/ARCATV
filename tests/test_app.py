import pytest

from arcatv import build_recommendation_sections, create_app
from arcatv.recommendations import add_recommendation_reasons, rank_recommendations
from arcatv.tmdb import synthetic_tmdb_show_id
from arcatv.utils import build_show_state, episode_code, normalize_show


SHOW = {
    "id": 1,
    "name": "Serie Demo",
    "premiered": "2020-01-01",
    "ended": None,
    "status": "Running",
    "language": "English",
    "genres": ["Drama"],
    "summary": "<p>Una serie de prueba.</p>",
    "image": {"medium": "https://example.com/poster.jpg"},
    "officialSite": "https://example.com",
    "network": {"name": "Demo Network"},
}

EPISODES = [
    {
        "id": 100,
        "name": "Piloto",
        "season": 1,
        "number": 1,
        "airdate": "2020-01-01",
        "airtime": "21:00",
        "runtime": 50,
        "summary": "<p>Empieza la historia.</p>",
        "image": None,
    },
    {
        "id": 101,
        "name": "Futuro",
        "season": 1,
        "number": 2,
        "airdate": "2099-02-01",
        "airtime": "21:00",
        "runtime": 50,
        "summary": "<p>Un episodio por venir.</p>",
        "image": None,
    },
]

FINALIZED_EPISODES = [
    {
        "id": 500,
        "name": "Final pendiente",
        "season": 1,
        "number": 1,
        "airdate": "2021-01-01",
        "airtime": "21:00",
        "runtime": 45,
        "summary": "<p>Un final emitido.</p>",
        "image": None,
    }
]

CANDIDATE_HIGH_RATED = {
    "id": 2,
    "name": "Drama Excelente",
    "premiered": "2022-01-01",
    "ended": None,
    "status": "Running",
    "language": "English",
    "genres": ["Drama"],
    "summary": "<p>Otra serie dramática.</p>",
    "image": None,
    "officialSite": "https://example.com/drama",
    "network": {"name": "Demo Network"},
    "rating": {"average": 8.8},
}

CANDIDATE_LOW_RATED = {
    **CANDIDATE_HIGH_RATED,
    "id": 3,
    "name": "Drama Correcto",
    "rating": {"average": 6.1},
}

CANDIDATE_OLD_HIGH_RATED = {
    **CANDIDATE_HIGH_RATED,
    "id": 4,
    "name": "Drama Antiguo",
    "premiered": "2005-01-01",
    "rating": {"average": 9.9},
}

FINALIZED_SHOW = {
    **SHOW,
    "id": 5,
    "name": "Serie Finalizada",
    "status": "Ended",
    "ended": "2021-01-01",
}

CANDIDATE_TELENOVELA = {
    **CANDIDATE_HIGH_RATED,
    "id": 6,
    "name": "Telenovela Demo",
    "premiered": "2024-02-01",
    "genres": ["Soap", "Drama"],
    "rating": {"average": 7.3},
}

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
    "overview": "Una serie que aparece en el repositorio alternativo.",
    "poster_path": None,
    "homepage": "",
    "networks": [{"name": "Netflix"}],
    "vote_average": 7.9,
    "seasons": [{"season_number": 1}],
}

TMDB_EPISODE = {
    "id": 4201,
    "name": "El hallazgo",
    "season_number": 1,
    "episode_number": 1,
    "air_date": "2025-03-01",
    "runtime": 45,
    "overview": "La historia empieza.",
    "still_path": None,
}

TMDB_RECOMMENDATION = {
    **TMDB_SHOW,
    "id": 43,
    "name": "Drama de Moda",
    "original_name": "Trending Drama",
    "first_air_date": "2026-01-15",
    "vote_average": 8.6,
}


class FakeTVMazeClient:
    def search_shows(self, query):
        assert query
        return [{"score": 1, "show": SHOW}]

    def get_show(self, show_id):
        shows = {
            1: SHOW,
            2: CANDIDATE_HIGH_RATED,
            3: CANDIDATE_LOW_RATED,
            4: CANDIDATE_OLD_HIGH_RATED,
            5: FINALIZED_SHOW,
            6: CANDIDATE_TELENOVELA,
        }
        return shows[show_id]

    def get_episodes(self, show_id):
        if show_id == 1:
            return EPISODES
        if show_id == 5:
            return FINALIZED_EPISODES
        assert show_id in {2, 3, 4, 5, 6}
        return []

    def get_akas(self, show_id):
        assert show_id in {1, 2, 3, 4, 5, 6}
        return []

    def get_shows_page(self, page):
        if page == 0:
            return [
                SHOW,
                CANDIDATE_LOW_RATED,
                CANDIDATE_HIGH_RATED,
                CANDIDATE_OLD_HIGH_RATED,
                CANDIDATE_TELENOVELA,
            ]
        return None


class EmptyTVMazeClient(FakeTVMazeClient):
    def search_shows(self, query):
        assert query
        return []


class FakeTMDbClient:
    enabled = True

    def search_tv(self, query):
        assert query
        return [TMDB_SHOW]

    def get_tv(self, series_id):
        assert series_id == 42
        return TMDB_SHOW

    def get_season(self, series_id, season_number):
        assert series_id == 42
        assert season_number == 1
        return {"episodes": [TMDB_EPISODE]}

    def get_trending_tv(self, time_window="week"):
        return []

    def get_recommendations(self, series_id):
        assert series_id == 42
        return [TMDB_RECOMMENDATION]

    def get_similar(self, series_id):
        return []


class EmptyTMDbClient(FakeTMDbClient):
    def search_tv(self, query):
        assert query
        return []

    def get_trending_tv(self, time_window="week"):
        return []

    def get_recommendations(self, series_id):
        return []


class AccentSensitiveTMDbClient(FakeTMDbClient):
    def search_tv(self, query):
        assert query
        if query.casefold() == "los briceno":
            return [
                {
                    **TMDB_SHOW,
                    "id": 96532,
                    "name": "Los Briceño",
                    "original_name": "Los Briceño",
                    "first_air_date": "2019-11-27",
                    "genres": [{"name": "Comedy"}],
                    "genre_ids": [35],
                    "overview": "El camino al amor.",
                    "vote_average": 7.9,
                }
            ]
        return []


class ExplodingTranslationClient:
    def translate_to_spanish(self, text):
        raise AssertionError(f"No se esperaba traducir: {text}")


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
            "TMDB_API_KEY": None,
            "TMDB_BEARER_TOKEN": None,
            "TRANSLATE_TO_SPANISH": False,
            "TVMAZE_RECOMMENDATION_PAGES": 2,
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def test_search_and_add_show(client):
    response = client.get("/buscar?q=demo")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Serie Demo" in html
    assert "Añadir" in html

    response = client.post("/series/1/add", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Serie Demo" in html
    assert "Piloto" in html


def test_mark_episode_updates_progress(client):
    client.post("/series/1/add", follow_redirects=True)

    response = client.post(
        "/episodios/100/visto",
        data={
            "show_id": "1",
            "season": "1",
            "number": "1",
            "name": "Piloto",
            "watched": "1",
        },
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "100%" in html
    assert "1 de 1 emitidos vistos" in client.get("/?estado=todas").get_data(as_text=True)


def test_upcoming_lists_future_episodes(client):
    client.post("/series/1/add", follow_redirects=True)

    response = client.get("/proximos")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Futuro" in html
    assert "Serie Demo" in html


def test_watched_episodes_are_hidden_by_default(client):
    client.post("/series/1/add", follow_redirects=True)
    client.post(
        "/episodios/100/visto",
        data={
            "show_id": "1",
            "season": "1",
            "number": "1",
            "name": "Piloto",
            "watched": "1",
        },
    )

    assert "Piloto" not in client.get("/series/1").get_data(as_text=True)
    assert "Piloto" in client.get("/series/1?vistos=1").get_data(as_text=True)


def test_spanish_aka_is_preferred_when_available():
    show = normalize_show(
        SHOW,
        akas=[{"name": "Serie Demo España", "country": {"code": "ES"}}],
    )

    assert show["name"] == "Serie Demo España"


def test_year_based_numbering_uses_absolute_episode_number():
    state = build_show_state(
        SHOW,
        [
            {**EPISODES[0], "id": 200, "season": 2025, "number": 36},
            {**EPISODES[1], "id": 201, "season": 2026, "number": 17},
        ],
        watched_ids=set(),
    )

    assert episode_code(state["episodes"][1]) == "E2"


def test_recommendations_are_sorted_by_rating_and_filterable(client):
    client.post("/series/1/add", follow_redirects=True)
    client.post(
        "/episodios/100/visto",
        data={
            "show_id": "1",
            "season": "1",
            "number": "1",
            "name": "Piloto",
            "watched": "1",
        },
    )

    html = client.get("/recomendaciones").get_data(as_text=True)

    assert html.index("Drama Excelente") < html.index("Drama Correcto")
    assert "8.8" in html
    assert 'action="/series/2/add"' in html
    assert "Añadir" in html
    assert "Porque viste Serie Demo" in html
    assert "Drama Antiguo" not in html

    filtered_html = client.get("/recomendaciones?genero=Comedia").get_data(as_text=True)
    assert "Drama Excelente" not in filtered_html

    telenovela_html = client.get("/recomendaciones?genero=Telenovela").get_data(as_text=True)
    assert "Telenovela Demo" in telenovela_html

    old_html = client.get("/recomendaciones?desde=2000&orden=puntuacion").get_data(as_text=True)
    assert old_html.index("Drama Antiguo") < old_html.index("Drama Excelente")


def test_can_add_recommendation_from_recommendations(client):
    client.post("/series/1/add", follow_redirects=True)
    client.post(
        "/episodios/100/visto",
        data={
            "show_id": "1",
            "season": "1",
            "number": "1",
            "name": "Piloto",
            "watched": "1",
        },
    )

    response = client.post("/series/2/add", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Drama Excelente" in html


def test_can_search_and_add_show_from_tmdb_fallback(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-tmdb-test.sqlite"),
            "TVMAZE_CLIENT": EmptyTVMazeClient(),
            "TMDB_CLIENT": FakeTMDbClient(),
            "TRANSLATE_TO_SPANISH": False,
        }
    )
    client = app.test_client()
    tmdb_show_id = synthetic_tmdb_show_id(42)

    html = client.get("/buscar?q=perdida").get_data(as_text=True)

    assert "La Serie Perdida" in html
    assert "TMDb" in html
    assert f'action="/series/{tmdb_show_id}/add"' in html

    response = client.post(f"/series/{tmdb_show_id}/add", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "La Serie Perdida" in html
    assert "El hallazgo" in html


def test_tmdb_is_primary_search_source_when_configured(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-tmdb-primary-search-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
            "TMDB_CLIENT": FakeTMDbClient(),
            "TRANSLATE_TO_SPANISH": False,
        }
    )
    client = app.test_client()

    html = client.get("/buscar?q=demo").get_data(as_text=True)

    assert "La Serie Perdida" in html
    assert "TMDb" in html
    assert "Serie Demo" not in html


def test_tvmaze_is_search_fallback_when_tmdb_has_no_results(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-tvmaze-search-fallback-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
            "TMDB_CLIENT": EmptyTMDbClient(),
            "TRANSLATE_TO_SPANISH": False,
        }
    )
    client = app.test_client()

    html = client.get("/buscar?q=demo").get_data(as_text=True)

    assert "Serie Demo" in html
    assert "TVmaze" in html


def test_tmdb_search_tries_accent_folded_query(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-accent-search-test.sqlite"),
            "TVMAZE_CLIENT": EmptyTVMazeClient(),
            "TMDB_CLIENT": AccentSensitiveTMDbClient(),
            "TRANSLATE_TO_SPANISH": False,
        }
    )
    client = app.test_client()

    html = client.get("/buscar?q=los briceño").get_data(as_text=True)

    assert "Los Briceño" in html
    assert "TMDb" in html


def test_tmdb_search_results_skip_external_translation(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-tmdb-no-translation-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
            "TMDB_CLIENT": FakeTMDbClient(),
            "TRANSLATION_CLIENT": ExplodingTranslationClient(),
            "TRANSLATE_TO_SPANISH": True,
        }
    )
    client = app.test_client()

    response = client.get("/buscar?q=demo")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Una serie que aparece en el repositorio alternativo." in html


def test_tmdb_recommendations_are_personalized_by_watched_profile(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-tmdb-recommendations-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
            "TMDB_CLIENT": FakeTMDbClient(),
            "TRANSLATE_TO_SPANISH": False,
            "TVMAZE_RECOMMENDATION_PAGES": 2,
        }
    )
    client = app.test_client()
    client.post("/series/1/add", follow_redirects=True)
    client.post(
        "/episodios/100/visto",
        data={
            "show_id": "1",
            "season": "1",
            "number": "1",
            "name": "Piloto",
            "watched": "1",
        },
    )

    html = client.get("/recomendaciones").get_data(as_text=True)

    assert "Drama de Moda" in html
    assert "Porque viste Serie Demo" in html
    assert "TMDb por Serie Demo" in html
    assert "8.6" in html


def test_recommendations_filter_generic_matches_and_sections_do_not_repeat():
    source_a = {
        "id": 10,
        "name": "Attack Demo",
        "genres": ["Anime", "Acción", "Aventura"],
        "watched_count": 1,
        "completed": False,
        "progress": 50,
    }
    source_b = {
        "id": 11,
        "name": "Arcane Demo",
        "genres": ["Animación", "Fantasía", "Aventura"],
        "watched_count": 1,
        "completed": False,
        "progress": 50,
    }
    good_candidate = {
        "_normalized": True,
        "id": 101,
        "name": "Anime Similar",
        "original_name": "Anime Similar",
        "premiered": "2026-01-01",
        "genres": ["Anime", "Acción"],
        "rating": 8.1,
        "source": "tmdb",
        "source_label": "TMDb por Attack Demo",
        "profile_sources": [{"id": 10, "name": "Attack Demo", "relation": "recommendation"}],
    }
    generic_candidate = {
        "_normalized": True,
        "id": 102,
        "name": "Road Action",
        "original_name": "Road Action",
        "premiered": "2026-01-01",
        "genres": ["Acción", "Aventura", "Comedia"],
        "rating": 7.8,
        "source": "tmdb",
        "source_label": "TMDb tendencias",
    }
    fantasy_candidate = {
        "_normalized": True,
        "id": 103,
        "name": "Fantasy Similar",
        "original_name": "Fantasy Similar",
        "premiered": "2026-01-01",
        "genres": ["Animación", "Fantasía"],
        "rating": 7.9,
        "source": "tmdb",
        "source_label": "TMDb por Arcane Demo",
        "profile_sources": [{"id": 11, "name": "Arcane Demo", "relation": "similar"}],
    }

    recommendations, _genres = rank_recommendations(
        [source_a, source_b],
        [good_candidate, generic_candidate, fantasy_candidate],
        saved_ids=set(),
    )
    recommendations = add_recommendation_reasons(recommendations, [source_a, source_b])
    sections = build_recommendation_sections(recommendations, [source_a, source_b])

    names = [recommendation["name"] for recommendation in recommendations]
    assert "Anime Similar" in names
    assert "Fantasy Similar" in names
    assert "Road Action" not in names

    section_ids = [
        show["id"]
        for section in sections
        for show in section["items"]
    ]
    assert len(section_ids) == len(set(section_ids))
    for section in sections:
        if section["title"] == "Porque viste Attack Demo":
            assert {show["reason_source_id"] for show in section["items"]} == {10}
        if section["title"] == "Porque viste Arcane Demo":
            assert {show["reason_source_id"] for show in section["items"]} == {11}


def test_only_completed_shows_are_hidden_on_dashboard_by_default(client):
    client.post("/series/5/add", follow_redirects=True)

    assert "Serie Finalizada" in client.get("/").get_data(as_text=True)

    client.post(
        "/episodios/500/visto",
        data={
            "show_id": "5",
            "season": "1",
            "number": "1",
            "name": "Final pendiente",
            "watched": "1",
        },
    )

    assert "Serie Finalizada" not in client.get("/").get_data(as_text=True)
    assert "Serie Finalizada" in client.get("/?estado=todas").get_data(as_text=True)
