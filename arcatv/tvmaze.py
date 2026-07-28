import time

import requests


class TVMazeError(RuntimeError):
    pass


class TVMazeClient:
    def __init__(
        self,
        base_url="https://api.tvmaze.com",
        session=None,
        timeout=10,
        allow_http_fallback=True,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.allow_http_fallback = allow_http_fallback
        self.user_agent = "ARCATV/0.1 (+local-tv-tracker)"

    def search_shows(self, query):
        return self._get("/search/shows", {"q": query}) or []

    def get_show(self, show_id):
        return self._get(f"/shows/{show_id}")

    def get_episodes(self, show_id):
        return self._get(f"/shows/{show_id}/episodes") or []

    def get_akas(self, show_id):
        return self._get(f"/shows/{show_id}/akas") or []

    def _get(self, path, params=None):
        url = f"{self.base_url}{path}"
        headers = {"User-Agent": self.user_agent}

        for attempt in range(2):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.exceptions.SSLError as exc:
                if self.allow_http_fallback and url.startswith("https://api.tvmaze.com"):
                    return self._get_over_http(path, params)
                raise TVMazeError("TVmaze no respondió. Revisa los certificados de la conexión.") from exc
            except requests.RequestException as exc:
                raise TVMazeError("TVmaze no respondió. Revisa la conexión e inténtalo de nuevo.") from exc

            if response.status_code == 404:
                return None

            if response.status_code == 429 and attempt == 0:
                retry_after = response.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else 2
                time.sleep(min(delay, 5))
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise TVMazeError(f"TVmaze devolvió un error {response.status_code}.") from exc

            try:
                return response.json()
            except ValueError as exc:
                raise TVMazeError("TVmaze devolvió una respuesta que no se pudo leer.") from exc

        raise TVMazeError("TVmaze está limitando las peticiones. Espera unos segundos y vuelve a probar.")

    def _get_over_http(self, path, params=None):
        original_base_url = self.base_url
        try:
            self.base_url = "http://api.tvmaze.com"
            return self._get(path, params)
        finally:
            self.base_url = original_base_url
