"""End-to-end test of the whole journey, using the real Flask app and a temporary SQLite DB.

Uses the development fallback embedder so it runs anywhere. To test with the supplied model,
run with  HX_TEST_REAL_MODEL=1.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import similarity  # noqa: E402
from app import create_app  # noqa: E402
from cli import seed_base  # noqa: E402
from config import Config  # noqa: E402
from db_models import Complaint, ComplaintGroup, Notification, Organization, Suggestion, User, db  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def app(tmp_path):
    similarity.reset_embedder()
    use_real_model = os.environ.get("HX_TEST_REAL_MODEL") == "1"
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 't.db').as_posix()}",
        "UPLOAD_FOLDER": str(tmp_path / "up"),
        "MODEL_ROOT": Config.MODEL_ROOT if use_real_model else str(tmp_path / "no_model_here"),
        "ALLOW_FALLBACK_EMBEDDER": not use_real_model,
        "SIMILARITY_THRESHOLD": None if use_real_model else 0.30,
    })
    with app.app_context():
        seed_base()
    yield app
    similarity.reset_embedder()


def token(client):
    client.get("/login")                       # renders a page, which creates the CSRF token
    with client.session_transaction() as s:
        return s["csrf"]


def register(client, email, name="Test User", org_id=1):
    t = token(client)
    return client.post("/register", data=dict(csrf_token=t, name=name, email=email, password="correct-horse-1",
                                              organization_id=org_id), follow_redirects=False)


def login(client, email, pw="correct-horse-1"):
    t = token(client)
    return client.post("/login", data=dict(csrf_token=t, email=email, password=pw))


def h(client):
    with client.session_transaction() as s:
        return {"X-CSRF-Token": s["csrf"]}


def submit(client, title, desc, location="Hostel Block", severity="high", org=1, image=None):
    data = dict(category="Infrastructure", title=title, description=desc, location=location,
                severity=severity, organization_id=str(org), user_type="student", sector="education")
    if image:
        import io
        data["image"] = (io.BytesIO(image), "x.png")
    return client.post("/api/complaints", data=data, headers=h(client), content_type="multipart/form-data")


A_TITLE = "Water leakage in Block B corridor"
A_DESC = "Water is continuously leaking from the ceiling in the second floor corridor of Block B and the floor is slippery."
B_TITLE = "Ceiling dripping near Block B stairs"
B_DESC = "The ceiling in the Block B second floor corridor drips water after rain and the floor is slippery for students."
C_TITLE = "Cafeteria menu has too few vegetarian options"
C_DESC = "The cafeteria offers very limited vegetarian choices at dinner time and they run out early every day."


def test_full_journey(app):
    imported = app.test_cli_runner().invoke(args=["import-organizations"])
    assert imported.exit_code == 0, imported.output
    user = app.test_client()
    other = app.test_client()
    staff = app.test_client()

    # --- auth + CSRF
    assert user.post("/api/complaints", data={}).status_code in (400, 401)       # no CSRF token
    assert user.get("/dashboard").status_code == 302                              # login required
    assert user.get("/api/dashboard").status_code == 401
    assert register(user, "a@example.com").status_code == 302
    assert register(other, "b@example.com", "Other User").status_code == 302
    dashboard = user.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"Submit a complaint" in dashboard.data and b"My Complaints" in dashboard.data

    with app.app_context():
        s = User(name="Staff One", email="staff@example.com", role="staff", organization_id=1)
        s.set_password("correct-horse-1")
        db.session.add(s)
        db.session.commit()
    assert login(staff, "staff@example.com").status_code == 302
    staff_dashboard = staff.get("/staff")
    assert staff_dashboard.status_code == 200
    assert b"Total complaints" in staff_dashboard.data and b"AI suggestions to review" in staff_dashboard.data

    # --- validation
    bad = submit(user, "short", "too short")
    assert bad.status_code == 422 and "fields" in bad.get_json()
    wrong_org = submit(user, A_TITLE, A_DESC, org=99)
    assert wrong_org.status_code == 422
    wrong_sector = user.post("/api/complaints", data=dict(
        category="Infrastructure", title=A_TITLE, description=A_DESC, location="Hostel Block",
        severity="medium", organization_id="1", user_type="student", sector="healthcare",
    ), headers=h(user), content_type="multipart/form-data")
    assert wrong_sector.status_code == 422
    assert submit(user, A_TITLE, A_DESC, image=b"not an image").status_code == 422

    # --- submit complaint A (with a real PNG header), then a near-duplicate B and an unrelated C
    r = submit(user, A_TITLE, A_DESC, image=PNG)
    assert r.status_code == 201, r.get_json()
    a_id = r.get_json()["id"]
    b_id = submit(other, B_TITLE, B_DESC).get_json()["id"]
    c_id = submit(other, C_TITLE, C_DESC, location="Cafeteria", severity="low").get_json()["id"]

    # --- analysis: possible matches are organizer-only
    an = other.get(f"/api/complaints/{b_id}/analysis").get_json()
    assert an["status"] == "Awaiting Staff Review"
    assert "matches" not in an and "score" not in str(an)
    assert other.get(f"/api/complaints/{c_id}/analysis").get_json() == {
        "id": c_id, "status": "Awaiting Staff Review",
    }
    assert user.get("/api/dashboard").get_json()["common_issues"] == []

    # --- privacy / access control
    assert other.get(f"/api/complaints/{a_id}").status_code == 404               # not theirs
    assert user.get("/api/staff/summary").status_code == 403
    assert user.get(f"/api/complaints/{a_id}/image").status_code == 200
    assert other.get(f"/api/complaints/{a_id}/image").status_code == 404
    assert user.get("/api/dashboard?org=2").status_code == 403

    # --- staff review
    summ = staff.get("/api/staff/summary").get_json()
    assert summ["total_groups"] == 3 and summ["pending_suggestions"] == 1
    assert summ["total_complaints"] == 3
    assert summ["open_complaints"] == 3 and summ["resolved_complaints"] == 0
    assert summ["similar_groups"] == 0
    assert staff.get("/api/staff/groups?sector=healthcare").get_json()["items"] == []
    assert staff.get("/api/staff/summary?sector=invalid").status_code == 422
    sug = staff.get("/api/staff/suggestions").get_json()["items"]
    assert len(sug) == 1 and sug[0]["complaint"]["id"] == b_id
    assert sug[0]["complaint"]["status"] == "Awaiting Staff Review"
    assert sug[0]["score_scope"].startswith("maximum similarity")
    assert sug[0]["complaint_pairs"][0]["complaint_id"] == a_id
    assert 0 <= sug[0]["complaint_pairs"][0]["score"] <= 1
    staff_analysis = staff.get(f"/api/complaints/{b_id}/analysis").get_json()
    assert len(staff_analysis["matches"]) == 1 and 0 < staff_analysis["matches"][0]["score"] <= 1
    assert summ["evaluation"] == {"available": False, "message": "Evaluation data unavailable."}
    assert user.post(f"/api/staff/suggestions/{sug[0]['id']}/confirm", headers=h(user)).status_code == 403

    assert staff.post(f"/api/staff/suggestions/{sug[0]['id']}/confirm", headers=h(staff)).status_code == 200
    assert staff.post(f"/api/staff/suggestions/{sug[0]['id']}/confirm", headers=h(staff)).status_code == 409

    groups = staff.get("/api/staff/groups").get_json()["items"]
    assert [g["count"] for g in groups] == [2, 1]                                # merged group, then the cafeteria
    gid = groups[0]["id"]
    assert staff.get("/api/staff/groups?q=Ceiling%20dripping").get_json()["items"][0]["id"] == gid
    assert staff.get("/api/staff/summary").get_json()["similar_groups"] == 1
    detail = staff.get(f"/api/groups/{gid}").get_json()
    assert {c["id"] for c in detail["confirmed"]} == {a_id, b_id}                # both complaints preserved
    assert detail["proposed"] == []
    with app.app_context():
        assert db.session.get(Complaint, b_id) is not None
        assert ComplaintGroup.query.count() == 2                                 # B's empty singleton group was retired

    # --- status + notes + priority
    r = staff.patch(f"/api/groups/{gid}", json=dict(status="In Progress", priority="high",
                                                    internal_note="Plumber booked", public_update="Plumber visiting Monday"),
                    headers=h(staff))
    assert r.status_code == 200
    assert staff.get("/api/staff/summary").get_json()["open_complaints"] == 3
    assert staff.patch(f"/api/groups/{gid}", json=dict(status="Banana"), headers=h(staff)).status_code == 422
    for client, cid in ((user, a_id), (other, b_id)):
        d = client.get(f"/api/complaints/{cid}").get_json()
        assert d["status"] == "In Progress" and d["priority"] == "high"
        assert d["updates"][0]["message"] == "Plumber visiting Monday"
        assert "Plumber booked" not in str(d)                                     # internal notes never leak
        assert [x["status"] for x in d["history"]][-1] == "In Progress"
    notes = user.get("/api/notifications").get_json()
    assert notes["unread_count"] >= 2
    user.post("/api/notifications/read", headers=h(user))
    assert user.get("/api/notifications?unread=1").get_json()["items"] == []

    # --- feedback only after resolution, once
    assert user.post(f"/api/complaints/{a_id}/feedback", json=dict(rating=5), headers=h(user)).status_code == 409
    staff.patch(f"/api/groups/{gid}", json=dict(status="Resolved"), headers=h(staff))
    resolved_summary = staff.get("/api/staff/summary").get_json()
    assert resolved_summary["open_complaints"] == 1
    assert resolved_summary["resolved_complaints"] == 2
    assert resolved_summary["similar_groups"] == 1
    assert user.post(f"/api/complaints/{a_id}/feedback", json=dict(rating=9), headers=h(user)).status_code == 422
    assert user.post(f"/api/complaints/{a_id}/feedback", json=dict(rating=4, comment="ok"), headers=h(user)).status_code == 200
    assert user.post(f"/api/complaints/{a_id}/feedback", json=dict(rating=4), headers=h(user)).status_code == 409
    assert user.get(f"/api/complaints/{a_id}").get_json()["feedback_submitted"] is True

    # --- reopen
    assert other.post(f"/api/complaints/{b_id}/reopen", headers=h(other)).status_code == 200
    assert other.get(f"/api/complaints/{b_id}").get_json()["status"] == "Awaiting Staff Review"

    # --- lists and filters
    assert len(user.get("/api/complaints?mine=1").get_json()["items"]) == 1
    assert len(other.get("/api/complaints?mine=1&q=cafeteria").get_json()["items"]) == 1
    assert len(staff.get("/api/complaints").get_json()["items"]) == 3
    assert user.get("/api/dashboard").get_json()["counts"]["mine"] == 1

    # --- public endpoints
    st = app.test_client().get("/api/stats").get_json()
    assert st["complaints"] == 3 and st["is_sample"] is False
    assert "groups" not in st
    assert app.test_client().get("/api/organizations?q=reva").get_json()["items"][0]["name"] == "REVA University"
    health_orgs = app.test_client().get("/api/organizations?sector=healthcare").get_json()["items"]
    assert health_orgs and all(item["sector"] == "healthcare" for item in health_orgs)
    assert health_orgs[0]["state"] and health_orgs[0]["source_url"]
    assert app.test_client().get("/api/organizations?sector=healthcare&location=Mumbai").get_json()["items"][0]["city"] == "Mumbai"
    assert app.test_client().get("/api/health").get_json()["status"] == "ok"


def test_organization_import_is_idempotent(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["import-organizations"])
    assert first.exit_code == 0, first.output
    with app.app_context():
        from db_models import Organization
        before = Organization.query.count()
        reva = Organization.query.filter_by(name="REVA University").one()
        reva.locations = ["Keep existing location"]
        db.session.commit()
    second = runner.invoke(args=["import-organizations"])
    assert second.exit_code == 0, second.output
    with app.app_context():
        assert Organization.query.count() == before
        assert Organization.query.filter_by(name="REVA University").one().locations == ["Keep existing location"]


def test_user_can_submit_to_listed_organization_without_cross_org_access(app):
    runner = app.test_cli_runner()
    imported = runner.invoke(args=["import-organizations"])
    assert imported.exit_code == 0, imported.output
    user, first_staff, target_staff, admin = (app.test_client(), app.test_client(), app.test_client(),
                                               app.test_client())
    register(user, "cross-org@example.com")
    with app.app_context():
        organizations = Organization.query.filter_by(sector="education").order_by(Organization.id).all()
        target_org = next(org for org in organizations if org.id != 1)
        target_id = target_org.id
        first_staff_user = User(name="Staff One", email="staff-one@example.com", role="staff", organization_id=1)
        target_staff_user = User(name="Staff Two", email="staff-two@example.com", role="staff", organization_id=target_id)
        first_staff_user.set_password("correct-horse-1")
        target_staff_user.set_password("correct-horse-1")
        admin_user = User(name="Admin", email="admin@example.com", role="admin")
        admin_user.set_password("correct-horse-1")
        db.session.add_all([first_staff_user, target_staff_user, admin_user])
        db.session.commit()
        target_sector = target_org.sector

    login(first_staff, "staff-one@example.com")
    login(target_staff, "staff-two@example.com")
    login(admin, "admin@example.com")
    first_id = submit(user, A_TITLE, A_DESC).get_json()["id"]
    target_result = user.post("/api/complaints", data=dict(
        category="Infrastructure", title=A_TITLE, description=A_DESC, location="Library",
        severity="medium", organization_id=str(target_id), user_type="employee", sector=target_sector,
    ), headers=h(user), content_type="multipart/form-data")
    assert target_result.status_code == 201, target_result.get_json()
    target_complaint_id = target_result.get_json()["id"]
    assert user.get(f"/api/complaints/{target_complaint_id}").status_code == 200
    assert target_staff.get(f"/api/complaints/{target_complaint_id}").status_code == 200
    assert first_staff.get(f"/api/complaints/{target_complaint_id}").status_code == 404
    assert target_staff.get(f"/api/complaints/{first_id}").status_code == 404
    assert len(first_staff.get(f"/api/staff/groups?org={target_id}").get_json()["items"]) == 1
    assert first_staff.get(f"/api/staff/summary?org={target_id}").get_json()["total_complaints"] == 1
    assert admin.get(f"/api/staff/summary?org={target_id}").get_json()["total_complaints"] == 1
    assert admin.get("/api/staff/summary?org=invalid").status_code == 422
    assert target_staff.get("/api/staff/suggestions").get_json()["items"] == []
    assert user.get(f"/api/complaints/{target_complaint_id}/analysis").get_json() == {
        "id": target_complaint_id, "status": "Awaiting Staff Review",
    }


def test_reject_keeps_complaints_separate(app):
    u, st = app.test_client(), app.test_client()
    register(u, "a@example.com")
    with app.app_context():
        s = User(name="S", email="s@example.com", role="staff", organization_id=1)
        s.set_password("correct-horse-1")
        db.session.add(s)
        db.session.commit()
    login(st, "s@example.com")
    submit(u, A_TITLE, A_DESC)
    submit(u, B_TITLE, B_DESC)
    sid = st.get("/api/staff/suggestions").get_json()["items"][0]["id"]
    assert st.post(f"/api/staff/suggestions/{sid}/reject", headers=h(st)).status_code == 200
    assert st.get("/api/staff/suggestions").get_json()["items"] == []
    assert len(st.get("/api/staff/groups").get_json()["items"]) == 2
    with app.app_context():
        assert Suggestion.query.filter_by(status="rejected").count() == 1


def test_staff_cannot_cross_organizations(app):
    from db_models import Organization
    with app.app_context():
        db.session.add(Organization(name="Other Org", sector="corporate", city="X"))
        s = User(name="S", email="s@example.com", role="staff", organization_id=2)
        s.set_password("correct-horse-1")
        db.session.add(s)
        db.session.commit()
    u, st = app.test_client(), app.test_client()
    assert app.test_client().get("/api/organizations?sector=corporate").get_json()["items"][0]["name"] == "Other Org"
    register(u, "a@example.com", org_id=1)
    login(st, "s@example.com")
    cid = submit(u, A_TITLE, A_DESC).get_json()["id"]
    assert st.get(f"/api/complaints/{cid}").status_code == 404
    assert st.get("/api/staff/groups").get_json()["items"] == []
    gid = Complaint_group(app, cid)
    assert st.get(f"/api/groups/{gid}").status_code == 404
    assert st.patch(f"/api/groups/{gid}", json=dict(status="Resolved"), headers=h(st)).status_code == 404


def Complaint_group(app, cid):
    with app.app_context():
        return db.session.get(Complaint, cid).group_id


def test_open_redirect_blocked_and_login_errors(app):
    c = app.test_client()
    register(c, "a@example.com")
    c2 = app.test_client()
    t = token(c2)
    r = c2.post("/login", data=dict(csrf_token=t, email="a@example.com", password="correct-horse-1", next="//evil.com"))
    assert r.status_code == 302 and "evil.com" not in r.headers["Location"]
    c3 = app.test_client()
    t = token(c3)
    assert c3.post("/login", data=dict(csrf_token=t, email="a@example.com", password="nope")).status_code == 401
