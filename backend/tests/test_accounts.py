"""Tests for Milestone 9: the google_accounts migration, linking accounts, merging
users, the course-admin filter and syncing several accounts.

The migration, linking and merge tests need the local Postgres: they create a
throwaway database (command_center_test_<pid>), run the real Alembic migrations
on it and drop it at the end. They are skipped when Postgres isn't running.
"""

import os
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import main
from app.auth import accounts
from app.auth import routes as auth_routes
from app.auth.google import GoogleReconnectRequired, decrypt_token, encrypt_token
from app.config import settings
from app.llm import extract as ex
from app.models import GoogleAccount, Item, User
from app.sync import dryrun, runner

TZ = ZoneInfo("Europe/Istanbul")
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEFORE_M9 = "f6a7b8c9d0e1"


# --- a throwaway database ---------------------------------------------------------

@pytest.fixture(scope="module")
def pg_url():
    url = make_url(settings.DATABASE_URL)
    test_url = url.set(database=f"command_center_test_{os.getpid()}")
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}"'))
            conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except Exception as e:  # no local Postgres
        pytest.skip(f"Postgres not available: {type(e).__name__}")
    engine = create_engine(test_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    engine.dispose()
    yield test_url.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_url.database}" WITH (FORCE)'))
    admin.dispose()


def migrate(url: str, revision: str, monkeypatch, down: bool = False) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", url)  # alembic/env.py reads it
    config = Config(os.path.join(BACKEND, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND, "alembic"))
    (command.downgrade if down else command.upgrade)(config, revision)


@pytest.fixture
def db(pg_url, monkeypatch):
    """A session on the migrated test database; everything is rolled back afterwards."""
    migrate(pg_url, "head", monkeypatch)
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        trans = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield session
        session.close()
        trans.rollback()
    engine.dispose()


# --- migration --------------------------------------------------------------------

def test_migration_moves_tokens_and_tags_existing_rows(pg_url, monkeypatch):
    migrate(pg_url, "base", monkeypatch, down=True)
    migrate(pg_url, BEFORE_M9, monkeypatch)
    engine = create_engine(pg_url)
    token = encrypt_token("refresh-1")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email) VALUES (1, 'me@gmail.com'), (2, 'other@gmail.com')"))
        conn.execute(text("INSERT INTO oauth_tokens (user_id, provider, encrypted_refresh_token, scopes) "
                          "VALUES (1, 'google', :t, 'gmail calendar')"), {"t": token})
        conn.execute(text("INSERT INTO sync_state (user_id, source, cursor) VALUES "
                          "(1, 'gmail', '123'), (1, 'calendar', NULL), (1, 'sucourse', 'https://x')"))
        conn.execute(text("INSERT INTO items (user_id, source, external_id, type, title) VALUES "
                          "(1, 'gmail', 'm1', 'email', 'a'), (1, 'calendar', 'e1', 'event', 'b'), "
                          "(1, 'sucourse', 's1', 'assignment', 'c')"))

    migrate(pg_url, "head", monkeypatch)

    with engine.begin() as conn:
        accounts_rows = conn.execute(text("SELECT id, user_id, email, encrypted_refresh_token, scopes "
                                          "FROM google_accounts")).all()
        assert len(accounts_rows) == 1
        account_id, user_id, email, enc, scopes = accounts_rows[0]
        assert (user_id, email, scopes) == (1, "me@gmail.com", "gmail calendar")
        assert decrypt_token(enc) == "refresh-1"  # moved as is, still encrypted
        state = dict(conn.execute(text("SELECT source, account_id FROM sync_state")).all())
        assert state == {"gmail": account_id, "calendar": account_id, "sucourse": None}
        items = dict(conn.execute(text("SELECT source, account_id FROM items")).all())
        assert items == {"gmail": account_id, "calendar": account_id, "sucourse": None}
        assert conn.scalar(text("SELECT to_regclass('oauth_tokens')")) is None

        # The same event id can now exist once per account; SUCourse (NULL) stays unique.
        conn.execute(text("INSERT INTO google_accounts (user_id, email, encrypted_refresh_token) "
                          "VALUES (1, 'me@sabanciuniv.edu', 'x')"))
        conn.execute(text("INSERT INTO items (user_id, account_id, source, external_id, type, title) "
                          "SELECT 1, id, 'calendar', 'e1', 'event', 'b' FROM google_accounts "
                          "WHERE email = 'me@sabanciuniv.edu'"))
        upsert = text("INSERT INTO items (user_id, source, external_id, type, title) "
                      "VALUES (1, 'sucourse', 's1', 'assignment', 'new') "
                      "ON CONFLICT (user_id, source, account_id, external_id) DO UPDATE SET title = excluded.title")
        conn.execute(upsert)
        assert conn.scalar(text("SELECT count(*) FROM items WHERE source = 'sucourse'")) == 1
        assert conn.scalar(text("SELECT title FROM items WHERE source = 'sucourse'")) == "new"

    # Downgrade keeps each user's first account as their oauth_token.
    migrate(pg_url, BEFORE_M9, monkeypatch, down=True)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT user_id, provider FROM oauth_tokens")).all() == [(1, "google")]
        assert conn.scalar(text("SELECT count(*) FROM items WHERE source = 'calendar'")) == 1
    engine.dispose()
    migrate(pg_url, "base", monkeypatch, down=True)  # leave a clean database for the next tests


# --- linking ------------------------------------------------------------------------

def add_user(db, email):
    user = User(email=email)
    db.add(user)
    db.flush()
    return user


def test_link_adds_a_second_account_to_the_signed_in_user(db):
    me = add_user(db, "me@gmail.com")
    accounts.save_account(db, me, "me@gmail.com", "r1", "s")
    request = SimpleNamespace(session={"user_id": me.id})

    resp = auth_routes.finish_sign_in(request, db, "me@sabanciuniv.edu", "r2", "s", link_user_id=me.id)

    assert resp.headers["location"].endswith("#/settings")
    assert [a.email for a in accounts.linked_accounts(db, me)] == ["me@gmail.com", "me@sabanciuniv.edu"]
    assert db.query(User).filter_by(email="me@sabanciuniv.edu").first() is None  # no new user
    # Signing in later with the university account opens the same user.
    assert accounts.user_for_sign_in(db, "ME@sabanciuniv.edu").id == me.id


def test_plain_sign_in_with_a_new_email_creates_a_user(db):
    request = SimpleNamespace(session={})
    auth_routes.finish_sign_in(request, db, "new@gmail.com", "r", "s", link_user_id=None)
    user = db.query(User).filter_by(email="new@gmail.com").one()
    assert request.session["user_id"] == user.id
    assert [a.email for a in accounts.linked_accounts(db, user)] == ["new@gmail.com"]


def test_link_is_ignored_when_the_session_user_changed(db):
    me = add_user(db, "me@gmail.com")
    other = add_user(db, "other@gmail.com")
    # The link flag says `me`, but the session now belongs to `other`: no linking to `me`.
    request = SimpleNamespace(session={"user_id": other.id})
    auth_routes.finish_sign_in(request, db, "third@gmail.com", "r", "s", link_user_id=me.id)
    assert accounts.linked_accounts(db, me) == []


def test_an_account_cannot_be_linked_to_two_users(db):
    me, other = add_user(db, "me@gmail.com"), add_user(db, "other@gmail.com")
    accounts.save_account(db, other, "shared@gmail.com", "r", "s")
    with pytest.raises(accounts.AccountTaken):
        accounts.save_account(db, me, "shared@gmail.com", "r", "s")


def test_relinking_refreshes_the_token_and_a_missing_token_keeps_the_old_one(db):
    me = add_user(db, "me@gmail.com")
    account = accounts.save_account(db, me, "me@gmail.com", "old", "s")
    accounts.save_account(db, me, "me@gmail.com", None, None)
    assert decrypt_token(account.encrypted_refresh_token) == "old"
    accounts.save_account(db, me, "me@gmail.com", "new", "s2")
    assert decrypt_token(account.encrypted_refresh_token) == "new"
    with pytest.raises(ValueError):
        accounts.save_account(db, me, "second@gmail.com", None, None)  # new account needs a token


def test_link_endpoint_needs_sign_in_and_asks_google_to_pick_an_account():
    client = TestClient(main.app)
    assert client.get("/auth/google/link", follow_redirects=False).status_code == 401

    main.app.dependency_overrides[auth_routes.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        resp = client.get("/auth/google/link", follow_redirects=False)
    finally:
        main.app.dependency_overrides.clear()
    assert resp.status_code == 307
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["prompt"] == ["consent select_account"]
    assert query["access_type"] == ["offline"]


# --- merging users --------------------------------------------------------------------

def test_merge_moves_accounts_and_deletes_the_empty_user(db):
    me, test_user = add_user(db, "me@gmail.com"), add_user(db, "me@sabanciuniv.edu")
    accounts.save_account(db, me, "me@gmail.com", "r1", "s")
    accounts.save_account(db, test_user, "me@sabanciuniv.edu", "r2", "s")

    assert accounts.merge_users(db, test_user.id, me.id) == 1
    db.expire_all()
    assert db.get(User, test_user.id) is None
    assert [a.email for a in accounts.linked_accounts(db, me)] == ["me@gmail.com", "me@sabanciuniv.edu"]


def test_merge_refuses_a_user_with_data(db):
    me, busy = add_user(db, "me@gmail.com"), add_user(db, "busy@gmail.com")
    db.add(Item(user_id=busy.id, source="gmail", external_id="m", type="email", title="t"))
    db.flush()
    with pytest.raises(ValueError, match="items"):
        accounts.merge_users(db, busy.id, me.id)
    assert db.get(User, busy.id) is not None


# --- labels and the course-admin filter ------------------------------------------------

def test_labels():
    assert accounts.account_label("student.name@sabanciuniv.edu") == "Sabancı"
    assert accounts.account_label("someone@mail.sabanciuniv.edu") == "Sabancı"
    assert accounts.account_label("me@gmail.com") == "Personal"
    assert accounts.account_label("me@notsabanciuniv.edu") == "Personal"
    assert accounts.account_label(None) is None


@pytest.mark.parametrize("subject,body", [
    ("NS101 Week 4", ""),
    ("Question about ns 101", ""),
    ("Hi", "See you at the recitation tomorrow."),
    ("Worksheet 3 answers", ""),
    ("Thanks", "As a Learning Assistant, could you ..."),
    ("LA meeting", ""),
    ("Hi", "Our LAs will grade this."),
])
def test_course_admin_filter_matches(subject, body):
    assert ex.is_course_admin(subject, body, "student@sabanciuniv.edu")
    assert ex.course_admin_matches(subject, body, None)


@pytest.mark.parametrize("subject,body", [
    ("Atlas internship", "la la land"),       # lowercase "la" and words containing it
    ("CS204 homework", "Submit by Friday."),
    ("Los Angeles trip", "Flights to LAX"),
])
def test_course_admin_filter_leaves_other_mail(subject, body):
    assert not ex.is_course_admin(subject, body, "prof@sabanciuniv.edu")


def test_course_admin_sender_list():
    assert ex.is_course_admin("Hello", "", "NS101 Course <ns101-list@sabanciuniv.edu>")
    assert "sender: ns\\s?-?101" in ex.course_admin_matches("Hello", "", "ns101-list@sabanciuniv.edu")


class FakeDB:
    def __init__(self, items):
        self.items, self.added = items, []

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: list(self.items))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def email(id, title, account_id, sender="Registrar <noreply@sabanciuniv.edu>"):
    return Item(id=id, title=title, body="", sender=sender, account_id=account_id,
                occurred_at=datetime(2026, 10, 6, 9, 0, tzinfo=TZ), processed=False)


def test_university_mail_is_allowed_by_default_except_blocked_senders():
    student = "Ali Yilmaz <Ali.Yilmaz@sabanciuniv.edu>"
    assert ex.university_skip("Can I get an extension?", "", student) is None  # allowed by default
    blocked = {"ali.yilmaz@sabanciuniv.edu"}
    assert ex.university_skip("Can I get an extension?", "", student, blocked) == (
        ex.BLOCKED_SENDER_SUMMARY, "skipped_blocked_sender")
    # Matched on the address, not the display name.
    assert ex.university_skip("Hi", "", "Ali Yilmaz <someone.else@sabanciuniv.edu>", blocked) is None
    # Course-admin keywords still apply to everyone.
    assert ex.university_skip("NS101 recitation", "", "noreply@sabanciuniv.edu") == (
        ex.COURSE_ADMIN_SUMMARY, "skipped_course_admin")


def test_university_filters_never_reach_claude(monkeypatch):
    sent = []

    def fake_extract(item, today, tasks):
        sent.append(item.id)
        return ex.Extraction(is_actionable=False, tasks=[], summary="FYI."), ex.Usage(100, 10, 0, 0)

    monkeypatch.setattr(ex, "extract", fake_extract)
    monkeypatch.setattr(ex, "open_tasks", lambda db, user, limit: [])
    monkeypatch.setattr(ex, "university_account_ids", lambda db, user_id: {2})
    monkeypatch.setattr(ex, "blocked_addresses", lambda db, user_id: {"spam.student@sabanciuniv.edu"})
    student = "Ali <ali.yilmaz@sabanciuniv.edu>"
    blocked = "Spam <Spam.Student@sabanciuniv.edu>"
    items = [email(1, "NS101 recitation groups", account_id=2),               # course admin: skipped
             email(2, "NS101 recitation groups", account_id=1, sender=student),  # personal account: unchanged
             email(3, "Library hours", account_id=2),                         # harmless: sent
             email(4, "Can I get an extension?", account_id=2, sender=student),  # student, not blocked: sent
             email(5, "Hello again", account_id=2, sender=blocked),           # blocked sender: skipped
             email(6, "Hello again", account_id=1, sender=blocked)]           # personal account: unchanged

    stats = ex.process_unprocessed(FakeDB(items), SimpleNamespace(id=1), limit=10)

    assert sent == [2, 3, 4, 6]
    assert items[0].summary == ex.COURSE_ADMIN_SUMMARY and items[0].processed
    assert items[4].summary == ex.BLOCKED_SENDER_SUMMARY and items[4].processed
    assert stats["skipped_course_admin"] == 1 and stats["skipped_blocked_sender"] == 1


def test_first_sync_cap_marks_the_rest_as_skipped(monkeypatch):
    sent = []

    def fake_extract(item, today, tasks):
        sent.append(item.id)
        return ex.Extraction(is_actionable=False, tasks=[], summary="FYI."), ex.Usage(1000, 0, 0, 0)

    monkeypatch.setattr(ex, "extract", fake_extract)
    monkeypatch.setattr(ex, "open_tasks", lambda db, user, limit: [])
    monkeypatch.setattr(ex, "university_account_ids", lambda db, user_id: set())
    monkeypatch.setattr(ex, "estimate_cost", lambda model, i, o, cw, cr: i / 100_000)  # $0.01 per call
    items = [email(i, f"Email {i}", account_id=5) for i in range(1, 6)]

    stats = ex.process_unprocessed(FakeDB(items), SimpleNamespace(id=1), limit=100, max_cost_usd=0.025,
                                   account_id=5, skip_over_cap=True)

    assert sent == [1, 2]
    assert stats["skipped_over_cap"] == 3
    assert all(i.processed for i in items)  # none left for a later, uncapped run
    assert items[4].summary == ex.OVER_CAP_SUMMARY


# --- syncing several accounts -------------------------------------------------------------

PERSONAL = SimpleNamespace(id=1, email="me@gmail.com")
SABANCI = SimpleNamespace(id=2, email="me@sabanciuniv.edu")
EMPTY_RUN = {k: 0 for k in runner.EXTRACTION_KEYS}


@pytest.fixture
def two_accounts(monkeypatch):
    calls = {"gmail": [], "extract": []}

    def fake_gmail(db, user, account):
        calls["gmail"].append(account.id)
        return 3

    def fake_extract(db, user, limit, **kwargs):
        calls["extract"].append(kwargs)
        return dict(EMPTY_RUN, processed=1, estimated_cost_usd=0.01)

    monkeypatch.setattr(runner, "linked_accounts", lambda db, user: [PERSONAL, SABANCI])
    monkeypatch.setattr(runner, "is_first_sync", lambda db, user, account: account is SABANCI)
    monkeypatch.setattr(runner, "sync_gmail", fake_gmail)
    monkeypatch.setattr(runner, "sync_calendar", lambda db, user, account: {"added": 1, "updated": 0, "deleted": 0})
    monkeypatch.setattr(runner, "sync_sucourse", lambda db, user: {"tasks_created": 0})
    monkeypatch.setattr(runner, "process_unprocessed", fake_extract)
    return calls


def test_sync_covers_every_account_and_caps_a_new_accounts_first_extraction(two_accounts):
    result = runner.run_full_sync(SimpleNamespace(rollback=lambda: None), SimpleNamespace(id=1))

    assert two_accounts["gmail"] == [1, 2]
    assert result["gmail"] == {"added": 6}
    assert result["calendar"]["added"] == 2
    first, regular = two_accounts["extract"]
    assert first == {"max_cost_usd": 0.30, "account_id": 2, "skip_over_cap": True}
    assert regular == {}
    assert result["extraction"]["processed"] == 2 and result["extraction"]["estimated_cost_usd"] == 0.02


def test_one_account_needing_reconnect_does_not_stop_the_other(two_accounts, monkeypatch):
    def gmail(db, user, account):
        if account is SABANCI:
            raise GoogleReconnectRequired("revoked")
        return 4

    monkeypatch.setattr(runner, "sync_gmail", gmail)
    result = runner.run_full_sync(SimpleNamespace(rollback=lambda: None), SimpleNamespace(id=1))
    assert result["gmail"] == {"added": 4}
    assert result["reconnect_needed"] == ["me@sabanciuniv.edu"]

    monkeypatch.setattr(runner, "sync_gmail", lambda db, user, account: (_ for _ in ()).throw(
        GoogleReconnectRequired("revoked")))
    with pytest.raises(GoogleReconnectRequired):
        runner.run_full_sync(SimpleNamespace(rollback=lambda: None), SimpleNamespace(id=1))


# --- dry run --------------------------------------------------------------------------------

def test_dry_run_classifies_without_showing_content():
    student = {"title": "Recitation 2 groups", "body": "", "sender": "Ali <ali@sabanciuniv.edu>"}
    assert dryrun.classify(student, university=True) == ("course admin", [r"\brecitations?\b"])
    assert dryrun.classify(student, university=False) == ("to Claude", [])
    question = dict(student, title="Grades are out")
    assert dryrun.classify(question, university=True) == ("to Claude", [])
    assert dryrun.classify(question, university=True, blocked={"ali@sabanciuniv.edu"})[0] == "blocked sender"
    otp = {"title": "Your OTP", "body": "NS101", "sender": "bank@x.com"}
    assert dryrun.classify(otp, university=True)[0] == "sensitive"


# --- blocking senders (needs Postgres) -------------------------------------------------------

def test_sender_list_and_blocking(db):
    from app.auth import university
    from app.models import Task

    me = add_user(db, "me@gmail.com")
    personal = accounts.save_account(db, me, "me@gmail.com", "r1", "s")
    sabanci = accounts.save_account(db, me, "me@sabanciuniv.edu", "r2", "s")

    def add_email(ext, account, sender, day):
        item = Item(user_id=me.id, account_id=account.id, source="gmail", external_id=ext, type="email",
                    title="t", sender=sender, occurred_at=datetime(2026, 10, day, 9, 0, tzinfo=TZ))
        db.add(item)
        db.flush()
        return item

    first = add_email("1", sabanci, "ali.y@sabanciuniv.edu", 1)
    latest = add_email("2", sabanci, "Ali Yilmaz <Ali.Y@sabanciuniv.edu>", 3)
    add_email("3", sabanci, "Registrar <registrar@sabanciuniv.edu>", 2)
    on_personal = add_email("4", personal, "Ali Yilmaz <ali.y@sabanciuniv.edu>", 4)
    tasks = [Task(user_id=me.id, item_id=i.id, title="x", created_by="extraction")
             for i in (first, latest, on_personal)]
    db.add_all(tasks)
    db.flush()

    senders = university.university_senders(db, me)
    assert [(s["address"], s["name"], s["email_count"], s["blocked"]) for s in senders] == [
        ("ali.y@sabanciuniv.edu", "Ali Yilmaz", 2, False),       # personal-account email not counted
        ("registrar@sabanciuniv.edu", "Registrar", 1, False),
    ]
    assert senders[0]["last_email_at"].startswith("2026-10-03")
    assert set(senders[0]) == {"address", "name", "email_count", "last_email_at", "blocked"}  # no subjects

    assert university.block_sender(db, me, "ali.y@sabanciuniv.edu") == 2
    assert university.block_sender(db, me, "ali.y@sabanciuniv.edu") == 0  # blocking twice is fine
    assert [t.status for t in tasks] == ["done", "done", "open"]  # the personal-account task stays open
    assert accounts.blocked_addresses(db, me.id) == {"ali.y@sabanciuniv.edu"}
    assert university.university_senders(db, me)[0]["blocked"] is True


def test_block_endpoint_validates_and_never_echoes_bad_input(monkeypatch):
    from app.auth import university

    main.app.dependency_overrides[auth_routes.get_current_user] = lambda: SimpleNamespace(id=1)
    seen = []
    monkeypatch.setattr(university, "block_sender", lambda db, user, address: seen.append(address) or 3)
    main.app.dependency_overrides[university.get_db] = lambda: SimpleNamespace(commit=lambda: None)
    try:
        client = TestClient(main.app)
        assert client.post("/university/senders/block", json={"address": "not an address"}).status_code == 422
        resp = client.post("/university/senders/block", json={"address": "  Ali.Y@SabanciUniv.edu "})
    finally:
        main.app.dependency_overrides.clear()
    assert resp.json() == {"address": "ali.y@sabanciuniv.edu", "blocked": True, "tasks_closed": 3}
    assert seen == ["ali.y@sabanciuniv.edu"]
