import pytest

from arcatv import create_app
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


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
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
