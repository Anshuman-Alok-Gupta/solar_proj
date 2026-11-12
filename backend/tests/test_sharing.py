"""Sharing (invites, roles), password change and PDF report export."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import seed_days


def _invite(client, auth, pid, email="family@example.com", role="viewer"):
    r = client.post(f"/api/plants/{pid}/invitations", headers=auth, json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def test_invite_accept_creates_viewer_with_read_only_access(client, auth, plant):
    pid = plant["id"]
    inv = _invite(client, auth, pid)
    assert inv["status"] == "pending" and len(inv["token"]) > 30

    info = client.get(f"/api/invitations/{inv['token']}").json()
    assert info == {
        **info,
        "email": "family@example.com",
        "plant_name": "Test plant",
        "role": "viewer",
        "status": "pending",
        "account_exists": False,
    }

    assert (
        client.post(f"/api/invitations/{inv['token']}/accept", json={"password": "short"}).status_code == 422
    )
    r = client.post(
        f"/api/invitations/{inv['token']}/accept", json={"password": "family-pass-1", "full_name": "Asha"}
    )
    assert r.status_code == 200, r.text
    viewer = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # one-time link
    assert (
        client.post(f"/api/invitations/{inv['token']}/accept", json={"password": "x" * 9}).status_code == 410
    )

    plants = client.get("/api/plants", headers=viewer).json()
    assert [p["id"] for p in plants] == [pid] and plants[0]["access_role"] == "viewer"
    assert client.get(f"/api/plants/{pid}/analytics/summary", headers=viewer).status_code == 200
    # read-only: no settings changes, no sharing management, no deleting
    assert client.patch(f"/api/plants/{pid}", headers=viewer, json={"capacity_kw": 1}).status_code == 403
    assert client.get(f"/api/plants/{pid}/members", headers=viewer).status_code == 403
    assert client.delete(f"/api/plants/{pid}", headers=viewer).status_code == 403
    # the AI analyst stays available to viewers (it is not configured in tests -> 503, not 403)
    assert (
        client.post(f"/api/plants/{pid}/ai/chat", headers=viewer, json={"message": "hi"}).status_code != 403
    )

    members = client.get(f"/api/plants/{pid}/members", headers=auth).json()
    assert [m["email"] for m in members["members"]] == ["family@example.com"]
    assert members["invitations"] == []
    uid = members["members"][0]["user_id"]

    # promote to editor -> can change settings, still cannot manage sharing
    assert (
        client.patch(f"/api/plants/{pid}/members/{uid}", headers=auth, json={"role": "editor"}).status_code
        == 200
    )
    assert client.patch(f"/api/plants/{pid}", headers=viewer, json={"capacity_kw": 5.2}).status_code == 200
    assert client.get(f"/api/plants/{pid}/members", headers=viewer).status_code == 403

    # removal revokes access
    assert client.delete(f"/api/plants/{pid}/members/{uid}", headers=auth).status_code == 204
    assert client.get(f"/api/plants/{pid}", headers=viewer).status_code == 403
    assert client.get("/api/plants", headers=viewer).json() == []


def test_invite_existing_account_requires_its_password(client, auth, plant, db):
    from app.core.security import hash_password
    from app.db.models import User

    db.add(
        User(email="neighbour@example.com", hashed_password=hash_password("old-password-1"), is_active=True)
    )
    db.commit()
    inv = _invite(client, auth, plant["id"], email="Neighbour@Example.com", role="editor")
    assert client.get(f"/api/invitations/{inv['token']}").json()["account_exists"] is True
    bad = client.post(f"/api/invitations/{inv['token']}/accept", json={"password": "wrong-password"})
    assert bad.status_code == 401
    ok = client.post(f"/api/invitations/{inv['token']}/accept", json={"password": "old-password-1"})
    assert ok.status_code == 200
    tok = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert client.get("/api/plants", headers=tok).json()[0]["access_role"] == "editor"


def test_revoked_and_expired_invites_and_validation(client, auth, plant, db):
    pid = plant["id"]
    inv = _invite(client, auth, pid)
    listed = client.get(f"/api/plants/{pid}/members", headers=auth).json()["invitations"]
    assert [i["id"] for i in listed] == [inv["id"]] and "token" not in listed[0]
    assert client.delete(f"/api/plants/{pid}/invitations/{inv['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/invitations/{inv['token']}").json()["status"] == "revoked"
    assert (
        client.post(f"/api/invitations/{inv['token']}/accept", json={"password": "x" * 10}).status_code == 410
    )

    from app.db.models import Invitation

    inv2 = _invite(client, auth, pid, email="late@example.com")
    row = db.get(Invitation, inv2["id"])
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert client.get(f"/api/invitations/{inv2['token']}").json()["status"] == "expired"

    assert client.get("/api/invitations/not-a-real-token").status_code == 404
    assert (
        client.post(
            f"/api/plants/{pid}/invitations", headers=auth, json={"email": "admin@example.com"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/plants/{pid}/invitations", headers=auth, json={"email": "not-an-email"}
        ).status_code
        == 422
    )


def test_change_password(client, auth):
    from app.core.config import get_settings

    s = get_settings()
    url = "/api/auth/change-password"
    assert (
        client.post(
            url, headers=auth, json={"current_password": "nope", "new_password": "new-password-1"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            url, headers=auth, json={"current_password": s.admin_password, "new_password": "short"}
        ).status_code
        == 422
    )
    r = client.post(
        url, headers=auth, json={"current_password": s.admin_password, "new_password": "new-password-1"}
    )
    assert r.status_code == 204
    assert (
        client.post(
            "/api/auth/login", json={"email": s.admin_email, "password": s.admin_password}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": s.admin_email, "password": "new-password-1"}
        ).status_code
        == 200
    )


def test_report_pdf_export(client, auth, plant, db):
    pid = plant["id"]
    seed_days(db, pid, n=40)
    rid = client.post(f"/api/plants/{pid}/reports/generate?kind=monthly", headers=auth).json()["id"]
    r = client.get(f"/api/plants/{pid}/reports/{rid}/pdf", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 2000
    assert client.get(f"/api/plants/{pid}/reports/99999/pdf", headers=auth).status_code == 404
