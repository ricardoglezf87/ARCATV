import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import requests
from flask import Flask, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_from_directory, url_for

from . import db as store
from .anilist import (
    AniListClient,
    AniListError,
    anilist_id_from_manga_id,
    is_anilist_manga_id,
    normalize_anilist_manga,
    normalize_anilist_staff_member,
)
from .comick import (
    COMICK_COVER_BASE_URL,
    ComicKClient,
    ComicKError,
    dedupe_chapters_by_number as dedupe_comick_chapters_by_number,
    is_comick_manga_id,
    normalize_comick_chapter,
    normalize_comick_manga,
    parse_number as parse_comick_number,
)
from .mangadex import (
    MANGADEX_COVER_BASE_URL,
    MangaDexClient,
    MangaDexError,
    dedupe_chapters_by_number,
    normalize_mangadex_author,
    normalize_mangadex_chapter,
    normalize_mangadex_manga,
    parse_number as parse_mangadex_number,
)
from .manga_downloads import (
    MangaDownloadError,
    MissingMangaDownloadDependency,
    chapter_images,
    clear_chapter_directory,
    download_manga_chapter,
    read_vignette_map,
)
from .manga_oni import MangaOniClient, MangaOniError, default_manga_url, normalize_manga_url
from .recommendations import (
    add_recommendation_reasons,
    rank_recommendations,
    top_profile_genres,
    top_profile_platforms,
)
from .tmdb import (
    TMDbClient,
    TMDbError,
    is_tmdb_movie_id,
    is_tmdb_show_id,
    normalize_tmdb_cast_member,
    normalize_tmdb_episode,
    normalize_tmdb_movie,
    normalize_tmdb_person,
    normalize_tmdb_show,
    tmdb_id_from_movie_id,
    tmdb_id_from_show_id,
)
from .utils import (
    build_episode_groups,
    build_show_state,
    episode_code,
    episode_modal_payload,
    format_air_datetime,
    format_date,
    normalize_episode,
    sort_dashboard_shows,
    sort_upcoming,
)


GENRE_FILTER_OPTIONS = [
    "Acción",
    "Aventura",
    "Animación",
    "Anime",
    "Bélica",
    "Ciencia ficción",
    "Comedia",
    "Crimen",
    "Documental",
    "Drama",
    "Familiar",
    "Fantasía",
    "Historia",
    "Misterio",
    "Música",
    "Pelicula de TV",
    "Romance",
    "Sobrenatural",
    "Suspense",
    "Telenovela",
    "Terror",
]

MANGA_GENRE_FILTER_OPTIONS = [
    "Acción",
    "Aventura",
    "Comedia",
    "Ciencia ficción",
    "Drama",
    "Fantasía",
    "Misterio",
    "Psicológico",
    "Romance",
    "Seinen",
    "Shoujo",
    "Shounen",
    "Slice of Life",
    "Sobrenatural",
    "Suspense",
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


def local_config_int(name, default):
    value = local_config_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=str(Path(app.instance_path) / "arcatv.sqlite"),
        RECOMMENDATION_LIMIT=24,
        ACTOR_RECOMMENDATION_SOURCE_LIMIT=4,
        ACTOR_RECOMMENDATION_CAST_PER_SHOW=3,
        AUTHOR_RECOMMENDATION_SOURCE_LIMIT=4,
        AUTHOR_RECOMMENDATION_STAFF_PER_MANGA=3,
        ANILIST_BASE_URL="https://graphql.anilist.co",
        ANILIST_CACHE_SECONDS=24 * 60 * 60,
        ANILIST_ENABLED=local_config_bool("ANILIST_ENABLED", True),
        COMICK_BASE_URL="https://api.comick.dev",
        COMICK_CACHE_SECONDS=6 * 60 * 60,
        COMICK_CHAPTER_CACHE_SECONDS=30 * 60,
        COMICK_CHAPTER_FETCH_LIMIT=5000,
        COMICK_CHAPTER_PAGE_SIZE=local_config_int("COMICK_CHAPTER_PAGE_SIZE", 100),
        COMICK_ENABLED=local_config_bool("COMICK_ENABLED", True),
        COMICK_LANGUAGES=["es", "en"],
        MANGADEX_BASE_URL="https://api.mangadex.org",
        MANGADEX_CACHE_SECONDS=6 * 60 * 60,
        MANGADEX_CHAPTER_CACHE_SECONDS=30 * 60,
        MANGADEX_CHAPTER_FETCH_LIMIT=500,
        MANGADEX_ENABLED=local_config_bool("MANGADEX_ENABLED", True),
        MANGADEX_LANGUAGES=["es", "es-la", "en"],
        MANGA_DOWNLOAD_ROOT=str(Path(app.instance_path) / "manga_downloads"),
        MANGA_BROWSER_VERSION=local_config_value("MANGA_BROWSER_VERSION"),
        MANGA_ONI_BASE_URL="https://manga-oni.com",
        MANGA_ONI_CACHE_SECONDS=30 * 60,
        MANGA_ONI_ENABLED=local_config_bool("MANGA_ONI_ENABLED", True),
        TMDB_API_KEY=local_config_value("TMDB_API_KEY"),
        TMDB_BEARER_TOKEN=local_config_value("TMDB_BEARER_TOKEN"),
        TMDB_BASE_URL="https://api.themoviedb.org/3",
        TMDB_CACHE_SECONDS=24 * 60 * 60,
        TMDB_CACHE_EPISODES_SECONDS=6 * 60 * 60,
        TMDB_VERIFY_SSL=local_config_bool("TMDB_VERIFY_SSL", True),
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
            except TMDbError:
                failed.append(saved_show["name"])

        if updated:
            scope = "todas las series" if include_finalized else "las series en emisión"
            flash(f"Se actualizaron {updated} de {scope}.", "success")
        if failed:
            flash(f"No se pudieron actualizar: {', '.join(failed)}.", "warning")
        if not updated and not failed:
            flash("No había series para actualizar con ese filtro.", "warning")

        return redirect_to_next(url_for("dashboard"))

    @app.get("/buscar/global")
    def global_search():
        query = request.args.get("q", "").strip()
        media_type = request.args.get("tipo", "series")
        if media_type == "peliculas":
            return redirect(url_for("movie_search", q=query))
        if media_type == "mangas":
            return redirect(url_for("manga_search", q=query))
        return redirect(url_for("search", q=query))

    @app.get("/peliculas")
    def movies_dashboard():
        status_filter = request.args.get("estado", "pendientes")
        if status_filter not in {"todas", "vistas", "pendientes"}:
            status_filter = "pendientes"

        all_movies = [
            build_movie_state(movie, store.get_movie_watched_at(movie["id"]))
            for movie in store.list_movies()
        ]
        totals = {
            "movies": len(all_movies),
            "watched": sum(1 for movie in all_movies if movie["watched"]),
            "pending": sum(1 for movie in all_movies if not movie["watched"]),
        }

        movies = all_movies
        if status_filter == "vistas":
            movies = [movie for movie in movies if movie["watched"]]
        elif status_filter == "pendientes":
            movies = [movie for movie in movies if not movie["watched"]]

        return render_template(
            "movies.html",
            movies=sort_movies(movies),
            totals=totals,
            status_filter=status_filter,
        )

    @app.get("/buscar/peliculas")
    def movie_search():
        query = request.args.get("q", "").strip()
        selected_genre = request.args.get("genero", "").strip()
        results = []
        error = None
        saved_ids = store.get_movie_ids()

        if query:
            if is_tmdb_enabled():
                results = search_tmdb_movie_results(query, saved_ids)
            else:
                error = "Configura TMDB_API_KEY o TMDB_BEARER_TOKEN para buscar en el catalogo."

            if selected_genre:
                results = [
                    movie for movie in results
                    if selected_genre in (movie.get("genres") or [])
                ]

        return render_template(
            "movie_search.html",
            query=query,
            results=results,
            error=error,
            genres=GENRE_FILTER_OPTIONS,
            selected_genre=selected_genre,
            tmdb_enabled=is_tmdb_enabled(),
        )

    @app.post("/peliculas/<int:movie_id>/add")
    def add_movie(movie_id):
        try:
            movie = get_movie_for_storage(movie_id, refresh=True)
        except TMDbError as exc:
            flash(f"No se pudo anadir la pelicula: {exc}", "error")
            return redirect_to_next(url_for("movie_search", q=request.form.get("q", "")))

        if not movie:
            abort(404)

        store.upsert_movie(movie)
        store.restore_movie_recommendation(movie["id"])
        if request.form.get("watched") == "1":
            store.mark_movie_watched(movie["id"])

        flash(f"{movie['name']} se anadio a tus peliculas.", "success")
        return redirect(url_for("movie_detail", movie_id=movie["id"]))

    @app.get("/peliculas/<int:movie_id>")
    def movie_detail(movie_id):
        movie = store.get_movie(movie_id)
        if not movie:
            abort(404)

        state = build_movie_state(movie, store.get_movie_watched_at(movie_id))
        cast = get_movie_cast(movie_id)
        return render_template(
            "movie.html",
            movie=state,
            cast=cast,
        )

    @app.post("/peliculas/<int:movie_id>/refresh")
    def refresh_movie(movie_id):
        movie = store.get_movie(movie_id)
        if not movie:
            abort(404)

        try:
            updated_movie = get_movie_for_storage(movie_id, refresh=True)
        except TMDbError as exc:
            flash(f"No se pudo actualizar {movie['name']}: {exc}", "error")
        else:
            if not updated_movie:
                flash(f"No se encontro {movie['name']} en TMDb.", "error")
                return redirect(url_for("movie_detail", movie_id=movie_id))

            store.upsert_movie(updated_movie)
            flash(f"{updated_movie['name']} se actualizo.", "success")

        return redirect(url_for("movie_detail", movie_id=movie_id))

    @app.post("/peliculas/<int:movie_id>/visto")
    def set_movie_watched(movie_id):
        movie = store.get_movie(movie_id)
        if not movie:
            abort(404)

        if request.form.get("watched") == "1":
            store.mark_movie_watched(movie_id)
            flash(f"{movie['name']} marcada como vista.", "success")
        else:
            store.unmark_movie_watched(movie_id)
            flash(f"{movie['name']} queda como pendiente.", "success")

        return redirect_to_next(url_for("movie_detail", movie_id=movie_id))

    @app.post("/peliculas/<int:movie_id>/remove")
    def remove_movie(movie_id):
        movie = store.get_movie(movie_id)
        if not movie:
            abort(404)

        store.remove_movie(movie_id)
        flash(f"{movie['name']} salio de tus peliculas.", "success")
        return redirect(url_for("movies_dashboard"))

    @app.get("/mangas")
    def mangas_dashboard():
        status_filter = request.args.get("estado", "pendientes")
        if status_filter not in {"todas", "leidos", "pendientes"}:
            status_filter = "pendientes"

        all_mangas = [
            build_manga_state(
                manga,
                store.get_manga_progress(manga["id"]),
                store.list_manga_chapter_read_overrides(manga["id"]),
            )
            for manga in store.list_mangas()
        ]
        totals = {
            "mangas": len(all_mangas),
            "read": sum(1 for manga in all_mangas if manga["read"]),
            "in_progress": sum(1 for manga in all_mangas if manga["in_progress"]),
            "pending": sum(1 for manga in all_mangas if not manga["read"]),
        }

        mangas = all_mangas
        if status_filter == "leidos":
            mangas = [manga for manga in mangas if manga["read"]]
        elif status_filter == "pendientes":
            mangas = [manga for manga in mangas if not manga["read"]]

        return render_template(
            "mangas.html",
            mangas=sort_mangas(mangas),
            totals=totals,
            status_filter=status_filter,
        )

    @app.post("/mangas/actualizar")
    def refresh_mangas_library():
        updated = 0
        failed = []

        for saved_manga in store.list_mangas():
            try:
                refreshed_manga = refresh_manga_from_source(saved_manga)
            except (AniListError, ComicKError, MangaDexError):
                failed.append(saved_manga["name"])
                continue
            if refreshed_manga:
                updated += 1
            else:
                failed.append(saved_manga["name"])

        if updated:
            flash(f"Se actualizaron {updated} mangas y sus capitulos.", "success")
        if failed:
            flash(f"No se pudieron actualizar: {', '.join(failed)}.", "warning")
        if not updated and not failed:
            flash("No habia mangas guardados para actualizar.", "warning")

        return redirect_to_next(url_for("mangas_dashboard"))

    @app.post("/mangas/imagenes-vistas/borrar")
    def cleanup_all_read_manga_downloads():
        deleted = delete_read_manga_downloads()
        if deleted:
            flash(f"Se borraron las imagenes de {deleted} capitulos ya leidos.", "success")
        else:
            flash("No habia imagenes de capitulos leidos para borrar.", "warning")
        return redirect_to_next(url_for("mangas_dashboard"))

    @app.get("/buscar/mangas")
    def manga_search():
        query = request.args.get("q", "").strip()
        selected_genre = request.args.get("genero", "").strip()
        results = []
        error = None
        saved_ids = store.get_manga_ids()
        saved_mangadex_ids = store.get_manga_mangadex_ids()
        saved_comick_id_map = store.get_manga_comick_id_map()

        if query:
            if is_comick_enabled():
                try:
                    results = search_comick_manga_results(query, saved_ids, saved_comick_id_map)
                except ComicKError as exc:
                    error = f"No se pudo buscar en ComicK: {exc}"

            if not results and is_mangadex_enabled():
                try:
                    results = search_mangadex_manga_results(query, saved_ids, saved_mangadex_ids)
                except MangaDexError as exc:
                    error = f"No se pudo buscar en MangaDex: {exc}"
            elif not is_comick_enabled() and not is_mangadex_enabled():
                error = "No hay una fuente de mangas configurada."

            if selected_genre:
                results = [
                    manga for manga in results
                    if selected_genre in (manga.get("genres") or [])
                ]

        return render_template(
            "manga_search.html",
            query=query,
            results=results,
            error=error,
            genres=MANGA_GENRE_FILTER_OPTIONS,
            selected_genre=selected_genre,
            anilist_enabled=is_anilist_enabled(),
            comick_enabled=is_comick_enabled(),
            mangadex_enabled=is_mangadex_enabled(),
        )

    @app.get("/mangas/portadas/comick/<path:file_name>")
    def comick_cover(file_name):
        if unsafe_remote_file_name(file_name):
            abort(404)
        return proxy_image_response(f"{COMICK_COVER_BASE_URL}/{file_name}")

    @app.get("/mangas/portadas/<mangadex_id>/<path:file_name>")
    def mangadex_cover(mangadex_id, file_name):
        if unsafe_remote_file_name(file_name) or not re.fullmatch(r"[0-9a-f-]{36}", mangadex_id, flags=re.IGNORECASE):
            abort(404)

        cover_url = f"{MANGADEX_COVER_BASE_URL}/{mangadex_id}/{file_name}.512.jpg"
        try:
            return proxy_image_response(cover_url)
        except MangaDexError:
            return proxy_image_response(f"{MANGADEX_COVER_BASE_URL}/{mangadex_id}/{file_name}")

    @app.post("/mangas/add")
    def add_mangadex_manga():
        source_id = request.form.get("source_id", "").strip()
        if not source_id:
            abort(400)
        source = request.form.get("source", "").strip().casefold()
        if not source:
            source = "mangadex" if re.fullmatch(r"[0-9a-f-]{36}", source_id, flags=re.IGNORECASE) else "comick"

        try:
            if source == "comick":
                manga = get_comick_manga_for_storage(source_id, refresh=True)
            else:
                manga = get_mangadex_manga_for_storage(source_id, refresh=True)
        except (ComicKError, MangaDexError) as exc:
            flash(f"No se pudo anadir el manga: {exc}", "error")
            return redirect_to_next(url_for("manga_search", q=request.form.get("q", "")))

        if not manga:
            abort(404)

        existing_manga = (
            store.get_manga_by_comick_id(source_id)
            if source == "comick"
            else store.get_manga_by_mangadex_id(source_id)
        )
        if existing_manga:
            manga = merge_manga_storage(existing_manga, manga)

        store.upsert_manga(manga)
        store.restore_manga_recommendation(manga["id"])

        flash(f"{manga['name']} se anadio a tus mangas.", "success")
        return redirect(url_for("manga_detail", manga_id=manga["id"]))

    @app.post("/mangas/<int:manga_id>/add")
    def add_manga(manga_id):
        source = request.form.get("source", "").strip().casefold()
        source_id = request.form.get("source_id", "").strip()
        try:
            if source == "mangadex" and source_id:
                manga = get_mangadex_manga_for_storage(source_id, refresh=True)
            elif source == "comick" and source_id:
                manga = get_comick_manga_for_storage(source_id, refresh=True)
            elif source == "anilist" and source_id.isdigit():
                raw_manga = get_anilist_manga_from_anilist_id(int(source_id), refresh=True)
                manga = normalize_anilist_manga(raw_manga) if raw_manga else None
            else:
                manga = get_manga_for_storage(manga_id, refresh=True)
        except (AniListError, ComicKError, MangaDexError) as exc:
            flash(f"No se pudo anadir el manga: {exc}", "error")
            return redirect_to_next(url_for("manga_search", q=request.form.get("q", "")))

        if not manga:
            abort(404)

        existing_manga = find_saved_duplicate_manga(manga)
        if existing_manga:
            store.restore_manga_recommendation(existing_manga["id"])
            flash(f"{existing_manga['name']} ya estaba en tus mangas.", "success")
            return redirect(url_for("manga_detail", manga_id=existing_manga["id"]))

        store.upsert_manga(manga)
        store.restore_manga_recommendation(manga["id"])
        flash(f"{manga['name']} se anadio a tus mangas.", "success")
        return redirect(url_for("manga_detail", manga_id=manga["id"]))

    @app.get("/mangas/<int:manga_id>")
    def manga_detail(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        manga = ensure_comick_link_for_manga(manga)
        if manga["id"] != manga_id:
            return redirect(url_for("manga_detail", manga_id=manga["id"]))

        if manga.get("comick_id"):
            try:
                updated_manga = get_comick_manga_for_storage(manga["comick_id"], refresh=True)
            except ComicKError:
                updated_manga = None
            if updated_manga:
                store.upsert_manga(merge_manga_storage(manga, updated_manga))
                manga = store.get_manga(manga_id) or manga
        elif manga.get("mangadex_id"):
            try:
                updated_manga = get_mangadex_manga_for_storage(manga["mangadex_id"], refresh=True)
            except MangaDexError:
                updated_manga = None
            if updated_manga:
                store.upsert_manga(merge_manga_storage(manga, updated_manga))
                manga = store.get_manga(manga_id) or manga

        state = build_manga_state(
            manga,
            store.get_manga_progress(manga_id),
            store.list_manga_chapter_read_overrides(manga_id),
        )
        chapters, chapter_error = preferred_manga_chapters(manga_id, manga)
        if chapter_error:
            flash(f"No se pudieron cargar los capitulos ahora: {chapter_error}", "warning")
        download_base_url = manga_download_base_url(manga)
        source_manga_oni_url = manga_oni_url(manga)
        chapters = attach_manga_downloads_to_chapters(manga_id, chapters, download_base_url)
        state = enrich_manga_state_with_chapters(state, chapters)
        show_read_chapters = request.args.get("leidos") == "1"
        visible_chapters = chapters
        if not show_read_chapters:
            visible_chapters = [chapter for chapter in chapters if not chapter.get("read")]

        authors = get_manga_authors(manga_id)
        return render_template(
            "manga.html",
            manga=state,
            authors=authors,
            chapters=visible_chapters,
            total_chapters=len(chapters),
            visible_chapter_count=len(visible_chapters),
            downloaded_chapter_count=sum(1 for chapter in chapters if chapter.get("download")),
            show_read_chapters=show_read_chapters,
            download_base_url=download_base_url,
            manga_oni_url=source_manga_oni_url,
            chapter_error=chapter_error,
        )

    @app.post("/mangas/<int:manga_id>/refresh")
    def refresh_manga(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        try:
            updated_manga = refresh_manga_from_source(manga)
        except (AniListError, ComicKError, MangaDexError) as exc:
            flash(f"No se pudo actualizar {manga['name']}: {exc}", "error")
        else:
            if not updated_manga:
                flash(f"No se encontro {manga['name']} en la fuente de mangas.", "error")
                return redirect(url_for("manga_detail", manga_id=manga_id))

            flash(f"{updated_manga['name']} se actualizo.", "success")

        return redirect(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/imagenes-vistas/borrar")
    def cleanup_manga_read_downloads(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        deleted = delete_read_manga_downloads(manga_id)
        if deleted:
            flash(f"Se borraron las imagenes de {deleted} capitulos leidos de {manga['name']}.", "success")
        else:
            flash(f"No habia imagenes leidas para borrar en {manga['name']}.", "warning")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/descarga-base")
    def set_manga_download_base(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        base_url = request.form.get("base_url", "").strip()
        store.set_manga_download_base_url(manga_id, base_url)
        if base_url:
            flash(f"URL base de descarga guardada para {manga['name']}.", "success")
        else:
            flash(f"URL base de descarga borrada para {manga['name']}.", "success")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/manga-oni")
    def set_manga_oni_source(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        source_url = request.form.get("manga_oni_url", "").strip()
        normalized_url = normalize_manga_url(source_url) if source_url else ""
        if source_url and not normalized_url:
            flash("La URL de Manga Oni no es valida.", "error")
            return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

        store.set_manga_oni_url(manga_id, normalized_url)
        flash(f"Fuente de Manga Oni guardada para {manga['name']}.", "success")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/capitulos/descargar")
    def download_manga_chapter_route(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        chapter = request.form.get("chapter", "").strip()
        if not chapter:
            abort(400)

        chapters, _ = preferred_manga_chapters(manga_id, manga)
        chapter_data = next(
            (item for item in chapters if manga_chapter_key(item.get("chapter")) == manga_chapter_key(chapter)),
            {"chapter": chapter},
        )
        download_urls = manga_chapter_download_candidates(
            chapter_data,
            manga_download_base_url(manga),
            request.form.get("download_url", ""),
        )
        if not download_urls:
            flash("Pega la URL del capitulo para descargarlo.", "error")
            return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

        try:
            result, _ = download_and_store_manga_chapter(manga_id, chapter_data, download_urls)
        except MissingMangaDownloadDependency as exc:
            flash(str(exc), "error")
        except MangaDownloadError as exc:
            flash(f"No se pudo descargar el capitulo {chapter}: {exc}", "error")
        else:
            flash(
                f"Capitulo {chapter} descargado con {result.panel_count} imagenes usando {result.strategy}.",
                "success",
            )

        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/capitulos/<chapter>/imagenes/borrar")
    def delete_manga_chapter_images(manga_id, chapter):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)
        chapter_key = manga_chapter_key(chapter)
        if not store.get_manga_download(manga_id, chapter_key):
            flash(f"El capitulo {chapter} ya no tiene imagenes descargadas.", "warning")
            return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

        delete_manga_download_files(manga_id, chapter_key)
        store.delete_manga_download(manga_id, chapter_key)
        flash(f"Imagenes descargadas del capitulo {chapter} borradas.", "success")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/capitulos/descargar-hasta")
    def download_manga_chapters_until(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        target = parse_chapter_number(request.form.get("chapter"))
        if target is None:
            abort(400)

        chapters, chapter_error = preferred_manga_chapters(manga_id, manga)
        if chapter_error and not chapters:
            flash(f"No se pudieron cargar los capitulos: {chapter_error}", "error")
            return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

        downloads = {item["chapter_key"] for item in store.list_manga_downloads(manga_id)}
        pending = [
            chapter for chapter in chapters
            if chapter.get("chapter_number") is not None
            and chapter["chapter_number"] <= target
            and not chapter.get("read")
            and manga_chapter_key(chapter.get("chapter")) not in downloads
        ]
        pending.sort(key=lambda chapter: chapter["chapter_number"])
        if not pending:
            flash("No hay capitulos pendientes sin descargar hasta ese punto.", "warning")
            return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

        downloaded = 0
        failures = []
        download_base_url = manga_download_base_url(manga)
        for chapter in pending:
            urls = manga_chapter_download_candidates(chapter, download_base_url)
            try:
                download_and_store_manga_chapter(manga_id, chapter, urls)
            except (MissingMangaDownloadDependency, MangaDownloadError) as exc:
                failures.append(f"{chapter['chapter']}: {exc}")
            else:
                downloaded += 1

        if downloaded:
            flash(f"Se descargaron {downloaded} capitulos pendientes.", "success")
        if failures:
            preview = " / ".join(failures[:3])
            remaining = len(failures) - 3
            if remaining > 0:
                preview = f"{preview} / y {remaining} mas"
            flash(f"No se pudieron descargar {len(failures)} capitulos: {preview}", "error")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.get("/mangas/<int:manga_id>/capitulos/<chapter>/leer")
    def manga_reader(manga_id, chapter):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        download = store.get_manga_download(manga_id, chapter)
        if not download:
            flash("Ese capitulo todavia no esta descargado.", "warning")
            return redirect(url_for("manga_detail", manga_id=manga_id))

        pages = manga_reader_pages(manga_id, chapter)
        if not pages:
            flash("No se encontraron imagenes para ese capitulo.", "error")
            return redirect(url_for("manga_detail", manga_id=manga_id))

        progress = store.get_manga_reader_progress(manga_id, chapter) or {}
        current_panel = min(max(int(progress.get("current_panel") or 1), 1), len(pages))
        return render_template(
            "manga_reader.html",
            manga=manga,
            chapter=chapter,
            pages=pages,
            current_panel=current_panel,
            detail_url=url_for("manga_detail", manga_id=manga_id),
            progress_url=url_for("save_manga_reader_progress", manga_id=manga_id, chapter=chapter),
            finish_url=url_for("finish_manga_reader", manga_id=manga_id, chapter=chapter),
            is_read=is_manga_chapter_read(manga_id, chapter),
        )

    @app.get("/mangas/<int:manga_id>/capitulos/<chapter>/imagenes/<path:file_name>")
    def manga_download_file(manga_id, chapter, file_name):
        if not store.get_manga_download(manga_id, chapter):
            abort(404)
        return send_from_directory(manga_download_folder(manga_id, chapter), file_name)

    @app.post("/mangas/<int:manga_id>/capitulos/<chapter>/progreso")
    def save_manga_reader_progress(manga_id, chapter):
        if not store.get_manga(manga_id) or not store.get_manga_download(manga_id, chapter):
            abort(404)

        payload = request.get_json(silent=True) or request.form
        current_panel = payload.get("panel", 1)
        store.save_manga_reader_progress(manga_id, chapter, current_panel)
        return jsonify({"ok": True})

    @app.post("/mangas/<int:manga_id>/capitulos/<chapter>/terminar")
    def finish_manga_reader(manga_id, chapter):
        manga = store.get_manga(manga_id)
        if not manga or not store.get_manga_download(manga_id, chapter):
            abort(404)

        payload = request.get_json(silent=True) or request.form
        current_panel = payload.get("panel", 1)
        store.save_manga_reader_progress(manga_id, chapter, current_panel, finished=True)
        mark_single_manga_chapter_read(manga_id, chapter)
        return jsonify({"ok": True, "message": f"Capitulo {chapter} marcado como leido."})

    @app.post("/mangas/<int:manga_id>/capitulos/<chapter>/leido")
    def set_single_manga_chapter_read(manga_id, chapter):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        is_read = request.form.get("read") == "1"
        chapter_key = manga_chapter_key(chapter)
        store.set_manga_chapter_read_override(manga_id, chapter_key, is_read)
        if is_read:
            flash(f"Capitulo {chapter} marcado como visto.", "success")
        else:
            flash(f"Capitulo {chapter} marcado como pendiente.", "success")
        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/leido")
    def set_manga_read(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        if "chapter_read" in request.form:
            chapter_read = request.form.get("chapter_read", "").strip()
            if chapter_read:
                clear_manga_chapter_overrides_through(manga_id, chapter_read)
                store.mark_manga_chapter_read(manga_id, chapter_read)
                flash(f"Progreso de {manga['name']} actualizado al capitulo {chapter_read}.", "success")
            else:
                store.unmark_manga_read(manga_id)
                flash(f"{manga['name']} queda como pendiente.", "success")
        elif "volumes_read" in request.form:
            volumes_read = request.form.get("volumes_read", type=int) or 0
            total_volumes = manga.get("volumes") or 0
            if total_volumes:
                volumes_read = min(max(volumes_read, 0), total_volumes)
            else:
                volumes_read = max(volumes_read, 0)

            if volumes_read:
                store.mark_manga_read(manga_id, volumes_read=volumes_read)
                flash(f"Progreso de {manga['name']} actualizado.", "success")
            else:
                store.unmark_manga_read(manga_id)
                flash(f"{manga['name']} queda como pendiente.", "success")
        elif request.form.get("read") == "1":
            total_volumes = manga.get("volumes") or 1
            store.mark_manga_read(manga_id, volumes_read=total_volumes)
            flash(f"{manga['name']} marcado como leido.", "success")
        else:
            store.unmark_manga_read(manga_id)
            flash(f"{manga['name']} queda como pendiente.", "success")

        return redirect_to_next(url_for("manga_detail", manga_id=manga_id))

    @app.post("/mangas/<int:manga_id>/remove")
    def remove_manga(manga_id):
        manga = store.get_manga(manga_id)
        if not manga:
            abort(404)

        store.remove_manga(manga_id)
        flash(f"{manga['name']} salio de tus mangas.", "success")
        return redirect(url_for("mangas_dashboard"))

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
            else:
                error = "Configura TMDB_API_KEY o TMDB_BEARER_TOKEN para buscar en el catálogo."

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
        except TMDbError as exc:
            flash(f"No se pudo añadir la serie: {exc}", "error")
            return redirect_to_next(url_for("search", q=request.form.get("q", "")))

        if not show:
            abort(404)

        store.upsert_show(show)
        store.restore_recommendation(show["id"])

        try:
            get_show_episodes(show_id, refresh=True)
        except TMDbError:
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
        cast = get_show_cast(show_id)

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
            cast=cast,
        )

    @app.get("/series/<int:show_id>/episodios/<int:episode_id>")
    def episode_detail(show_id, episode_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            episodes = get_show_episodes(show_id)
        except TMDbError:
            abort(404)

        state = build_show_state(show, episodes, store.get_watched_ids(show_id))
        episode = next(
            (item for item in state["episodes"] if item["id"] == episode_id),
            None,
        )
        if not episode:
            abort(404)

        return jsonify(episode_modal_payload(episode))

    @app.post("/series/<int:show_id>/refresh")
    def refresh_show(show_id):
        show = store.get_show(show_id)
        if not show:
            abort(404)

        try:
            updated_show = get_show_for_storage(show_id, refresh=True)
            episodes = get_show_episodes(show_id, refresh=True)
        except TMDbError as exc:
            flash(f"No se pudo actualizar {show['name']}: {exc}", "error")
        else:
            if not updated_show:
                flash(f"No se encontró {show['name']} en TMDb.", "error")
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
        except TMDbError as exc:
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
        except TMDbError as exc:
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
        selected_source_ids = {
            int(value)
            for value in request.args.getlist("origen")
            if value.isdigit()
        }
        source_filter_submitted = request.args.get("fuentes") == "seleccionadas"
        include_rejected = request.args.get("rechazadas") == "1"
        actor_query = request.args.get("actor", "").strip()
        actor_id = request.args.get("actor_id", type=int)
        current_year = date.today().year
        year_from = request.args.get("desde", type=int)
        year_to = request.args.get("hasta", type=int)
        sort_mode = request.args.get("orden", "puntuacion")
        if year_from is None and "desde" not in request.args:
            year_from = current_year - 8
        if sort_mode not in {"recientes", "puntuacion"}:
            sort_mode = "puntuacion"
        sync_errors = []

        show_states = []
        for show in store.list_shows():
            try:
                episodes = get_show_episodes(show["id"])
            except TMDbError:
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

        source_options = [
            show for show in show_states
            if show.get("watched_count") or show.get("completed")
        ]
        profile_states = [
            show for show in show_states
            if not source_filter_submitted or show["id"] in selected_source_ids
        ]

        selected_actor = None
        actor_search_results = []
        raw_candidates = []
        if is_tmdb_enabled():
            raw_candidates.extend(get_tmdb_profile_candidates(profile_states))
            if actor_id or actor_query:
                selected_actor, actor_candidates, actor_search_results = (
                    get_manual_actor_recommendation_context(actor_id, actor_query)
                )
                raw_candidates.extend(actor_candidates)
            else:
                raw_candidates.extend(get_tmdb_actor_candidates(profile_states))
        else:
            sync_errors.append("TMDb no está configurado.")

        rejected_ids = store.get_rejected_recommendation_ids()
        recommendations, genres = rank_recommendations(
            profile_states,
            raw_candidates,
            store.get_show_ids(),
            selected_genre=selected_genre or None,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            limit=current_app.config["RECOMMENDATION_LIMIT"],
            rejected_ids=rejected_ids,
            include_rejected=include_rejected,
        )

        enriched_recommendations = add_recommendation_reasons(
            list(recommendations),
            profile_states,
        )
        sections = build_recommendation_sections(
            enriched_recommendations,
            profile_states,
            selected_genre=selected_genre or None,
            selected_actor=selected_actor,
        )

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
            has_profile=bool(source_options),
            tmdb_enabled=is_tmdb_enabled(),
            source_options=source_options,
            selected_source_ids=selected_source_ids,
            source_filter_submitted=source_filter_submitted,
            include_rejected=include_rejected,
            actor_query=actor_query,
            selected_actor=selected_actor,
            actor_search_results=actor_search_results,
        )

    @app.post("/recomendaciones/<int:show_id>/rechazar")
    def reject_recommendation(show_id):
        show = recommendation_from_form(show_id)
        store.reject_recommendation(show)
        flash(f"{show['name']} no volverá a aparecer en recomendaciones.", "success")
        return redirect_to_next(url_for("recommendations"))

    @app.post("/recomendaciones/<int:show_id>/restaurar")
    def restore_recommendation(show_id):
        store.restore_recommendation(show_id)
        flash("Recomendación restaurada.", "success")
        return redirect_to_next(url_for("recommendations", rechazadas=1))

    @app.get("/recomendaciones/peliculas")
    def movie_recommendations():
        selected_genre = request.args.get("genero", "").strip()
        selected_source_ids = {
            int(value)
            for value in request.args.getlist("origen")
            if value.isdigit()
        }
        source_filter_submitted = request.args.get("fuentes") == "seleccionadas"
        include_rejected = request.args.get("rechazadas") == "1"
        actor_query = request.args.get("actor", "").strip()
        actor_id = request.args.get("actor_id", type=int)
        current_year = date.today().year
        year_from = request.args.get("desde", type=int)
        year_to = request.args.get("hasta", type=int)
        sort_mode = request.args.get("orden", "puntuacion")
        if year_from is None and "desde" not in request.args:
            year_from = current_year - 12
        if sort_mode not in {"recientes", "puntuacion"}:
            sort_mode = "puntuacion"
        sync_errors = []

        movie_states = [
            build_movie_state(movie, store.get_movie_watched_at(movie["id"]))
            for movie in store.list_movies()
        ]
        source_options = [movie for movie in movie_states if movie["watched"]]
        profile_states = [
            movie for movie in movie_states
            if not source_filter_submitted or movie["id"] in selected_source_ids
        ]

        selected_actor = None
        actor_search_results = []
        raw_candidates = []
        if is_tmdb_enabled():
            raw_candidates.extend(get_tmdb_movie_profile_candidates(profile_states))
            if actor_id or actor_query:
                selected_actor, actor_candidates, actor_search_results = (
                    get_manual_actor_movie_recommendation_context(actor_id, actor_query)
                )
                raw_candidates.extend(actor_candidates)
            else:
                raw_candidates.extend(get_tmdb_movie_actor_candidates(profile_states))
        else:
            sync_errors.append("TMDb no esta configurado.")

        rejected_ids = store.get_rejected_movie_recommendation_ids()
        recommendations, genres = rank_recommendations(
            profile_states,
            raw_candidates,
            store.get_movie_ids(),
            selected_genre=selected_genre or None,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            limit=current_app.config["RECOMMENDATION_LIMIT"],
            rejected_ids=rejected_ids,
            include_rejected=include_rejected,
        )

        enriched_recommendations = add_recommendation_reasons(
            list(recommendations),
            profile_states,
            media_singular="pelicula",
            media_plural="peliculas",
        )
        sections = build_recommendation_sections(
            enriched_recommendations,
            profile_states,
            selected_genre=selected_genre or None,
            selected_actor=selected_actor,
            media_plural="peliculas",
        )

        return render_template(
            "movie_recommendations.html",
            recommendations=enriched_recommendations,
            sections=sections,
            genres=sorted(genres),
            selected_genre=selected_genre,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            current_year=current_year,
            sync_errors=sync_errors,
            has_profile=bool(source_options),
            tmdb_enabled=is_tmdb_enabled(),
            source_options=source_options,
            selected_source_ids=selected_source_ids,
            source_filter_submitted=source_filter_submitted,
            include_rejected=include_rejected,
            actor_query=actor_query,
            selected_actor=selected_actor,
            actor_search_results=actor_search_results,
        )

    @app.post("/recomendaciones/peliculas/<int:movie_id>/rechazar")
    def reject_movie_recommendation(movie_id):
        movie = movie_recommendation_from_form(movie_id)
        store.reject_movie_recommendation(movie)
        flash(f"{movie['name']} no volvera a aparecer en recomendaciones de peliculas.", "success")
        return redirect_to_next(url_for("movie_recommendations"))

    @app.post("/recomendaciones/peliculas/<int:movie_id>/restaurar")
    def restore_movie_recommendation(movie_id):
        store.restore_movie_recommendation(movie_id)
        flash("Recomendacion de pelicula restaurada.", "success")
        return redirect_to_next(url_for("movie_recommendations", rechazadas=1))

    @app.get("/recomendaciones/mangas")
    def manga_recommendations():
        selected_genre = request.args.get("genero", "").strip()
        selected_source_ids = {
            int(value)
            for value in request.args.getlist("origen")
            if value.isdigit()
        }
        source_filter_submitted = request.args.get("fuentes") == "seleccionadas"
        include_rejected = request.args.get("rechazadas") == "1"
        author_query = request.args.get("autor", "").strip()
        author_id = request.args.get("autor_id", "").strip()
        author_source = request.args.get("autor_fuente", "").strip().casefold()
        current_year = date.today().year
        year_from = request.args.get("desde", type=int)
        year_to = request.args.get("hasta", type=int)
        sort_mode = request.args.get("orden", "puntuacion")
        if year_from is None and "desde" not in request.args:
            year_from = current_year - 35
        if sort_mode not in {"recientes", "puntuacion"}:
            sort_mode = "puntuacion"
        sync_errors = []

        manga_states = [
            build_manga_state(
                manga,
                store.get_manga_progress(manga["id"]),
                store.list_manga_chapter_read_overrides(manga["id"]),
            )
            for manga in store.list_mangas()
        ]
        source_options = [
            manga for manga in manga_states
            if manga["watched_count"] or manga["completed"]
        ]
        profile_states = [
            manga for manga in manga_states
            if not source_filter_submitted or manga["id"] in selected_source_ids
        ]

        selected_author = None
        author_search_results = []
        raw_candidates = []
        if is_anilist_enabled():
            try:
                raw_candidates.extend(get_anilist_manga_profile_candidates(profile_states))
                if author_id or author_query:
                    selected_author, author_candidates, author_search_results = (
                        get_manual_author_manga_recommendation_context(
                            author_id,
                            author_query,
                            author_source=author_source,
                        )
                    )
                    raw_candidates.extend(author_candidates)
                else:
                    raw_candidates.extend(get_anilist_manga_author_candidates(profile_states))
            except (AniListError, MangaDexError) as exc:
                sync_errors.append(str(exc))
        else:
            sync_errors.append("AniList no esta configurado.")

        raw_candidates = canonical_manga_recommendation_candidates(
            raw_candidates,
            store.list_mangas(),
        )
        rejected_ids = store.get_rejected_manga_recommendation_ids()
        recommendations, genres = rank_recommendations(
            profile_states,
            raw_candidates,
            store.get_manga_ids(),
            selected_genre=selected_genre or None,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            limit=current_app.config["RECOMMENDATION_LIMIT"],
            rejected_ids=rejected_ids,
            include_rejected=include_rejected,
        )

        enriched_recommendations = add_recommendation_reasons(
            list(recommendations),
            profile_states,
            media_singular="manga",
            media_plural="mangas",
            source_action="leiste",
            person_label="Autor",
            person_connection="autores",
            direct_source_label="AniList",
        )
        sections = build_recommendation_sections(
            enriched_recommendations,
            profile_states,
            selected_genre=selected_genre or None,
            selected_actor=selected_author,
            media_plural="mangas",
            source_action="leiste",
            direct_source_label="AniList",
            selected_person_subtitle="Mangas del autor que has elegido.",
            people_section_title="Con autores que ya has leido",
            people_section_subtitle="Mangas conectados por autores de tu biblioteca.",
            profile_action_label="leido",
            profile_seen_label="leidos",
        )

        return render_template(
            "manga_recommendations.html",
            recommendations=enriched_recommendations,
            sections=sections,
            genres=sorted(genres),
            selected_genre=selected_genre,
            year_from=year_from,
            year_to=year_to,
            sort_mode=sort_mode,
            current_year=current_year,
            sync_errors=sync_errors,
            has_profile=bool(source_options),
            anilist_enabled=is_anilist_enabled(),
            source_options=source_options,
            selected_source_ids=selected_source_ids,
            source_filter_submitted=source_filter_submitted,
            include_rejected=include_rejected,
            author_query=author_query,
            author_source=author_source,
            selected_author=selected_author,
            author_search_results=author_search_results,
        )

    @app.post("/recomendaciones/mangas/<int:manga_id>/rechazar")
    def reject_manga_recommendation(manga_id):
        manga = manga_recommendation_from_form(manga_id)
        store.reject_manga_recommendation(manga)
        flash(f"{manga['name']} no volvera a aparecer en recomendaciones de mangas.", "success")
        return redirect_to_next(url_for("manga_recommendations"))

    @app.post("/recomendaciones/mangas/<int:manga_id>/restaurar")
    def restore_manga_recommendation(manga_id):
        store.restore_manga_recommendation(manga_id)
        flash("Recomendacion de manga restaurada.", "success")
        return redirect_to_next(url_for("manga_recommendations", rechazadas=1))

    @app.get("/autores/<staff_id>")
    def author_detail(staff_id):
        if staff_id.isdigit():
            if not is_anilist_enabled():
                abort(404)

            try:
                author = get_anilist_staff(int(staff_id))
                manga_credits = get_staff_manga_recommendations(author)
                author_source_label = "AniList"
            except (KeyError, AniListError):
                abort(404)
        elif is_mangadex_enabled():
            try:
                author = get_mangadex_author(staff_id)
            except (KeyError, MangaDexError):
                abort(404)
            manga_credits = get_mangadex_author_manga_recommendations(author)
            author_source_label = "MangaDex"
        else:
            abort(404)

        saved_manga_ids = store.get_manga_ids()
        for manga in manga_credits:
            existing_manga = find_saved_duplicate_manga(manga)
            if existing_manga:
                manga["id"] = existing_manga["id"]
                manga["is_saved"] = True
            else:
                manga["is_saved"] = manga["id"] in saved_manga_ids

        return render_template(
            "author.html",
            author=author,
            author_source_label=author_source_label,
            author_source=author_source_label.casefold(),
            manga_credits=manga_credits,
        )

    @app.get("/actores/<int:person_id>")
    def actor_detail(person_id):
        if not is_tmdb_enabled():
            abort(404)

        try:
            person = get_tmdb_person(person_id)
            credits = get_person_tv_recommendations(person)
            movie_credits = get_person_movie_recommendations(person)
        except (KeyError, TMDbError):
            abort(404)

        saved_ids = store.get_show_ids()
        for show in credits:
            show["is_saved"] = show["id"] in saved_ids

        saved_movie_ids = store.get_movie_ids()
        for movie in movie_credits:
            movie["is_saved"] = movie["id"] in saved_movie_ids

        return render_template(
            "actor.html",
            person=person,
            credits=credits,
            movie_credits=movie_credits,
        )


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


def get_anilist_client():
    injected = current_app.config.get("ANILIST_CLIENT")
    if injected:
        return injected

    if "anilist_client" not in g:
        g.anilist_client = AniListClient(
            base_url=current_app.config["ANILIST_BASE_URL"],
            enabled=current_app.config["ANILIST_ENABLED"],
        )
    return g.anilist_client


def get_comick_client():
    injected = current_app.config.get("COMICK_CLIENT")
    if injected:
        return injected

    if "comick_client" not in g:
        g.comick_client = ComicKClient(
            base_url=current_app.config["COMICK_BASE_URL"],
            enabled=current_app.config["COMICK_ENABLED"],
        )
    return g.comick_client


def get_mangadex_client():
    injected = current_app.config.get("MANGADEX_CLIENT")
    if injected:
        return injected

    if "mangadex_client" not in g:
        g.mangadex_client = MangaDexClient(
            base_url=current_app.config["MANGADEX_BASE_URL"],
            enabled=current_app.config["MANGADEX_ENABLED"],
        )
    return g.mangadex_client


def get_manga_oni_client():
    injected = current_app.config.get("MANGA_ONI_CLIENT")
    if injected:
        return injected

    if "manga_oni_client" not in g:
        g.manga_oni_client = MangaOniClient(
            base_url=current_app.config["MANGA_ONI_BASE_URL"],
            enabled=current_app.config["MANGA_ONI_ENABLED"],
        )
    return g.manga_oni_client


def is_tmdb_enabled():
    return get_tmdb_client().enabled


def is_anilist_enabled():
    return get_anilist_client().enabled


def is_comick_enabled():
    return get_comick_client().enabled


def is_mangadex_enabled():
    return get_mangadex_client().enabled


def cached_json(key, ttl_seconds, producer, refresh=False):
    cached = None if refresh else store.cache_get(key)
    if cached is not None:
        return cached

    fallback = store.cache_get(key, allow_expired=True)

    try:
        payload = producer()
    except (TMDbError, AniListError, ComicKError, MangaDexError, MangaOniError):
        if fallback is not None:
            return fallback
        raise

    store.cache_set(key, payload, ttl_seconds)
    return payload


def safe_is_tmdb_show_id(value):
    try:
        return value is not None and is_tmdb_show_id(value)
    except (TypeError, ValueError):
        return False


def safe_is_tmdb_movie_id(value):
    try:
        return value is not None and is_tmdb_movie_id(value)
    except (TypeError, ValueError):
        return False


def safe_is_anilist_manga_id(value):
    try:
        return value is not None and is_anilist_manga_id(value)
    except (TypeError, ValueError):
        return False


def safe_is_comick_manga_id(value):
    try:
        return value is not None and is_comick_manga_id(value)
    except (TypeError, ValueError):
        return False


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


def tmdb_direct_movie_id_from_query(query):
    match = re.search(r"themoviedb\.org/movie/(\d+)", query, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    clean_query = query.strip()
    if clean_query.isdigit():
        return int(clean_query)
    return None


def anilist_direct_manga_id_from_query(query):
    match = re.search(r"anilist\.co/manga/(\d+)", query, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    clean_query = query.strip()
    if clean_query.isdigit():
        return int(clean_query)
    return None


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


def search_tmdb_movie_results(query, saved_ids, existing_results=None):
    if not is_tmdb_enabled():
        return []

    existing_keys = {show_signature(movie) for movie in (existing_results or [])}

    results = []
    seen_tmdb_ids = set()
    direct_id = tmdb_direct_movie_id_from_query(query)
    if direct_id:
        try:
            raw_movie = cached_json(
                f"tmdb:movie:{direct_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda: get_tmdb_client().get_movie(direct_id),
            )
        except TMDbError:
            raw_movie = None
        if raw_movie:
            movie = normalize_tmdb_movie(raw_movie)
            movie["is_saved"] = movie["id"] in saved_ids
            return [movie]

    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"tmdb:search:movie:{search_query.casefold()}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda search_query=search_query: get_tmdb_client().search_movie(search_query),
            )
        except TMDbError:
            continue

        for raw_movie in raw_results:
            tmdb_id = raw_movie.get("id")
            if not tmdb_id or tmdb_id in seen_tmdb_ids:
                continue
            seen_tmdb_ids.add(tmdb_id)
            movie = normalize_tmdb_movie(raw_movie)
            if show_signature(movie) in existing_keys:
                continue
            movie["is_saved"] = movie["id"] in saved_ids
            results.append(movie)
            if len(results) >= 12:
                return results
    return results


def search_anilist_manga_results(query, saved_ids, existing_results=None):
    if not is_anilist_enabled():
        return []

    existing_keys = {show_signature(manga) for manga in (existing_results or [])}

    results = []
    seen_anilist_ids = set()
    direct_id = anilist_direct_manga_id_from_query(query)
    if direct_id:
        try:
            raw_manga = cached_json(
                f"anilist:manga:{direct_id}",
                current_app.config["ANILIST_CACHE_SECONDS"],
                lambda: get_anilist_client().get_manga(direct_id),
            )
        except AniListError:
            raw_manga = None
        if raw_manga and not raw_manga.get("isAdult"):
            manga = normalize_anilist_manga(raw_manga)
            manga["is_saved"] = manga["id"] in saved_ids
            return [manga]

    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"anilist:search:manga:{search_query.casefold()}",
                current_app.config["ANILIST_CACHE_SECONDS"],
                lambda search_query=search_query: get_anilist_client().search_manga(search_query),
            )
        except AniListError:
            continue

        for raw_manga in raw_results:
            anilist_id = raw_manga.get("id")
            if not anilist_id or anilist_id in seen_anilist_ids or raw_manga.get("isAdult"):
                continue
            seen_anilist_ids.add(anilist_id)
            manga = normalize_anilist_manga(raw_manga)
            if show_signature(manga) in existing_keys:
                continue
            manga["is_saved"] = manga["id"] in saved_ids
            results.append(manga)
            if len(results) >= 12:
                return results
    return results


def search_comick_manga_results(query, saved_ids, saved_comick_id_map=None, existing_results=None):
    if not is_comick_enabled():
        return []

    existing_keys = {show_signature(manga) for manga in (existing_results or [])}
    saved_comick_id_map = saved_comick_id_map or {}
    results = []
    seen_ids = set()

    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"comick:search:manga:{search_query.casefold()}",
                current_app.config["COMICK_CACHE_SECONDS"],
                lambda search_query=search_query: get_comick_client().search_manga(search_query),
            )
        except ComicKError:
            continue

        for raw_manga in raw_results:
            comick_id = raw_manga.get("hid")
            if not comick_id or comick_id in seen_ids:
                continue
            if (raw_manga.get("content_rating") or "").casefold() == "pornographic":
                continue
            seen_ids.add(comick_id)
            manga = normalize_comick_manga(raw_manga)
            if show_signature(manga) in existing_keys:
                continue
            saved_manga_id = saved_comick_id_map.get(comick_id)
            if saved_manga_id:
                manga["id"] = saved_manga_id
            manga["is_saved"] = manga["id"] in saved_ids or bool(saved_manga_id)
            results.append(manga)
            if len(results) >= 12:
                return results
    return results


def search_mangadex_manga_results(query, saved_ids, saved_mangadex_ids=None, existing_results=None):
    if not is_mangadex_enabled():
        return []

    existing_keys = {show_signature(manga) for manga in (existing_results or [])}
    saved_mangadex_ids = saved_mangadex_ids or set()
    results = []
    seen_ids = set()

    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"mangadex:search:manga:{search_query.casefold()}",
                current_app.config["MANGADEX_CACHE_SECONDS"],
                lambda search_query=search_query: get_mangadex_client().search_manga(search_query),
            )
        except MangaDexError:
            continue

        for raw_manga in raw_results:
            mangadex_id = raw_manga.get("id")
            if not mangadex_id or mangadex_id in seen_ids:
                continue
            seen_ids.add(mangadex_id)
            manga = normalize_mangadex_manga(raw_manga)
            if show_signature(manga) in existing_keys:
                continue
            manga["is_saved"] = manga["id"] in saved_ids or mangadex_id in saved_mangadex_ids
            results.append(manga)
            if len(results) >= 12:
                return results
    return results


def build_recommendation_sections(
    recommendations,
    show_states,
    selected_genre=None,
    selected_actor=None,
    media_plural="series",
    source_action="viste",
    direct_source_label="TMDb",
    selected_person_subtitle=None,
    people_section_title=None,
    people_section_subtitle=None,
    profile_action_label="visto",
    profile_seen_label="vistas",
):
    if not recommendations:
        return []

    sections = []
    used_ids = set()
    media_plural_title = media_plural[:1].upper() + media_plural[1:]

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

    if selected_actor:
        actor_items = take_unique([
            recommendation for recommendation in recommendations
            if selected_actor["id"] in (recommendation.get("reason_actor_ids") or [])
        ])
        if actor_items:
            sections.append(
                {
                    "title": f"Con {selected_actor['name']}",
                    "subtitle": selected_person_subtitle
                    or f"{media_plural_title} del reparto del actor que has elegido.",
                    "items": actor_items,
                }
            )

    actor_items = take_unique(
        [recommendation for recommendation in recommendations if recommendation.get("actor_sources")]
    )
    if actor_items:
        sections.append(
            {
                "title": people_section_title or "Con actores que ya has visto",
                "subtitle": people_section_subtitle
                or f"{media_plural_title} conectadas por el reparto de tu biblioteca.",
                "items": actor_items,
            }
        )

    if selected_genre:
        genre_items = take_unique([
            recommendation for recommendation in recommendations
            if selected_genre in (recommendation.get("genres") or [])
        ])
        if genre_items:
            sections.append(
                {
                    "title": f"Recomendación por {selected_genre}",
                    "subtitle": "Priorizadas por nota y afinidad dentro del género seleccionado.",
                    "items": genre_items,
                }
            )

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
                    "title": f"Porque {source_action} {source['name']}",
                    "subtitle": "Recomendaciones con relación directa o géneros realmente compartidos.",
                    "items": items,
                }
            )

    direct_items = take_unique(
        [recommendation for recommendation in recommendations if recommendation.get("profile_sources")]
    )
    if direct_items:
        sections.append(
            {
                "title": f"Similares de {direct_source_label}",
                "subtitle": f"Sugerencias directas de {direct_source_label} para tus {media_plural} {profile_seen_label}.",
                "items": direct_items,
            }
        )

    top_items = take_unique(recommendations)
    if top_items:
        sections.append(
            {
                "title": "Para ti ahora",
                "subtitle": f"Mezcla de afinidad, puntuación y lo que ya has marcado como {profile_action_label}.",
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
                "subtitle": f"{media_plural_title} recientes y bien valoradas que encajan con tu perfil.",
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
                    "subtitle": f"Más opciones de cadenas o plataformas que ya aparecen en tus {profile_seen_label}.",
                    "items": items,
                }
            )

    return [section for section in sections if section["items"]]


def build_movie_state(movie, watched_at=None):
    watched = bool(watched_at)
    return {
        **movie,
        "watched": watched,
        "watched_at": watched_at,
        "watched_count": 1 if watched else 0,
        "completed": watched,
        "progress": 100 if watched else 0,
    }


def sort_movies(movies):
    return sorted(
        movies,
        key=lambda movie: (
            0 if movie["watched"] else 1,
            movie["name"].casefold(),
        ),
    )


def build_manga_state(manga, progress=None, chapter_overrides=None):
    progress = progress or {}
    chapter_overrides = chapter_overrides or {}
    read_at = progress.get("read_at")
    chapter_read = progress.get("chapter_read")
    latest_chapter_number = parse_chapter_number(manga.get("latest_chapter") or manga.get("chapters"))
    numeric_overrides = {
        number: is_read
        for chapter_key, is_read in chapter_overrides.items()
        if (number := parse_chapter_number(chapter_key)) is not None
    }
    if chapter_read or numeric_overrides:
        chapter_read_number = parse_chapter_number(chapter_read)
        effective_count = int(chapter_read_number or 0)
        for number, is_read in numeric_overrides.items():
            if is_read and (chapter_read_number is None or number > chapter_read_number):
                effective_count += 1
            elif not is_read and chapter_read_number is not None and number <= chapter_read_number:
                effective_count = max(0, effective_count - 1)

        pending_overrides = [number for number, is_read in numeric_overrides.items() if not is_read]
        read = (
            chapter_read_number is not None
            and latest_chapter_number is not None
            and chapter_read_number >= latest_chapter_number
            and not any(number <= latest_chapter_number for number in pending_overrides)
        )
        percent = manga_chapter_progress_percent(effective_count, latest_chapter_number, read)
        next_chapter = next_manga_chapter_placeholder(chapter_read_number, latest_chapter_number)
        if chapter_read_number is None and latest_chapter_number is not None and latest_chapter_number >= 1:
            next_chapter = {
                "chapter": "1",
                "chapter_number": 1.0,
            }
        if pending_overrides:
            pending_number = min(pending_overrides)
            if not next_chapter or pending_number < next_chapter["chapter_number"]:
                next_chapter = {
                    "chapter": format_chapter_number(pending_number),
                    "chapter_number": pending_number,
                }
        while next_chapter and numeric_overrides.get(next_chapter["chapter_number"]) is True:
            next_chapter = next_manga_chapter_placeholder(
                next_chapter["chapter_number"],
                latest_chapter_number,
            )
        return {
            **manga,
            "read": read,
            "read_at": read_at,
            "chapter_read": chapter_read,
            "chapter_read_number": chapter_read_number,
            "volumes_read": 0,
            "in_progress": bool(effective_count) and not read,
            "progress_label": f"Cap. {chapter_read}" if chapter_read else f"{effective_count} capitulos vistos",
            "progress_percent": percent,
            "next_volume_count": 1,
            "next_chapter": next_chapter,
            "read_chapter_count": effective_count,
            "available_chapter_count": format_chapter_number(latest_chapter_number),
            "watched_count": effective_count,
            "completed": read,
            "progress": percent,
        }

    volumes_read = int(progress.get("volumes_read") or 0)
    total_volumes = manga.get("volumes") or 0
    if total_volumes:
        volumes_read = min(volumes_read, total_volumes)
        read = volumes_read >= total_volumes
        percent = round((volumes_read / total_volumes) * 100)
        progress_label = f"{volumes_read}/{total_volumes} tomos"
    else:
        read = bool(read_at)
        percent = 100 if read else 0
        progress_label = "Leido" if read else "Pendiente"

    in_progress = bool(volumes_read) and not read
    return {
        **manga,
        "read": read,
        "read_at": read_at,
        "chapter_read": None,
        "chapter_read_number": None,
        "volumes_read": volumes_read,
        "in_progress": in_progress,
        "progress_label": progress_label,
        "progress_percent": percent,
        "next_chapter": None,
        "next_volume_count": (
            min(volumes_read + 1, total_volumes)
            if total_volumes
            else max(volumes_read + 1, 1)
        ),
        "read_chapter_count": 0,
        "available_chapter_count": format_chapter_number(latest_chapter_number),
        "watched_count": volumes_read if volumes_read else (1 if read else 0),
        "completed": read,
        "progress": percent,
    }


def manga_chapter_progress_percent(chapter_read_number, latest_chapter_number, read=False):
    if read:
        return 100
    if chapter_read_number is None or latest_chapter_number is None or latest_chapter_number <= 0:
        return 50 if chapter_read_number is not None else 0
    percent = int((chapter_read_number / latest_chapter_number) * 100)
    return min(max(percent, 1), 99)


def sort_mangas(mangas):
    return sorted(
        mangas,
        key=lambda manga: (
            0 if manga["in_progress"] else 2 if manga["read"] else 1,
            manga["name"].casefold(),
        ),
    )


def parse_chapter_number(value):
    return parse_comick_number(value) or parse_mangadex_number(value)


def format_chapter_number(value):
    if value is None:
        return None
    if float(value).is_integer():
        return str(int(value))
    return str(value).rstrip("0").rstrip(".")


def next_manga_chapter_placeholder(current_number, latest_number):
    if current_number is None or latest_number is None or current_number >= latest_number:
        return None
    if not float(current_number).is_integer():
        return None
    next_number = current_number + 1
    return {
        "chapter": format_chapter_number(next_number),
        "chapter_number": next_number,
    }


def enrich_manga_state_with_chapters(manga, chapters):
    if not chapters:
        return manga

    numbered = [
        chapter for chapter in chapters
        if chapter.get("chapter_number") is not None
    ]
    if not numbered:
        return manga

    latest = max(numbered, key=lambda chapter: chapter["chapter_number"])
    manga["latest_chapter"] = latest.get("chapter")
    manga["available_chapter_count"] = format_chapter_number(latest["chapter_number"])
    current_number = manga.get("chapter_read_number")
    unread_chapters = [chapter for chapter in numbered if not chapter.get("read")]
    manga["next_chapter"] = (
        min(unread_chapters, key=lambda chapter: chapter["chapter_number"])
        if unread_chapters
        else None
    )
    if not unread_chapters and (
        current_number is not None and latest["chapter_number"] <= current_number
    ):
        manga["read"] = True
        manga["completed"] = True
        manga["in_progress"] = False
        manga["progress"] = 100
        manga["progress_percent"] = 100
    else:
        manga["read"] = False
        manga["completed"] = False
        manga["in_progress"] = bool(manga.get("read_chapter_count"))
        manga["progress"] = manga_chapter_progress_percent(
            manga.get("read_chapter_count"),
            latest["chapter_number"],
        )
        manga["progress_percent"] = manga["progress"]
    return manga


def numbered_manga_chapters(chapters):
    return [
        chapter for chapter in chapters
        if chapter.get("chapter") and chapter.get("chapter_number") is not None
    ]


def manga_chapter_key(value):
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z._-]+", "-", text).strip("-._")
    return text[:80] or "capitulo"


def manga_download_root():
    root = Path(current_app.config["MANGA_DOWNLOAD_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def manga_download_relative_folder(manga_id, chapter_key):
    return f"{manga_id}/{manga_chapter_key(chapter_key)}"


def manga_download_folder(manga_id, chapter_key):
    return manga_download_root() / str(manga_id) / manga_chapter_key(chapter_key)


def manga_download_temp_folder(manga_id, chapter_key):
    folder = manga_download_folder(manga_id, chapter_key)
    return folder.parent / f".{folder.name}.tmp"


def replace_manga_download_folder(source_folder, target_folder):
    delete_manga_download_folder(target_folder)
    source_folder.rename(target_folder)


def delete_manga_download_folder(folder):
    folder = Path(folder)
    if not folder.exists():
        return
    clear_chapter_directory(folder)
    folder.rmdir()


def manga_download_base_url(manga):
    stored_url = store.get_manga_download_base_url(manga["id"])
    if stored_url:
        return stored_url.rstrip("/")

    title_candidates = {
        fold_search_text(manga.get("name")).casefold(),
        fold_search_text(manga.get("original_name")).casefold(),
    }
    if title_candidates & {"one piece", "una pieza"}:
        return "https://mangasnosekai.com/manga/una-pieza"
    return ""


def manga_oni_url(manga):
    stored_url = store.get_manga_oni_url(manga["id"])
    if stored_url:
        return normalize_manga_url(stored_url)
    return default_manga_url(manga.get("name") or manga.get("original_name"))


def get_manga_oni_chapters(manga, refresh=False):
    source_url = manga_oni_url(manga)
    if not source_url or not get_manga_oni_client().enabled:
        return []
    return cached_json(
        f"manga-oni:chapters:{source_url.casefold()}",
        current_app.config["MANGA_ONI_CACHE_SECONDS"],
        lambda: get_manga_oni_client().get_manga_chapters(source_url),
        refresh=refresh,
    )


def merge_manga_oni_chapters(chapters, manga_oni_chapters):
    merged = {chapter.get("chapter"): dict(chapter) for chapter in chapters if chapter.get("chapter")}
    for oni_chapter in manga_oni_chapters:
        chapter_number = oni_chapter.get("chapter")
        current = merged.get(chapter_number)
        if not current:
            merged[chapter_number] = dict(oni_chapter)
            continue
        if oni_chapter.get("title"):
            current["title"] = oni_chapter["title"]
        current["download_url"] = oni_chapter.get("download_url")
        current["manga_oni_url"] = oni_chapter.get("official_url")
        current["title_source"] = "Manga Oni"

    return sorted(
        merged.values(),
        key=lambda chapter: (
            chapter.get("chapter_number") is None,
            chapter.get("chapter_number") or 0,
            chapter.get("chapter") or "",
        ),
    )


def manga_chapter_download_url(base_url, chapter):
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url or not chapter:
        return ""
    return f"{base_url}/capitulo-{manga_chapter_key(chapter)}/"


def attach_manga_downloads_to_chapters(manga_id, chapters, download_base_url=""):
    downloads = {
        download["chapter_key"]: download
        for download in store.list_manga_downloads(manga_id)
    }
    read_number = parse_chapter_number(store.get_manga_progress(manga_id).get("chapter_read"))
    pending_download_count = 0
    for chapter in sorted(chapters, key=lambda item: item.get("chapter_number") or 0):
        chapter_key = manga_chapter_key(chapter.get("chapter") or chapter.get("id"))
        chapter["chapter_key"] = chapter_key
        chapter["download_url"] = chapter.get("download_url") or manga_chapter_download_url(
            download_base_url,
            chapter.get("chapter"),
        )
        download = downloads.get(chapter_key)
        if (
            not download
            and chapter.get("chapter_number") is not None
            and (read_number is None or chapter["chapter_number"] > read_number)
        ):
            pending_download_count += 1
        chapter["pending_download_count"] = pending_download_count
        if not download:
            chapter["download"] = None
            continue

        progress = store.get_manga_reader_progress(manga_id, chapter_key) or {}
        chapter["download"] = {
            **download,
            "reader_url": url_for("manga_reader", manga_id=manga_id, chapter=chapter_key),
            "resume_panel": progress.get("current_panel") or 1,
        }
    return chapters


def preferred_manga_chapters(manga_id, manga, refresh=False):
    errors = []
    try:
        chapters = numbered_manga_chapters(get_manga_chapters(manga_id, refresh=refresh))
    except (ComicKError, MangaDexError) as exc:
        chapters = []
        errors.append(str(exc))

    try:
        oni_chapters = get_manga_oni_chapters(manga, refresh=refresh)
    except MangaOniError as exc:
        oni_chapters = []
        errors.append(str(exc))

    chapters = numbered_manga_chapters(merge_manga_oni_chapters(chapters, oni_chapters))
    chapter_read_number = parse_chapter_number(store.get_manga_progress(manga_id).get("chapter_read"))
    chapters = mark_manga_chapters_read(
        chapters,
        chapter_read_number,
        overrides=store.list_manga_chapter_read_overrides(manga_id),
    )
    return chapters, " / ".join(dict.fromkeys(errors)) if not chapters else None


def manga_chapter_download_candidates(chapter, download_base_url="", explicit_url=""):
    candidates = []
    for candidate in (
        explicit_url,
        chapter.get("download_url"),
        manga_chapter_download_url(download_base_url, chapter.get("chapter")),
        chapter.get("official_url"),
    ):
        candidate = (candidate or "").strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def download_and_store_manga_chapter(manga_id, chapter, download_urls):
    chapter_label = chapter.get("chapter") or chapter.get("chapter_key")
    chapter_key = manga_chapter_key(chapter_label)
    chapter_dir = manga_download_folder(manga_id, chapter_key)
    temp_chapter_dir = manga_download_temp_folder(manga_id, chapter_key)
    errors = []

    for download_url in download_urls:
        try:
            result = download_manga_chapter(
                download_url,
                temp_chapter_dir,
                chrome_version=current_app.config.get("MANGA_BROWSER_VERSION"),
            )
        except MissingMangaDownloadDependency:
            delete_manga_download_folder(temp_chapter_dir)
            raise
        except MangaDownloadError as exc:
            delete_manga_download_folder(temp_chapter_dir)
            errors.append(str(exc))
            continue

        replace_manga_download_folder(temp_chapter_dir, chapter_dir)
        store.upsert_manga_download(
            manga_id,
            chapter_key,
            download_url,
            manga_download_relative_folder(manga_id, chapter_key),
            result.panel_count,
            result.page_count,
        )
        return result, download_url

    raise MangaDownloadError(" / ".join(errors) or "No hay una URL disponible para descargar el capitulo.")


def manga_reader_pages(manga_id, chapter_key):
    folder = manga_download_folder(manga_id, chapter_key)
    vignette_map = read_vignette_map(folder)
    pages = []
    for index, image_path in enumerate(chapter_images(folder), start=1):
        original_page = vignette_map.get(str(index))
        full_url = None
        if original_page:
            full_url = url_for(
                "manga_download_file",
                manga_id=manga_id,
                chapter=chapter_key,
                file_name=f"paginas_completas/{original_page}",
            )
        pages.append(
            {
                "index": index,
                "url": url_for(
                    "manga_download_file",
                    manga_id=manga_id,
                    chapter=chapter_key,
                    file_name=image_path.name,
                ),
                "full_url": full_url,
            }
        )
    return pages


def is_manga_chapter_read(manga_id, chapter_key):
    override = store.get_manga_chapter_read_override(manga_id, manga_chapter_key(chapter_key))
    if override is not None:
        return override
    progress = store.get_manga_progress(manga_id)
    chapter_read_number = parse_chapter_number(progress.get("chapter_read"))
    chapter_number = parse_chapter_number(chapter_key)
    if chapter_read_number is not None and chapter_number is not None:
        return chapter_number <= chapter_read_number

    manga = store.get_manga(manga_id)
    if not manga:
        return False
    return build_manga_state(manga, progress)["read"]


def mark_single_manga_chapter_read(manga_id, chapter_key):
    store.set_manga_chapter_read_override(
        manga_id,
        manga_chapter_key(chapter_key),
        True,
    )


def clear_manga_chapter_overrides_through(manga_id, chapter_key):
    target_number = parse_chapter_number(chapter_key)
    if target_number is None:
        store.delete_manga_chapter_read_override(manga_id, manga_chapter_key(chapter_key))
        return
    for override_key in store.list_manga_chapter_read_overrides(manga_id):
        override_number = parse_chapter_number(override_key)
        if override_number is not None and override_number <= target_number:
            store.delete_manga_chapter_read_override(manga_id, override_key)


def delete_read_manga_downloads(manga_id=None):
    downloads = store.list_manga_downloads(manga_id)
    deleted = 0
    for download in downloads:
        current_manga = store.get_manga(download["manga_id"])
        if not current_manga:
            continue
        if not is_downloaded_manga_chapter_read(current_manga, download):
            continue

        delete_manga_download_files(download["manga_id"], download["chapter_key"])
        store.delete_manga_download(download["manga_id"], download["chapter_key"])
        deleted += 1
    return deleted


def is_downloaded_manga_chapter_read(manga, download):
    return is_manga_chapter_read(manga["id"], download["chapter_key"])


def delete_manga_download_files(manga_id, chapter_key):
    folder = manga_download_folder(manga_id, chapter_key)
    delete_manga_download_folder(folder)
    parent = folder.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def redirect_to_next(default_url):
    next_url = request.form.get("next")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(default_url)


def unsafe_remote_file_name(file_name):
    return (
        not file_name
        or "\\" in file_name
        or file_name.startswith("/")
        or any(part in {"", ".", ".."} for part in file_name.split("/"))
    )


def proxy_image_response(url):
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ARCATV/0.1 (+local-tv-tracker)"},
            timeout=15,
        )
        if response.status_code == 404:
            raise MangaDexError("No se encontro la portada.")
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MangaDexError("No se pudo cargar la portada.") from exc

    return Response(
        response.content,
        mimetype=response.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


def is_show_finalized(show):
    status = (show.get("status") or "").casefold()
    return bool(show.get("ended")) or status in {"finalizada", "ended"}


def is_show_completed(show_state):
    return bool(show_state.get("completed"))


def get_show(show_id, refresh=False):
    tmdb_id = resolve_tmdb_id_for_show_id(show_id)
    if not tmdb_id:
        return None
    return get_tmdb_show_from_tmdb_id(tmdb_id, refresh=refresh)


def get_show_for_storage(show_id, refresh=False):
    raw_show = get_show(show_id, refresh=refresh)
    if not raw_show:
        return None
    return normalize_tmdb_show(raw_show)


def get_tmdb_show(show_id, refresh=False):
    tmdb_id = tmdb_id_from_show_id(show_id)
    return get_tmdb_show_from_tmdb_id(tmdb_id, refresh=refresh)


def get_tmdb_show_from_tmdb_id(tmdb_id, refresh=False):
    return cached_json(
        f"tmdb:show:{tmdb_id}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().get_tv(tmdb_id),
        refresh=refresh,
    )


def resolve_tmdb_id_for_show_id(show_id):
    if safe_is_tmdb_show_id(show_id):
        return tmdb_id_from_show_id(show_id)
    saved_show = store.get_show(show_id)
    if not saved_show:
        return None
    return find_tmdb_id_for_show(saved_show)


def get_movie(movie_id, refresh=False):
    tmdb_id = resolve_tmdb_id_for_movie_id(movie_id)
    if not tmdb_id:
        return None
    return get_tmdb_movie_from_tmdb_id(tmdb_id, refresh=refresh)


def get_movie_for_storage(movie_id, refresh=False):
    raw_movie = get_movie(movie_id, refresh=refresh)
    if not raw_movie:
        return None
    return normalize_tmdb_movie(raw_movie)


def get_tmdb_movie(movie_id, refresh=False):
    tmdb_id = tmdb_id_from_movie_id(movie_id)
    return get_tmdb_movie_from_tmdb_id(tmdb_id, refresh=refresh)


def get_tmdb_movie_from_tmdb_id(tmdb_id, refresh=False):
    return cached_json(
        f"tmdb:movie:{tmdb_id}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().get_movie(tmdb_id),
        refresh=refresh,
    )


def resolve_tmdb_id_for_movie_id(movie_id):
    if safe_is_tmdb_movie_id(movie_id):
        return tmdb_id_from_movie_id(movie_id)
    saved_movie = store.get_movie(movie_id)
    if not saved_movie:
        return None
    return find_tmdb_id_for_movie(saved_movie)


def get_manga(manga_id, refresh=False):
    anilist_id = resolve_anilist_id_for_manga_id(manga_id)
    if not anilist_id:
        return None
    return get_anilist_manga_from_anilist_id(anilist_id, refresh=refresh)


def get_manga_for_storage(manga_id, refresh=False):
    saved_manga = store.get_manga(manga_id)
    if saved_manga and saved_manga.get("comick_id"):
        return get_comick_manga_for_storage(saved_manga["comick_id"], refresh=refresh)
    if saved_manga and saved_manga.get("mangadex_id"):
        return get_mangadex_manga_for_storage(saved_manga["mangadex_id"], refresh=refresh)

    raw_manga = get_manga(manga_id, refresh=refresh)
    if not raw_manga:
        return None
    return normalize_anilist_manga(raw_manga)


def get_comick_manga_for_storage(comick_id, refresh=False):
    raw_manga = cached_json(
        f"comick:manga:{comick_id}",
        current_app.config["COMICK_CACHE_SECONDS"],
        lambda: get_comick_client().get_manga(comick_id),
        refresh=refresh,
    )
    if not raw_manga:
        return None
    return normalize_comick_manga(raw_manga)


def get_mangadex_manga_for_storage(mangadex_id, refresh=False):
    raw_manga = cached_json(
        f"mangadex:manga:{mangadex_id}",
        current_app.config["MANGADEX_CACHE_SECONDS"],
        lambda: get_mangadex_client().get_manga(mangadex_id),
        refresh=refresh,
    )
    if not raw_manga:
        return None
    return normalize_mangadex_manga(raw_manga)


def merge_manga_storage(current_manga, updated_manga):
    merged = {
        **current_manga,
        **updated_manga,
        "id": current_manga["id"],
        "comick_id": updated_manga.get("comick_id") or current_manga.get("comick_id"),
        "mangadex_id": updated_manga.get("mangadex_id") or current_manga.get("mangadex_id"),
    }
    if not updated_manga.get("image_url") and current_manga.get("image_url"):
        merged["image_url"] = current_manga["image_url"]
    if not updated_manga.get("official_url") and current_manga.get("official_url"):
        merged["official_url"] = current_manga["official_url"]
    return merged


def refresh_manga_from_source(manga):
    manga = ensure_comick_link_for_manga(manga)
    updated_manga = get_manga_for_storage(manga["id"], refresh=True)
    if not updated_manga:
        return None

    merged = merge_manga_storage(manga, updated_manga)
    store.upsert_manga(merged)
    get_manga_chapters(manga["id"], refresh=True)
    return store.get_manga(manga["id"]) or merged


def find_saved_duplicate_manga(manga):
    if not manga:
        return None

    direct_match = store.get_manga(manga["id"])
    if direct_match:
        return direct_match

    for key, getter in (
        ("comick_id", store.get_manga_by_comick_id),
        ("mangadex_id", store.get_manga_by_mangadex_id),
    ):
        external_id = manga.get(key)
        if not external_id:
            continue
        existing_manga = getter(external_id)
        if existing_manga and existing_manga["id"] != manga["id"]:
            return existing_manga

    if not manga.get("comick_id") and is_comick_enabled():
        comick_id = find_comick_id_for_manga(manga, allow_fallback=False)
        if comick_id:
            existing_manga = store.get_manga_by_comick_id(comick_id)
            if existing_manga and existing_manga["id"] != manga["id"]:
                return existing_manga

    return None


def ensure_comick_link_for_manga(manga):
    if not manga or manga.get("comick_id") or not is_comick_enabled():
        return manga

    comick_id = find_comick_id_for_manga(manga, allow_fallback=False)
    if not comick_id:
        return manga

    existing_manga = store.get_manga_by_comick_id(comick_id)
    if existing_manga and existing_manga["id"] != manga["id"]:
        if not store.manga_has_user_state(manga["id"]):
            store.remove_manga(manga["id"])
        try:
            comick_manga = get_comick_manga_for_storage(comick_id, refresh=True)
        except ComicKError:
            return store.get_manga(existing_manga["id"]) or existing_manga
        if comick_manga:
            store.upsert_manga(merge_manga_storage(existing_manga, comick_manga))
        return store.get_manga(existing_manga["id"]) or existing_manga

    try:
        comick_manga = get_comick_manga_for_storage(comick_id, refresh=True)
    except ComicKError:
        return manga
    if not comick_manga:
        return manga

    merged = merge_manga_storage(manga, comick_manga)
    store.upsert_manga(merged)
    return store.get_manga(manga["id"]) or merged


def find_comick_id_for_manga(manga, allow_fallback=True):
    query = manga.get("original_name") or manga.get("name")
    if not query:
        return None

    wanted_year = (manga.get("premiered") or "")[:4]
    wanted_signature = show_signature(manga)
    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"comick:resolve:manga:{search_query.casefold()}:{wanted_year}",
                current_app.config["COMICK_CACHE_SECONDS"],
                lambda search_query=search_query: get_comick_client().search_manga(search_query, limit=8),
            )
        except ComicKError:
            continue

        normalized = []
        for raw_manga in raw_results:
            if not raw_manga.get("hid"):
                continue
            if (raw_manga.get("content_rating") or "").casefold() == "pornographic":
                continue
            candidate = normalize_comick_manga(raw_manga)
            normalized.append(candidate)
            if show_signature(candidate) == wanted_signature:
                return candidate["comick_id"]

        for candidate in normalized:
            if wanted_year and (candidate.get("premiered") or "")[:4] == wanted_year:
                return candidate["comick_id"]
        if normalized and allow_fallback:
            return normalized[0]["comick_id"]
    return None


def ensure_mangadex_link_for_manga(manga):
    if not manga or manga.get("mangadex_id") or not is_mangadex_enabled():
        return manga

    mangadex_id = find_mangadex_id_for_manga(manga, allow_fallback=False)
    if not mangadex_id:
        return manga

    existing_manga = store.get_manga_by_mangadex_id(mangadex_id)
    if existing_manga and existing_manga["id"] != manga["id"]:
        return {**manga, "mangadex_id": mangadex_id}

    try:
        mangadex_manga = get_mangadex_manga_for_storage(mangadex_id, refresh=True)
    except MangaDexError:
        return {**manga, "mangadex_id": mangadex_id}
    if not mangadex_manga:
        return {**manga, "mangadex_id": mangadex_id}

    merged = merge_manga_storage(manga, mangadex_manga)
    store.upsert_manga(merged)
    return store.get_manga(manga["id"]) or merged


def find_mangadex_id_for_manga(manga, allow_fallback=True):
    query = manga.get("original_name") or manga.get("name")
    if not query:
        return None

    wanted_year = (manga.get("premiered") or "")[:4]
    wanted_signature = show_signature(manga)
    for search_query in tmdb_search_queries(query):
        try:
            raw_results = cached_json(
                f"mangadex:resolve:manga:{search_query.casefold()}:{wanted_year}",
                current_app.config["MANGADEX_CACHE_SECONDS"],
                lambda search_query=search_query: get_mangadex_client().search_manga(search_query),
            )
        except MangaDexError:
            continue

        normalized = []
        for raw_manga in raw_results:
            if not raw_manga.get("id"):
                continue
            candidate = normalize_mangadex_manga(raw_manga)
            normalized.append(candidate)
            if show_signature(candidate) == wanted_signature:
                return candidate["mangadex_id"]

        for candidate in normalized:
            if wanted_year and (candidate.get("premiered") or "")[:4] == wanted_year:
                return candidate["mangadex_id"]
        if normalized and allow_fallback:
            return normalized[0]["mangadex_id"]
    return None


def get_anilist_manga_from_anilist_id(anilist_id, refresh=False):
    return cached_json(
        f"anilist:manga:{anilist_id}",
        current_app.config["ANILIST_CACHE_SECONDS"],
        lambda: get_anilist_client().get_manga(anilist_id),
        refresh=refresh,
    )


def resolve_anilist_id_for_manga_id(manga_id):
    if safe_is_anilist_manga_id(manga_id):
        return anilist_id_from_manga_id(manga_id)
    saved_manga = store.get_manga(manga_id)
    if not saved_manga:
        return None
    return find_anilist_id_for_manga(saved_manga)


def get_manga_chapters(manga_id, refresh=False):
    manga = store.get_manga(manga_id)
    if not manga:
        return []
    if manga.get("comick_id") and is_comick_enabled():
        try:
            return get_comick_manga_chapters(manga, refresh=refresh)
        except ComicKError:
            if is_mangadex_enabled():
                fallback_manga = ensure_mangadex_link_for_manga(manga)
                if fallback_manga.get("mangadex_id"):
                    return get_mangadex_manga_chapters(fallback_manga, refresh=refresh)
            raise
    if manga.get("mangadex_id") and is_mangadex_enabled():
        return get_mangadex_manga_chapters(manga, refresh=refresh)
    return []


def get_comick_manga_chapters(manga, refresh=False):
    comick_id = manga["comick_id"]
    languages = current_app.config["COMICK_LANGUAGES"]
    fetch_limit = current_app.config["COMICK_CHAPTER_FETCH_LIMIT"]
    page_size = max(1, current_app.config["COMICK_CHAPTER_PAGE_SIZE"])

    def load_chapters():
        chapters = []
        for language in languages:
            fetched_for_language = 0
            page = 1
            while fetched_for_language < fetch_limit:
                payload = get_comick_client().get_manga_chapters(
                    comick_id,
                    language=language,
                    page=page,
                    limit=min(page_size, fetch_limit - fetched_for_language),
                )
                raw_items = payload.get("chapters") or []
                chapters.extend(raw_items)
                fetched_for_language += len(raw_items)
                total = payload.get("total") or fetched_for_language
                if not raw_items or fetched_for_language >= total:
                    break
                page += 1
        return chapters

    raw_chapters = cached_json(
        f"comick:manga:chapters:{comick_id}:{','.join(languages)}:{fetch_limit}",
        current_app.config["COMICK_CHAPTER_CACHE_SECONDS"],
        load_chapters,
        refresh=refresh,
    )
    progress = store.get_manga_progress(manga["id"])
    chapter_read_number = parse_chapter_number(progress.get("chapter_read"))
    chapters = dedupe_comick_chapters_by_number([
        normalize_comick_chapter(raw_chapter)
        for raw_chapter in raw_chapters
    ])
    chapters = fill_missing_manga_chapters(chapters, manga)
    return mark_manga_chapters_read(chapters, chapter_read_number)


def get_mangadex_manga_chapters(manga, refresh=False):
    mangadex_id = manga["mangadex_id"]
    languages = current_app.config["MANGADEX_LANGUAGES"]
    fetch_limit = current_app.config["MANGADEX_CHAPTER_FETCH_LIMIT"]

    def load_chapters():
        chapters = []
        offset = 0
        page_size = 100
        while offset < fetch_limit:
            payload = get_mangadex_client().get_manga_feed(
                mangadex_id,
                languages=languages,
                offset=offset,
                limit=min(page_size, fetch_limit - offset),
            )
            raw_items = payload.get("data") or []
            chapters.extend(raw_items)
            total = payload.get("total") or len(chapters)
            offset += len(raw_items)
            if not raw_items or offset >= total:
                break
        return chapters

    raw_chapters = cached_json(
        f"mangadex:manga:chapters:{mangadex_id}:{','.join(languages)}:{fetch_limit}",
        current_app.config["MANGADEX_CHAPTER_CACHE_SECONDS"],
        load_chapters,
        refresh=refresh,
    )
    progress = store.get_manga_progress(manga["id"])
    chapter_read_number = parse_chapter_number(progress.get("chapter_read"))
    chapters = dedupe_chapters_by_number([
        normalize_mangadex_chapter(raw_chapter)
        for raw_chapter in raw_chapters
    ])
    chapters = fill_missing_manga_chapters(chapters, manga)
    return mark_manga_chapters_read(chapters, chapter_read_number)


def mark_manga_chapters_read(chapters, chapter_read_number, overrides=None):
    overrides = overrides or {}
    numbered_chapters = sorted(
        [
            chapter for chapter in chapters
            if chapter.get("chapter_number") is not None
        ],
        key=lambda chapter: chapter["chapter_number"],
    )
    previous_chapter = None
    for chapter in numbered_chapters:
        chapter["previous_chapter"] = previous_chapter.get("chapter") if previous_chapter else ""
        previous_chapter = chapter

    for chapter in chapters:
        chapter_number = chapter.get("chapter_number")
        chapter_key = manga_chapter_key(chapter.get("chapter") or chapter.get("id"))
        chapter.setdefault("previous_chapter", "")
        default_read = (
            chapter_read_number is not None
            and chapter_number is not None
            and chapter_number <= chapter_read_number
        )
        chapter["read"] = overrides.get(chapter_key, default_read)
    return chapters


def fill_missing_manga_chapters(chapters, manga):
    numbered_chapters = [
        chapter for chapter in chapters
        if chapter.get("chapter_number") is not None
        and float(chapter["chapter_number"]).is_integer()
    ]
    latest_number = parse_chapter_number(manga.get("latest_chapter") or manga.get("chapters"))
    existing_numbers = {int(chapter["chapter_number"]) for chapter in numbered_chapters}

    if numbered_chapters:
        latest_number = max(
            latest_number or 0,
            max(chapter["chapter_number"] for chapter in numbered_chapters),
        )
    if not latest_number or latest_number < 1:
        return chapters

    latest_number = int(latest_number)
    filled = list(chapters)
    for chapter_number in range(1, latest_number + 1):
        if chapter_number in existing_numbers:
            continue
        filled.append(
            {
                "id": f"missing:{manga['id']}:{chapter_number}",
                "chapter": str(chapter_number),
                "chapter_number": float(chapter_number),
                "title": "Sin titulo disponible",
                "volume": None,
                "language": "",
                "group": f"No aparece en {manga.get('network') or 'la fuente'}",
                "publish_at": None,
                "pages": 0,
                "official_url": None,
                "source": "missing",
                "missing": True,
            }
        )

    return sorted(
        filled,
        key=lambda chapter: (
            chapter.get("chapter_number") is None,
            chapter.get("chapter_number") or 0,
            chapter.get("chapter") or "",
        ),
    )


def get_show_episodes(show_id, refresh=False):
    return get_tmdb_episodes(show_id, refresh=refresh)


def get_tmdb_episodes(show_id, refresh=False):
    tmdb_id = resolve_tmdb_id_for_show_id(show_id)
    if not tmdb_id:
        return []

    def load_episodes():
        raw_show = get_tmdb_show_from_tmdb_id(tmdb_id, refresh=refresh)
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
        current_app.config["TMDB_CACHE_EPISODES_SECONDS"],
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


def merge_tmdb_candidate(
    candidates,
    candidates_by_id,
    raw_item,
    source_label=None,
    profile_source=None,
    actor_source=None,
):
    if not raw_item.get("id"):
        return None

    show = normalize_tmdb_show(raw_item)
    existing = candidates_by_id.get(show["id"])
    if existing:
        show = existing
    else:
        candidates_by_id[show["id"]] = show
        candidates.append(show)

    if source_label and not show.get("source_label"):
        show["source_label"] = source_label

    if profile_source:
        sources = show.setdefault("profile_sources", [])
        if profile_source["id"] not in {item["id"] for item in sources}:
            sources.append(profile_source)

    if actor_source:
        sources = show.setdefault("actor_sources", [])
        if actor_source["id"] not in {item["id"] for item in sources}:
            sources.append(actor_source)

    return show


def merge_tmdb_movie_candidate(
    candidates,
    candidates_by_id,
    raw_item,
    source_label=None,
    profile_source=None,
    actor_source=None,
):
    if not raw_item.get("id"):
        return None

    movie = normalize_tmdb_movie(raw_item)
    existing = candidates_by_id.get(movie["id"])
    if existing:
        movie = existing
    else:
        candidates_by_id[movie["id"]] = movie
        candidates.append(movie)

    if source_label and not movie.get("source_label"):
        movie["source_label"] = source_label

    if profile_source:
        sources = movie.setdefault("profile_sources", [])
        if profile_source["id"] not in {item["id"] for item in sources}:
            sources.append(profile_source)

    if actor_source:
        sources = movie.setdefault("actor_sources", [])
        if actor_source["id"] not in {item["id"] for item in sources}:
            sources.append(actor_source)

    return movie


def merge_anilist_manga_candidate(
    candidates,
    candidates_by_id,
    raw_item,
    source_label=None,
    profile_source=None,
    actor_source=None,
):
    if not raw_item.get("id") or raw_item.get("isAdult"):
        return None

    manga = normalize_anilist_manga(raw_item)
    existing = candidates_by_id.get(manga["id"])
    if existing:
        manga = existing
    else:
        candidates_by_id[manga["id"]] = manga
        candidates.append(manga)

    if source_label and not manga.get("source_label"):
        manga["source_label"] = source_label

    if profile_source:
        sources = manga.setdefault("profile_sources", [])
        if profile_source["id"] not in {item["id"] for item in sources}:
            sources.append(profile_source)

    if actor_source:
        sources = manga.setdefault("actor_sources", [])
        if actor_source["id"] not in {item["id"] for item in sources}:
            sources.append(actor_source)

    return manga


def merge_normalized_manga_candidate(
    candidates,
    candidates_by_id,
    manga,
    source_label=None,
    actor_source=None,
):
    identity = (
        f"comick:{manga.get('comick_id')}" if manga.get("comick_id")
        else f"mangadex:{manga.get('mangadex_id')}" if manga.get("mangadex_id")
        else f"anilist:{manga.get('anilist_id')}" if manga.get("anilist_id")
        else f"title:{show_signature(manga)}"
    )
    existing = candidates_by_id.get(identity)
    if existing:
        manga = existing
    else:
        candidates_by_id[identity] = manga
        candidates.append(manga)

    if source_label and not manga.get("source_label"):
        manga["source_label"] = source_label
    if actor_source:
        sources = manga.setdefault("actor_sources", [])
        if actor_source["id"] not in {item["id"] for item in sources}:
            sources.append(actor_source)
    return manga


def merge_mangadex_author_candidates(candidates, candidates_by_id, author, source_shows=None):
    author_source = {
        "id": author["id"],
        "name": author["name"],
        "source_shows": source_shows or [],
    }
    for raw_item in get_mangadex_author_manga_raw(author["id"]):
        manga = normalize_mangadex_manga(raw_item)
        manga["author_role"] = raw_item.get("staff_role") or ""
        merge_normalized_manga_candidate(
            candidates,
            candidates_by_id,
            manga,
            source_label=f"MangaDex por {author['name']}",
            actor_source=author_source,
        )


def canonical_manga_recommendation_candidates(candidates, saved_mangas):
    saved_signatures = {
        signature
        for manga in saved_mangas
        for signature in manga_title_identity_keys(manga)
    }
    canonical = []
    by_signature = {}
    for manga in candidates:
        signatures = manga_title_identity_keys(manga)
        if signatures & saved_signatures:
            continue
        existing = next(
            (by_signature[item] for item in signatures if item in by_signature),
            None,
        )
        if not existing:
            for item in signatures:
                by_signature[item] = manga
            canonical.append(manga)
            continue
        for item in signatures:
            by_signature.setdefault(item, existing)
        for field in ("profile_sources", "actor_sources"):
            current_items = existing.setdefault(field, [])
            current_ids = {item.get("id") for item in current_items}
            for item in manga.get(field) or []:
                if item.get("id") not in current_ids:
                    current_items.append(item)
                    current_ids.add(item.get("id"))
        if not existing.get("source_label") and manga.get("source_label"):
            existing["source_label"] = manga["source_label"]
    return canonical


def manga_title_identity_keys(manga):
    year = (manga.get("premiered") or "")[:4]
    keys = {
        (fold_search_text(title).casefold(), year)
        for title in (manga.get("name"), manga.get("original_name"))
        if title
    }
    return keys or {(str(manga.get("id") or ""), year)}


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


def get_tmdb_actor_candidates(show_states):
    if not is_tmdb_enabled():
        return []

    candidates = []
    candidates_by_id = {}
    source_shows = [
        show for show in show_states
        if show.get("watched_count") or show.get("completed")
    ][: current_app.config["ACTOR_RECOMMENDATION_SOURCE_LIMIT"]]

    for source in source_shows:
        for actor in get_show_cast(source["id"], limit=current_app.config["ACTOR_RECOMMENDATION_CAST_PER_SHOW"]):
            actor_source = {
                "id": actor["id"],
                "name": actor["name"],
                "source_shows": [source["name"]],
            }
            for raw_item in get_person_tv_credits_raw(actor["id"]):
                merge_tmdb_candidate(
                    candidates,
                    candidates_by_id,
                    raw_item,
                    source_label=f"TMDb por {actor['name']}",
                    actor_source=actor_source,
                )

    return candidates


def get_tmdb_movie_profile_candidates(movie_states):
    if not is_tmdb_enabled():
        return []

    candidates = []
    candidates_by_id = {}

    def add_many(raw_items, source_label=None, source=None, relation=None):
        for raw_item in raw_items:
            if not raw_item.get("id"):
                continue
            movie = normalize_tmdb_movie(raw_item)
            existing = candidates_by_id.get(movie["id"])
            if existing:
                movie = existing
            else:
                candidates_by_id[movie["id"]] = movie
                candidates.append(movie)

            if source_label:
                movie["source_label"] = source_label
            if source:
                profile_source = {
                    "id": source["id"],
                    "name": source["name"],
                    "relation": relation or "recommendation",
                }
                sources = movie.setdefault("profile_sources", [])
                if profile_source["id"] not in {item["id"] for item in sources}:
                    sources.append(profile_source)

    try:
        trending = cached_json(
            "tmdb:trending:movie:week",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_trending_movies("week"),
        )
        add_many(trending, source_label="TMDb tendencias")
    except TMDbError:
        pass

    source_movies = [movie for movie in movie_states if movie.get("watched")][:5]
    for source in source_movies:
        tmdb_id = source.get("tmdb_id")
        if not tmdb_id:
            tmdb_id = find_tmdb_id_for_movie(source)
        if not tmdb_id:
            continue

        try:
            recommended = cached_json(
                f"tmdb:movie:recommendations:{tmdb_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda movie_id=tmdb_id: get_tmdb_client().get_movie_recommendations(movie_id),
            )
            similar = cached_json(
                f"tmdb:movie:similar:{tmdb_id}",
                current_app.config["TMDB_CACHE_SECONDS"],
                lambda movie_id=tmdb_id: get_tmdb_client().get_movie_similar(movie_id),
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


def get_tmdb_movie_actor_candidates(movie_states):
    if not is_tmdb_enabled():
        return []

    candidates = []
    candidates_by_id = {}
    source_movies = [
        movie for movie in movie_states
        if movie.get("watched")
    ][: current_app.config["ACTOR_RECOMMENDATION_SOURCE_LIMIT"]]

    for source in source_movies:
        for actor in get_movie_cast(source["id"], limit=current_app.config["ACTOR_RECOMMENDATION_CAST_PER_SHOW"]):
            actor_source = {
                "id": actor["id"],
                "name": actor["name"],
                "source_shows": [source["name"]],
            }
            for raw_item in get_person_movie_credits_raw(actor["id"]):
                merge_tmdb_movie_candidate(
                    candidates,
                    candidates_by_id,
                    raw_item,
                    source_label=f"TMDb por {actor['name']}",
                    actor_source=actor_source,
                )

    return candidates


def get_anilist_manga_profile_candidates(manga_states):
    if not is_anilist_enabled():
        return []

    candidates = []
    candidates_by_id = {}

    try:
        trending = cached_json(
            "anilist:trending:manga",
            current_app.config["ANILIST_CACHE_SECONDS"],
            lambda: get_anilist_client().get_trending_manga(),
        )
        for raw_item in trending:
            merge_anilist_manga_candidate(
                candidates,
                candidates_by_id,
                raw_item,
                source_label="AniList tendencias",
            )
    except AniListError:
        pass

    source_mangas = [
        manga for manga in manga_states
        if manga.get("watched_count") or manga.get("completed")
    ][:5]
    for source in source_mangas:
        anilist_id = source.get("anilist_id")
        if not anilist_id:
            anilist_id = find_anilist_id_for_manga(source)
        if not anilist_id:
            continue

        try:
            recommended = cached_json(
                f"anilist:manga:recommendations:{anilist_id}",
                current_app.config["ANILIST_CACHE_SECONDS"],
                lambda manga_id=anilist_id: get_anilist_client().get_manga_recommendations(manga_id),
            )
        except AniListError:
            continue

        for raw_item in recommended:
            merge_anilist_manga_candidate(
                candidates,
                candidates_by_id,
                raw_item,
                source_label=f"AniList por {source['name']}",
                profile_source={
                    "id": source["id"],
                    "name": source["name"],
                    "relation": "recommendation",
                },
            )

    return candidates


def get_anilist_manga_author_candidates(manga_states):
    candidates = []
    candidates_by_id = {}
    source_mangas = [
        manga for manga in manga_states
        if manga.get("watched_count") or manga.get("completed")
    ][: current_app.config["AUTHOR_RECOMMENDATION_SOURCE_LIMIT"]]

    for source in source_mangas:
        for author in get_manga_authors(source["id"], limit=current_app.config["AUTHOR_RECOMMENDATION_STAFF_PER_MANGA"]):
            if isinstance(author.get("id"), int) and is_anilist_enabled():
                author_source = {
                    "id": author["id"],
                    "name": author["name"],
                    "source_shows": [source["name"]],
                }
                for raw_item in get_staff_manga_raw(author["id"]):
                    merge_anilist_manga_candidate(
                        candidates,
                        candidates_by_id,
                        raw_item,
                        source_label=f"AniList por {author['name']}",
                        actor_source=author_source,
                    )
            elif is_mangadex_enabled():
                merge_mangadex_author_candidates(
                    candidates,
                    candidates_by_id,
                    author,
                    source_shows=[source["name"]],
                )

    return candidates


def get_manual_author_manga_recommendation_context(author_id=None, author_query="", author_source=""):
    author_search_results = []
    selected_author = None
    use_mangadex = author_source == "mangadex" or bool(
        author_id and re.fullmatch(r"[0-9a-f-]{36}", str(author_id), flags=re.IGNORECASE)
    )

    if author_id and use_mangadex:
        selected_author = get_mangadex_author(str(author_id))
    elif author_id:
        selected_author = get_anilist_staff(int(author_id))
    elif author_query:
        author_search_results = search_anilist_staff(author_query)
        if author_search_results:
            selected_author = author_search_results[0]

    if not selected_author:
        return None, [], author_search_results

    candidates = []
    candidates_by_id = {}
    if use_mangadex:
        merge_mangadex_author_candidates(candidates, candidates_by_id, selected_author)
        return selected_author, candidates, author_search_results

    author_source = {
        "id": selected_author["id"],
        "name": selected_author["name"],
        "source_shows": [],
    }
    for raw_item in get_staff_manga_raw(selected_author["id"]):
        merge_anilist_manga_candidate(
            candidates,
            candidates_by_id,
            raw_item,
            source_label=f"AniList por {selected_author['name']}",
            actor_source=author_source,
        )

    return selected_author, candidates, author_search_results


def get_manual_actor_recommendation_context(actor_id=None, actor_query=""):
    actor_search_results = []
    selected_actor = None

    if actor_id:
        selected_actor = get_tmdb_person(actor_id)
    elif actor_query:
        actor_search_results = search_tmdb_people(actor_query)
        if actor_search_results:
            selected_actor = actor_search_results[0]

    if not selected_actor:
        return None, [], actor_search_results

    candidates = []
    candidates_by_id = {}
    actor_source = {
        "id": selected_actor["id"],
        "name": selected_actor["name"],
        "source_shows": [],
    }
    for raw_item in get_person_tv_credits_raw(selected_actor["id"]):
        merge_tmdb_candidate(
            candidates,
            candidates_by_id,
            raw_item,
            source_label=f"TMDb por {selected_actor['name']}",
            actor_source=actor_source,
        )

    return selected_actor, candidates, actor_search_results


def get_manual_actor_movie_recommendation_context(actor_id=None, actor_query=""):
    actor_search_results = []
    selected_actor = None

    if actor_id:
        selected_actor = get_tmdb_person(actor_id)
    elif actor_query:
        actor_search_results = search_tmdb_people(actor_query)
        if actor_search_results:
            selected_actor = actor_search_results[0]

    if not selected_actor:
        return None, [], actor_search_results

    candidates = []
    candidates_by_id = {}
    actor_source = {
        "id": selected_actor["id"],
        "name": selected_actor["name"],
        "source_shows": [],
    }
    for raw_item in get_person_movie_credits_raw(selected_actor["id"]):
        merge_tmdb_movie_candidate(
            candidates,
            candidates_by_id,
            raw_item,
            source_label=f"TMDb por {selected_actor['name']}",
            actor_source=actor_source,
        )

    return selected_actor, candidates, actor_search_results


def search_tmdb_people(query):
    if not query or not is_tmdb_enabled():
        return []

    raw_results = cached_json(
        f"tmdb:search:person:{query.casefold()}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().search_people(query),
    )
    people = []
    for raw_person in raw_results[:8]:
        if raw_person.get("id"):
            people.append(normalize_tmdb_person(raw_person))
    return people


def get_tmdb_person(person_id, refresh=False):
    raw_person = cached_json(
        f"tmdb:person:{person_id}",
        current_app.config["TMDB_CACHE_SECONDS"],
        lambda: get_tmdb_client().get_person(person_id),
        refresh=refresh,
    )
    if not raw_person or not raw_person.get("id"):
        raise TMDbError("No se encontro el actor en TMDb.")
    return normalize_tmdb_person(raw_person)


def search_anilist_staff(query):
    if not query or not is_anilist_enabled():
        return []

    raw_results = cached_json(
        f"anilist:search:staff:{query.casefold()}",
        current_app.config["ANILIST_CACHE_SECONDS"],
        lambda: get_anilist_client().search_staff(query),
    )
    people = []
    for raw_staff in raw_results[:8]:
        if raw_staff.get("id"):
            people.append(normalize_anilist_staff_member(raw_staff))
    return people


def get_anilist_staff(staff_id, refresh=False):
    raw_staff = cached_json(
        f"anilist:staff:{staff_id}",
        current_app.config["ANILIST_CACHE_SECONDS"],
        lambda: get_anilist_client().get_staff(staff_id),
        refresh=refresh,
    )
    if not raw_staff or not raw_staff.get("id"):
        raise AniListError("No se encontro el autor en AniList.")
    return normalize_anilist_staff_member(raw_staff)


def get_mangadex_author(author_id, refresh=False):
    raw_author = cached_json(
        f"mangadex:author:{author_id}",
        current_app.config["MANGADEX_CACHE_SECONDS"],
        lambda: get_mangadex_client().get_author(author_id),
        refresh=refresh,
    )
    if not raw_author or not raw_author.get("id"):
        raise MangaDexError("No se encontro el autor en MangaDex.")
    return normalize_mangadex_author(raw_author)


def is_manga_author_role(role):
    normalized_role = (role or "").casefold()
    return any(
        keyword in normalized_role
        for keyword in ("story", "art", "creator", "manga")
    )


def get_manga_authors(manga_id, limit=12):
    saved_manga = store.get_manga(manga_id)
    if saved_manga and saved_manga.get("mangadex_id") and is_mangadex_enabled():
        return get_mangadex_manga_authors(saved_manga["mangadex_id"], limit=limit)

    if not is_anilist_enabled():
        return []
    anilist_id = resolve_anilist_id_for_manga_id(manga_id)
    if not anilist_id:
        return []

    try:
        raw_manga = cached_json(
            f"anilist:manga:{anilist_id}",
            current_app.config["ANILIST_CACHE_SECONDS"],
            lambda: get_anilist_client().get_manga(anilist_id),
        )
    except AniListError:
        return []

    edges = ((raw_manga.get("staff") or {}).get("edges")) or []
    authors = [
        normalize_anilist_staff_member(edge)
        for edge in edges
        if (edge.get("node") or {}).get("id")
    ]
    preferred = [author for author in authors if is_manga_author_role(author.get("role"))]
    if preferred:
        authors = preferred

    deduped = []
    seen_ids = set()
    for author in authors:
        if author["id"] in seen_ids:
            continue
        seen_ids.add(author["id"])
        deduped.append(author)
    return deduped[:limit]


def get_mangadex_manga_authors(mangadex_id, limit=12):
    try:
        raw_manga = cached_json(
            f"mangadex:manga:{mangadex_id}",
            current_app.config["MANGADEX_CACHE_SECONDS"],
            lambda: get_mangadex_client().get_manga(mangadex_id),
        )
    except MangaDexError:
        return []

    authors = []
    seen_ids = set()
    for relationship in raw_manga.get("relationships") or []:
        if relationship.get("type") not in {"author", "artist"}:
            continue
        if relationship.get("id") in seen_ids:
            continue
        seen_ids.add(relationship.get("id"))
        authors.append(normalize_mangadex_author(relationship))
    return authors[:limit]


def get_show_cast(show_id, limit=12):
    if not is_tmdb_enabled():
        return []
    tmdb_id = resolve_tmdb_id_for_show_id(show_id)
    if not tmdb_id:
        return []

    try:
        credits = cached_json(
            f"tmdb:tv:credits:{tmdb_id}",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_tv_credits(tmdb_id),
        )
    except TMDbError:
        return []

    cast = [
        normalize_tmdb_cast_member(member)
        for member in credits.get("cast") or []
        if member.get("id")
    ]
    cast.sort(key=lambda member: (member["order"], -member["popularity"], member["name"]))
    return cast[:limit]


def get_movie_cast(movie_id, limit=12):
    if not is_tmdb_enabled():
        return []
    tmdb_id = resolve_tmdb_id_for_movie_id(movie_id)
    if not tmdb_id:
        return []

    try:
        credits = cached_json(
            f"tmdb:movie:credits:{tmdb_id}",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_movie_credits(tmdb_id),
        )
    except TMDbError:
        return []

    cast = [
        normalize_tmdb_cast_member(member)
        for member in credits.get("cast") or []
        if member.get("id")
    ]
    cast.sort(key=lambda member: (member["order"], -member["popularity"], member["name"]))
    return cast[:limit]


def get_person_tv_credits_raw(person_id):
    try:
        credits = cached_json(
            f"tmdb:person:tv-credits:{person_id}",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_person_tv_credits(person_id),
        )
    except TMDbError:
        return []

    raw_items = [
        item for item in credits.get("cast") or []
        if item.get("id") and item.get("first_air_date")
    ]
    raw_items.sort(
        key=lambda item: (
            -(item.get("vote_average") or 0),
            -(item.get("popularity") or 0),
            item.get("name") or "",
        )
    )
    return raw_items[:48]


def get_person_movie_credits_raw(person_id):
    try:
        credits = cached_json(
            f"tmdb:person:movie-credits:{person_id}",
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().get_person_movie_credits(person_id),
        )
    except TMDbError:
        return []

    raw_items = [
        item for item in credits.get("cast") or []
        if item.get("id") and item.get("release_date")
    ]
    raw_items.sort(
        key=lambda item: (
            -(item.get("vote_average") or 0),
            -(item.get("popularity") or 0),
            item.get("title") or item.get("name") or "",
        )
    )
    return raw_items[:48]


def get_person_tv_recommendations(person):
    shows = []
    seen_ids = set()
    for raw_item in get_person_tv_credits_raw(person["id"]):
        show = normalize_tmdb_show(raw_item)
        if show["id"] in seen_ids:
            continue
        seen_ids.add(show["id"])
        show["actor_character"] = raw_item.get("character") or ""
        shows.append(show)
    return shows


def get_person_movie_recommendations(person):
    movies = []
    seen_ids = set()
    for raw_item in get_person_movie_credits_raw(person["id"]):
        movie = normalize_tmdb_movie(raw_item)
        if movie["id"] in seen_ids:
            continue
        seen_ids.add(movie["id"])
        movie["actor_character"] = raw_item.get("character") or ""
        movies.append(movie)
    return movies


def get_staff_manga_raw(staff_id):
    try:
        raw_items = cached_json(
            f"anilist:staff:manga:{staff_id}",
            current_app.config["ANILIST_CACHE_SECONDS"],
            lambda: get_anilist_client().get_staff_manga(staff_id),
        )
    except AniListError:
        return []

    raw_items = [
        item for item in raw_items
        if item.get("id") and not item.get("isAdult")
    ]
    raw_items.sort(
        key=lambda item: (
            -(item.get("averageScore") or 0),
            item.get("title", {}).get("english")
            or item.get("title", {}).get("romaji")
            or "",
        )
    )
    return raw_items[:48]


def get_staff_manga_recommendations(author):
    mangas = []
    seen_ids = set()
    for raw_item in get_staff_manga_raw(author["id"]):
        manga = normalize_anilist_manga(raw_item)
        if manga["id"] in seen_ids:
            continue
        seen_ids.add(manga["id"])
        manga["author_role"] = raw_item.get("staff_role") or ""
        mangas.append(manga)
    return mangas


def get_mangadex_author_manga_raw(author_id):
    if not is_mangadex_enabled():
        return []

    raw_items = []
    for relationship, role_label in (("author", "Autor"), ("artist", "Arte")):
        try:
            items = cached_json(
                f"mangadex:author:manga:{relationship}:{author_id}",
                current_app.config["MANGADEX_CACHE_SECONDS"],
                lambda relationship=relationship: get_mangadex_client().get_author_manga(
                    author_id,
                    relationship=relationship,
                ),
            )
        except MangaDexError:
            continue

        for item in items:
            raw_items.append({**item, "staff_role": role_label})

    raw_items.sort(
        key=lambda item: (
            -(parse_mangadex_number((item.get("attributes") or {}).get("lastChapter")) or 0),
            (item.get("attributes") or {}).get("title", {}).get("en")
            or (item.get("attributes") or {}).get("title", {}).get("ja-ro")
            or "",
        )
    )
    return raw_items[:48]


def get_mangadex_author_manga_recommendations(author):
    mangas = []
    seen_ids = set()
    for raw_item in get_mangadex_author_manga_raw(author["id"]):
        mangadex_id = raw_item.get("id")
        if not mangadex_id or mangadex_id in seen_ids:
            continue
        seen_ids.add(mangadex_id)
        manga = normalize_mangadex_manga(raw_item)
        manga["author_role"] = raw_item.get("staff_role") or ""
        mangas.append(manga)
    return mangas


def recommendation_from_form(show_id):
    try:
        show = get_show_for_storage(show_id)
    except TMDbError:
        show = None

    if show:
        return show

    return {
        "id": show_id,
        "name": request.form.get("name") or "Serie recomendada",
        "original_name": request.form.get("original_name"),
        "source": request.form.get("source") or "tmdb",
    }


def movie_recommendation_from_form(movie_id):
    try:
        movie = get_movie_for_storage(movie_id)
    except TMDbError:
        movie = None

    if movie:
        return movie

    return {
        "id": movie_id,
        "name": request.form.get("name") or "Pelicula recomendada",
        "original_name": request.form.get("original_name"),
        "source": request.form.get("source") or "tmdb",
    }


def manga_recommendation_from_form(manga_id):
    try:
        manga = get_manga_for_storage(manga_id)
    except AniListError:
        manga = None

    if manga:
        return manga

    return {
        "id": manga_id,
        "name": request.form.get("name") or "Manga recomendado",
        "original_name": request.form.get("original_name"),
        "source": request.form.get("source") or "anilist",
    }


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


def find_tmdb_id_for_movie(movie):
    movie_id = movie.get("id")
    if movie_id and is_tmdb_movie_id(movie_id):
        return tmdb_id_from_movie_id(movie_id)

    query = movie.get("original_name") or movie.get("name")
    if not query:
        return None

    cache_key = f"tmdb:resolve:movie:{query.lower()}:{(movie.get('premiered') or '')[:4]}"
    try:
        results = cached_json(
            cache_key,
            current_app.config["TMDB_CACHE_SECONDS"],
            lambda: get_tmdb_client().search_movie(query),
        )
    except TMDbError:
        return None

    wanted_year = (movie.get("premiered") or "")[:4]
    for result in results:
        result_year = (result.get("release_date") or "")[:4]
        if not wanted_year or result_year == wanted_year:
            return result.get("id")
    return results[0].get("id") if results else None


def find_anilist_id_for_manga(manga):
    manga_id = manga.get("id")
    if manga_id and is_anilist_manga_id(manga_id):
        return anilist_id_from_manga_id(manga_id)

    query = manga.get("original_name") or manga.get("name")
    if not query:
        return None

    cache_key = f"anilist:resolve:manga:{query.lower()}:{(manga.get('premiered') or '')[:4]}"
    try:
        results = cached_json(
            cache_key,
            current_app.config["ANILIST_CACHE_SECONDS"],
            lambda: get_anilist_client().search_manga(query),
        )
    except AniListError:
        return None

    wanted_year = (manga.get("premiered") or "")[:4]
    for result in results:
        result_year = ""
        start_date = result.get("startDate") or {}
        if start_date.get("year"):
            result_year = str(start_date["year"])
        if not wanted_year or result_year == wanted_year:
            return result.get("id")
    return results[0].get("id") if results else None
