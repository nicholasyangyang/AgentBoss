import os
import pytest
import sqlite3
from cli.storage import Storage


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = Storage(db_path)
    s.init_db()
    yield s
    s.close()


class TestStorageInit:
    def test_creates_tables(self, db):
        tables = db.list_tables()
        assert "jobs" in tables
        assert "regions" in tables
        assert "config" in tables


class TestConfig:
    def test_set_and_get(self, db):
        db.set_config("relay", "ws://localhost:7777")
        assert db.get_config("relay") == "ws://localhost:7777"

    def test_get_missing_returns_default(self, db):
        assert db.get_config("missing", "default") == "default"

    def test_set_overwrites(self, db):
        db.set_config("key", "v1")
        db.set_config("key", "v2")
        assert db.get_config("key") == "v2"


class TestJobs:
    def test_upsert_and_get(self, db):
        db.upsert_job(
            event_id="id1",
            d_tag="d1",
            pubkey="pub1",
            province_code=1,
            city_code=101,
            content='{"title":"Dev"}',
            created_at=1000,
        )
        job = db.get_job("id1")
        assert job is not None
        assert job["d_tag"] == "d1"
        assert job["province_code"] == 1

    def test_upsert_replaces_by_d_tag(self, db):
        db.upsert_job("id1", "d1", "pub1", 1, 101, '{"v":1}', 1000)
        db.upsert_job("id2", "d1", "pub1", 1, 101, '{"v":2}', 2000)
        # old id1 should be gone, id2 should exist
        assert db.get_job("id1") is None
        assert db.get_job("id2") is not None

    def test_list_jobs_filter_province(self, db):
        db.upsert_job("id1", "d1", "p", 1, 101, "{}", 1000)
        db.upsert_job("id2", "d2", "p", 2, 201, "{}", 1000)
        jobs = db.list_jobs(province_code=1)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "id1"

    def test_list_jobs_filter_city(self, db):
        db.upsert_job("id1", "d1", "p", 1, 101, "{}", 1000)
        db.upsert_job("id2", "d2", "p", 1, 102, "{}", 1000)
        jobs = db.list_jobs(city_code=102)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "id2"

    def test_evict_oldest_when_over_limit(self, db):
        for i in range(5):
            db.upsert_job(f"id{i}", f"d{i}", "p", 1, 101, "{}", created_at=i)
        db.evict_oldest(max_count=3)
        remaining = db.list_jobs()
        assert len(remaining) == 3
        # oldest (created_at=0,1) should be evicted
        ids = [j["event_id"] for j in remaining]
        assert "id0" not in ids
        assert "id1" not in ids

    def test_count_jobs(self, db):
        db.upsert_job("id1", "d1", "p", 1, 101, "{}", 1000)
        db.upsert_job("id2", "d2", "p", 1, 101, "{}", 1000)
        assert db.count_jobs() == 2


class TestRegions:
    def test_upsert_and_get_region(self, db):
        db.upsert_region(code=1, name="北京", region_type="province")
        r = db.get_region(1)
        assert r["name"] == "北京"
        assert r["type"] == "province"

    def test_upsert_city_with_parent(self, db):
        db.upsert_region(1, "北京", "province")
        db.upsert_region(101, "北京市", "city", parent_code=1)
        r = db.get_region(101)
        assert r["parent_code"] == 1

    def test_list_provinces(self, db):
        db.upsert_region(1, "北京", "province")
        db.upsert_region(2, "上海", "province")
        db.upsert_region(101, "北京市", "city", parent_code=1)
        provinces = db.list_regions(region_type="province")
        assert len(provinces) == 2

    def test_get_region_version(self, db):
        """Region version stored in config"""
        assert db.get_config("region_version", "0") == "0"
        db.set_config("region_version", "3")
        assert db.get_config("region_version") == "3"


class TestJobSearch:
    def test_search_single_keyword_title(self, db):
        """Search finds job by keyword in title"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Developer","company":"TechCo","description":"Frontend work"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Python Backend","company":"DataCorp","description":"API development"}', 1000)
        jobs = db.search_jobs("React")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_single_keyword_company(self, db):
        """Search finds job by keyword in company"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"Dev","company":"Google","description":"Work here"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Dev","company":"Meta","description":"Work here"}', 1000)
        jobs = db.search_jobs("Google")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_single_keyword_description(self, db):
        """Search finds job by keyword in description"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"Dev","company":"Co","description":"Remote work available"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Dev","company":"Co","description":"Onsite only"}', 1000)
        jobs = db.search_jobs("Remote")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_multiple_keywords_and(self, db):
        """Multiple keywords must all match (AND logic)"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Developer","company":"TechCo","description":"Frontend"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"React Developer","company":"BigCorp","description":"Backend"}', 1000)
        db.upsert_job("j3", "d3", "pub1", 1, 101, '{"title":"Python Dev","company":"TechCo","description":"Fullstack"}', 1000)
        # Both "React" AND "Frontend" must match
        jobs = db.search_jobs("React Frontend")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_case_insensitive(self, db):
        """Search is case-insensitive"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Developer","company":"TechCo","description":""}', 1000)
        jobs_lower = db.search_jobs("react")
        jobs_upper = db.search_jobs("REACT")
        jobs_mixed = db.search_jobs("ReAcT")
        assert len(jobs_lower) == 1
        assert len(jobs_upper) == 1
        assert len(jobs_mixed) == 1
        assert jobs_lower[0]["event_id"] == "j1"

    def test_search_no_match(self, db):
        """Search returns empty when no job matches"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Developer","company":"TechCo","description":""}', 1000)
        jobs = db.search_jobs("Python")
        assert len(jobs) == 0

    def test_search_empty_query(self, db):
        """Search with empty query returns all jobs"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"Dev1","company":"C","description":""}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Dev2","company":"C","description":""}', 1000)
        jobs = db.search_jobs("")
        assert len(jobs) == 2

    def test_search_combined_with_filters(self, db):
        """Search can be combined with province/city filters"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Dev","company":"Co","description":""}', 1000)
        db.upsert_job("j2", "d2", "pub1", 2, 201, '{"title":"React Dev","company":"Co","description":""}', 1000)
        # Search for React, filtered by province 1
        jobs = db.list_jobs(search_query="React", province_code=1)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_returns_relevant_jobs_only(self, db):
        """Search only searches in title, company, description of job content"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"Dev","company":"Acme","description":"Keywords: golang rust"}', 1000)
        # "golang" is in description, "python" is not - should not match both (AND logic)
        jobs = db.search_jobs("golang python")
        assert len(jobs) == 0

    def test_list_jobs_with_search_query(self, db):
        """list_jobs accepts search_query parameter"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"Senior React Dev","company":"BigTech","description":"Build UI"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Python Backend","company":"DataCo","description":"API work"}', 1000)
        jobs = db.list_jobs(search_query="React")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_search_word_boundary_uses_json_extract(self, db):
        """Search uses json_extract to match specific JSON fields, not raw JSON substring"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"React Developer","company":"TechCo","description":"Frontend work"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Python Dev","company":"Contact Co","description":"Customer support"}', 1000)
        # "Contact" appears in j2's company field
        # json_extract ensures we match the actual field values
        jobs = db.search_jobs("Contact")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j2"

    def test_search_special_chars_escaped(self, db):
        """Search correctly handles LIKE special characters (%)"""
        db.upsert_job("j1", "d1", "pub1", 1, 101, '{"title":"100% Remote","company":"Co","description":"Work from anywhere"}', 1000)
        db.upsert_job("j2", "d2", "pub1", 1, 101, '{"title":"Onsite Only","company":"Co","description":"No remote"}', 1000)
        # "100%" should match the job with "100% Remote" in title
        jobs = db.search_jobs("100%")
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"


class TestJobStatus:
    def test_job_status_table_exists(self, db):
        tables = db.list_tables()
        assert "job_status" in tables

    def test_upsert_status_favorited(self, db):
        db.upsert_status("ev1", favorited=True)
        status = db.get_status("ev1")
        assert status is not None
        assert status["favorited"] == 1
        assert status["applied"] == 0

    def test_get_status_returns_status(self, db):
        db.upsert_status("ev1", favorited=True, applied=False)
        status = db.get_status("ev1")
        assert status["favorited"] == 1
        assert status["applied"] == 0
        assert status["event_id"] == "ev1"

    def test_get_status_not_found(self, db):
        assert db.get_status("nonexistent") is None

    def test_toggle_favorited(self, db):
        # First call: set favorited=1
        db.upsert_status("ev1", favorited=True)
        assert db.get_status("ev1")["favorited"] == 1
        # Second call: toggle to 0
        db.upsert_status("ev1", favorited=True)  # already True, toggles off
        assert db.get_status("ev1")["favorited"] == 0

    def test_toggle_applied(self, db):
        db.upsert_status("ev1", applied=True)
        assert db.get_status("ev1")["applied"] == 1
        db.upsert_status("ev1", applied=True)  # toggle off
        assert db.get_status("ev1")["applied"] == 0

    def test_list_jobs_filter_favorited(self, db):
        db.upsert_job("j1", "d1", "pub1", 1, 101, "{}", 1000)
        db.upsert_job("j2", "d2", "pub2", 1, 101, "{}", 1000)
        db.upsert_status("j1", favorited=True)
        jobs = db.list_jobs(favorited=True)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"

    def test_list_jobs_filter_applied(self, db):
        db.upsert_job("j1", "d1", "pub1", 1, 101, "{}", 1000)
        db.upsert_job("j2", "d2", "pub2", 2, 201, "{}", 1000)
        db.upsert_status("j2", applied=True)
        jobs = db.list_jobs(applied=True)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j2"

    def test_list_jobs_filter_favorited_and_province(self, db):
        db.upsert_job("j1", "d1", "pub1", 1, 101, "{}", 1000)
        db.upsert_job("j2", "d2", "pub2", 2, 201, "{}", 1000)
        db.upsert_status("j1", favorited=True)
        db.upsert_status("j2", favorited=True)
        jobs = db.list_jobs(province_code=1, favorited=True)
        assert len(jobs) == 1
        assert jobs[0]["event_id"] == "j1"


class TestProfiles:
    """Tests for user profiles storage."""

    def test_profiles_table_exists(self, db):
        """Profile table exists in database."""
        tables = db.list_tables()
        assert "profiles" in tables

    def test_upsert_profile(self, db):
        """Can store a user profile."""
        db.upsert_profile(
            pubkey="aa" * 32,
            event_id="ev1",
            d_tag="profile_1",
            content='{"name":"Alice","bio":"Developer"}',
            created_at=1000,
        )
        profile = db.get_profile("aa" * 32)
        assert profile is not None
        assert profile["pubkey"] == "aa" * 32
        assert profile["name"] == "Alice"

    def test_upsert_replaces_profile(self, db):
        """Upsert replaces existing profile with same pubkey."""
        db.upsert_profile(
            pubkey="aa" * 32,
            event_id="ev1",
            d_tag="profile_1",
            content='{"name":"Alice"}',
            created_at=1000,
        )
        db.upsert_profile(
            pubkey="aa" * 32,
            event_id="ev2",
            d_tag="profile_1",
            content='{"name":"Bob"}',
            created_at=2000,
        )
        profile = db.get_profile("aa" * 32)
        assert profile["name"] == "Bob"
        assert profile["event_id"] == "ev2"

    def test_get_profile_not_found(self, db):
        """Returns None for non-existent profile."""
        profile = db.get_profile("nonexistent")
        assert profile is None

    def test_get_own_profile(self, db):
        """Can retrieve own profile by pubkey."""
        db.upsert_profile(
            pubkey="aa" * 32,
            event_id="ev1",
            d_tag="profile_1",
            content='{"name":"Alice","bio":"Dev"}',
            created_at=1000,
        )
        own = db.get_own_profile("aa" * 32)
        assert own is not None
        assert own["name"] == "Alice"

    def test_delete_profile(self, db):
        """Can delete a profile."""
        db.upsert_profile(
            pubkey="aa" * 32,
            event_id="ev1",
            d_tag="profile_1",
            content='{"name":"Alice"}',
            created_at=1000,
        )
        db.delete_profile("aa" * 32)
        profile = db.get_profile("aa" * 32)
        assert profile is None


class TestApplications:
    """Tests for job applications storage."""

    def test_applications_table_exists(self, db):
        """applications table exists after init_db."""
        assert "applications" in db.list_tables()

    def test_upsert_application(self, db):
        """Can insert and retrieve an application."""
        db.upsert_application(
            event_id="app1",
            d_tag="app_job1_1000",
            job_id="job1",
            employer_pubkey="emp1",
            applicant_pubkey="app1",
            message="I'm interested",
            status="pending",
            created_at=1000,
        )
        app = db.get_application("app1")
        assert app is not None
        assert app["job_id"] == "job1"
        assert app["status"] == "pending"

    def test_list_applications_filter(self, db):
        """Can filter applications by applicant and status."""
        db.upsert_application("a1", "d1", "job1", "emp1", "app1", "msg", "pending", created_at=1000)
        db.upsert_application("a2", "d2", "job1", "emp1", "app1", "msg", "accepted", created_at=1001)
        db.upsert_application("a3", "d3", "job2", "emp1", "app2", "msg", "pending", created_at=1002)

        # Filter by applicant
        apps = db.list_applications(applicant_pubkey="app1")
        assert len(apps) == 2

        # Filter by status
        apps = db.list_applications(status="pending")
        assert len(apps) == 2

        # Filter by job_id
        apps = db.list_applications(job_id="job1")
        assert len(apps) == 2

    def test_has_application(self, db):
        """Can check if application exists for job+applicant."""
        db.upsert_application("a1", "app_job1_1000", "job1", "emp1", "app1", "msg", "pending", created_at=1000)
        assert db.has_application("job1", "app1") is True
        assert db.has_application("job2", "app1") is False

    def test_update_application_status(self, db):
        """Can update application status and response message."""
        db.upsert_application("a1", "d1", "job1", "emp1", "app1", "msg", "pending", created_at=1000)
        db.update_application_status("a1", "accepted", "Welcome aboard!")
        app = db.get_application("a1")
        assert app["status"] == "accepted"
        assert app["response_message"] == "Welcome aboard!"
