from datetime import date
from pathlib import Path

from flask import Flask, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import db as store
from .tvmaze import TVMazeClient, TVMazeError
from .utils import (
    build_episode_groups,
    build_show_state,
    episode_code,
    format_air_datetime,
    format_date,
    normalize_episode,
    normalize_search_result,
    normalize_show,
    sort_upcoming,
)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=str(Path(app.instance_path) / "arcatv.sqlite"),
        TVMAZE_BASE_URL="https://api.tvmaze.com",
        TVMAZE_CACHE_SEARCH_SECONDS=15 * 60,
        TVMAZE_CACHE_SHOW_SECONDS=60 * 60,
        TVMAZE_CACHE_EPISODES_SECONDS=6 * 60 * 60,
    )

    if test_config:
        app.config.update(test_config)

    store.init_app(app)
    register_template_helpers(app)
    register_routes(app)
    return app


def register_template_helpers(app):
    app.template_filter("episode_code")(episode_code)
    app.template_filter("format_date")(format_date)
    app.template_filter("format_air_datetime")(format_air_datetime)


def register_routes(app):
    @app.get("/")
    def dashboard():
        shows = []
        sync_errors = []

        for show in store.list_shows():
            try:
                episodes = get_show_episodes(show["id"])
            except TVMazeError:
                episodes = []
                sync_errors.append(show["name"])

            watched_ids = store.get_watched_ids(show["id"])
            shows.append(build_show_state(show, episodes, watched_ids))

        upcoming = sort_upcoming(
            episode
            for show in shows
            for episode in show["upcoming_episodes"]
        )

        totals = {
            "shows": len(shows),
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
        )

    @app.get("/buscar")
    def search():
        query = request.args.get("q", "").strip()
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
                results = [normalize_search_result(item, saved_ids) for item in raw_results]
            except TVMazeError as exc:
                error = str(exc)

        return render_template("search.html", query=query, results=results, error=error)

    @app.post("/series/<int:show_id>/add")
    def add_show(show_id):
        try:
            show_data = get_show(show_id, refresh=True)
        except TVMazeError as exc:
            flash(f"No se pudo añadir la serie: {exc}", "error")
            return redirect(url_for("search", q=request.form.get("q", "")))

        if not show_data:
            abort(404)

        show = normalize_show(show_data)
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

        sync_error = None
        try:
            episodes = get_show_episodes(show_id)
        except TVMazeError as exc:
            episodes = []
            sync_error = str(exc)

        watched_ids = store.get_watched_ids(show_id)
        state = build_show_state(show, episodes, watched_ids)
        episode_groups = build_episode_groups(state["episodes"])

        return render_template(
            "show.html",
            show=state,
            episode_groups=episode_groups,
            sync_error=sync_error,
        )

    @app.post("/series/<int:show_id>/refresh")
    def refresh_show(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            show_data = get_show(show_id, refresh=True)
            episodes = get_show_episodes(show_id, refresh=True)
        except TVMazeError as exc:
            flash(f"No se pudo actualizar {show['name']}: {exc}", "error")
        else:
            if not show_data:
                flash(f"No se encontró {show['name']} en TVmaze.", "error")
                return redirect(url_for("show_detail", show_id=show_id))

            store.upsert_show(normalize_show(show_data))
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
        return redirect(url_for("show_detail", show_id=show_id))

    @app.post("/series/<int:show_id>/clear")
    def clear_show_progress(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        store.clear_show_progress(show_id)
        flash(f"Progreso reiniciado para {show['name']}.", "success")
        return redirect(url_for("show_detail", show_id=show_id))

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
        return redirect(url_for("show_detail", show_id=show_id))

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

        next_url = request.form.get("next") or url_for("show_detail", show_id=show_id)
        return redirect(next_url)

    @app.get("/proximos")
    def upcoming():
        upcoming_episodes = []
        sync_errors = []

        for show in store.list_shows():
            try:
                episodes = get_show_episodes(show["id"])
            except TVMazeError:
                sync_errors.append(show["name"])
                continue

            state = build_show_state(show, episodes, store.get_watched_ids(show["id"]))
            upcoming_episodes.extend(state["upcoming_episodes"])

        return render_template(
            "upcoming.html",
            upcoming=sort_upcoming(upcoming_episodes),
            sync_errors=sync_errors,
        )


def get_tvmaze_client():
    injected = current_app.config.get("TVMAZE_CLIENT")
    if injected:
        return injected

    if "tvmaze_client" not in g:
        g.tvmaze_client = TVMazeClient(base_url=current_app.config["TVMAZE_BASE_URL"])
    return g.tvmaze_client


def cached_json(key, ttl_seconds, producer, refresh=False):
    if refresh:
        store.cache_delete(key)

    cached = store.cache_get(key)
    if cached is not None:
        return cached

    payload = producer()
    store.cache_set(key, payload, ttl_seconds)
    return payload


def get_show(show_id, refresh=False):
    return cached_json(
        f"show:{show_id}",
        current_app.config["TVMAZE_CACHE_SHOW_SECONDS"],
        lambda: get_tvmaze_client().get_show(show_id),
        refresh=refresh,
    )


def get_show_episodes(show_id, refresh=False):
    return cached_json(
        f"episodes:{show_id}",
        current_app.config["TVMAZE_CACHE_EPISODES_SECONDS"],
        lambda: get_tvmaze_client().get_episodes(show_id),
        refresh=refresh,
    )
