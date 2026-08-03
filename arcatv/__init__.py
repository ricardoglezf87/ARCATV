import hashlib
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

from flask import Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import db as store
from .recommendations import (
    add_recommendation_reasons,
    rank_recommendations,
    top_profile_genres,
    top_profile_platforms,
)
from .tmdb import (
    TMDbClient,
    TMDbError,
    is_tmdb_show_id,
    normalize_tmdb_episode,
    normalize_tmdb_show,
    tmdb_id_from_show_id,
)
from .translation import MyMemoryClient, TranslationError
from .tvmaze import TVMazeClient, TVMazeError
from .utils import (
    build_episode_groups,
    build_show_state,
    episode_code,
    episode_modal_payload,
    format_air_datetime,
    format_date,
    normalize_episode,
    normalize_search_result,
    normalize_show,
    sort_dashboard_shows,
    sort_upcoming,
    strip_html,
)


GENRE_FILTER_OPTIONS = [
    "Acción",
    "Aventura",
    "Anime",
    "Ciencia ficción",
    "Comedia",
    "Crimen",
    "Drama",
    "Fantasía",
    "Misterio",
    "Romance",
    "Sobrenatural",
    "Suspense",
    "Telenovela",
    "Terror",
]


def local_config_value(name):
    if os.environ.get(name):
        return os.environ[name]

    seen_paths = set()
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if env_path in seen_paths or not env_path.exists():
            continue
        seen_paths.add(env_path)
        for line in env_path.read_text(encoding="utf-8").splitlines():
            clean_line = line.strip()
            if not clean_line or clean_line.startswith("#") or "=" not in clean_line:
                continue
            key, value = clean_line.split("=", 1)
            if key.strip().lstrip("\ufeff") == name:
                return value.strip().strip('"').strip("'")
    return None


def local_config_bool(name, default=True):
    value = local_config_value(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off"}


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=str(Path(app.instance_path) / "arcatv.sqlite"),
        TVMAZE_BASE_URL="https://api.tvmaze.com",
        TVMAZE_CACHE_SEARCH_SECONDS=15 * 60,
        TVMAZE_CACHE_SHOW_SECONDS=60 * 60,
        TVMAZE_CACHE_AKAS_SECONDS=24 * 60 * 60,
        TVMAZE_CACHE_EPISODES_SECONDS=6 * 60 * 60,
        TVMAZE_CACHE_CATALOG_SECONDS=7 * 24 * 60 * 60,
        TVMAZE_RECOMMENDATION_PAGES=10,
        TVMAZE_RECOMMENDATION_RECENT_PAGES=12,
        RECOMMENDATION_LIMIT=24,
        TMDB_API_KEY=local_config_value("TMDB_API_KEY"),
        TMDB_BEARER_TOKEN=local_config_value("TMDB_BEARER_TOKEN"),
        TMDB_BASE_URL="https://api.themoviedb.org/3",
        TMDB_CACHE_SECONDS=24 * 60 * 60,
        TMDB_VERIFY_SSL=local_config_bool("TMDB_VERIFY_SSL", True),
        TRANSLATE_TMDB_SUMMARIES=False,
        TRANSLATE_TO_SPANISH=True,
        TRANSLATION_CACHE_SECONDS=30 * 24 * 60 * 60,
        MYMEMORY_BASE_URL="https://api.mymemory.translated.net",
        AUTO_REFRESH_ON_DASHBOARD=False,
    )

    if test_config:
        app.config.update(test_config)

    store.init_app(app)
    register_template_helpers(app)
    register_routes(app)
    return app


def register_template_helpers(app):
    app.template_filter("episode_code")(episode_code)
    app.template_filter("episode_modal_payload")(episode_modal_payload)
    app.template_filter("format_date")(format_date)
    app.template_filter("format_air_datetime")(format_air_datetime)


def register_routes(app):
    @app.get("/")
    def dashboard():
        shows = []
        sync_errors = []
        show_finalized = request.args.get("estado") == "todas"

        for saved_show in store.list_shows():
            show = saved_show
            episodes = get_show_episodes_cached(show["id"])

            watched_ids = store.get_watched_ids(show["id"])
            latest_watched_at = store.get_latest_watched_at(show["id"])
            state = build_show_state(show, episodes, watched_ids, latest_watched_at)
            if not show_finalized and is_show_completed(state):
                continue
            shows.append(state)

        shows = sort_dashboard_shows(shows)
        all_shows = store.list_shows()
        hidden_completed_count = 0
        if not show_finalized:
            for show in all_shows:
                state = build_show_state(
                    show,
                    get_show_episodes_cached(show["id"]),
                    store.get_watched_ids(show["id"]),
                    store.get_latest_watched_at(show["id"]),
                )
                if is_show_completed(state):
                    hidden_completed_count += 1

        upcoming = sort_upcoming(
            episode
            for show in shows
            for episode in show["upcoming_episodes"]
        )

        totals = {
            "shows": len(shows),
            "all_shows": len(all_shows),
            "hidden_finalized": hidden_completed_count,
            "watched": sum(show["watched_count"] for show in shows),
            "aired": sum(show["aired_count"] for show in shows),
            "upcoming": len(upcoming),
        }

        return render_template(
            "index.html",
            shows=shows,
            upcoming=upcoming[:8],
            sync_errors=sync_errors,
            totals=totals,
            show_finalized=show_finalized,
        )

    @app.post("/actualizar")
    def refresh_library():
        include_finalized = request.form.get("alcance") == "todas"
        updated = 0
        failed = []

        for saved_show in store.list_shows():
            if not include_finalized and is_show_finalized(saved_show):
                continue

            try:
                updated_show = get_show_for_storage(saved_show["id"], refresh=True)
                if updated_show:
                    store.upsert_show(updated_show)
                get_show_episodes(saved_show["id"], refresh=True)
                updated += 1
            except (TVMazeError, TMDbError):
                failed.append(saved_show["name"])

        if updated:
            scope = "todas las series" if include_finalized else "las series en emisión"
            flash(f"Se actualizaron {updated} de {scope}.", "success")
        if failed:
            flash(f"No se pudieron actualizar: {', '.join(failed)}.", "warning")
        if not updated and not failed:
            flash("No había series para actualizar con ese filtro.", "warning")

        return redirect_to_next(url_for("dashboard"))

    @app.get("/buscar")
    def search():
        query = request.args.get("q", "").strip()
        selected_genre = request.args.get("genero", "").strip()
        results = []
        error = None
        saved_ids = store.get_show_ids()

        if query:
            if is_tmdb_enabled():
                results = search_tmdb_results(query, saved_ids)
            if not results:
                results, error = search_tvmaze_results(query, saved_ids)

            if selected_genre:
                results = [
                    show for show in results
                    if selected_genre in (show.get("genres") or [])
                ]

        return render_template(
            "search.html",
            query=query,
            results=results,
            error=error,
            genres=GENRE_FILTER_OPTIONS,
            selected_genre=selected_genre,
            tmdb_enabled=is_tmdb_enabled(),
        )

    @app.post("/series/<int:show_id>/add")
    def add_show(show_id):
        try:
            show = get_show_for_storage(show_id, refresh=True)
        except (TVMazeError, TMDbError) as exc:
            flash(f"No se pudo añadir la serie: {exc}", "error")
            return redirect_to_next(url_for("search", q=request.form.get("q", "")))

        if not show:
            abort(404)

        store.upsert_show(show)

        try:
            get_show_episodes(show_id, refresh=True)
        except (TVMazeError, TMDbError):
            flash(
                f"{show['name']} se añadió, pero los episodios se sincronizarán al abrirla.",
                "warning",
            )
        else:
            flash(f"{show['name']} se añadió a tus series.", "success")

        return redirect(url_for("show_detail", show_id=show_id))

    @app.get("/series/<int:show_id>")
    def show_detail(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        show_watched = request.args.get("vistos") == "1"
        sync_error = None
        episodes = get_show_episodes_cached(show_id)

        watched_ids = store.get_watched_ids(show_id)
        latest_watched_at = store.get_latest_watched_at(show_id)
        state = build_show_state(show, episodes, watched_ids, latest_watched_at)
        visible_episodes = state["episodes"] if show_watched else [
            episode for episode in state["episodes"] if not episode["watched"]
        ]
        episode_groups = build_episode_groups(visible_episodes)

        return render_template(
            "show.html",
            show=state,
            show_watched=show_watched,
            visible_episode_count=len(visible_episodes),
            episode_groups=episode_groups,
            sync_error=sync_error,
        )

    @app.get("/series/<int:show_id>/episodios/<int:episode_id>")
    def episode_detail(show_id, episode_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            episodes = get_show_episodes(show_id)
        except (TVMazeError, TMDbError):
            abort(404)

        state = build_show_state(show, episodes, store.get_watched_ids(show_id))
        episode = next(
            (item for item in state["episodes"] if item["id"] == episode_id),
            None,
        )
        if not episode:
            abort(404)

        enhance_episode_summary(episode)
        return jsonify(episode_modal_payload(episode))

    @app.post("/series/<int:show_id>/refresh")
    def refresh_show(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            updated_show = get_show_for_storage(show_id, refresh=True)
            episodes = get_show_episodes(show_id, refresh=True)
        except (TVMazeError, TMDbError) as exc:
            flash(f"No se pudo actualizar {show['name']}: {exc}", "error")
        else:
            if not updated_show:
                flash(f"No se encontró {show['name']} en TVmaze.", "error")
                return redirect(url_for("show_detail", show_id=show_id))

            store.upsert_show(updated_show)
            flash(f"{updated_show['name']} se actualizó con {len(episodes)} episodios.", "success")

        return redirect(url_for("show_detail", show_id=show_id))

    @app.post("/series/<int:show_id>/remove")
    def remove_show(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        store.remove_show(show_id)
        flash(f"{show['name']} salió de tus series.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/series/<int:show_id>/watch-aired")
    def watch_aired(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            episodes = [normalize_episode(item, show) for item in get_show_episodes(show_id)]
        except (TVMazeError, TMDbError) as exc:
            flash(f"No se pudieron cargar los episodios de {show['name']}: {exc}", "error")
            return redirect(url_for("show_detail", show_id=show_id))

        count = 0
        today = date.today()
        for episode in episodes:
            if episode["airdate_value"] and episode["airdate_value"] <= today:
                store.mark_episode(episode)
                count += 1

        flash(f"Marcados {count} episodios emitidos de {show['name']}.", "success")
        return redirect_to_next(url_for("show_detail", show_id=show_id))

    @app.post("/series/<int:show_id>/clear")
    def clear_show_progress(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        store.clear_show_progress(show_id)
        flash(f"Progreso reiniciado para {show['name']}.", "success")
        return redirect_to_next(url_for("show_detail", show_id=show_id))

    @app.post("/series/<int:show_id>/watch-through/<int:episode_id>")
    def watch_through(show_id, episode_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            episodes = [normalize_episode(item, show) for item in get_show_episodes(show_id)]
        except (TVMazeError, TMDbError) as exc:
            flash(f"No se pudieron cargar los episodios de {show['name']}: {exc}", "error")
            return redirect(url_for("show_detail", show_id=show_id))

        marked = 0
        today = date.today()
        for episode in episodes:
            if episode["airdate_value"] and episode["airdate_value"] <= today:
                store.mark_episode(episode)
                marked += 1
            if episode["id"] == episode_id:
                break

        flash(f"Marcados {marked} episodios hasta ese punto.", "success")
        return redirect_to_next(url_for("show_detail", show_id=show_id))

    @app.post("/episodios/<int:episode_id>/visto")
    def set_episode_watched(episode_id):
        show_id = request.form.get("show_id", type=int)
        if not show_id:
            abort(400)

        if request.form.get("watched") == "1":
            store.mark_episode(
                {
                    "id": episode_id,
                    "show_id": show_id,
                    "season": request.form.get("season", type=int),
                    "number": request.form.get("number", type=int),
                    "name": request.form.get("name", ""),
                }
            )
        else:
            store.unmark_episode(episode_id)

        return redirect_to_next(url_for("show_detail", show_id=show_id))

    @app.get("/proximos")
    def upcoming():
        upcoming_episodes = []
        sync_errors = []
        show_finalized = request.args.get("estado") == "todas"

        for saved_show in store.list_shows():
            show = saved_show
            episodes = get_show_episodes_cached(show["id"])

            state = build_show_state(
                show,
                episodes,
                store.get_watched_ids(show["id"]),
                store.get_latest_watched_at(show["id"]),
            )
            if not show_finalized and is_show_completed(state):
                continue
            upcoming_episodes.extend(state["upcoming_episodes"])

        return render_template(
            "upcoming.html",
            upcoming=sort_upcoming(upcoming_episodes),
            sync_errors=sync_errors,
            show_finalized=show_finalized,
        )

    @app.get("/recomendaciones")
    def recommendations():
        selected_genre = request.args.get("genero", "").strip()
        current_year = date.today().year
        year_from = request.args.get("desde", type=int)
        year_to = request.args.get("hasta", type=int)
        sort_mode = request.args.get("orden", "recientes")
        if year_from is None and "desde" not in request.args:
            year_from = current_year - 8
        if sort_mode not in {"recientes", "puntuacion"}:
            sort_mode = "recientes"
        sync_errors = []

        show_states = []
        for show in store.list_shows():
            try:
                episodes = get_show_episodes(show["id"])
            except (TVMazeError, TMDbError):
                episodes = []
                sync_errors.append(show["name"])

            show_states.append(
                build_show_state(
                    show,
                    episodes,
                    store.get_watched_ids(show["id"]),
                    store.get_latest_watched_at(show["id"]),
                )
            )

        raw_candidates = get_tmdb_profile_candidates(show_states)
        recommendations, genres = rank_recommendations(
            show_states,
            raw_candidates,
            store.get_show_ids(),
            selected_genre=selected_genre or None,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            limit=current_app.config["RECOMMENDATION_LIMIT"],
        )

        if not recommendations:
            try:
                raw_candidates = get_recommendation_catalog()
            except TVMazeError as exc:
                raw_candidates = []
                sync_errors.append(str(exc))
            recommendations, genres = rank_recommendations(
                show_states,
                raw_candidates,
                store.get_show_ids(),
                selected_genre=selected_genre or None,
                year_from=year_from,
                year_to=year_to,
                sort_mode=sort_mode,
                limit=current_app.config["RECOMMENDATION_LIMIT"],
            )

        enriched_recommendations = add_recommendation_reasons([
            enhance_show_summary(recommendation)
            for recommendation in recommendations
        ], show_states)
        sections = build_recommendation_sections(enriched_recommendations, show_states)

        return render_template(
            "recommendations.html",
            recommendations=enriched_recommendations,
            sections=sections,
            genres=sorted(set(genres).union({"Telenovela"})),
            selected_genre=selected_genre,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            current_year=current_year,
            sync_errors=sync_errors,
            has_profile=bool(show_states),
            tmdb_enabled=is_tmdb_enabled(),
        )


def get_tvmaze_client():
    injected = current_app.config.get("TVMAZE_CLIENT")
    if injected:
        return injected

    if "tvmaze_client" not in g:
        g.tvmaze_client = TVMazeClient(base_url=current_app.config["TVMAZE_BASE_URL"])
    return g.tvmaze_client


def get_tmdb_client():
    injected = current_app.config.get("TMDB_CLIENT")
    if injected:
        return injected

    if "tmdb_client" not in g:
        g.tmdb_client = TMDbClient(
            api_key=current_app.config.get("TMDB_API_KEY"),
            bearer_token=current_app.config.get("TMDB_BEARER_TOKEN"),
            base_url=current_app.config["TMDB_BASE_URL"],
            verify_ssl=current_app.config["TMDB_VERIFY_SSL"],
        )
    return g.tmdb_client


def is_tmdb_enabled():
    return get_tmdb_client().enabled


def get_translation_client():
    injected = current_app.config.get("TRANSLATION_CLIENT")
    if injected:
        return injected

    if "translation_client" not in g:
        g.translation_client = MyMemoryClient(base_url=current_app.config["MYMEMORY_BASE_URL"])
    return g.translation_client


def cached_json(key, ttl_seconds, producer, refresh=False):
    cached = store.cache_get(key)
    if cached is not None and not refresh:
        return cached

    try:
        payload = producer()
    except (TVMazeError, TMDbError):
        if cached is not None:
            return cached
        raise

    store.cache_set(key, payload, ttl_seconds)
    return payload


def translate_text_to_spanish(text, namespace):
    clean_text = strip_html(text)
    if not clean_text or not current_app.config["TRANSLATE_TO_SPANISH"]:
        return clean_text

    digest = hashlib.sha1(clean_text.encode("utf-8")).hexdigest()
    cache_key = f"translation:{namespace}:{digest}"
    cached = store.cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        translated = get_translation_client().translate_to_spanish(clean_text)
    except TranslationError:
        return clean_text

    store.cache_set(cache_key, translated, current_app.config["TRANSLATION_CACHE_SECONDS"])
    return translated


def safe_is_tmdb_show_id(value):
    try:
        return value is not None and is_tmdb_show_id(value)
    except (TypeError, ValueError):
        return False


def show_uses_tmdb(show):
    return show.get("source") == "tmdb" or safe_is_tmdb_show_id(show.get("id"))


def episode_uses_tmdb(episode):
    return safe_is_tmdb_show_id(episode.get("show_id"))


def enhance_show_summary(show):
    if show_uses_tmdb(show) and not current_app.config["TRANSLATE_TMDB_SUMMARIES"]:
        return show
    if show.get("summary"):
        show["summary"] = translate_text_to_spanish(show["summary"], f"show:{show['id']}:summary")
    return show


def normalize_show_for_display(show_data, akas=None):
    return enhance_show_summary(normalize_show(show_data, akas=akas))


def show_signature(show):
    return (
        (show.get("original_name") or show.get("name") or "").casefold(),
        (show.get("premiered") or "")[:4],
    )


def fold_search_text(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    without_symbols = re.sub(r"[^\w\s]", " ", without_marks, flags=re.UNICODE)
    return re.sub(r"\s+", " ", without_symbols).strip()


def tmdb_search_queries(query):
    variants = [query.strip(), fold_search_text(query)]
    folded_query = variants[-1]
    for word in re.findall(r"\S+", query):
        if "ñ" not in word.casefold():
            continue
        folded_word = fold_search_text(word)
        if len(folded_word) > 3 and folded_word[-1:] in "aeiou":
            truncated = folded_word[:-1]
            variants.append(folded_query.replace(folded_word, truncated))
            variants.append(truncated)
    unique = []
    for variant in variants:
        if variant and variant.casefold() not in {item.casefold() for item in unique}:
            unique.append(variant)
    return unique


def tmdb_direct_id_from_query(query):
    match = re.search(r"themoviedb\.org/tv/(\d+)", query, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    clean_query = query.strip()
    if clean_query.isdigit():
        return int(clean_query)
    return None


def search_tvmaze_results(query, saved_ids, existing_results=None):
    existing_keys = {show_signature(show) for show in (existing_results or [])}

    try:
        raw_results = cached_json(
            f"search:{query.lower()}",
            current_app.config["TVMAZE_CACHE_SEARCH_SECONDS"],
            lambda: get_tvmaze_client().search_shows(query),
        )
    except TVMazeError as exc:
        return [], f"TVmaze no respondió: {exc}."

    results = []
    for item in raw_results:
        show = normalize_search_result(
            item,
            saved_ids,
            akas=safe_get_show_akas(item["show"]["id"]),
        )
        if show_signature(show) in existing_keys:
            continue
        show["source"] = "tvmaze"
        show["source_label"] = "TVmaze"
        results.append(enhance_show_summary(show))
    return results, None


def search_tmdb_results(query, saved_ids, existing_results=None):
    if not is_tmdb_enabled():
        return []

    existing_keys = {show_signature(show) for show in (existing_results or [])}

    results = []
    seen_tmdb_ids = set()
    direct_id = tmdb_direct_id_from_query(query)
    if direct_id:
        try:
            raw_show = cached_json(
                f"tmdb:show:{direct_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda: get_tmdb_client().get_tv(direct_id),
            )
        except TMDbError:
            raw_show = None
        if raw_show:
            show = normalize_tmdb_show(raw_show)
            show["is_saved"] = show["id"] in saved_ids
            return [show]

    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"tmdb:search:v2:{search_query.casefold()}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda search_query=search_query: get_tmdb_client().search_tv(search_query),
            )
        except TMDbError:
            continue

        for raw_show in raw_results:
            tmdb_id = raw_show.get("id")
            if not tmdb_id or tmdb_id in seen_tmdb_ids:
                continue
            seen_tmdb_ids.add(tmdb_id)
            show = normalize_tmdb_show(raw_show)
            if show_signature(show) in existing_keys:
                continue
            show["is_saved"] = show["id"] in saved_ids
            results.append(show)
            if len(results) >= 12:
                return results
    return results


def build_recommendation_sections(recommendations, show_states):
    if not recommendations:
        return []

    sections = []
    used_ids = set()

    def take_unique(pool, limit=12):
        items = []
        for show in pool:
            show_id = show.get("id")
            if show_id in used_ids:
                continue
            used_ids.add(show_id)
            items.append(show)
            if len(items) >= limit:
                break
        return items

    source_shows = [
        show for show in show_states
        if show.get("watched_count") or show.get("completed")
    ][:4]
    for source in source_shows:
        source_items = [
            recommendation for recommendation in recommendations
            if recommendation.get("reason_source_id") == source["id"]
        ]
        items = take_unique(source_items)
        if items:
            sections.append(
                {
                    "title": f"Porque viste {source['name']}",
                    "subtitle": "Recomendaciones con relacion directa o generos realmente compartidos.",
                    "items": items,
                }
            )

    direct_items = take_unique(
        [recommendation for recommendation in recommendations if recommendation.get("profile_sources")]
    )
    if direct_items:
        sections.append(
            {
                "title": "Similares de TMDb",
                "subtitle": "Sugerencias directas del nuevo catalogo para tus series vistas.",
                "items": direct_items,
            }
        )

    top_items = take_unique(recommendations)
    if top_items:
        sections.append(
            {
                "title": "Para ti ahora",
                "subtitle": "Mezcla de afinidad, puntuacion y lo que ya has marcado como visto.",
                "items": top_items,
            }
        )

    trending_items = take_unique(
        sorted(
            recommendations,
            key=lambda show: (
                -(show.get("premiered_year") or 0),
                -(show.get("rating") or 0),
            ),
        )
    )
    if trending_items:
        sections.append(
            {
                "title": "Tops del momento para tus gustos",
                "subtitle": "Series recientes y bien valoradas que encajan con tu perfil.",
                "items": trending_items,
            }
        )

    for genre in top_profile_genres(show_states):
        items = take_unique([
            recommendation for recommendation in recommendations
            if genre in (recommendation.get("genres") or [])
        ])
        if items:
            sections.append(
                {
                    "title": f"Top en {genre}",
                    "subtitle": "Uno de tus géneros más repetidos.",
                    "items": items,
                }
            )

    for platform in top_profile_platforms(show_states):
        items = take_unique([
            recommendation for recommendation in recommendations
            if recommendation.get("network") == platform
        ])
        if items:
            sections.append(
                {
                    "title": f"De {platform}",
                    "subtitle": "Más opciones de cadenas o plataformas que ya aparecen en tus vistas.",
                    "items": items,
                }
            )

    return [section for section in sections if section["items"]]


def enhance_episode_summary(episode):
    if episode_uses_tmdb(episode) and not current_app.config["TRANSLATE_TMDB_SUMMARIES"]:
        return episode
    if episode.get("summary"):
        episode["summary"] = translate_text_to_spanish(
            episode["summary"],
            f"episode:{episode['id']}:summary",
        )
    return episode


def redirect_to_next(default_url):
    next_url = request.form.get("next")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(default_url)


def is_show_finalized(show):
    status = (show.get("status") or "").casefold()
    return bool(show.get("ended")) or status in {"finalizada", "ended"}


def is_show_completed(show_state):
    return bool(show_state.get("completed"))


def get_show(show_id, refresh=False):
    if is_tmdb_show_id(show_id):
        return get_tmdb_show(show_id, refresh=refresh)

    return cached_json(
        f"show:{show_id}",
        current_app.config["TVMAZE_CACHE_SHOW_SECONDS"],
        lambda: get_tvmaze_client().get_show(show_id),
        refresh=refresh,
    )


def get_show_for_storage(show_id, refresh=False):
    if is_tmdb_show_id(show_id):
        raw_show = get_tmdb_show(show_id, refresh=refresh)
        if not raw_show:
            return None
        return normalize_tmdb_show(raw_show)

    show_data, akas = get_show_with_akas(show_id, refresh=refresh)
    if not show_data:
        return None
    return normalize_show_for_display(show_data, akas=akas)


def get_tmdb_show(show_id, refresh=False):
    tmdb_id = tmdb_id_from_show_id(show_id)
    return cached_json(
        f"tmdb:show:{tmdb_id}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().get_tv(tmdb_id),
        refresh=refresh,
    )


def get_show_akas(show_id, refresh=False):
    return cached_json(
        f"akas:{show_id}",
        current_app.config["TVMAZE_CACHE_AKAS_SECONDS"],
        lambda: get_tvmaze_client().get_akas(show_id),
        refresh=refresh,
    )


def safe_get_show_akas(show_id):
    try:
        return get_show_akas(show_id)
    except TVMazeError:
        return []


def get_show_with_akas(show_id, refresh=False):
    show_data = get_show(show_id, refresh=refresh)
    if not show_data:
        return None, []
    try:
        akas = get_show_akas(show_id, refresh=refresh)
    except TVMazeError:
        akas = []
    return show_data, akas


def get_show_episodes(show_id, refresh=False):
    if is_tmdb_show_id(show_id):
        return get_tmdb_episodes(show_id, refresh=refresh)

    return cached_json(
        f"episodes:{show_id}",
        current_app.config["TVMAZE_CACHE_EPISODES_SECONDS"],
        lambda: get_tvmaze_client().get_episodes(show_id),
        refresh=refresh,
    )


def get_tmdb_episodes(show_id, refresh=False):
    tmdb_id = tmdb_id_from_show_id(show_id)

    def load_episodes():
        raw_show = get_tmdb_show(show_id, refresh=refresh)
        if not raw_show:
            return []
        show = normalize_tmdb_show(raw_show)
        episodes = []
        for season in raw_show.get("seasons") or []:
            season_number = season.get("season_number")
            if season_number is None:
                continue
            raw_season = get_tmdb_season(tmdb_id, season_number, refresh=refresh)
            for raw_episode in raw_season.get("episodes") or []:
                episodes.append(normalize_tmdb_episode(raw_episode, show))
        return episodes

    return cached_json(
        f"episodes:{show_id}",
        current_app.config["TVMAZE_CACHE_EPISODES_SECONDS"],
        load_episodes,
        refresh=refresh,
    )


def get_tmdb_season(tmdb_id, season_number, refresh=False):
    return cached_json(
        f"tmdb:season:{tmdb_id}:{season_number}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().get_season(tmdb_id, season_number),
        refresh=refresh,
    )


def get_show_episodes_cached(show_id):
    return store.cache_get(f"episodes:{show_id}", allow_expired=True) or []


def get_shows_page(page, refresh=False):
    return cached_json(
        f"shows-page:{page}",
        current_app.config["TVMAZE_CACHE_CATALOG_SECONDS"],
        lambda: get_tvmaze_client().get_shows_page(page),
        refresh=refresh,
    )


def get_recommendation_catalog():
    shows = []
    last_page = get_last_catalog_page()
    page_count = min(
        current_app.config["TVMAZE_RECOMMENDATION_PAGES"],
        current_app.config["TVMAZE_RECOMMENDATION_RECENT_PAGES"],
    )
    first_page = max(0, last_page - page_count + 1)

    for page in range(last_page, first_page - 1, -1):
        page_items = get_shows_page(page)
        if not page_items:
            continue
        shows.extend(page_items)
    return shows


def get_tmdb_profile_candidates(show_states):
    if not is_tmdb_enabled():
        return []

    candidates = []
    candidates_by_id = {}

    def add_many(raw_items, source_label=None, source=None, relation=None):
        for raw_item in raw_items:
            if not raw_item.get("id"):
                continue
            show = normalize_tmdb_show(raw_item)
            existing = candidates_by_id.get(show["id"])
            if existing:
                show = existing
            else:
                candidates_by_id[show["id"]] = show
                candidates.append(show)

            if source_label:
                show["source_label"] = source_label
            if source:
                profile_source = {
                    "id": source["id"],
                    "name": source["name"],
                    "relation": relation or "recommendation",
                }
                sources = show.setdefault("profile_sources", [])
                if profile_source["id"] not in {item["id"] for item in sources}:
                    sources.append(profile_source)

    try:
        trending = cached_json(
            "tmdb:trending:tv:week",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_trending_tv("week"),
        )
        add_many(trending, source_label="TMDb tendencias")
    except TMDbError:
        pass

    source_shows = [
        show for show in show_states
        if show.get("watched_count") or show.get("completed")
    ][:5]
    for source in source_shows:
        tmdb_id = source.get("tmdb_id")
        if not tmdb_id:
            tmdb_id = find_tmdb_id_for_show(source)
        if not tmdb_id:
            continue

        try:
            recommended = cached_json(
                f"tmdb:recommendations:{tmdb_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda show_id=tmdb_id: get_tmdb_client().get_recommendations(show_id),
            )
            similar = cached_json(
                f"tmdb:similar:{tmdb_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda show_id=tmdb_id: get_tmdb_client().get_similar(show_id),
            )
        except TMDbError:
            continue

        add_many(
            recommended,
            source_label=f"TMDb por {source['name']}",
            source=source,
            relation="recommendation",
        )
        add_many(
            similar,
            source_label=f"TMDb similares a {source['name']}",
            source=source,
            relation="similar",
        )

    return candidates


def find_tmdb_id_for_show(show):
    show_id = show.get("id")
    if show_id and is_tmdb_show_id(show_id):
        return tmdb_id_from_show_id(show_id)

    query = show.get("original_name") or show.get("name")
    if not query:
        return None

    cache_key = f"tmdb:resolve:{query.lower()}:{(show.get('premiered') or '')[:4]}"
    try:
        results = cached_json(
            cache_key,
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().search_tv(query),
        )
    except TMDbError:
        return None

    wanted_year = (show.get("premiered") or "")[:4]
    for result in results:
        result_year = (result.get("first_air_date") or "")[:4]
        if not wanted_year or result_year == wanted_year:
            return result.get("id")
    return results[0].get("id") if results else None


def get_last_catalog_page():
    return cached_json(
        "shows-last-page",
        current_app.config["TVMAZE_CACHE_CATALOG_SECONDS"],
        discover_last_catalog_page,
    )


def discover_last_catalog_page():
    low = 0
    high = 64

    while get_shows_page(high):
        low = high
        high *= 2

    while low + 1 < high:
        middle = (low + high) // 2
        if get_shows_page(middle):
            low = middle
        else:
            high = middle

    return low
