import re
import urllib3

import requests


class TranslationError(RuntimeError):
    pass


SPANISH_HINTS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "de",
    "del",
    "que",
    "para",
    "con",
    "sin",
    "serie",
    "episodio",
    "temporada",
}


def looks_spanish(text):
    words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", text.lower())
    if not words:
        return False
    if re.search(r"[áéíóúüñ¿¡]", text.lower()):
        return True

    hint_count = sum(1 for word in words if word in SPANISH_HINTS)
    return hint_count / len(words) >= 0.08


def split_for_translation(text, max_bytes=450):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue

        if current:
            chunks.append(current)
        current = sentence

        while len(current.encode("utf-8")) > max_bytes:
            slice_end = max_bytes
            while slice_end > 0 and len(current[:slice_end].encode("utf-8")) > max_bytes:
                slice_end -= 1
            chunks.append(current[:slice_end].strip())
            current = current[slice_end:].strip()

    if current:
        chunks.append(current)
    return chunks


class MyMemoryClient:
    def __init__(
        self,
        base_url="https://api.mymemory.translated.net",
        session=None,
        timeout=10,
        allow_unverified_https_fallback=True,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.allow_unverified_https_fallback = allow_unverified_https_fallback
        self.user_agent = "ARCATV/0.1 (+local-tv-tracker)"

    def translate_to_spanish(self, text):
        if not text or looks_spanish(text):
            return text

        translated_chunks = [self._translate_chunk(chunk) for chunk in split_for_translation(text)]
        translated = " ".join(chunk for chunk in translated_chunks if chunk).strip()
        return translated or text

    def _translate_chunk(self, chunk):
        try:
            response = self._request(chunk)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TranslationError("No se pudo traducir el texto ahora.") from exc

        response_data = payload.get("responseData") or {}
        return response_data.get("translatedText") or chunk

    def _request(self, chunk):
        params = {"q": chunk, "langpair": "en|es"}
        headers = {"User-Agent": self.user_agent}
        try:
            return self.session.get(
                f"{self.base_url}/get",
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError:
            if not self.allow_unverified_https_fallback:
                raise

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return self.session.get(
                f"{self.base_url}/get",
                params=params,
                headers=headers,
                timeout=self.timeout,
                verify=False,
            )
