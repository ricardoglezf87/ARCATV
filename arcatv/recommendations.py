from collections import Counter
from datetime import date

from .utils import normalize_show


def rating_value(show):
    rating = show.get("rating") or {}
    return rating.get("average") or 0


def premiered_year(show):
    premiered = show.get("premiered")
    if not premiered:
        return None
    try:
        return int(premiered[:4])
    except (TypeError, ValueError):
        return None


def recommendation_profile(show_states):
    weights = Counter()

    for show in show_states:
        if not show["watched_count"] and not show["completed"]:
            continue

        progress_weight = max(show["progress"], 15) / 100
        show_weight = 3 if show["completed"] else 1 + progress_weight
        for genre in show.get("genres") or []:
            weights[genre] += show_weight

    return weights


def rank_recommendations(
    show_states,
    raw_candidates,
    saved_ids,
    selected_genre=None,
    year_from=None,
    year_to=None,
    sort_mode="recientes",
    limit=48,
):
    profile = recommendation_profile(show_states)
    if not profile:
        return [], []

    if year_from is None:
        year_from = date.today().year - 8

    recommendations = []
    genre_options = set()

    for raw_show in raw_candidates:
        if raw_show.get("id") in saved_ids:
            continue

        show = normalize_show(raw_show)
        year = premiered_year(show)
        if year_from and (year is None or year < year_from):
            continue
        if year_to and (year is None or year > year_to):
            continue

        genres = set(show.get("genres") or [])
        overlap = genres.intersection(profile)
        if not overlap:
            continue

        genre_options.update(genres)
        if selected_genre and selected_genre not in genres:
            continue

        rating = rating_value(raw_show)
        affinity = sum(profile[genre] for genre in overlap)
        show.update(
            {
                "rating": rating,
                "affinity": round(min(100, affinity * 18 + rating * 4)),
                "matched_genres": sorted(overlap),
                "premiered_year": year,
            }
        )
        recommendations.append(show)

    if sort_mode == "puntuacion":
        recommendations.sort(
            key=lambda show: (
                -(show["rating"] or 0),
                -(show.get("premiered_year") or 0),
                -show["affinity"],
                show["name"].casefold(),
            )
        )
    else:
        recommendations.sort(
            key=lambda show: (
                -(show.get("premiered_year") or 0),
                -(show["rating"] or 0),
                -show["affinity"],
                show["name"].casefold(),
            )
        )

    return recommendations[:limit], sorted(genre_options)
