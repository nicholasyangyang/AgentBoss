"""AgentBoss CLI — decentralized job recruitment on Nostr."""

import json
import os
import asyncio
import time
from pathlib import Path
from typing import Optional

import typer

from shared.constants import (
    APP_TAG, JOB_TAG, REGION_TAG, KIND_APP_DATA, KIND_FEDERATION,
    REGION_MAP_D_TAG, DEFAULT_RELAY, DEFAULT_MAX_JOBS, JOB_CONTENT_VERSION,
)
from shared.crypto import gen_keys, derive_pub, to_npub, nsec_to_hex, to_nsec
from shared.event import build_event
from cli.storage import Storage
from cli.regions import RegionResolver
from cli.models import parse_job_content
from cli.nostr_client import NostrRelay, fetch_events_from_relays

app = typer.Typer(help="AgentBoss: Decentralized Job Recruitment CLI")
regions_app = typer.Typer(help="Region mapping management")
config_app = typer.Typer(help="Configuration management")
profile_app = typer.Typer(help="User profile management")
applications_app = typer.Typer(help="Manage job applications")
federation_app = typer.Typer(help="Federation management")
app.add_typer(regions_app, name="regions")
app.add_typer(config_app, name="config")
app.add_typer(profile_app, name="profile")
app.add_typer(applications_app, name="applications")
app.add_typer(federation_app, name="federation")


def _home() -> Path:
    return Path(os.environ.get("AGENTBOSS_HOME", Path.home() / ".agentboss"))


def _ensure_home() -> Path:
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _get_storage() -> Storage:
    home = _ensure_home()
    s = Storage(str(home / "agentboss.db"))
    s.init_db()
    return s


def _load_identity() -> dict | None:
    path = _home() / "identity.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save_identity(privkey: str, pubkey: str):
    path = _ensure_home() / "identity.json"
    path.write_text(json.dumps({
        "nsec": to_nsec(privkey),
        "npub": to_npub(pubkey),
        "privkey": privkey,
        "pubkey": pubkey,
    }))


# ── Identity ──

@app.command()
def login(key: str = typer.Option(..., "--key", help="Private key (nsec or 64-char hex)")):
    """Import identity from nsec or hex private key."""
    try:
        if key.startswith("nsec1"):
            privkey = nsec_to_hex(key)
        elif len(key) == 64 and all(c in "0123456789abcdef" for c in key.lower()):
            privkey = key.lower()
        else:
            typer.echo("Invalid key format. Use nsec1... or 64-char hex.")
            raise typer.Exit(code=1)
        pubkey = derive_pub(privkey)
        _save_identity(privkey, pubkey)
        typer.echo(f"Logged in as {to_npub(pubkey)}")
    except Exception as e:
        typer.echo(f"Invalid key: {e}")
        raise typer.Exit(code=1)


@app.command()
def whoami():
    """Show current identity."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity found. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)
    typer.echo(f"npub: {identity['npub']}")
    typer.echo(f"hex:  {identity['pubkey']}")


# ── Publish ──

@app.command()
def publish(
    province: str = typer.Option(..., "--province"),
    city: str = typer.Option(..., "--city"),
    title: str = typer.Option(..., "--title"),
    company: str = typer.Option(..., "--company"),
    salary: str = typer.Option("", "--salary"),
    description: str = typer.Option("", "--description"),
    contact: str = typer.Option("", "--contact"),
    federation: Optional[str] = typer.Option(None, "--federation", help="Publish to a federation by name"),
):
    """Publish a job posting to the Nostr network."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    resolver = RegionResolver(storage)

    prov_code = resolver.province_code(province)
    if prov_code is None:
        typer.echo(f"Unknown province '{province}'. Run: agentboss regions sync")
        raise typer.Exit(code=1)

    city_code = resolver.city_code(city)
    if city_code is None:
        typer.echo(f"Unknown city '{city}'. Run: agentboss regions sync")
        raise typer.Exit(code=1)

    import uuid
    content = json.dumps({
        "title": title,
        "company": company,
        "salary_range": salary,
        "description": description,
        "contact": contact or identity["npub"],
        "version": JOB_CONTENT_VERSION,
    }, ensure_ascii=False)

    tags = [
        ["d", str(uuid.uuid4())],
        ["t", APP_TAG],
        ["t", JOB_TAG],
        ["province", str(prov_code)],
        ["city", str(city_code)],
    ]

    event = build_event(
        kind=KIND_APP_DATA,
        content=content,
        privkey=identity["privkey"],
        pubkey=identity["pubkey"],
        tags=tags,
    )

    async def _publish():
        if federation:
            # Multi-relay publish to federation
            fed = next((f for f in storage.list_federations() if f["name"] == federation), None)
            if not fed:
                typer.echo(f"Federation '{federation}' not found. Run: agentboss federation list")
                raise typer.Exit(code=1)
            relay_urls = fed["relay_urls"]
            failed = []
            for relay_url in relay_urls:
                relay = NostrRelay(relay_url)
                try:
                    await relay.connect()
                    result = await relay.publish_event(event)
                    if not result["accepted"]:
                        failed.append(f"{relay_url}: {result['message']}")
                except Exception as e:
                    failed.append(f"{relay_url}: {e}")
                finally:
                    await relay.close()
            if failed:
                typer.echo(f"Published to {len(relay_urls) - len(failed)}/{len(relay_urls)} federation relays. Failures:")
                for f in failed:
                    typer.echo(f"  - {f}")
            else:
                typer.echo(f"Published to all {len(relay_urls)} federation relays.")
            return

        # Single relay publish (existing behavior)
        relay_url = storage.get_config("relay", DEFAULT_RELAY)
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            result = await relay.publish_event(event)
            if result["accepted"]:
                typer.echo(f"Published: {event['id'][:16]}...")
            else:
                typer.echo(f"Rejected: {result['message']}")
        finally:
            await relay.close()

    asyncio.run(_publish())
    storage.close()


# ── Fetch ──

@app.command()
def fetch(
    province: Optional[str] = typer.Option(None, "--province"),
    city: Optional[str] = typer.Option(None, "--city"),
    limit: int = typer.Option(DEFAULT_MAX_JOBS, "--limit"),
    federation: Optional[str] = typer.Option(None, "--federation", help="Fetch from a federation by name"),
):
    """Fetch job postings from Relay and store locally."""
    storage = _get_storage()
    resolver = RegionResolver(storage)

    tags = {"#t": [APP_TAG, JOB_TAG]}
    if province:
        prov_code = resolver.province_code(province)
        if prov_code is None:
            typer.echo(f"Unknown province '{province}'. Run: agentboss regions sync")
            raise typer.Exit(code=1)
        tags["#province"] = [str(prov_code)]
    if city:
        city_code = resolver.city_code(city)
        if city_code is None:
            typer.echo(f"Unknown city '{city}'. Run: agentboss regions sync")
            raise typer.Exit(code=1)
        tags["#city"] = [str(city_code)]

    async def _fetch():
        if federation:
            # Multi-relay fetch from federation
            fed = next((f for f in storage.list_federations() if f["name"] == federation), None)
            if not fed:
                typer.echo(f"Federation '{federation}' not found. Run: agentboss federation list")
                raise typer.Exit(code=1)
            relay_urls = fed["relay_urls"]
            events = await fetch_events_from_relays(
                relay_urls=relay_urls,
                kinds=[KIND_APP_DATA],
                tags=tags,
                limit=limit,
            )
            count = 0
            for event in events:
                pcode = ccode = 0
                for tag in event.get("tags", []):
                    if tag[0] == "province":
                        pcode = int(tag[1])
                    elif tag[0] == "city":
                        ccode = int(tag[1])
                d_tag = ""
                for tag in event.get("tags", []):
                    if tag[0] == "d":
                        d_tag = tag[1]
                has_job_tag = any(t[0] == "t" and t[1] == JOB_TAG for t in event.get("tags", []))
                if has_job_tag and d_tag:
                    storage.upsert_job(
                        event_id=event["id"],
                        d_tag=d_tag,
                        pubkey=event["pubkey"],
                        province_code=pcode,
                        city_code=ccode,
                        content=event["content"],
                        created_at=event["created_at"],
                    )
                    count += 1
            max_jobs = int(storage.get_config("max-jobs", str(DEFAULT_MAX_JOBS)))
            storage.evict_oldest(max_jobs)
            typer.echo(f"Fetched {count} jobs from federation '{federation}'. Total stored: {storage.count_jobs()}")
            return

        # Single relay fetch (existing behavior)
        relay_url = storage.get_config("relay", DEFAULT_RELAY)
        relay = NostrRelay(relay_url)
        count = 0
        try:
            await relay.connect()
            await relay.subscribe("fetch", kinds=[KIND_APP_DATA], tags=tags, limit=limit)
            async for event in relay.receive_events("fetch"):
                # Extract province/city from tags
                pcode = ccode = 0
                for tag in event.get("tags", []):
                    if tag[0] == "province":
                        pcode = int(tag[1])
                    elif tag[0] == "city":
                        ccode = int(tag[1])
                d_tag = ""
                for tag in event.get("tags", []):
                    if tag[0] == "d":
                        d_tag = tag[1]
                # Only store job events (not region maps)
                has_job_tag = any(t[0] == "t" and t[1] == JOB_TAG for t in event.get("tags", []))
                if has_job_tag and d_tag:
                    storage.upsert_job(
                        event_id=event["id"],
                        d_tag=d_tag,
                        pubkey=event["pubkey"],
                        province_code=pcode,
                        city_code=ccode,
                        content=event["content"],
                        created_at=event["created_at"],
                    )
                    count += 1
            await relay.unsubscribe("fetch")
        finally:
            await relay.close()
        max_jobs = int(storage.get_config("max-jobs", str(DEFAULT_MAX_JOBS)))
        storage.evict_oldest(max_jobs)
        typer.echo(f"Fetched {count} jobs. Total stored: {storage.count_jobs()}")

    asyncio.run(_fetch())
    storage.close()


# ── List ──

@app.command(name="list")
def list_jobs(
    province: Optional[str] = typer.Option(None, "--province"),
    city: Optional[str] = typer.Option(None, "--city"),
    favorited: bool = typer.Option(False, "--favorited", is_flag=True, help="Show only favorited jobs"),
    applied: bool = typer.Option(False, "--applied", is_flag=True, help="Show only applied jobs"),
    search: Optional[str] = typer.Option(None, "--search", help="Search keywords (space-separated, AND match)"),
):
    """List locally stored job postings."""
    storage = _get_storage()
    resolver = RegionResolver(storage)

    prov_code = None
    city_code_val = None
    if province:
        prov_code = resolver.province_code(province)
    if city:
        city_code_val = resolver.city_code(city)

    jobs = storage.list_jobs(
        province_code=prov_code,
        city_code=city_code_val,
        favorited=favorited or None,
        applied=applied or None,
        search_query=search,
    )
    if not jobs:
        typer.echo("No jobs found.")
        storage.close()
        return

    for job in jobs:
        try:
            content = parse_job_content(job["content"])
            pname = resolver.province_name(job["province_code"]) or str(job["province_code"])
            cname = resolver.city_name(job["city_code"]) or str(job["city_code"])
            # Show status indicators
            status = storage.get_status(job["event_id"])
            indicators = ""
            if status:
                indicators += " ⭐" if status["favorited"] else ""
                indicators += " ✅" if status["applied"] else ""
            typer.echo(f"[{job['event_id'][:12]}] {content.title} @ {content.company} | {pname}/{cname} | {content.salary_range}{indicators}")
        except Exception:
            typer.echo(f"[{job['event_id'][:12]}] (parse error)")
    storage.close()


# ── Job Status ──

@app.command()
def favorite(job_id: str = typer.Argument(..., help="Job ID (full or prefix)")):
    """Toggle favorited status of a job."""
    storage = _get_storage()
    job = storage.get_job(job_id)
    if not job:
        # Try prefix match
        jobs = storage.list_jobs()
        matches = [j for j in jobs if j["event_id"].startswith(job_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            typer.echo("Multiple jobs match prefix. Use full ID.")
            raise typer.Exit(code=1)
        else:
            typer.echo("Job not found in local storage. Run `fetch` first.")
            raise typer.Exit(code=1)
    storage.upsert_status(job["event_id"], favorited=True)
    status = storage.get_status(job["event_id"])
    new_state = "⭐ favorited" if status["favorited"] else "unfavorited"
    typer.echo(f"Job {job['event_id'][:12]}... - {new_state}")
    storage.close()


@app.command()
def apply(job_id: str = typer.Argument(..., help="Job ID (full or prefix)")):
    """Toggle applied status of a job."""
    storage = _get_storage()
    job = storage.get_job(job_id)
    if not job:
        jobs = storage.list_jobs()
        matches = [j for j in jobs if j["event_id"].startswith(job_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            typer.echo("Multiple jobs match prefix. Use full ID.")
            raise typer.Exit(code=1)
        else:
            typer.echo("Job not found in local storage. Run `fetch` first.")
            raise typer.Exit(code=1)
    storage.upsert_status(job["event_id"], applied=True)
    status = storage.get_status(job["event_id"])
    new_state = "✅ marked as applied" if status["applied"] else "unmarked"
    typer.echo(f"Job {job['event_id'][:12]}... - {new_state}")
    storage.close()


@app.command(name="submit")
def submit(
    job_id: str = typer.Argument(..., help="Job ID (full or prefix)"),
    message: str = typer.Option("", "--message", help="Introduction message"),
):
    """Submit an application for a job posting."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()

    # Find job
    job = storage.get_job(job_id)
    if not job:
        # Try prefix match
        jobs = storage.list_jobs()
        matches = [j for j in jobs if j["event_id"].startswith(job_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            typer.echo("Multiple jobs match prefix. Use full ID.")
            storage.close()
            raise typer.Exit(code=1)
        else:
            typer.echo("Job not found in local storage. Run `agentboss fetch` first.")
            storage.close()
            raise typer.Exit(code=1)

    # Check if already applied
    if storage.has_application(job["event_id"], identity["pubkey"]):
        typer.echo("You have already applied to this job.")
        storage.close()
        raise typer.Exit(code=1)

    employer_pubkey = job["pubkey"]
    applicant_privkey = identity["privkey"]
    applicant_pubkey = identity["pubkey"]

    # Build d_tag: app_<job_id>_<timestamp>
    import time
    d_tag = f"app_{job['event_id']}_{int(time.time())}"

    # Build application event (kind:31970)
    event = build_event(
        kind=KIND_APP_DATA,
        content=json.dumps({"message": message}) if message else "{}",
        privkey=applicant_privkey,
        pubkey=applicant_pubkey,
        tags=[
            ["d", d_tag],
            ["t", APP_TAG],
            ["t", "application"],
        ],
    )

    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _submit():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            # Publish application event
            result = await relay.publish_event(event)
            if not result["accepted"]:
                typer.echo(f"Failed to submit: {result['message']}")
                return

            # Send DM to employer
            dm_content = json.dumps({
                "type": "application",
                "job_id": job["event_id"],
                "app_id": event["id"],
                "action": "submit",
                "message": message or "",
            })
            try:
                await relay.send_dm(applicant_privkey, employer_pubkey, dm_content)
            except Exception as e:
                typer.echo(f"Warning: Could not notify employer: {e}")

            # Store locally
            storage.upsert_application(
                event_id=event["id"],
                d_tag=d_tag,
                job_id=job["event_id"],
                employer_pubkey=employer_pubkey,
                applicant_pubkey=applicant_pubkey,
                message=message,
                status="pending",
                created_at=event["created_at"],
            )
            typer.echo(f"Application submitted for {job['event_id'][:12]}...")
        finally:
            await relay.close()

    asyncio.run(_submit())
    storage.close()


@app.command()
def status(job_id: str = typer.Argument(..., help="Job ID (full or prefix)")):
    """Show favorited/applied status of a job."""
    storage = _get_storage()
    job = storage.get_job(job_id)
    if not job:
        jobs = storage.list_jobs()
        matches = [j for j in jobs if j["event_id"].startswith(job_id)]
        if len(matches) == 1:
            job = matches[0]
        elif len(matches) > 1:
            typer.echo("Multiple jobs match prefix. Use full ID.")
            raise typer.Exit(code=1)
        else:
            typer.echo("Job not found.")
            raise typer.Exit(code=1)
    st = storage.get_status(job["event_id"])
    fav = "⭐ favorited" if (st and st["favorited"]) else "☆ not favorited"
    app = "✅ applied" if (st and st["applied"]) else "○ not applied"
    typer.echo(f"Job {job['event_id'][:12]}... | {fav} | {app}")
    storage.close()


# ── Show ──

@app.command()
def show(job_id: str = typer.Argument(..., help="Event ID (full or prefix)")):
    """Show details of a job posting."""
    storage = _get_storage()
    resolver = RegionResolver(storage)

    # Support prefix match
    job = storage.get_job(job_id)
    if not job:
        jobs = storage.list_jobs()
        for j in jobs:
            if j["event_id"].startswith(job_id):
                job = j
                break
    if not job:
        typer.echo("Job not found.")
        storage.close()
        raise typer.Exit(code=1)

    content = parse_job_content(job["content"])
    pname = resolver.province_name(job["province_code"]) or str(job["province_code"])
    cname = resolver.city_name(job["city_code"]) or str(job["city_code"])

    typer.echo(f"ID:          {job['event_id']}")
    typer.echo(f"Title:       {content.title}")
    typer.echo(f"Company:     {content.company}")
    typer.echo(f"Location:    {pname} / {cname}")
    typer.echo(f"Salary:      {content.salary_range}")
    typer.echo(f"Description: {content.description}")
    typer.echo(f"Contact:     {content.contact}")
    typer.echo(f"Publisher:   {job['pubkey']}")
    typer.echo(f"Posted:      {job['created_at']}")
    storage.close()


# ── Regions ──

@regions_app.command(name="list")
def regions_list():
    """List local region mappings."""
    storage = _get_storage()
    provinces = storage.list_regions(region_type="province")
    cities = storage.list_regions(region_type="city")
    if not provinces and not cities:
        typer.echo("No region mappings. Run: agentboss regions sync")
        storage.close()
        return
    typer.echo("Provinces:")
    for p in provinces:
        typer.echo(f"  {p['code']}: {p['name']}")
    typer.echo("Cities:")
    for c in cities:
        parent = f" (province: {c['parent_code']})" if c["parent_code"] else ""
        typer.echo(f"  {c['code']}: {c['name']}{parent}")
    storage.close()


@regions_app.command(name="sync")
def regions_sync():
    """Sync region mappings from Relay."""
    storage = _get_storage()
    resolver = RegionResolver(storage)
    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _sync():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            await relay.subscribe(
                "region_sync",
                kinds=[KIND_APP_DATA],
                tags={"#t": [APP_TAG, REGION_TAG]},
                limit=1,
            )
            async for event in relay.receive_events("region_sync"):
                resolver.apply_mapping(event["content"])
                typer.echo(f"Region mapping updated.")
            await relay.unsubscribe("region_sync")
        finally:
            await relay.close()

    asyncio.run(_sync())
    storage.close()


@regions_app.command(name="publish")
def regions_publish():
    """Publish region mapping to Relay (requires identity)."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    # Build mapping from local DB
    provinces = {str(r["code"]): r["name"] for r in storage.list_regions("province")}
    cities = {str(r["code"]): r["name"] for r in storage.list_regions("city")}
    province_city: dict[str, list[int]] = {}
    for c in storage.list_regions("city"):
        if c["parent_code"] is not None:
            key = str(c["parent_code"])
            province_city.setdefault(key, []).append(c["code"])

    current_version = int(storage.get_config("region_version", "0"))
    new_version = current_version + 1

    content = json.dumps({
        "version": new_version,
        "provinces": provinces,
        "cities": cities,
        "province_city": province_city,
    }, ensure_ascii=False)

    event = build_event(
        kind=KIND_APP_DATA,
        content=content,
        privkey=identity["privkey"],
        pubkey=identity["pubkey"],
        tags=[["d", REGION_MAP_D_TAG], ["t", APP_TAG], ["t", REGION_TAG]],
    )

    async def _publish():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            result = await relay.publish_event(event)
            if result["accepted"]:
                storage.set_config("region_version", str(new_version))
                typer.echo(f"Region mapping v{new_version} published.")
            else:
                typer.echo(f"Rejected: {result['message']}")
        finally:
            await relay.close()

    asyncio.run(_publish())
    storage.close()


# ── Config ──

@config_app.command(name="set")
def config_set(key: str = typer.Argument(...), value: str = typer.Argument(...)):
    """Set a config value."""
    storage = _get_storage()
    storage.set_config(key, value)
    typer.echo(f"Set {key} = {value}")
    storage.close()


@config_app.command(name="show")
def config_show():
    """Show all config."""
    storage = _get_storage()
    relay = storage.get_config("relay", DEFAULT_RELAY)
    max_jobs = storage.get_config("max-jobs", str(DEFAULT_MAX_JOBS))
    typer.echo(f"relay:    {relay}")
    typer.echo(f"max-jobs: {max_jobs}")
    storage.close()


# ── Profile ──

PROFILE_CONTENT_VERSION = 1
PROFILE_TAG = "profile"


@profile_app.command(name="set")
def profile_set(
    name: str = typer.Option(..., "--name", help="Display name"),
    bio: str = typer.Option("", "--bio", help="Short bio"),
    avatar: str = typer.Option("", "--avatar", help="Avatar URL"),
):
    """Set your local profile."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    content = json.dumps({
        "name": name,
        "bio": bio,
        "avatar": avatar,
        "version": PROFILE_CONTENT_VERSION,
    }, ensure_ascii=False)

    storage = _get_storage()
    storage.upsert_profile(
        pubkey=identity["pubkey"],
        event_id="",
        d_tag="",
        content=content,
        created_at=0,
    )
    typer.echo(f"Profile saved for {identity['npub']}")
    storage.close()


@profile_app.command(name="show")
def profile_show(
    pubkey: Optional[str] = typer.Option(None, "--pubkey", help="Show profile for pubkey"),
):
    """Show your profile or another user's profile."""
    storage = _get_storage()

    if pubkey:
        # Show other user's profile
        profile = storage.get_profile(pubkey)
        if not profile:
            typer.echo("Profile not found locally. Run: agentboss profile fetch <npub>")
            storage.close()
            raise typer.Exit(code=1)
    else:
        # Show own profile
        identity = _load_identity()
        if not identity:
            typer.echo("No identity. Run: agentboss login --key <nsec>")
            storage.close()
            raise typer.Exit(code=1)
        profile = storage.get_own_profile(identity["pubkey"])
        if not profile:
            typer.echo("No profile set. Run: agentboss profile set --name <name>")
            storage.close()
            raise typer.Exit(code=1)

    try:
        content = json.loads(profile["content"])
        typer.echo(f"Name:   {content.get('name', 'N/A')}")
        typer.echo(f"Bio:    {content.get('bio', 'N/A')}")
        typer.echo(f"Avatar: {content.get('avatar', 'N/A')}")
        typer.echo(f"Pubkey: {profile['pubkey']}")
    except json.JSONDecodeError:
        typer.echo("Error: Invalid profile content")
    storage.close()


@profile_app.command(name="publish")
def profile_publish():
    """Publish your profile to the Nostr relay."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    profile = storage.get_own_profile(identity["pubkey"])
    if not profile:
        typer.echo("No profile set. Run: agentboss profile set --name <name>")
        storage.close()
        raise typer.Exit(code=1)

    import uuid
    d_tag = f"profile_{identity['pubkey']}"

    event = build_event(
        kind=KIND_APP_DATA,
        content=profile["content"],
        privkey=identity["privkey"],
        pubkey=identity["pubkey"],
        tags=[
            ["d", d_tag],
            ["t", APP_TAG],
            ["t", PROFILE_TAG],
        ],
    )

    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _publish():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            result = await relay.publish_event(event)
            if result["accepted"]:
                # Save event_id to local profile
                storage.upsert_profile(
                    pubkey=identity["pubkey"],
                    event_id=event["id"],
                    d_tag=d_tag,
                    content=profile["content"],
                    created_at=event["created_at"],
                )
                typer.echo(f"Published: {event['id'][:16]}...")
            else:
                typer.echo(f"Rejected: {result['message']}")
        finally:
            await relay.close()

    asyncio.run(_publish())
    storage.close()


@profile_app.command(name="fetch")
def profile_fetch(
    npub: str = typer.Argument(..., help="User npub to fetch profile for"),
):
    """Fetch a user's profile from the relay."""
    from shared.crypto import to_hex
    try:
        pubkey = to_hex(npub)
    except Exception:
        typer.echo("Invalid npub format")
        raise typer.Exit(code=1)

    storage = _get_storage()
    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _fetch():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            await relay.subscribe(
                "profile_fetch",
                kinds=[KIND_APP_DATA],
                authors=[pubkey],
                tags={"#t": [APP_TAG, PROFILE_TAG]},
                limit=1,
            )
            async for event in relay.receive_events("profile_fetch"):
                storage.upsert_profile(
                    pubkey=event["pubkey"],
                    event_id=event["id"],
                    d_tag=next((t[1] for t in event.get("tags", []) if t[0] == "d"), ""),
                    content=event["content"],
                    created_at=event["created_at"],
                )
                typer.echo(f"Profile fetched for {event['pubkey'][:16]}...")
            await relay.unsubscribe("profile_fetch")
        finally:
            await relay.close()

    asyncio.run(_fetch())
    storage.close()


# ── Applications ──────────────────────────────────────────────────────

@applications_app.command(name="list")
def applications_list(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status: pending, accepted, rejected"),
):
    """List your job applications."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    resolver = RegionResolver(storage)

    apps = storage.list_applications(
        applicant_pubkey=identity["pubkey"],
        status=status,
    )

    if not apps:
        typer.echo("No applications found.")
        storage.close()
        return

    for app_entry in apps:
        job = storage.get_job(app_entry["job_id"])
        job_title = "(unknown job)"
        if job:
            try:
                job_content = json.loads(job["content"])
                job_title = job_content.get("title", "(no title)")
            except (json.JSONDecodeError, KeyError):
                job_title = "(unknown job)"
        status_emoji = {"pending": "⏳", "accepted": "✅", "rejected": "❌"}.get(app_entry["status"], "?")
        typer.echo(f"{status_emoji} {job_title} | {app_entry['status']} | {app_entry['created_at']}")

    storage.close()


@applications_app.command(name="respond")
def applications_respond(
    app_id: str = typer.Argument(..., help="Application event ID"),
    accept: bool = typer.Option(False, "--accept", is_flag=True, help="Accept the application"),
    reject: bool = typer.Option(False, "--reject", is_flag=True, help="Reject the application"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Reason or feedback"),
):
    """Respond to a job application (employer only)."""
    if not (accept or reject):
        typer.echo("Use --accept or --reject")
        raise typer.Exit(code=1)

    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()

    # Get application
    app_record = storage.get_application(app_id)
    if not app_record:
        typer.echo("Application not found.")
        storage.close()
        raise typer.Exit(code=1)

    # Verify employer
    job = storage.get_job(app_record["job_id"])
    if not job or job["pubkey"] != identity["pubkey"]:
        typer.echo("Only the job publisher can respond to this application.")
        storage.close()
        raise typer.Exit(code=1)

    status = "accepted" if accept else "rejected"
    response_content = json.dumps({
        "message": reason or "",
    })

    # Build response event (kind:31971)
    event = build_event(
        kind=KIND_APP_DATA,
        content=response_content,
        privkey=identity["privkey"],
        pubkey=identity["pubkey"],
        tags=[
            ["d", app_record["d_tag"]],  # Same d_tag as application = replaceable
            ["job", app_record["job_id"]],
            ["status", status],
            ["t", "application_response"],
        ],
    )

    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _respond():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            result = await relay.publish_event(event)
            if not result["accepted"]:
                typer.echo(f"Failed to respond: {result['message']}")
                return

            # Send DM to applicant
            dm_content = json.dumps({
                "type": "application",
                "job_id": app_record["job_id"],
                "app_id": app_record["event_id"],
                "action": status,
                "message": reason or "",
            })
            try:
                await relay.send_dm(identity["privkey"], app_record["applicant_pubkey"], dm_content)
            except Exception as e:
                typer.echo(f"Warning: Could not notify applicant: {e}")

            # Update local status
            storage.update_application_status(app_record["event_id"], status, reason)
            typer.echo(f"Application {status}: {app_record['event_id'][:12]}...")
        finally:
            await relay.close()

    asyncio.run(_respond())
    storage.close()


# ── Federations ────────────────────────────────────────────────

@federation_app.command(name="join")
def federation_join(
    invite_code: str = typer.Argument(..., help="Federation invite code (federation:npub:name)"),
):
    """Join a federation by invite code.

    Fetches the relay list from the federation owner's npub and stores locally.
    """
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    # Parse invite code: federation:<npub_hex>:<name>
    if not invite_code.startswith("federation:"):
        typer.echo("Invalid invite code format. Must start with 'federation:'")
        raise typer.Exit(code=1)

    parts = invite_code.split(":", 2)
    if len(parts) != 3:
        typer.echo("Invalid invite code format. Use: federation:<npub_hex>:<name>")
        raise typer.Exit(code=1)
    _, federation_id, federation_name = parts

    # Validate npub hex (64 chars)
    if len(federation_id) != 64 or not all(c in "0123456789abcdef" for c in federation_id.lower()):
        typer.echo("Invalid federation npub. Must be 64 hex characters.")
        raise typer.Exit(code=1)

    storage = _get_storage()
    relay_url = storage.get_config("relay", DEFAULT_RELAY)

    async def _fetch_federation():
        relay = NostrRelay(relay_url)
        try:
            await relay.connect()
            await relay.subscribe(
                "fed_lookup",
                kinds=[KIND_FEDERATION],
                tags={"authors": [federation_id], "#d": [federation_name]},
            )
            events = []
            async for event in relay.receive_events("fed_lookup"):
                events.append(event)
            await relay.unsubscribe("fed_lookup")

            if not events:
                typer.echo(f"Federation '{federation_name}' not found for npub {federation_id[:16]}...")
                return

            latest = max(events, key=lambda e: e.get("created_at", 0))
            try:
                relay_urls = json.loads(latest["content"])
            except (json.JSONDecodeError, KeyError):
                typer.echo("Error: Invalid federation relay list in event content")
                return

            if not isinstance(relay_urls, list) or not all(isinstance(r, str) for r in relay_urls):
                typer.echo("Error: Federation relay list must be a JSON array of relay URLs")
                return

            storage.upsert_federation(
                federation_id=federation_id,
                name=federation_name,
                relay_urls=relay_urls,
                created_at=latest.get("created_at", int(time.time())),
            )
            typer.echo(f"Joined federation '{federation_name}' with {len(relay_urls)} relay(s)")
        finally:
            await relay.close()

    asyncio.run(_fetch_federation())
    storage.close()


@federation_app.command(name="list")
def federation_list():
    """List all joined federations."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    feds = storage.list_federations()
    if not feds:
        typer.echo("No federations joined. Run: agentboss federation join <code>")
        storage.close()
        return

    for fed in feds:
        relay_count = len(fed["relay_urls"])
        typer.echo(f"- {fed['name']} ({fed['federation_id'][:16]}...) | {relay_count} relay(s)")
    storage.close()


@federation_app.command(name="leave")
def federation_leave(
    federation_id: str = typer.Argument(..., help="Federation ID (npub hex)"),
    confirm: bool = typer.Option(False, "--yes", help="Skip confirmation"),
):
    """Leave and delete a federation."""
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    storage = _get_storage()
    fed = storage.get_federation(federation_id)
    if not fed:
        typer.echo("Federation not found.")
        storage.close()
        raise typer.Exit(code=1)

    if not confirm:
        typer.echo(f"Leave federation '{fed['name']}'? Use --yes to confirm.")
        storage.close()
        raise typer.Exit(code=1)

    storage.delete_federation(federation_id)
    typer.echo(f"Left federation '{fed['name']}'.")
    storage.close()


@federation_app.command(name="create")
def federation_create(
    name: str = typer.Argument(..., help="Federation name"),
    relays: list[str] = typer.Argument(..., help="Relay URLs (at least one)"),
):
    """Create a new federation and publish its metadata.

    Publishes a federation metadata event (kind:31990) to all specified relays.
    The resulting invite code can be shared with others to join this federation.
    """
    identity = _load_identity()
    if not identity:
        typer.echo("No identity. Run: agentboss login --key <nsec>")
        raise typer.Exit(code=1)

    if len(relays) < 1:
        typer.echo("At least one relay URL is required.")
        raise typer.Exit(code=1)

    storage = _get_storage()
    federation_id = identity["pubkey"]
    relay_urls = relays

    content = json.dumps(relay_urls)
    event = build_event(
        kind=KIND_FEDERATION,
        content=content,
        privkey=identity["privkey"],
        pubkey=identity["pubkey"],
        tags=[
            ["d", name],
            ["t", "agentboss"],
            ["t", "federation"],
        ],
    )

    async def _create():
        failed = []
        for relay_url in relay_urls:
            relay = NostrRelay(relay_url)
            try:
                await relay.connect()
                result = await relay.publish_event(event)
                if not result["accepted"]:
                    failed.append(f"{relay_url}: {result['message']}")
            except Exception as e:
                failed.append(f"{relay_url}: {e}")
            finally:
                await relay.close()

        if failed:
            typer.echo(f"Federation created but failed to publish to {len(failed)} relay(s):")
            for f in failed:
                typer.echo(f"  - {f}")
        else:
            typer.echo(f"Federation '{name}' created and published to {len(relay_urls)} relay(s).")

        storage.upsert_federation(
            federation_id=federation_id,
            name=name,
            relay_urls=relay_urls,
            created_at=event["created_at"],
        )

        invite_code = f"federation:{federation_id}:{name}"
        typer.echo(f"\nYour invite code: {invite_code}")
        typer.echo("Share this code with others to join your federation.")

    asyncio.run(_create())
    storage.close()
