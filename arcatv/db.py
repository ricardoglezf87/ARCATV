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


def list_shows():
    rows = get_db().execute("SELECT * FROM shows ORDER BY name COLLATE NOCASE").fetchall()
    return [row_to_show(row) for row in rows]


def get_show_ids():
    rows = get_db().execute("SELECT id FROM shows").fetchall()
    return {row["id"] for row in rows}


def get_show(show_id):
    row = get_db().execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
    return row_to_show(row) if row else None


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


def remove_show(show_id):
    db = get_db()
    db.execute("DELETE FROM watched_episodes WHERE show_id = ?", (show_id,))
    db.execute("DELETE FROM shows WHERE id = ?", (show_id,))
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


def unmark_episode(episode_id):
    get_db().execute("DELETE FROM watched_episodes WHERE episode_id = ?", (episode_id,))
    get_db().commit()


def clear_show_progress(show_id):
    get_db().execute("DELETE FROM watched_episodes WHERE show_id = ?", (show_id,))
    get_db().commit()


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
        cache_delete(key)
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
