from collections import Counter
from datetime import date

from .utils import normalize_show


GENERIC_MATCH_GENRES = {"Acción", "Aventura", "Comedia", "Drama", "Romance"}


def rating_value(show):
    rating = show.get("rating") or {}
    if isinstance(rating, dict):
        return rating.get("average") or 0
    if isinstance(rating, (int, float)):
        return rating
    return show.get("vote_average") or 0


def premiered_year(show):
    premiered = show.get("premiered")
    if not premiered:
        return None
    try:
        return int(premiered[:4])
    except (TypeError, ValueError):
        return None


def normalize_candidate(raw_show):
    if raw_show.get("_normalized"):
        return dict(raw_show)
    show = normalize_show(raw_show)
    show["source"] = "tvmaze"
    show["source_label"] = "TVmaze"
    return show


def source_priority(show):
    return 0 if show.get("source") == "tmdb" else 1


def meaningful_genres(genres):
    return set(genres or []).difference(GENERIC_MATCH_GENRES)


def profile_source_ids(show):
    return {
        source.get("id")
        for source in show.get("profile_sources") or []
        if source.get("id") is not None
    }


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

    meaningful_profile = meaningful_genres(profile)
    if year_from is None:
        year_from = date.today().year - 8

    recommendations = []
    genre_options = set()
    saved_signatures = {
        (
            (show.get("original_name") or show.get("name") or "").casefold(),
            (show.get("premiered") or "")[:4],
        )
        for show in show_states
    }

    for raw_show in raw_candidates:
        if raw_show.get("id") in saved_ids:
            continue

        show = normalize_candidate(raw_show)
        signature = (
            (show.get("original_name") or show.get("name") or "").casefold(),
            (show.get("premiered") or "")[:4],
        )
        if signature in saved_signatures:
            continue

        year = premiered_year(show)
        if year_from and (year is None or year < year_from):
            continue
        if year_to and (year is None or year > year_to):
            continue

        genres = set(show.get("genres") or [])
        overlap = genres.intersection(profile)
        source_match = bool(profile_source_ids(show))
        meaningful_overlap = overlap.intersection(meaningful_profile)
        if not overlap and not source_match:
            continue
        if meaningful_profile and not meaningful_overlap and not source_match:
            continue

        genre_options.update(genres)
        if selected_genre and selected_genre not in genres:
            continue

        rating = rating_value(raw_show)
        affinity = sum(
            profile[genre] * (1.35 if genre in meaningful_overlap else 0.6)
            for genre in overlap
        )
        if source_match:
            affinity += 4
        show.update(
            {
                "rating": rating,
                "affinity": round(min(100, affinity * 18 + rating * 4)),
                "matched_genres": sorted(meaningful_overlap or overlap),
                "premiered_year": year,
                "source_priority": source_priority(show),
                "source_match": source_match,
            }
        )
        recommendations.append(show)

    if sort_mode == "puntuacion":
        recommendations.sort(
            key=lambda show: (
                show["source_priority"],
                -int(show["source_match"]),
                -(show["rating"] or 0),
                -(show.get("premiered_year") or 0),
                -show["affinity"],
                show["name"].casefold(),
            )
        )
    else:
        recommendations.sort(
            key=lambda show: (
                show["source_priority"],
                -int(show["source_match"]),
                -(show.get("premiered_year") or 0),
                -(show["rating"] or 0),
                -show["affinity"],
                show["name"].casefold(),
            )
        )

    return recommendations[:limit], sorted(genre_options)


def add_recommendation_reasons(recommendations, show_states):
    source_shows = [
        show for show in show_states
        if show.get("watched_count") or show.get("completed")
    ]
    source_by_id = {show["id"]: show for show in source_shows}

    for recommendation in recommendations:
        profile_sources = recommendation.get("profile_sources") or []
        direct_source = next(
            (
                source_by_id.get(profile_source.get("id"))
                for profile_source in profile_sources
                if source_by_id.get(profile_source.get("id"))
            ),
            None,
        )
        if direct_source:
            relation = profile_sources[0].get("relation")
            detail = "Recomendacion directa de TMDb"
            if relation == "similar":
                detail = "Serie similar segun TMDb"
            recommendation["reason"] = f"Porque viste {direct_source['name']}"
            recommendation["reason_detail"] = detail
            recommendation["reason_source_id"] = direct_source["id"]
            continue

        rec_genres = set(recommendation.get("genres") or [])
        best_source = None
        best_overlap = set()
        best_meaningful_overlap = set()

        for source in source_shows:
            overlap = rec_genres.intersection(source.get("genres") or [])
            meaningful_overlap = meaningful_genres(overlap)
            if (
                len(meaningful_overlap),
                len(overlap),
            ) > (
                len(best_meaningful_overlap),
                len(best_overlap),
            ):
                best_source = source
                best_overlap = overlap
                best_meaningful_overlap = meaningful_overlap

        if best_source:
            recommendation["reason"] = f"Porque viste {best_source['name']}"
            displayed_overlap = best_meaningful_overlap or best_overlap
            recommendation["reason_detail"] = "Coincide en " + ", ".join(sorted(displayed_overlap))
            recommendation["reason_source_id"] = best_source["id"]
        else:
            recommendation["reason"] = "Segun tus series vistas"
            recommendation["reason_detail"] = ""

    return recommendations


def top_profile_genres(show_states, limit=4):
    return [genre for genre, _weight in recommendation_profile(show_states).most_common(limit)]


def top_profile_platforms(show_states, limit=4):
    counter = Counter()
    for show in show_states:
        if not show.get("watched_count") and not show.get("completed"):
            continue
        if show.get("network"):
            counter[show["network"]] += 3 if show.get("completed") else 1
    return [platform for platform, _weight in counter.most_common(limit)]
