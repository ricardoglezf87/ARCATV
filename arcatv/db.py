import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT,
    premiered TEXT,
    ended TEXT,
    status TEXT,
    language TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    image_url TEXT,
    official_url TEXT,
    network TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_episodes (
    episode_id INTEGER PRIMARY KEY,
    show_id INTEGER NOT NULL,
    season INTEGER,
    number INTEGER,
    name TEXT,
    watched_at TEXT NOT NULL,
    FOREIGN KEY(show_id) REFERENCES shows(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_recommendations (
    show_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT,
    source TEXT,
    rejected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT,
    premiered TEXT,
    status TEXT,
    language TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    image_url TEXT,
    official_url TEXT,
    network TEXT,
    runtime INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watched_movies (
    movie_id INTEGER PRIMARY KEY,
    watched_at TEXT NOT NULL,
    FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rejected_movie_recommendations (
    movie_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT,
    source TEXT,
    rejected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mangas (
    id INTEGER PRIMARY KEY,
    comick_id TEXT UNIQUE,
    mangadex_id TEXT UNIQUE,
    name TEXT NOT NULL,
    original_name TEXT,
    premiered TEXT,
    status TEXT,
    language TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    image_url TEXT,
    official_url TEXT,
    network TEXT,
    chapters INTEGER,
    volumes INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS read_mangas (
    manga_id INTEGER PRIMARY KEY,
    volumes_read INTEGER NOT NULL DEFAULT 0,
    chapter_read TEXT,
    read_at TEXT NOT NULL,
    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manga_chapter_read_overrides (
    manga_id INTEGER NOT NULL,
    chapter_key TEXT NOT NULL,
    is_read INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(manga_id, chapter_key),
    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manga_chapter_downloads (
    manga_id INTEGER NOT NULL,
    chapter_key TEXT NOT NULL,
    source_url TEXT,
    folder_name TEXT NOT NULL,
    panel_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    downloaded_at TEXT NOT NULL,
    PRIMARY KEY(manga_id, chapter_key),
    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manga_reader_progress (
    manga_id INTEGER NOT NULL,
    chapter_key TEXT NOT NULL,
    current_panel INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY(manga_id, chapter_key),
    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manga_download_preferences (
    manga_id INTEGER PRIMARY KEY,
    base_url TEXT,
    manga_oni_url TEXT,
    split_panels INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rejected_manga_recommendations (
    manga_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    original_name TEXT,
    source TEXT,
    rejected_at TEXT NOT NULL
);
"""


def get_db():
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(database_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    ensure_columns(db)
    db.commit()


def ensure_columns(db):
    show_columns = {row["name"] for row in db.execute("PRAGMA table_info(shows)").fetchall()}
    if "original_name" not in show_columns:
        db.execute("ALTER TABLE shows ADD COLUMN original_name TEXT")

    read_manga_columns = {row["name"] for row in db.execute("PRAGMA table_info(read_mangas)").fetchall()}
    if read_manga_columns and "volumes_read" not in read_manga_columns:
        db.execute("ALTER TABLE read_mangas ADD COLUMN volumes_read INTEGER NOT NULL DEFAULT 0")
    if read_manga_columns and "chapter_read" not in read_manga_columns:
        db.execute("ALTER TABLE read_mangas ADD COLUMN chapter_read TEXT")

    manga_columns = {row["name"] for row in db.execute("PRAGMA table_info(mangas)").fetchall()}
    if manga_columns and "comick_id" not in manga_columns:
        db.execute("ALTER TABLE mangas ADD COLUMN comick_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mangas_comick_id ON mangas(comick_id)")
    if manga_columns and "mangadex_id" not in manga_columns:
        db.execute("ALTER TABLE mangas ADD COLUMN mangadex_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mangas_mangadex_id ON mangas(mangadex_id)")

    download_preference_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(manga_download_preferences)").fetchall()
    }
    if download_preference_columns and "manga_oni_url" not in download_preference_columns:
        db.execute("ALTER TABLE manga_download_preferences ADD COLUMN manga_oni_url TEXT")
    if download_preference_columns and "split_panels" not in download_preference_columns:
        db.execute("ALTER TABLE manga_download_preferences ADD COLUMN split_panels INTEGER NOT NULL DEFAULT 0")


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def row_to_show(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "original_name": row["original_name"],
        "premiered": row["premiered"],
        "ended": row["ended"],
        "status": row["status"],
        "language": row["language"],
        "genres": json.loads(row["genres"] or "[]"),
        "summary": row["summary"],
        "image_url": row["image_url"],
        "official_url": row["official_url"],
        "network": row["network"],
        "updated_at": row["updated_at"],
    }


def row_to_movie(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "original_name": row["original_name"],
        "premiered": row["premiered"],
        "status": row["status"],
        "language": row["language"],
        "genres": json.loads(row["genres"] or "[]"),
        "summary": row["summary"],
        "image_url": row["image_url"],
        "official_url": row["official_url"],
        "network": row["network"],
        "runtime": row["runtime"],
        "updated_at": row["updated_at"],
    }


def row_to_manga(row):
    return {
        "id": row["id"],
        "comick_id": row["comick_id"],
        "mangadex_id": row["mangadex_id"],
        "name": row["name"],
        "original_name": row["original_name"],
        "premiered": row["premiered"],
        "status": row["status"],
        "language": row["language"],
        "genres": json.loads(row["genres"] or "[]"),
        "summary": row["summary"],
        "image_url": row["image_url"],
        "official_url": row["official_url"],
        "network": row["network"],
        "chapters": row["chapters"],
        "volumes": row["volumes"],
        "updated_at": row["updated_at"],
    }


def row_to_manga_download(row):
    return {
        "manga_id": row["manga_id"],
        "chapter_key": row["chapter_key"],
        "source_url": row["source_url"],
        "folder_name": row["folder_name"],
        "panel_count": row["panel_count"] or 0,
        "page_count": row["page_count"] or 0,
        "downloaded_at": row["downloaded_at"],
    }


def row_to_manga_reader_progress(row):
    return {
        "manga_id": row["manga_id"],
        "chapter_key": row["chapter_key"],
        "current_panel": row["current_panel"] or 1,
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


def list_shows():
    rows = get_db().execute("SELECT * FROM shows ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_show(row) for row in rows]


def list_movies():
    rows = get_db().execute("SELECT * FROM movies ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_movie(row) for row in rows]


def list_mangas():
    rows = get_db().execute("SELECT * FROM mangas ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_manga(row) for row in rows]


def get_show_ids():
    rows = get_db().execute("SELECT id FROM shows").fetchall()
    return {row["id"] for row in rows}


def get_movie_ids():
    rows = get_db().execute("SELECT id FROM movies").fetchall()
    return {row["id"] for row in rows}


def get_manga_ids():
    rows = get_db().execute("SELECT id FROM mangas").fetchall()
    return {row["id"] for row in rows}


def get_manga_mangadex_ids():
    rows = get_db().execute("SELECT mangadex_id FROM mangas WHERE mangadex_id IS NOT NULL").fetchall()
    return {row["mangadex_id"] for row in rows}


def get_manga_comick_ids():
    rows = get_db().execute("SELECT comick_id FROM mangas WHERE comick_id IS NOT NULL").fetchall()
    return {row["comick_id"] for row in rows}


def get_manga_comick_id_map():
    rows = get_db().execute("SELECT id, comick_id FROM mangas WHERE comick_id IS NOT NULL").fetchall()
    return {row["comick_id"]: row["id"] for row in rows}


def get_show(show_id):
    row = get_db().execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
    return row_to_show(row) if row else None


def get_movie(movie_id):
    row = get_db().execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    return row_to_movie(row) if row else None


def get_manga(manga_id):
    row = get_db().execute("SELECT * FROM mangas WHERE id = ?", (manga_id,)).fetchone()
    return row_to_manga(row) if row else None


def manga_has_user_state(manga_id):
    db = get_db()
    checks = (
        "SELECT 1 FROM read_mangas WHERE manga_id = ? LIMIT 1",
        "SELECT 1 FROM manga_chapter_read_overrides WHERE manga_id = ? LIMIT 1",
        "SELECT 1 FROM manga_reader_progress WHERE manga_id = ? LIMIT 1",
        "SELECT 1 FROM manga_chapter_downloads WHERE manga_id = ? LIMIT 1",
        "SELECT 1 FROM manga_download_preferences WHERE manga_id = ? LIMIT 1",
    )
    return any(db.execute(query, (manga_id,)).fetchone() for query in checks)


def get_manga_by_mangadex_id(mangadex_id):
    row = get_db().execute("SELECT * FROM mangas WHERE mangadex_id = ?", (mangadex_id,)).fetchone()
    return row_to_manga(row) if row else None


def get_manga_by_comick_id(comick_id):
    row = get_db().execute("SELECT * FROM mangas WHERE comick_id = ?", (comick_id,)).fetchone()
    return row_to_manga(row) if row else None


def upsert_show(show):
    get_db().execute(
        """
        INSERT INTO shows (
            id, name, original_name, premiered, ended, status, language, genres, summary,
            image_url, official_url, network, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            original_name = excluded.original_name,
            premiered = excluded.premiered,
            ended = excluded.ended,
            status = excluded.status,
            language = excluded.language,
            genres = excluded.genres,
            summary = excluded.summary,
            image_url = excluded.image_url,
            official_url = excluded.official_url,
            network = excluded.network,
            updated_at = excluded.updated_at
        """,
        (
            show["id"],
            show["name"],
            show.get("original_name"),
            show.get("premiered"),
            show.get("ended"),
            show.get("status"),
            show.get("language"),
            json.dumps(show.get("genres") or []),
            show.get("summary"),
            show.get("image_url"),
            show.get("official_url"),
            show.get("network"),
            now_iso(),
        ),
    )
    get_db().commit()


def upsert_movie(movie):
    get_db().execute(
        """
        INSERT INTO movies (
            id, name, original_name, premiered, status, language, genres, summary,
            image_url, official_url, network, runtime, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            original_name = excluded.original_name,
            premiered = excluded.premiered,
            status = excluded.status,
            language = excluded.language,
            genres = excluded.genres,
            summary = excluded.summary,
            image_url = excluded.image_url,
            official_url = excluded.official_url,
            network = excluded.network,
            runtime = excluded.runtime,
            updated_at = excluded.updated_at
        """,
        (
            movie["id"],
            movie["name"],
            movie.get("original_name"),
            movie.get("premiered"),
            movie.get("status"),
            movie.get("language"),
            json.dumps(movie.get("genres") or []),
            movie.get("summary"),
            movie.get("image_url"),
            movie.get("official_url"),
            movie.get("network"),
            movie.get("runtime"),
            now_iso(),
        ),
    )
    get_db().commit()


def upsert_manga(manga):
    get_db().execute(
        """
        INSERT INTO mangas (
            id, comick_id, mangadex_id, name, original_name, premiered, status, language, genres, summary,
            image_url, official_url, network, chapters, volumes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            comick_id = excluded.comick_id,
            mangadex_id = excluded.mangadex_id,
            name = excluded.name,
            original_name = excluded.original_name,
            premiered = excluded.premiered,
            status = excluded.status,
            language = excluded.language,
            genres = excluded.genres,
            summary = excluded.summary,
            image_url = excluded.image_url,
            official_url = excluded.official_url,
            network = excluded.network,
            chapters = excluded.chapters,
            volumes = excluded.volumes,
            updated_at = excluded.updated_at
        """,
        (
            manga["id"],
            manga.get("comick_id"),
            manga.get("mangadex_id"),
            manga["name"],
            manga.get("original_name"),
            manga.get("premiered"),
            manga.get("status"),
            manga.get("language"),
            json.dumps(manga.get("genres") or []),
            manga.get("summary"),
            manga.get("image_url"),
            manga.get("official_url"),
            manga.get("network"),
            manga.get("chapters"),
            manga.get("volumes"),
            now_iso(),
        ),
    )
    get_db().commit()


def remove_show(show_id):
    db = get_db()
    db.execute("DELETE FROM watched_episodes WHERE show_id = ?", (show_id,))
    db.execute("DELETE FROM shows WHERE id = ?", (show_id,))
    db.commit()


def remove_movie(movie_id):
    db = get_db()
    db.execute("DELETE FROM watched_movies WHERE movie_id = ?", (movie_id,))
    db.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    db.commit()


def remove_manga(manga_id):
    db = get_db()
    db.execute("DELETE FROM read_mangas WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM manga_chapter_read_overrides WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM manga_reader_progress WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM manga_chapter_downloads WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM manga_download_preferences WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM mangas WHERE id = ?", (manga_id,))
    db.commit()


def get_watched_ids(show_id):
    rows = get_db().execute(
        "SELECT episode_id FROM watched_episodes WHERE show_id = ?",
        (show_id,),
    ).fetchall()
    return {row["episode_id"] for row in rows}


def get_latest_watched_at(show_id):
    row = get_db().execute(
        "SELECT MAX(watched_at) AS latest_watched_at FROM watched_episodes WHERE show_id = ?",
        (show_id,),
    ).fetchone()
    return row["latest_watched_at"] if row else None


def get_movie_watched_at(movie_id):
    row = get_db().execute(
        "SELECT watched_at FROM watched_movies WHERE movie_id = ?",
        (movie_id,),
    ).fetchone()
    return row["watched_at"] if row else None


def get_manga_progress(manga_id):
    row = get_db().execute(
        "SELECT read_at, volumes_read, chapter_read FROM read_mangas WHERE manga_id = ?",
        (manga_id,),
    ).fetchone()
    if not row:
        return {"read_at": None, "volumes_read": 0, "chapter_read": None}
    return {
        "read_at": row["read_at"],
        "volumes_read": row["volumes_read"] or 0,
        "chapter_read": row["chapter_read"],
    }


def get_manga_read_at(manga_id):
    return get_manga_progress(manga_id)["read_at"]


def get_manga_download_base_url(manga_id):
    row = get_db().execute(
        "SELECT base_url FROM manga_download_preferences WHERE manga_id = ?",
        (manga_id,),
    ).fetchone()
    return row["base_url"] if row and row["base_url"] else None


def get_manga_oni_url(manga_id):
    row = get_db().execute(
        "SELECT manga_oni_url FROM manga_download_preferences WHERE manga_id = ?",
        (manga_id,),
    ).fetchone()
    return row["manga_oni_url"] if row and row["manga_oni_url"] else None


def set_manga_download_base_url(manga_id, base_url):
    base_url = (base_url or "").strip().rstrip("/")
    db = get_db()
    if not base_url:
        db.execute(
            "UPDATE manga_download_preferences SET base_url = NULL, updated_at = ? WHERE manga_id = ?",
            (now_iso(), manga_id),
        )
    else:
        db.execute(
            """
            INSERT INTO manga_download_preferences (manga_id, base_url, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(manga_id) DO UPDATE SET
                base_url = excluded.base_url,
                updated_at = excluded.updated_at
            """,
            (manga_id, base_url, now_iso()),
        )
    db.commit()


def set_manga_oni_url(manga_id, manga_oni_url):
    manga_oni_url = (manga_oni_url or "").strip().rstrip("/")
    db = get_db()
    if not manga_oni_url:
        db.execute(
            "UPDATE manga_download_preferences SET manga_oni_url = NULL, updated_at = ? WHERE manga_id = ?",
            (now_iso(), manga_id),
        )
    else:
        db.execute(
            """
            INSERT INTO manga_download_preferences (manga_id, manga_oni_url, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(manga_id) DO UPDATE SET
                manga_oni_url = excluded.manga_oni_url,
                updated_at = excluded.updated_at
            """,
            (manga_id, manga_oni_url, now_iso()),
        )
    db.commit()


def get_manga_split_panels(manga_id):
    row = get_db().execute(
        "SELECT split_panels FROM manga_download_preferences WHERE manga_id = ?",
        (manga_id,),
    ).fetchone()
    return bool(row["split_panels"]) if row and row["split_panels"] is not None else False


def set_manga_split_panels(manga_id, split_panels):
    val = 1 if split_panels else 0
    db = get_db()
    db.execute(
        """
        INSERT INTO manga_download_preferences (manga_id, split_panels, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(manga_id) DO UPDATE SET
            split_panels = excluded.split_panels,
            updated_at = excluded.updated_at
        """,
        (manga_id, val, now_iso()),
    )
    db.commit()


def get_manga_download(manga_id, chapter_key):
    row = get_db().execute(
        """
        SELECT * FROM manga_chapter_downloads
        WHERE manga_id = ? AND chapter_key = ?
        """,
        (manga_id, str(chapter_key)),
    ).fetchone()
    return row_to_manga_download(row) if row else None


def list_manga_downloads(manga_id=None):
    if manga_id is None:
        rows = get_db().execute(
            "SELECT * FROM manga_chapter_downloads ORDER BY manga_id, chapter_key"
        ).fetchall()
    else:
        rows = get_db().execute(
            """
            SELECT * FROM manga_chapter_downloads
            WHERE manga_id = ?
            ORDER BY chapter_key
            """,
            (manga_id,),
        ).fetchall()
    return [row_to_manga_download(row) for row in rows]


def upsert_manga_download(manga_id, chapter_key, source_url, folder_name, panel_count, page_count):
    get_db().execute(
        """
        INSERT INTO manga_chapter_downloads (
            manga_id, chapter_key, source_url, folder_name, panel_count, page_count, downloaded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(manga_id, chapter_key) DO UPDATE SET
            source_url = excluded.source_url,
            folder_name = excluded.folder_name,
            panel_count = excluded.panel_count,
            page_count = excluded.page_count,
            downloaded_at = excluded.downloaded_at
        """,
        (
            manga_id,
            str(chapter_key),
            source_url,
            folder_name,
            int(panel_count or 0),
            int(page_count or 0),
            now_iso(),
        ),
    )
    get_db().commit()


def delete_manga_download(manga_id, chapter_key):
    db = get_db()
    db.execute(
        "DELETE FROM manga_reader_progress WHERE manga_id = ? AND chapter_key = ?",
        (manga_id, str(chapter_key)),
    )
    db.execute(
        "DELETE FROM manga_chapter_downloads WHERE manga_id = ? AND chapter_key = ?",
        (manga_id, str(chapter_key)),
    )
    db.commit()


def get_manga_reader_progress(manga_id, chapter_key):
    row = get_db().execute(
        """
        SELECT * FROM manga_reader_progress
        WHERE manga_id = ? AND chapter_key = ?
        """,
        (manga_id, str(chapter_key)),
    ).fetchone()
    return row_to_manga_reader_progress(row) if row else None


def save_manga_reader_progress(manga_id, chapter_key, current_panel, finished=False):
    current_panel = max(1, int(current_panel or 1))
    finished_at = now_iso() if finished else None
    get_db().execute(
        """
        INSERT INTO manga_reader_progress (
            manga_id, chapter_key, current_panel, updated_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(manga_id, chapter_key) DO UPDATE SET
            current_panel = excluded.current_panel,
            updated_at = excluded.updated_at,
            finished_at = COALESCE(excluded.finished_at, manga_reader_progress.finished_at)
        """,
        (manga_id, str(chapter_key), current_panel, now_iso(), finished_at),
    )
    get_db().commit()


def mark_episode(episode):
    get_db().execute(
        """
        INSERT INTO watched_episodes (episode_id, show_id, season, number, name, watched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(episode_id) DO UPDATE SET
            show_id = excluded.show_id,
            season = excluded.season,
            number = excluded.number,
            name = excluded.name,
            watched_at = excluded.watched_at
        """,
        (
            episode["id"],
            episode["show_id"],
            episode.get("season"),
            episode.get("number"),
            episode.get("name"),
            now_iso(),
        ),
    )
    get_db().commit()


def mark_movie_watched(movie_id):
    get_db().execute(
        """
        INSERT INTO watched_movies (movie_id, watched_at)
        VALUES (?, ?)
        ON CONFLICT(movie_id) DO UPDATE SET
            watched_at = excluded.watched_at
        """,
        (movie_id, now_iso()),
    )
    get_db().commit()


def mark_manga_read(manga_id, volumes_read=1):
    volumes_read = max(1, int(volumes_read or 1))
    get_db().execute(
        """
        INSERT INTO read_mangas (manga_id, volumes_read, read_at)
        VALUES (?, ?, ?)
        ON CONFLICT(manga_id) DO UPDATE SET
            volumes_read = excluded.volumes_read,
            read_at = excluded.read_at
        """,
        (manga_id, volumes_read, now_iso()),
    )
    get_db().commit()


def mark_manga_chapter_read(manga_id, chapter_read):
    get_db().execute(
        """
        INSERT INTO read_mangas (manga_id, volumes_read, chapter_read, read_at)
        VALUES (?, 0, ?, ?)
        ON CONFLICT(manga_id) DO UPDATE SET
            chapter_read = excluded.chapter_read,
            read_at = excluded.read_at
        """,
        (manga_id, str(chapter_read), now_iso()),
    )
    get_db().commit()


def get_manga_chapter_read_override(manga_id, chapter_key):
    row = get_db().execute(
        """
        SELECT is_read FROM manga_chapter_read_overrides
        WHERE manga_id = ? AND chapter_key = ?
        """,
        (manga_id, str(chapter_key)),
    ).fetchone()
    return bool(row["is_read"]) if row else None


def list_manga_chapter_read_overrides(manga_id):
    rows = get_db().execute(
        """
        SELECT chapter_key, is_read FROM manga_chapter_read_overrides
        WHERE manga_id = ?
        """,
        (manga_id,),
    ).fetchall()
    return {row["chapter_key"]: bool(row["is_read"]) for row in rows}


def set_manga_chapter_read_override(manga_id, chapter_key, is_read):
    get_db().execute(
        """
        INSERT INTO manga_chapter_read_overrides (manga_id, chapter_key, is_read, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(manga_id, chapter_key) DO UPDATE SET
            is_read = excluded.is_read,
            updated_at = excluded.updated_at
        """,
        (manga_id, str(chapter_key), 1 if is_read else 0, now_iso()),
    )
    get_db().commit()


def delete_manga_chapter_read_override(manga_id, chapter_key):
    get_db().execute(
        "DELETE FROM manga_chapter_read_overrides WHERE manga_id = ? AND chapter_key = ?",
        (manga_id, str(chapter_key)),
    )
    get_db().commit()


def clear_manga_chapter_read_overrides(manga_id):
    get_db().execute(
        "DELETE FROM manga_chapter_read_overrides WHERE manga_id = ?",
        (manga_id,),
    )
    get_db().commit()


def unmark_episode(episode_id):
    get_db().execute("DELETE FROM watched_episodes WHERE episode_id = ?", (episode_id,))
    get_db().commit()


def unmark_movie_watched(movie_id):
    get_db().execute("DELETE FROM watched_movies WHERE movie_id = ?", (movie_id,))
    get_db().commit()


def unmark_manga_read(manga_id):
    db = get_db()
    db.execute("DELETE FROM read_mangas WHERE manga_id = ?", (manga_id,))
    db.execute("DELETE FROM manga_chapter_read_overrides WHERE manga_id = ?", (manga_id,))
    db.commit()


def clear_show_progress(show_id):
    get_db().execute("DELETE FROM watched_episodes WHERE show_id = ?", (show_id,))
    get_db().commit()


def reject_recommendation(show):
    get_db().execute(
        """
        INSERT INTO rejected_recommendations (
            show_id, name, original_name, source, rejected_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(show_id) DO UPDATE SET
            name = excluded.name,
            original_name = excluded.original_name,
            source = excluded.source,
            rejected_at = excluded.rejected_at
        """,
        (
            show["id"],
            show.get("name") or "Sin título",
            show.get("original_name"),
            show.get("source"),
            now_iso(),
        ),
    )
    get_db().commit()


def reject_movie_recommendation(movie):
    get_db().execute(
        """
        INSERT INTO rejected_movie_recommendations (
            movie_id, name, original_name, source, rejected_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(movie_id) DO UPDATE SET
            name = excluded.name,
            original_name = excluded.original_name,
            source = excluded.source,
            rejected_at = excluded.rejected_at
        """,
        (
            movie["id"],
            movie.get("name") or "Sin titulo",
            movie.get("original_name"),
            movie.get("source"),
            now_iso(),
        ),
    )
    get_db().commit()


def reject_manga_recommendation(manga):
    get_db().execute(
        """
        INSERT INTO rejected_manga_recommendations (
            manga_id, name, original_name, source, rejected_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(manga_id) DO UPDATE SET
            name = excluded.name,
            original_name = excluded.original_name,
            source = excluded.source,
            rejected_at = excluded.rejected_at
        """,
        (
            manga["id"],
            manga.get("name") or "Sin titulo",
            manga.get("original_name"),
            manga.get("source"),
            now_iso(),
        ),
    )
    get_db().commit()


def restore_recommendation(show_id):
    get_db().execute(
        "DELETE FROM rejected_recommendations WHERE show_id = ?",
        (show_id,),
    )
    get_db().commit()


def restore_movie_recommendation(movie_id):
    get_db().execute(
        "DELETE FROM rejected_movie_recommendations WHERE movie_id = ?",
        (movie_id,),
    )
    get_db().commit()


def restore_manga_recommendation(manga_id):
    get_db().execute(
        "DELETE FROM rejected_manga_recommendations WHERE manga_id = ?",
        (manga_id,),
    )
    get_db().commit()


def get_rejected_recommendation_ids():
    rows = get_db().execute("SELECT show_id FROM rejected_recommendations").fetchall()
    return {row["show_id"] for row in rows}


def get_rejected_movie_recommendation_ids():
    rows = get_db().execute("SELECT movie_id FROM rejected_movie_recommendations").fetchall()
    return {row["movie_id"] for row in rows}


def get_rejected_manga_recommendation_ids():
    rows = get_db().execute("SELECT manga_id FROM rejected_manga_recommendations").fetchall()
    return {row["manga_id"] for row in rows}


def cache_get(key, allow_expired=False):
    row = get_db().execute(
        "SELECT payload, expires_at FROM api_cache WHERE cache_key = ?",
        (key,),
    ).fetchone()
    if not row:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        if allow_expired:
            return json.loads(row["payload"])
        return None

    return json.loads(row["payload"])


def cache_set(key, payload, ttl_seconds):
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    get_db().execute(
        """
        INSERT INTO api_cache (cache_key, payload, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            payload = excluded.payload,
            expires_at = excluded.expires_at
        """,
        (key, json.dumps(payload), expires_at.isoformat()),
    )
    get_db().commit()


def cache_delete(key):
    get_db().execute("DELETE FROM api_cache WHERE cache_key = ?", (key,))
    get_db().commit()
