"""SQLite local storage for jobs, regions, and config."""

import json
import sqlite3
import time


class Storage:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def init_db(self):
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                event_id TEXT PRIMARY KEY,
                d_tag TEXT UNIQUE,
                pubkey TEXT NOT NULL,
                province_code INTEGER,
                city_code INTEGER,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                received_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS regions (
                code INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                parent_code INTEGER
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_status (
                event_id TEXT PRIMARY KEY,
                favorited INTEGER DEFAULT 0,
                applied INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (unixepoch('now')),
                updated_at INTEGER DEFAULT (unixepoch('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_job_status_event_id ON job_status(event_id);
            CREATE TABLE IF NOT EXISTS profiles (
                pubkey TEXT PRIMARY KEY,
                event_id TEXT UNIQUE,
                d_tag TEXT UNIQUE,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                received_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS applications (
                event_id TEXT PRIMARY KEY,
                d_tag TEXT NOT NULL,
                job_id TEXT NOT NULL,
                employer_pubkey TEXT NOT NULL,
                applicant_pubkey TEXT NOT NULL,
                message TEXT,
                status TEXT DEFAULT 'pending',
                response_message TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER DEFAULT (unixepoch('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
            CREATE INDEX IF NOT EXISTS idx_applications_applicant ON applications(applicant_pubkey);
        """)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def list_tables(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        return [row["name"] for row in cur.fetchall()]

    # ── Config ──

    def set_config(self, key: str, value: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_config(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    # ── Jobs ──

    def upsert_job(
        self,
        event_id: str,
        d_tag: str,
        pubkey: str,
        province_code: int,
        city_code: int,
        content: str,
        created_at: int,
    ):
        # Delete existing job with same d_tag (replaceable event)
        self._conn.execute("DELETE FROM jobs WHERE d_tag = ?", (d_tag,))
        self._conn.execute(
            """INSERT INTO jobs
               (event_id, d_tag, pubkey, province_code, city_code, content, created_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, d_tag, pubkey, province_code, city_code, content, created_at, int(time.time())),
        )
        self._conn.commit()

    def get_job(self, event_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_jobs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
        return row["cnt"]

    def evict_oldest(self, max_count: int):
        count = self.count_jobs()
        if count <= max_count:
            return
        to_delete = count - max_count
        self._conn.execute(
            "DELETE FROM jobs WHERE event_id IN "
            "(SELECT event_id FROM jobs ORDER BY created_at ASC LIMIT ?)",
            (to_delete,),
        )
        self._conn.commit()

    # ── Regions ──

    def upsert_region(
        self,
        code: int,
        name: str,
        region_type: str,
        parent_code: int | None = None,
    ):
        self._conn.execute(
            "INSERT OR REPLACE INTO regions (code, name, type, parent_code) VALUES (?, ?, ?, ?)",
            (code, name, region_type, parent_code),
        )
        self._conn.commit()

    def get_region(self, code: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM regions WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None

    def list_regions(self, region_type: str | None = None) -> list[dict]:
        if region_type:
            rows = self._conn.execute(
                "SELECT * FROM regions WHERE type = ? ORDER BY code", (region_type,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM regions ORDER BY code"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Job Status ──

    def upsert_status(self, event_id: str, favorited: bool | None = None, applied: bool | None = None):
        """Set or toggle status for a job. If status already exists, flips the value."""
        existing = self.get_status(event_id)
        current_fav = existing["favorited"] if existing else 0
        current_app = existing["applied"] if existing else 0
        # Toggle if favorited is True, keep current if favorited is False (explicit set)
        new_fav = (1 - current_fav) if favorited is True else (0 if favorited is False else current_fav)
        new_app = (1 - current_app) if applied is True else (0 if applied is False else current_app)
        self._conn.execute(
            """INSERT OR REPLACE INTO job_status (event_id, favorited, applied, updated_at)
               VALUES (?, ?, ?, unixepoch('now'))""",
            (event_id, new_fav, new_app),
        )
        self._conn.commit()

    def get_status(self, event_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM job_status WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── Job Search ──

    def _escape_like(self, text: str) -> str:
        """Escape special characters in LIKE pattern."""
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def search_jobs(self, query: str) -> list[dict]:
        """Search jobs by keywords in title, company, and description.

        Case-insensitive AND matching: all keywords must match.
        Empty query returns all jobs.
        Uses json_extract to search specific fields (no substring false positives).
        """
        if not query or not query.strip():
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

        keywords = query.strip().split()
        # Build WHERE clause for AND matching all keywords
        conditions = []
        params = []
        for keyword in keywords:
            escaped = self._escape_like(keyword)
            pattern = f"%{escaped}%"
            # Search in title, company, description using json_extract
            conditions.append(
                "(LOWER(json_extract(content, '$.title')) LIKE ? ESCAPE '\\' COLLATE NOCASE"
                " OR LOWER(json_extract(content, '$.company')) LIKE ? ESCAPE '\\' COLLATE NOCASE"
                " OR LOWER(json_extract(content, '$.description')) LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            params.extend([pattern, pattern, pattern])

        where_clause = " AND ".join(conditions)
        sql = f"SELECT * FROM jobs WHERE {where_clause} ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_jobs(
        self,
        province_code: int | None = None,
        city_code: int | None = None,
        favorited: bool | None = None,
        applied: bool | None = None,
        search_query: str | None = None,
    ) -> list[dict]:
        query = "SELECT jobs.* FROM jobs LEFT JOIN job_status ON jobs.event_id = job_status.event_id WHERE 1=1"
        params: list = []
        if province_code is not None:
            query += " AND jobs.province_code = ?"
            params.append(province_code)
        if city_code is not None:
            query += " AND jobs.city_code = ?"
            params.append(city_code)
        if favorited is not None:
            query += " AND job_status.favorited = ?"
            params.append(1 if favorited else 0)
        if applied is not None:
            query += " AND job_status.applied = ?"
            params.append(1 if applied else 0)
        if search_query and search_query.strip():
            keywords = search_query.strip().split()
            for keyword in keywords:
                escaped = self._escape_like(keyword)
                pattern = f"%{escaped}%"
                query += (
                    " AND (LOWER(json_extract(jobs.content, '$.title')) LIKE ? ESCAPE '\\' COLLATE NOCASE"
                    " OR LOWER(json_extract(jobs.content, '$.company')) LIKE ? ESCAPE '\\' COLLATE NOCASE"
                    " OR LOWER(json_extract(jobs.content, '$.description')) LIKE ? ESCAPE '\\' COLLATE NOCASE)"
                )
                params.extend([pattern, pattern, pattern])
        query += " ORDER BY jobs.created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── Profiles ──

    def upsert_profile(
        self,
        pubkey: str,
        event_id: str,
        d_tag: str,
        content: str,
        created_at: int,
    ):
        """Store or update a user profile."""
        self._conn.execute(
            """INSERT OR REPLACE INTO profiles
               (pubkey, event_id, d_tag, content, created_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pubkey, event_id, d_tag, content, created_at, int(time.time())),
        )
        self._conn.commit()

    def get_profile(self, pubkey: str) -> dict | None:
        """Get profile by pubkey."""
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE pubkey = ?", (pubkey,)
        ).fetchone()
        if not row:
            return None
        profile = dict(row)
        # Parse JSON content to extract name for convenience
        try:
            content = json.loads(profile.get("content", "{}"))
            profile["name"] = content.get("name", "")
        except (json.JSONDecodeError, TypeError):
            profile["name"] = ""
        return profile

    def get_own_profile(self, pubkey: str) -> dict | None:
        """Alias for get_profile."""
        return self.get_profile(pubkey)

    def delete_profile(self, pubkey: str):
        """Delete profile by pubkey."""
        self._conn.execute("DELETE FROM profiles WHERE pubkey = ?", (pubkey,))
        self._conn.commit()

    # ── Applications ──────────────────────────────────────────────────

    def upsert_application(
        self,
        event_id: str,
        d_tag: str,
        job_id: str,
        employer_pubkey: str,
        applicant_pubkey: str,
        message: str | None,
        status: str = "pending",
        response_message: str | None = None,
        created_at: int | None = None,
    ):
        """Insert or update an application record."""
        if created_at is None:
            created_at = int(time.time())
        self._conn.execute(
            """INSERT OR REPLACE INTO applications
               (event_id, d_tag, job_id, employer_pubkey, applicant_pubkey, message, status, response_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))""",
            (event_id, d_tag, job_id, employer_pubkey, applicant_pubkey, message, status, response_message, created_at),
        )
        self._conn.commit()

    def get_application(self, event_id: str) -> dict | None:
        """Get application by event_id."""
        row = self._conn.execute(
            "SELECT * FROM applications WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_applications(
        self,
        applicant_pubkey: str | None = None,
        employer_pubkey: str | None = None,
        job_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List applications with optional filters."""
        query = "SELECT * FROM applications WHERE 1=1"
        params: list = []
        if applicant_pubkey:
            query += " AND applicant_pubkey = ?"
            params.append(applicant_pubkey)
        if employer_pubkey:
            query += " AND employer_pubkey = ?"
            params.append(employer_pubkey)
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_application_status(
        self,
        event_id: str,
        status: str,
        response_message: str | None = None,
    ):
        """Update application status (accepted/rejected)."""
        self._conn.execute(
            "UPDATE applications SET status = ?, response_message = ?, updated_at = unixepoch('now') WHERE event_id = ?",
            (status, response_message, event_id),
        )
        self._conn.commit()

    def has_application(self, job_id: str, applicant_pubkey: str) -> bool:
        """Check if an application exists for this job and applicant."""
        row = self._conn.execute(
            "SELECT 1 FROM applications WHERE job_id = ? AND applicant_pubkey = ?",
            (job_id, applicant_pubkey),
        ).fetchone()
        return row is not None
