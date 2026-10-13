"""Local synthetic request profile, never a production or iPhone FPS benchmark.

Run from the repository: python -m tests.browser.profile_pages
The preview lifespan creates and removes its own temporary SQLite database.
"""
import asyncio
import cProfile
import json
import time

import httpx
from sqlalchemy import update

from tests.browser.preview_app import app, factory
from app.models.activity import Activity


async def main():
    async with app.router.lifespan_context(app):
        async with factory() as db:
            await db.execute(update(Activity).values(
                raw_data={"utc_offset": 0, "synthetic_payload": "x" * 200000},
                streams_data={"synthetic": list(range(40000))},
                splits_metric=[{"distance": 1000, "moving_time": 300,
                                "elevation_difference": 3}] * 10,
            ))
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/sante", "/activities", "/simulator", "/simulator/routes/1"):
                timings, calculations = [], []
                for _ in range(4):
                    profile = cProfile.Profile()
                    start = time.perf_counter()
                    with profile:
                        response = await client.get(path)
                    timings.append(round((time.perf_counter() - start) * 1000, 1))
                    assert response.status_code == 200, (path, response.status_code)
                    calculations.append(sum(entry.callcount for entry in profile.getstats()
                        if hasattr(entry.code, "co_name") and entry.code.co_name == "_sleep_baseline"))
                print(json.dumps({"path": path, "ms": timings, "html_bytes": len(response.content),
                                  "baseline_calculations": calculations}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
