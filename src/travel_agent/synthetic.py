"""Explicit synthetic providers for offline demos and reproducible evaluation. No network."""

import json
from time import sleep

import httpx

CENTERS = {"南京": (118.8, 32.0), "杭州": (120.15, 30.27), "上海": (121.47, 31.23)}


class SyntheticProviders:
    def __init__(self, *, flags=None, latency=0):
        self.flags, self.latency = flags or {}, latency
        self.model_transport = httpx.MockTransport(self.model)
        self.source_transport = httpx.MockTransport(self.source)

    def model(self, request):
        sleep(self.latency)
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        experience = {
            "visits": [
                {
                    "day": index // 2 + 1,
                    "place_source_id": place["source_id"],
                    "duration_minutes": 90,
                }
                for index, place in enumerate(data["places"][: data["days"] * 2])
            ]
        }
        food = {
            "keyword": "餐厅",
            "recommended_source_ids": [
                place["source_id"] for place in data.get("food_places", [])[:4]
            ],
        }
        schema = payload["messages"][0]["content"]
        content = (
            {"experience": experience, "food": food}
            if '"CombinedResearch"' in schema
            else experience
            if '"ExperienceResearch"' in schema
            else food
        )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            },
        )

    def source(self, request):
        sleep(self.latency)
        path, params = request.url.path, request.url.params
        if path.endswith("/tools/search"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com/travel-demo",
                            "title": "合成旅行资料",
                            "snippet": "仅用于离线演示，不代表真实营业、价格或旅行事实。",
                        }
                    ]
                },
            )
        if path.endswith("/direction/walking"):
            paths = (
                [] if self.flags.get("empty_routes") else [{"distance": "500", "duration": "420"}]
            )
            return httpx.Response(200, json={"status": "1", "route": {"paths": paths}})
        city = params.get("city", "南京")
        if path.endswith("/place/around"):
            lon, lat = map(float, params["location"].split(","))
            city = min(
                CENTERS, key=lambda name: abs(CENTERS[name][0] - lon) + abs(CENTERS[name][1] - lat)
            )
        lon, lat = CENTERS.get(city, CENTERS["南京"])
        food = path.endswith("/place/around") or params.get("keywords") in {
            "餐厅",
            "本地菜",
            "小吃",
            "面馆",
            "素食",
        }
        kind = "餐厅" if food else "景点"
        rows = [
            {
                "id": f"demo-{city}-{kind}-{index}",
                "name": f"演示{kind}{index + 1}",
                "address": "合成地址 · 仅用于演示",
                **(
                    {}
                    if self.flags.get("missing_coordinates")
                    else {
                        "location": f"{lon + index * 0.001 + (0.0002 if food else 0):.6f},{lat:.6f}"
                    }
                ),
            }
            for index in range(6)
        ]
        keywords = params.get("keywords", "")
        if "不存在" in keywords:
            rows = []
        elif keywords.startswith("演示"):
            rows = [row for row in rows if keywords in row["name"]]
        return httpx.Response(
            200,
            json={
                "status": "1",
                "count": str(len(rows)),
                "pois": rows[: int(params.get("offset", "5"))],
            },
        )
