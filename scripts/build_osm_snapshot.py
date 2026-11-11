"""Build an attributed, compact public walking graph from local OSM XML extracts."""

import argparse
import gzip
import json
from pathlib import Path
from xml.etree import ElementTree

WALKABLE = {
    "footway",
    "path",
    "pedestrian",
    "residential",
    "living_street",
    "service",
    "steps",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "track",
    "cycleway",
}


def build(files, output):
    nodes, ways, records = {}, {}, {}
    for path in files:
        for item in ElementTree.parse(path).getroot():
            if item.tag not in {"node", "way"}:
                continue
            id_ = int(item.attrib["id"])
            tags = {child.attrib["k"]: child.attrib["v"] for child in item.findall("tag")}
            if item.tag == "node":
                nodes[id_] = [float(item.attrib[k]) for k in ("lon", "lat")]
            else:
                ways[id_] = ([int(child.attrib["ref"]) for child in item.findall("nd")], tags)
            label = tags.get("name:zh") or tags.get("name")
            category = (
                tags["tourism"]
                if tags.get("tourism") in {"attraction", "museum", "gallery"}
                else "park"
                if tags.get("leisure") == "park"
                else tags["amenity"]
                if tags.get("amenity") in {"restaurant", "cafe"}
                else None
            )
            if label and category and tags.get("access") not in {"private", "no"}:
                records[(item.tag, id_)] = (label, category, tags)
    graph, links = {}, set()
    for refs, tags in ways.values():
        if (
            tags.get("highway") not in WALKABLE
            or tags.get("foot") in {"no", "private"}
            or (tags.get("access") in {"no", "private"} and tags.get("foot") != "yes")
        ):
            continue
        for a, b in zip(refs, refs[1:], strict=False):
            if a in nodes and b in nodes and a != b:
                for node in (a, b):
                    graph.setdefault(node, len(graph))
                links.add(tuple(sorted((graph[a], graph[b]))))
    places = []
    for (kind, id_), (name, category, tags) in sorted(records.items()):
        points = [nodes[id_]] if kind == "node" else [nodes[n] for n in ways[id_][0] if n in nodes]
        if not points:
            continue
        lon, lat = (round(sum(values) / len(points), 6) for values in zip(*points, strict=True))
        address = "".join(tags.get(k, "") for k in ("addr:street", "addr:housenumber")) or None
        places.append([f"{kind}/{id_}", name, category, lon, lat, address, tags.get("cuisine", "")])
    raw = json.dumps(
        {"places": places, "nodes": [nodes[n] for n in graph], "edges": sorted(links)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(raw, mtime=0))
    print(f"{output.name}: {len(places)} places, {len(graph)} walking nodes, {len(links)} edges")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    build(args.inputs, args.output)
