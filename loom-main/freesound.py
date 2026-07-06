"""Fetch a real, CC-licensed sample from Freesound to give the sampler a human timbre.

Needs a free API token: get one at https://freesound.org/apiv2/apply and set FREESOUND_API_KEY.
Search -> first result's ogg preview -> mono float array. Endpoint helper. Previews are CC —
attribute the author (printed). Nothing here touches the pure engine.
"""
from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request

import numpy as np
import soundfile as sf

API = "https://freesound.org/apiv2/search/text/"


def fetch(query: str, *, token: str = ""):
    token = token or os.environ.get("FREESOUND_API_KEY", "")
    if not token:
        raise SystemExit("set FREESOUND_API_KEY (free at https://freesound.org/apiv2/apply)")
    url = API + "?" + urllib.parse.urlencode({
        "query": query, "fields": "id,name,username,previews,license,duration",
        "filter": "duration:[0.3 TO 8]", "page_size": 5, "token": token})
    res = json.load(urllib.request.urlopen(url, timeout=30))
    if not res.get("results"):
        raise SystemExit(f"no Freesound results for {query!r}")
    r = res["results"][0]
    prev = r["previews"].get("preview-hq-ogg") or r["previews"].get("preview-lq-ogg")
    data = urllib.request.urlopen(
        urllib.request.Request(prev, headers={"Authorization": f"Token {token}"}), timeout=60).read()
    audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    print(f"freesound: \"{r['name']}\" by {r['username']} ({r['license']}) — attribute this sample")
    return audio.astype(np.float32), sr
