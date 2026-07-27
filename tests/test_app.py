import pytest

from arcatv import create_app


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


class FakeTVMazeClient:
    def search_shows(self, query):
        assert query
        return [{"score": 1, "show": SHOW}]

    def get_show(self, show_id):
        assert show_id == 1
        return SHOW

    def get_episodes(self, show_id):
        assert show_id == 1
        return EPISODES


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "arcatv-test.sqlite"),
            "TVMAZE_CLIENT": FakeTVMazeClient(),
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
