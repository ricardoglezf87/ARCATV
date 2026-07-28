import hashlib
from datetime import date
from pathlib import Path

from flask import Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import db as store
from .recommendations import rank_recommendations
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
            if not show_finalized and is_show_finalized(show):
                continue

            episodes = get_show_episodes_cached(show["id"])

            watched_ids = store.get_watched_ids(show["id"])
            latest_watched_at = store.get_latest_watched_at(show["id"])
            shows.append(build_show_state(show, episodes, watched_ids, latest_watched_at))

        shows = sort_dashboard_shows(shows)
        all_shows = store.list_shows()
        hidden_finalized_count = sum(1 for show in all_shows if is_show_finalized(show))

        upcoming = sort_upcoming(
            episode
            for show in shows
            for episode in show["upcoming_episodes"]
        )

        totals = {
            "shows": len(shows),
            "all_shows": len(all_shows),
            "hidden_finalized": hidden_finalized_count if not show_finalized else 0,
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
                show_data, akas = get_show_with_akas(saved_show["id"], refresh=True)
                if show_data:
                    store.upsert_show(normalize_show_for_display(show_data, akas=akas))
                get_show_episodes(saved_show["id"], refresh=True)
                updated += 1
            except TVMazeError:
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
            try:
                raw_results = cached_json(
                    f"search:{query.lower()}",
                    current_app.config["TVMAZE_CACHE_SEARCH_SECONDS"],
                    lambda: get_tvmaze_client().search_shows(query),
                )
                results = [
                    enhance_show_summary(
                        normalize_search_result(
                            item,
                            saved_ids,
                            akas=safe_get_show_akas(item["show"]["id"]),
                        )
                    )
                    for item in raw_results
                ]
                if selected_genre:
                    results = [
                        show for show in results
                        if selected_genre in (show.get("genres") or [])
                    ]
            except TVMazeError as exc:
                error = str(exc)

        return render_template(
            "search.html",
            query=query,
            results=results,
            error=error,
            genres=GENRE_FILTER_OPTIONS,
            selected_genre=selected_genre,
        )

    @app.post("/series/<int:show_id>/add")
    def add_show(show_id):
        try:
            show_data, akas = get_show_with_akas(show_id, refresh=True)
        except TVMazeError as exc:
            flash(f"No se pudo añadir la serie: {exc}", "error")
            return redirect_to_next(url_for("search", q=request.form.get("q", "")))

        if not show_data:
            abort(404)

        show = normalize_show_for_display(show_data, akas=akas)
        store.upsert_show(show)

        try:
            get_show_episodes(show_id, refresh=True)
        except TVMazeError:
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
        except TVMazeError:
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
            show_data, akas = get_show_with_akas(show_id, refresh=True)
            episodes = get_show_episodes(show_id, refresh=True)
        except TVMazeError as exc:
            flash(f"No se pudo actualizar {show['name']}: {exc}", "error")
        else:
            if not show_data:
                flash(f"No se encontró {show['name']} en TVmaze.", "error")
                return redirect(url_for("show_detail", show_id=show_id))

            store.upsert_show(normalize_show_for_display(show_data, akas=akas))
            flash(f"{show_data['name']} se actualizó con {len(episodes)} episodios.", "success")

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
        except TVMazeError as exc:
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
        except TVMazeError as exc:
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
            if not show_finalized and is_show_finalized(show):
                continue

            episodes = get_show_episodes_cached(show["id"])

            state = build_show_state(
                show,
                episodes,
                store.get_watched_ids(show["id"]),
                store.get_latest_watched_at(show["id"]),
            )
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
            except TVMazeError:
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

        enriched_recommendations = [
            enhance_show_summary(recommendation)
            for recommendation in recommendations
        ]

        return render_template(
            "recommendations.html",
            recommendations=enriched_recommendations,
            genres=sorted(set(genres).union({"Telenovela"})),
            selected_genre=selected_genre,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            current_year=current_year,
            sync_errors=sync_errors,
            has_profile=bool(show_states),
        )


def get_tvmaze_client():
    injected = current_app.config.get("TVMAZE_CLIENT")
    if injected:
        return injected

    if "tvmaze_client" not in g:
        g.tvmaze_client = TVMazeClient(base_url=current_app.config["TVMAZE_BASE_URL"])
    return g.tvmaze_client


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
    except TVMazeError:
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


def enhance_show_summary(show):
    if show.get("summary"):
        show["summary"] = translate_text_to_spanish(show["summary"], f"show:{show['id']}:summary")
    return show


def normalize_show_for_display(show_data, akas=None):
    return enhance_show_summary(normalize_show(show_data, akas=akas))


def enhance_episode_summary(episode):
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


def get_show(show_id, refresh=False):
    return cached_json(
        f"show:{show_id}",
        current_app.config["TVMAZE_CACHE_SHOW_SECONDS"],
        lambda: get_tvmaze_client().get_show(show_id),
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
    return cached_json(
        f"episodes:{show_id}",
        current_app.config["TVMAZE_CACHE_EPISODES_SECONDS"],
        lambda: get_tvmaze_client().get_episodes(show_id),
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
