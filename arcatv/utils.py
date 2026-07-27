import html
import re
from collections import OrderedDict
from datetime import date, datetime


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


def normalize_show(show):
    network = show.get("network") or show.get("webChannel") or {}
    image = show.get("image") or {}

    return {
        "id": show["id"],
        "name": show.get("name") or "Sin título",
        "premiered": show.get("premiered"),
        "ended": show.get("ended"),
        "status": show.get("status") or "Sin estado",
        "language": show.get("language") or "Sin idioma",
        "genres": show.get("genres") or [],
        "summary": strip_html(show.get("summary")),
        "image_url": image.get("medium") or image.get("original"),
        "official_url": show.get("officialSite") or show.get("url"),
        "network": network.get("name") if network else None,
    }


def normalize_search_result(result, saved_ids):
    show = normalize_show(result["show"])
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


def build_show_state(show, raw_episodes, watched_ids):
    episodes = [normalize_episode(item, show) for item in raw_episodes]
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
        "progress": progress,
        "next_unwatched": next_unwatched,
        "next_upcoming": sort_upcoming(upcoming_episodes)[:1][0] if upcoming_episodes else None,
        "upcoming_episodes": upcoming_episodes,
    }


def build_episode_groups(episodes):
    groups = OrderedDict()
    for episode in episodes:
        season = episode["season"] or 0
        groups.setdefault(season, []).append(episode)
    return groups.items()


def sort_upcoming(episodes):
    return sorted(
        episodes,
        key=lambda episode: (
            episode.get("airdate") or "9999-12-31",
            episode.get("airtime") or "23:59",
            episode.get("show_name") or "",
        ),
    )


def episode_code(episode):
    season = episode.get("season")
    number = episode.get("number")
    if season is None and number is None:
        return "Especial"
    if number is None:
        return f"T{season}"
    return f"T{season:02d} E{number:02d}"


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


def current_year():
    return datetime.now().year
