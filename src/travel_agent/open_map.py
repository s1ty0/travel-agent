"""Real OpenStreetMap snapshot and local walking estimates. No map network calls."""

import gzip
import heapq
import json
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from .deepseek import ProviderError
from .source_models import Coordinate, NearbyQuery, PlaceHit, PlaceQuery, SourceBatch, WalkingPath
from .sources import fingerprint

SNAPSHOT = datetime(2026, 11, 11, tzinfo=UTC)
FILES = {"杭州": "hangzhou.json.gz", "苏州": "suzhou.json.gz"}
CITY_ALIASES = {
    "杭州市": "杭州",
    "杭州城区": "杭州",
    "杭州西湖": "杭州",
    "杭州西湖东岸": "杭州",
    "苏州市": "苏州",
    "苏州古城": "苏州",
    "苏州古城区": "苏州",
    "苏州姑苏区": "苏州",
}
SIGHTS = {"museum", "gallery", "attraction", "park"}
FOOD = {"restaurant", "cafe"}
TOP = {
    "杭州": (
        "钱王祠",
        "西湖博物馆",
        "河坊街",
        "杭州博物馆",
        "胡雪岩故居",
        "南宋德寿宫遗址博物馆",
        "鼓楼",
        "城隍阁",
    ),
    "苏州": (
        "拙政园",
        "苏州博物馆",
        "狮子林",
        "中国昆曲博物馆",
        "苏州园林博物馆",
        "耦园",
        "环秀山庄",
        "怡园",
    ),
}


def meters(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    return 6371008 * math.hypot((lon1 - lon2) * math.cos((lat1 + lat2) / 2), lat1 - lat2)


@lru_cache(maxsize=2)
def load_map(city):
    if city not in FILES:
        return None
    path = Path(__file__).parent / "osm_data" / FILES[city]
    with gzip.open(path, "rt", encoding="utf-8") as file:
        data = json.load(file)
    data["adj"] = [[] for _ in data["nodes"]]
    for a, b in data["edges"]:
        distance = meters(data["nodes"][a], data["nodes"][b])
        data["adj"][a].append((b, distance))
        data["adj"][b].append((a, distance))
    visited, connected = set(), []
    for start in range(len(data["nodes"])):
        if start in visited:
            continue
        group = [start]
        visited.add(start)
        for node in group:
            for other, _ in data["adj"][node]:
                if other not in visited:
                    visited.add(other)
                    group.append(other)
        if len(group) > len(connected):
            connected = group
    data["connected"] = connected
    return data


def shortest(data, origin, destination):
    nodes = data["nodes"]
    main = data["connected"]
    if not main:
        return None
    a = min(main, key=lambda i: meters(origin, nodes[i]))
    b = min(main, key=lambda i: meters(destination, nodes[i]))
    first, last = meters(origin, nodes[a]), meters(destination, nodes[b])
    if max(first, last) > 140:
        return None
    queue, costs = [(0, a)], {a: 0}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != costs[node]:
            continue
        if node == b:
            return math.ceil(cost + first + last)
        for nxt, length in data["adj"][node]:
            distance = cost + length
            if distance < costs.get(nxt, math.inf):
                costs[nxt] = distance
                heapq.heappush(queue, (distance, nxt))
    return None


def place(record):
    id_, name, _, lon, lat, address, _ = record
    return PlaceHit(
        source_id="osm_" + fingerprint(id_)[:20],
        poi_id=id_,
        name=name,
        address=address,
        adcode=None,
        coordinate=Coordinate(longitude=lon, latitude=lat, system="WGS84"),
    )


class OpenMap:
    def __init__(self, city):
        self.data = load_map(city)
        self.priority = {name: i for i, name in enumerate(TOP.get(city, ()))}

    def __call__(self, query):
        if self.data is None:
            raise ProviderError("open_map_city_unavailable")
        count = getattr(query, "count", 1)
        if isinstance(query, PlaceQuery):
            operation = "places"
            terms = {
                "博物馆": {"museum", "gallery"},
                "公园": {"park"},
                "风景名胜": SIGHTS,
                "园林": {"park", "attraction"},
                "寺庙": {"attraction"},
                "古镇": {"attraction"},
            }
            category = terms.get(query.keywords)
            if query.keywords == "餐厅":
                selected = [p for p in self.data["places"] if p[2] in FOOD]
            elif query.keywords == "素食":
                selected = [p for p in self.data["places"] if p[2] in FOOD and vegetarian(p)]
            elif category:
                selected = [p for p in self.data["places"] if p[2] in category]
                if query.keywords in {"园林", "寺庙", "古镇"}:
                    hints = {"园林": ("园",), "寺庙": ("寺", "庙"), "古镇": ("古镇", "古街")}
                    selected = [
                        p for p in selected if any(k in p[1] for k in hints[query.keywords])
                    ]
            else:
                selected = [p for p in self.data["places"] if query.keywords in p[1]]
            selected = sorted(
                selected,
                key=lambda p: (
                    self.priority.get(p[1], 100),
                    p[2] == "cafe",
                    not any(ord(c) > 127 for c in p[1]),
                    p[1],
                ),
            )
            distinct = {p[1]: p for p in reversed(selected)}
            hits = [place(p) for p in list(distinct.values())[::-1][:count]]
        elif isinstance(query, NearbyQuery):
            operation = "nearby"
            origin = (query.center.longitude, query.center.latitude)
            choices = sorted(
                (meters(origin, p[3:5]), p[2] == "cafe", p)
                for p in self.data["places"]
                if p[2] == "restaurant" and (query.keywords != "素食" or vegetarian(p))
            )
            distinct = {}
            for distance, _, p in choices:
                if distance > query.radius_meters or len(distinct) == count:
                    break
                distinct.setdefault(p[1], p)
            hits = [place(p) for p in distinct.values()]
        else:
            operation = "walking"
            a, b = query.origin, query.destination
            length = (
                None
                if any(x.system != "WGS84" for x in (a, b))
                else shortest(self.data, (a.longitude, a.latitude), (b.longitude, b.latitude))
            )
            hits = (
                [
                    WalkingPath(
                        source_id="osm_route_" + fingerprint(query.model_dump_json())[:20],
                        distance_meters=length,
                        duration_seconds=math.ceil(length / 1.25),
                    )
                ]
                if length is not None
                else []
            )
        return SourceBatch(
            run_id=str(uuid4()),
            provider="openstreetmap",
            operation=operation,
            retrieved_at=SNAPSHOT,
            query_sha256=fingerprint(query.model_dump_json()),
            requested_count=count,
            received_count=len(hits),
            status="ok" if hits else "empty",
            hits=tuple(hits),
            pagination_status="not_supported",
            elapsed_ms=0,
        )


def vegetarian(record):
    return (
        "素食" in record[1] or "vegetarian" in record[-1].lower() or "vegan" in record[-1].lower()
    )
