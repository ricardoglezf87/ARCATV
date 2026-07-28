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


class FakeTVMazeClient:
    def search_shows(self, query):
        assert query
        return [{"score": 1, "show": SHOW}]

    def get_show(self, show_id):
        shows = {
            1: SHOW,
            2: CANDIDATE_HIGH_RATED,
            3: CANDIDATE_LOW_RATED,
        }
        return shows[show_id]

    def get_episodes(self, show_id):
        assert show_id == 1
        return EPISODES

    def get_akas(self, show_id):
        assert show_id in {1, 2, 3}
        return []

    def get_shows_page(self, page):
        if page == 0:
            return [SHOW, CANDIDATE_LOW_RATED, CANDIDATE_HIGH_RATED]
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
    assert "1 de 1 emitidos vistos" in client.get("/").get_data(as_text=True)


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

    filtered_html = client.get("/recomendaciones?genero=Comedia").get_data(as_text=True)
    assert "Drama Excelente" not in filtered_html
