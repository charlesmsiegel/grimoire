"""Regenerate the weather regression fixture. Run deliberately, never in CI.

Regenerating after an intentional algorithm change is correct. Regenerating to
make a red test go green destroys the only thing protecting a user's existing
weather from silently moving.
"""

import json
import pathlib

from grimoire.store.weather.draw import draw
from grimoire.store.weather.noise import field, latent_u, latent_z, quantile

SEASON = {
    "name": "winter", "from": 0.0, "to": 0.0,
    "temperature": [{"name": "freezing", "weight": 2}, {"name": "mild", "weight": 8}],
    "conditions": [{"name": "clear", "weight": 5},
                   {"name": "snow", "weight": 5, "requires_temp": ["freezing"]}],
    "wind": [{"name": "calm", "weight": 1}, {"name": "breeze", "weight": 4},
             {"name": "strong", "weight": 3}, {"name": "gale", "weight": 1}],
}
CASES = [("saltmarch-chronicle", "saltmarch", 0, 0.0),
         ("saltmarch-chronicle", "saltmarch", 0, 0.5),
         ("saltmarch-chronicle", "saltmarch", 0, 0.9),
         ("saltmarch-chronicle", "highreach", 137, 0.35),
         ("saltmarch-chronicle", "saltmarch", -42, 0.75),
         # Draws `snow` on `freezing`. Without it every row draws the
         # unconstrained condition, so the fixture would never pin the branch
         # where a `requires_temp` row survives the filter and wins.
         ("saltmarch-chronicle", "saltmarch", 34, 0.6)]

rows = []
for cid, zone, i, p in CASES:
    rows.append({
        "cid": cid, "zone": zone, "ordinal": i, "persistence": p,
        "u": latent_u(cid, zone, "condition", i),
        "z": latent_z(cid, zone, "condition", i),
        "g": field(cid, zone, "condition", i, p),
        "phi": quantile(cid, zone, "condition", i, p),
        "drawn": draw(cid, zone, SEASON, p, i),
    })

out = pathlib.Path(__file__).parent / "weather_vectors.json"
out.write_text(json.dumps({"season": SEASON, "rows": rows}, indent=2), encoding="utf-8")
print(f"wrote {len(rows)} rows to {out}")
