import html
import re
from collections import OrderedDict
from datetime import date, datetime, timezone


SPANISH_COUNTRIES = [
    "ES",
    "MX",
    "AR",
    "CL",
    "CO",
    "PE",
    "VE",
    "UY",
    "PY",
    "BO",
    "EC",
    "CR",
    "CU",
    "DO",
    "GT",
    "HN",
    "NI",
    "PA",
    "PR",
    "SV",
    "GQ",
]

STATUS_TRANSLATIONS = {
    "Running": "En emisión",
    "Ended": "Finalizada",
    "To Be Determined": "Por confirmar",
    "In Development": "En desarrollo",
}

GENRE_TRANSLATIONS = {
    "Action": "Acción",
    "Adventure": "Aventura",
    "Anime": "Anime",
    "Children": "Infantil",
    "Comedy": "Comedia",
    "Crime": "Crimen",
    "Drama": "Drama",
    "Espionage": "Espionaje",
    "Family": "Familiar",
    "Fantasy": "Fantasía",
    "Food": "Cocina",
    "History": "Historia",
    "Horror": "Terror",
    "Legal": "Legal",
    "Medical": "Médica",
    "Music": "Música",
    "Mystery": "Misterio",
    "Nature": "Naturaleza",
    "Romance": "Romance",
    "Science-Fiction": "Ciencia ficción",
    "Soap": "Telenovela",
    "Sports": "Deportes",
    "Supernatural": "Sobrenatural",
    "Thriller": "Suspense",
    "Travel": "Viajes",
    "War": "Bélica",
    "Western": "Wéstern",
}

TELENOVELA_NETWORKS = {
    "Caracol TV",
    "Las Estrellas",
    "Telemundo",
    "Televisa",
    "Univision",
}

TELENOVELA_TERMS = [
    "soap opera",
    "telenovela",
    "telenovelas",
    "novela",
]

MONTHS = [
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
]


def strip_html(value):
    if not value:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def preferred_spanish_name(akas):
    if not akas:
        return None

    by_country = {}
    for aka in akas:
        country = aka.get("country") or {}
        code = country.get("code")
        name = aka.get("name")
        if code and name and code not in by_country:
            by_country[code] = name

    for code in SPANISH_COUNTRIES:
        if by_country.get(code):
            return by_country[code]
    return None


def translated_status(status):
    return STATUS_TRANSLATIONS.get(status, status or "Sin estado")


def translated_genres(genres, show=None, network=None):
    translated = [GENRE_TRANSLATIONS.get(genre, genre) for genre in (genres or [])]
    source_text = " ".join(
        str(value or "")
        for value in [
            (show or {}).get("name"),
            (show or {}).get("summary"),
            network.get("name") if network else "",
        ]
    ).casefold()

    if (
        "Soap" in (genres or [])
        or (network and network.get("name") in TELENOVELA_NETWORKS)
        or any(term in source_text for term in TELENOVELA_TERMS)
    ):
        translated.append("Telenovela")

    return sorted(dict.fromkeys(translated))


def normalize_show(show, akas=None):
    network = show.get("network") or show.get("webChannel") or {}
    image = show.get("image") or {}
    original_name = show.get("name") or "Sin título"
    spanish_name = preferred_spanish_name(akas)

    return {
        "id": show["id"],
        "name": spanish_name or original_name,
        "original_name": original_name,
        "premiered": show.get("premiered"),
        "ended": show.get("ended"),
        "status": translated_status(show.get("status")),
        "language": show.get("language") or "Sin idioma",
        "genres": translated_genres(show.get("genres"), show=show, network=network),
        "summary": strip_html(show.get("summary")),
        "image_url": image.get("medium") or image.get("original"),
        "official_url": show.get("officialSite") or show.get("url"),
        "network": network.get("name") if network else None,
    }


def normalize_search_result(result, saved_ids, akas=None):
    show = normalize_show(result["show"], akas=akas)
    show["score"] = result.get("score", 0)
    show["is_saved"] = show["id"] in saved_ids
    return show


def parse_airdate(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def normalize_episode(episode, show):
    image = episode.get("image") or {}
    airdate_value = parse_airdate(episode.get("airdate"))

    return {
        "id": episode["id"],
        "show_id": show["id"],
        "show_name": show["name"],
        "show_image_url": show.get("image_url"),
        "name": episode.get("name") or "Sin título",
        "season": episode.get("season"),
        "number": episode.get("number"),
        "airdate": episode.get("airdate"),
        "airtime": episode.get("airtime"),
        "airdate_value": airdate_value,
        "runtime": episode.get("runtime"),
        "summary": strip_html(episode.get("summary")),
        "image_url": image.get("medium") or image.get("original"),
        "watched": False,
        "aired": bool(airdate_value and airdate_value <= date.today()),
        "upcoming": bool(airdate_value and airdate_value > date.today()),
    }


def uses_year_based_numbering(episodes):
    seasons = {episode["season"] for episode in episodes if episode.get("season")}
    if len(seasons) < 2:
        return False

    max_year = date.today().year + 2
    return all(1900 <= season <= max_year for season in seasons)


def enrich_episode_numbering(episodes):
    year_based = uses_year_based_numbering(episodes)
    for index, episode in enumerate(episodes, start=1):
        episode["absolute_number"] = index
        episode["year_based_numbering"] = year_based
        episode["season_heading"] = (
            f"Emisión {episode['season']}" if year_based and episode.get("season") else None
        )
        episode["display_code"] = f"E{index}" if year_based else standard_episode_code(episode)
    return episodes


def build_show_state(show, raw_episodes, watched_ids, latest_watched_at=None):
    episodes = [normalize_episode(item, show) for item in raw_episodes]
    enrich_episode_numbering(episodes)
    for episode in episodes:
        episode["watched"] = episode["id"] in watched_ids

    aired_episodes = [episode for episode in episodes if episode["aired"]]
    watched_count = sum(1 for episode in aired_episodes if episode["watched"])
    aired_count = len(aired_episodes)
    progress = round((watched_count / aired_count) * 100) if aired_count else 0
    next_unwatched = next((episode for episode in aired_episodes if not episode["watched"]), None)
    upcoming_episodes = [episode for episode in episodes if episode["upcoming"]]

    return {
        **show,
        "episodes": episodes,
        "episode_count": len(episodes),
        "aired_count": aired_count,
        "watched_count": watched_count,
        "completed": bool(aired_count and watched_count >= aired_count),
        "latest_watched_at": latest_watched_at,
        "progress": progress,
        "next_unwatched": next_unwatched,
        "next_upcoming": sort_upcoming(upcoming_episodes)[:1][0] if upcoming_episodes else None,
        "upcoming_episodes": upcoming_episodes,
    }


def build_episode_groups(episodes):
    groups = OrderedDict()
    for episode in episodes:
        if episode.get("season_heading"):
            season = episode["season_heading"]
        elif episode.get("season"):
            season = f"Temporada {episode['season']}"
        else:
            season = "Especiales"
        groups.setdefault(season, []).append(episode)
    return groups.items()


def sort_dashboard_shows(shows):
    def sort_key(show):
        if show["next_unwatched"]:
            bucket = 0
        elif show["completed"]:
            bucket = 2
        else:
            bucket = 1

        latest = parse_iso_datetime(show.get("latest_watched_at"))
        latest_sort = -latest.timestamp() if latest else 0
        return (bucket, latest_sort, show["name"].casefold())

    return sorted(shows, key=sort_key)


def sort_upcoming(episodes):
    return sorted(
        episodes,
        key=lambda episode: (
            episode.get("airdate") or "9999-12-31",
            episode.get("airtime") or "23:59",
            episode.get("show_name") or "",
        ),
    )


def standard_episode_code(episode):
    season = episode.get("season")
    number = episode.get("number")
    if season is None and number is None:
        return "Especial"
    if number is None:
        return f"T{season}"
    return f"T{season:02d} E{number:02d}"


def episode_code(episode):
    return episode.get("display_code") or standard_episode_code(episode)


def format_date(value):
    parsed = parse_airdate(value)
    if not parsed:
        return "Sin fecha"
    return f"{parsed.day} {MONTHS[parsed.month - 1]} {parsed.year}"


def format_air_datetime(episode):
    date_label = format_date(episode.get("airdate"))
    if episode.get("airtime"):
        return f"{date_label} · {episode['airtime']}"
    return date_label


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def episode_modal_payload(episode):
    return {
        "code": episode_code(episode),
        "title": episode["name"],
        "show": episode["show_name"],
        "air": format_air_datetime(episode),
        "runtime": f"{episode['runtime']} min" if episode.get("runtime") else "Duración no disponible",
        "summary": episode["summary"] or "Sin sinopsis disponible.",
        "image": episode.get("image_url") or "",
        "status": "Visto" if episode["watched"] else ("Pendiente" if episode["aired"] else "Próximo"),
        "absolute": episode.get("absolute_number"),
    }


def current_year():
    return datetime.now().year
