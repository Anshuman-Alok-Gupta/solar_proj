"""API tests: auth, plant access control, analytics empty states, import workflow, reports, AI status."""

from __future__ import annotations

import io

from tests.conftest import seed_days


def test_health_and_capabilities(client):
    assert client.get("/api/health").json()["status"] == "ok"
    caps = client.get("/api/system/capabilities").json()
    assert "sems_configured" in caps and "llm_configured" in caps


def test_login_failure_and_unauthenticated(client):
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert r.status_code == 401
    assert client.get("/api/plants").status_code == 401


def test_plant_crud_and_access_control(client, auth, plant, db):
    pid = plant["id"]
    r = client.patch(
        f"/api/plants/{pid}", headers=auth, json={"capacity_kw": 5.5, "tariff_import_per_kwh": 9}
    )
    assert r.status_code == 200 and r.json()["capacity_kw"] == 5.5
    assert client.get("/api/plants/999", headers=auth).status_code == 404
    # another (non-superuser) user cannot see this plant
    from app.core.security import create_access_token, hash_password
    from app.db.models import User

    other = User(email="other@example.com", hashed_password=hash_password("pw"), is_active=True)
    db.add(other)
    db.commit()
    tok = create_access_token(str(other.id))
    assert client.get(f"/api/plants/{pid}", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert client.get("/api/plants", headers={"Authorization": f"Bearer {tok}"}).json() == []


def test_analytics_empty_states(client, auth, plant):
    pid = plant["id"]
    s = client.get(f"/api/plants/{pid}/analytics/summary", headers=auth).json()
    assert s["has_data"] is False and s["today_kwh"] is None
    assert client.get(f"/api/plants/{pid}/analytics/monthly", headers=auth).json()["items"] == []
    assert client.get(f"/api/plants/{pid}/analytics/distribution", headers=auth).json()["bins"] == []
    sav = client.get(f"/api/plants/{pid}/analytics/savings", headers=auth).json()
    assert sav["is_estimate"] is True
    assert client.get(f"/api/plants/{pid}/forecasts/backtest", headers=auth).status_code == 400  # no model


def test_analytics_with_data_and_synthetic_exclusion(client, auth, plant, db):
    pid = plant["id"]
    seed_days(db, pid, n=40)
    s = client.get(f"/api/plants/{pid}/analytics/summary", headers=auth).json()
    assert s["has_data"] and s["lifetime_kwh"] > 0 and s["best_day"]
    d = client.get(f"/api/plants/{pid}/analytics/daily?start=2000-01-01", headers=auth).json()
    assert d["count"] == 40 and d["items"][0]["gti_wh"] is not None
    corr = client.get(
        f"/api/plants/{pid}/analytics/weather-correlation?start=2000-01-01", headers=auth
    ).json()
    assert corr["correlations"]["gti_wh"] is not None and corr["correlations"]["gti_wh"] > 0.5
    sav = client.get(f"/api/plants/{pid}/analytics/savings", headers=auth).json()
    assert sav["estimated_value"] > 0 and "estimated_value_if_all_exported" in sav
    # synthetic rows are excluded by default
    from datetime import date

    from app.db.models import DataSource
    from app.services.energy_store import upsert_daily_energy

    upsert_daily_energy(
        db, [{"plant_id": pid, "date": date(2020, 1, 1), "generation_kwh": 5, "source": DataSource.SYNTHETIC}]
    )
    db.commit()
    assert (
        client.get(f"/api/plants/{pid}/analytics/daily?start=2019-12-31&end=2020-01-02", headers=auth).json()[
            "count"
        ]
        == 0
    )
    assert (
        client.get(
            f"/api/plants/{pid}/analytics/daily?start=2019-12-31&end=2020-01-02&include_synthetic=true",
            headers=auth,
        ).json()["count"]
        == 1
    )
    cov = client.get(f"/api/plants/{pid}/data-coverage", headers=auth).json()
    assert cov["has_real_data"] is True
    assert client.post(f"/api/plants/{pid}/clear-synthetic", headers=auth).json()["daily_deleted"] == 1


def test_import_workflow_csv(client, auth, plant):
    pid = plant["id"]
    csv = "Date,Energy Generation(kWh)\n2026-10-01,14.2\n2026-10-02,13.9\n2026-10-03,4.1\n"
    r = client.post(
        f"/api/plants/{pid}/imports",
        headers=auth,
        files={"file": ("daily.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "uploaded" and job["rows_total"] == 3
    mapping = job["inspection"]["suggested_mapping"]
    assert "generation_kwh" in mapping["columns"]
    r = client.post(f"/api/plants/{pid}/imports/{job['id']}/commit", headers=auth)
    assert r.status_code == 400  # must preview first
    r = client.post(f"/api/plants/{pid}/imports/{job['id']}/preview", headers=auth, json={"mapping": mapping})
    assert r.status_code == 200 and r.json()["quality_report"]["status"] in ("ok", "warn")
    r = client.post(f"/api/plants/{pid}/imports/{job['id']}/commit", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "committed" and r.json()["rows_inserted"] == 3
    # idempotent re-import: upload again -> updated not duplicated
    r = client.post(
        f"/api/plants/{pid}/imports",
        headers=auth,
        files={"file": ("daily.csv", io.BytesIO(csv.encode()), "text/csv")},
    )
    j2 = r.json()
    client.post(f"/api/plants/{pid}/imports/{j2['id']}/preview", headers=auth, json={"mapping": mapping})
    r = client.post(f"/api/plants/{pid}/imports/{j2['id']}/commit", headers=auth)
    assert r.json()["rows_inserted"] == 0 and r.json()["rows_updated"] == 3
    cov = client.get(f"/api/plants/{pid}/data-coverage", headers=auth).json()
    assert cov["daily"][0]["days"] == 3
    assert client.get(f"/api/plants/{pid}/imports", headers=auth).json()[0]["status"] == "committed"
    # bad file type
    r = client.post(
        f"/api/plants/{pid}/imports",
        headers=auth,
        files={"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert r.status_code == 400


def test_reports(client, auth, plant, db):
    pid = plant["id"]
    seed_days(db, pid, n=40)
    r = client.get(f"/api/plants/{pid}/reports/preview?kind=monthly", headers=auth)
    assert r.status_code == 200 and r.json()["content"]["generation"]["total_kwh"] > 0
    r = client.post(f"/api/plants/{pid}/reports/generate?kind=weekly", headers=auth)
    assert r.status_code == 200
    rid = r.json()["id"]
    csv = client.get(f"/api/plants/{pid}/reports/{rid}/csv", headers=auth)
    assert csv.status_code == 200 and csv.text.startswith("date,generation_kwh")


def test_sems_endpoints_without_credentials(client, auth, plant, monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "sems_account", None)
    monkeypatch.setattr(s, "sems_password", None)
    pid = plant["id"]
    st = client.get(f"/api/plants/{pid}/sems/status", headers=auth).json()
    assert st["configured"] is False
    assert client.post(f"/api/plants/{pid}/sems/sync", headers=auth).status_code == 400


def test_ai_status_and_chat_unconfigured(client, auth, plant):
    pid = plant["id"]
    st = client.get(f"/api/plants/{pid}/ai/status", headers=auth).json()
    assert st["configured"] is False and st["suggested_questions"]
    r = client.post(f"/api/plants/{pid}/ai/chat", headers=auth, json={"message": "hi"})
    assert r.status_code == 503


def test_ai_tools_and_fake_provider(client, auth, plant, db):
    from app.ai.analyst import run_analyst_turn
    from app.ai.providers import BaseProvider, LLMResponse, LLMToolsUnsupported, ToolCall
    from app.ai.tools import TOOL_DEFINITIONS, ToolExecutor
    from app.db.models import Plant, User

    pid = plant["id"]
    seed_days(db, pid, n=30)
    p = db.get(Plant, pid)
    ex = ToolExecutor(db, p)
    for t in TOOL_DEFINITIONS:
        args = (
            {"day": "2026-11-11"}
            if t["name"] == "get_intraday_power"
            else ({"kind": "weekly"} if t["name"] == "generate_performance_report" else {})
        )
        payload, err = ex.run(t["name"], args)
        assert not err, (t["name"], payload)
    assert ex.run("nope", {})[1] is True

    class Native(BaseProvider):
        name = "fake"

        def __init__(self):
            self.n = 0

        def chat(self, *, system, messages, tools, max_tokens):
            self.n += 1
            if self.n == 1:
                return LLMResponse(
                    text="",
                    tool_calls=[ToolCall("1", "get_generation_summary", {})],
                    raw_assistant_message="a",
                )
            return LLMResponse(text="Lifetime is in the data.", usage={"input_tokens": 1, "output_tokens": 1})

        def tool_result_messages(self, assistant_msg, results):
            return [
                {"role": "assistant", "content": "calls"},
                {"role": "tool", "content": results[0]["content"]},
            ]

    class NoTools(BaseProvider):
        name = "fake2"

        def chat(self, *, system, messages, tools, max_tokens):
            if tools:
                raise LLMToolsUnsupported("no tools")
            assert "get_generation_summary" in messages[-1]["content"]
            return LLMResponse(text="context answer")

    user = db.query(User).first()
    out = run_analyst_turn(db, p, user, "How much lifetime?", provider=Native())
    assert (
        out["reply"]
        and out["tool_calls"][0]["tool"] == "get_generation_summary"
        and out["usage"]["tool_mode"] == "native"
    )
    out2 = run_analyst_turn(db, p, user, "Why low yesterday?", provider=NoTools())
    assert out2["usage"]["tool_mode"] == "context" and "get_anomalies" in [
        c["tool"] for c in out2["tool_calls"]
    ]
    convs = client.get(f"/api/plants/{pid}/ai/conversations", headers=auth).json()["items"]
    assert len(convs) == 2
    c = client.get(f"/api/plants/{pid}/ai/conversations/{convs[0]['id']}", headers=auth).json()
    assert len(c["messages"]) == 2
