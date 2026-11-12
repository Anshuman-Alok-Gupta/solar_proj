"""One-off discovery probe for the SEMS account configured in .env.

Writes a REDACTED JSON dump (no account, uid or token values) to the path given as
argv[1] so the real response shapes can be inspected before building the adapter.
Prints only a structural summary to stdout. Never commit the output.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.integrations.sems.client import SemsClient, SemsError


def summarize(obj, depth=0, max_depth=3):
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:40]:
            if isinstance(v, (dict, list)) and depth < max_depth:
                print(f"{pad}{k}: {type(v).__name__}[{len(v)}]")
                summarize(v, depth + 1, max_depth)
            else:
                sv = "***" if k.lower() in {"token", "uid", "account", "pwd"} else repr(v)[:60]
                print(f"{pad}{k}: {sv}")
    elif isinstance(obj, list):
        if obj:
            print(f"{pad}[0]:")
            summarize(obj[0], depth + 1, max_depth)


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    s = get_settings()
    if not s.sems_configured:
        print("SEMS credentials not configured")
        return 1
    dump: dict = {}
    with SemsClient(s.sems_account, s.sems_password) as c:
        for mode in ("new", "web", "legacy"):
            try:
                tok = c.login(mode)
                dump[f"login_{mode}"] = {"ok": True, "api": tok.api, "keys": sorted(tok.data.keys())}
                print(f"login {mode}: OK api={tok.api} keys={sorted(tok.data.keys())}")
            except SemsError as e:
                dump[f"login_{mode}"] = {"ok": False, "error": str(e)}
                print(f"login {mode}: FAILED {e}")

        try:
            stations = c.list_stations()
        except SemsError as e:
            print("list_stations failed:", e)
            stations = []
        dump["stations"] = SemsClient.redact(stations)
        print(f"\n== stations: {len(stations)}")
        for st in stations:
            print(
                {
                    k: st.get(k)
                    for k in (
                        "id",
                        "name",
                        "capacity",
                        "powerstation_type",
                        "status",
                        "type",
                        "createTime",
                        "timeZone",
                        "timezone",
                        "inverterNum",
                        "deviceNum",
                    )
                }
            )
        station_id = s.sems_station_id or (stations[0].get("id") if stations else None)
        if not station_id:
            print("no station id")
            return 1
        print("station id (first 8):", str(station_id)[:8], "...")

        def run(name, fn):
            try:
                res = fn()
                dump[name] = SemsClient.redact(res)
                print(f"\n== {name}: {type(res).__name__}")
                summarize(res)
            except SemsError as e:
                dump[name] = {"error": str(e)}
                print(f"\n== {name}: ERROR {e}")
                return None
            return res

        devices = run("devices", lambda: c.station_devices(station_id)) or []
        run("flow", lambda: c.station_flow(station_id))
        for d in devices:
            sn = d.get("sn")
            dt = d.get("deviceType", "INVERTER")
            if dt == "DONGLE":
                continue
            run(f"telemetry_{dt}_{str(sn)[-4:]}", lambda sn=sn, dt=dt: c.device_telemetry(station_id, sn, dt))
            run(
                f"counters_{dt}_{str(sn)[-4:]}",
                lambda sn=sn, dt=dt: c.device_telecounting(station_id, sn, dt),
            )
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        run(
            "stats_day_last14",
            lambda: c.station_statistics(
                station_id, "day", now - timedelta(days=13), now + timedelta(days=1) - timedelta(seconds=1)
            ),
        )
        run(
            "stats_month_thisyear",
            lambda: c.station_statistics(
                station_id, "month", now.replace(month=1, day=1), now + timedelta(days=1)
            ),
        )
        run(
            "stats_year_all",
            lambda: c.station_statistics(station_id, "year", datetime(2015, 1, 1), now + timedelta(days=1)),
        )
        run(
            "production_today",
            lambda: c.station_production(station_id, now, now + timedelta(days=1) - timedelta(seconds=1)),
        )
        run("monitor_detail", lambda: c.monitor_detail(station_id))
        run("legacy_power_chart_yesterday", lambda: c.legacy_power_chart(station_id, now - timedelta(days=1)))

    if out_path:
        out_path.write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
        print(f"\nredacted dump written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
