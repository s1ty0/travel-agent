# OpenStreetMap snapshot data

`hangzhou.json.gz` and `suzhou.json.gz` contain selected named points and a walking graph derived from **© OpenStreetMap contributors**. The data in these two files is available under the [Open Data Commons Open Database License (ODbL)](https://www.openstreetmap.org/copyright). The compressed derived database is available directly in this public repository. This notice covers the two data files, independently of the application source code.

Retrieved on 2026-11-11 using the official OpenStreetMap API. Bounding boxes are longitude, latitude, longitude, latitude.

| Coverage | API extraction bounds |
| --- | --- |
| East shore and old town of Hangzhou | `120.143,30.236,120.174,30.268` |
| West half of the Suzhou old town | `120.608,31.307,120.627,31.334` |
| East half of the Suzhou old town | `120.627,31.307,120.644,31.334` |

The source endpoint is `https://api.openstreetmap.org/api/0.6/map?bbox=...`. The download was a small one-time acquisition. Production runs read the packaged files and do not query this API. `scripts/build_osm_snapshot.py` compiles local XML extracts, retaining only selected place labels, address fields, coordinates and walkable graph edges. It omits editor names, user IDs and edit metadata. The route estimate uses graph distance plus a short connection to the mapped graph, at 4.5 km/h before the planner's buffer. Coverage gaps produce an unknown route.
