"""
Neikos: Hundred Islands - Deterministic Island Creature-Ecology Simulation

A Radio OS plugin game.  The player is born on one of 100 sealed islands,
each generated from a deterministic seed.  300 species, competitive leagues,
genetic breeding, faction territorial influence, and a hidden world ledger
drive toward one of 100 outcome bands computed from the fusion of
Island State × Personal Trajectory.

Architecture (all in one file, pure math - LLM is presentation-only):
  §1  - Determinism infrastructure (SeededRNG, hashing)
  §2  - Global Type library & interaction matrix
  §3  - Biome vector model & climate archetypes
  §4  - Island macro-graph topology generation
  §5  - Species generation (300 per island)
  §6  - Encounter table system
  §7  - Battle & league simulation
  §8  - Genetic breeding & evolutionary drift
  §9  - Faction territorial influence & dialogue weighting
  §10 - Island ledger, normalization, 100 outcome bands
  §11 - Gate requirement computation
  §12 - Player trajectory & personal outcome
  §13 - Island controller & tick engine
  §14 - Widget registration (Radio OS plugin contract)

Design Principles:
  • Deterministic given (seed, player choices).
  • No infinite procedural sprawl - everything derives from seed.
  • LLM is presentation-only; the Cold Layer is pure math.
  • 100 islands × 300 species × 120-180 nodes each.
  • Simulation is pure computation - no blocking I/O on main thread.
"""

from __future__ import annotations

# ── stdlib ──────────────────────────────────────────────────
import hashlib
import json
import math
import os
import queue
import random
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, FrozenSet, List, Optional,
    Set, Tuple, Union,
)

# ── Debug gate ──────────────────────────────────────────────
NK_DEBUG: bool = os.environ.get("NK_DEBUG", "").strip() in ("1", "true", "yes")


def _dbg(*a, **kw):
    if NK_DEBUG:
        print("[NK]", *a, **kw)


# ============================================================
# PLUGIN METADATA
# ============================================================

IS_FEED = False
PLUGIN_NAME = "Neikos: Hundred Islands"
PLUGIN_DESC = (
    "Deterministic 100-island creature-ecology simulation - "
    "300 species, league battling, genetic breeding, faction influence, "
    "100 outcome bands"
)
FEED_DEFAULTS: Dict[str, Any] = {}  # widget-only, no feed config

# ── Island preview cache (§14 fills this lazily at startup) ──────────────────
# Populated by _build_island_cache() in a background thread so /api/islands
# can respond instantly after warm-up instead of blocking for 3-5 s.
_NK_ISLAND_CACHE: Dict[int, dict] = {}
_NK_ISLAND_CACHE_LOCK = threading.Lock()
_NK_ISLAND_CACHE_BUILT = threading.Event()   # set when all 100 entries are ready


def _build_island_cache() -> None:
    """Background worker: generate all 100 island previews and populate the cache."""
    # _seed_to_base_tier and generate_island_topology are defined later in the
    # module.  By the time this thread actually runs (started from
    # register_widgets), both functions are fully defined.
    for s in range(1, 101):
        try:
            topo = generate_island_topology(s)
            tier = _seed_to_base_tier(s)
            entry = {
                "seed": s,
                "name": topo.island_name,
                "climate": topo.climate.name if topo.climate else "TEMPERATE",
                "node_count": topo.node_count,
                "base_tier": tier.name,
                "active_types": [t.name for t in (topo.active_types or [])[:4]],
            }
        except Exception as exc:  # noqa: BLE001
            entry = {
                "seed": s,
                "name": f"Island {s}",
                "climate": "UNKNOWN",
                "node_count": 0,
                "base_tier": "TIER_I",
                "active_types": [],
                "error": str(exc),
            }
        with _NK_ISLAND_CACHE_LOCK:
            _NK_ISLAND_CACHE[s] = entry
    _NK_ISLAND_CACHE_BUILT.set()
    _dbg("Island cache fully built (100 seeds)")


# ============================================================
# §1  DETERMINISM INFRASTRUCTURE
# ============================================================

class SeededRNG:
    """
    Reproducible PRNG wrapper.  Every sub-system forks its own RNG from
    the master seed so evaluation order between independent systems cannot
    break determinism.
    """

    def __init__(self, seed: int):
        self._seed = seed
        self._rng = random.Random(seed)

    # ---- delegation ------------------------------------------------
    def random(self) -> float:
        return self._rng.random()

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def uniform(self, a: float, b: float) -> float:
        return self._rng.uniform(a, b)

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def choice(self, seq):
        return self._rng.choice(seq)

    def choices(self, population, weights=None, k=1):
        return self._rng.choices(population, weights=weights, k=k)

    def shuffle(self, seq):
        self._rng.shuffle(seq)

    def sample(self, population, k: int):
        return self._rng.sample(population, k)

    # ---- forking ---------------------------------------------------
    def fork(self, label: str) -> "SeededRNG":
        h = hashlib.sha256(f"{self._seed}:{label}".encode()).hexdigest()
        return SeededRNG(int(h[:16], 16))

    @property
    def seed(self) -> int:
        return self._seed


def _det_hash(text: str) -> int:
    """Deterministic 64-bit hash from a string."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)


# ============================================================
# §2  GLOBAL TYPE LIBRARY & INTERACTION MATRIX
# ============================================================

class NkType(Enum):
    """18 global mechanical creature types."""
    EMBER    = 0
    TIDE     = 1
    STONE    = 2
    GALE     = 3
    VERDANT  = 4
    FROST    = 5
    VOLT     = 6
    VENOM    = 7
    ALLOY    = 8
    SHADE    = 9
    RADIANT  = 10
    ECHO     = 11
    RIFT     = 12
    THORN    = 13
    BLOOM    = 14
    DUNE     = 15
    TORRENT  = 16
    PULSE    = 17


# Number of types
_NUM_TYPES = len(NkType)

# Canonical type interaction matrix  (18 × 18)
# Values: 1.25 = advantage, 1.0 = neutral, 0.75 = resistance
# Built with deterministic, balanced adjacency rules:
#   • Every type has ≥2 advantages and ≥2 weaknesses
#   • No type dominates >4 others or is weak to >4

def _build_type_matrix() -> List[List[float]]:
    """
    Build a balanced 18×18 interaction matrix.

    Strategy: ring-of-advantages with cross-links for depth.
    Each type i is strong against types at offsets +1, +5 (mod 18)
    and weak to types at offsets -1, -5 (mod 18).
    Additional cross-links at offsets +8 for a 3rd advantage / weakness.
    """
    n = _NUM_TYPES
    mat = [[1.0] * n for _ in range(n)]

    adv_offsets = [1, 5, 8]
    for i in range(n):
        for off in adv_offsets:
            j = (i + off) % n
            mat[i][j] = 1.25   # i has advantage over j
            mat[j][i] = 0.75   # j resists i → j is weak to i

    # Validate constraints
    for i in range(n):
        advs = sum(1 for j in range(n) if mat[i][j] == 1.25)
        weaks = sum(1 for j in range(n) if mat[i][j] == 0.75)
        assert 2 <= advs <= 4, f"Type {i} has {advs} advantages"
        assert 2 <= weaks <= 4, f"Type {i} has {weaks} weaknesses"

    return mat


TYPE_MATRIX: List[List[float]] = _build_type_matrix()


def type_multiplier(attacker: NkType, defender: NkType) -> float:
    """Look up the type effectiveness multiplier."""
    return TYPE_MATRIX[attacker.value][defender.value]


# ============================================================
# §3  BIOME VECTOR MODEL & CLIMATE ARCHETYPES
# ============================================================

class ClimateArchetype(Enum):
    TEMPERATE_MARITIME   = auto()
    SUBTROPICAL_LUSH     = auto()
    ARID_PLATEAU         = auto()
    BOREAL_COLD          = auto()
    VOLCANIC_ACTIVE      = auto()
    STORM_WRACKED        = auto()
    MISTBOUND_HIGHLAND   = auto()


@dataclass
class BiomeVector:
    """
    5-axis biome descriptor.  All values in [0.0, 1.0].
    """
    temperature:       float = 0.5
    moisture:          float = 0.5
    elevation:         float = 0.3
    vegetation_density: float = 0.5
    instability_bias:  float = 0.1

    def distance(self, other: "BiomeVector") -> float:
        return math.sqrt(
            (self.temperature - other.temperature) ** 2
            + (self.moisture - other.moisture) ** 2
            + (self.elevation - other.elevation) ** 2
            + (self.vegetation_density - other.vegetation_density) ** 2
            + (self.instability_bias - other.instability_bias) ** 2
        )

    def blend(self, other: "BiomeVector", w: float) -> "BiomeVector":
        """Weighted blend toward *other* (w=0 → self, w=1 → other)."""
        inv = 1.0 - w
        return BiomeVector(
            temperature=self.temperature * inv + other.temperature * w,
            moisture=self.moisture * inv + other.moisture * w,
            elevation=self.elevation * inv + other.elevation * w,
            vegetation_density=self.vegetation_density * inv + other.vegetation_density * w,
            instability_bias=self.instability_bias * inv + other.instability_bias * w,
        )

    def perturb(self, rng: SeededRNG, sigma: float = 0.05) -> "BiomeVector":
        def _clamp(v):
            return max(0.0, min(1.0, v))
        return BiomeVector(
            temperature=_clamp(self.temperature + rng.gauss(0, sigma)),
            moisture=_clamp(self.moisture + rng.gauss(0, sigma)),
            elevation=_clamp(self.elevation + rng.gauss(0, sigma)),
            vegetation_density=_clamp(self.vegetation_density + rng.gauss(0, sigma)),
            instability_bias=_clamp(self.instability_bias + rng.gauss(0, sigma * 0.5)),
        )

    def to_tuple(self) -> Tuple[float, ...]:
        return (
            self.temperature, self.moisture, self.elevation,
            self.vegetation_density, self.instability_bias,
        )


# Climate archetype → base biome vector
CLIMATE_BASES: Dict[ClimateArchetype, BiomeVector] = {
    ClimateArchetype.TEMPERATE_MARITIME:  BiomeVector(0.50, 0.60, 0.30, 0.55, 0.08),
    ClimateArchetype.SUBTROPICAL_LUSH:    BiomeVector(0.70, 0.75, 0.25, 0.80, 0.10),
    ClimateArchetype.ARID_PLATEAU:        BiomeVector(0.65, 0.15, 0.55, 0.15, 0.12),
    ClimateArchetype.BOREAL_COLD:         BiomeVector(0.20, 0.40, 0.45, 0.35, 0.07),
    ClimateArchetype.VOLCANIC_ACTIVE:     BiomeVector(0.75, 0.30, 0.60, 0.20, 0.30),
    ClimateArchetype.STORM_WRACKED:       BiomeVector(0.45, 0.80, 0.20, 0.40, 0.25),
    ClimateArchetype.MISTBOUND_HIGHLAND:  BiomeVector(0.35, 0.65, 0.70, 0.50, 0.15),
}

# Climate archetype → NkType affinity weights (higher = more likely selected)
CLIMATE_TYPE_AFFINITY: Dict[ClimateArchetype, Dict[NkType, float]] = {
    ClimateArchetype.TEMPERATE_MARITIME:  {NkType.TIDE: 1.5, NkType.VERDANT: 1.3, NkType.GALE: 1.2},
    ClimateArchetype.SUBTROPICAL_LUSH:    {NkType.BLOOM: 1.5, NkType.VERDANT: 1.4, NkType.VENOM: 1.3},
    ClimateArchetype.ARID_PLATEAU:        {NkType.DUNE: 1.5, NkType.STONE: 1.4, NkType.EMBER: 1.3},
    ClimateArchetype.BOREAL_COLD:         {NkType.FROST: 1.6, NkType.STONE: 1.3, NkType.SHADE: 1.2},
    ClimateArchetype.VOLCANIC_ACTIVE:     {NkType.EMBER: 1.6, NkType.STONE: 1.4, NkType.RIFT: 1.3},
    ClimateArchetype.STORM_WRACKED:       {NkType.VOLT: 1.5, NkType.TORRENT: 1.4, NkType.GALE: 1.3},
    ClimateArchetype.MISTBOUND_HIGHLAND:  {NkType.ECHO: 1.5, NkType.SHADE: 1.3, NkType.FROST: 1.2},
}


# ============================================================
# §4  ISLAND MACRO-GRAPH TOPOLOGY GENERATION
# ============================================================

class MacroRegion(Enum):
    """The 7 structural limbs of every island."""
    CENTRAL_BASIN    = auto()
    NORTH_RANGE      = auto()
    SOUTH_EXPANSE    = auto()
    WEST_WILD_BELT   = auto()
    EAST_COASTAL     = auto()
    INTERIOR_DEPTH   = auto()
    SUB_ISLET        = auto()


class NodeType(Enum):
    SETTLEMENT  = auto()
    CITY        = auto()
    PATH        = auto()
    WILD_ZONE   = auto()
    FACILITY    = auto()
    DUNGEON     = auto()
    GATE        = auto()
    LANDMARK    = auto()
    ANOMALY_ZONE = auto()   # §19 - nodes where the system seams are most visible


class GateType(Enum):
    LEAGUE      = auto()
    REPUTATION  = auto()
    RESEARCH    = auto()
    ECOLOGICAL  = auto()
    ANOMALY     = auto()
    ECONOMIC    = auto()


@dataclass
class GateRequirement:
    """A gate condition on an edge."""
    gate_type: GateType = GateType.LEAGUE
    primary_metric: str = "trainer_rating"
    threshold: float = 0.0
    secondary_modifier: str = ""
    flex_buffer: float = 0.0
    alternate_paths: List[Dict[str, Any]] = field(default_factory=list)

    def check(self, player_state: Dict[str, float]) -> bool:
        """Return True if player satisfies this gate."""
        val = player_state.get(self.primary_metric, 0.0)
        effective_threshold = self.threshold - self.flex_buffer
        if val >= effective_threshold:
            return True
        # Check alternates
        for alt in self.alternate_paths:
            metric = alt.get("metric", "")
            thresh = alt.get("threshold", 0.0)
            if player_state.get(metric, 0.0) >= thresh:
                return True
        return False


@dataclass
class MapNode:
    """A single node in the island topology graph."""
    node_id: str = ""
    node_type: NodeType = NodeType.PATH
    region: MacroRegion = MacroRegion.CENTRAL_BASIN
    name: str = ""
    biome: BiomeVector = field(default_factory=BiomeVector)
    neighbors: List[str] = field(default_factory=list)
    gate: Optional[GateRequirement] = None
    settlement_pop: int = 0
    is_start: bool = False
    is_depth_entrance: bool = False
    is_relay_node: bool = False      # §19 - Cartographer infrastructure node
    faction_influence: Dict[str, float] = field(default_factory=dict)
    # Encounter table slots (filled by §6)
    encounter_slots: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.name,
            "region": self.region.name,
            "name": self.name,
            "biome": self.biome.to_tuple(),
            "neighbors": list(self.neighbors),
            "gate": self.gate.gate_type.name if self.gate else None,
            "gate_detail": {
                "type": self.gate.gate_type.name,
                "metric": self.gate.primary_metric,
                "threshold": round(self.gate.threshold, 1),
                "flex_buffer": round(self.gate.flex_buffer, 1),
                "effective_threshold": round(self.gate.threshold - self.gate.flex_buffer, 1),
                "secondary_modifier": self.gate.secondary_modifier,
            } if self.gate else None,
            "is_start": self.is_start,
            "is_relay_node": self.is_relay_node,
        }


# ── Name generation ─────────────────────────────────────────

_SYLLABLES_A = [
    "ka", "ri", "mo", "ta", "su", "ne", "lo", "vi", "an", "du",
    "fe", "go", "hi", "ju", "le", "mi", "no", "pa", "re", "si",
    "to", "um", "va", "we", "xi", "yo", "za", "be", "ci", "da",
    "el", "fu", "gi", "ho", "in", "ja", "ke", "li", "mu", "na",
]
_SYLLABLES_B = [
    "ra", "shi", "wen", "tol", "mar", "ven", "kis", "dor", "pal",
    "thi", "arn", "bel", "cor", "den", "eld", "fen", "gal", "hel",
    "ion", "jor", "kel", "lan", "men", "nor", "osh", "pen", "ryn",
    "sol", "tor", "uth", "val", "wyr", "xen", "yol", "zan", "bir",
]


def _generate_name(rng: SeededRNG, prefix: str = "", min_syl: int = 2,
                   max_syl: int = 4) -> str:
    """Deterministic name from syllable tables."""
    n_syl = rng.randint(min_syl, max_syl)
    parts: List[str] = []
    for i in range(n_syl):
        table = _SYLLABLES_A if i % 2 == 0 else _SYLLABLES_B
        parts.append(rng.choice(table))
    name = "".join(parts).capitalize()
    if prefix:
        name = f"{prefix} {name}"
    return name


def generate_island_name(seed: int) -> str:
    """Canonical island name from seed."""
    rng = SeededRNG(seed).fork("island_name")
    return _generate_name(rng, min_syl=2, max_syl=3)


# ── Region biome base vectors ──────────────────────────────

_REGION_BIOME_OFFSETS: Dict[MacroRegion, BiomeVector] = {
    MacroRegion.CENTRAL_BASIN:   BiomeVector(0.0,  0.0,  -0.10, 0.05,  0.0),
    MacroRegion.NORTH_RANGE:     BiomeVector(-0.15, -0.05, 0.25,  -0.05, 0.02),
    MacroRegion.SOUTH_EXPANSE:   BiomeVector(0.10, -0.15, -0.10, -0.10, 0.03),
    MacroRegion.WEST_WILD_BELT:  BiomeVector(0.0,  0.10,  0.0,   0.20,  0.02),
    MacroRegion.EAST_COASTAL:    BiomeVector(0.0,  0.15,  -0.15, 0.0,   0.05),
    MacroRegion.INTERIOR_DEPTH:  BiomeVector(0.05, -0.10, 0.10,  -0.15, 0.20),
    MacroRegion.SUB_ISLET:       BiomeVector(0.0,  0.05,  -0.05, 0.0,   0.08),
}


def _region_biome(climate: ClimateArchetype, region: MacroRegion,
                  rng: SeededRNG) -> BiomeVector:
    """Compute the biome vector for a region on this island."""
    base = CLIMATE_BASES[climate]
    off = _REGION_BIOME_OFFSETS[region]
    def _c(v): return max(0.0, min(1.0, v))
    bv = BiomeVector(
        temperature=_c(base.temperature + off.temperature),
        moisture=_c(base.moisture + off.moisture),
        elevation=_c(base.elevation + off.elevation),
        vegetation_density=_c(base.vegetation_density + off.vegetation_density),
        instability_bias=_c(base.instability_bias + off.instability_bias),
    )
    return bv.perturb(rng, sigma=0.03)


# ── Topology builder ────────────────────────────────────────

@dataclass
class IslandTopology:
    """Complete island graph - nodes + edges + metadata."""
    seed: int = 0
    island_name: str = ""
    climate: ClimateArchetype = ClimateArchetype.TEMPERATE_MARITIME
    nodes: Dict[str, MapNode] = field(default_factory=dict)
    start_node_id: str = ""
    depth_entrance_ids: List[str] = field(default_factory=list)
    sub_islet_ids: List[List[str]] = field(default_factory=list)
    active_types: List[NkType] = field(default_factory=list)
    relay_node_ids: List[str] = field(default_factory=list)       # §19 relay nodes
    anomaly_zone_ids: List[str] = field(default_factory=list)     # §19 anomaly zones

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    def neighbors_of(self, node_id: str) -> List[MapNode]:
        node = self.nodes.get(node_id)
        if not node:
            return []
        return [self.nodes[nid] for nid in node.neighbors if nid in self.nodes]

    def graph_distance(self, a: str, b: str) -> int:
        """BFS shortest-path distance between two nodes (-1 if unreachable)."""
        if a == b:
            return 0
        visited: Set[str] = {a}
        frontier = [a]
        dist = 0
        while frontier:
            dist += 1
            next_frontier: List[str] = []
            for nid in frontier:
                node = self.nodes.get(nid)
                if not node:
                    continue
                for nb in node.neighbors:
                    if nb == b:
                        return dist
                    if nb not in visited:
                        visited.add(nb)
                        next_frontier.append(nb)
            frontier = next_frontier
        return -1


def _add_edge(nodes: Dict[str, MapNode], a: str, b: str):
    """Add undirected edge between two node ids."""
    if b not in nodes[a].neighbors:
        nodes[a].neighbors.append(b)
    if a not in nodes[b].neighbors:
        nodes[b].neighbors.append(a)


def generate_island_topology(seed: int) -> IslandTopology:
    """
    Build the full island macro-graph for a given seed.

    Target: 120-180 nodes with structural grammar.
    """
    rng = SeededRNG(seed).fork("topology")
    name = generate_island_name(seed)

    # Pick climate
    climates = list(ClimateArchetype)
    climate = climates[seed % len(climates)]

    # Pick active types (12-15 from 18)
    type_rng = SeededRNG(seed).fork("active_types")
    n_active = type_rng.randint(12, 15)
    all_types = list(NkType)
    type_rng.shuffle(all_types)
    # Bias toward climate-affine types
    affinity = CLIMATE_TYPE_AFFINITY.get(climate, {})
    def _type_weight(t: NkType) -> float:
        return affinity.get(t, 1.0)
    all_types.sort(key=lambda t: -_type_weight(t))
    active_types = all_types[:n_active]

    nodes: Dict[str, MapNode] = {}
    nid_counter = [0]

    def _nid(prefix: str = "n") -> str:
        nid_counter[0] += 1
        return f"{prefix}_{nid_counter[0]:04d}"

    # ── Helper: build a spine of nodes for a region ────────
    def _build_spine(region: MacroRegion, length: int,
                     start_connect: Optional[str] = None) -> List[str]:
        spine_ids: List[str] = []
        bio_rng = rng.fork(f"biome_{region.name}")
        region_biome = _region_biome(climate, region, bio_rng)
        for i in range(length):
            nid = _nid("sp")
            # Periodically inject settlements / wild zones
            if i > 0 and i % rng.randint(3, 5) == 0:
                nt = NodeType.SETTLEMENT
            elif i > 0 and i % rng.randint(4, 7) == 0:
                nt = NodeType.WILD_ZONE
            else:
                nt = NodeType.PATH
            n = MapNode(
                node_id=nid,
                node_type=nt,
                region=region,
                name=_generate_name(bio_rng, prefix=region.name.replace("_", " ").title()),
                biome=region_biome.perturb(bio_rng, 0.04),
            )
            nodes[nid] = n
            if spine_ids:
                _add_edge(nodes, spine_ids[-1], nid)
            spine_ids.append(nid)
        if start_connect and spine_ids:
            _add_edge(nodes, start_connect, spine_ids[0])
        return spine_ids

    # ── Helper: attach branches off spine nodes ────────────
    def _attach_branches(spine_ids: List[str], region: MacroRegion,
                         p_branch: float = 0.3, max_branch_len: int = 6):
        bio_rng = rng.fork(f"branch_{region.name}")
        region_biome = _region_biome(climate, region, bio_rng)
        for sid in spine_ids:
            if bio_rng.random() < p_branch:
                blen = bio_rng.randint(2, max_branch_len)
                prev = sid
                for _ in range(blen):
                    nid = _nid("br")
                    nt_roll = bio_rng.random()
                    if nt_roll < 0.2:
                        nt = NodeType.SETTLEMENT
                    elif nt_roll < 0.4:
                        nt = NodeType.WILD_ZONE
                    elif nt_roll < 0.5:
                        nt = NodeType.LANDMARK
                    else:
                        nt = NodeType.PATH
                    n = MapNode(
                        node_id=nid, node_type=nt, region=region,
                        name=_generate_name(bio_rng),
                        biome=region_biome.perturb(bio_rng, 0.05),
                    )
                    nodes[nid] = n
                    _add_edge(nodes, prev, nid)
                    prev = nid
                # Terminal node might be dungeon or landmark
                terminal = nodes[prev]
                roll = bio_rng.random()
                if roll < 0.3:
                    terminal.node_type = NodeType.DUNGEON
                elif roll < 0.5:
                    terminal.node_type = NodeType.LANDMARK

    # ── 1. Start settlement ────────────────────────────────
    s0_id = _nid("s0")
    s0_biome = _region_biome(climate, MacroRegion.CENTRAL_BASIN, rng.fork("s0bio"))
    nodes[s0_id] = MapNode(
        node_id=s0_id, node_type=NodeType.SETTLEMENT,
        region=MacroRegion.CENTRAL_BASIN,
        name=_generate_name(rng.fork("s0name"), prefix="Haven"),
        biome=s0_biome, is_start=True,
    )

    # ── 2. Central Basin spine ─────────────────────────────
    cb_len = rng.randint(8, 13)
    cb_spine = _build_spine(MacroRegion.CENTRAL_BASIN, cb_len, start_connect=s0_id)

    # Place city C1 midway along central basin
    c1_idx = len(cb_spine) // 2
    if c1_idx < len(cb_spine):
        c1_node = nodes[cb_spine[c1_idx]]
        c1_node.node_type = NodeType.CITY
        c1_node.name = _generate_name(rng.fork("c1"), prefix="City")

    _attach_branches(cb_spine, MacroRegion.CENTRAL_BASIN,
                     p_branch=rng.uniform(0.2, 0.4))

    # ── 3. Regional limb spines ────────────────────────────
    limb_regions = [
        MacroRegion.NORTH_RANGE,
        MacroRegion.SOUTH_EXPANSE,
        MacroRegion.WEST_WILD_BELT,
        MacroRegion.EAST_COASTAL,
    ]
    limb_spines: Dict[MacroRegion, List[str]] = {}

    for lr in limb_regions:
        l_len = rng.randint(6, 14)
        # Connect to a random node on central basin spine
        connect_idx = rng.randint(0, max(0, len(cb_spine) - 1))
        connect_to = cb_spine[connect_idx]
        spine = _build_spine(lr, l_len, start_connect=connect_to)
        limb_spines[lr] = spine
        _attach_branches(spine, lr, p_branch=rng.uniform(0.20, 0.35),
                         max_branch_len=rng.randint(2, 5))

    # Place city C2 at end of east coastal
    if limb_spines.get(MacroRegion.EAST_COASTAL):
        ec_spine = limb_spines[MacroRegion.EAST_COASTAL]
        c2_node = nodes[ec_spine[-1]]
        c2_node.node_type = NodeType.CITY
        c2_node.name = _generate_name(rng.fork("c2"), prefix="Port")

    # Place city C3 somewhere on north range (seed-dependent)
    if rng.random() < 0.7 and limb_spines.get(MacroRegion.NORTH_RANGE):
        nr_spine = limb_spines[MacroRegion.NORTH_RANGE]
        c3_idx = rng.randint(len(nr_spine) // 3, len(nr_spine) - 1)
        c3_node = nodes[nr_spine[c3_idx]]
        c3_node.node_type = NodeType.CITY
        c3_node.name = _generate_name(rng.fork("c3"), prefix="Summit")

    # ── 4. Loops within and between limbs ──────────────────
    loop_rng = rng.fork("loops")
    all_spines = [cb_spine] + list(limb_spines.values())
    for spine in all_spines:
        # Intra-limb short loop
        if len(spine) >= 6:
            a_idx = loop_rng.randint(0, len(spine) // 2)
            b_idx = loop_rng.randint(len(spine) // 2 + 1, len(spine) - 1)
            _add_edge(nodes, spine[a_idx], spine[b_idx])
    # Cross-limb loops
    for _ in range(rng.randint(1, 3)):
        sa = loop_rng.choice(all_spines)
        sb = loop_rng.choice(all_spines)
        if sa is not sb and sa and sb:
            _add_edge(nodes, loop_rng.choice(sa), loop_rng.choice(sb))

    # ── 5. Wild core clusters ──────────────────────────────
    n_clusters = rng.randint(3, 6)
    wc_rng = rng.fork("wild_cores")
    wild_region = MacroRegion.WEST_WILD_BELT
    wild_biome = _region_biome(climate, wild_region, wc_rng)
    for ci in range(n_clusters):
        cluster_size = wc_rng.randint(3, 7)
        cluster_ids: List[str] = []
        # Entrance connects to a random existing node
        existing_ids = list(nodes.keys())
        anchor = wc_rng.choice(existing_ids)
        for wi in range(cluster_size):
            nid = _nid("wc")
            n = MapNode(
                node_id=nid, node_type=NodeType.WILD_ZONE,
                region=wild_region,
                name=_generate_name(wc_rng, prefix="Wild"),
                biome=wild_biome.perturb(wc_rng, 0.06),
            )
            nodes[nid] = n
            if cluster_ids:
                _add_edge(nodes, cluster_ids[-1], nid)
                # Micro loop inside cluster
                if len(cluster_ids) >= 3 and wc_rng.random() < 0.4:
                    _add_edge(nodes, cluster_ids[0], nid)
            cluster_ids.append(nid)
        # Connect entrance to anchor
        if cluster_ids:
            _add_edge(nodes, anchor, cluster_ids[0])

    # ── 6. Interior Depth Zone ─────────────────────────────
    depth_size = rng.randint(6, 12)
    depth_rng = rng.fork("depth")
    depth_biome = _region_biome(climate, MacroRegion.INTERIOR_DEPTH, depth_rng)
    # Invert biome relative to island baseline
    ib = CLIMATE_BASES[climate]
    depth_biome.temperature = max(0, min(1, 1.0 - ib.temperature + depth_rng.gauss(0, 0.05)))
    depth_biome.instability_bias = min(1.0, depth_biome.instability_bias + 0.25)

    depth_ids: List[str] = []
    for di in range(depth_size):
        nid = _nid("dp")
        nt = NodeType.DUNGEON if di > depth_size // 2 else NodeType.PATH
        n = MapNode(
            node_id=nid, node_type=nt,
            region=MacroRegion.INTERIOR_DEPTH,
            name=_generate_name(depth_rng, prefix="Depth"),
            biome=depth_biome.perturb(depth_rng, 0.04),
        )
        nodes[nid] = n
        if depth_ids:
            _add_edge(nodes, depth_ids[-1], nid)
        depth_ids.append(nid)
    # Loop inside depth
    if len(depth_ids) >= 4:
        _add_edge(nodes, depth_ids[0], depth_ids[-1])

    # Depth entrance gate
    depth_entrance_id = depth_ids[0] if depth_ids else ""
    if depth_entrance_id:
        nodes[depth_entrance_id].is_depth_entrance = True
        nodes[depth_entrance_id].gate = GateRequirement(
            gate_type=GateType.ANOMALY,
            primary_metric="anomaly_exposure",
            threshold=50.0,
            alternate_paths=[
                {"metric": "league_tier", "threshold": 3.0},
                {"metric": "research_milestones", "threshold": 5.0},
            ],
        )
        # Connect depth to interior-most node on central basin
        if cb_spine:
            _add_edge(nodes, cb_spine[-1], depth_entrance_id)

    # ── 7. Sub-islets ──────────────────────────────────────
    n_islets = rng.randint(2, 5)
    sub_islet_all: List[List[str]] = []
    islet_rng = rng.fork("islets")
    for ii in range(n_islets):
        islet_size = islet_rng.randint(4, 10)
        islet_biome = _region_biome(climate, MacroRegion.SUB_ISLET,
                                     islet_rng.fork(f"islet_{ii}"))
        islet_ids: List[str] = []
        for si in range(islet_size):
            nid = _nid("is")
            nt_roll = islet_rng.random()
            nt = (NodeType.SETTLEMENT if nt_roll < 0.2
                  else NodeType.WILD_ZONE if nt_roll < 0.5
                  else NodeType.PATH)
            n = MapNode(
                node_id=nid, node_type=nt,
                region=MacroRegion.SUB_ISLET,
                name=_generate_name(islet_rng, prefix="Isle"),
                biome=islet_biome.perturb(islet_rng, 0.05),
            )
            nodes[nid] = n
            if islet_ids:
                _add_edge(nodes, islet_ids[-1], nid)
            islet_ids.append(nid)
        # Loop
        if len(islet_ids) >= 4:
            _add_edge(nodes, islet_ids[0], islet_ids[-1])
        # Gate entrance from main island
        if islet_ids:
            existing_ids = [nid for nid, nd in nodes.items()
                           if nd.region != MacroRegion.SUB_ISLET]
            if existing_ids:
                anchor = islet_rng.choice(existing_ids)
                _add_edge(nodes, anchor, islet_ids[0])
                nodes[islet_ids[0]].gate = GateRequirement(
                    gate_type=islet_rng.choice(list(GateType)),
                    primary_metric="exploration_score",
                    threshold=islet_rng.uniform(20, 60),
                )
        sub_islet_all.append(islet_ids)

    # ── 8. Facilities (research / industrial) ──────────────
    n_facilities = rng.randint(3, 6)
    fac_rng = rng.fork("facilities")
    existing_ids = list(nodes.keys())
    for fi in range(n_facilities):
        nid = _nid("fac")
        anchor = fac_rng.choice(existing_ids)
        anchor_node = nodes[anchor]
        n = MapNode(
            node_id=nid, node_type=NodeType.FACILITY,
            region=anchor_node.region,
            name=_generate_name(fac_rng, prefix="Lab" if fi % 2 == 0 else "Works"),
            biome=anchor_node.biome.perturb(fac_rng, 0.03),
        )
        nodes[nid] = n
        _add_edge(nodes, anchor, nid)

    # ── 9. Gate placement (8-15 gated edges) ───────────────
    gate_rng = rng.fork("gates")
    n_gates = gate_rng.randint(8, 15)
    # We already placed depth + islet gates; add more
    placed_gates = 1 + n_islets  # depth entrance + islet entrances
    non_start_ids = [nid for nid, nd in nodes.items()
                     if not nd.is_start and nd.gate is None]
    gate_rng.shuffle(non_start_ids)
    for gi in range(min(n_gates - placed_gates, len(non_start_ids))):
        target_nid = non_start_ids[gi]
        target = nodes[target_nid]
        # Don't gate start-radius nodes (distance ≤ 2 from S0)
        if s0_id in nodes:
            # Quick distance check using BFS (only for small radius)
            dist = 0
            found = False
            visited_set: Set[str] = {s0_id}
            frontier_set = [s0_id]
            while frontier_set and dist < 3:
                dist += 1
                nf: List[str] = []
                for fid in frontier_set:
                    for nb in nodes[fid].neighbors:
                        if nb == target_nid:
                            found = True
                            break
                        if nb not in visited_set:
                            visited_set.add(nb)
                            nf.append(nb)
                    if found:
                        break
                frontier_set = nf
            if found and dist <= 2:
                continue  # skip gating near start

        gt = gate_rng.choice(list(GateType))
        metric_map = {
            GateType.LEAGUE: "trainer_rating",
            GateType.REPUTATION: "faction_standing",
            GateType.RESEARCH: "research_milestones",
            GateType.ECOLOGICAL: "ecological_balance",
            GateType.ANOMALY: "anomaly_exposure",
            GateType.ECONOMIC: "economic_investment",
        }
        target.gate = GateRequirement(
            gate_type=gt,
            primary_metric=metric_map[gt],
            threshold=gate_rng.uniform(15, 80),
            flex_buffer=gate_rng.uniform(0, 10),
        )

    # ── 10. Ensure extra cities / settlements to hit targets ──
    settlement_count = sum(1 for n in nodes.values()
                          if n.node_type == NodeType.SETTLEMENT)
    city_count = sum(1 for n in nodes.values()
                     if n.node_type == NodeType.CITY)
    # Promote some settlements to cities if needed
    if city_count < 5:
        for nid, nd in list(nodes.items()):
            if nd.node_type == NodeType.SETTLEMENT and len(nd.neighbors) >= 3:
                nd.node_type = NodeType.CITY
                nd.name = _generate_name(rng.fork(f"promote_{nid}"), prefix="City")
                city_count += 1
                if city_count >= 5:
                    break

    # ── 10b. Relay Nodes (§19 / A4) ───────────────────────
    # 1-3 relay nodes placed deterministically in depth / dungeon nodes.
    # Accessibility is gated by anomaly_exposure; the gate threshold is
    # modulated at runtime by the player's current containment tier.
    relay_rng = rng.fork("relay_nodes")
    n_relay = relay_rng.randint(1, 3)
    relay_candidates = [
        nid for nid, nd in nodes.items()
        if nd.region == MacroRegion.INTERIOR_DEPTH
        or nd.node_type == NodeType.DUNGEON
    ]
    relay_rng.shuffle(relay_candidates)
    relay_node_ids: List[str] = []
    for ri in range(min(n_relay, len(relay_candidates))):
        rn_id = relay_candidates[ri]
        nd = nodes[rn_id]
        nd.is_relay_node = True
        nd.name = _generate_name(relay_rng, prefix="Relay")
        # Relay nodes get an anomaly gate - tier modulates the threshold at runtime
        if not nd.gate:
            nd.gate = GateRequirement(
                gate_type=GateType.ANOMALY,
                primary_metric="anomaly_exposure",
                threshold=relay_rng.uniform(40.0, 75.0),
                alternate_paths=[
                    {"metric": "research_milestones", "threshold": relay_rng.uniform(3.0, 7.0)},
                ],
            )
        relay_node_ids.append(rn_id)
    _dbg(f"Relay nodes: {relay_node_ids}")

    # ── 10c. Anomaly Zones (§19 / A4) ─────────────────────
    # Convert a handful of high-instability nodes to ANOMALY_ZONE type.
    # These are the locations where systemic seams are most visible.
    anomaly_zone_ids: List[str] = []
    high_instab = sorted(
        nodes.items(),
        key=lambda kv: kv[1].biome.instability_bias,
        reverse=True,
    )
    n_anomaly_zones = relay_rng.randint(3, 7)
    for az_id, az_node in high_instab:
        if len(anomaly_zone_ids) >= n_anomaly_zones:
            break
        if az_node.node_type in (NodeType.WILD_ZONE, NodeType.DUNGEON,
                                  NodeType.PATH):
            az_node.node_type = NodeType.ANOMALY_ZONE
            anomaly_zone_ids.append(az_id)
    _dbg(f"Anomaly zones: {len(anomaly_zone_ids)}")

    # ── 11. Biome adjacency blending (70/20/10 rule) ───────
    # One pass of smoothing
    for nid, nd in nodes.items():
        neighbor_biomes = [nodes[nb].biome for nb in nd.neighbors if nb in nodes]
        if not neighbor_biomes:
            continue
        avg_nb = BiomeVector(
            temperature=sum(b.temperature for b in neighbor_biomes) / len(neighbor_biomes),
            moisture=sum(b.moisture for b in neighbor_biomes) / len(neighbor_biomes),
            elevation=sum(b.elevation for b in neighbor_biomes) / len(neighbor_biomes),
            vegetation_density=sum(b.vegetation_density for b in neighbor_biomes) / len(neighbor_biomes),
            instability_bias=sum(b.instability_bias for b in neighbor_biomes) / len(neighbor_biomes),
        )
        # 70% self, 20% neighbors, 10% noise (already in initial perturb)
        nd.biome = nd.biome.blend(avg_nb, 0.2)

    topo = IslandTopology(
        seed=seed,
        island_name=name,
        climate=climate,
        nodes=nodes,
        start_node_id=s0_id,
        depth_entrance_ids=[depth_entrance_id] if depth_entrance_id else [],
        sub_islet_ids=sub_islet_all,
        active_types=active_types,
        relay_node_ids=relay_node_ids,
        anomaly_zone_ids=anomaly_zone_ids,
    )

    _dbg(f"Island '{name}' (seed={seed}): {topo.node_count} nodes, "
         f"{city_count} cities, {len(active_types)} active types, "
         f"climate={climate.name}")
    return topo


# ============================================================
# §5  SPECIES GENERATION (300 PER ISLAND)
# ============================================================

class RarityTier(Enum):
    COMMON   = 0
    UNCOMMON = 1
    RARE     = 2
    ELITE    = 3
    APEX     = 4
    ANOMALY  = 5


# BST ranges per rarity
_BST_RANGES: Dict[RarityTier, Tuple[int, int]] = {
    RarityTier.COMMON:   (300, 380),
    RarityTier.UNCOMMON: (360, 440),
    RarityTier.RARE:     (420, 500),
    RarityTier.ELITE:    (480, 560),
    RarityTier.APEX:     (550, 620),
    RarityTier.ANOMALY:  (350, 650),  # volatile
}

# Rarity distribution targets (out of 300)
_RARITY_TARGETS: Dict[RarityTier, Tuple[int, int]] = {
    RarityTier.COMMON:   (110, 130),   # ~40%
    RarityTier.UNCOMMON: (65, 85),     # ~25%
    RarityTier.RARE:     (38, 52),     # ~15%
    RarityTier.ELITE:    (25, 35),     # ~10%
    RarityTier.APEX:     (10, 18),     # ~5%
    RarityTier.ANOMALY:  (12, 22),     # ~5%
}


class StatArchetype(Enum):
    BALANCED  = auto()
    GLASS     = auto()   # high offense, low defense
    TANK      = auto()   # high defense, low offense
    TEMPO     = auto()   # high reflex/flux
    DISRUPTOR = auto()   # high focus, moderate others
    VOLATILE  = auto()   # anomaly-tier - wild distribution


@dataclass
class StatVector:
    """6-stat creature stat block."""
    vitality:  int = 50
    force:     int = 50
    reflex:    int = 50
    focus:     int = 50
    stability: int = 50
    flux:      int = 50

    @property
    def bst(self) -> int:
        return (self.vitality + self.force + self.reflex
                + self.focus + self.stability + self.flux)

    def to_tuple(self) -> Tuple[int, ...]:
        return (self.vitality, self.force, self.reflex,
                self.focus, self.stability, self.flux)


@dataclass
class HabitatAffinity:
    """Species habitat preference matching BiomeVector structure."""
    temperature_pref:  float = 0.5
    moisture_pref:     float = 0.5
    elevation_pref:    float = 0.3
    vegetation_pref:   float = 0.5
    instability_pref:  float = 0.1

    def distance_to_biome(self, bv: BiomeVector) -> float:
        return math.sqrt(
            (self.temperature_pref - bv.temperature) ** 2
            + (self.moisture_pref - bv.moisture) ** 2
            + (self.elevation_pref - bv.elevation) ** 2
            + (self.vegetation_pref - bv.vegetation_density) ** 2
            + (self.instability_pref - bv.instability_bias) ** 2
        )


@dataclass
class GeneticProfile:
    """Per-instance genetic profile."""
    stat_genes: List[int] = field(default_factory=lambda: [16] * 6)  # 0-31 each
    trait_genes: List[str] = field(default_factory=list)
    stability_gene: int = 16
    variance_seed: int = 0
    lineage_depth: int = 0

    # Gene clusters
    @property
    def physical_cluster(self) -> int:
        return self.stat_genes[0] + self.stat_genes[1]  # vitality + force

    @property
    def tempo_cluster(self) -> int:
        return self.stat_genes[2] + self.stat_genes[5]  # reflex + flux

    @property
    def cognitive_cluster(self) -> int:
        return self.stat_genes[3] + self.stat_genes[4]  # focus + stability


@dataclass
class Species:
    """A species template (one of 300 per island)."""
    species_id: str = ""
    name: str = ""
    primary_type: NkType = NkType.EMBER
    secondary_type: Optional[NkType] = None
    rarity: RarityTier = RarityTier.COMMON
    base_stats: StatVector = field(default_factory=StatVector)
    stat_archetype: StatArchetype = StatArchetype.BALANCED
    habitat: HabitatAffinity = field(default_factory=HabitatAffinity)
    move_pool: List[str] = field(default_factory=list)
    passive_trait: str = ""
    mutation_potential: float = 0.5
    evolution_stage: int = 1        # 1, 2, or 3
    evolution_line_id: str = ""     # shared across stages
    evolves_from: Optional[str] = None
    evolves_to: Optional[str] = None
    biome_affinity_regions: List[MacroRegion] = field(default_factory=list)

    def to_dict(self) -> dict:
        # evo_level: the level threshold at which this species evolves to the next stage.
        # None for species with no evolves_to (single-stage lines or final-stage species).
        if self.evolves_to:
            _evo_level = 16 if self.evolution_stage == 1 else (36 if self.evolution_stage == 2 else None)
        else:
            _evo_level = None
        return {
            "species_id": self.species_id,
            "name": self.name,
            "primary_type": self.primary_type.name,
            "secondary_type": self.secondary_type.name if self.secondary_type else None,
            "rarity": self.rarity.name,
            "bst": self.base_stats.bst,
            "stats": self.base_stats.to_tuple(),
            "archetype": self.stat_archetype.name,
            "passive": self.passive_trait,
            "evo_stage": self.evolution_stage,
            "evo_line": self.evolution_line_id,
            "evolves_to": self.evolves_to,
            "evolves_from": self.evolves_from,
            "evo_level": _evo_level,
        }


@dataclass
class CreatureInstance:
    """A living creature instance (owned by player or wild)."""
    instance_id: str = ""
    species_id: str = ""
    nickname: str = ""
    level: int = 1
    xp: int = 0                   # accumulated experience points
    genes: GeneticProfile = field(default_factory=GeneticProfile)
    fatigue: float = 0.0          # 0-100
    loyalty: float = 50.0         # 0-100
    temperament: float = 0.5      # 0-1
    adaptation_drift: float = 0.0
    exposure_history: List[str] = field(default_factory=list)
    # Effective stats = base_stats modified by genes, level, fatigue
    current_hp: int = 100

    def effective_stats(self, species: Species) -> StatVector:
        """Compute effective stats from base + genes + level + fatigue."""
        bs = species.base_stats
        g = self.genes.stat_genes
        fatigue_mult = max(0.5, 1.0 - self.fatigue / 200.0)
        level_mult = 1.0 + (self.level - 1) * 0.02
        def _calc(base: int, gene: int) -> int:
            return max(1, int((base + gene) * level_mult * fatigue_mult))
        return StatVector(
            vitality=_calc(bs.vitality, g[0]),
            force=_calc(bs.force, g[1]),
            reflex=_calc(bs.reflex, g[2]),
            focus=_calc(bs.focus, g[3]),
            stability=_calc(bs.stability, g[4]),
            flux=_calc(bs.flux, g[5]),
        )


# ── XP / Leveling helpers ──────────────────────────────────

_XP_MAX_LEVEL = 50  # hard cap


def xp_for_level(level: int) -> int:
    """Total XP needed to reach `level` from level 1.

    Formula: sum of (n * 20) for n in 1..level-1  (linear, so early levels are fast)
    Level 2  needs  20 XP,  marginal.
    Level 16 needs 2400 XP total  (~20-30 battles  → first evolution)
    Level 36 needs 12600 XP total (~100-150 battles → second evolution)
    Level 50 needs 24500 XP total (deep late-game)
    """
    return sum(n * 20 for n in range(1, level))


def xp_to_next_level(level: int) -> int:
    """Marginal XP cost to advance from `level` to `level+1`."""
    if level >= _XP_MAX_LEVEL:
        return 0
    return level * 20  # linear marginal cost


def award_battle_xp(
    inst: "CreatureInstance",
    species: "Species",
    opponent_avg_level: float,
    turns_fought: int,
    won: bool,
) -> List[dict]:
    """Award XP to a creature after a battle.  Returns list of level_up events (may be empty).

    XP awarded = base_xp * difficulty_mult * win_bonus
      base_xp        = 20 + turns_fought * 2
      difficulty_mult = clamp(opponent_avg_level / max(1, inst.level), 0.5, 3.0)
      win_bonus       = 1.5 if player won else 0.8
    """
    if inst.level >= _XP_MAX_LEVEL:
        return []

    base_xp = 20 + turns_fought * 2
    diff = max(0.5, min(3.0, opponent_avg_level / max(1, inst.level)))
    win_bonus = 1.5 if won else 0.8
    gained = max(1, int(base_xp * diff * win_bonus))

    inst.xp += gained
    events: List[dict] = []

    # Resolve level-ups (may gain multiple levels in one fight)
    while inst.level < _XP_MAX_LEVEL:
        needed = xp_to_next_level(inst.level)
        if inst.xp >= xp_for_level(inst.level + 1):
            inst.level += 1
            events.append({
                "type": "level_up",
                "instance_id": inst.instance_id,
                "species_id": inst.species_id,
                "new_level": inst.level,
                "xp_gained": gained,
                "species_name": species.name,
            })
        else:
            break

    return events


def award_capture_xp(
    inst: "CreatureInstance",
    species: "Species",
    target_level: int,
) -> List[dict]:
    """Award a flat XP reward for capturing a creature.

    Rewards scouting new species; bonus if the target outlevels the capturer.
    """
    if inst.level >= _XP_MAX_LEVEL:
        return []
    gained = max(5, int(15 + target_level * 0.5))
    inst.xp += gained
    events: List[dict] = []
    while inst.level < _XP_MAX_LEVEL:
        if inst.xp >= xp_for_level(inst.level + 1):
            inst.level += 1
            events.append({
                "type": "level_up",
                "instance_id": inst.instance_id,
                "species_id": inst.species_id,
                "new_level": inst.level,
                "xp_gained": gained,
                "species_name": species.name,
            })
        else:
            break
    return events


# ── Evolution thresholds ───────────────────────────────────
#
# Stage 1 → Stage 2: creature must reach level _EVO_STAGE1_LEVEL
# Stage 2 → Stage 3: creature must reach level _EVO_STAGE2_LEVEL
#
# These thresholds match natural XP pacing (Lv16 typical capture level,
# Lv36 deep-mid-game). Anomaly-tier species cannot evolve further.

_EVO_STAGE1_LEVEL: int = 16   # Stage 1 → Stage 2
_EVO_STAGE2_LEVEL: int = 36   # Stage 2 → Stage 3


def check_evolution(
    inst: "CreatureInstance",
    species_map: dict,
    player_team: list,
) -> list:
    """Check if a creature is ready to evolve after a level-up.

    Mutates ``inst.species_id`` and updates the matching tuple in
    ``player_team`` in-place.  Returns a list of evolution event dicts
    (may be empty).  Only progresses one stage at a time.
    """
    events: list = []
    cur_sp = species_map.get(inst.species_id)
    if cur_sp is None:
        return events

    # Determine threshold for the current evolution stage
    if cur_sp.evolution_stage == 1 and inst.level >= _EVO_STAGE1_LEVEL:
        next_sid = cur_sp.evolves_to
    elif cur_sp.evolution_stage == 2 and inst.level >= _EVO_STAGE2_LEVEL:
        next_sid = cur_sp.evolves_to
    else:
        return events

    if not next_sid or next_sid not in species_map:
        return events  # no evolution defined

    next_sp = species_map[next_sid]

    # Update the instance's species pointer
    old_sid = inst.species_id
    inst.species_id = next_sid

    # Update the player_team list (list of (instance_id, species_id) tuples)
    for idx, (iid, sid) in enumerate(player_team):
        if iid == inst.instance_id:
            player_team[idx] = (iid, next_sid)
            break

    events.append({
        "type": "evolved",
        "instance_id": inst.instance_id,
        "old_species_id": old_sid,
        "old_species_name": cur_sp.name,
        "new_species_id": next_sid,
        "new_species_name": next_sp.name,
        "new_evo_stage": next_sp.evolution_stage,
        "level": inst.level,
    })
    return events


# ── Trait library ──────────────────────────────────────────

_PASSIVE_TRAITS = [
    "Adaptive", "Resilient", "Swift", "Cunning", "Brute",
    "Stoic", "Volatile", "Predatory", "Symbiotic", "Migratory",
    "Burrower", "Glider", "Nocturnal", "Photosynthetic", "Venomous",
    "Armored", "Regenerator", "Echolocator", "Pack Hunter", "Solitary",
    "Thermal Vent", "Tidal Rhythm", "Spore Bearer", "Crystal Shell",
    "Rift Born", "Storm Caller", "Root Anchor", "Sand Walker",
    "Mist Cloak", "Pulse Emitter",
]

# ── Per-type move pool ─────────────────────────────────────
#
# Each NkType has a dedicated pool of 12 moves across four categories:
#   [D] Damage     - direct offensive moves
#   [S] Status     - apply/remove StatusEffect or trajectory modifiers
#   [F] Field      - alter the battle field (terrain, weather, hazard)
#   [P] Passive    - reactions / trigger-based effects
#
# Format per move: "<Name> [D|S|F|P]"
# The category tag is stored for LLM narration routing; the battle engine
# reads only the name string today. Future: add power/accuracy columns.

_TYPE_MOVE_POOL: Dict[NkType, List[str]] = {
    NkType.EMBER: [
        "Cinder Strike [D]",      "Magma Surge [D]",         "Char Lash [D]",
        "Ignition Burst [D]",     "Searing Edge [D]",        "Flamewall [S]",
        "Burn Coat [S]",          "Overheat [S]",            "Scorched Field [F]",
        "Ember Rain [F]",         "Backdraft Trigger [P]",   "Ashen Guard [P]",
    ],
    NkType.TIDE: [
        "Tidal Crash [D]",        "Undertow Lash [D]",       "Salt Surge [D]",
        "Riptide Burst [D]",      "Wave Cutter [D]",         "Current Drain [S]",
        "Soak Coat [S]",          "Pull Under [S]",          "High Tide Field [F]",
        "Brackish Mist [F]",      "Undertow Counter [P]",    "Brine Shell [P]",
    ],
    NkType.STONE: [
        "Boulder Drop [D]",       "Gravel Slam [D]",         "Fault Line [D]",
        "Rockslide [D]",          "Core Crush [D]",          "Petrify [S]",
        "Iron Skin [S]",          "Sediment Layer [S]",      "Quake Vent [F]",
        "Dust Wall [F]",          "Bedrock Stance [P]",      "Impact Return [P]",
    ],
    NkType.GALE: [
        "Gale Cut [D]",           "Crosswind Slash [D]",     "Vortex Strike [D]",
        "Headwind Tear [D]",      "Shear Force [D]",         "Updraft [S]",
        "Wind Cloak [S]",         "Pressure Drop [S]",       "Storm Front [F]",
        "Cyclone Pin [F]",        "Draft Dodge [P]",         "Tempest Return [P]",
    ],
    NkType.VERDANT: [
        "Root Whip [D]",          "Thorn Barrage [D]",       "Spore Lash [D]",
        "Overgrowth Strike [D]",  "Canopy Crash [D]",        "Leech Sap [S]",
        "Spore Cloud [S]",        "Entangle [S]",            "Dense Canopy [F]",
        "Creep Terrain [F]",      "Photosynthetic Surge [P]","Symbiotic Bond [P]",
    ],
    NkType.FROST: [
        "Ice Shard [D]",          "Frostbite Rake [D]",      "Glacial Slam [D]",
        "Sleet Whip [D]",         "Cryo Pulse [D]",          "Freeze Coat [S]",
        "Permafrost [S]",         "Chill Field [S]",         "Blizzard Veil [F]",
        "Snowpack Wall [F]",      "Ice Mirror [P]",          "Cold Snap Trigger [P]",
    ],
    NkType.VENOM: [
        "Venom Spray [D]",        "Toxin Fang [D]",          "Acid Lash [D]",
        "Corrosive Burst [D]",    "Neurotoxin Stab [D]",     "Poison Coat [S]",
        "Dissolve Armor [S]",     "Contaminate [S]",         "Toxic Bog [F]",
        "Spore Hazard [F]",       "Residue Counter [P]",     "Venom Pulse [P]",
    ],
    NkType.ALLOY: [
        "Steel Spike [D]",        "Rivet Slam [D]",          "Alloy Lance [D]",
        "Shrapnel Burst [D]",     "Magnetar Drive [D]",      "Alloy Shell [S]",
        "Lattice Lock [S]",       "Overclock [S]",           "Metal Terrain [F]",
        "Mag Pulse Field [F]",    "Ricochet Guard [P]",      "Core Resonance [P]",
    ],
    NkType.SHADE: [
        "Shadow Lash [D]",        "Void Claw [D]",           "Dark Tide [D]",
        "Penumbra Strike [D]",    "Eclipse Slash [D]",       "Blind Shroud [S]",
        "Phase Blur [S]",         "Drain Essence [S]",       "Darkness Field [F]",
        "Silhouette Pin [F]",     "Shadow Step Counter [P]", "Mist Cloak Trigger [P]",
    ],
    NkType.RADIANT: [
        "Radiant Flash [D]",      "Prism Lance [D]",         "Solar Strike [D]",
        "Light Burst [D]",        "Photon Blade [D]",        "Dazzle [S]",
        "Illuminate [S]",         "Halo Guard [S]",          "Lens Field [F]",
        "Dawn Terrain [F]",       "Refraction Counter [P]",  "Beacon Pulse [P]",
    ],
    NkType.RIFT: [
        "Rift Tear [D]",          "Space Crush [D]",         "Phase Rupture [D]",
        "Warp Strike [D]",        "Seam Slash [D]",          "Distort [S]",
        "Null Zone [S]",          "Spatial Drain [S]",       "Rift Terrain [F]",
        "Collapse Point [F]",     "Fold Counter [P]",        "Anomaly Surge [P]",
    ],
    NkType.THORN: [
        "Thorn Snare [D]",        "Spike Barrage [D]",       "Briar Whip [D]",
        "Bramble Crush [D]",      "Burdock Lance [D]",       "Entangle [S]",
        "Barbed Coat [S]",        "Root Lock [S]",           "Briar Terrain [F]",
        "Spike Bed [F]",          "Retribution Thorns [P]",  "Snare Trigger [P]",
    ],
    NkType.BLOOM: [
        "Bloom Burst [D]",        "Petal Blade [D]",         "Nectar Sting [D]",
        "Spore Shower [D]",       "Pollen Surge [D]",        "Lull [S]",
        "Fragrance Veil [S]",     "Charm Bloom [S]",         "Garden Field [F]",
        "Pollin Storm [F]",       "Overgrowth Surge [P]",    "Blossom Counter [P]",
    ],
    NkType.TORRENT: [
        "Torrent Slam [D]",       "Flood Rush [D]",          "Current Whip [D]",
        "Hydro Lance [D]",        "Deluge Strike [D]",       "Saturate [S]",
        "Current Coat [S]",       "Whirlpool Lock [S]",      "Flash Flood [F]",
        "Mudslide Field [F]",     "Reservoir Counter [P]",   "Surge Pulse [P]",
    ],
    NkType.PULSE: [
        "Pulse Wave [D]",         "Resonance Burst [D]",     "Frequency Slash [D]",
        "Oscillate Strike [D]",   "Harmonic Drive [D]",      "Jam Signal [S]",
        "Amplify [S]",            "Frequency Lock [S]",      "Interference Field [F]",
        "Node Terrain [F]",       "Resonance Counter [P]",   "Echo Spike [P]",
    ],
    # Non-active types (fallback pool - used if a species has a non-active secondary)
    NkType.VOLT: [
        "Volt Shock [D]",         "Arc Strike [D]",          "Overcharge [D]",
        "Static Lash [D]",        "Spark Burst [D]",         "Paralyze [S]",
        "Charge Field [S]",       "Short Circuit [S]",       "Storm Terrain [F]",
        "Overload Field [F]",     "Discharge Counter [P]",   "Amp Surge [P]",
    ],
    NkType.ECHO: [
        "Echo Wave [D]",          "Resonant Lash [D]",       "Mirror Scream [D]",
        "Phase Echo [D]",         "Feedback Strike [D]",     "Mimic [S]",
        "Frequency Mask [S]",     "Sound Bind [S]",          "Echo Terrain [F]",
        "Silence Zone [F]",       "Counter Echo [P]",        "Harmonic Shield [P]",
    ],
    NkType.DUNE: [
        "Dune Storm [D]",         "Sand Slash [D]",          "Desert Fang [D]",
        "Dust Surge [D]",         "Quicksand Trap [D]",      "Blind Sand [S]",
        "Sand Coat [S]",          "Bury [S]",                "Arid Field [F]",
        "Sandstorm Veil [F]",     "Erosion Counter [P]",     "Dust Devil Trigger [P]",
    ],
}

# ── Cross-type move cross-contamination pool ───────────────
# When a species draws off-type moves (2-3 per species from secondary
# or adjacent type), these universal utility moves pad the pool.
_UNIVERSAL_MOVES: List[str] = [
    "Stabilize [S]", "Tempo Shift [S]", "Disrupt Field [F]", "Guard [S]",
    "Rush [D]", "Barrier [S]", "Drain [D]", "Quake [D]",
    "Flux Overload [D]", "Pulse Emitter [P]", "Root Anchor [P]",
]


def _build_species_moveset(
    rng: SeededRNG,
    primary: NkType,
    secondary: Optional[NkType],
    archetype: "StatArchetype",
    n_moves: int,
) -> List[str]:
    """
    Build a species' move pool deterministically.

    Rules:
      • At least 3 moves from the primary type pool (always)
      • 1-2 moves from secondary type pool (if dual-typed)
      • Remaining slots: sample from _UNIVERSAL_MOVES
      • Archetype biases which categories dominate:
          GLASS / TEMPO     → prefer [D] moves
          TANK              → prefer [S] and [P] moves
          DISRUPTOR         → prefer [S] and [F] moves
          BALANCED          → even spread
          VOLATILE          → random draw ignoring category
    """
    primary_pool = _TYPE_MOVE_POOL.get(primary, _UNIVERSAL_MOVES)
    secondary_pool = _TYPE_MOVE_POOL.get(secondary, []) if secondary else []

    # Category preference by archetype
    _ARCHETYPE_CATEGORY_BIAS: Dict["StatArchetype", List[str]] = {
        StatArchetype.GLASS:     ["[D]", "[D]", "[S]", "[F]"],
        StatArchetype.TEMPO:     ["[D]", "[D]", "[F]", "[S]"],
        StatArchetype.TANK:      ["[S]", "[P]", "[S]", "[D]"],
        StatArchetype.DISRUPTOR: ["[S]", "[F]", "[S]", "[D]"],
        StatArchetype.BALANCED:  ["[D]", "[S]", "[F]", "[P]"],
        StatArchetype.VOLATILE:  [],  # pure random
    }
    bias_tags = _ARCHETYPE_CATEGORY_BIAS.get(archetype, [])

    def _biased_sample(pool: List[str], k: int) -> List[str]:
        if not pool:
            return []
        if archetype == StatArchetype.VOLATILE or not bias_tags:
            shuffled = list(pool)
            rng.shuffle(shuffled)
            return shuffled[:k]
        # Sort pool to put preferred categories first, then sample
        preferred = [m for m in pool if any(tag in m for tag in bias_tags[:2])]
        rest = [m for m in pool if m not in preferred]
        rng.shuffle(preferred)
        rng.shuffle(rest)
        ordered = (preferred + rest)
        return ordered[:k]

    moves: List[str] = []

    # Guaranteed 3 from primary
    primary_draw = _biased_sample(primary_pool, min(3, len(primary_pool)))
    moves.extend(primary_draw)

    # 1-2 from secondary
    if secondary_pool:
        n_sec = min(rng.randint(1, 2), len(secondary_pool))
        sec_pool_copy = [m for m in secondary_pool if m not in moves]
        rng.shuffle(sec_pool_copy)
        moves.extend(sec_pool_copy[:n_sec])

    # Fill remaining with more primary or universal
    remaining = n_moves - len(moves)
    if remaining > 0:
        extra_primary = [m for m in primary_pool if m not in moves]
        rng.shuffle(extra_primary)
        universal_copy = [m for m in _UNIVERSAL_MOVES if m not in moves]
        rng.shuffle(universal_copy)
        filler = (extra_primary + universal_copy)[:remaining]
        moves.extend(filler)

    return moves[:n_moves]


def _generate_stat_vector(rng: SeededRNG, archetype: StatArchetype,
                          bst_target: int) -> StatVector:
    """Generate a stat vector matching archetype and BST target."""
    # Archetype weight profiles (vitality, force, reflex, focus, stability, flux)
    profiles: Dict[StatArchetype, List[float]] = {
        StatArchetype.BALANCED:  [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        StatArchetype.GLASS:     [0.6, 1.6, 1.2, 1.0, 0.5, 1.1],
        StatArchetype.TANK:      [1.5, 0.7, 0.6, 0.9, 1.5, 0.8],
        StatArchetype.TEMPO:     [0.8, 0.9, 1.5, 0.8, 0.7, 1.3],
        StatArchetype.DISRUPTOR: [0.9, 0.8, 1.0, 1.5, 0.9, 0.9],
        StatArchetype.VOLATILE:  [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    }
    weights = profiles[archetype]
    if archetype == StatArchetype.VOLATILE:
        # Anomaly-style wild variance
        weights = [rng.uniform(0.4, 1.8) for _ in range(6)]

    total_w = sum(weights)
    raw = [bst_target * w / total_w for w in weights]
    # Add small noise
    raw = [max(10, int(r + rng.gauss(0, r * 0.08))) for r in raw]
    # Normalize to hit BST target
    current_bst = sum(raw)
    if current_bst > 0:
        scale = bst_target / current_bst
        raw = [max(10, int(r * scale)) for r in raw]
    # Fix rounding residual
    diff = bst_target - sum(raw)
    if diff != 0:
        idx = rng.randint(0, 5)
        raw[idx] = max(10, raw[idx] + diff)

    return StatVector(
        vitality=raw[0], force=raw[1], reflex=raw[2],
        focus=raw[3], stability=raw[4], flux=raw[5],
    )


def generate_species_roster(topology: IslandTopology) -> Dict[str, Species]:
    """
    Generate 300 species for an island based on its topology, climate,
    and active type pool.
    """
    seed = topology.seed
    rng = SeededRNG(seed).fork("species")
    climate = topology.climate
    active_types = topology.active_types
    n_types = len(active_types)

    species_map: Dict[str, Species] = {}
    species_list: List[Species] = []

    # ── Rarity distribution (constrained allocator) ────────
    # Start with guaranteed minimums, then distribute remaining budget
    # by weighted random.  This prevents the old approach from summing
    # independent random picks to more (or fewer) than 300.
    rarity_counts: Dict[RarityTier, int] = {}
    budget = 300
    for rt in RarityTier:
        lo, _hi = _RARITY_TARGETS[rt]
        rarity_counts[rt] = lo
        budget -= lo
    # budget is now the surplus above all minimums
    # Distribute surplus one-at-a-time, respecting per-tier ceilings
    tiers_with_room = [rt for rt in RarityTier
                       if rarity_counts[rt] < _RARITY_TARGETS[rt][1]]
    while budget > 0 and tiers_with_room:
        rt = rng.choice(tiers_with_room)
        rarity_counts[rt] += 1
        budget -= 1
        if rarity_counts[rt] >= _RARITY_TARGETS[rt][1]:
            tiers_with_room = [r for r in tiers_with_room if r != rt]
    # Any leftover (very unlikely) goes to COMMON
    if budget > 0:
        rarity_counts[RarityTier.COMMON] += budget

    # ── Evolution lines ────────────────────────────────────
    evo_rng = rng.fork("evolution")
    line_counter = [0]
    species_counter = [0]

    def _next_line_id() -> str:
        line_counter[0] += 1
        return f"line_{line_counter[0]:04d}"

    def _next_species_id() -> str:
        species_counter[0] += 1
        return f"sp_{species_counter[0]:04d}"

    def _make_species(line_id: str, stage: int, rarity: RarityTier,
                      primary: NkType, secondary: Optional[NkType],
                      archetype: StatArchetype,
                      prev_id: Optional[str] = None) -> Species:
        sid = _next_species_id()
        bst_lo, bst_hi = _BST_RANGES[rarity]
        # Higher stages get higher BST within range
        stage_bias = (stage - 1) * 0.3
        bst = rng.randint(
            int(bst_lo + (bst_hi - bst_lo) * stage_bias * 0.3),
            bst_hi,
        )
        stats = _generate_stat_vector(rng.fork(sid), archetype, bst)

        # Habitat affinity
        climate_base = CLIMATE_BASES[climate]
        # Bias by type
        type_temp_bias = {
            NkType.EMBER: 0.15, NkType.FROST: -0.2, NkType.TIDE: -0.05,
            NkType.DUNE: 0.1, NkType.VERDANT: 0.0, NkType.VOLT: 0.0,
        }
        temp_off = type_temp_bias.get(primary, 0.0)
        hab = HabitatAffinity(
            temperature_pref=max(0, min(1, climate_base.temperature + temp_off + rng.gauss(0, 0.08))),
            moisture_pref=max(0, min(1, climate_base.moisture + rng.gauss(0, 0.1))),
            elevation_pref=max(0, min(1, climate_base.elevation + rng.gauss(0, 0.1))),
            vegetation_pref=max(0, min(1, climate_base.vegetation_density + rng.gauss(0, 0.1))),
            instability_pref=max(0, min(1, climate_base.instability_bias + rng.gauss(0, 0.05))),
        )

        # Move pool (5-9 moves, type-accurate)
        n_moves = rng.randint(5, 9)
        moves = _build_species_moveset(
            rng.fork(f"moves_{sid}"), primary, secondary, archetype, n_moves
        )

        # Passive trait
        passive = rng.choice(_PASSIVE_TRAITS)

        # Region affinity (1-3 regions)
        regions = list(MacroRegion)
        n_reg = rng.randint(1, 3)
        region_affinities = rng.sample(regions, min(n_reg, len(regions)))

        sp = Species(
            species_id=sid,
            name=_generate_name(rng.fork(f"name_{sid}"), min_syl=2, max_syl=3),
            primary_type=primary,
            secondary_type=secondary,
            rarity=rarity,
            base_stats=stats,
            stat_archetype=archetype,
            habitat=hab,
            move_pool=moves,
            passive_trait=passive,
            mutation_potential=rng.uniform(0.2, 0.9),
            evolution_stage=stage,
            evolution_line_id=line_id,
            evolves_from=prev_id,
            biome_affinity_regions=region_affinities,
        )
        species_map[sid] = sp
        species_list.append(sp)
        return sp

    # ── Build lines ────────────────────────────────────────
    # Type allocation: ensure each active type has ≥10 species
    type_quotas: Dict[NkType, int] = {t: 0 for t in active_types}
    min_per_type = 12  # guarantee at least 12 per type (well above 10 floor)

    # Track species per rarity used
    rarity_used: Dict[RarityTier, int] = {rt: 0 for rt in RarityTier}

    def _pick_rarity() -> RarityTier:
        """Pick a rarity tier that still has budget."""
        available = [rt for rt in RarityTier if rarity_used[rt] < rarity_counts[rt]]
        if not available:
            return RarityTier.COMMON
        return rng.choice(available)

    def _pick_archetype() -> StatArchetype:
        archetypes = list(StatArchetype)
        return rng.choice(archetypes[:-1])  # exclude VOLATILE for non-anomaly

    # Ensure minimum type coverage
    for t in active_types:
        while type_quotas[t] < min_per_type and len(species_list) < 300:
            line_id = _next_line_id()
            stages = evo_rng.choice([1, 2, 2, 2, 3, 3])
            rarity_base = _pick_rarity()
            arch = _pick_archetype()
            # Dual type?
            secondary = None
            if evo_rng.random() < 0.47:
                candidates = [at for at in active_types if at != t]
                if candidates:
                    secondary = evo_rng.choice(candidates)

            prev_id = None
            for stage in range(1, stages + 1):
                # Rarity escalates with stage
                if stage == 1:
                    r = rarity_base
                elif stage == 2:
                    r = RarityTier(min(rarity_base.value + 1, RarityTier.ELITE.value))
                else:
                    r = RarityTier(min(rarity_base.value + 2, RarityTier.APEX.value))
                sp = _make_species(line_id, stage, r, t, secondary, arch, prev_id)
                if prev_id and prev_id in species_map:
                    species_map[prev_id].evolves_to = sp.species_id
                prev_id = sp.species_id
                type_quotas[t] += 1
                rarity_used[r] = rarity_used.get(r, 0) + 1

    # Fill remaining slots
    while len(species_list) < 300:
        t = rng.choice(active_types)
        line_id = _next_line_id()
        stages = evo_rng.choice([1, 1, 2])
        rarity_base = _pick_rarity()
        arch = _pick_archetype()
        secondary = None
        if evo_rng.random() < 0.47:
            candidates = [at for at in active_types if at != t]
            if candidates:
                secondary = evo_rng.choice(candidates)
        prev_id = None
        for stage in range(1, stages + 1):
            if len(species_list) >= 300:
                break
            if stage == 1:
                r = rarity_base
            else:
                r = RarityTier(min(rarity_base.value + 1, RarityTier.ELITE.value))
            sp = _make_species(line_id, stage, r, t, secondary, arch, prev_id)
            if prev_id and prev_id in species_map:
                species_map[prev_id].evolves_to = sp.species_id
            prev_id = sp.species_id
            type_quotas[t] += 1
            rarity_used[r] = rarity_used.get(r, 0) + 1

    # Anomaly tier: override some species
    anomaly_count = rarity_counts.get(RarityTier.ANOMALY, 15)
    anomaly_candidates = [sp for sp in species_list
                         if sp.rarity in (RarityTier.RARE, RarityTier.ELITE)]
    evo_rng.shuffle(anomaly_candidates)
    for i in range(min(anomaly_count, len(anomaly_candidates))):
        sp = anomaly_candidates[i]
        sp.rarity = RarityTier.ANOMALY
        sp.stat_archetype = StatArchetype.VOLATILE
        bst = rng.randint(350, 650)
        sp.base_stats = _generate_stat_vector(rng.fork(f"anom_{sp.species_id}"),
                                               StatArchetype.VOLATILE, bst)
        sp.habitat.instability_pref = min(1.0, sp.habitat.instability_pref + 0.3)

    _dbg(f"Species roster: {len(species_list)} species, "
         f"{len(set(sp.evolution_line_id for sp in species_list))} lines")
    return species_map


# ============================================================
# §6  ENCOUNTER TABLE SYSTEM
# ============================================================

@dataclass
class EncounterTable:
    """Per-node encounter table with rarity-tiered slots."""
    node_id: str = ""
    common_slots: List[str] = field(default_factory=list)      # species_ids
    uncommon_slots: List[str] = field(default_factory=list)
    rare_slots: List[str] = field(default_factory=list)
    elite_slots: List[str] = field(default_factory=list)
    apex_slot: Optional[str] = None
    anomaly_slot: Optional[str] = None

    def all_species(self) -> List[str]:
        result = (self.common_slots + self.uncommon_slots
                  + self.rare_slots + self.elite_slots)
        if self.apex_slot:
            result.append(self.apex_slot)
        if self.anomaly_slot:
            result.append(self.anomaly_slot)
        return result


# Default slot counts by node type
_SLOT_COUNTS: Dict[NodeType, Dict[str, int]] = {
    NodeType.PATH:      {"common": 6, "uncommon": 3, "rare": 2, "elite": 0, "apex": 0},
    NodeType.WILD_ZONE: {"common": 5, "uncommon": 4, "rare": 3, "elite": 1, "apex": 0},
    NodeType.DUNGEON:   {"common": 2, "uncommon": 3, "rare": 3, "elite": 2, "apex": 1},
    NodeType.LANDMARK:  {"common": 3, "uncommon": 3, "rare": 2, "elite": 1, "apex": 0},
    NodeType.SETTLEMENT:{"common": 4, "uncommon": 2, "rare": 1, "elite": 0, "apex": 0},
    NodeType.CITY:      {"common": 3, "uncommon": 2, "rare": 1, "elite": 0, "apex": 0},
    NodeType.FACILITY:  {"common": 2, "uncommon": 2, "rare": 2, "elite": 1, "apex": 0},
    NodeType.GATE:      {"common": 4, "uncommon": 3, "rare": 2, "elite": 0, "apex": 0},
}

_HABITAT_THRESHOLD = 0.65  # max distance for species eligibility


def generate_encounter_tables(
    topology: IslandTopology,
    species_map: Dict[str, Species],
    ledger: Optional["IslandLedger"] = None,
) -> Dict[str, EncounterTable]:
    """
    Build encounter tables for every node in the island.

    Early-game protection: within radius 3 of start, no apex/anomaly,
    rare capped at 1.
    """
    rng = SeededRNG(topology.seed).fork("encounters")
    tables: Dict[str, EncounterTable] = {}

    # Precompute start-radius
    start_radius: Set[str] = set()
    if topology.start_node_id:
        visited: Set[str] = {topology.start_node_id}
        frontier = [topology.start_node_id]
        for _ in range(3):
            nf: List[str] = []
            for fid in frontier:
                node = topology.nodes.get(fid)
                if not node:
                    continue
                for nb in node.neighbors:
                    if nb not in visited:
                        visited.add(nb)
                        nf.append(nb)
            frontier = nf
        start_radius = visited

    # Group species by rarity
    by_rarity: Dict[RarityTier, List[Species]] = {rt: [] for rt in RarityTier}
    for sp in species_map.values():
        by_rarity[sp.rarity].append(sp)

    for nid, node in topology.nodes.items():
        slots = _SLOT_COUNTS.get(node.node_type,
                                  _SLOT_COUNTS[NodeType.PATH])
        is_early = nid in start_radius

        def _fill_slots(rarity: RarityTier, count: int) -> List[str]:
            if count <= 0:
                return []
            # Early-game protection
            if is_early:
                if rarity in (RarityTier.APEX, RarityTier.ANOMALY):
                    return []
                if rarity == RarityTier.RARE:
                    count = min(count, 1)

            candidates = by_rarity.get(rarity, [])
            # Filter by habitat distance
            eligible: List[Tuple[float, Species]] = []
            for sp in candidates:
                dist = sp.habitat.distance_to_biome(node.biome)
                if dist < _HABITAT_THRESHOLD:
                    # Weight: closer = higher
                    weight = max(0.01, 1.0 - dist / _HABITAT_THRESHOLD)
                    # Region bonus
                    if node.region in sp.biome_affinity_regions:
                        weight *= 1.5
                    eligible.append((weight, sp))

            if not eligible:
                # Fallback: relax threshold
                for sp in candidates:
                    eligible.append((0.1, sp))

            if not eligible:
                return []

            weights = [w for w, _ in eligible]
            pool = [sp for _, sp in eligible]
            chosen: List[str] = []
            for _ in range(min(count, len(pool))):
                picks = rng.choices(pool, weights=weights, k=1)
                chosen.append(picks[0].species_id)
            return chosen

        et = EncounterTable(
            node_id=nid,
            common_slots=_fill_slots(RarityTier.COMMON, slots["common"]),
            uncommon_slots=_fill_slots(RarityTier.UNCOMMON, slots["uncommon"]),
            rare_slots=_fill_slots(RarityTier.RARE, slots["rare"]),
            elite_slots=_fill_slots(RarityTier.ELITE, slots["elite"]),
        )

        if slots.get("apex", 0) > 0:
            apex_list = _fill_slots(RarityTier.APEX, 1)
            et.apex_slot = apex_list[0] if apex_list else None

        # Anomaly slot: only if node instability high enough
        if node.biome.instability_bias > 0.25 and not is_early:
            anom_list = _fill_slots(RarityTier.ANOMALY, 1)
            et.anomaly_slot = anom_list[0] if anom_list else None

        tables[nid] = et
        node.encounter_slots = {
            "common": et.common_slots,
            "uncommon": et.uncommon_slots,
            "rare": et.rare_slots,
        }

    return tables


def roll_encounter(table: EncounterTable, rng: SeededRNG) -> Optional[str]:
    """Roll an encounter from a node table. Returns species_id or None."""
    # Rarity roll
    roll = rng.random()
    if roll < 0.02 and table.anomaly_slot:
        return table.anomaly_slot
    elif roll < 0.05 and table.apex_slot:
        return table.apex_slot
    elif roll < 0.15 and table.elite_slots:
        return rng.choice(table.elite_slots)
    elif roll < 0.30 and table.rare_slots:
        return rng.choice(table.rare_slots)
    elif roll < 0.55 and table.uncommon_slots:
        return rng.choice(table.uncommon_slots)
    elif table.common_slots:
        return rng.choice(table.common_slots)
    return None


# ============================================================
# §7  BATTLE & LEAGUE SIMULATION
# ============================================================

class StatusEffect(Enum):
    FRACTURED   = auto()  # -Stability scaling each turn
    OVERCLOCKED = auto()  # +Force, -Stability
    ENTRENCHED  = auto()  # +Stability, -Reflex
    DISRUPTED   = auto()  # -Focus, misfire chance
    FLUXED      = auto()  # volatility in damage


@dataclass
class BattleCreature:
    """Snapshot of a creature in battle."""
    instance: CreatureInstance = field(default_factory=CreatureInstance)
    species: Species = field(default_factory=Species)
    effective: StatVector = field(default_factory=StatVector)
    current_hp: int = 0
    statuses: List[StatusEffect] = field(default_factory=list)
    tempo_debt: float = 0.0
    fainted: bool = False
    is_player_owned: bool = False   # team ownership tag for faint routing
    field_bonus: float = 0.0        # §7: accumulated field-effect multiplier
    move_log: List[str] = field(default_factory=list)  # §7: moves used this battle

    def init_from(self, instance: CreatureInstance, species: Species):
        self.instance = instance
        self.species = species
        self.effective = instance.effective_stats(species)
        self.current_hp = self.effective.vitality * 3  # HP pool
        self.statuses = []
        self.tempo_debt = 0.0
        self.fainted = False
        self.field_bonus = 0.0
        self.move_log = []


# ── Move tag parsing ───────────────────────────────────────

def _parse_move_tag(move_name: str) -> str:
    """Extract category tag from a move name string.

    Returns one of: 'D', 'S', 'F', 'P', or 'D' as default.
    Tags are encoded as ' [X]' suffix: e.g. 'Cinder Strike [D]'.
    """
    if " [" in move_name:
        tag = move_name.rsplit(" [", 1)[-1].rstrip("]").upper()
        return tag if tag in ("D", "S", "F", "P") else "D"
    return "D"


# ── Status application by attacker type ───────────────────

_TYPE_STATUS_MAP: Dict[NkType, StatusEffect] = {
    NkType.EMBER:    StatusEffect.OVERCLOCKED,   # fire → supercharge
    NkType.FROST:    StatusEffect.ENTRENCHED,    # ice → slow/fortify
    NkType.VOLT:     StatusEffect.OVERCLOCKED,   # electric → supercharge
    NkType.VENOM:    StatusEffect.FRACTURED,     # poison → stability decay
    NkType.SHADE:    StatusEffect.DISRUPTED,     # dark → focus drain
    NkType.RIFT:     StatusEffect.FLUXED,        # rift → variance
    NkType.ALLOY:    StatusEffect.ENTRENCHED,    # metal → anchor
    NkType.PULSE:    StatusEffect.DISRUPTED,     # pulse → interrupt
    NkType.GALE:     StatusEffect.FLUXED,        # wind → unpredictable
    NkType.STONE:    StatusEffect.ENTRENCHED,    # rock → fortify
    NkType.TIDE:     StatusEffect.FRACTURED,     # water → erode
    NkType.VERDANT:  StatusEffect.ENTRENCHED,    # plant → root
    NkType.RADIANT:  StatusEffect.OVERCLOCKED,   # light → energise
    NkType.THORN:    StatusEffect.FRACTURED,     # thorn → bleed-like
    NkType.BLOOM:    StatusEffect.FLUXED,        # bloom → wild variance
    NkType.TORRENT:  StatusEffect.FRACTURED,     # torrent → cascade erosion
    NkType.ECHO:     StatusEffect.DISRUPTED,     # echo → resonance distort
    NkType.DUNE:     StatusEffect.DISRUPTED,     # dune → sand in eyes
}


def _apply_status_move(atk: "BattleCreature", dfn: "BattleCreature"):
    """[S] move: apply attacker-type status to the defender (capped at 2 stacks)."""
    se = _TYPE_STATUS_MAP.get(atk.species.primary_type)
    if se and dfn.statuses.count(se) < 2:
        dfn.statuses.append(se)


def _apply_field_move(atk: "BattleCreature"):
    """[F] move: accumulate field bonus on the attacker for the next [D] move."""
    atk.field_bonus = min(atk.field_bonus + 0.25, 0.75)  # stacks up to +75%


def _apply_passive_move(atk: "BattleCreature"):
    """[P] move: self-buff - clear one negative status or gain OVERCLOCKED."""
    negatives = [StatusEffect.FRACTURED, StatusEffect.DISRUPTED, StatusEffect.FLUXED]
    for neg in negatives:
        if neg in atk.statuses:
            atk.statuses.remove(neg)
            return
    # No negatives to clear - gain OVERCLOCKED if not already present
    if StatusEffect.OVERCLOCKED not in atk.statuses:
        atk.statuses.append(StatusEffect.OVERCLOCKED)


def _select_move(bc: "BattleCreature", rng: SeededRNG) -> str:
    """Choose a move from the creature's move pool.

    Priority:
      1. If field_bonus is 0 and a [F] move is available - set up field first
         (only on turn 1 for each creature, i.e. move_log empty)
      2. Otherwise prefer [D] if OVERCLOCKED, [S] if opponent would be useful,
         fall back to random pick from pool.
    Deterministic via rng.
    """
    pool = bc.species.move_pool
    if not pool:
        return "Basic Strike [D]"

    # Categorise available moves
    by_tag: Dict[str, List[str]] = {"D": [], "S": [], "F": [], "P": []}
    for mv in pool:
        by_tag[_parse_move_tag(mv)].append(mv)

    # First move: try field setup if pool has [F] and no bonus yet
    if not bc.move_log and by_tag["F"] and bc.field_bonus == 0.0:
        return rng.choice(by_tag["F"])

    # OVERCLOCKED → prefer [D]
    if StatusEffect.OVERCLOCKED in bc.statuses and by_tag["D"]:
        return rng.choice(by_tag["D"])

    # Has [P] and carries a negative status → use passive to clear
    negatives = {StatusEffect.FRACTURED, StatusEffect.DISRUPTED, StatusEffect.FLUXED}
    if any(s in bc.statuses for s in negatives) and by_tag["P"]:
        return rng.choice(by_tag["P"])

    # Default: weighted pick - [D] 50%, [S] 20%, [F] 15%, [P] 15%
    weights = [50, 20, 15, 15]
    tags = ["D", "S", "F", "P"]
    available = [(t, by_tag[t]) for t, w in zip(tags, weights) if by_tag[t]]
    if not available:
        return rng.choice(pool)
    chosen_tag = rng.choices([t for t, _ in available],
                              weights=[w for _, pool_l in available
                                       for w in [weights[tags.index(_)]]]
                              if False else [50 if t == "D" else 20 if t == "S"
                                             else 15 for t, _ in available],
                              k=1)[0]
    return rng.choice(by_tag[chosen_tag])


@dataclass
class BattleResult:
    """Outcome of a single battle."""
    winner: str = ""       # "player" or "opponent"
    player_remaining: int = 0
    opponent_remaining: int = 0
    turns: int = 0
    fatigue_delta: float = 0.0
    moves_used: List[str] = field(default_factory=list)  # §7: move log


def simulate_battle(
    player_team: List[Tuple[CreatureInstance, Species]],
    opponent_team: List[Tuple[CreatureInstance, Species]],
    rng: SeededRNG,
) -> BattleResult:
    """
    Simulate a 3v3 turn-based battle.  Pure deterministic math.

    §7 - Move architecture is now wired in:
      • Each creature selects a move from its species.move_pool each turn.
      • Move tags route execution: [D] damage, [S] status apply, [F] field setup, [P] passive.
      • All 5 StatusEffects tick with real mechanical consequences:
          FRACTURED   - defender loses 2 stability per active stack each turn
          OVERCLOCKED - +30% force, costs 2 stability per stack per turn
          ENTRENCHED  - +20% stability, -15% reflex (reflected via tempo debt)
          DISRUPTED   - misfire chance 20% per stack; move has no effect on misfire
          FLUXED      - damage variance ±40% instead of ±10%
    """
    # ── Build battle snapshots ─────────────────────────────
    p_team: List[BattleCreature] = []
    for inst, sp in player_team[:3]:
        bc = BattleCreature()
        bc.init_from(inst, sp)
        bc.is_player_owned = True
        p_team.append(bc)

    o_team: List[BattleCreature] = []
    for inst, sp in opponent_team[:3]:
        bc = BattleCreature()
        bc.init_from(inst, sp)
        bc.is_player_owned = False
        o_team.append(bc)

    turns = 0
    max_turns = 100
    all_moves_used: List[str] = []

    p_active = 0
    o_active = 0

    # ── Damage resolver ────────────────────────────────────
    def _calc_damage(atk: BattleCreature, dfn: BattleCreature, move: str) -> int:
        base_power = 60 + rng.randint(-5, 5)
        stat_ratio = max(0.1, atk.effective.force / max(1, dfn.effective.stability))
        type_mult = type_multiplier(atk.species.primary_type,
                                    dfn.species.primary_type)
        if dfn.species.secondary_type:
            type_mult *= type_multiplier(atk.species.primary_type,
                                          dfn.species.secondary_type)
            type_mult = math.sqrt(type_mult)  # geometric mean normalisation

        # ── Status modifiers on attacker ───────────────────
        force_mod = 1.0
        if StatusEffect.OVERCLOCKED in atk.statuses:
            force_mod *= 1.3 * (1 + 0.1 * (atk.statuses.count(StatusEffect.OVERCLOCKED) - 1))
        if StatusEffect.DISRUPTED in atk.statuses:
            force_mod *= 0.8
        if StatusEffect.ENTRENCHED in atk.statuses:
            # ENTRENCHED attacker: lower force but more stable (no force modifier needed,
            # the stability gain on them is handled in tick; minor force penalty)
            force_mod *= 0.92

        # ── Field bonus ────────────────────────────────────
        field_mod = 1.0 + atk.field_bonus
        atk.field_bonus = 0.0  # consume after use

        # ── Damage variance: FLUXED → wider spread ─────────
        if StatusEffect.FLUXED in atk.statuses:
            variance = rng.uniform(0.6, 1.4)
        else:
            variance = rng.uniform(0.9, 1.1)

        damage = int(base_power * stat_ratio * type_mult * force_mod * field_mod * variance)
        return max(1, damage)

    # ── DISRUPTED misfire check ────────────────────────────
    def _misfires(bc: BattleCreature) -> bool:
        stacks = bc.statuses.count(StatusEffect.DISRUPTED)
        return stacks > 0 and rng.random() < 0.20 * stacks

    # ── Per-turn status tick ───────────────────────────────
    def _tick_statuses(bc: BattleCreature):
        """Apply per-turn status consequences and decay tempo debt."""
        # FRACTURED: erode stability (simulated as HP bleed)
        fractured_stacks = bc.statuses.count(StatusEffect.FRACTURED)
        if fractured_stacks:
            bleed = fractured_stacks * 3
            bc.current_hp = max(0, bc.current_hp - bleed)

        # OVERCLOCKED: burn stability → remove one stack after 3 turns' cost
        # (modelled as extra tempo debt accumulation)
        oc_stacks = bc.statuses.count(StatusEffect.OVERCLOCKED)
        if oc_stacks:
            bc.tempo_debt += 1.5 * oc_stacks

        # ENTRENCHED: slow (extra tempo debt)
        en_stacks = bc.statuses.count(StatusEffect.ENTRENCHED)
        if en_stacks:
            bc.tempo_debt += 1.0 * en_stacks

        # Decay tempo debt
        bc.tempo_debt = max(0.0, bc.tempo_debt - 1.0)

        # Status natural decay: each effect has a 15% chance to clear per stack per turn
        survived: List[StatusEffect] = []
        for se in bc.statuses:
            if rng.random() > 0.15:
                survived.append(se)
        bc.statuses = survived

    # ── Execute one creature's action ─────────────────────
    def _execute_action(atk: BattleCreature, dfn: BattleCreature):
        """Select move, route by tag, apply effects. Returns damage dealt (0 for non-[D])."""
        move = _select_move(atk, rng)
        atk.move_log.append(move)
        all_moves_used.append(move)
        tag = _parse_move_tag(move)

        # DISRUPTED misfire: action fizzles
        if _misfires(atk):
            all_moves_used[-1] = f"{move} [MISFIRE]"
            atk.tempo_debt += 2.0
            return 0

        if tag == "D":
            dmg = _calc_damage(atk, dfn, move)
            dfn.current_hp -= dmg
            atk.tempo_debt += 3.0
            return dmg
        elif tag == "S":
            _apply_status_move(atk, dfn)
            atk.tempo_debt += 2.0
            return 0
        elif tag == "F":
            _apply_field_move(atk)
            atk.tempo_debt += 1.5
            return 0
        elif tag == "P":
            _apply_passive_move(atk)
            atk.tempo_debt += 1.0
            return 0
        return 0

    # ── Main battle loop ───────────────────────────────────
    while turns < max_turns:
        turns += 1

        if p_active >= len(p_team) or o_active >= len(o_team):
            break

        p_cur = p_team[p_active]
        o_cur = o_team[o_active]

        if p_cur.fainted:
            p_active += 1
            continue
        if o_cur.fainted:
            o_active += 1
            continue

        # Initiative: reflex minus tempo debt ± jitter
        p_init = p_cur.effective.reflex - p_cur.tempo_debt + rng.uniform(-5, 5)
        o_init = o_cur.effective.reflex - o_cur.tempo_debt + rng.uniform(-5, 5)
        first, second = (p_cur, o_cur) if p_init >= o_init else (o_cur, p_cur)

        # First creature acts
        _execute_action(first, second)
        if second.current_hp <= 0:
            second.fainted = True
            if second.is_player_owned:
                p_active += 1
            else:
                o_active += 1
            _tick_statuses(first)
            continue

        # Second creature acts (if still standing)
        _execute_action(second, first)
        if first.current_hp <= 0:
            first.fainted = True
            if first.is_player_owned:
                p_active += 1
            else:
                o_active += 1

        # End-of-turn status tick for both
        _tick_statuses(first)
        _tick_statuses(second)

        # Faint check after status bleed (FRACTURED)
        for bc in (first, second):
            if not bc.fainted and bc.current_hp <= 0:
                bc.fainted = True
                if bc.is_player_owned:
                    p_active += 1
                else:
                    o_active += 1

    p_alive = sum(1 for c in p_team if not c.fainted)
    o_alive = sum(1 for c in o_team if not c.fainted)

    winner = "player" if p_alive > o_alive else "opponent" if o_alive > p_alive else "draw"
    fatigue = turns * 0.5

    return BattleResult(
        winner=winner,
        player_remaining=p_alive,
        opponent_remaining=o_alive,
        turns=turns,
        fatigue_delta=fatigue,
        moves_used=all_moves_used,
    )


# ── League system ──────────────────────────────────────────

class LeagueTier(Enum):
    LOCAL     = 1
    REGIONAL  = 2
    ISLAND    = 3
    APEX_INV  = 4


@dataclass
class Trainer:
    """An AI or player trainer entity."""
    trainer_id: str = ""
    name: str = ""
    is_player: bool = False
    rating: float = 1200.0   # ELO-like
    tier: LeagueTier = LeagueTier.LOCAL
    team_species_ids: List[str] = field(default_factory=list)
    risk_profile: float = 0.5    # 0 = conservative, 1 = aggressive
    ideology_vector: Dict[str, float] = field(default_factory=dict)
    wins: int = 0
    losses: int = 0


@dataclass
class LeagueState:
    """Island-wide league simulation state."""
    trainers: Dict[str, Trainer] = field(default_factory=dict)
    tournament_history: List[Dict[str, Any]] = field(default_factory=list)
    meta_health: Dict[str, float] = field(default_factory=dict)

    def update_rating(self, winner_id: str, loser_id: str, k: float = 32.0):
        """ELO-like rating update. Player rating is floored at 400."""
        w = self.trainers.get(winner_id)
        l = self.trainers.get(loser_id)
        if not w or not l:
            return
        expected_w = 1.0 / (1.0 + 10 ** ((l.rating - w.rating) / 400.0))
        expected_l = 1.0 - expected_w
        w.rating += k * (1.0 - expected_w)
        l.rating += k * (0.0 - expected_l)
        # Floor player rating at 400 — prevents permanent death spiral where
        # player rating collapses to near-zero, making all trainer fights
        # mathematically unwinnable.
        player_trainer = self.trainers.get("player")
        if player_trainer and player_trainer.rating < 400.0:
            player_trainer.rating = 400.0
        w.wins += 1
        l.losses += 1


def generate_ai_trainers(topology: IslandTopology,
                         species_map: Dict[str, Species],
                         count: int = 50) -> Dict[str, Trainer]:
    """Generate deterministic AI trainers for the island league."""
    rng = SeededRNG(topology.seed).fork("trainers")
    trainers: Dict[str, Trainer] = {}

    species_ids = list(species_map.keys())

    for i in range(count):
        tid = f"trainer_{i:04d}"
        name = _generate_name(rng.fork(f"tn_{i}"), min_syl=2, max_syl=3)

        # Team of 3-6
        team_size = rng.randint(3, 6)
        team = rng.sample(species_ids, min(team_size, len(species_ids)))

        # Rating distribution: gaussian around 1200, std 300
        rating = max(800, rng.gauss(1200, 300))

        tier = (LeagueTier.LOCAL if rating < 1300
                else LeagueTier.REGIONAL if rating < 1600
                else LeagueTier.ISLAND if rating < 1900
                else LeagueTier.APEX_INV)

        trainers[tid] = Trainer(
            trainer_id=tid, name=name, rating=rating, tier=tier,
            team_species_ids=team,
            risk_profile=rng.uniform(0.1, 0.9),
            ideology_vector={
                "competition": rng.uniform(-1, 1),
                "preservation": rng.uniform(-1, 1),
                "research_priority": rng.uniform(-1, 1),
            },
        )

    return trainers


# ============================================================
# §8  GENETIC BREEDING & EVOLUTIONARY DRIFT
# ============================================================

def breed_creatures(
    parent_a: CreatureInstance,
    parent_b: CreatureInstance,
    species_a: Species,
    species_b: Species,
    rng: SeededRNG,
    anomaly_instability: float = 0.0,
) -> GeneticProfile:
    """
    Compute offspring GeneticProfile from two parents.

    Inheritance: 40/40/20 rule with cluster suppression.
    """
    genes_a = parent_a.genes.stat_genes
    genes_b = parent_b.genes.stat_genes

    offspring_genes: List[int] = []
    mutation_band = 2 + int(anomaly_instability * 3)  # wider under instability

    for i in range(6):
        # 40% from A, 40% from B, 20% mutation
        if rng.random() < 0.4:
            base = genes_a[i]
        elif rng.random() < 0.67:  # 0.4 / (0.4+0.2) adjusted
            base = genes_b[i]
        else:
            base = (genes_a[i] + genes_b[i]) // 2
        # Mutation
        mutation = rng.randint(-mutation_band, mutation_band)
        gene = max(0, min(31, base + mutation))
        offspring_genes.append(gene)

    # Cluster suppression: if any cluster > 50, suppress weakest stat in cluster
    profile = GeneticProfile(
        stat_genes=offspring_genes,
        variance_seed=rng.randint(0, 2**31),
        lineage_depth=max(parent_a.genes.lineage_depth,
                          parent_b.genes.lineage_depth) + 1,
    )

    clusters = [
        (profile.physical_cluster, [0, 1]),   # vitality, force
        (profile.tempo_cluster, [2, 5]),       # reflex, flux
        (profile.cognitive_cluster, [3, 4]),   # focus, stability
    ]
    for total, indices in clusters:
        if total > 50:
            # Suppress weakest
            weakest_idx = min(indices, key=lambda idx: offspring_genes[idx])
            suppress = min(offspring_genes[weakest_idx], (total - 50) // 2)
            offspring_genes[weakest_idx] = max(0, offspring_genes[weakest_idx] - suppress)

    # Trait inheritance
    traits: List[str] = []
    if parent_a.genes.trait_genes:
        traits.append(rng.choice(parent_a.genes.trait_genes))
    if parent_b.genes.trait_genes:
        traits.append(rng.choice(parent_b.genes.trait_genes))
    # Rare trait unlock at lineage depth threshold
    if profile.lineage_depth >= 5 and rng.random() < 0.15:
        traits.append(rng.choice(_PASSIVE_TRAITS))
    profile.trait_genes = traits[:3]  # cap at 3

    return profile


@dataclass
class PopulationGenePool:
    """Tracks genetic diversity per species across the island."""
    species_id: str = ""
    avg_stat_genes: List[float] = field(default_factory=lambda: [16.0] * 6)
    trait_frequency: Dict[str, float] = field(default_factory=dict)
    diversity_variance: float = 1.0
    population_count: int = 100

    def update_from_breeding(self, offspring: GeneticProfile):
        """Shift pool statistics toward new offspring."""
        alpha = 0.01  # slow drift
        for i in range(6):
            self.avg_stat_genes[i] = (
                self.avg_stat_genes[i] * (1 - alpha)
                + offspring.stat_genes[i] * alpha
            )
        # Diversity: measure variance of avg genes
        mean = sum(self.avg_stat_genes) / 6
        var = sum((g - mean) ** 2 for g in self.avg_stat_genes) / 6
        self.diversity_variance = max(0.1, var)


# ============================================================
# §9  FACTION TERRITORIAL INFLUENCE & DIALOGUE WEIGHTING
# ============================================================

class FactionArchetype(Enum):
    LEAGUE_AUTHORITY      = auto()
    RESEARCH_CONSORTIUM   = auto()
    PRESERVATION_CIRCLE   = auto()
    INDUSTRIAL_SYNDICATE  = auto()
    FRONTIER_SETTLERS     = auto()
    DEPTH_SECT            = auto()


@dataclass
class IdeologyVector:
    """Multi-axis ideology descriptor."""
    competition:       float = 0.0
    preservation:      float = 0.0
    industrialization:  float = 0.0
    research_priority: float = 0.0
    anomaly_curiosity: float = 0.0

    def distance(self, other: "IdeologyVector") -> float:
        return math.sqrt(
            (self.competition - other.competition) ** 2
            + (self.preservation - other.preservation) ** 2
            + (self.industrialization - other.industrialization) ** 2
            + (self.research_priority - other.research_priority) ** 2
            + (self.anomaly_curiosity - other.anomaly_curiosity) ** 2
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "competition": self.competition,
            "preservation": self.preservation,
            "industrialization": self.industrialization,
            "research_priority": self.research_priority,
            "anomaly_curiosity": self.anomaly_curiosity,
        }


# Baseline ideology per faction archetype
_FACTION_IDEOLOGIES: Dict[FactionArchetype, IdeologyVector] = {
    FactionArchetype.LEAGUE_AUTHORITY:    IdeologyVector(0.8, -0.2, 0.2, 0.0, -0.3),
    FactionArchetype.RESEARCH_CONSORTIUM: IdeologyVector(0.0, 0.3, 0.1, 0.9, 0.4),
    FactionArchetype.PRESERVATION_CIRCLE: IdeologyVector(-0.3, 0.9, -0.5, 0.2, 0.0),
    FactionArchetype.INDUSTRIAL_SYNDICATE:IdeologyVector(0.2, -0.5, 0.9, 0.1, -0.2),
    FactionArchetype.FRONTIER_SETTLERS:   IdeologyVector(0.1, 0.1, 0.3, -0.1, 0.1),
    FactionArchetype.DEPTH_SECT:          IdeologyVector(-0.1, 0.0, -0.2, 0.5, 0.9),
}


@dataclass
class Faction:
    """A political faction on the island."""
    faction_id: str = ""
    archetype: FactionArchetype = FactionArchetype.LEAGUE_AUTHORITY
    name: str = ""
    influence_score: float = 50.0
    ideology: IdeologyVector = field(default_factory=IdeologyVector)
    territorial_nodes: Set[str] = field(default_factory=set)
    expansion_rate: float = 1.0
    stability_val: float = 0.8
    public_sentiment: float = 0.5


@dataclass
class DialogueDelta:
    """Effect of a dialogue choice on ideology axes."""
    competition:       float = 0.0
    preservation:      float = 0.0
    industrialization:  float = 0.0
    research_priority: float = 0.0
    anomaly_curiosity: float = 0.0


def generate_factions(topology: IslandTopology) -> Dict[str, Faction]:
    """Generate 4-6 factions for the island."""
    rng = SeededRNG(topology.seed).fork("factions")
    archetypes = list(FactionArchetype)
    n_factions = rng.randint(4, 6)
    rng.shuffle(archetypes)
    selected = archetypes[:n_factions]

    factions: Dict[str, Faction] = {}
    node_ids = list(topology.nodes.keys())

    for i, arch in enumerate(selected):
        fid = f"faction_{i:02d}"
        base_ideology = _FACTION_IDEOLOGIES[arch]
        # Perturb slightly per seed
        ideology = IdeologyVector(
            competition=max(-1, min(1, base_ideology.competition + rng.gauss(0, 0.1))),
            preservation=max(-1, min(1, base_ideology.preservation + rng.gauss(0, 0.1))),
            industrialization=max(-1, min(1, base_ideology.industrialization + rng.gauss(0, 0.1))),
            research_priority=max(-1, min(1, base_ideology.research_priority + rng.gauss(0, 0.1))),
            anomaly_curiosity=max(-1, min(1, base_ideology.anomaly_curiosity + rng.gauss(0, 0.1))),
        )

        # Initial territory: 10-30 random nodes
        n_terr = rng.randint(10, min(30, len(node_ids)))
        territory = set(rng.sample(node_ids, n_terr))

        name = _generate_name(rng.fork(f"fname_{i}"), prefix=arch.name.replace("_", " ").title())

        factions[fid] = Faction(
            faction_id=fid,
            archetype=arch,
            name=name,
            influence_score=rng.uniform(30, 70),
            ideology=ideology,
            territorial_nodes=territory,
            expansion_rate=rng.uniform(0.5, 1.5),
            stability_val=rng.uniform(0.5, 1.0),
            public_sentiment=rng.uniform(0.3, 0.7),
        )

        # Set initial node influence
        for nid in territory:
            if nid in topology.nodes:
                topology.nodes[nid].faction_influence[fid] = rng.uniform(0.3, 0.8)

    return factions


def diffuse_faction_influence(
    topology: IslandTopology,
    factions: Dict[str, Faction],
    diffusion_factor: float = 0.15,
):
    """
    One tick of territorial influence diffusion across the graph.

    Cities amplify, wild zones dampen, facilities anchor.
    """
    new_influence: Dict[str, Dict[str, float]] = {}

    for nid, node in topology.nodes.items():
        new_influence[nid] = {}
        neighbors = [topology.nodes[nb] for nb in node.neighbors
                     if nb in topology.nodes]

        for fid, faction in factions.items():
            local = node.faction_influence.get(fid, 0.0)

            # Average adjacent influence
            if neighbors:
                avg_adj = sum(nb.faction_influence.get(fid, 0.0)
                              for nb in neighbors) / len(neighbors)
            else:
                avg_adj = 0.0

            # Node type modifiers
            amp = 1.0
            if node.node_type == NodeType.CITY:
                amp = 1.3
            elif node.node_type == NodeType.WILD_ZONE:
                amp = 0.6
            elif node.node_type == NodeType.FACILITY:
                amp = 1.2
            elif node.node_type == NodeType.DUNGEON:
                amp = 0.4

            # Opposition pressure
            opposition = sum(node.faction_influence.get(ofid, 0.0)
                            for ofid in factions if ofid != fid)

            new_val = (local
                       + avg_adj * diffusion_factor * amp
                       - opposition * 0.05)
            new_influence[nid][fid] = max(0.0, min(1.0, new_val))

    # Apply
    for nid in topology.nodes:
        topology.nodes[nid].faction_influence = new_influence.get(nid, {})

    # Sync Faction objects: recompute territorial_nodes and influence_score
    for fid, faction in factions.items():
        terr: Set[str] = set()
        total_inf = 0.0
        for nid, node in topology.nodes.items():
            inf = node.faction_influence.get(fid, 0.0)
            if inf > 0.3:          # threshold for "controlled" territory
                terr.add(nid)
            total_inf += inf
        faction.territorial_nodes = terr
        faction.influence_score = total_inf / max(1, len(topology.nodes)) * 100.0


def compute_dialogue_impact(
    delta: DialogueDelta,
    player_credibility: float,
    faction_standings: Dict[str, float],
) -> Dict[str, float]:
    """
    Weight a dialogue delta by player credibility.

    Credibility = f(achievements, faction standing, league rating, milestones).
    Higher credibility → stronger impact.
    """
    mult = max(0.1, min(3.0, player_credibility))
    return {
        "competition": delta.competition * mult,
        "preservation": delta.preservation * mult,
        "industrialization": delta.industrialization * mult,
        "research_priority": delta.research_priority * mult,
        "anomaly_curiosity": delta.anomaly_curiosity * mult,
    }


# ============================================================
# §10  ISLAND LEDGER, NORMALIZATION, 100 OUTCOME BANDS
# ============================================================

@dataclass
class IslandLedger:
    """
    Hidden macro-state tracker.  All axes in [-100, +100].
    """
    ecological_balance:   float = 0.0
    urbanization_level:   float = 0.0
    league_influence:     float = 0.0
    research_advancement: float = 0.0
    genetic_diversity:    float = 0.0
    population_pressure:  float = 0.0
    anomaly_stability:    float = 0.0
    cultural_cohesion:    float = 0.0

    # Seed baseline bias (for normalization)
    _baseline: Dict[str, float] = field(default_factory=dict)

    def set_baseline(self, seed: int):
        """Store seed-dependent baseline for fair normalization."""
        rng = SeededRNG(seed).fork("ledger_baseline")
        self._baseline = {
            "ecological_balance": rng.uniform(-15, 15),
            "urbanization_level": rng.uniform(-10, 20),
            "league_influence": rng.uniform(-10, 10),
            "research_advancement": rng.uniform(-5, 10),
            "genetic_diversity": rng.uniform(0, 15),
            "population_pressure": rng.uniform(-10, 10),
            "anomaly_stability": rng.uniform(-5, 5),
            "cultural_cohesion": rng.uniform(-5, 15),
        }

    def normalize(self) -> Dict[str, float]:
        """Normalize raw values against seed baseline."""
        def _norm(raw: float, key: str) -> float:
            bias = self._baseline.get(key, 0.0)
            scaling = 50.0  # normalization denominator
            return max(-100, min(100, (raw - bias) / scaling * 100))
        return {
            "ecological_balance": _norm(self.ecological_balance, "ecological_balance"),
            "urbanization_level": _norm(self.urbanization_level, "urbanization_level"),
            "league_influence": _norm(self.league_influence, "league_influence"),
            "research_advancement": _norm(self.research_advancement, "research_advancement"),
            "genetic_diversity": _norm(self.genetic_diversity, "genetic_diversity"),
            "population_pressure": _norm(self.population_pressure, "population_pressure"),
            "anomaly_stability": _norm(self.anomaly_stability, "anomaly_stability"),
            "cultural_cohesion": _norm(self.cultural_cohesion, "cultural_cohesion"),
        }

    # Derived indices
    def stability_index(self) -> float:
        n = self.normalize()
        return (n["ecological_balance"] + n["genetic_diversity"]
                + n["anomaly_stability"]) / 3.0

    def civilization_index(self) -> float:
        n = self.normalize()
        return (n["urbanization_level"] + n["league_influence"]
                + n["research_advancement"]) / 3.0

    def tension_index(self) -> float:
        """Higher value = more tension / instability on the island."""
        n = self.normalize()
        # Tension rises when ecology and urbanization diverge,
        # when cultural cohesion is low, and when anomaly stability is low.
        eco_urb_gap = abs(n["ecological_balance"] - n["urbanization_level"])
        culture_stress = max(0, -n["cultural_cohesion"])   # negative cohesion → tension
        anomaly_stress = max(0, -n["anomaly_stability"])   # negative stability → tension
        return (eco_urb_gap + culture_stress + anomaly_stress) / 3.0

    def apply_delta(self, axis: str, delta: float):
        """Shift a ledger axis by delta, clamping to [-100, 100]."""
        current = getattr(self, axis, 0.0)
        setattr(self, axis, max(-100, min(100, current + delta)))

    def to_dict(self) -> dict:
        return {
            "raw": {
                "ecological_balance": self.ecological_balance,
                "urbanization_level": self.urbanization_level,
                "league_influence": self.league_influence,
                "research_advancement": self.research_advancement,
                "genetic_diversity": self.genetic_diversity,
                "population_pressure": self.population_pressure,
                "anomaly_stability": self.anomaly_stability,
                "cultural_cohesion": self.cultural_cohesion,
            },
            "normalized": self.normalize(),
            "stability_index": self.stability_index(),
            "civilization_index": self.civilization_index(),
            "tension_index": self.tension_index(),
        }


# ── Outcome bands ──────────────────────────────────────────

_ISLAND_QUADRANT_NAMES = [
    "Pristine Harmony",
    "Strained Ecology",
    "Industrial Surge",
    "Research Ascendant",
    "League Dominated",
    "Balanced Growth",
    "Anomaly Destabilized",
    "Cultural Fracture",
    "Genetic Bottleneck",
    "Frontier Expansion",
]

_PERSONAL_ARCHETYPE_NAMES = [
    "Island Champion",
    "Grand Research Architect",
    "Wanderer of the Wild Core",
    "Genetic Visionary",
    "Relic Seeker",
    "Stabilizer of the Fracture",
    "Instigator of Collapse",
    "League Reformer",
    "Isolationist Guardian",
    "Rift Walker",
]


def compute_island_quadrant(ledger: IslandLedger) -> int:
    """
    Compute island quadrant (0-9) from ledger axes using a rule-based
    argmax classifier.  Each quadrant maps to a named condition; the
    classifier picks the condition whose indicator is strongest.

    Quadrants:
      0 Pristine Harmony       - high stability, high ecology, low tension
      1 Strained Ecology       - low ecology, moderate urbanization
      2 Industrial Surge       - high urbanization, low ecology
      3 Research Ascendant     - research dominates
      4 League Dominated       - league influence dominates
      5 Balanced Growth        - no axis dominates (low variance)
      6 Anomaly Destabilized   - anomaly_stability deeply negative
      7 Cultural Fracture      - cultural_cohesion deeply negative
      8 Genetic Bottleneck     - genetic_diversity deeply negative
      9 Frontier Expansion     - high exploration-style pop pressure, moderate urb
    """
    n = ledger.normalize()
    eco = n["ecological_balance"]
    urb = n["urbanization_level"]
    lea = n["league_influence"]
    res = n["research_advancement"]
    gen = n["genetic_diversity"]
    pop = n["population_pressure"]
    ano = n["anomaly_stability"]
    cul = n["cultural_cohesion"]

    si = ledger.stability_index()

    # Score each quadrant - highest score wins
    scores: List[Tuple[float, int]] = [
        (si + eco * 0.3 - abs(urb) * 0.1,                    0),  # Pristine Harmony
        (-eco * 0.6 + urb * 0.2 - gen * 0.2,                 1),  # Strained Ecology
        (urb * 0.6 - eco * 0.3 + pop * 0.1,                  2),  # Industrial Surge
        (res * 0.8,                                            3),  # Research Ascendant
        (lea * 0.8,                                            4),  # League Dominated
        (-abs(eco) - abs(urb) - abs(lea) - abs(res) + 200,   5),  # Balanced Growth (low variance bonus)
        (-ano * 0.8,                                           6),  # Anomaly Destabilized
        (-cul * 0.8,                                           7),  # Cultural Fracture
        (-gen * 0.8,                                           8),  # Genetic Bottleneck
        (pop * 0.4 + urb * 0.3 + eco * 0.1,                  9),  # Frontier Expansion
    ]

    best_score, best_q = max(scores, key=lambda t: t[0])
    return best_q


def compute_personal_quadrant(trajectory: "PlayerTrajectory") -> int:
    """Compute personal archetype quadrant (0-9)."""
    scores = [
        trajectory.competitive_focus,
        trajectory.exploration_depth,
        trajectory.research_investment,
        trajectory.breeding_intensity,
        trajectory.anomaly_exposure,
    ]
    # Dominant axis determines base quadrant
    max_score = max(scores)
    max_idx = scores.index(max_score)

    # Map 5 primary axes to 10 quadrants with sub-splits
    quadrant_map = {
        0: [0, 7],   # competitive → Champion or Reformer
        1: [2, 4],   # exploration → Wanderer or Relic Seeker
        2: [1, 3],   # research → Architect or Visionary
        3: [3, 8],   # breeding → Visionary or Guardian
        4: [5, 9],   # anomaly → Stabilizer or Rift Walker
    }

    options = quadrant_map.get(max_idx, [0, 5])
    # Sub-split by risk appetite
    if trajectory.risk_appetite > 50:
        return options[1]
    return options[0]


def compute_outcome_band(ledger: IslandLedger,
                         trajectory: "PlayerTrajectory") -> int:
    """Compute final outcome band ID (0-99)."""
    iq = compute_island_quadrant(ledger)
    pq = compute_personal_quadrant(trajectory)
    return iq * 10 + pq


def describe_outcome_band(band_id: int) -> Dict[str, str]:
    """Human-readable description of an outcome band."""
    iq = band_id // 10
    pq = band_id % 10
    return {
        "band_id": band_id,
        "island_quadrant": iq,
        "island_condition": _ISLAND_QUADRANT_NAMES[iq],
        "personal_quadrant": pq,
        "personal_archetype": _PERSONAL_ARCHETYPE_NAMES[pq],
        "narrative_role": NARRATIVE_OUTCOME_ROLES.get((iq, pq), "The Unnamed"),
        "summary": (
            f"Island State: {_ISLAND_QUADRANT_NAMES[iq]}. "
            f"Personal Trajectory: {_PERSONAL_ARCHETYPE_NAMES[pq]}."
        ),
    }


# ============================================================
# §11  GATE REQUIREMENT COMPUTATION
# ============================================================

def compute_gate_thresholds(
    topology: IslandTopology,
    ledger: IslandLedger,
    factions: Dict[str, Faction],
):
    """
    Dynamically adjust gate thresholds based on ledger state and faction
    territorial control.  Called each simulation tick.
    """
    n = ledger.normalize()

    for nid, node in topology.nodes.items():
        if not node.gate:
            continue

        gate = node.gate
        # Dynamic adjustment based on ledger — reset each tick, then recompute
        base_flex = 0.0
        if gate.gate_type == GateType.LEAGUE:
            # High league influence → easier league gates
            base_flex = max(0, n["league_influence"] * 0.15)
        elif gate.gate_type == GateType.ECOLOGICAL:
            base_flex = max(0, n["ecological_balance"] * 0.15)
        elif gate.gate_type == GateType.RESEARCH:
            base_flex = max(0, n["research_advancement"] * 0.15)
        elif gate.gate_type == GateType.ECONOMIC:
            base_flex = max(0, n["urbanization_level"] * 0.1)
        elif gate.gate_type == GateType.ANOMALY:
            # Anomaly gates can open temporarily when stability is deeply negative
            if n["anomaly_stability"] < -30:
                base_flex = abs(n["anomaly_stability"]) * 0.2

        # Faction control: dominant faction in this area may shift threshold
        # (capped at 10.0 to prevent runaway accumulation over many ticks)
        dominant_fid = max(node.faction_influence,
                          key=node.faction_influence.get,
                          default=None) if node.faction_influence else None
        faction_flex = 0.0
        if dominant_fid and dominant_fid in factions:
            dom_influence = node.faction_influence[dominant_fid]
            if dom_influence > 0.6:
                # Strong faction presence: aligned players get easier access
                faction_flex = min(10.0, dom_influence * 5.0)

        gate.flex_buffer = base_flex + faction_flex


# ============================================================
# §12  PLAYER TRAJECTORY & PERSONAL OUTCOME
# ============================================================

@dataclass
class PlayerTrajectory:
    """
    Accumulated player behavioral vector.
    All scores in [0, 100].
    """
    competitive_focus:    float = 0.0
    exploration_depth:    float = 0.0
    research_investment:  float = 0.0
    breeding_intensity:   float = 0.0
    anomaly_exposure:     float = 0.0
    risk_appetite:        float = 50.0
    dialogue_ideology:    IdeologyVector = field(default_factory=IdeologyVector)

    # Tracking counters
    battles_won: int = 0
    battles_lost: int = 0
    nodes_explored: int = 0
    species_discovered: int = 0
    breeds_completed: int = 0
    relics_found: int = 0
    anomaly_events: int = 0
    creatures_captured: int = 0

    def update_from_battle(self, won: bool):
        if won:
            self.battles_won += 1
        else:
            self.battles_lost += 1
        # Recalculate competitive focus
        total_battles = self.battles_won + self.battles_lost
        if total_battles > 0:
            self.competitive_focus = min(100, total_battles * 1.5)

    def update_from_exploration(self, node: MapNode):
        self.nodes_explored += 1
        self.exploration_depth = min(100, self.nodes_explored * 0.8)
        if node.node_type == NodeType.LANDMARK:
            self.relics_found += 1

    def update_from_breeding(self):
        self.breeds_completed += 1
        self.breeding_intensity = min(100, self.breeds_completed * 3.0)

    def update_from_research(self, delta: float = 5.0):
        self.research_investment = min(100, self.research_investment + delta)

    def update_from_anomaly(self):
        self.anomaly_events += 1
        self.anomaly_exposure = min(100, self.anomaly_events * 8.0)

    def update_from_capture(self):
        self.creatures_captured += 1
        # Each capture nudges competitive focus and exploration slightly
        self.competitive_focus = min(100, self.competitive_focus + 0.8)
        self.exploration_depth = min(100, self.exploration_depth + 0.3)

    def update_dialogue(self, delta: DialogueDelta, credibility: float = 1.0):
        iv = self.dialogue_ideology
        mult = max(0.1, min(3.0, credibility))
        iv.competition = max(-1, min(1, iv.competition + delta.competition * mult))
        iv.preservation = max(-1, min(1, iv.preservation + delta.preservation * mult))
        iv.industrialization = max(-1, min(1, iv.industrialization + delta.industrialization * mult))
        iv.research_priority = max(-1, min(1, iv.research_priority + delta.research_priority * mult))
        iv.anomaly_curiosity = max(-1, min(1, iv.anomaly_curiosity + delta.anomaly_curiosity * mult))

    def dominant_archetype(self) -> str:
        """Return the player's dominant personal archetype name."""
        pq = compute_personal_quadrant(self)
        return _PERSONAL_ARCHETYPE_NAMES[pq]

    def to_dict(self) -> dict:
        return {
            "competitive_focus": self.competitive_focus,
            "exploration_depth": self.exploration_depth,
            "research_investment": self.research_investment,
            "breeding_intensity": self.breeding_intensity,
            "anomaly_exposure": self.anomaly_exposure,
            "risk_appetite": self.risk_appetite,
            "battles_won": self.battles_won,
            "battles_lost": self.battles_lost,
            "nodes_explored": self.nodes_explored,
            "species_discovered": self.species_discovered,
            "breeds_completed": self.breeds_completed,
            "relics_found": self.relics_found,
            "anomaly_events": self.anomaly_events,
            "creatures_captured": self.creatures_captured,
            "dominant_archetype": self.dominant_archetype(),
            "ideology": self.dialogue_ideology.to_dict(),
        }


# ============================================================
# §17  PROJECT HUNDRED CANON  (A1)
# ============================================================
#
# Base truth - these facts never vary across seeds.
# What varies is *interpretation* (see resolve_founder_framing).

PROJECT_HUNDRED: str = "PROJECT HUNDRED"
THE_CARTOGRAPHERS: str = "THE CARTOGRAPHERS"


@dataclass
class FounderRecord:
    """A named architect of PROJECT HUNDRED."""
    name:        str
    role:        str        # public title
    thesis:      str        # their core belief
    fate_note:   str        # canonical fate (always true)


# ── Fixed base canon ────────────────────────────────────────
FOUNDER_CANON: Dict[str, FounderRecord] = {
    "voss": FounderRecord(
        name="Dr. Elian Voss",
        role="Systems Architect",
        thesis="Fragmentation preserves adaptability. A single civilization always centralizes and fails.",
        fate_note="Authorized the final launch sequence. Believed the experiment would eventually end.",
    ),
    "ilyanova": FounderRecord(
        name="Dr. Mara Ilyanova",
        role="Bioengineer - Neiko Genome Design",
        thesis="Engineered organisms must carry the capacity for genuine autonomy.",
        fate_note="Discovered PROJECT HUNDRED was permanent isolation, not temporary containment. Attempted to leak internal data.",
    ),
    "kincaid": FounderRecord(
        name="Director Hale Kincaid",
        role="Funding Authority - Military Liaison",
        thesis="Stability requires control. The experiment is the only viable path.",
        fate_note="Suppressed Ilyanova's leak. Ensured launch proceeded.",
    ),
}

# ── Per-seed interpretation variants ────────────────────────
#
# Each variant assigns:
#   traitor   - who is culturally framed as having betrayed the project
#   martyr    - who is framed as sacrificing for truth
#   protector - who is framed as having held things together
#
# The base facts do not change. Interpretation does.

_FOUNDER_FRAMING_VARIANTS: List[Dict[str, str]] = [
    # Variant 0 - Most common / closest to objective truth
    {"traitor": "kincaid",   "martyr": "ilyanova", "protector": "voss"},
    # Variant 1 - Kincaid apologist framing
    {"traitor": "ilyanova",  "martyr": "voss",     "protector": "kincaid"},
    # Variant 2 - Voss blamed for authorizing
    {"traitor": "voss",      "martyr": "ilyanova", "protector": "kincaid"},
    # Variant 3 - All three complicit, no clear martyr
    {"traitor": "voss",      "martyr": "kincaid",  "protector": "ilyanova"},
    # Variant 4 - Ilyanova rewritten as saboteur
    {"traitor": "ilyanova",  "martyr": "kincaid",  "protector": "voss"},
]


def resolve_founder_framing(seed: int) -> Dict[str, str]:
    """
    Deterministically select the founder interpretation framing for this island.
    Returns dict with keys: traitor, martyr, protector - each a founder key.
    """
    rng = SeededRNG(seed).fork("founder_framing")
    idx = rng.randint(0, len(_FOUNDER_FRAMING_VARIANTS) - 1)
    return _FOUNDER_FRAMING_VARIANTS[idx]


# ============================================================
# §18  BEHAVIORAL AXIS  (A2)
# ============================================================

class BehavioralAxis(Enum):
    """
    Player behavioral classification - computed, never chosen.
    Derived from relative strength of trajectory axes.
    """
    DOMINANT      = "dominant"       # high competitive_focus, high risk_appetite
    CURIOUS       = "curious"        # high exploration_depth + anomaly_exposure
    STABILIZING   = "stabilizing"    # high research + breeding, low anomaly
    EXPLOITATIVE  = "exploitative"   # high breeding + competitive, low exploration


def compute_behavioral_axis(trajectory: "PlayerTrajectory") -> BehavioralAxis:
    """
    Derive the player's dominant behavioral axis from trajectory scores.
    Pure computation - no mutation.
    """
    pt = trajectory
    dominant_score = (
        pt.competitive_focus * 0.5
        + pt.risk_appetite * 0.3
        - pt.exploration_depth * 0.1
    )
    curious_score = (
        pt.exploration_depth * 0.45
        + pt.anomaly_exposure * 0.45
        - pt.competitive_focus * 0.1
    )
    stabilizing_score = (
        pt.research_investment * 0.4
        + pt.breeding_intensity * 0.3
        - pt.anomaly_exposure * 0.2
        + (100.0 - pt.risk_appetite) * 0.1
    )
    exploitative_score = (
        pt.breeding_intensity * 0.4
        + pt.competitive_focus * 0.3
        - pt.exploration_depth * 0.1
        - pt.research_investment * 0.1
    )
    best = max(
        (dominant_score,     BehavioralAxis.DOMINANT),
        (curious_score,      BehavioralAxis.CURIOUS),
        (stabilizing_score,  BehavioralAxis.STABILIZING),
        (exploitative_score, BehavioralAxis.EXPLOITATIVE),
        key=lambda t: t[0],
    )
    return best[1]


# ============================================================
# §19  CONTAINMENT TIER SYSTEM  (A3)
# ============================================================

class ContainmentTier(Enum):
    """
    The five-tier containment architecture of PROJECT HUNDRED.
    Tier is never shown to the player by name - only experienced through
    systemic intensity.
    """
    TIER_I   = 1   # Baseline Containment
    TIER_II  = 2   # Instability Stress Layer
    TIER_III = 3   # Observer Escalation Layer
    TIER_IV  = 4   # Escalation Containment Layer
    TIER_V   = 5   # Valhalla Layer - Containment Apex


@dataclass
class TierCharacteristics:
    """Observable properties of a containment tier."""
    mutation_rate_bias:       float   # additive to base mutation chance
    league_stability:         float   # 1.0 = fully stable, 0.0 = fractured
    anomaly_density:          float   # 0-1 frequency multiplier on anomaly events
    npc_awareness_level:      float   # 0-1 how close NPCs are to breaking the 4th wall
    relay_node_accessibility: float   # 0-1 how accessible relay nodes are
    description:              str


TIER_CHARACTERISTICS: Dict[ContainmentTier, TierCharacteristics] = {
    ContainmentTier.TIER_I: TierCharacteristics(
        mutation_rate_bias=0.00,
        league_stability=1.00,
        anomaly_density=0.05,
        npc_awareness_level=0.00,
        relay_node_accessibility=0.05,
        description="Baseline containment. League dominant. Anomalies rare. Everything feels natural.",
    ),
    ContainmentTier.TIER_II: TierCharacteristics(
        mutation_rate_bias=0.05,
        league_stability=0.80,
        anomaly_density=0.15,
        npc_awareness_level=0.10,
        relay_node_accessibility=0.20,
        description="Instability stress layer. Mutation rates slightly elevated. League shows internal disagreement.",
    ),
    ContainmentTier.TIER_III: TierCharacteristics(
        mutation_rate_bias=0.12,
        league_stability=0.60,
        anomaly_density=0.30,
        npc_awareness_level=0.25,
        relay_node_accessibility=0.45,
        description="Observer escalation. NPC dialogue slips increase. Relay nodes partially active.",
    ),
    ContainmentTier.TIER_IV: TierCharacteristics(
        mutation_rate_bias=0.22,
        league_stability=0.35,
        anomaly_density=0.55,
        npc_awareness_level=0.50,
        relay_node_accessibility=0.70,
        description="Escalation containment. Biomes visibly unstable. League fractured or militarized.",
    ),
    ContainmentTier.TIER_V: TierCharacteristics(
        mutation_rate_bias=0.40,
        league_stability=0.10,
        anomaly_density=0.85,
        npc_awareness_level=0.80,
        relay_node_accessibility=0.95,
        description="Valhalla layer. Maximum stress. Some NPCs near-aware. Relay nodes openly unstable.",
    ),
}


def compute_containment_tier(ledger: IslandLedger,
                              trajectory: "PlayerTrajectory") -> ContainmentTier:
    """
    Compute the player's current containment tier from live scores.
    Deterministic: same inputs → same tier.
    """
    axis = compute_behavioral_axis(trajectory)
    anomaly_idx    = trajectory.anomaly_exposure
    league_score   = trajectory.competitive_focus
    stability      = max(0.0, ledger.stability_index())
    mutation_drift = max(0.0, -ledger.normalize().get("genetic_diversity", 0.0))

    destab = (
        anomaly_idx * 0.40
        + mutation_drift * 0.25
        + (100.0 - stability) * 0.20
        + (20.0 if axis in (BehavioralAxis.CURIOUS, BehavioralAxis.EXPLOITATIVE) else 0.0)
        - league_score * 0.15
    )
    destab = max(0.0, min(100.0, destab))

    if destab < 15:
        return ContainmentTier.TIER_I
    elif destab < 32:
        return ContainmentTier.TIER_II
    elif destab < 52:
        return ContainmentTier.TIER_III
    elif destab < 72:
        return ContainmentTier.TIER_IV
    else:
        return ContainmentTier.TIER_V


def _seed_to_base_tier(seed: int) -> ContainmentTier:
    """
    Derive the island's baseline tier from seed alone (before player action).
    60% Tier I → 25% Tier II → 10% III → 4% IV → 1% V.
    """
    rng = SeededRNG(seed).fork("base_tier")
    roll = rng.random()
    if roll < 0.60:
        return ContainmentTier.TIER_I
    elif roll < 0.85:
        return ContainmentTier.TIER_II
    elif roll < 0.95:
        return ContainmentTier.TIER_III
    elif roll < 0.99:
        return ContainmentTier.TIER_IV
    else:
        return ContainmentTier.TIER_V


# ============================================================
# §20  NARRATIVE ARCHITECTURE  (B1-B4)
# ============================================================
#
# All story beats derive from fixed pools.
# Islands recombine them deterministically from seed.
# There is no freeform lore generation - only recombination.

@dataclass
class NarrativeMountain:
    """
    A structural narrative element - a fixed story pressure in the world.
    tier_escalations maps ContainmentTier.value (1-5) → description.
    """
    code:        str
    label:       str
    description: str   # base (Tier I) manifestation
    tier_escalations: Dict[int, str] = field(default_factory=dict)

    def get_description(self, tier: ContainmentTier) -> str:
        return self.tier_escalations.get(tier.value, self.description)


# ── §20.1 Global Mountains (M1-M20) ────────────────────────
GLOBAL_MOUNTAINS: List[NarrativeMountain] = [
    NarrativeMountain(
        code="M1", label="Founder Betrayal Fracture",
        description="Conflicting accounts of a historical betrayal circulate in old texts.",
        tier_escalations={
            2: "NPCs reference a dispute between founders without knowing details.",
            3: "Fragments mention names: Voss, Ilyanova, Kincaid. Dates don't add up.",
            4: "Records actively contradict each other. One faction suppresses a version.",
            5: "The suppressed version surfaces. The League has been protecting a lie.",
        },
    ),
    NarrativeMountain(
        code="M2", label="League Corruption Drift",
        description="League officials prioritize results over rules. Minor inconsistencies in rankings.",
        tier_escalations={
            2: "Ranking discrepancies that never get corrected. Officials deflect questions.",
            3: "Evidence that top trainers receive advance information about opponents.",
            4: "League bureaucracy enforces suppression of challengers who ask too many questions.",
            5: "The League exists to manage behavioral data, not to crown champions.",
        },
    ),
    NarrativeMountain(
        code="M3", label="Ecological Collapse Pressure",
        description="Some wild zones are diminishing in biodiversity. Researchers note it quietly.",
        tier_escalations={
            2: "Multiple zones report the same anomaly simultaneously. No official explanation.",
            3: "Ecological contraction follows a non-random pattern - almost grid-like.",
            4: "Species are disappearing in sequence matching an experimental depletion model.",
            5: "The island's ecology is being actively managed. Collapse is a measurement.",
        },
    ),
    NarrativeMountain(
        code="M4", label="Neiko Mutation Instability",
        description="Occasional unusual evolutions. Researchers log them as outliers.",
        tier_escalations={
            2: "Mutation rates slightly elevated in specific zones. No explanation given.",
            3: "Certain Neikos exhibit traits from species in entirely different biomes.",
            4: "Mutation drift appears to accelerate near relay nodes.",
            5: "Neiko genomes contain non-ecological data. They are not just animals.",
        },
    ),
    NarrativeMountain(
        code="M5", label="Hidden Observer Within Island",
        description="One NPC seems unusually well-informed. Easy to dismiss as rumor.",
        tier_escalations={
            2: "The same figure appears in multiple unconnected locations.",
            3: "They reference events they shouldn't have access to.",
            4: "Their dialogue unlocks differently depending on player choices.",
            5: "They are not a resident. They are a monitor.",
        },
    ),
    NarrativeMountain(
        code="M6", label="Memory Archive Degradation",
        description="Old records in facilities are partially corrupted. Normal wear and tear.",
        tier_escalations={
            2: "The corruption pattern is non-random - specific dates and names are always missing.",
            3: "The gaps in the records correspond to PROJECT HUNDRED activation.",
            4: "Someone has been maintaining the degradation. It is deliberate.",
            5: "The archive is not corrupted. It is redacted. And not recently.",
        },
    ),
    NarrativeMountain(
        code="M7", label="Containment Breach Attempt (Past)",
        description="Local legend of someone who tried to leave the island and disappeared.",
        tier_escalations={
            2: "Two separate legends with identical outcomes - both dismissed as myth.",
            3: "One account has verifiable dates. The disappearance was logged by the League.",
            4: "The League logged it and closed the file the same day. No follow-up.",
            5: "The breach didn't fail. It was intercepted. From outside.",
        },
    ),
    NarrativeMountain(
        code="M8", label="Silent Cartographer Loyalist",
        description="An elderly NPC who avoids certain topics. Seems to carry weight.",
        tier_escalations={
            2: "They know more technical vocabulary than their role suggests.",
            3: "They use the phrase 'the original design' when referring to island structure.",
            4: "They have a symbol - an old cartographer's mark - hidden on their person.",
            5: "They were there at activation. They chose to stay inside.",
        },
    ),
    NarrativeMountain(
        code="M9", label="Failed Rebellion Myth",
        description="Stories of a group that tried to change the League from within.",
        tier_escalations={
            2: "The group's name is known but their goals aren't recorded.",
            3: "Their goals were ecological - they wanted to open the restricted zones.",
            4: "They were absorbed into the League structure. Their leaders became officials.",
            5: "The absorption was deliberate. Rebellion is a pressure valve, not a threat.",
        },
    ),
    NarrativeMountain(
        code="M10", label="False History Rewrite",
        description="The island's founding myth doesn't quite match the ruins.",
        tier_escalations={
            2: "Two versions of founding history exist. Neither is officially challenged.",
            3: "The official version was written 40 years after the ruins date.",
            4: "The ruins predate every recorded civilization on this island.",
            5: "There was no founding. There was activation.",
        },
    ),
    NarrativeMountain(
        code="M11", label="League Protects the System",
        description="The League enforces competition standards rigorously. Respected institution.",
        tier_escalations={
            2: "Standards enforcement extends beyond competition into movement and research access.",
            3: "The League gates certain information as 'above competitive rank'.",
            4: "League authority correlates perfectly with anomaly suppression zones.",
            5: "The League is not an institution. It is an instrument.",
        },
    ),
    NarrativeMountain(
        code="M12", label="League Accidentally Destabilizes the System",
        description="League pressure on certain biomes has unintended ecological side effects.",
        tier_escalations={
            2: "Tournament locations show measurable post-event instability spikes.",
            3: "The instability pattern matches a known stress-test model.",
            4: "The League was designed to generate exactly this pressure. It is working.",
            5: "Destabilization is the goal, not the accident. The stress test needs subjects.",
        },
    ),
    NarrativeMountain(
        code="M13", label="Neiko Sentience Spike",
        description="Some Neikos seem to react to environmental cues unusually intelligently.",
        tier_escalations={
            2: "Certain Neikos near anomaly zones show coordinated behavior.",
            3: "A Neiko refuses to battle near a relay node. Repeatedly. Specifically.",
            4: "Neikos appear to recognize the player's behavioral pattern, not just commands.",
            5: "Neikos were designed with behavioral mirroring. They are reflecting back.",
        },
    ),
    NarrativeMountain(
        code="M14", label="Anomaly Zone Expanding",
        description="One restricted wild zone has been growing over the past decade.",
        tier_escalations={
            2: "Two zones are growing. Their edges are approaching each other.",
            3: "The expansion follows a schedule. It is not geological drift.",
            4: "The League marks expansion zones as 'under study' and restricts access.",
            5: "The anomaly zones are not malfunctions. They are measurement instruments.",
        },
    ),
    NarrativeMountain(
        code="M15", label="Relay Node (Control Infrastructure)",
        description="Abandoned structures deep in restricted zones. Origin unknown.",
        tier_escalations={
            2: "The structures use materials not found on this island.",
            3: "They emit a faint signal that Neikos react to.",
            4: "The signal is not natural. It is scheduled. It is a telemetry ping.",
            5: "The relay node is still active. Something is still receiving.",
        },
    ),
    NarrativeMountain(
        code="M16", label="Player Profile Flagged as Outlier",
        description="An NPC mentions offhand that 'people like you don't come through here often'.",
        tier_escalations={
            2: "Multiple NPCs independently use similar language about the player.",
            3: "A facility log contains an entry that matches the player's exact arrival date.",
            4: "The entry was pre-written. Before arrival.",
            5: "The system was expecting the player. The experiment was designed for variables.",
        },
    ),
    NarrativeMountain(
        code="M17", label="Ecological Over-Optimization",
        description="One biome is suspiciously stable - no natural variance at all.",
        tier_escalations={
            2: "The stable zone has existed for exactly as long as the island's recorded history.",
            3: "Its species distribution is mathematically too even to be natural.",
            4: "It is a control group. Someone needs an unperturbed baseline to compare against.",
            5: "The control group has been observed to drift once. After the player arrived.",
        },
    ),
    NarrativeMountain(
        code="M18", label="Cultural Ritual Around Containment",
        description="A local festival involves symbolic 'boundary walking' - origin unclear.",
        tier_escalations={
            2: "The ritual predates written records. It has never been explained, only practiced.",
            3: "The route of boundary walking traces the actual relay node network.",
            4: "Elders who lead the ritual know what the path marks. They do not say.",
            5: "The ritual is a maintained memory. Someone made sure it would not be forgotten.",
        },
    ),
    NarrativeMountain(
        code="M19", label="Fragmented Founder Recording",
        description="A garbled audio clip found in an abandoned facility. Mostly noise.",
        tier_escalations={
            2: "More clips found. The voice is consistent. The words are almost coherent.",
            3: "Reconstruction produces a name: Ilyanova. And a phrase: 'they won't know'.",
            4: "The full recording surfaces: a warning that was never transmitted.",
            5: "The warning was transmitted. It was received. It was suppressed by Kincaid.",
        },
    ),
    NarrativeMountain(
        code="M20", label="Genetic Drift Beyond Parameters",
        description="A species is evolving in ways the League's research database doesn't account for.",
        tier_escalations={
            2: "The drift is occurring in multiple unrelated species simultaneously.",
            3: "The pattern matches a theoretical evolutionary model from a suppressed paper.",
            4: "The paper was written by Dr. Ilyanova. It was classified by Kincaid.",
            5: "The drift is intentional. The Neikos were designed to outpace entropy.",
        },
    ),
]

# ── §25.1 Island Mystery Pool (IM1-IM20) ───────────────────
@dataclass
class IslandMystery:
    """A local mystery structure - island-specific narrative pressure."""
    code:        str
    label:       str
    tier_descriptions: Dict[int, str]   # tier value → text

    def get_description(self, tier: ContainmentTier) -> str:
        # Return closest tier description (fall back to lower tiers)
        for t in range(tier.value, 0, -1):
            if t in self.tier_descriptions:
                return self.tier_descriptions[t]
        return list(self.tier_descriptions.values())[0]


ISLAND_MYSTERY_POOL: List[IslandMystery] = [
    IslandMystery("IM1",  "Disappearing Wild Zone",
        {1: "A wild zone that local maps show is no longer accessible.",
         2: "Rangers confirm the zone exists but won't escort anyone there.",
         3: "Satellite-equivalent survey data shows the zone still active. Access is blocked, not gone.",
         4: "The zone is a measurement node. Access was revoked after anomaly threshold exceeded.",
         5: "The zone was sealed because someone got too close to a relay node."}),
    IslandMystery("IM2",  "League Record Inconsistency",
        {1: "A ranking result that doesn't match the reported outcome.",
         2: "Three discrepancies in the same season. All favor the same trainer.",
         3: "The trainer is connected to League administration. No one files complaints.",
         4: "The records are being managed to produce a specific competitive outcome.",
         5: "The competitive outcome is a data point in the experiment. It was pre-calculated."}),
    IslandMystery("IM3",  "Restricted Ruin Area",
        {1: "An area fenced off by the League. 'Unstable terrain' is the official reason.",
         2: "The terrain is stable. Researchers have been turned away twice.",
         3: "Ruins predate the island's recorded history by centuries.",
         4: "The ruins are Cartographer infrastructure from activation.",
         5: "Inside: a deactivated relay node and a partial Ilyanova field log."}),
    IslandMystery("IM4",  "Mutated Neiko Cluster",
        {1: "A group of Neikos near a specific cave with unusual coloration.",
         2: "Their stats are outside species normal range - not bred, just different.",
         3: "The cave is near a relay node. The mutation correlates with proximity.",
         4: "Their genome contains markers not present in the rest of the species roster.",
         5: "They are prototype variants. Ilyanova's notes describe this cluster by coordinates."}),
    IslandMystery("IM5",  "Gym Leader Acting Unstable",
        {1: "A Gym Leader who has been making unusual decisions lately.",
         2: "They've been visiting a restricted zone repeatedly. No explanation given.",
         3: "They found something there and haven't told anyone what.",
         4: "They found a fragment of the original activation manifest.",
         5: "They now know the island is contained. They haven't decided what to do with that."}),
    IslandMystery("IM6",  "Missing Researcher",
        {1: "A researcher who left for the field three weeks ago and hasn't returned.",
         2: "Their last known location is near an anomaly zone.",
         3: "Their notes describe mutation irregularities that don't match League data.",
         4: "They accessed partial relay node logs before disappearing.",
         5: "They are flagged in the system as a high-impact variable. Still being observed."}),
    IslandMystery("IM7",  "Strange Weather Pattern",
        {1: "Unusual weather in one region - localized, recurring.",
         2: "The pattern has a 14-day cycle. Too precise for natural weather.",
         3: "It correlates with relay node activity spikes.",
         4: "The weather is a side effect of telemetry transmission.",
         5: "The signal is still being sent. Someone is still receiving it."}),
    IslandMystery("IM8",  "Neiko Refuses Command Near Specific Zone",
        {1: "A reliable Neiko that refuses to enter one particular area.",
         2: "Multiple trainers report the same behavior near the same zone.",
         3: "The zone contains a relay node. The Neiko's behavioral logic is reacting to it.",
         4: "The refusal is a design feature - Neikos were programmed to avoid relay nodes.",
         5: "Except when the player approaches. Something about the variable changes the response."}),
    IslandMystery("IM9",  "League Ranking Glitch",
        {1: "A top-ranked trainer who doesn't appear in recent battle logs.",
         2: "Their rating was manually adjusted. No timestamp, no authorization code.",
         3: "The adjustment came from an account that doesn't exist in the official system.",
         4: "The account exists in a deeper database layer - Cartographer administrative access.",
         5: "The ranking is being managed to produce specific pressure on the player's trajectory."}),
    IslandMystery("IM10", "Abandoned Outpost",
        {1: "A League outpost that has been empty for years. No decommission record.",
         2: "Equipment inside is still powered. Something is still running.",
         3: "The equipment is telemetry hardware, not League issue.",
         4: "It is a passive observation station. It has been logging since activation.",
         5: "It logged the player's entry into this island 72 hours before they arrived."}),
    IslandMystery("IM11", "Repeating Dream Reports",
        {1: "Locals in one settlement report similar recurring dreams.",
         2: "The imagery is consistent: a grid, a signal, a room with no door.",
         3: "The signal in the dream matches the relay node frequency.",
         4: "The dreams started when the player arrived on the island.",
         5: "The dreams are a systemic response. The island is aware something changed."}),
    IslandMystery("IM12", "Echoing Signal in Cave",
        {1: "A cave with an unusual acoustic signature. Local kids dare each other inside.",
         2: "The echo contains a repeating pattern. Not natural reverb.",
         3: "The pattern is a compressed data fragment - partial relay transmission.",
         4: "Decoded: a list of 100 coordinates. Each one is an island.",
         5: "One coordinate matches this island. Ninety-nine others exist."}),
    IslandMystery("IM13", "Identical Architecture Across Distant Regions",
        {1: "Two buildings in different regions with the exact same unusual window shape.",
         2: "A third building, different region, same shape. Different construction teams.",
         3: "The shape is a Cartographer design standard. It appears on all 100 islands.",
         4: "It was included as a passive continuity marker - to be noticed only at high tier.",
         5: "The player has noticed it. The system has logged the noticing."}),
    IslandMystery("IM14", "Historical Dates That Don't Align",
        {1: "A monument with a founding date that predates the oldest known settlement.",
         2: "Multiple date discrepancies across different institutions. All by the same margin.",
         3: "The margin is the activation delay - the time between Cartographer seeding and 'founding'.",
         4: "The false history was written into the island's seed data.",
         5: "There was no history before activation. The memories were generated."}),
    IslandMystery("IM15", "Forbidden Route",
        {1: "A path marked on old maps that has been officially closed for 'safety'.",
         2: "The path leads to the island's geographic center. Exact center.",
         3: "At the center: a structure that appears on no official map.",
         4: "The structure is the primary relay node - the island's main telemetry hub.",
         5: "The hub is still active. Its last transmission was yesterday."}),
    IslandMystery("IM16", "Champion Who Vanished",
        {1: "A former island champion who retired and then disappeared entirely.",
         2: "No death record. No emigration record. Simply not in any database after one date.",
         3: "The date matches a containment breach attempt that was suppressed.",
         4: "They got out. Or they tried. The system flagged the attempt and responded.",
         5: "Their profile still exists in the Cartographer database. Active. Monitored."}),
    IslandMystery("IM17", "Incorrect Evolution Event",
        {1: "A Neiko that evolved into a form not in the registered species database.",
         2: "A second case in a different region. Same unregistered form.",
         3: "The form is in the Ilyanova research database - a planned generation-three variant.",
         4: "The variant was supposed to appear in generation three of the simulation.",
         5: "The simulation is in generation three. The timeline is on schedule."}),
    IslandMystery("IM18", "Cultural Ritual With Unknown Origin",
        {1: "A ceremony practiced by one settlement with no recorded historical origin.",
         2: "The ceremony involves counting to one hundred. Every year. Always exactly once.",
         3: "One hundred is the number of islands. The ceremony is a memory of the partition.",
         4: "An elder knows this. They have always known. They do not speak of it unsolicited.",
         5: "The player asks. The elder answers. The system logs it as a disclosure event."}),
    IslandMystery("IM19", "League Directive Contradiction",
        {1: "Two official League directives that directly contradict each other.",
         2: "Both are current. Neither has been rescinded. Officials act on both selectively.",
         3: "The contradiction is structural - it allows officials to deny access to anything.",
         4: "The contradiction was designed in. Plausible deniability at institutional scale.",
         5: "It is the same mechanism Kincaid used to suppress Ilyanova's disclosure."}),
    IslandMystery("IM20", "Silent Zone (Ambient Absence Anomaly)",
        {1: "A node in the wild where ambient sound completely stops. Locals avoid it.",
         2: "The silence is a standing wave - something is dampening ambient frequency.",
         3: "The dampening is active, not geological. Something is broadcasting silence.",
         4: "It is a dead relay node - still broadcasting its nullification field.",
         5: "It was turned off once. When it turned back on, a Neiko colony had relocated around it."}),
]

# ── §25.2 Character Arc Pool (CA1-CA8) ─────────────────────
@dataclass
class CharacterArc:
    """A fixed archetype structure for an island NPC arc."""
    code:         str
    label:        str
    role_hint:    str    # what kind of NPC carries this arc
    description:  str


CHARACTER_ARC_POOL: List[CharacterArc] = [
    CharacterArc("CA1", "Loyalist League Leader",   "gym leader / official",
        "Deeply invested in the League as a stabilizing force. Will resist destabilization at any cost."),
    CharacterArc("CA2", "Doubting Gym Leader",      "gym leader",
        "Privately questions the League's purpose but enforces its rules. Can be tipped either way."),
    CharacterArc("CA3", "Hidden Knower",            "hermit / archivist / signal voice",
        "Holds partial knowledge of PROJECT HUNDRED. Confirms suspicions. Never gives full exposition."),
    CharacterArc("CA4", "Overzealous Researcher",   "researcher / academic",
        "Pursuing anomaly data obsessively. Getting dangerously close to triggering a suppression response."),
    CharacterArc("CA5", "Disillusioned Champion",   "former champion",
        "Achieved the top of the League and found it hollow. Suspects the system is managed."),
    CharacterArc("CA6", "Ecological Purist",        "wild zone ranger / preservationist",
        "Devoted to protecting biodiversity. Increasingly aware the ecology is being managed, not preserved."),
    CharacterArc("CA7", "Power-Obsessed Rival",     "rival trainer",
        "Drives the player through competitive pressure. Unknowingly functions as a stress-test instrument."),
    CharacterArc("CA8", "Resigned Archivist",       "facility archivist / librarian",
        "Has seen the redacted records. Chose not to act. Carries the weight of that choice."),
]

# ── §25.3 League Conflict Pool (LC1-LC6) ───────────────────
@dataclass
class LeagueConflict:
    """A structural tension within the island's League."""
    code:        str
    label:       str
    description: str


LEAGUE_CONFLICT_POOL: List[LeagueConflict] = [
    LeagueConflict("LC1", "League Protects Stability",
        "League enforces order as a legitimate cultural institution. Trusted by most."),
    LeagueConflict("LC2", "League Suppresses Anomalies",
        "League actively restricts information about anomaly zones and unusual Neiko behavior."),
    LeagueConflict("LC3", "League Exploits Mutation",
        "Top trainers quietly leverage mutation-drift Neikos for competitive advantage."),
    LeagueConflict("LC4", "League Denies Historical Records",
        "Official League history omits the founding period. Researchers who ask are reassigned."),
    LeagueConflict("LC5", "League Split Into Factions",
        "Internal ideological division - one faction wants open research, one wants controlled access."),
    LeagueConflict("LC6", "League Militarization of Neikos",
        "Elite division of League using Neikos as enforcement. Framed as 'conservation rangers'."),
]


# ── §20.2 Island Narrative Profile ─────────────────────────

@dataclass
class IslandNarrativeProfile:
    """
    Deterministic narrative skeleton for one island.
    Selected from fixed pools via seeded RNG - never freeform generated.
    """
    # Global experiment mountains
    primary_global_boulders:   List[str] = field(default_factory=list)   # 8-12 M-codes
    secondary_global_boulders: List[str] = field(default_factory=list)   # 4-6 M-codes

    # Island-local mysteries
    primary_mysteries:   List[str] = field(default_factory=list)   # 3-5 IM-codes
    secondary_mysteries: List[str] = field(default_factory=list)   # 2-3 IM-codes

    # Character arcs
    active_character_arcs: List[str] = field(default_factory=list)  # 3 CA-codes
    minor_roles:           List[str] = field(default_factory=list)  # 2 CA-codes

    # League conflict
    primary_league_conflict:    str = ""   # 1 LC-code
    background_league_tension:  str = ""   # 1 LC-code

    # Founder interpretation (from §17)
    founder_framing: Dict[str, str] = field(default_factory=dict)

    # Runtime resolution tracking
    resolved_mysteries:   Set[str] = field(default_factory=set)
    unresolved_mysteries: Set[str] = field(default_factory=set)

    def is_mountain_active(self, code: str) -> bool:
        return (code in self.primary_global_boulders
                or code in self.secondary_global_boulders)

    def is_mystery_primary(self, code: str) -> bool:
        return code in self.primary_mysteries

    def resolve_mystery(self, code: str):
        self.resolved_mysteries.add(code)
        self.unresolved_mysteries.discard(code)

    def to_dict(self) -> dict:
        return {
            "primary_global_boulders": self.primary_global_boulders,
            "secondary_global_boulders": self.secondary_global_boulders,
            "primary_mysteries": self.primary_mysteries,
            "secondary_mysteries": self.secondary_mysteries,
            "active_character_arcs": self.active_character_arcs,
            "minor_roles": self.minor_roles,
            "primary_league_conflict": self.primary_league_conflict,
            "background_league_tension": self.background_league_tension,
            "founder_framing": self.founder_framing,
            "resolved_mysteries": list(self.resolved_mysteries),
            "unresolved_mysteries": list(self.unresolved_mysteries),
        }


# ── §20.3 / B3  generate_island_narrative ──────────────────

def generate_island_narrative(seed: int,
                               base_tier: ContainmentTier) -> IslandNarrativeProfile:
    """
    Deterministically build the narrative profile for this island.

    Higher base_tier → more instability/containment-themed mountains promoted
    into primary positions.
    """
    rng = SeededRNG(seed).fork("narrative")

    all_m_codes  = [m.code for m in GLOBAL_MOUNTAINS]
    all_im_codes = [m.code for m in ISLAND_MYSTERY_POOL]
    all_ca_codes = [a.code for a in CHARACTER_ARC_POOL]
    all_lc_codes = [c.code for c in LEAGUE_CONFLICT_POOL]

    # ── Global mountains ──────────────────────────────────
    # Tier-weighted: higher tier boosts instability mountains
    _instability_mountains = {"M3", "M4", "M7", "M12", "M13", "M14", "M16", "M20"}
    _containment_mountains = {"M1", "M6", "M10", "M15", "M18", "M19"}

    def _mountain_weight(code: str) -> float:
        tier_v = base_tier.value
        if code in _instability_mountains:
            return 1.0 + (tier_v - 1) * 0.3
        if code in _containment_mountains:
            return 1.0 + (tier_v - 1) * 0.2
        return 1.0

    weights = [_mountain_weight(c) for c in all_m_codes]
    # Sample primary boulders (8-12)
    n_primary = rng.randint(8, 12)
    # Weighted sampling without replacement
    pool = list(zip(all_m_codes, weights))
    selected: List[str] = []
    for _ in range(min(n_primary, len(pool))):
        total_w = sum(w for _, w in pool)
        r = rng.random() * total_w
        cumul = 0.0
        chosen_idx = 0
        for i, (_, w) in enumerate(pool):
            cumul += w
            if r <= cumul:
                chosen_idx = i
                break
        selected.append(pool[chosen_idx][0])
        pool.pop(chosen_idx)

    primary_global = selected

    # Secondary from remaining (4-6)
    remaining_codes = [c for c, _ in pool]
    n_secondary = rng.randint(4, 6)
    rng.shuffle(remaining_codes)
    secondary_global = remaining_codes[:n_secondary]

    # ── Island mysteries ──────────────────────────────────
    rng.shuffle(all_im_codes)
    n_prim_m = rng.randint(3, 5)
    primary_myst = all_im_codes[:n_prim_m]
    n_sec_m = rng.randint(2, 3)
    secondary_myst = all_im_codes[n_prim_m: n_prim_m + n_sec_m]

    # ── Character arcs ────────────────────────────────────
    rng.shuffle(all_ca_codes)
    active_arcs = all_ca_codes[:3]
    minor_roles  = all_ca_codes[3:5]

    # Force CA3 (Hidden Knower) into active arcs - every island has one
    if "CA3" not in active_arcs:
        active_arcs.append("CA3")
        if len(active_arcs) > 3:
            active_arcs = active_arcs[1:]  # drop first non-CA3 to maintain count

    # ── League conflicts ──────────────────────────────────
    rng.shuffle(all_lc_codes)
    primary_lc    = all_lc_codes[0]
    background_lc = all_lc_codes[1]

    # ── Unresolved mysteries (§27) ────────────────────────
    # At least 1 primary mystery starts as unresolved
    unresolved = set(primary_myst[:1])

    return IslandNarrativeProfile(
        primary_global_boulders=primary_global,
        secondary_global_boulders=secondary_global,
        primary_mysteries=primary_myst,
        secondary_mysteries=secondary_myst,
        active_character_arcs=active_arcs,
        minor_roles=minor_roles,
        primary_league_conflict=primary_lc,
        background_league_tension=background_lc,
        founder_framing=resolve_founder_framing(seed),
        unresolved_mysteries=unresolved,
    )


def get_mystery_description(mystery_code: str, tier: ContainmentTier) -> str:
    """Return the tier-appropriate description for a given island mystery code."""
    for m in ISLAND_MYSTERY_POOL:
        if m.code == mystery_code:
            return m.get_description(tier)
    for m in GLOBAL_MOUNTAINS:
        if m.code == mystery_code:
            return m.get_description(tier)
    return f"[{mystery_code}: no description found]"


# ============================================================
# §21  HIDDEN KNOWER SYSTEM
# ============================================================

class KnowerArchetype(Enum):
    RETIRED_ARCHIVIST  = auto()   # holds pre-PROJECT files; spawns near FACILITY
    REGIONAL_GYM_LEADER = auto()  # knows league anomalies; spawns near CITY/SETTLEMENT
    ISOLATED_RESEARCHER = auto()  # knows mutation data; spawns near FACILITY/WILD_ZONE
    ELDERLY_HERMIT      = auto()  # knows island's founding secret; spawns WILD_ZONE/ANOMALY_ZONE
    ANONYMOUS_SIGNAL    = auto()  # disembodied signal; attached to a relay node


_KNOWER_NAMES: Dict[str, List[str]] = {
    "RETIRED_ARCHIVIST":   ["Docent Fehr", "Former-Keeper Voss", "Archivist Lenne",
                             "Curator Rael", "Keeper Oshiro"],
    "REGIONAL_GYM_LEADER": ["Gym Chief Maris", "Circuit-Marshal Dune",
                             "Ex-Leader Sable", "Arena Head Torrek"],
    "ISOLATED_RESEARCHER": ["Dr. Sona Vey", "Researcher Phelim", "Lab-Ghost Oria",
                             "Analyst Crest"],
    "ELDERLY_HERMIT":      ["The Old One", "Hermit of the Shore", "Root-Speaker Ilya",
                             "The Watcher", "Elder Namun"],
    "ANONYMOUS_SIGNAL":    ["Signal-7", "The Echo", "RELAY-FRAGMENT", "Voice-in-Static"],
}

_KNOWER_UNLOCK_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "RETIRED_ARCHIVIST":   {"exploration_depth": 30.0, "research_investment": 20.0},
    "REGIONAL_GYM_LEADER": {"competitive_focus": 40.0, "battles_won": 5.0},
    "ISOLATED_RESEARCHER": {"research_investment": 35.0, "anomaly_exposure": 10.0},
    "ELDERLY_HERMIT":      {"exploration_depth": 50.0, "anomaly_events": 2.0},
    "ANONYMOUS_SIGNAL":    {"anomaly_exposure": 25.0},
}

_KNOWER_DIALOGUE_FRAGMENTS: Dict[str, List[str]] = {
    "RETIRED_ARCHIVIST": [
        "I remember when this was just a file number. PROJECT HUNDRED - "
        "SITE {island_name}. They thought naming them would make it easier to care.",
        "Dr. Voss believed the creatures were evolving responses to the containment "
        "fields themselves. Not despite the boundary. Because of it.",
        "THE CARTOGRAPHERS made us sign documents. We were never to call them anything "
        "but 'subjects.' I called them {species_name}. I still do.",
    ],
    "REGIONAL_GYM_LEADER": [
        "The league tables have been wrong for years. Half these rankings reflect "
        "Tier pressure, not actual ability. You'd know that if you'd seen {island_name} "
        "in TIER III.",
        "They sent us a memo. 'Increased anomalous incidents are a normal feature of "
        "regional league expansion.' I framed it. It's still on my wall.",
        "First time I saw a relay node activate mid-match I thought the arena was "
        "glitching. No - that's just what TIER II looks like up close.",
    ],
    "ISOLATED_RESEARCHER": [
        "Mutation rates above 0.4 shouldn't be possible in a closed system. "
        "And yet here we are, tick after tick. Something is driving the variance.",
        "I mapped every anomaly zone on {island_name}. They're not random. "
        "They form a lattice. PROJECT HUNDRED Site Survey maps match perfectly.",
        "Ilyanova's containment thesis was correct. The boundary is porous by design. "
        "They wanted to see what would pass through.",
    ],
    "ELDERLY_HERMIT": [
        "This island has a name older than the one THE CARTOGRAPHERS gave it. "
        "It has always known what it was. A cage with open doors.",
        "I was here when the first relay went dark. Three days later the creatures "
        "in the southern ANOMALY_ZONE started moving in patterns. The patterns matched "
        "Kincaid's early field notes.",
        "You're not the first outsider. You won't be the last. But you're the first "
        "in a long time who came here looking rather than counting.",
    ],
    "ANONYMOUS_SIGNAL": [
        "FRAGMENT RECEIVED // CARTOGRAPHER INTERNAL // "
        "DO NOT DISTRIBUTE // SITE {island_name} shows accelerating deviation "
        "from baseline ecological model. Current tier: ESCALATING.",
        "---SIGNAL INTERRUPT--- "
        "VOSS THESIS NODE 7: 'the subjects are not contained. they are observed.' "
        "---END FRAGMENT---",
        "You have been in proximity to {anomaly_count} anomaly zones. "
        "The relay network has been tracking you since node {relay_node_id}. "
        "This is not a warning. This is documentation.",
    ],
}


@dataclass
class HiddenKnower:
    """A non-player character who holds partial truths about PROJECT HUNDRED."""
    archetype:         KnowerArchetype
    name:              str
    location_node_id:  str
    unlock_thresholds: Dict[str, float]   # trajectory key → minimum value
    dialogue_fragments: List[str]          # raw template strings

    def is_unlocked(self, trajectory: "PlayerTrajectory") -> bool:
        """Return True if the player has met all unlock thresholds."""
        traj_dict = trajectory.to_dict()
        for key, required in self.unlock_thresholds.items():
            actual = traj_dict.get(key, 0.0)
            if actual < required:
                return False
        return True

    def get_fragment(self, index: int, context: Dict[str, Any]) -> str:
        """Return a formatted dialogue fragment by index."""
        if not self.dialogue_fragments:
            return "[no dialogue]"
        frag = self.dialogue_fragments[index % len(self.dialogue_fragments)]
        try:
            return frag.format(**context)
        except KeyError:
            return frag

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archetype": self.archetype.name,
            "name": self.name,
            "location_node_id": self.location_node_id,
            "unlock_thresholds": self.unlock_thresholds,
            "fragment_count": len(self.dialogue_fragments),
        }


def generate_hidden_knower(
    topology: "IslandTopology",
    narrative_profile: "IslandNarrativeProfile",
    seed: int,
) -> HiddenKnower:
    """
    Place a hidden knower on the island whose archetype is consistent
    with the CA3 arc (The Hidden Knower) in the narrative profile.
    Archetype is seeded so it's deterministic per island.
    """
    rng = SeededRNG(seed).fork("hidden_knower")

    # Pick archetype - weighted: hermit/archivist/signal slightly more common
    archetype_weights = [
        (KnowerArchetype.ELDERLY_HERMIT,      30),
        (KnowerArchetype.RETIRED_ARCHIVIST,   25),
        (KnowerArchetype.ANONYMOUS_SIGNAL,    20),
        (KnowerArchetype.ISOLATED_RESEARCHER, 15),
        (KnowerArchetype.REGIONAL_GYM_LEADER, 10),
    ]
    total_w = sum(w for _, w in archetype_weights)
    roll = rng.randint(0, total_w - 1)
    chosen = archetype_weights[0][0]
    acc = 0
    for arch, w in archetype_weights:
        acc += w
        if roll < acc:
            chosen = arch
            break

    # Pick location based on archetype preference
    archetype_node_prefs: Dict[str, List[str]] = {
        "RETIRED_ARCHIVIST":   ["FACILITY"],
        "REGIONAL_GYM_LEADER": ["CITY", "SETTLEMENT"],
        "ISOLATED_RESEARCHER": ["FACILITY", "WILD_ZONE"],
        "ELDERLY_HERMIT":      ["WILD_ZONE", "ANOMALY_ZONE"],
        "ANONYMOUS_SIGNAL":    [],  # prefer relay nodes
    }

    prefs = archetype_node_prefs.get(chosen.name, [])
    candidate_nodes: List[str] = []

    if chosen == KnowerArchetype.ANONYMOUS_SIGNAL and topology.relay_node_ids:
        candidate_nodes = list(topology.relay_node_ids)
    else:
        for nid, node in topology.nodes.items():
            if node.node_type.name in prefs:
                candidate_nodes.append(nid)

    # Fallback: any non-start interior node
    if not candidate_nodes:
        candidate_nodes = [
            nid for nid, nd in topology.nodes.items()
            if nid != topology.start_node_id
        ]

    rng.shuffle(candidate_nodes)
    location = candidate_nodes[0] if candidate_nodes else topology.start_node_id

    # Pick name
    name_pool = _KNOWER_NAMES.get(chosen.name, ["The Stranger"])
    name = name_pool[rng.randint(0, len(name_pool) - 1)]

    # Unlock thresholds (base + small seed-based variance)
    base_thresholds = dict(_KNOWER_UNLOCK_THRESHOLDS.get(chosen.name, {}))

    # Dialogue fragments
    fragments = list(_KNOWER_DIALOGUE_FRAGMENTS.get(chosen.name, []))

    return HiddenKnower(
        archetype=chosen,
        name=name,
        location_node_id=location,
        unlock_thresholds=base_thresholds,
        dialogue_fragments=fragments,
    )


# ============================================================
# §22  NGP+ BEHAVIORAL PROFILE PERSISTENCE
# ============================================================

import json as _json
import os as _os

_NK_PROFILE_FILENAME = ".nk_profile.json"


def _nk_profile_path() -> str:
    """Return the absolute path to the hidden NGP+ profile file."""
    saves_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "..", "saves")
    _os.makedirs(saves_dir, exist_ok=True)
    return _os.path.join(saves_dir, _NK_PROFILE_FILENAME)


@dataclass
class BehavioralProfileSignature:
    """
    Persistent cross-run behavioral signature - stored in saves/.nk_profile.json.
    Written at expedition end; applied to the next island at init time.
    """
    behavioral_axis:              str   = "STABILIZING"  # BehavioralAxis name
    anomaly_engagement_history:   float = 0.0            # cumulative anomaly_exposure
    ecological_disruption_pattern:float = 0.0            # cumulative disruption
    dominance_harmony_bias:       float = 0.0            # -1 (harmony) .. +1 (dominance)
    completed_tier:               int   = 1              # tier value at expedition end
    echo_seeds:                   List[int] = field(default_factory=list)  # seeds seen
    run_count:                    int   = 0              # total expeditions completed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "behavioral_axis":               self.behavioral_axis,
            "anomaly_engagement_history":    self.anomaly_engagement_history,
            "ecological_disruption_pattern": self.ecological_disruption_pattern,
            "dominance_harmony_bias":        self.dominance_harmony_bias,
            "completed_tier":                self.completed_tier,
            "echo_seeds":                    self.echo_seeds,
            "run_count":                     self.run_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BehavioralProfileSignature":
        return cls(
            behavioral_axis=d.get("behavioral_axis", "STABILIZING"),
            anomaly_engagement_history=float(d.get("anomaly_engagement_history", 0.0)),
            ecological_disruption_pattern=float(d.get("ecological_disruption_pattern", 0.0)),
            dominance_harmony_bias=float(d.get("dominance_harmony_bias", 0.0)),
            completed_tier=int(d.get("completed_tier", 1)),
            echo_seeds=list(d.get("echo_seeds", [])),
            run_count=int(d.get("run_count", 0)),
        )


def compute_behavioral_signature(
    trajectory: "PlayerTrajectory",
    ledger: "IslandLedger",
    current_tier: "ContainmentTier",
    seed: int,
) -> BehavioralProfileSignature:
    """Derive a BehavioralProfileSignature from the end-of-expedition state."""
    axis = compute_behavioral_axis(trajectory)

    # Dominance-harmony bias: +1 = full dominance, -1 = full harmony
    dom = trajectory.competitive_focus / 100.0
    harm = (trajectory.exploration_depth + trajectory.research_investment) / 200.0
    bias = round(dom - harm, 4)

    # Ecological disruption: urbanization + negative ecology signal
    n = ledger.normalize()
    disruption = round(
        max(0.0, n["urbanization_level"] * 0.5 - n["ecological_balance"] * 0.3), 4
    )

    return BehavioralProfileSignature(
        behavioral_axis=axis.name,
        anomaly_engagement_history=round(trajectory.anomaly_exposure, 4),
        ecological_disruption_pattern=disruption,
        dominance_harmony_bias=bias,
        completed_tier=current_tier.value,
        echo_seeds=[seed],
        run_count=1,
    )


def save_behavioral_profile(
    profile: BehavioralProfileSignature,
    path: Optional[str] = None,
) -> str:
    """
    Merge the new profile with any existing one and write to disk.
    Returns the path written to.
    """
    target = path or _nk_profile_path()
    existing = load_behavioral_profile(target)

    if existing:
        # Merge - accumulate history, keep highest tier seen
        merged = BehavioralProfileSignature(
            behavioral_axis=profile.behavioral_axis,  # latest run wins
            anomaly_engagement_history=round(
                existing.anomaly_engagement_history + profile.anomaly_engagement_history, 4
            ),
            ecological_disruption_pattern=round(
                existing.ecological_disruption_pattern + profile.ecological_disruption_pattern, 4
            ),
            dominance_harmony_bias=round(
                (existing.dominance_harmony_bias + profile.dominance_harmony_bias) / 2.0, 4
            ),
            completed_tier=max(existing.completed_tier, profile.completed_tier),
            echo_seeds=list(set(existing.echo_seeds + profile.echo_seeds))[-10:],
            run_count=existing.run_count + 1,
        )
    else:
        merged = profile
        merged.run_count = max(1, merged.run_count)

    with open(target, "w", encoding="utf-8") as fh:
        _json.dump(merged.to_dict(), fh, indent=2)
    _dbg(f"NGP+ profile saved to {target} (run #{merged.run_count})")
    return target


def load_behavioral_profile(
    path: Optional[str] = None,
) -> Optional[BehavioralProfileSignature]:
    """Load NGP+ profile from disk. Returns None if not found or corrupt."""
    target = path or _nk_profile_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        return BehavioralProfileSignature.from_dict(data)
    except (FileNotFoundError, _json.JSONDecodeError, KeyError):
        return None


def apply_profile_to_island(
    profile: BehavioralProfileSignature,
    state: "IslandState",
) -> None:
    """
    Apply persisted behavioral signature to a freshly initialized island.
    Only additive nudges - never overwrites base values, only leans them.
    """
    if profile is None:
        return

    # Nudge ledger based on previous disruption pattern
    if profile.ecological_disruption_pattern > 0.3:
        state.ledger.apply_delta("ecological_balance", -profile.ecological_disruption_pattern * 5)
        state.ledger.apply_delta("urbanization_level",  profile.ecological_disruption_pattern * 3)

    # Anomaly history increases anomaly field pressure slightly
    if profile.anomaly_engagement_history > 20.0:
        state.ledger.apply_delta("anomaly_stability", -profile.anomaly_engagement_history * 0.2)

    # Carry dominance bias into starting trajectory
    bias = profile.dominance_harmony_bias
    if abs(bias) > 0.1:
        state.player_trajectory.competitive_focus = min(
            100, state.player_trajectory.competitive_focus + max(0, bias * 15)
        )
        state.player_trajectory.exploration_depth = min(
            100, state.player_trajectory.exploration_depth + max(0, -bias * 10)
        )

    # If run_count ≥ 2, raise base tier floor by 1 (NGP+ pressure)
    if profile.run_count >= 2 and state.base_tier.value < ContainmentTier.TIER_V.value:
        escalated = ContainmentTier(state.base_tier.value + 1)
        state.base_tier = escalated
        state.current_tier = escalated
        _dbg(f"NGP+ tier floor raised to {escalated.name} (run #{profile.run_count})")

    _dbg(f"NGP+ profile applied: axis={profile.behavioral_axis} "
         f"runs={profile.run_count} tier_floor={state.base_tier.name}")


# ============================================================
# §22b  GAME STATE SAVE / LOAD
# ============================================================

_NK_SAVE_FILENAME = ".nk_save.json"


def _nk_save_path() -> str:
    """Return the absolute path to the game-state save file."""
    saves_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "..", "saves")
    _os.makedirs(saves_dir, exist_ok=True)
    return _os.path.join(saves_dir, _NK_SAVE_FILENAME)


def save_game_state(state: "IslandState", path: Optional[str] = None) -> str:
    """
    Serialize the mutable player-layer to JSON and write to disk.
    Does NOT serialize the entire simulation (topology, species roster etc.)
    because those are deterministically regenerated from seed on load.

    Returns the path written.
    """
    target = path or _nk_save_path()

    # Serialize player team creatures (only those with instance_ids)
    creatures_data: Dict[str, Any] = {}
    for inst_id, creature in state.creatures.items():
        # Only save non-wild instances that are either on the team or bred
        if inst_id in {t[0] for t in state.player_team} or inst_id.startswith("bred_"):
            creatures_data[inst_id] = {
                "instance_id": creature.instance_id,
                "species_id": creature.species_id,
                "nickname": creature.nickname,
                "level": creature.level,
                "xp": creature.xp,
                "fatigue": creature.fatigue,
                "loyalty": creature.loyalty,
                "temperament": creature.temperament,
                "adaptation_drift": creature.adaptation_drift,
                "current_hp": creature.current_hp,
                "genes": {
                    "stat_genes": creature.genes.stat_genes,
                    "type_genes": [t.name for t in creature.genes.type_genes],
                    "expression_level": creature.genes.expression_level,
                    "mutation_flags": creature.genes.mutation_flags,
                },
            }

    # Trainer rating state - save ALL trainers so ELO is preserved
    trainers_data: Dict[str, Any] = {}
    for tid, tr in state.league.trainers.items():
        trainers_data[tid] = {
            "rating": tr.rating,
            "wins": tr.wins,
            "losses": tr.losses,
        }

    data = {
        "version": 1,
        "seed": state.seed,
        "tick": state.tick,
        "player_location": state.player_location,
        "instance_counter": state._instance_counter,
        "player_team": list(state.player_team),  # list of [inst_id, species_id]
        "creatures": creatures_data,
        "discovered_species": list(state.discovered_species),
        "discovered_fragments": list(state.discovered_fragments),
        "faction_standings": dict(state.faction_standings),
        "current_tier": state.current_tier.name,
        "base_tier": state.base_tier.name,
        "trainers": trainers_data,
        "trajectory": {
            "competitive_focus": state.player_trajectory.competitive_focus,
            "exploration_depth": state.player_trajectory.exploration_depth,
            "research_investment": state.player_trajectory.research_investment,
            "breeding_intensity": state.player_trajectory.breeding_intensity,
            "anomaly_exposure": state.player_trajectory.anomaly_exposure,
            "risk_appetite": state.player_trajectory.risk_appetite,
            "battles_won": state.player_trajectory.battles_won,
            "battles_lost": state.player_trajectory.battles_lost,
            "nodes_explored": state.player_trajectory.nodes_explored,
            "species_discovered": state.player_trajectory.species_discovered,
            "breeds_completed": state.player_trajectory.breeds_completed,
            "relics_found": state.player_trajectory.relics_found,
            "anomaly_events": state.player_trajectory.anomaly_events,
            "creatures_captured": state.player_trajectory.creatures_captured,
            "ideology": state.player_trajectory.dialogue_ideology.to_dict(),
        },
    }

    with open(target, "w", encoding="utf-8") as fh:
        _json.dump(data, fh, indent=2)
    _dbg(f"[NK Save] Game state saved to {target} (tick={state.tick}, seed={state.seed})")
    return target


def load_game_state(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load raw save dict. Returns None if not found or wrong seed."""
    target = path or _nk_save_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        if data.get("version") != 1:
            _dbg(f"[NK Save] Ignoring save file with unknown version: {data.get('version')}")
            return None
        return data
    except (FileNotFoundError, _json.JSONDecodeError, KeyError) as e:
        _dbg(f"[NK Save] No save found or corrupt ({e})")
        return None


def apply_saved_state(state: "IslandState", data: Dict[str, Any]) -> bool:
    """
    Overlay a save dict onto a freshly init_island()'d IslandState.
    Only restores player-layer data; deterministic sim data is left as-is.

    Returns True if successfully applied, False if seed mismatch.
    """
    if data.get("seed") != state.seed:
        _dbg(f"[NK Save] Seed mismatch: save has {data.get('seed')}, state has {state.seed}. Ignoring.")
        return False

    state.tick = int(data.get("tick", 0))
    state.player_location = data.get("player_location", state.player_location)
    state._instance_counter = int(data.get("instance_counter", 0))

    # Restore player team
    state.player_team = [tuple(t) for t in data.get("player_team", [])]

    # Restore creature instances
    for inst_id, cd in data.get("creatures", {}).items():
        sp_id = cd.get("species_id", "")
        species = state.species_map.get(sp_id)
        if not species:
            _dbg(f"[NK Save] Warning: species {sp_id} not found for {inst_id}, skipping")
            continue
        genes_d = cd.get("genes", {})
        type_genes = []
        for tname in genes_d.get("type_genes", []):
            try:
                type_genes.append(NkType[tname])
            except KeyError:
                pass
        genes = GeneticProfile(
            stat_genes=genes_d.get("stat_genes", [0, 0, 0, 0, 0, 0]),
            type_genes=type_genes or [species.primary_type],
            expression_level=genes_d.get("expression_level", 1.0),
            mutation_flags=genes_d.get("mutation_flags", []),
        )
        creature = CreatureInstance(
            instance_id=inst_id,
            species_id=sp_id,
            nickname=cd.get("nickname", ""),
            level=int(cd.get("level", 1)),
            xp=int(cd.get("xp", 0)),
            genes=genes,
            fatigue=float(cd.get("fatigue", 0.0)),
            loyalty=float(cd.get("loyalty", 50.0)),
            temperament=float(cd.get("temperament", 0.5)),
            adaptation_drift=float(cd.get("adaptation_drift", 0.0)),
            current_hp=int(cd.get("current_hp", 100)),
        )
        state.creatures[inst_id] = creature

    # Restore sets
    state.discovered_species = set(data.get("discovered_species", []))
    state.discovered_fragments = list(data.get("discovered_fragments", []))

    # Restore faction standings
    state.faction_standings = dict(data.get("faction_standings", {}))

    # Restore containment tier
    try:
        state.current_tier = ContainmentTier[data["current_tier"]]
        state.base_tier = ContainmentTier[data["base_tier"]]
    except (KeyError, TypeError):
        pass

    # Restore trainer ratings (ELO history)
    for tid, td in data.get("trainers", {}).items():
        tr = state.league.trainers.get(tid)
        if tr:
            tr.rating = float(td.get("rating", tr.rating))
            tr.wins = int(td.get("wins", tr.wins))
            tr.losses = int(td.get("losses", tr.losses))

    # Restore trajectory
    td = data.get("trajectory", {})
    traj = state.player_trajectory
    traj.competitive_focus    = float(td.get("competitive_focus", 0.0))
    traj.exploration_depth    = float(td.get("exploration_depth", 0.0))
    traj.research_investment  = float(td.get("research_investment", 0.0))
    traj.breeding_intensity   = float(td.get("breeding_intensity", 0.0))
    traj.anomaly_exposure     = float(td.get("anomaly_exposure", 0.0))
    traj.risk_appetite        = float(td.get("risk_appetite", 50.0))
    traj.battles_won          = int(td.get("battles_won", 0))
    traj.battles_lost         = int(td.get("battles_lost", 0))
    traj.nodes_explored       = int(td.get("nodes_explored", 0))
    traj.species_discovered   = int(td.get("species_discovered", 0))
    traj.breeds_completed     = int(td.get("breeds_completed", 0))
    traj.relics_found         = int(td.get("relics_found", 0))
    traj.anomaly_events       = int(td.get("anomaly_events", 0))
    traj.creatures_captured   = int(td.get("creatures_captured", 0))
    ideo = td.get("ideology", {})
    if ideo:
        iv = traj.dialogue_ideology
        iv.competition       = float(ideo.get("competition", 0.0))
        iv.preservation      = float(ideo.get("preservation", 0.0))
        iv.industrialization = float(ideo.get("industrialization", 0.0))
        iv.research_priority = float(ideo.get("research_priority", 0.0))
        iv.anomaly_curiosity = float(ideo.get("anomaly_curiosity", 0.0))

    # Re-mark fragments as discovered in the island_fragments list
    for frag in state.island_fragments:
        if frag.fragment_id in state.discovered_fragments:
            frag.discovered = True

    _dbg(f"[NK Save] State restored: tick={state.tick}, location={state.player_location}, "
         f"team={len(state.player_team)}, fragments_found={len(state.discovered_fragments)}")
    return True


# ============================================================
# §23  NARRATIVE OUTCOME ROLES
# ============================================================

# Maps (island_quadrant, personal_quadrant) → canonical narrative role label.
# Additive - describe_outcome_band() merges this in without removing existing keys.
NARRATIVE_OUTCOME_ROLES: Dict[Tuple[int, int], str] = {
    # Pristine Harmony (iq=0)
    (0, 0): "The Model Subject",        # Champion in harmony
    (0, 1): "The Architect of Balance", # Architect in harmony
    (0, 2): "The Wanderer at Peace",
    (0, 3): "The Visionary Steward",
    (0, 4): "The League Emissary",
    (0, 5): "The Rift Guardian",
    (0, 6): "The Anomaly Ward",
    (0, 7): "The League Reformer",
    (0, 8): "The Gene Keeper",
    (0, 9): "The Horizon Scout",
    # Strained Ecology (iq=1)
    (1, 0): "The Efficient Champion",
    (1, 1): "The Ecological Architect",
    (1, 2): "The Scarred Wanderer",
    (1, 3): "The Restoration Visionary",
    (1, 4): "The Distressed Reformer",
    (1, 5): "The Anomaly Ecologist",
    (1, 6): "The Destabilized Stabilizer",
    (1, 7): "The Crisis Reformer",
    (1, 8): "The Bottleneck Guardian",
    (1, 9): "The Desperate Scout",
    # Industrial Surge (iq=2)
    (2, 0): "The Industrial Victor",
    (2, 1): "The Surge Architect",
    (2, 2): "The Frontier Wanderer",
    (2, 3): "The Conflicted Visionary",
    (2, 4): "The League Enforcer",
    (2, 5): "The Industrial Anomalist",
    (2, 6): "The Unstable Engineer",
    (2, 7): "The Syndicate Reformer",
    (2, 8): "The Pressure Breeder",
    (2, 9): "The Development Scout",
    # Research Ascendant (iq=3)
    (3, 0): "The Champion Scholar",
    (3, 1): "The Research Architect",
    (3, 2): "The Scholar Wanderer",
    (3, 3): "The Deep Visionary",
    (3, 4): "The League Theorist",
    (3, 5): "The Anomaly Researcher",
    (3, 6): "The Contained Scientist",
    (3, 7): "The Reform Scholar",
    (3, 8): "The Gene Theorist",
    (3, 9): "The Data Scout",
    # League Dominated (iq=4)
    (4, 0): "The Dominant Champion",
    (4, 1): "The League Architect",
    (4, 2): "The Circuit Wanderer",
    (4, 3): "The League Visionary",
    (4, 4): "The Established Reformer",
    (4, 5): "The League Anomalist",
    (4, 6): "The Pressured Stabilizer",
    (4, 7): "The League Critic",
    (4, 8): "The Circuit Breeder",
    (4, 9): "The Ranked Scout",
    # Balanced Growth (iq=5)
    (5, 0): "The Measured Champion",
    (5, 1): "The Steady Architect",
    (5, 2): "The Even Wanderer",
    (5, 3): "The Balanced Visionary",
    (5, 4): "The Moderate Reformer",
    (5, 5): "The Equilibrium Anomalist",
    (5, 6): "The Calm Stabilizer",
    (5, 7): "The Balanced Reformer",
    (5, 8): "The Stable Breeder",
    (5, 9): "The Surveyor",
    # Anomaly Destabilized (iq=6)
    (6, 0): "The Rift Champion",
    (6, 1): "The Fractured Architect",
    (6, 2): "The Anomaly Wanderer",
    (6, 3): "The Chaos Visionary",
    (6, 4): "The Anomaly Reformer",
    (6, 5): "The Deep Anomalist",
    (6, 6): "The Rift Walker",
    (6, 7): "The Chaos Reformer",
    (6, 8): "The Mutant Breeder",
    (6, 9): "The Rift Scout",
    # Cultural Fracture (iq=7)
    (7, 0): "The Fractured Victor",
    (7, 1): "The Tension Architect",
    (7, 2): "The Displaced Wanderer",
    (7, 3): "The Cultural Visionary",
    (7, 4): "The Fracture Reformer",
    (7, 5): "The Cultural Anomalist",
    (7, 6): "The Cohesion Stabilizer",
    (7, 7): "The Fracture Mediator",
    (7, 8): "The Cultural Breeder",
    (7, 9): "The Exiled Scout",
    # Genetic Bottleneck (iq=8)
    (8, 0): "The Bottleneck Victor",
    (8, 1): "The Gene Architect",
    (8, 2): "The Strain Wanderer",
    (8, 3): "The Diversity Visionary",
    (8, 4): "The Gene Reformer",
    (8, 5): "The Bottleneck Anomalist",
    (8, 6): "The Diversity Stabilizer",
    (8, 7): "The Genetic Reformer",
    (8, 8): "The Lineage Keeper",
    (8, 9): "The Gene Scout",
    # Frontier Expansion (iq=9)
    (9, 0): "The Frontier Champion",
    (9, 1): "The Expansion Architect",
    (9, 2): "The Pioneer Wanderer",
    (9, 3): "The Frontier Visionary",
    (9, 4): "The Frontier Reformer",
    (9, 5): "The Expansion Anomalist",
    (9, 6): "The Frontier Stabilizer",
    (9, 7): "The Frontier Critic",
    (9, 8): "The Pioneer Breeder",
    (9, 9): "The Trailblazer",
}


def compute_narrative_role(band_id: int) -> str:
    """Return the canonical narrative role label for an outcome band."""
    iq = band_id // 10
    pq = band_id % 10
    return NARRATIVE_OUTCOME_ROLES.get((iq, pq), "The Unnamed")


# ============================================================
# §24  MEMORY ECHO SYSTEM
# ============================================================

@dataclass
class EchoEvent:
    """A subtle recurrence artifact experienced on the current island."""
    node_id:     str
    echo_type:   str   # one of: acoustic | visual | statistical | dialogue
    motif_code:  str   # short identifier, e.g. "E07"
    description: str   # player-facing text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":     self.node_id,
            "echo_type":   self.echo_type,
            "motif_code":  self.motif_code,
            "description": self.description,
        }


# ~30 echo motif templates.  {island_name} and {species_name} are filled at generation.
_ECHO_MOTIF_POOL: List[Dict[str, Any]] = [
    # acoustic
    {"code": "E01", "type": "acoustic",
     "template": "A Neiko call pattern repeats at irregular intervals near this node - "
                 "you've heard this exact sequence before, on a different island."},
    {"code": "E02", "type": "acoustic",
     "template": "The ambient biome tone here has a harmonic undertone that doesn't match "
                 "the surrounding environment. It sounds like a relay carrier wave."},
    {"code": "E03", "type": "acoustic",
     "template": "For a moment you hear your own footsteps a half-second before you take "
                 "the step. A containment field artifact, maybe. Maybe not."},
    {"code": "E04", "type": "acoustic",
     "template": "Static from a nearby relay node resolves briefly into something "
                 "recognizable - a fragment of the same signal you heard on {island_name}."},
    {"code": "E05", "type": "acoustic",
     "template": "A {species_name} nearby produces a vocalization you've catalogued before. "
                 "The exact pitch, duration, sequence. Statistically impossible coincidence."},
    # visual
    {"code": "E06", "type": "visual",
     "template": "The architectural layout of this settlement mirrors a district you passed "
                 "through before - same proportions, same blind alleys. Different island."},
    {"code": "E07", "type": "visual",
     "template": "The boundary markers here are weathered in a pattern identical to ones "
                 "you photographed on {island_name}. Same erosion angle. Same moss placement."},
    {"code": "E08", "type": "visual",
     "template": "A league poster on the wall uses the same graphic template as one you saw "
                 "three islands ago. Not a chain. A distributed instruction."},
    {"code": "E09", "type": "visual",
     "template": "The node's terrain silhouette matches a diagram in Kincaid's field notes "
                 "almost exactly. Either he was here, or someone mapped it for him."},
    {"code": "E10", "type": "visual",
     "template": "You notice the {species_name} population density here clusters in the "
                 "same compass-bearing arrangement as on a previous island. Not coincidence."},
    {"code": "E11", "type": "visual",
     "template": "Two facility structures here are positioned relative to each other at an "
                 "angle you've seen before - 17 degrees off cardinal north. Always 17."},
    # statistical
    {"code": "E12", "type": "statistical",
     "template": "The league ranking of the regional gym leader here is within 3 points "
                 "of the equivalent position on your last island. The system self-balances."},
    {"code": "E13", "type": "statistical",
     "template": "You've encountered {species_name} at this exact level range on "
                 "three separate islands now. The containment field normalizes level curves."},
    {"code": "E14", "type": "statistical",
     "template": "Ecological balance reading here: {echo_val:.1f}. Your last island's "
                 "same-tier reading: within 2 points. PROJECT HUNDRED sites converge."},
    {"code": "E15", "type": "statistical",
     "template": "Gate threshold here is set to a value you've seen before - exactly. "
                 "Distributed configuration. Same team, same numbers, different islands."},
    {"code": "E16", "type": "statistical",
     "template": "Faction influence scores here follow a distribution your records show "
                 "across five previous sites. The pattern is too regular to be ecological."},
    {"code": "E17", "type": "statistical",
     "template": "Anomaly zone count on this island matches {island_name} exactly. "
                 "Ilyanova's containment thesis predicted this: anomaly density is fixed."},
    # dialogue
    {"code": "E18", "type": "dialogue",
     "template": "An NPC here uses a phrase you've only heard once before - from the "
                 "hidden knower on {island_name}. Word for word. They've been briefed."},
    {"code": "E19", "type": "dialogue",
     "template": "A trainer mentions a league incident using the same words as a trainer "
                 "three islands back. Not a rumor spreading. A script being followed."},
    {"code": "E20", "type": "dialogue",
     "template": "The gym leader here deflects your question with the exact phrase: "
                 "'Some things are outside regional jurisdiction.' You've heard that before."},
    {"code": "E21", "type": "dialogue",
     "template": "A child near the settlement describes a dream about 'numbers in the sky.' "
                 "The same description appeared in a researcher's log on {island_name}."},
    {"code": "E22", "type": "dialogue",
     "template": "Someone here references 'the survey team from before.' Every island "
                 "has had a survey team. None of the locals remember when they came."},
    {"code": "E23", "type": "dialogue",
     "template": "An NPC corrects themselves mid-sentence, switching from a place name "
                 "you don't recognize to '{island_name}.' They don't acknowledge the slip."},
    # anomaly-specific
    {"code": "E24", "type": "acoustic",
     "template": "Deep in this anomaly zone, a sub-harmonic pulse repeats on a 47-second "
                 "interval. Relay network maintenance cycle. You've timed it before."},
    {"code": "E25", "type": "visual",
     "template": "The mutation coloration on the creatures clustering near this anomaly "
                 "zone matches a pattern from your records. Same clade, different island."},
    {"code": "E26", "type": "statistical",
     "template": "Instability reading at this node: elevated by exactly the margin "
                 "Voss predicted in the original containment variance model."},
    {"code": "E27", "type": "dialogue",
     "template": "A researcher passing through mentions 'the echo problem.' When you ask, "
                 "they stop talking and change the subject. You've seen that response before."},
    {"code": "E28", "type": "visual",
     "template": "The relay node configuration here is a mirror image of one on {island_name}. "
                 "Not a copy - a reflection. As if the islands are paired."},
    {"code": "E29", "type": "acoustic",
     "template": "At this exact time of day, the ambient signal from the relay network "
                 "carries a harmonic you've catalogued from two other PROJECT HUNDRED sites."},
    {"code": "E30", "type": "statistical",
     "template": "Species diversity index here: {echo_val:.2f}. You've seen this number "
                 "before. Exact same figure. The containment system targets a fixed value."},
]


def generate_echo_events(
    profile: "BehavioralProfileSignature",
    topology: "IslandTopology",
    seed: int,
) -> List[EchoEvent]:
    """
    Generate memory echo events for this island based on prior-run history.
    Returns an empty list if run_count < 2 (no history yet).

    Each echo is placed at a specific node so _cmd_explore() can surface it.
    """
    if profile is None or profile.run_count < 2:
        return []

    rng = SeededRNG(seed).fork("echo_events")

    # Number of echoes scales with run count, capped at 8
    count = min(8, 2 + (profile.run_count - 1))

    # Build context for template substitution
    prior_seed = profile.echo_seeds[-1] if profile.echo_seeds else seed - 1
    prior_topo_rng = SeededRNG(prior_seed).fork("island_name")
    # Derive prior island name deterministically from that seed
    _ISLAND_NAME_FRAGMENTS = [
        "Gomar", "Umhelda", "Velthi", "Karos", "Nessa", "Drevin", "Orvai",
        "Thelun", "Bresca", "Morveth", "Ilaen", "Sunva", "Craleth", "Doven",
    ]
    prior_island_name = _ISLAND_NAME_FRAGMENTS[
        prior_topo_rng.randint(0, len(_ISLAND_NAME_FRAGMENTS) - 1)
    ]

    # Pick a sample species for substitution
    all_nodes = list(topology.nodes.values())
    sample_species_name = "the creature"  # overridden below if possible

    # Get a node list to place echoes on
    # Prefer non-start, non-PATH nodes for interesting placement
    candidate_nodes = [
        nid for nid, nd in topology.nodes.items()
        if nd.node_type.name not in ("PATH",) and nid != topology.start_node_id
    ]
    if not candidate_nodes:
        candidate_nodes = list(topology.nodes.keys())
    rng.shuffle(candidate_nodes)

    # Motif selection - bias toward the player's history
    motif_pool = list(_ECHO_MOTIF_POOL)
    # If high anomaly history, weight anomaly motifs higher
    if profile.anomaly_engagement_history > 20:
        motif_pool = motif_pool + [
            m for m in _ECHO_MOTIF_POOL if m["code"] in ("E24", "E25", "E26", "E27", "E28", "E29")
        ]

    rng.shuffle(motif_pool)
    selected_motifs = motif_pool[:count]

    # Ecological value for {echo_val} substitution
    echo_val = 40.0 + rng.uniform(-15.0, 15.0)

    events: List[EchoEvent] = []
    for i, motif in enumerate(selected_motifs):
        node_id = candidate_nodes[i % len(candidate_nodes)]
        tmpl = motif["template"]
        try:
            desc = tmpl.format(
                island_name=prior_island_name,
                species_name=sample_species_name,
                echo_val=echo_val,
            )
        except KeyError:
            desc = tmpl
        events.append(EchoEvent(
            node_id=node_id,
            echo_type=motif["type"],
            motif_code=motif["code"],
            description=desc,
        ))

    _dbg(f"Generated {len(events)} echo events (run #{profile.run_count})")
    return events


# ============================================================
# §25  NARRATIVE FRAGMENT SYSTEM
# ============================================================

class FragmentType(Enum):
    REDACTED_LOG         = auto()
    STATISTICAL_SUMMARY  = auto()
    RESEARCH_NOTE        = auto()
    SPECIES_REGISTRY_GLITCH = auto()
    AUDIO_ARTIFACT       = auto()


@dataclass
class NarrativeFragment:
    """A discoverable piece of PROJECT HUNDRED documentary evidence."""
    fragment_id:      str
    ftype:            FragmentType
    title:            str
    body_template:    str          # {founder}, {island_name}, {tier}, {species_name} slots
    mountain_code:    str          # which GLOBAL_MOUNTAIN this belongs to
    unlock_condition: Dict[str, float]  # trajectory key → minimum value

    def render(self, context: Dict[str, Any]) -> str:
        """Render body_template with the given context dict."""
        try:
            return self.body_template.format(**context)
        except KeyError:
            return self.body_template

    def render_title(self, context: Dict[str, Any]) -> str:
        """Render title template with the given context dict."""
        try:
            return self.title.format(**context)
        except KeyError:
            return self.title

    def to_dict(self, rendered_body: Optional[str] = None, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rendered_title = self.render_title(context) if context else self.title
        return {
            "fragment_id":     self.fragment_id,
            "type":            self.ftype.name,
            "title":           rendered_title,
            "body":            rendered_body or self.body_template,
            "mountain_code":   self.mountain_code,
            "unlock_condition": self.unlock_condition,
        }


# ~40 fragments tied to specific mountain codes.
# unlock_condition keys match PlayerTrajectory.to_dict() keys.
FRAGMENT_POOL: List[NarrativeFragment] = [
    # ── M1  Founder Betrayal Fracture ──────────────────────────────────────
    NarrativeFragment(
        "F001", FragmentType.REDACTED_LOG,
        "INTERNAL MEMO - REDACTED",
        "[CARTOGRAPHER INTERNAL // EYES ONLY]\n"
        "RE: {founder} - classification review.\n"
        "Following the {island_name} incident, {founder}'s thesis has been "
        "reclassified. All field references are to be updated. Do not discuss "
        "the original containment model with site personnel.\n"
        "[END OF ACCESSIBLE SECTION]",
        "M1", {"exploration_depth": 20.0},
    ),
    NarrativeFragment(
        "F002", FragmentType.RESEARCH_NOTE,
        "Handwritten margin note - survey log",
        "Someone has written in the margin of a league survey log:\n"
        "'Ask why {founder}'s name was removed from the founding plaque. "
        "Ask why no one here has heard of PROJECT HUNDRED. Then stop asking.'",
        "M1", {"exploration_depth": 35.0, "anomaly_exposure": 5.0},
    ),
    # ── M2  League Corruption Drift ────────────────────────────────────────
    NarrativeFragment(
        "F003", FragmentType.STATISTICAL_SUMMARY,
        "League Ranking Audit - SITE {island_name}",
        "AUTOMATED AUDIT LOG\n"
        "Site: {island_name} | Tier: {tier}\n"
        "Anomaly: 3 gym leaders hold ratings inconsistent with recorded match history.\n"
        "Variance: +/- {variance:.0f} points unexplained.\n"
        "Resolution: PENDING. Flag forwarded to Regional Authority.\n"
        "Previous flag: 14 months ago. Status: CLOSED (no action).",
        "M2", {"competitive_focus": 30.0},
    ),
    NarrativeFragment(
        "F004", FragmentType.REDACTED_LOG,
        "League Directive 7-C (partial)",
        "[LEAGUE AUTHORITY - RESTRICTED]\n"
        "Directive 7-C: Gym leaders at Tier {tier} sites are reminded that "
        "anomaly-related match outcomes are to be recorded as 'equipment failure' "
        "or 'environmental interference.' Do not use the word 'anomaly' in "
        "official match records.",
        "M2", {"competitive_focus": 45.0},
    ),
    # ── M3  Ecological Collapse Pressure ──────────────────────────────────
    NarrativeFragment(
        "F005", FragmentType.RESEARCH_NOTE,
        "Field Note - Ecological Survey Team",
        "Observation log, {island_name}, {tier} conditions:\n"
        "Wild {species_name} population has declined 34% since last survey. "
        "Cause: unclear. The biome readings don't support natural attrition. "
        "I'm flagging this but I don't expect a response. I've flagged it before.",
        "M3", {"exploration_depth": 25.0},
    ),
    NarrativeFragment(
        "F006", FragmentType.STATISTICAL_SUMMARY,
        "Biome Stability Index - ANOMALOUS READING",
        "SYSTEM ALERT - {island_name}\n"
        "Ecological balance axis: BELOW THRESHOLD\n"
        "Projected collapse timeline: {collapse_ticks} ticks at current rate.\n"
        "Recommended action: NONE FILED\n"
        "Previous identical alert: YES (2 prior instances, both closed without action)",
        "M3", {"exploration_depth": 30.0, "anomaly_exposure": 8.0},
    ),
    # ── M4  Neiko Mutation Instability ────────────────────────────────────
    NarrativeFragment(
        "F007", FragmentType.SPECIES_REGISTRY_GLITCH,
        "Species Registry - CORRUPTED ENTRY",
        "SPECIES: {species_name}\n"
        "ENTRY STATUS: CORRUPTED\n"
        "Recorded type: [DATA MISSING]\n"
        "Mutation variance: EXCEEDS PARAMETER\n"
        "Note appended by {founder}: 'This is not drift. This is response. "
        "The subject is adapting to the boundary, not the biome.'",
        "M4", {"anomaly_exposure": 15.0},
    ),
    NarrativeFragment(
        "F008", FragmentType.RESEARCH_NOTE,
        "Mutation Log - Researcher annotation",
        "Personal log, researcher unidentified:\n"
        "'The {species_name} in Zone 4 has changed its secondary type three times "
        "in recorded history. The registry calls this a classification error. "
        "I've watched it happen. It is not a classification error.'",
        "M4", {"anomaly_exposure": 20.0, "research_investment": 15.0},
    ),
    # ── M5  Hidden Observer Within Island ────────────────────────────────
    NarrativeFragment(
        "F009", FragmentType.REDACTED_LOG,
        "CARTOGRAPHER DISPATCH - EYES ONLY",
        "[CLASSIFIED // {tier} CLEARANCE REQUIRED]\n"
        "Observer asset on {island_name} has reported: subject (player-class) "
        "is exhibiting deviation from expected behavioral parameters.\n"
        "Observation continues. No intervention at this time.",
        "M5", {"exploration_depth": 40.0},
    ),
    NarrativeFragment(
        "F010", FragmentType.AUDIO_ARTIFACT,
        "[AUDIO LOG - PARTIAL RECOVERY]",
        "RECOVERED AUDIO FRAGMENT\n"
        "Source: relay node, {island_name}\n"
        "Duration: 11 seconds (partial)\n"
        "Transcript: '...they don't know we're watching. The point isn't "
        "containment - the point is to see what they do when they think "
        "they're free. {founder} understood that. That's why-'\n"
        "[SIGNAL LOST]",
        "M5", {"anomaly_exposure": 10.0, "exploration_depth": 30.0},
    ),
    # ── M6  Memory Archive Degradation ───────────────────────────────────
    NarrativeFragment(
        "F011", FragmentType.REDACTED_LOG,
        "Archive Access Log - {island_name} FACILITY",
        "ARCHIVE STATUS REPORT\n"
        "Pre-PROJECT records: 78% degraded\n"
        "Cause: [REDACTED]\n"
        "Note from {founder}: 'The degradation is not random. The files "
        "that survive are the ones they want to survive. The ones that "
        "don't - ask yourself what they had in common.'",
        "M6", {"research_investment": 25.0},
    ),
    NarrativeFragment(
        "F012", FragmentType.RESEARCH_NOTE,
        "Researcher's personal note - FACILITY node",
        "I found three versions of the founding charter in the archive today. "
        "Same document, three different texts. {founder}'s name appears in "
        "all three, but with a different role each time.\n"
        "I'm keeping copies of all three. I'm not sure where.",
        "M6", {"research_investment": 30.0, "exploration_depth": 20.0},
    ),
    # ── M7  Containment Breach Attempt ───────────────────────────────────
    NarrativeFragment(
        "F013", FragmentType.STATISTICAL_SUMMARY,
        "INCIDENT REPORT - {island_name} - CLASSIFIED",
        "INCIDENT TYPE: Boundary test (attempted)\n"
        "Date: [REDACTED]\n"
        "Summary: Subject reached island boundary perimeter and attempted "
        "to cross. Containment field response: nominal. Subject returned "
        "to interior without apparent awareness of the boundary.\n"
        "Classification: ROUTINE",
        "M7", {"anomaly_exposure": 20.0, "exploration_depth": 45.0},
    ),
    NarrativeFragment(
        "F014", FragmentType.AUDIO_ARTIFACT,
        "[RECOVERED AUDIO - ANOMALY ZONE]",
        "AUDIO RECOVERY - {island_name}, anomaly zone\n"
        "Timestamp: unknown\n"
        "Transcript: '...I reached the edge. There's a wall you can't see. "
        "I kept walking and I was back at the start. The Neikos didn't follow. "
        "They knew not to. They've always known-'\n"
        "[RECORDING ENDS]",
        "M7", {"anomaly_exposure": 25.0},
    ),
    # ── M8  Silent Cartographer Loyalist ─────────────────────────────────
    NarrativeFragment(
        "F015", FragmentType.REDACTED_LOG,
        "LOYALTY ASSESSMENT - SITE PERSONNEL",
        "[CARTOGRAPHER INTERNAL]\n"
        "Site: {island_name} | Assessment cycle: {tier}\n"
        "Personnel flagged for re-briefing: 2\n"
        "Reason: unauthorized discussion of PROJECT origin\n"
        "Action taken: standard re-orientation protocol\n"
        "Note: No action required for {founder}-aligned personnel. "
        "Their compliance is structural.",
        "M8", {"exploration_depth": 30.0, "competitive_focus": 20.0},
    ),
    # ── M9  Failed Rebellion Myth ─────────────────────────────────────────
    NarrativeFragment(
        "F016", FragmentType.RESEARCH_NOTE,
        "Oral history transcription - elderly resident",
        "Transcript from {island_name} community archive:\n"
        "'There was a time when people left. Not just traveled - left. "
        "Gone past the WILD ZONES and not come back. We called them the "
        "Walkers. The league called them missing. The records call them "
        "nothing. They were never in the records to begin with.'",
        "M9", {"exploration_depth": 50.0},
    ),
    # ── M10 False History Rewrite ────────────────────────────────────────
    NarrativeFragment(
        "F017", FragmentType.SPECIES_REGISTRY_GLITCH,
        "Registry Entry - DATE CONFLICT",
        "SPECIES: {species_name}\n"
        "First recorded on {island_name}: [DATE A]\n"
        "Cross-reference with regional archive: First recorded: [DATE B]\n"
        "DATE A precedes DATE B by 14 years.\n"
        "Resolution: DATE A suppressed pending review.\n"
        "Note: DATE A is consistent with pre-PROJECT documentation.",
        "M10", {"research_investment": 20.0},
    ),
    NarrativeFragment(
        "F018", FragmentType.REDACTED_LOG,
        "HISTORICAL RECORD - AMENDMENT NOTICE",
        "[LEAGUE AUTHORITY - ADMINISTRATIVE]\n"
        "The founding date of the {island_name} league circuit has been "
        "updated to reflect corrected archival data.\n"
        "Previous date: [REDACTED]\n"
        "Amended date: 23 years after PROJECT HUNDRED Site Survey.\n"
        "All references to the previous date are to be treated as "
        "transcription error.",
        "M10", {"research_investment": 25.0, "competitive_focus": 15.0},
    ),
    # ── M13 Neiko Sentience Spike ────────────────────────────────────────
    NarrativeFragment(
        "F019", FragmentType.RESEARCH_NOTE,
        "Observation log - behavioral anomaly",
        "Subject: {species_name} cluster, {island_name}\n"
        "The cluster spent 40 minutes arranged in a geometric pattern "
        "near the relay node. When the relay cycled, they dispersed. "
        "When it cycled again, they reformed - same pattern, same positions.\n"
        "I don't have a framework for what I watched.",
        "M13", {"anomaly_exposure": 18.0, "research_investment": 10.0},
    ),
    NarrativeFragment(
        "F020", FragmentType.AUDIO_ARTIFACT,
        "[AUDIO - CREATURE VOCALIZATION - FLAGGED]",
        "FLAGGED AUDIO - {island_name}\n"
        "Captured near anomaly zone.\n"
        "Signal analysis: {species_name} vocalization contains structured "
        "sub-harmonic layer not present in standard type signature.\n"
        "{founder}'s notation on similar signal (archive, pre-PROJECT): "
        "'They are not communicating with each other. They are communicating "
        "with something else.'",
        "M13", {"anomaly_exposure": 22.0},
    ),
    # ── M14 Anomaly Zone Expanding ───────────────────────────────────────
    NarrativeFragment(
        "F021", FragmentType.STATISTICAL_SUMMARY,
        "ANOMALY ZONE SURVEY - {island_name}",
        "SURVEY RESULTS\n"
        "Anomaly zones mapped: {anomaly_count}\n"
        "Variance from baseline model: +{variance:.0f}%\n"
        "Trend: EXPANDING\n"
        "Projected boundary in {collapse_ticks} ticks: adjacent to SETTLEMENT nodes\n"
        "League notification: PENDING (filed {tier})\n"
        "Previous identical survey: filed, no response.",
        "M14", {"anomaly_exposure": 15.0, "exploration_depth": 35.0},
    ),
    # ── M15 Relay Node (formerly Anchor) ─────────────────────────────────
    NarrativeFragment(
        "F022", FragmentType.REDACTED_LOG,
        "RELAY NODE TECHNICAL SPECIFICATION - PARTIAL",
        "[CARTOGRAPHER INFRASTRUCTURE // RESTRICTED]\n"
        "Relay node function: passive telemetry collection, field boundary "
        "maintenance, behavioral signal logging.\n"
        "Secondary function: [REDACTED]\n"
        "Tertiary function: [REDACTED]\n"
        "Note from {founder}: 'Call them relay nodes. Don't call them what "
        "they actually are. The word will mean something to people who "
        "know the old literature.'",
        "M15", {"anomaly_exposure": 5.0},
    ),
    NarrativeFragment(
        "F023", FragmentType.AUDIO_ARTIFACT,
        "[RELAY NODE AMBIENT CAPTURE]",
        "AMBIENT CAPTURE - relay node, {island_name}\n"
        "Background signal (always present, below threshold of notice):\n"
        "Pulse interval: 47 seconds\n"
        "Encoded in pulse: [access restricted to {tier} clearance]\n"
        "Plain interpretation: this node has been logging your position "
        "since you arrived on the island.",
        "M15", {"anomaly_exposure": 20.0, "exploration_depth": 25.0},
    ),
    # ── M16 Player Profile Flagged as Outlier ────────────────────────────
    NarrativeFragment(
        "F024", FragmentType.REDACTED_LOG,
        "SUBJECT BEHAVIORAL REPORT - FLAGGED",
        "[CARTOGRAPHER OBSERVATION LOG]\n"
        "Subject ID: [REDACTED]\n"
        "Site: {island_name} | Tier: {tier}\n"
        "Status: OUTLIER - behavioral axis deviates from site baseline\n"
        "Deviation type: excessive cross-system engagement\n"
        "Note: Subject is exploring systems that typical subjects do not "
        "engage with. This is notable. Continue passive observation.",
        "M16", {"exploration_depth": 40.0, "research_investment": 20.0},
    ),
    NarrativeFragment(
        "F025", FragmentType.STATISTICAL_SUMMARY,
        "BEHAVIORAL VARIANCE REPORT - {island_name}",
        "AUTOMATED BEHAVIORAL ANALYSIS\n"
        "Subject behavioral axis: OUTLIER DETECTED\n"
        "Comparison baseline: 847 prior subjects, same site tier\n"
        "Variance: top 2.3%\n"
        "Flag: ACTIVE\n"
        "Action: passive documentation\n"
        "Note from {founder}'s original framework: 'The outliers are the "
        "point. The rest is statistical noise.'",
        "M16", {"exploration_depth": 50.0, "anomaly_exposure": 10.0},
    ),
    # ── M17 Ecological Over-Optimization ────────────────────────────────
    NarrativeFragment(
        "F026", FragmentType.RESEARCH_NOTE,
        "Ecological audit note - {island_name}",
        "Something is wrong with the balance here.\n"
        "The ecosystem is too stable. No natural system maintains this "
        "equilibrium under the pressures present on {island_name}.\n"
        "Either something is managing it - or something managed it once, "
        "and the pattern hasn't decayed yet. {founder} wrote about this "
        "in the original PROJECT documentation. The word used was 'sculpted.'",
        "M17", {"exploration_depth": 30.0, "research_investment": 20.0},
    ),
    # ── M19 Fragmented Founder Recording ────────────────────────────────
    NarrativeFragment(
        "F027", FragmentType.AUDIO_ARTIFACT,
        "[FOUNDER RECORDING - FRAGMENT]",
        "RECOVERED AUDIO - source unknown\n"
        "Voice analysis: consistent with {founder}\n"
        "Transcript:\n"
        "'We built PROJECT HUNDRED because we wanted to know what a closed "
        "system produces when left alone. What we got instead was - the "
        "system isn't closed. It never was. The subjects know the walls are "
        "there. They've always known. They just can't prove it yet.'\n"
        "[FRAGMENT ENDS]",
        "M19", {"research_investment": 30.0, "anomaly_exposure": 10.0},
    ),
    NarrativeFragment(
        "F028", FragmentType.AUDIO_ARTIFACT,
        "[FOUNDER RECORDING - FRAGMENT 2]",
        "RECOVERED AUDIO - relay node archive, {island_name}\n"
        "Voice: {founder}\n"
        "Transcript:\n"
        "'The {species_name} near Site {island_name} responded to the "
        "containment field adjustment before we made it. Not after. Before. "
        "I've reviewed the timestamps four times. I don't know what to do "
        "with this information. I don't know who to tell.'\n"
        "[ENDS]",
        "M19", {"anomaly_exposure": 25.0, "research_investment": 20.0},
    ),
    # ── M20 Genetic Drift Beyond Parameters ─────────────────────────────
    NarrativeFragment(
        "F029", FragmentType.SPECIES_REGISTRY_GLITCH,
        "Registry INTEGRITY ERROR - {species_name}",
        "INTEGRITY CHECK FAILED\n"
        "Species: {species_name}\n"
        "Recorded genome baseline: [FILE NOT FOUND]\n"
        "Current observed genome: DEVIATION EXCEEDS MODEL BOUNDS\n"
        "System note: this species no longer matches its own registry entry.\n"
        "Researcher annotation ({founder}): 'Of course it doesn't. We "
        "made the model from the original population. That population "
        "no longer exists. We've been logging ghosts.'",
        "M20", {"research_investment": 15.0, "anomaly_exposure": 12.0},
    ),
    NarrativeFragment(
        "F030", FragmentType.STATISTICAL_SUMMARY,
        "Genetic Drift Report - {island_name}",
        "POPULATION GENETICS SUMMARY\n"
        "Site: {island_name} | Tier: {tier}\n"
        "Species sampled: {species_name}\n"
        "Observed drift rate: EXCEEDS CONTAINMENT MODEL PARAMETERS\n"
        "Possible causes: mutation instability, anomaly field interaction, "
        "deliberate modification [LAST OPTION: NOT TO BE FILED]\n"
        "Recommendation: extended monitoring\n"
        "Previous recommendation: extended monitoring (×6)",
        "M20", {"research_investment": 20.0},
    ),
    # ── Relay-node specific ──────────────────────────────────────────────
    NarrativeFragment(
        "F031", FragmentType.AUDIO_ARTIFACT,
        "[RELAY INTERCEPT - AUTOMATED LOG]",
        "RELAY INTERCEPT - {island_name}\n"
        "Origin: unknown node\n"
        "Content: behavioral telemetry, subject unidentified\n"
        "Note: This relay node has been transmitting continuously since "
        "installation. Every trainer, every traveler, every researcher "
        "who passed within range: logged. Their routes: logged. "
        "Their decisions: logged. You: logged.",
        "M15", {"anomaly_exposure": 30.0},
    ),
    # ── Early-discovery fragments (low/zero threshold) ───────────────────
    # These fire on the first LANDMARK/DUNGEON the player visits.
    # They introduce lore without requiring trajectory investment.
    NarrativeFragment(
        "F042", FragmentType.STATISTICAL_SUMMARY,
        "League Survey Marker - {island_name}",
        "LEAGUE CIRCUIT ADMINISTRATIVE NOTICE\n"
        "Site: {island_name} | Classification: {tier}\n"
        "Survey marker placed. Purpose: standard trainer route logging.\n"
        "Secondary data collected: [see attached - RESTRICTED]\n"
        "Note for field teams: do not discuss secondary data collection "
        "with site personnel or visiting trainers. Standard cover language: "
        "'routine administrative survey.'",
        "M2", {},
    ),
    NarrativeFragment(
        "F043", FragmentType.RESEARCH_NOTE,
        "Field observation - landmark vicinity",
        "Something about this place feels watched.\n"
        "Not paranoia - I've been to a hundred sites. The Neikos behave "
        "differently near the old markers. They approach, circle, retreat. "
        "Like they recognize something we built as significant, and want us "
        "to know that they recognize it.\n"
        "I put this in my personal log. Not the official one.",
        "M3", {"nodes_explored": 5},
    ),
    # ── General archive fragments ────────────────────────────────────────
    NarrativeFragment(
        "F032", FragmentType.REDACTED_LOG,
        "PROJECT HUNDRED - SITE OVERVIEW (partial)",
        "[CARTOGRAPHER INTERNAL - FOUNDING DOCUMENT]\n"
        "PROJECT HUNDRED: 100 contained ecological sites, each seeded with "
        "a regulated Neiko population and a human community unaware of "
        "the containment structure.\n"
        "Objective: longitudinal behavioral study, minimum 3 generations.\n"
        "Ethics review: [REDACTED]\n"
        "Authorization: {founder}, Director Kincaid, Board vote [REDACTED]",
        "M5", {"research_investment": 35.0, "exploration_depth": 35.0},
    ),
    NarrativeFragment(
        "F033", FragmentType.RESEARCH_NOTE,
        "Note found in abandoned FACILITY node",
        "To whoever finds this:\n"
        "The island is a cage. The league is administration. The Neikos "
        "are not the subjects - you are.\n"
        "{founder} knew this before the project started. The question "
        "isn't whether it's wrong. The question is what you do now that "
        "you know.",
        "M1", {"exploration_depth": 55.0},
    ),
    NarrativeFragment(
        "F034", FragmentType.AUDIO_ARTIFACT,
        "[SIGNAL - UNCLASSIFIED SOURCE]",
        "UNCLASSIFIED SIGNAL - {island_name}\n"
        "Origin: indeterminate\n"
        "Content:\n"
        "'HUNDRED ISLANDS. HUNDRED EXPERIMENTS. ONE QUESTION:\n"
        " what does a mind do when it realizes the walls are real?\n"
        " {founder} said: it adapts.\n"
        " We said: let's find out.'\n"
        "[SIGNAL ENDS]",
        "M5", {"anomaly_exposure": 20.0, "exploration_depth": 45.0},
    ),
    NarrativeFragment(
        "F035", FragmentType.STATISTICAL_SUMMARY,
        "CROSS-SITE CORRELATION REPORT",
        "AUTOMATED ANALYSIS - MULTI-SITE\n"
        "Correlation detected: behavioral patterns across 14 PROJECT sites "
        "show convergence toward identical outcome bands over time.\n"
        "Interpretation A: containment conditions produce uniform behavior.\n"
        "Interpretation B: subjects are responding to each other through "
        "the relay network.\n"
        "Note: Interpretation B was removed from this report by "
        "editorial review. It appears here because this is a relay-node "
        "cached copy that was not updated.",
        "M16", {"research_investment": 40.0, "anomaly_exposure": 15.0},
    ),
    NarrativeFragment(
        "F036", FragmentType.REDACTED_LOG,
        "CONTAINMENT PARAMETER ADJUSTMENT - NOTICE",
        "[CARTOGRAPHER OPERATIONS]\n"
        "Site: {island_name}\n"
        "Adjustment type: Tier escalation ({tier})\n"
        "Effect on population: behavioral stress increase, expected\n"
        "Effect on Neiko: mutation rate increase, expected\n"
        "Rationale: stress-response data collection\n"
        "Resident awareness: none intended\n"
        "Note: {founder} opposed this methodology. The record shows "
        "this note was filed. The adjustment proceeded anyway.",
        "M7", {"anomaly_exposure": 18.0, "exploration_depth": 30.0},
    ),
    NarrativeFragment(
        "F037", FragmentType.RESEARCH_NOTE,
        "Field log - ANOMALY ZONE boundary",
        "Standing at the ANOMALY ZONE boundary on {island_name}.\n"
        "The {species_name} don't cross this line. Not because they can't - "
        "I've seen them approach and turn back. Something in the relay "
        "signal discourages them from entering this zone.\n"
        "Or something in this zone discourages them from leaving.\n"
        "I'm not sure which direction the boundary faces.",
        "M14", {"anomaly_exposure": 22.0, "exploration_depth": 40.0},
    ),
    NarrativeFragment(
        "F038", FragmentType.AUDIO_ARTIFACT,
        "[AUDIO - ARCHIVED DEBRIEF - PARTIAL]",
        "ARCHIVAL DEBRIEF - researcher exit interview\n"
        "Site: {island_name} | Year: [REDACTED]\n"
        "Transcript (partial):\n"
        "'...the part that stays with me is the Neikos near the relay "
        "nodes. They weren't afraid of it. They were - attentive. Like "
        "they were waiting for something. After three years on {island_name} "
        "I started to feel the same way. Waiting. I don't know for what.'\n"
        "[DEBRIEF CONTINUES - ACCESS RESTRICTED]",
        "M13", {"research_investment": 25.0, "anomaly_exposure": 15.0},
    ),
    NarrativeFragment(
        "F039", FragmentType.SPECIES_REGISTRY_GLITCH,
        "Registry - DUPLICATE ENTRY DETECTED",
        "DUPLICATE ENTRY ALERT\n"
        "Species {species_name} appears in registry under two classifications:\n"
        "Classification A: standard ecological documentation\n"
        "Classification B: [ACCESS RESTRICTED - {tier} CLEARANCE]\n"
        "Note: Classification B was created 3 years after Classification A.\n"
        "Note: Classification B contains {founder}'s original field data.\n"
        "Note: Classification A was created to replace it.",
        "M10", {"research_investment": 30.0},
    ),
    NarrativeFragment(
        "F040", FragmentType.REDACTED_LOG,
        "END-OF-CYCLE SUMMARY - {island_name}",
        "[CARTOGRAPHER - CYCLE CLOSE REPORT]\n"
        "Island: {island_name} | Final Tier: {tier}\n"
        "Subject behavioral profile: ARCHIVED\n"
        "Neiko mutation variance: ARCHIVED\n"
        "Anomaly zone data: ARCHIVED\n"
        "Resident awareness of PROJECT: [MEASUREMENT OMITTED]\n"
        "Recommendation for next cycle: standard re-seeding\n"
        "Note filed by {founder}: 'We keep archiving. We keep re-seeding. "
        "At what point does the experiment become the condition?'",
        "M5", {"exploration_depth": 60.0, "research_investment": 40.0},
    ),
]


def generate_island_fragments(
    narrative_profile: "IslandNarrativeProfile",
    topology: "IslandTopology",
    seed: int,
) -> List[NarrativeFragment]:
    """
    Select the subset of FRAGMENT_POOL relevant to this island's active
    mountain codes.  Returns fragments in deterministic order.
    """
    active_mountains = set(
        narrative_profile.primary_global_boulders
        + narrative_profile.secondary_global_boulders
    )

    # Filter pool to fragments whose mountain_code is active on this island
    relevant = [f for f in FRAGMENT_POOL if f.mountain_code in active_mountains]

    # Shuffle deterministically, then keep up to 20 fragments per island
    rng = SeededRNG(seed).fork("fragment_selection")
    items = list(relevant)
    rng.shuffle(items)
    return items[:20]


# ============================================================
# §26  SUBLOCATION SYSTEM  (R-Unit spatial puck layout)
# ============================================================
#
# The R-Unit is a central console players carry between map nodes.
# Each node has up to N sublocations spread across pages of ≤4.
# Pucks (speakers / mics) physically placed around the room map to
# page slots 1-4.  The R-Unit is always slot 0 - the entrance/exit
# that takes the player to the *next* node or back to the last.
#
# Sublocation taxonomy follows the Mountains-and-Boulders pattern
# used in the narrative system (§20):
#
#   Mountain (SL-M*)    - top-level spatial category, derived from NodeType
#   Boulder  (SL-B*)    - specific sublocation archetype within the mountain
#
# Mountain ↔ NodeType mapping
# ────────────────────────────
#   SL-M1  SOCIAL       ← SETTLEMENT, CITY
#   SL-M2  WILDS        ← WILD_ZONE, ANOMALY_ZONE
#   SL-M3  STRUCTURE    ← FACILITY, DUNGEON
#   SL-M4  TRANSIT      ← PATH
#   SL-M5  LANDMARK     ← LANDMARK
#   SL-M6  DEEP_SITE    ← INTERIOR_DEPTH nodes, relay nodes
#
# Within each mountain the biome vector, region, and rarity-weighted
# roll determine which boulder archetype spawns in each puck slot.
# ─────────────────────────────────────────────────────────────────────────────

class SlMountain(Enum):
    SOCIAL    = "SL-M1"
    WILDS     = "SL-M2"
    STRUCTURE = "SL-M3"
    TRANSIT   = "SL-M4"
    LANDMARK  = "SL-M5"
    DEEP_SITE = "SL-M6"


# Sublocation boulder archetypes - (code, label, description_template, mountain)
# description_template may use {island_name}, {node_name}, {type_name}, {region}
_SUBLOCATION_BOULDERS: List[Tuple[str, str, str, SlMountain]] = [
    # ── SL-M1  SOCIAL ────────────────────────────────────
    ("SL-B101", "Market Stall",
     "A vendor row in {node_name}. Goods change with the ledger.",
     SlMountain.SOCIAL),
    ("SL-B102", "Inn Common Room",
     "The gathering place in {node_name}. NPCs share routes and rumours.",
     SlMountain.SOCIAL),
    ("SL-B103", "Research Noticeboard",
     "Pinboard outside the {node_name} league office. Bounties and briefs.",
     SlMountain.SOCIAL),
    ("SL-B104", "Trainer Pit",
     "An informal challenge pit. Trainers queue for quick bouts.",
     SlMountain.SOCIAL),
    ("SL-B105", "Archive Anteroom",
     "Filing room adjacent to a {node_name} civic building. Old maps on shelves.",
     SlMountain.SOCIAL),
    ("SL-B106", "Faction Liaison Post",
     "A faction representative sits here when {node_name} is active.",
     SlMountain.SOCIAL),
    ("SL-B107", "Breeding Pen",
     "A supervised pen where registered creatures can be paired.",
     SlMountain.SOCIAL),
    ("SL-B108", "Port Dock",
     "The sea-facing dock of {node_name}. Sub-islet ferries depart here.",
     SlMountain.SOCIAL),
    # ── SL-M2  WILDS ─────────────────────────────────────
    ("SL-B201", "Clearing",
     "Open ground in the {region} belt. Weak encounters; good foraging.",
     SlMountain.WILDS),
    ("SL-B202", "Dense Undergrowth",
     "Thick growth near {node_name}. Mid-tier creatures shelter here.",
     SlMountain.WILDS),
    ("SL-B203", "Watering Hole",
     "A natural pool in the {region}. Multiple type affinities overlap.",
     SlMountain.WILDS),
    ("SL-B204", "Rock Shelf",
     "Elevated stone shelf at the edge of {node_name}. STONE/FROST affinity.",
     SlMountain.WILDS),
    ("SL-B205", "Anomaly Seam",
     "A fault in the containment field. RIFT/ANOMALY creatures surface here.",
     SlMountain.WILDS),
    ("SL-B206", "Root Hollow",
     "A cave under the root system of the {region} canopy. SHADE affinity.",
     SlMountain.WILDS),
    ("SL-B207", "Shoreline Shelf",
     "Tide-pool edge. TIDE/TORRENT creatures forage at low tide.",
     SlMountain.WILDS),
    ("SL-B208", "Thermal Vent",
     "Ground vent emitting heat. EMBER/RIFT encounters; instability elevated.",
     SlMountain.WILDS),
    # ── SL-M3  STRUCTURE ─────────────────────────────────
    ("SL-B301", "Lab Annex",
     "A side-wing of the {node_name} facility. Research milestone checks.",
     SlMountain.STRUCTURE),
    ("SL-B302", "Containment Chamber",
     "Sealed room - holds anomaly-tier specimens. Requires relay key.",
     SlMountain.STRUCTURE),
    ("SL-B303", "Server Room",
     "Archival hardware still running. Fragment discovery probability high.",
     SlMountain.STRUCTURE),
    ("SL-B304", "Dungeon Antechamber",
     "The first lit hall before the dungeon proper. Trainers camp here.",
     SlMountain.STRUCTURE),
    ("SL-B305", "Excavation Pit",
     "Active dig beneath {node_name}. STONE/ALLOY encounters; gate check.",
     SlMountain.STRUCTURE),
    ("SL-B306", "Generator Block",
     "Power plant section. ALLOY/PULSE affinity; overclocked status common.",
     SlMountain.STRUCTURE),
    ("SL-B307", "Specimen Vault",
     "Cold storage holding preserved neiko samples. Genetics +1 if inspected.",
     SlMountain.STRUCTURE),
    ("SL-B308", "Monitoring Post",
     "Observation deck overlooking {node_name}. Echo events surface here.",
     SlMountain.STRUCTURE),
    # ── SL-M4  TRANSIT ───────────────────────────────────
    ("SL-B401", "Crossroads",
     "A fork in the {region} path system. Direction clues for traversal.",
     SlMountain.TRANSIT),
    ("SL-B402", "Rest Marker",
     "A stone or post marking a rest point. Fatigue recovery possible.",
     SlMountain.TRANSIT),
    ("SL-B403", "Ambush Hollow",
     "A shadowed dip in the path. Uncommon/Rare creatures stalk here.",
     SlMountain.TRANSIT),
    ("SL-B404", "Ridge Overlook",
     "Elevated point on the path. Shows neighboring nodes on the HUD.",
     SlMountain.TRANSIT),
    ("SL-B405", "Stream Crossing",
     "A ford across a stream. TIDE/VERDANT encounter bump.",
     SlMountain.TRANSIT),
    ("SL-B406", "Waypoint Post",
     "Old directional post. Knower dialogue sometimes triggered here.",
     SlMountain.TRANSIT),
    # ── SL-M5  LANDMARK ──────────────────────────────────
    ("SL-B501", "Carved Stele",
     "Stone inscription in {node_name}. Fragment discovery on inspect.",
     SlMountain.LANDMARK),
    ("SL-B502", "Ancient Ruin",
     "Structural remnant. RIFT/STONE encounter affinity; high instability.",
     SlMountain.LANDMARK),
    ("SL-B503", "Observation Platform",
     "Elevated platform at {node_name}. View radius expands; map reveal.",
     SlMountain.LANDMARK),
    ("SL-B504", "Memorial Grove",
     "A ring of marked trees. Knower dialogue unlocks here at tier ≥ III.",
     SlMountain.LANDMARK),
    ("SL-B505", "Signal Beacon",
     "An old transmission tower. Relay-adjacent; echo events amplified.",
     SlMountain.LANDMARK),
    # ── SL-M6  DEEP SITE ─────────────────────────────────
    ("SL-B601", "Relay Core",
     "The active relay node housing - glyphs pulse. Fragment drop chance max.",
     SlMountain.DEEP_SITE),
    ("SL-B602", "Collapsed Tunnel",
     "A partially blocked passage deeper into {node_name}. Anomaly affinity.",
     SlMountain.DEEP_SITE),
    ("SL-B603", "Underground Lake",
     "Dark water beneath {node_name}. TIDE/SHADE apex encounters possible.",
     SlMountain.DEEP_SITE),
    ("SL-B604", "Void Crack",
     "A rift in the geology. RIFT/ANOMALY encounter; instability spikes.",
     SlMountain.DEEP_SITE),
    ("SL-B605", "Field Array Terminal",
     "Cartographer hardware - reads containment field status of the island.",
     SlMountain.DEEP_SITE),
    ("SL-B606", "Echo Chamber",
     "Acoustically sealed room. Memory echoes replay here; knower may appear.",
     SlMountain.DEEP_SITE),
]

# Map each boulder to its mountain for quick lookup
_BOULDER_BY_MOUNTAIN: Dict[SlMountain, List[Tuple[str, str, str, SlMountain]]] = {
    m: [] for m in SlMountain
}
for _b in _SUBLOCATION_BOULDERS:
    _BOULDER_BY_MOUNTAIN[_b[3]].append(_b)


def _node_mountain(node: MapNode) -> SlMountain:
    """Derive the primary spatial mountain category from a MapNode."""
    if node.is_relay_node or node.region == MacroRegion.INTERIOR_DEPTH:
        return SlMountain.DEEP_SITE
    mapping = {
        NodeType.SETTLEMENT:  SlMountain.SOCIAL,
        NodeType.CITY:        SlMountain.SOCIAL,
        NodeType.WILD_ZONE:   SlMountain.WILDS,
        NodeType.ANOMALY_ZONE: SlMountain.WILDS,
        NodeType.FACILITY:    SlMountain.STRUCTURE,
        NodeType.DUNGEON:     SlMountain.STRUCTURE,
        NodeType.PATH:        SlMountain.TRANSIT,
        NodeType.LANDMARK:    SlMountain.LANDMARK,
        NodeType.GATE:        SlMountain.TRANSIT,
    }
    return mapping.get(node.node_type, SlMountain.TRANSIT)


@dataclass
class Sublocation:
    """
    A single puck-addressable sublocation within a MapNode.

    Players visit it by walking to the physical puck in their home.
    The R-Unit (node entrance/exit) is represented separately - it
    never occupies a puck slot.
    """
    sublocation_id: str = ""      # e.g. "sub_sp_0003_p1_s2"
    boulder_code: str = ""        # e.g. "SL-B201"
    mountain: SlMountain = SlMountain.TRANSIT
    label: str = ""               # short display name
    description: str = ""        # rendered from template
    page: int = 1                 # page index (1-based)
    slot: int = 1                 # puck slot within page (1-4)
    node_id: str = ""
    # Dynamic state
    has_encounter: bool = False   # wild encounter possible here
    has_fragment_hint: bool = False  # fragment clue surface point
    has_trainer: bool = False     # AI trainer may challenge here
    has_echo: bool = False        # memory echo fires here (§24)
    is_locked: bool = False       # gate check required to enter


@dataclass
class SubpageLayout:
    """
    The complete spatial layout for one MapNode as experienced through
    the R-Unit puck system.

    Structure:
        r_unit_label  - what the R-Unit announces at this node
        pages         - list of pages; each page is ≤4 Sublocation slots
                        plus a conceptual "access hatch" R-Unit portal
                        pointing to the next path segment.
        exit_labels   - list of (neighbor_node_id, direction_hint) - the
                        R-Unit's available "access hatch" destinations.
    """
    node_id: str = ""
    node_name: str = ""
    mountain: SlMountain = SlMountain.TRANSIT
    r_unit_label: str = ""
    pages: List[List[Sublocation]] = field(default_factory=list)
    exit_labels: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def total_sublocations(self) -> int:
        return sum(len(p) for p in self.pages)

    def all_sublocations(self) -> List[Sublocation]:
        return [s for page in self.pages for s in page]

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "mountain": self.mountain.value,
            "r_unit_label": self.r_unit_label,
            "total_pages": len(self.pages),
            "total_sublocations": self.total_sublocations,
            "exit_count": len(self.exit_labels),
            "pages": [
                [
                    {
                        "sublocation_id": s.sublocation_id,
                        "boulder_code": s.boulder_code,
                        "label": s.label,
                        "description": s.description,
                        "page": s.page,
                        "slot": s.slot,
                        "has_encounter": s.has_encounter,
                        "has_fragment_hint": s.has_fragment_hint,
                        "has_trainer": s.has_trainer,
                        "has_echo": s.has_echo,
                        "is_locked": s.is_locked,
                    }
                    for s in page
                ]
                for page in self.pages
            ],
            "exits": [
                {"neighbor_node_id": nid, "direction_hint": hint}
                for nid, hint in self.exit_labels
            ],
        }


# ── Sublocation count table ────────────────────────────────
# How many puck-addressable sublocations each NodeType generates.
# The R-Unit portal does NOT count toward this number.

_NODE_SUBLOC_COUNT: Dict[NodeType, int] = {
    NodeType.CITY:        8,   # 2 pages × 4 pucks
    NodeType.SETTLEMENT:  4,   # 1 page × 4 pucks
    NodeType.DUNGEON:     8,   # 2 pages × 4 pucks
    NodeType.FACILITY:    8,   # 2 pages × 4 pucks
    NodeType.LANDMARK:    4,   # 1 page × 4 pucks
    NodeType.WILD_ZONE:   4,   # 1 page × 4 pucks
    NodeType.ANOMALY_ZONE: 8,  # 2 pages × 4 pucks (seams)
    NodeType.PATH:        4,   # 1 page × 4 pucks
    NodeType.GATE:        4,
}

# Deep-site override: relay nodes and INTERIOR_DEPTH always get 2 pages
_DEEP_SITE_SUBLOC_COUNT = 8


def _direction_hint(rng: SeededRNG, neighbor_node: MapNode) -> str:
    """Human-readable direction hint for the R-Unit exit label."""
    compass = ["north", "south", "east", "west",
               "north-east", "north-west", "south-east", "south-west"]
    depth_words = {
        NodeType.DUNGEON: "down into",
        NodeType.FACILITY: "through to",
        NodeType.ANOMALY_ZONE: "further into",
        NodeType.CITY: "ahead to",
        NodeType.SETTLEMENT: "toward",
        NodeType.LANDMARK: "over to",
    }
    direction = rng.choice(compass)
    prefix = depth_words.get(neighbor_node.node_type, "toward")
    return f"{prefix} {neighbor_node.name} ({direction})"


def generate_node_sublocations(
    node: MapNode,
    topology: IslandTopology,
    seed: int,
    echo_node_ids: Optional[Set[str]] = None,
    trainer_node_ids: Optional[Set[str]] = None,
) -> SubpageLayout:
    """
    Build the deterministic SubpageLayout for a single MapNode.

    Parameters
    ----------
    node            : the MapNode to populate
    topology        : full island topology (for neighbor lookups)
    seed            : island seed (determinism anchor)
    echo_node_ids   : set of node_ids that have a memory echo placed (§24)
    trainer_node_ids: set of node_ids where an AI trainer is stationed
    """
    rng = SeededRNG(seed).fork(f"subloc_{node.node_id}")

    echo_node_ids = echo_node_ids or set()
    trainer_node_ids = trainer_node_ids or set()

    mountain = _node_mountain(node)

    # Number of sublocations
    if node.is_relay_node or node.region == MacroRegion.INTERIOR_DEPTH:
        n_sublocs = _DEEP_SITE_SUBLOC_COUNT
    else:
        n_sublocs = _NODE_SUBLOC_COUNT.get(node.node_type, 4)

    # Boulder candidates from this mountain, plus 1-2 "crossover" from adjacent
    primary_boulders = list(_BOULDER_BY_MOUNTAIN[mountain])

    # Adjacent mountains: add flavour based on biome
    adjacent: List[SlMountain] = []
    bv = node.biome
    if bv.instability_bias > 0.3:
        adjacent.append(SlMountain.DEEP_SITE)
    if bv.vegetation_density > 0.5:
        adjacent.append(SlMountain.WILDS)
    if bv.elevation > 0.6:
        adjacent.append(SlMountain.LANDMARK)

    crossover_boulders: List[Tuple[str, str, str, SlMountain]] = []
    for adj_m in adjacent:
        crossover_boulders.extend(_BOULDER_BY_MOUNTAIN[adj_m])

    # Build candidate pool: primary first, then crossover (deduped by code)
    seen_codes: Set[str] = set()
    candidates: List[Tuple[str, str, str, SlMountain]] = []
    for b in (primary_boulders + crossover_boulders):
        if b[0] not in seen_codes:
            seen_codes.add(b[0])
            candidates.append(b)

    rng.shuffle(candidates)

    # Assign boulders to slots - allow repeats if we run out of unique boulders
    assigned_boulders: List[Tuple[str, str, str, SlMountain]] = []
    for i in range(n_sublocs):
        assigned_boulders.append(candidates[i % len(candidates)])

    # Render description
    ctx = {
        "island_name": topology.island_name,
        "node_name": node.name,
        "type_name": "",
        "region": node.region.name.replace("_", " ").title(),
    }

    # Build Sublocation objects and distribute across pages
    sublocations: List[Sublocation] = []
    for i, boulder in enumerate(assigned_boulders):
        page = (i // 4) + 1
        slot = (i % 4) + 1
        desc = boulder[2].format(**ctx)
        sub_id = f"sub_{node.node_id}_p{page}_s{slot}"

        # Determine dynamic flags
        # Encounters: wilds-type boulders and anomaly nodes
        has_enc = mountain in (SlMountain.WILDS, SlMountain.DEEP_SITE) or \
                  node.node_type == NodeType.ANOMALY_ZONE
        # Trainer: first slot of first page in SOCIAL / STRUCTURE nodes
        has_trainer_flag = (node.node_id in trainer_node_ids and i == 0)
        # Fragment hint: server rooms, monitoring posts, steles
        fragment_hint_codes = {"SL-B303", "SL-B308", "SL-B501", "SL-B601", "SL-B605"}
        has_frag_hint = boulder[0] in fragment_hint_codes
        # Echo: echoes fire at the sublocation matching the node's echo
        has_echo_flag = (node.node_id in echo_node_ids and slot == 1 and page == 1)
        # Locked: containment chamber and relay core require progression
        is_locked_flag = boulder[0] in {"SL-B302", "SL-B601"} and node.gate is not None

        sub = Sublocation(
            sublocation_id=sub_id,
            boulder_code=boulder[0],
            mountain=boulder[3],
            label=boulder[1],
            description=desc,
            page=page,
            slot=slot,
            node_id=node.node_id,
            has_encounter=has_enc,
            has_fragment_hint=has_frag_hint,
            has_trainer=has_trainer_flag,
            has_echo=has_echo_flag,
            is_locked=is_locked_flag,
        )
        sublocations.append(sub)

    # Group into pages (≤4 per page)
    page_dict: Dict[int, List[Sublocation]] = {}
    for sub in sublocations:
        page_dict.setdefault(sub.page, []).append(sub)
    pages = [page_dict[p] for p in sorted(page_dict.keys())]

    # R-Unit label
    r_unit_labels = {
        SlMountain.SOCIAL:    f"R-Unit @ {node.name}  ·  Social district",
        SlMountain.WILDS:     f"R-Unit @ {node.name}  ·  Wild zone entrance",
        SlMountain.STRUCTURE: f"R-Unit @ {node.name}  ·  Facility access point",
        SlMountain.TRANSIT:   f"R-Unit @ {node.name}  ·  Path junction",
        SlMountain.LANDMARK:  f"R-Unit @ {node.name}  ·  Landmark approach",
        SlMountain.DEEP_SITE: f"R-Unit @ {node.name}  ·  Deep site terminal",
    }
    r_unit_label = r_unit_labels[mountain]

    # Exit labels - neighbors reachable from the R-Unit "access hatch"
    exit_rng = rng.fork("exits")
    exit_labels: List[Tuple[str, str]] = []
    for nb_id in node.neighbors:
        nb_node = topology.nodes.get(nb_id)
        if nb_node:
            hint = _direction_hint(exit_rng, nb_node)
            exit_labels.append((nb_id, hint))

    return SubpageLayout(
        node_id=node.node_id,
        node_name=node.name,
        mountain=mountain,
        r_unit_label=r_unit_label,
        pages=pages,
        exit_labels=exit_labels,
    )


def generate_world_sublocations(
    topology: IslandTopology,
    seed: int,
    echo_node_ids: Optional[Set[str]] = None,
    trainer_node_ids: Optional[Set[str]] = None,
) -> Dict[str, SubpageLayout]:
    """
    Generate SubpageLayout for every node in the island.
    Returns a dict keyed by node_id.
    """
    echo_node_ids = echo_node_ids or set()
    trainer_node_ids = trainer_node_ids or set()
    layouts: Dict[str, SubpageLayout] = {}
    for nid, node in topology.nodes.items():
        layouts[nid] = generate_node_sublocations(
            node, topology, seed, echo_node_ids, trainer_node_ids
        )
    return layouts


# ============================================================
# §13  ISLAND CONTROLLER & TICK ENGINE
# ============================================================

@dataclass
class IslandState:
    """Complete mutable state of one island simulation."""
    seed: int = 0
    tick: int = 0
    topology: IslandTopology = field(default_factory=IslandTopology)
    species_map: Dict[str, Species] = field(default_factory=dict)
    encounter_tables: Dict[str, EncounterTable] = field(default_factory=dict)
    ledger: IslandLedger = field(default_factory=IslandLedger)
    factions: Dict[str, Faction] = field(default_factory=dict)
    league: LeagueState = field(default_factory=LeagueState)
    gene_pools: Dict[str, PopulationGenePool] = field(default_factory=dict)
    player_trajectory: PlayerTrajectory = field(default_factory=PlayerTrajectory)
    player_location: str = ""
    player_team: List[Tuple[str, str]] = field(default_factory=list)  # (instance_id, species_id)
    creatures: Dict[str, CreatureInstance] = field(default_factory=dict)
    discovered_species: Set[str] = field(default_factory=set)
    faction_standings: Dict[str, float] = field(default_factory=dict)  # player↔faction standing
    # §19 - Containment tier (base = seed-determined, current = live-computed)
    base_tier: ContainmentTier = ContainmentTier.TIER_I
    current_tier: ContainmentTier = ContainmentTier.TIER_I
    # §20 - Island narrative profile
    narrative_profile: Optional[IslandNarrativeProfile] = None
    # §21 - Hidden Knower NPC
    hidden_knower: Optional["HiddenKnower"] = None
    # §22 - Applied NGP+ profile (None on first expedition)
    ngp_profile: Optional["BehavioralProfileSignature"] = None
    # §24 - Memory echo events keyed by node_id
    echo_events: Dict[str, "EchoEvent"] = field(default_factory=dict)
    # §25 - Fragment system
    island_fragments: List["NarrativeFragment"] = field(default_factory=list)
    discovered_fragments: List[str] = field(default_factory=list)  # fragment_ids
    # §26 - Spatial sublocation layouts (puck system)
    sublocation_layouts: Dict[str, SubpageLayout] = field(default_factory=dict)
    _instance_counter: int = 0   # deterministic ID generator (replaces uuid)

    def next_id(self, prefix: str = "inst") -> str:
        """Return a deterministic, monotonically increasing instance ID."""
        self._instance_counter += 1
        return f"{prefix}_{self._instance_counter:06d}"


class NKController:
    """
    Main controller for one island simulation.
    Manages initialization, tick loop, and command dispatch.
    """

    def __init__(self, runtime_stub: Dict[str, Any], config: Dict[str, Any]):
        self._runtime_stub = runtime_stub
        self._config = config
        self._state: Optional[IslandState] = None
        self._cmd_q: queue.Queue = runtime_stub.get("nk_cmd_q", queue.Queue())
        self._ui_q: queue.Queue = runtime_stub.get("nk_ui_q", queue.Queue())
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._poll_interval = 0.1  # how often to check the command queue (no sim drift)
        # Ring buffer: last 50 UI events surfaced to /api/state recent_events
        self._recent_events: list = []
        self._recent_events_max: int = 50
        # Monotonic event counter: always increments on push, used by API
        # polling loops to detect new events even when the ring buffer is full.
        self._event_seq: int = 0
        # Stash the most-recent expedition_ended payload so the API endpoint can
        # always retrieve it even after init_island has flooded the ring buffer.
        self._last_expedition_result: Optional[dict] = None
        # Auto-save throttle: only write to disk at most once every 3 ticks
        self._last_save_tick: int = -1

    # ── Initialization ─────────────────────────────────────

    def init_island(self, seed: int,
                    ngp_profile: Optional["BehavioralProfileSignature"] = None):
        """Initialize a fresh island from seed, optionally applying an NGP+ profile."""
        # Clear recent_events ring buffer so /api/state shows only events from
        # the current island session (not bleed-through from a prior expedition).
        self._recent_events = []
        _dbg(f"Initializing island seed={seed}")
        topology = generate_island_topology(seed)
        species_map = generate_species_roster(topology)

        ledger = IslandLedger()
        ledger.set_baseline(seed)

        factions = generate_factions(topology)
        encounter_tables = generate_encounter_tables(topology, species_map, ledger)

        trainers = generate_ai_trainers(topology, species_map)
        league = LeagueState(trainers=trainers)

        # Initialize gene pools for each species
        gene_pools: Dict[str, PopulationGenePool] = {}
        for sid in species_map:
            gene_pools[sid] = PopulationGenePool(species_id=sid)

        # §19 - Containment tiers
        base_tier = _seed_to_base_tier(seed)

        # §20 - Narrative profile
        narrative_profile = generate_island_narrative(seed, base_tier)

        # §21 - Hidden Knower
        hidden_knower = generate_hidden_knower(topology, narrative_profile, seed)

        # §24 - Memory echo events (only populated on NGP+ runs)
        echo_events: Dict[str, EchoEvent] = {}
        if ngp_profile and ngp_profile.run_count >= 2:
            raw_echoes = generate_echo_events(ngp_profile, topology, seed)
            echo_events = {e.node_id: e for e in raw_echoes}

        # §25 - Island-specific fragment selection
        island_fragments = generate_island_fragments(narrative_profile, topology, seed)

        # §26 - Spatial sublocation puck layout
        trainers = generate_ai_trainers(topology, species_map)
        trainer_nids = set(trainers.keys())  # trainer_id == node_id for station trainers
        sublocation_layouts = generate_world_sublocations(
            topology, seed,
            echo_node_ids=set(echo_events.keys()),
            trainer_node_ids=trainer_nids,
        )

        self._state = IslandState(
            seed=seed,
            topology=topology,
            species_map=species_map,
            encounter_tables=encounter_tables,
            ledger=ledger,
            factions=factions,
            league=league,
            gene_pools=gene_pools,
            player_location=topology.start_node_id,
            faction_standings={fid: 0.0 for fid in factions},
            base_tier=base_tier,
            current_tier=base_tier,
            narrative_profile=narrative_profile,
            hidden_knower=hidden_knower,
            ngp_profile=ngp_profile,
            echo_events=echo_events,
            island_fragments=island_fragments,
            sublocation_layouts=sublocation_layouts,
        )

        # §22 - Apply NGP+ profile nudges after state is built
        if ngp_profile:
            apply_profile_to_island(ngp_profile, self._state)

        _dbg(f"Island initialized: {topology.island_name} "
             f"({topology.node_count} nodes, {len(species_map)} species, "
             f"{len(factions)} factions, base_tier={base_tier.name}, "
             f"knower={hidden_knower.name})")
        self._push_ui("island_initialized", {
            "island_name": topology.island_name,
            "seed": seed,
            "node_count": topology.node_count,
            "species_count": len(species_map),
            "faction_count": len(factions),
            "active_types": [t.name for t in topology.active_types],
            "climate": topology.climate.name,
            "relay_nodes": len(topology.relay_node_ids),
            "anomaly_zones": len(topology.anomaly_zone_ids),
            "knower_archetype": hidden_knower.archetype.name,
            "knower_name": hidden_knower.name,
            "ngp_run": ngp_profile.run_count if ngp_profile else 0,
        })

    # ── Tick engine ────────────────────────────────────────

    def start(self):
        """Start the background tick loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                         name="nk_tick")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run_loop(self):
        """
        Command-driven loop - the simulation advances deterministically
        only when the player issues commands.  An explicit 'advance'
        command (or any action that mutates game state) triggers one
        simulation tick, so wall-clock timing never affects the sim.
        """
        while not self._stop.is_set():
            # Block-wait for next command (with timeout so stop-flag works)
            try:
                cmd = self._cmd_q.get(timeout=self._poll_interval)
                self._handle_cmd(cmd)
            except queue.Empty:
                pass

    def _tick(self):
        """One simulation tick."""
        if not self._state:
            return

        st = self._state
        st.tick += 1

        # Faction diffusion
        diffuse_faction_influence(st.topology, st.factions)

        # Gate threshold adjustment
        compute_gate_thresholds(st.topology, st.ledger, st.factions)

        # Ledger drift from faction balance
        total_factions = len(st.factions)
        if total_factions > 0:
            # Compute average faction influence on key axes
            for fid, faction in st.factions.items():
                if faction.archetype == FactionArchetype.LEAGUE_AUTHORITY:
                    st.ledger.apply_delta("league_influence",
                                           faction.influence_score * 0.002)
                elif faction.archetype == FactionArchetype.RESEARCH_CONSORTIUM:
                    st.ledger.apply_delta("research_advancement",
                                           faction.influence_score * 0.002)
                elif faction.archetype == FactionArchetype.PRESERVATION_CIRCLE:
                    st.ledger.apply_delta("ecological_balance",
                                           faction.influence_score * 0.002)
                elif faction.archetype == FactionArchetype.INDUSTRIAL_SYNDICATE:
                    st.ledger.apply_delta("urbanization_level",
                                           faction.influence_score * 0.002)
                    st.ledger.apply_delta("ecological_balance",
                                           -faction.influence_score * 0.001)
                elif faction.archetype == FactionArchetype.DEPTH_SECT:
                    st.ledger.apply_delta("anomaly_stability",
                                           -faction.influence_score * 0.001)

        # Population pressure from urbanization
        st.ledger.apply_delta("population_pressure",
                               st.ledger.urbanization_level * 0.001)

        # Genetic diversity: slow natural recovery toward baseline (wild populations breed).
        # Caps recovery so it never significantly overshoots zero normalized.
        if st.ledger.genetic_diversity < 5.0:
            st.ledger.apply_delta("genetic_diversity", +0.05)

        # Cultural cohesion decay
        tension = st.ledger.tension_index()
        if tension > 60:
            st.ledger.apply_delta("cultural_cohesion", -0.3)
        elif tension < 30:
            st.ledger.apply_delta("cultural_cohesion", 0.1)

        # AI league matches (off-screen)
        if st.tick % 5 == 0:
            self._simulate_ai_league_round()

        # §19 - Recompute current containment tier every 20 ticks
        if st.tick % 20 == 0:
            new_tier = compute_containment_tier(st.ledger, st.player_trajectory)
            # Tier can only escalate upward (never de-escalates mid-island)
            if new_tier.value > st.current_tier.value:
                old_tier = st.current_tier
                st.current_tier = new_tier
                _dbg(f"Tier escalated: {old_tier.name} → {new_tier.name}")
                self._push_ui("tier_escalated", {
                    "previous_tier": old_tier.name,
                    "current_tier": new_tier.name,
                    "tier_description": TIER_CHARACTERISTICS[new_tier].description,
                })

        # Periodic UI update
        if st.tick % 10 == 0:
            self._push_ui("tick_update", {
                "tick": st.tick,
                "ledger": st.ledger.to_dict(),
                "player_location": st.player_location,
                "current_tier": st.current_tier.name,
            })

    def _simulate_ai_league_round(self):
        """Simulate one round of AI vs AI battles."""
        if not self._state:
            return
        league = self._state.league
        trainer_ids = list(league.trainers.keys())
        if len(trainer_ids) < 2:
            return

        rng = SeededRNG(self._state.seed + self._state.tick).fork("ai_league")
        rng.shuffle(trainer_ids)
        pairs = list(zip(trainer_ids[::2], trainer_ids[1::2]))

        for a_id, b_id in pairs[:10]:  # max 10 matches per round
            a = league.trainers[a_id]
            b = league.trainers[b_id]
            # Simplified: higher rating + random → winner
            a_strength = a.rating + rng.gauss(0, 100)
            b_strength = b.rating + rng.gauss(0, 100)
            if a_strength > b_strength:
                league.update_rating(a_id, b_id)
            else:
                league.update_rating(b_id, a_id)

    # ── Command handling ───────────────────────────────────

    def _maybe_autosave(self) -> None:
        """Write game state to disk if at least 3 ticks have passed since last save."""
        if not self._state:
            return
        if self._state.tick - self._last_save_tick >= 3:
            try:
                save_game_state(self._state)
                self._last_save_tick = self._state.tick
            except Exception as _save_err:
                _dbg(f"[NK Save] Auto-save failed: {_save_err}")

    def _handle_cmd(self, cmd: Dict[str, Any]):
        """Dispatch a player command."""
        action = cmd.get("action", "")

        if action == "init":
            self.init_island(cmd.get("seed", 42))

        elif action == "advance":
            # Explicit sim-clock step (N ticks, default 1)
            n = max(1, int(cmd.get("ticks", 1)))
            for _ in range(n):
                self._tick()

        elif action == "move":
            self._cmd_move(cmd.get("target_node", ""))

        elif action == "encounter":
            self._cmd_encounter()

        elif action == "capture":
            self._cmd_capture(cmd.get("instance_id", ""))

        elif action == "release":
            self._cmd_release(cmd.get("instance_id", ""))

        elif action == "claim":
            self._cmd_claim(cmd.get("instance_id", ""))

        elif action == "battle":
            self._cmd_battle(cmd.get("opponent_id", ""))

        elif action == "breed":
            self._cmd_breed(cmd.get("parent_a_id", ""),
                             cmd.get("parent_b_id", ""))

        elif action == "rest":
            self._cmd_rest()

        elif action == "explore":
            self._cmd_explore()

        elif action == "dialogue":
            self._cmd_dialogue(cmd.get("delta", {}))

        elif action == "get_state":
            self._cmd_get_state()

        elif action == "get_species":
            self._cmd_get_species()

        elif action == "get_map":
            self._cmd_get_map()

        elif action == "get_outcome":
            self._cmd_get_outcome()

        elif action == "talk_knower":
            self._cmd_talk_to_knower(int(cmd.get("fragment_index", 0)))

        elif action == "get_narrative":
            self._cmd_get_narrative()

        elif action == "get_knower":
            self._cmd_get_knower()

        elif action == "new_expedition":
            self._cmd_new_expedition(int(cmd.get("seed", 0)))

        elif action == "reset_simulation":
            self._cmd_reset_simulation(int(cmd.get("seed", 42)))

        elif action == "get_fragments":
            self._cmd_get_fragments()

        elif action == "get_sublocation":
            self._cmd_get_sublocation(
                cmd.get("node_id", ""),
                cmd.get("page", None),
            )

        else:
            self._push_ui("error", {"message": f"Unknown command: {action}"})

        # Auto-save after any state-mutating action (throttled to once per 3 ticks)
        if action in {"move", "capture", "release", "claim", "battle",
                      "breed", "rest", "explore", "dialogue"}:
            self._maybe_autosave()

    def _cmd_move(self, target_node: str):
        """Move player to an adjacent node."""
        if not self._state:
            return
        st = self._state
        current = st.topology.nodes.get(st.player_location)
        if not current:
            self._push_ui("error", {"message": "Invalid current location"})
            return

        if target_node not in current.neighbors:
            self._push_ui("error", {"message": f"Cannot reach {target_node} from here"})
            return

        target = st.topology.nodes.get(target_node)
        if not target:
            self._push_ui("error", {"message": f"Unknown node {target_node}"})
            return

        # Check gate
        if target.gate:
            # Best faction standing the player has with any faction present at this node
            best_standing = 0.0
            for fid in st.factions:
                node_inf = target.faction_influence.get(fid, 0.0)
                player_rel = st.faction_standings.get(fid, 0.0)
                # Standing contribution: player relationship weighted by node presence
                best_standing = max(best_standing, node_inf * max(0, player_rel))

            player_state = {
                "trainer_rating": st.league.trainers.get("player", Trainer()).rating,
                "faction_standing": best_standing,
                "research_milestones": st.player_trajectory.research_investment / 20.0,
                "ecological_balance": st.ledger.ecological_balance,
                "anomaly_exposure": st.player_trajectory.anomaly_exposure,
                "economic_investment": st.ledger.urbanization_level,
                "exploration_score": st.player_trajectory.exploration_depth,
                "league_tier": 1.0 + st.player_trajectory.competitive_focus / 33.0,
            }
            if not target.gate.check(player_state):
                self._push_ui("gate_blocked", {
                    "node": target_node,
                    "gate_type": target.gate.gate_type.name,
                    "requirement": target.gate.primary_metric,
                    "threshold": target.gate.threshold,
                })
                return

        st.player_location = target_node
        st.player_trajectory.update_from_exploration(target)

        # Discover species at this node
        et = st.encounter_tables.get(target_node)
        if et:
            for sid in et.all_species():
                if sid not in st.discovered_species:
                    st.discovered_species.add(sid)
                    st.player_trajectory.species_discovered += 1

        self._push_ui("moved", {
            "node_id": target_node,
            "node_type": target.node_type.name,
            "region": target.region.name,
            "name": target.name,
            "biome": target.biome.to_tuple(),
            "neighbors": target.neighbors,
        })

        # Notify ESP32 puck manager of player move (fire-and-forget, never blocks)
        try:
            from .spatial.esp32 import get_puck_manager as _gpm_move
            _pm = _gpm_move()
            if _pm and _pm.connected_count() > 0:
                _tier_int = {"TIER_I": 1, "TIER_II": 2, "TIER_III": 3,
                             "TIER_IV": 4, "TIER_V": 5}.get(
                    st.containment_tier.name if st.containment_tier else "TIER_I", 1)
                _pm.on_player_move(
                    target_node,
                    target.node_type.name,
                    _tier_int,
                    bool(getattr(target, "is_relay_node", False)),
                )
        except Exception:
            pass

        self._tick()  # advance sim after player action

    def _cmd_encounter(self):
        """Roll an encounter at current location."""
        if not self._state:
            return
        st = self._state
        et = st.encounter_tables.get(st.player_location)
        if not et:
            self._push_ui("no_encounter", {"message": "No encounters here"})
            return

        rng = SeededRNG(st.seed + st.tick + _det_hash(st.player_location)).fork("enc_roll")
        species_id = roll_encounter(et, rng)
        if not species_id or species_id not in st.species_map:
            self._push_ui("no_encounter", {"message": "Nothing appeared"})
            return

        sp = st.species_map[species_id]

        # Create wild creature instance
        inst_id = st.next_id("wild")
        level = max(1, rng.randint(3, 30))
        genes = GeneticProfile(
            stat_genes=[rng.randint(5, 28) for _ in range(6)],
            variance_seed=rng.randint(0, 2**31),
        )
        inst = CreatureInstance(
            instance_id=inst_id,
            species_id=species_id,
            level=level,
            genes=genes,
            temperament=rng.uniform(0.2, 0.8),
        )

        st.creatures[inst_id] = inst
        st.discovered_species.add(species_id)

        self._push_ui("encounter", {
            "species": sp.to_dict(),
            "instance_id": inst_id,
            "level": level,
            "temperament": inst.temperament,  # needed by UI for docility/catch% display
        })
        self._tick()  # advance sim after player action

    def _cmd_capture(self, instance_id: str):
        """Attempt to capture a wild creature by instance_id.

        Catch probability is derived from the creature's temperament
        (0 = docile/easy, 1 = fierce/hard) and whether the player has
        room on their team (max 6 slots).  No ball item is modelled yet
        - that is a future addition.
        """
        if not self._state:
            return
        st = self._state

        if not instance_id:
            self._push_ui("error", {"message": "instance_id required for capture"})
            return

        inst = st.creatures.get(instance_id)
        if not inst:
            self._push_ui("error", {"message": f"Unknown creature {instance_id}"})
            return

        # Verify this is actually a wild creature (not already player-owned)
        owned_ids = {iid for iid, _ in st.player_team}
        if instance_id in owned_ids:
            self._push_ui("error", {"message": "Creature is already on your team"})
            return

        # Team cap
        TEAM_MAX = 6
        if len(st.player_team) >= TEAM_MAX:
            self._push_ui("capture_failed", {
                "instance_id": instance_id,
                "reason": "team_full",
                "message": f"Team is full ({TEAM_MAX}/{TEAM_MAX}). Release a creature first.",
            })
            return

        # Catch probability:
        #   base 0.45  +  docility bonus (1 - temperament) * 0.45
        #   = 0.45 (fierce) … 0.90 (docile)
        # Anomaly exposure slightly boosts catch chance (attuned to weirdness)
        anomaly_bonus = min(0.10, st.player_trajectory.anomaly_exposure * 0.001)
        catch_prob = 0.45 + (1.0 - inst.temperament) * 0.45 + anomaly_bonus

        rng = SeededRNG(st.seed + st.tick + _det_hash(instance_id)).fork("capture")
        success = rng.random() < catch_prob

        sp = st.species_map.get(inst.species_id)
        sp_name = sp.name if sp else inst.species_id

        if success:
            # Add to team
            st.player_team.append((instance_id, inst.species_id))
            # Boost loyalty - freshly caught creature is cautiously loyal
            inst.loyalty = 45.0
            # Initialize XP to the floor for this creature's current level
            # so it only needs the marginal amount to reach the next level.
            inst.xp = xp_for_level(inst.level)
            # Update trajectory
            st.player_trajectory.update_from_capture()
            is_new_species = inst.species_id not in st.discovered_species
            st.discovered_species.add(inst.species_id)
            # Ecological ledger: removing one wild creature from the pool
            st.ledger.apply_delta("population_pressure", -0.05)
            # Genetic diversity: cataloguing a new species increases observed diversity.
            # First capture of a unique species is worth more (+0.4) than a repeat (+0.1).
            if is_new_species:
                st.ledger.apply_delta("genetic_diversity", +0.4)
            else:
                st.ledger.apply_delta("genetic_diversity", +0.1)
            # Award XP to front-of-team creatures for the capture milestone
            for front_id, front_sid in st.player_team[:3]:
                if front_id == instance_id:
                    continue  # don't award XP to the newly caught creature
                front_inst = st.creatures.get(front_id)
                front_sp = st.species_map.get(front_sid)
                if front_inst and front_sp:
                    lu_evs = award_capture_xp(front_inst, front_sp, inst.level)
                    for lu_ev in lu_evs:
                        self._push_ui("level_up", lu_ev)
                    # Check evolution after XP gain
                    evo_evs = check_evolution(front_inst, st.species_map, st.player_team)
                    for evo_ev in evo_evs:
                        self._push_ui("evolved", evo_ev)
            self._push_ui("captured", {
                "instance_id": instance_id,
                "species_id": inst.species_id,
                "species_name": sp_name,
                "level": inst.level,
                "temperament": inst.temperament,
                "catch_prob": round(catch_prob, 3),
                "team_size": len(st.player_team),
                "creatures_captured": st.player_trajectory.creatures_captured,
            })
        else:
            self._push_ui("capture_failed", {
                "instance_id": instance_id,
                "species_id": inst.species_id,
                "species_name": sp_name,
                "reason": "escaped",
                "catch_prob": round(catch_prob, 3),
                "message": f"{sp_name} broke free!",
            })

        self._tick()  # advance sim after player action

    def _cmd_release(self, instance_id: str):
        """Release a team creature back into the wild.

        Removes the creature from player_team and resets its loyalty.
        The creature re-enters the wild pool (remains in st.creatures).
        Slightly restores ecological population_pressure (we returned one
        bio-synthetic entity to the environment).
        """
        if not self._state:
            return
        st = self._state

        if not instance_id:
            self._push_ui("error", {"message": "instance_id required for release"})
            return

        # Check the creature is actually on the player's team
        team_ids = [iid for iid, _ in st.player_team]
        if instance_id not in team_ids:
            self._push_ui("error", {"message": f"{instance_id} is not on your team"})
            return

        inst = st.creatures.get(instance_id)
        sp = st.species_map.get(inst.species_id) if inst else None
        sp_name = sp.name if sp else instance_id

        # Remove from team
        st.player_team = [(iid, sid) for iid, sid in st.player_team if iid != instance_id]

        # Reset creature loyalty (released back to wild temperament)
        if inst:
            inst.loyalty = 0.0

        # Small ecological benefit - released creature returns to wild gene pool
        st.ledger.apply_delta("population_pressure", +0.03)
        st.ledger.apply_delta("genetic_diversity", +0.15)  # returned to wild gene pool

        self._push_ui("released", {
            "instance_id": instance_id,
            "species_name": sp_name,
            "team_size": len(st.player_team),
            "message": f"{sp_name} was released.",
        })

        self._tick()  # advance sim

    def _cmd_claim(self, instance_id: str):
        """Claim a bred offspring directly onto the player's team.

        Bred creatures (prefix 'bred_') exist in st.creatures after breeding
        but have no wild encounter entry, making them unreachable via normal
        capture flow.  This command transfers a bred creature to the team
        directly, mirroring post-breeding bonding.

        Constraints:
        - instance_id must start with 'bred_' (only bred creatures are claimable)
        - Creature must exist in st.creatures
        - Creature must not already be on the team
        - Team must have a free slot (max 6)
        """
        if not self._state:
            return
        st = self._state

        if not instance_id:
            self._push_ui("error", {"message": "instance_id required for claim"})
            return

        if not instance_id.startswith("bred_"):
            self._push_ui("error", {"message": "Only bred offspring can be claimed"})
            return

        inst = st.creatures.get(instance_id)
        if not inst:
            self._push_ui("error", {"message": f"Unknown creature {instance_id}"})
            return

        # Check not already on team
        owned_ids = {iid for iid, _ in st.player_team}
        if instance_id in owned_ids:
            self._push_ui("error", {"message": "Offspring is already on your team"})
            return

        # Team cap
        if len(st.player_team) >= 6:
            self._push_ui("claimed", {
                "ok": False,
                "reason": "team_full",
                "message": "Party is full (6/6). Release a creature first.",
            })
            return

        sp = st.species_map.get(inst.species_id)
        sp_name = sp.name if sp else inst.species_id

        # Bond the offspring — starts with higher loyalty than a wild catch
        inst.loyalty = 60.0
        inst.fatigue = 0.0
        st.player_team.append((instance_id, inst.species_id))

        # Slight trajectory boost (intentional bonding act)
        st.player_trajectory.creatures_captured += 1
        st.player_trajectory.update_from_capture()

        self._push_ui("claimed", {
            "ok": True,
            "instance_id": instance_id,
            "species_name": sp_name,
            "level": inst.level,
            "loyalty": inst.loyalty,
            "team_size": len(st.player_team),
            "message": f"{sp_name} joined your party!",
        })
        self._tick()

    def _cmd_rest(self):
        """
        Rest the team at a SETTLEMENT or CITY node.
        Reduces fatigue by 40% (proportional recovery), increases loyalty slightly.
        Only valid at SETTLEMENT or CITY nodes — rest in the wild is not supported.
        """
        if not self._state:
            return
        st = self._state
        cur_node = st.topology.nodes.get(st.player_location)
        rest_types = {NodeType.SETTLEMENT, NodeType.CITY}
        if not cur_node or cur_node.node_type not in rest_types:
            self._push_ui("error", {
                "message": "You can only rest at a settlement or city.",
                "code": "rest_invalid_location",
            })
            return
        if not st.player_team:
            self._push_ui("error", {
                "message": "No creatures to rest.",
                "code": "rest_no_team",
            })
            return

        recovered: list = []
        for inst_id, sp_id in st.player_team:
            inst = st.creatures.get(inst_id)
            sp = st.species_map.get(sp_id)
            if not inst:
                continue
            old_fatigue = inst.fatigue
            # Recover 60% of current fatigue (linear decay toward 0).
            # Changed from 40% — single rest now meaningfully restores the
            # team instead of two rests being needed to get below 40 fatigue.
            inst.fatigue = max(0.0, inst.fatigue * 0.4)
            # Small loyalty boost for resting together
            inst.loyalty = min(100.0, inst.loyalty + 3.0)
            recovered.append({
                "instance_id": inst_id,
                "species_name": sp.name if sp else sp_id,
                "fatigue_before": round(old_fatigue, 1),
                "fatigue_after": round(inst.fatigue, 1),
                "loyalty": round(inst.loyalty, 1),
            })

        self._push_ui("rested", {
            "location": cur_node.name,
            "team_recovered": recovered,
            "message": f"Your team rested at {cur_node.name}. Fatigue reduced.",
        })
        self._tick()  # time passes while resting

    def _cmd_battle(self, opponent_id: str):
        """Battle an AI trainer."""
        if not self._state:
            return
        st = self._state
        opponent = st.league.trainers.get(opponent_id)
        if not opponent:
            self._push_ui("error", {"message": f"Unknown trainer {opponent_id}"})
            return

        # Build player team from their creatures
        p_team: List[Tuple[CreatureInstance, Species]] = []
        for inst_id, sp_id in st.player_team[:3]:
            inst = st.creatures.get(inst_id)
            sp = st.species_map.get(sp_id)
            if inst and sp:
                p_team.append((inst, sp))

        if not p_team:
            self._push_ui("error", {"message": "No team set"})
            return

        # Exhaustion guard: refuse battle if all participating creatures are
        # critically fatigued (>= 80). This prevents the death spiral where a
        # maxed-fatigue creature fights at 50% stats and loses indefinitely.
        team_creatures = [st.creatures.get(iid) for iid, _ in st.player_team[:3]]
        active_creatures = [c for c in team_creatures if c is not None]
        if active_creatures and all(c.fatigue >= 80 for c in active_creatures):
            cur_node = st.topology.nodes.get(st.player_location)
            node_type = getattr(cur_node, "node_type", "").upper() if cur_node else ""
            self._push_ui("error", {
                "message": (
                    "Your team is exhausted (fatigue >= 80). Rest them at a "
                    "settlement or city before battling."
                )
            })
            return

        # Build opponent team — opponent creature level is scaled relative to
        # the AVERAGE level of the player's fighting team (not the max).
        # Using max caused unfair matchups: a Lv14/29/21 front-3 set max=29,
        # so every opponent came in at Lv28+, wrecking the Lv14 and Lv21 slots.
        # Average (21) gives opponents at Lv22-25, which is beatable with
        # type advantage + reasonable RNG.
        fighting_levels = [inst.level for inst, _ in p_team]
        player_avg_level = sum(fighting_levels) // max(1, len(fighting_levels))
        # Still track max for the floor so high-level players aren't sandbagged
        player_best_level = max(fighting_levels, default=1)
        o_team: List[Tuple[CreatureInstance, Species]] = []
        rng = SeededRNG(st.seed + st.tick).fork(f"battle_{opponent_id}")
        # Match opponent team size to player team size for a fair fight.
        # A 1-creature player should never face 3 opponent creatures — the
        # asymmetry made every battle mathematically unwinnable.
        fair_team_size = min(3, max(1, len(p_team)))
        for sp_id in opponent.team_species_ids[:fair_team_size]:
            sp = st.species_map.get(sp_id)
            if sp:
                raw_level = max(5, int(opponent.rating / 50))
                # Cap opponent at avg+4 (accessible to the weakest fighter)
                # and floor at avg-1 (some challenge). If raw_level < avg,
                # allow the floor to dominate so underlevelled trainers still
                # provide a test.
                capped_level = min(raw_level, player_avg_level + 4)
                capped_level = max(capped_level, max(1, player_avg_level - 1))
                inst = CreatureInstance(
                    instance_id=st.next_id("ai"),
                    species_id=sp_id,
                    level=capped_level,
                    genes=GeneticProfile(
                        stat_genes=[rng.randint(8, 25) for _ in range(6)],
                    ),
                )
                o_team.append((inst, sp))

        if not o_team:
            self._push_ui("error", {"message": "Opponent has no team"})
            return

        result = simulate_battle(p_team, o_team, rng)

        # Update systems
        player_won = result.winner == "player"
        st.player_trajectory.update_from_battle(player_won)

        # Update league ratings
        player_trainer = st.league.trainers.get("player")
        if not player_trainer:
            player_trainer = Trainer(trainer_id="player", name="Player",
                                     is_player=True, rating=1200)
            st.league.trainers["player"] = player_trainer
        if player_won:
            st.league.update_rating("player", opponent_id)
        else:
            st.league.update_rating(opponent_id, "player")

        # Ledger impact
        st.ledger.apply_delta("league_influence", 0.5 if player_won else 0.1)

        # Faction standing shift: battling at a node builds relationship
        # with the dominant faction there
        cur_node = st.topology.nodes.get(st.player_location)
        if cur_node and cur_node.faction_influence:
            dom_fid = max(cur_node.faction_influence,
                          key=cur_node.faction_influence.get)
            standing_delta = 0.04 if player_won else 0.01
            old = st.faction_standings.get(dom_fid, 0.0)
            st.faction_standings[dom_fid] = max(-1.0, min(1.0, old + standing_delta))

        # Fatigue
        for inst_id, _ in st.player_team[:3]:
            inst = st.creatures.get(inst_id)
            if inst:
                inst.fatigue = min(100, inst.fatigue + result.fatigue_delta)

        # XP gain for all creatures that participated (front 3)
        opp_levels = [inst.level for inst, _ in o_team]
        opp_avg = sum(opp_levels) / max(1, len(opp_levels))
        level_up_events: List[dict] = []
        for inst_id, sid in st.player_team[:3]:
            inst = st.creatures.get(inst_id)
            sp = st.species_map.get(sid)
            if inst and sp:
                lu = award_battle_xp(inst, sp, opp_avg, result.turns, player_won)
                level_up_events.extend(lu)
        # Emit level_up events; also check for evolutions triggered by level-up
        for lu_ev in level_up_events:
            self._push_ui("level_up", lu_ev)
        # Evolution check after XP distribution
        for inst_id, _sid in st.player_team[:3]:
            inst = st.creatures.get(inst_id)
            if inst:
                evo_evs = check_evolution(inst, st.species_map, st.player_team)
                for evo_ev in evo_evs:
                    self._push_ui("evolved", evo_ev)

        # --- NPC Voice: post-battle trainer quip ---
        # Structured quip pools — Cold Layer (no LLM invention).
        # Winner speaks. Archetype determined by LeagueTier + risk_profile.
        try:
            from .voice import speak_npc as _speak_npc
            _BATTLE_QUIPS_WIN_AGGRESSIVE = [
                "Your creatures fought hard. Not hard enough.",
                "Speed and pressure — that's the only strategy that matters here.",
                "Adaptation requires defeat. Consider this educational.",
                "The League doesn't reward hesitation. Neither do I.",
            ]
            _BATTLE_QUIPS_WIN_CONSERVATIVE = [
                "Patience wins more battles than strength. Remember that.",
                "I've studied this match-up for months. You had no chance today.",
                "The system favors those who prepare. I prepared.",
                "Efficiency, not force. That's the Cartographer way.",
            ]
            _BATTLE_QUIPS_LOSS_AGGRESSIVE = [
                "...Impressive. Don't let it make you comfortable.",
                "You found a gap I didn't know existed. I'll close it.",
                "Raw instinct over theory. I underestimated that.",
                "You won today. The system will correct for you eventually.",
            ]
            _BATTLE_QUIPS_LOSS_CONSERVATIVE = [
                "My analysis was incomplete. That's a data point.",
                "Your team's divergence from the baseline was higher than projected.",
                "I'll need to revise my models after this. You've introduced variance.",
                "Unexpected. I don't use that word often.",
            ]

            if player_won:
                # Opponent lost — opponent speaks a loss quip
                pool = (_BATTLE_QUIPS_LOSS_AGGRESSIVE
                        if opponent.risk_profile > 0.5
                        else _BATTLE_QUIPS_LOSS_CONSERVATIVE)
            else:
                # Opponent won — opponent speaks a win quip
                pool = (_BATTLE_QUIPS_WIN_AGGRESSIVE
                        if opponent.risk_profile > 0.5
                        else _BATTLE_QUIPS_WIN_CONSERVATIVE)

            quip_rng = SeededRNG(st.seed + st.tick).fork(f"quip_{opponent_id}")
            quip = pool[quip_rng.randint(0, len(pool) - 1)]

            # Pick archetype for opponent voice: tier maps to sophistication level
            tier_archetype = {
                1: "TRAINER",          # LOCAL — neutral
                2: "ISOLATED_RESEARCHER",  # REGIONAL — slightly clinical
            }.get(getattr(opponent.tier, "value", 1), "RETIRED_ARCHIVIST")

            _speak_npc(quip, tier_archetype, opponent.name)
        except Exception:
            pass  # voice never blocks gameplay

        self._push_ui("battle_result", {
            "winner": result.winner,
            "player_remaining": result.player_remaining,
            "opponent_remaining": result.opponent_remaining,
            "turns": result.turns,
            "moves_used": result.moves_used,
            "player_rating": player_trainer.rating,
            "opponent_name": opponent.name,
        })
        self._tick()  # advance sim after player action

    def _cmd_breed(self, parent_a_id: str, parent_b_id: str):
        """Breed two creatures."""
        if not self._state:
            return
        st = self._state
        inst_a = st.creatures.get(parent_a_id)
        inst_b = st.creatures.get(parent_b_id)
        if not inst_a or not inst_b:
            self._push_ui("error", {"message": "Invalid parent(s)"})
            return

        sp_a = st.species_map.get(inst_a.species_id)
        sp_b = st.species_map.get(inst_b.species_id)
        if not sp_a or not sp_b:
            self._push_ui("error", {"message": "Unknown species"})
            return

        # Compatibility check: same species or shared type
        compatible = (inst_a.species_id == inst_b.species_id
                     or sp_a.evolution_line_id == sp_b.evolution_line_id
                     or sp_a.primary_type == sp_b.primary_type
                     or sp_a.secondary_type == sp_b.primary_type)
        if not compatible:
            self._push_ui("error", {"message": "Incompatible pair"})
            return

        rng = SeededRNG(st.seed + st.tick).fork(f"breed_{parent_a_id}_{parent_b_id}")
        anomaly_inst = max(0, -st.ledger.anomaly_stability) / 100.0

        offspring_genes = breed_creatures(inst_a, inst_b, sp_a, sp_b, rng,
                                          anomaly_instability=anomaly_inst)

        # Create offspring
        offspring_id = st.next_id("bred")
        offspring = CreatureInstance(
            instance_id=offspring_id,
            species_id=sp_a.species_id,  # same species as parent A base
            level=1,
            genes=offspring_genes,
            temperament=rng.uniform(0.2, 0.8),
        )
        st.creatures[offspring_id] = offspring

        # Update tracking
        st.player_trajectory.update_from_breeding()

        # Ecological impact
        pool = st.gene_pools.get(sp_a.species_id)
        if pool:
            pool.update_from_breeding(offspring_genes)
        st.ledger.apply_delta("genetic_diversity", -0.2)  # breeding narrows diversity
        st.ledger.apply_delta("population_pressure", 0.1)

        # Parent fatigue
        inst_a.fatigue = min(100, inst_a.fatigue + 10)
        inst_b.fatigue = min(100, inst_b.fatigue + 10)

        self._push_ui("breed_result", {
            "offspring_id": offspring_id,
            "species": sp_a.species_id,
            "genes": offspring_genes.stat_genes,
            "lineage_depth": offspring_genes.lineage_depth,
            "traits": offspring_genes.trait_genes,
        })
        self._tick()  # advance sim after player action

    def _cmd_explore(self):
        """Survey current location for discoveries."""
        if not self._state:
            return
        st = self._state
        node = st.topology.nodes.get(st.player_location)
        if not node:
            return

        # §24 - Surface memory echo if one is placed at this node
        echo = st.echo_events.get(st.player_location)
        if echo:
            self._push_ui("memory_echo", {
                "node": st.player_location,
                "echo_type": echo.echo_type,
                "motif_code": echo.motif_code,
                "description": echo.description,
            })
            # Echo fires once then is consumed
            del st.echo_events[st.player_location]

        # §25 - Check for fragment discovery at this node type
        # WILD_ZONE included: most-traversed node type; fragments surface here
        # at the same rate as dedicated structures so exploration always pays off.
        _FRAGMENT_NODE_TYPES = {
            NodeType.FACILITY, NodeType.LANDMARK, NodeType.DUNGEON,
            NodeType.WILD_ZONE,
        }
        _ANOMALY_FRAG_TYPE = FragmentType.AUDIO_ARTIFACT

        if node.node_type in _FRAGMENT_NODE_TYPES or node.is_relay_node:
            # Find an undiscovered fragment whose unlock condition is met
            for frag in st.island_fragments:
                if frag.fragment_id in st.discovered_fragments:
                    continue
                traj_dict = st.player_trajectory.to_dict()
                can_unlock = all(
                    traj_dict.get(k, 0.0) >= v
                    for k, v in frag.unlock_condition.items()
                )
                # Relay nodes only surface relay-relevant fragments (M15);
                # conversely, M15 fragments only surface AT relay nodes.
                if node.is_relay_node and frag.mountain_code != "M15":
                    continue
                if not node.is_relay_node and frag.mountain_code == "M15":
                    continue
                if can_unlock:
                    st.discovered_fragments.append(frag.fragment_id)
                    # Build render context
                    framing = (st.narrative_profile.founder_framing
                               if st.narrative_profile else {})
                    traitor = FOUNDER_CANON.get(
                        framing.get("traitor", "voss"), FounderRecord("Dr. Voss","","","")
                    ).name
                    species_sample = next(iter(st.species_map.values()), None)
                    ctx = {
                        "founder":      traitor,
                        "island_name":  st.topology.island_name,
                        "tier":         st.current_tier.name,
                        "species_name": species_sample.name if species_sample else "the creature",
                        "anomaly_count": len(st.topology.anomaly_zone_ids),
                        "variance":     15.0 + (st.tick % 30),
                        "collapse_ticks": max(10, 200 - st.tick),
                    }
                    _rendered_title = frag.render_title(ctx)
                    _rendered_body  = frag.render(ctx)
                    self._push_ui("fragment_discovered", {
                        "fragment_id":  frag.fragment_id,
                        "type":         frag.ftype.name,
                        "title":        _rendered_title,
                        "body":         _rendered_body,
                        "mountain_code": frag.mountain_code,
                        "total_discovered": len(st.discovered_fragments),
                    })
                    # Wire Radio OS voice: narrate fragment discovery (same map
                    # as ANOMALY_ZONE branch; archetype chosen by fragment type).
                    _FRAGMENT_VOICE_MAP_FL = {
                        "RESEARCH_NOTE":           "ISOLATED_RESEARCHER",
                        "REDACTED_LOG":            "ANONYMOUS_SIGNAL",
                        "STATISTICAL_SUMMARY":     "RETIRED_ARCHIVIST",
                        "SPECIES_REGISTRY_GLITCH": "ELDERLY_HERMIT",
                        "AUDIO_ARTIFACT":          "ANONYMOUS_SIGNAL",
                    }
                    try:
                        from .voice import speak_npc as _speak_npc
                        _varchetype = _FRAGMENT_VOICE_MAP_FL.get(frag.ftype.name, "CARTOGRAPHER")
                        _body_first = _rendered_body.split(".")[0].strip() + "."
                        _speak_npc(
                            text=f"{_rendered_title}. {_body_first}",
                            archetype=_varchetype,
                            npc_name="Archive Fragment",
                        )
                    except Exception:
                        pass  # voice not available -- silent fallback
                    break  # one fragment per explore action

        elif node.node_type == NodeType.ANOMALY_ZONE:
            # ANOMALY_ZONE: surface any unlockable fragment (all types).
            # AUDIO_ARTIFACTs are listed first in FRAGMENT_POOL so they surface
            # preferentially, preserving thematic weight.  M15 reserved for relay nodes.
            for frag in st.island_fragments:
                if frag.fragment_id in st.discovered_fragments:
                    continue
                if frag.mountain_code == "M15":
                    continue
                traj_dict = st.player_trajectory.to_dict()
                can_unlock = all(
                    traj_dict.get(k, 0.0) >= v
                    for k, v in frag.unlock_condition.items()
                )
                if can_unlock:
                    st.discovered_fragments.append(frag.fragment_id)
                    species_sample = next(iter(st.species_map.values()), None)
                    framing = (st.narrative_profile.founder_framing
                               if st.narrative_profile else {})
                    traitor = FOUNDER_CANON.get(
                        framing.get("traitor", "voss"), FounderRecord("Dr. Voss","","","")
                    ).name
                    ctx = {
                        "founder":      traitor,
                        "island_name":  st.topology.island_name,
                        "tier":         st.current_tier.name,
                        "species_name": species_sample.name if species_sample else "the creature",
                        "anomaly_count": len(st.topology.anomaly_zone_ids),
                        "variance":     15.0 + (st.tick % 30),
                        "collapse_ticks": max(10, 200 - st.tick),
                    }
                    rendered_title = frag.render_title(ctx)
                    rendered_body  = frag.render(ctx)
                    self._push_ui("fragment_discovered", {
                        "fragment_id":   frag.fragment_id,
                        "type":          frag.ftype.name,
                        "title":         rendered_title,
                        "body":          rendered_body,
                        "mountain_code": frag.mountain_code,
                        "total_discovered": len(st.discovered_fragments),
                    })
                    # Wire Radio OS voice: narrate fragment discovery.
                    # Archetype is chosen by fragment type:
                    #   RESEARCH_NOTE          → ISOLATED_RESEARCHER (clinical)
                    #   REDACTED_LOG           → ANONYMOUS_SIGNAL    (haunted)
                    #   STATISTICAL_SUMMARY    → RETIRED_ARCHIVIST   (precise)
                    #   SPECIES_REGISTRY_GLITCH→ ELDERLY_HERMIT      (cryptic)
                    #   AUDIO_ARTIFACT         → ANONYMOUS_SIGNAL    (eerie)
                    _FRAGMENT_VOICE_MAP = {
                        "RESEARCH_NOTE":           "ISOLATED_RESEARCHER",
                        "REDACTED_LOG":            "ANONYMOUS_SIGNAL",
                        "STATISTICAL_SUMMARY":     "RETIRED_ARCHIVIST",
                        "SPECIES_REGISTRY_GLITCH": "ELDERLY_HERMIT",
                        "AUDIO_ARTIFACT":          "ANONYMOUS_SIGNAL",
                    }
                    try:
                        from .voice import speak_npc as _speak_npc
                        _ftype_name  = frag.ftype.name
                        _varchetype  = _FRAGMENT_VOICE_MAP.get(_ftype_name, "CARTOGRAPHER")
                        # Narrate title first, then first sentence of body (avoid wall of text).
                        _body_first  = rendered_body.split(".")[0].strip() + "."
                        _speak_npc(
                            text=f"{rendered_title}. {_body_first}",
                            archetype=_varchetype,
                            npc_name="Archive Fragment",
                        )
                    except Exception:
                        pass  # voice not available — silent fallback
                    break

        # Check for anomaly events - does NOT short-circuit; anomaly and
        # research gain are additive (a FACILITY can be both unstable and
        # productive; returning early would starve research_investment forever
        # on high-instability FACILITY/LANDMARK nodes).
        if node.biome.instability_bias > 0.05:
            rng = SeededRNG(st.seed + st.tick).fork("anomaly_check")
            if rng.random() < node.biome.instability_bias:
                st.player_trajectory.update_from_anomaly()
                st.ledger.apply_delta("anomaly_stability", -1.0)
                self._push_ui("anomaly_event", {
                    "node": st.player_location,
                    "instability": node.biome.instability_bias,
                })
                # Wire Radio OS voice: brief atmospheric narration on anomaly.
                # Uses ANONYMOUS_SIGNAL archetype (haunted/distorted) per BIBLE.
                # Narration text is deterministic (seeded RNG) -- Cold Layer compliant.
                _ANOMALY_LINES = [
                    "Something is wrong with the data here.",
                    "The readings don't match the baseline. They never do.",
                    "This zone has been flagged. The flag has no author.",
                    "Signal interference. Origin: unknown.",
                    "The Neikos near here behave differently. The logs don't explain why.",
                    "Environmental variance exceeds parameters. No corrective action logged.",
                    "There is a pattern in the instability. You almost recognize it.",
                    "The system is leaking. This is one of the cracks.",
                ]
                try:
                    from .voice import speak_npc as _speak_npc
                    _arng = SeededRNG(st.seed + st.tick).fork("anomaly_voice")
                    _anomaly_line = _ANOMALY_LINES[int(_arng.random() * len(_ANOMALY_LINES))]
                    _speak_npc(
                        text=_anomaly_line,
                        archetype="ANONYMOUS_SIGNAL",
                        npc_name="Signal",
                    )
                except Exception:
                    pass  # voice not available -- silent fallback

        # Research gain (runs regardless of whether anomaly fired above)
        if node.node_type in (NodeType.FACILITY, NodeType.LANDMARK):
            st.player_trajectory.update_from_research(10.0)
            st.ledger.apply_delta("research_advancement", 0.5)
            self._push_ui("research_discovery", {
                "node": st.player_location,
                "research_score": st.player_trajectory.research_investment,
            })
        else:
            self._push_ui("explored", {
                "node": st.player_location,
                "biome": node.biome.to_tuple(),
                "is_relay_node": node.is_relay_node,
                "node_type": node.node_type.name,
            })
        self._tick()  # advance sim after player action

    def _cmd_dialogue(self, delta_raw: Dict[str, float]):
        """Process a dialogue choice."""
        if not self._state:
            return
        delta = DialogueDelta(
            competition=delta_raw.get("competition", 0.0),
            preservation=delta_raw.get("preservation", 0.0),
            industrialization=delta_raw.get("industrialization", 0.0),
            research_priority=delta_raw.get("research_priority", 0.0),
            anomaly_curiosity=delta_raw.get("anomaly_curiosity", 0.0),
        )

        # Credibility from achievements
        pt = self._state.player_trajectory
        credibility = (
            0.5
            + pt.battles_won * 0.02
            + pt.nodes_explored * 0.01
            + pt.relics_found * 0.1
            + pt.research_investment * 0.005
        )
        credibility = min(3.0, credibility)

        pt.update_dialogue(delta, credibility)

        # Faction influence shift at current node
        node = self._state.topology.nodes.get(self._state.player_location)
        if node:
            for fid, faction in self._state.factions.items():
                alignment = 1.0 - faction.ideology.distance(pt.dialogue_ideology) / 3.0
                shift = alignment * credibility * 0.05
                current = node.faction_influence.get(fid, 0.0)
                node.faction_influence[fid] = max(0, min(1, current + shift))

        # Update player faction standings based on dialogue alignment
        for fid, faction in self._state.factions.items():
            alignment = 1.0 - faction.ideology.distance(pt.dialogue_ideology) / 3.0
            standing_shift = alignment * credibility * 0.03
            old = self._state.faction_standings.get(fid, 0.0)
            self._state.faction_standings[fid] = max(-1.0, min(1.0, old + standing_shift))

        self._push_ui("dialogue_processed", {
            "credibility": credibility,
            "ideology": pt.dialogue_ideology.to_dict(),
        })
        self._tick()  # advance sim after player action

    def _cmd_get_state(self):
        """Return full game state snapshot."""
        if not self._state:
            self._push_ui("error", {"message": "No island initialized"})
            return
        st = self._state
        self._push_ui("state", {
            "seed": st.seed,
            "tick": st.tick,
            "island_name": st.topology.island_name,
            "climate": st.topology.climate.name,
            "node_count": st.topology.node_count,
            "species_count": len(st.species_map),
            "player_location": st.player_location,
            "discovered_species": len(st.discovered_species),
            "ledger": st.ledger.to_dict(),
            "trajectory": st.player_trajectory.to_dict(),
            "factions": {fid: f.name for fid, f in st.factions.items()},
        })

    def _cmd_get_species(self):
        """Return species roster summary."""
        if not self._state:
            return
        species_list = [sp.to_dict() for sp in self._state.species_map.values()]
        self._push_ui("species_roster", {"species": species_list})

    def _cmd_get_map(self):
        """Return topology summary."""
        if not self._state:
            return
        nodes_data = {nid: nd.to_dict()
                      for nid, nd in self._state.topology.nodes.items()}
        self._push_ui("map_data", {
            "nodes": nodes_data,
            "start": self._state.topology.start_node_id,
            "player_location": self._state.player_location,
        })

    def _cmd_get_outcome(self):
        """Compute and return current outcome band."""
        if not self._state:
            return
        band = compute_outcome_band(self._state.ledger,
                                     self._state.player_trajectory)
        desc = describe_outcome_band(band)
        self._push_ui("outcome_band", desc)

    def _cmd_talk_to_knower(self, fragment_index: int = 0):
        """Talk to the Hidden Knower if present and unlocked."""
        if not self._state:
            return
        st = self._state
        knower = st.hidden_knower
        if not knower:
            self._push_ui("error", {"message": "No hidden knower on this island"})
            return

        if not knower.is_unlocked(st.player_trajectory):
            self._push_ui("knower_locked", {
                "name": knower.name,
                "archetype": knower.archetype.name,
                "hint": "You sense a presence here, but they aren't ready to speak.",
                "unlock_thresholds": knower.unlock_thresholds,
            })
            return

        # Build template context
        relay_id = st.topology.relay_node_ids[0] if st.topology.relay_node_ids else "none"
        species_sample = next(iter(st.species_map.values()), None)
        context = {
            "island_name": st.topology.island_name,
            "species_name": species_sample.name if species_sample else "the creature",
            "anomaly_count": len(st.topology.anomaly_zone_ids),
            "relay_node_id": relay_id,
        }

        fragment = knower.get_fragment(fragment_index, context)
        self._push_ui("knower_dialogue", {
            "name": knower.name,
            "archetype": knower.archetype.name,
            "fragment_index": fragment_index,
            "fragment": fragment,
            "total_fragments": len(knower.dialogue_fragments),
        })
        # Wire Radio OS voice: auto-speak Knower dialogue when served.
        # speak_npc is fire-and-forget via its internal queue; never blocks.
        try:
            from .voice import speak_npc as _speak_npc
            _speak_npc(
                text=fragment,
                archetype=knower.archetype.name,
                npc_name=knower.name,
            )
        except Exception:
            pass  # voice not available (no voice_provider) — silent fallback
        self._tick()

    def _cmd_get_knower(self):
        """Return the hidden knower's public profile."""
        if not self._state:
            return
        knower = self._state.hidden_knower
        if not knower:
            self._push_ui("error", {"message": "No hidden knower on this island"})
            return
        unlocked = knower.is_unlocked(self._state.player_trajectory)
        data = knower.to_dict()
        data["is_unlocked"] = unlocked
        if not unlocked:
            # Hide exact location until unlocked
            data["location_node_id"] = "???"
        self._push_ui("knower_profile", data)

    def _cmd_get_narrative(self):
        """Return the island's full narrative profile."""
        if not self._state:
            return
        np = self._state.narrative_profile
        if not np:
            self._push_ui("error", {"message": "No narrative profile generated"})
            return
        self._push_ui("narrative_profile", np.to_dict())

    def _cmd_new_expedition(self, next_seed: int = 0):
        """
        End the current expedition, save the behavioral profile to disk,
        and start a new island.  If next_seed is 0, derive it from current state.
        """
        if not self._state:
            self._push_ui("error", {"message": "No island to conclude"})
            return
        st = self._state

        # Derive next seed deterministically if not specified
        if next_seed == 0:
            rng = SeededRNG(st.seed + st.tick).fork("next_expedition")
            next_seed = rng.randint(1, 2**31 - 1)

        # Compute and save profile
        sig = compute_behavioral_signature(
            st.player_trajectory, st.ledger, st.current_tier, st.seed
        )
        path = save_behavioral_profile(sig)
        _dbg(f"Expedition ended (seed={st.seed}). Profile saved: {path}")

        expedition_payload = {
            "completed_seed": st.seed,
            "ticks_played": st.tick,
            "final_tier": st.current_tier.name,
            "behavioral_axis": sig.behavioral_axis,
            "profile_path": path,
            "next_seed": next_seed,
        }
        # Stash before init_island floods the ring buffer so the HTTP endpoint
        # can always retrieve it via _last_expedition_result.
        self._last_expedition_result = expedition_payload
        self._push_ui("expedition_ended", expedition_payload)

        # Load the saved profile (includes run_count increment) and re-init
        saved_profile = load_behavioral_profile(path)
        self.init_island(next_seed, ngp_profile=saved_profile)

    def _cmd_reset_simulation(self, seed: int = 42):
        """
        Delete the NGP+ profile and reinitialize at Tier I with a clean slate.
        """
        profile_path = _nk_profile_path()
        deleted = False
        try:
            _os.remove(profile_path)
            deleted = True
            _dbg("NGP+ profile deleted (reset_simulation)")
        except FileNotFoundError:
            pass

        self._push_ui("simulation_reset", {
            "profile_deleted": deleted,
            "new_seed": seed,
        })
        self.init_island(seed, ngp_profile=None)

    def _cmd_get_fragments(self):
        """Return all discovered fragments with rendered bodies."""
        if not self._state:
            return
        st = self._state
        framing = st.narrative_profile.founder_framing if st.narrative_profile else {}
        traitor = FOUNDER_CANON.get(
            framing.get("traitor", "voss"), FounderRecord("Dr. Voss","","","")
        ).name
        species_sample = next(iter(st.species_map.values()), None)
        ctx = {
            "founder":       traitor,
            "island_name":   st.topology.island_name,
            "tier":          st.current_tier.name,
            "species_name":  species_sample.name if species_sample else "the creature",
            "anomaly_count": len(st.topology.anomaly_zone_ids),
            "variance":      15.0 + (st.tick % 30),
            "collapse_ticks": max(10, 200 - st.tick),
        }
        result = []
        for frag in st.island_fragments:
            result.append({
                "fragment_id":    frag.fragment_id,
                "type":           frag.ftype.name,
                "title":          frag.title,
                "body":           frag.render(ctx) if frag.fragment_id in st.discovered_fragments else None,
                "mountain_code":  frag.mountain_code,
                "discovered":     frag.fragment_id in st.discovered_fragments,
                "unlock_condition": frag.unlock_condition,
            })
        self._push_ui("fragments", {
            "total": len(st.island_fragments),
            "discovered": len(st.discovered_fragments),
            "fragments": result,
        })

    def _cmd_get_sublocation(self, node_id: str, page: Optional[int] = None):
        """
        Return the SubpageLayout for a node (defaults to player's current node).

        If page is specified, returns only that page's sublocations.
        The R-Unit portal (entrance/exit) is always included in the response.
        """
        if not self._state:
            return
        st = self._state
        nid = node_id or st.player_location
        layout = st.sublocation_layouts.get(nid)
        if not layout:
            self._push_ui("error", {"message": f"No sublocation layout for node {nid}"})
            return

        data = layout.to_dict()
        if page is not None:
            page_idx = page - 1  # 1-based input → 0-based list index
            if 0 <= page_idx < len(layout.pages):
                data["pages"] = [data["pages"][page_idx]]
                data["current_page"] = page
            else:
                self._push_ui("error", {
                    "message": f"Page {page} out of range (1-{len(layout.pages)})"
                })
                return

        self._push_ui("sublocation_layout", data)



    # ── UI push ────────────────────────────────────────────

    def _push_ui(self, event_type: str, data: Dict[str, Any]):
        """Push an event to the UI queue and the recent_events ring buffer."""
        event = {"type": event_type, "data": data, "tick": self._state.tick if self._state else 0}
        self._ui_q.put(event)
        # Append to ring buffer; drop oldest when full
        self._recent_events.append(event)
        if len(self._recent_events) > self._recent_events_max:
            self._recent_events = self._recent_events[-self._recent_events_max:]
        # Increment monotonic counter so polling loops can detect new events
        # even when the ring buffer is at capacity (len stays at max).
        self._event_seq += 1


# ============================================================
# §14  WEB SERVER (OPTIONAL - FastAPI)
# ============================================================

def _start_web_server(stop_event: threading.Event,
                      runtime_stub: Dict[str, Any]):
    """Web UI + API server on port 7700."""
    # Kick off the background island cache if not already running.
    # This covers both register_widgets path and the standalone dev-server
    # script which calls _start_web_server directly.
    if not runtime_stub.get("nk_island_cache_started"):
        runtime_stub["nk_island_cache_started"] = True
        _cache_thread = threading.Thread(
            target=_build_island_cache,
            daemon=True,
            name="nk_island_cache",
        )
        _cache_thread.start()
        _dbg("Island cache background build started (from _start_web_server)")

    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse, HTMLResponse
        import uvicorn

        app = FastAPI(title="Neikos: Hundred Islands")
        # Starlette's CORSMiddleware is safe for WebSocket connections: it checks
        # scope["type"] != "http" and passes non-HTTP scopes (including WebSocket)
        # directly to the inner app unchanged. This is required for ESP32 pucks
        # connecting via /ws/puck without a browser Origin header.
        from starlette.middleware.cors import CORSMiddleware as _CORSMiddleware
        app.add_middleware(
            _CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        controller: NKController = runtime_stub.get("nk_controller")

        @app.get("/", response_class=HTMLResponse)
        def root():
            st = controller._state if controller else None
            seed = st.seed if st else "-"
            island = st.topology.island_name if (st and st.topology) else "Unknown Island"
            _tier_display = {"TIER_I": "Stable", "TIER_II": "Shifting", "TIER_III": "Unstable", "TIER_IV": "Volatile", "TIER_V": "Critical"}
            tier = _tier_display.get(st.current_tier.name, st.current_tier.name) if (st and st.current_tier) else "Stable"
            node = st.player_location if st else "-"
            tick = st.tick if st else 0
            return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Neikos: Hundred Islands</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Courier New', monospace; background: #0a0f1a; color: #c9d1d9; min-height: 100vh; }}
  .header {{ background: #111827; border-bottom: 1px solid #22304a; padding: 12px 20px; display: flex; align-items: center; gap: 16px; }}
  .header h1 {{ font-size: 18px; font-weight: 700; color: #60a5fa; letter-spacing: 1px; }}
  .header .tier {{ font-size: 11px; padding: 2px 8px; border-radius: 4px; background: #1e3a5f; color: #93c5fd; font-weight: 700; }}
  .status-bar {{ display: flex; gap: 24px; padding: 10px 20px; background: #0d1826; border-bottom: 1px solid #1a2a40; font-size: 12px; }}
  .status-item {{ display: flex; flex-direction: column; gap: 2px; }}
  .status-label {{ color: #4b6080; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }}
  .status-value {{ color: #e2e8f0; font-weight: 600; }}
  .main {{ padding: 20px; max-width: 900px; margin: 0 auto; }}
  .section {{ background: #111827; border: 1px solid #1e3a5f; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .section h2 {{ font-size: 13px; color: #60a5fa; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; border-bottom: 1px solid #1e3a5f; padding-bottom: 8px; }}
  .node-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 8px; }}
  .node-card {{ background: #0d1826; border: 1px solid #1e3a5f; border-radius: 6px; padding: 10px; cursor: pointer; transition: border-color 0.15s; }}
  .node-card:hover {{ border-color: #3b82f6; }}
  .node-card.current {{ border-color: #22d3ee; background: #0c2030; }}
  .node-card.neighbor {{ border-color: #4ade80; }}
  .node-card.node-gated {{ border-color: #ef4444 !important; opacity: 0.8; }}
  .node-card.node-gated:hover {{ border-color: #f87171 !important; }}
  .node-name {{ font-size: 12px; font-weight: 700; color: #e2e8f0; }}
  .node-meta {{ font-size: 10px; color: #4b6080; margin-top: 3px; }}
  .btn {{ display: inline-block; padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-family: inherit; font-weight: 600; transition: opacity 0.15s; }}
  .btn:hover {{ opacity: 0.85; }}
  .btn-primary {{ background: #1d4ed8; color: #fff; }}
  .btn-ghost {{ background: transparent; color: #60a5fa; border: 1px solid #1e3a5f; }}
  .fragment-list {{ display: flex; flex-direction: column; gap: 6px; }}
  .fragment {{ background: #0d1826; border: 1px solid #1e3a5f; border-radius: 6px; padding: 10px 12px; font-size: 12px; }}
  .fragment .ftype {{ font-size: 10px; color: #4b6080; text-transform: uppercase; margin-bottom: 4px; }}
  .fragment .ftext {{ color: #c9d1d9; line-height: 1.5; white-space: pre-wrap; }}
  #log {{ font-size: 11px; color: #4b6080; max-height: 200px; overflow-y: auto; }}
  #log .entry {{ padding: 3px 6px; border-bottom: 1px solid #0d1826; font-size: 11px; line-height: 1.5; }}
  .entry-move {{ color: #7dd3fc; }}
  .entry-enc {{ color: #86efac; }}
  .entry-cap {{ color: #4ade80; font-weight: 600; }}
  .entry-fail {{ color: #f87171; }}
  .entry-frag {{ color: #c084fc; font-style: italic; }}
  .entry-research {{ color: #93c5fd; }}
  .entry-anomaly {{ color: #fbbf24; }}
  .entry-battle {{ color: #e2e8f0; }}
  .entry-tick {{ color: #475569; font-size: 10px; }}
  .entry-release {{ color: #94a3b8; }}
  .entry-echo {{ color: #a78bfa; font-style: italic; }}
  .entry-levelup {{ color: #fbbf24; font-weight: 700; }}
  .ev-meta {{ color: #475569; font-size: 10px; }}
  .refresh-note {{ font-size: 10px; color: #4b6080; margin-top: 8px; }}
  .knower-panel {{ background: #080f1c; border: 1px solid #5b21b6; border-radius: 8px; padding: 14px; margin-top: 10px; }}
  .knower-name {{ font-size: 15px; font-weight: 700; color: #a78bfa; margin-bottom: 2px; }}
  .knower-archetype {{ font-size: 10px; color: #6d28d9; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }}
  .knower-hint {{ font-size: 12px; color: #7c3aed; font-style: italic; margin-bottom: 6px; }}
  .knower-locked {{ font-size: 11px; color: #4b6080; padding: 8px; background: #060c18; border-radius: 4px; }}
  .knower-fragment {{ font-size: 12px; color: #c4b5fd; line-height: 1.7; padding: 10px; background: #080f1c; border: 1px solid #3730a3; border-radius: 6px; white-space: pre-wrap; }}
  .knower-nav {{ display: flex; gap: 8px; align-items: center; margin-top: 8px; font-size: 11px; color: #4b6080; }}
  .btn-knower {{ background: #4c1d95; color: #ddd6fe; border: 1px solid #7c3aed; }}
  .encounter-card {{ background: #0d1826; border: 1px solid #2d5a27; border-radius: 8px; padding: 14px; margin-top: 10px; }}
  .encounter-card .enc-name {{ font-size: 16px; font-weight: 700; color: #4ade80; margin-bottom: 4px; }}
  .encounter-card .enc-meta {{ font-size: 11px; color: #4b6080; margin-bottom: 10px; }}
  .encounter-card .enc-actions {{ display: flex; gap: 8px; }}
  .btn-capture {{ background: #166534; color: #bbf7d0; border: 1px solid #4ade80; }}
  .btn-flee {{ background: #1c1917; color: #a8a29e; border: 1px solid #44403c; }}
  .team-list {{ display: flex; flex-direction: column; gap: 4px; }}
  .team-entry {{ background: #0d1826; border: 1px solid #1e3a5f; border-radius: 4px; padding: 6px 10px; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }}
  .team-entry .te-row1 {{ display: flex; gap: 12px; align-items: center; }}
  .team-entry .te-name {{ color: #e2e8f0; font-weight: 600; flex: 1; }}
  .team-entry .te-meta {{ color: #4b6080; font-size: 11px; }}
  .team-entry .te-loyalty {{ color: #a78bfa; font-size: 11px; }}
  .te-fatigue-bar {{ display: flex; flex-direction: column; gap: 2px; }}
  .te-fatigue-label {{ font-size: 10px; color: #4b6080; }}
  .te-fatigue-track {{ height: 5px; background: #0a1525; border-radius: 3px; overflow: hidden; width: 100%; }}
  .te-fatigue-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
  .te-fatigue-fill.fg-low {{ background: #22c55e; }}
  .te-fatigue-fill.fg-med {{ background: #f59e0b; }}
  .te-fatigue-fill.fg-high {{ background: #ef4444; }}
  .te-xp-bar {{ display: flex; flex-direction: column; gap: 2px; margin-top: 3px; }}
  .te-xp-label {{ font-size: 10px; color: #60a5fa; }}
  .te-xp-track {{ height: 4px; background: #0a1525; border-radius: 3px; overflow: hidden; width: 100%; }}
  .te-xp-fill {{ height: 100%; background: #3b82f6; border-radius: 3px; transition: width 0.4s; }}
  .trainer-list {{ display: flex; flex-direction: column; gap: 6px; }}
  .trainer-card {{ background: #0d1826; border: 1px solid #1e3a5f; border-radius: 6px; padding: 10px 12px; font-size: 12px; display: flex; align-items: center; gap: 12px; }}
  .trainer-card .tr-name {{ font-weight: 700; color: #e2e8f0; flex: 1; }}
  .trainer-card .tr-meta {{ color: #4b6080; font-size: 11px; }}
  .btn-battle {{ background: #7c3aed; color: #ede9fe; border: 1px solid #8b5cf6; }}
  .btn-release {{ background: transparent; color: #f87171; border: 1px solid #7f1d1d; font-size: 11px; padding: 3px 8px; margin-left: auto; }}
  .battle-result {{ background: #0d1826; border: 1px solid #7c3aed; border-radius: 8px; padding: 14px; margin-top: 10px; }}
  .battle-result .br-winner {{ font-size: 16px; font-weight: 700; margin-bottom: 6px; }}
  .battle-result.won .br-winner {{ color: #4ade80; }}
  .battle-result.lost .br-winner {{ color: #f87171; }}
  .battle-result .br-stats {{ font-size: 11px; color: #4b6080; margin-bottom: 8px; }}
  .battle-log {{ font-size: 11px; color: #6b7280; max-height: 120px; overflow-y: auto; background: #060d16; border-radius: 4px; padding: 6px 8px; }}
  .battle-log div {{ padding: 2px 0; border-bottom: 1px solid #0d1826; }}
  /* Faction Standings */
  .faction-panel {{ background: #080f1c; border: 1px solid #2e1e52; border-radius: 8px; padding: 12px; }}
  .faction-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid #1a1a2e; }}
  .faction-row:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
  .faction-name {{ font-size: 13px; font-weight: 600; color: #c9d5e4; flex: 1; }}
  .faction-arch {{ font-size: 10px; color: #6d28d9; text-transform: uppercase; letter-spacing: 0.05em; }}
  .faction-bar-wrap {{ width: 100px; height: 8px; background: #1a1a2e; border-radius: 4px; overflow: hidden; flex-shrink: 0; }}
  .faction-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.4s; }}
  .fbar-positive {{ background: #22c55e; }}
  .fbar-negative {{ background: #ef4444; float: right; }}
  .fbar-neutral {{ background: #4b5563; }}
  .faction-val {{ font-size: 11px; color: #7aa2c8; width: 40px; text-align: right; flex-shrink: 0; }}
  .faction-territory {{ font-size: 10px; color: #4b6080; }}
  .identity-panel {{ background: #080f1c; border: 1px solid #1e3a5f; border-radius: 8px; padding: 14px; }}
  .identity-role {{ font-size: 16px; font-weight: 700; color: #60a5fa; margin-bottom: 4px; }}
  .identity-archetype {{ font-size: 12px; color: #93c5fd; margin-bottom: 2px; }}
  .identity-condition {{ font-size: 12px; color: #4b6080; margin-bottom: 8px; }}
  .identity-summary {{ font-size: 12px; color: #c9d1d9; line-height: 1.5; font-style: italic; }}
  /* Local Intel */
  .local-panel {{ background: #080f1c; border: 1px solid #1e3a5f; border-radius: 8px; padding: 12px; }}
  .local-node-name {{ font-size: 15px; font-weight: 700; color: #60a5fa; margin-bottom: 2px; }}
  .local-meta {{ font-size: 11px; color: #4b6080; margin-bottom: 8px; }}
  .local-biome {{ font-size: 11px; color: #7aa; margin-bottom: 8px; }}
  .enc-tier {{ margin-bottom: 6px; }}
  .enc-tier-label {{ font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px; }}
  .enc-tier-label.t-common {{ color: #9ca3af; }}
  .enc-tier-label.t-uncommon {{ color: #34d399; }}
  .enc-tier-label.t-rare {{ color: #60a5fa; }}
  .enc-tier-label.t-elite {{ color: #a78bfa; }}
  .enc-tier-label.t-apex {{ color: #f59e0b; }}
  .enc-tier-label.t-anomaly {{ color: #f43f5e; }}
  .enc-species-list {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .enc-species-chip {{ font-size: 10px; padding: 2px 6px; border-radius: 10px; background: #111827; border: 1px solid #1e3a5f; cursor: default; }}
  .enc-species-chip:hover {{ border-color: #60a5fa; color: #c9d1d9; }}
  .gate-row {{ font-size: 11px; margin-bottom: 4px; padding: 4px 8px; border-radius: 4px; background: #0d1b2a; }}
  .gate-open {{ color: #4ade80; }}
  .gate-locked {{ color: #f87171; }}
  .local-no-enc {{ font-size: 11px; color: #4b6080; font-style: italic; }}
  /* ── Tier Visual Theme System ───────────────────────────────────────── */
  /* Defaults (Tier I — warm/adventure) are baked into the static rules above.
     JS sets data-tier="I"|"II"|"III"|"IV"|"V" on <body> and transitions apply. */
  body {{ transition: background 2s ease, color 1s ease; }}
  .header {{ transition: background 2s ease, border-color 1s ease; }}
  .status-bar {{ transition: background 2s ease, border-color 1s ease; }}
  .section {{ transition: background 1.5s ease, border-color 1.5s ease; }}
  .section h2 {{ transition: color 1.5s ease, border-color 1.5s ease; }}
  /* Tier II — first hints of cold */
  body[data-tier="II"] {{ background: #09101f; color: #b8c4cc; }}
  body[data-tier="II"] .header {{ background: #0e1928; border-bottom-color: #1f3a5e; }}
  body[data-tier="II"] .header h1 {{ color: #5b9bd5; }}
  body[data-tier="II"] .status-bar {{ background: #0b1522; border-bottom-color: #162843; }}
  body[data-tier="II"] .section {{ background: #0e1928; border-color: #1a3558; }}
  body[data-tier="II"] .section h2 {{ color: #5b9bd5; border-color: #1a3558; }}
  body[data-tier="II"] .tier {{ background: #1a3a60; color: #7ab8e8; }}
  /* Tier III — institutional cold creeping in */
  body[data-tier="III"] {{ background: #070e1d; color: #a0afbb; }}
  body[data-tier="III"] .header {{ background: #0b1623; border-bottom-color: #18304f; }}
  body[data-tier="III"] .header h1 {{ color: #4a8abf; letter-spacing: 2px; }}
  body[data-tier="III"] .status-bar {{ background: #081019; border-bottom-color: #10253f; }}
  body[data-tier="III"] .section {{ background: #0b1623; border-color: #152d4d; }}
  body[data-tier="III"] .section h2 {{ color: #4a8abf; border-color: #152d4d; }}
  body[data-tier="III"] .tier {{ background: #0f2e50; color: #4a9fd5; border: 1px solid #1e4a78; }}
  body[data-tier="III"] .node-card.current {{ border-color: #4a8abf; background: #091828; }}
  body[data-tier="III"] .node-card.neighbor {{ border-color: #2a7a4a; }}
  /* Tier IV — cold/institutional, clinical, sterile */
  body[data-tier="IV"] {{ background: #050b18; color: #8a9eaf; font-family: 'Courier New', monospace; }}
  body[data-tier="IV"] .header {{ background: #071020; border-bottom-color: #102540; }}
  body[data-tier="IV"] .header h1 {{ color: #3a7aaa; letter-spacing: 3px; font-size: 15px; text-transform: uppercase; }}
  body[data-tier="IV"] .status-bar {{ background: #060e1a; border-bottom-color: #0d2038; }}
  body[data-tier="IV"] .section {{ background: #071020; border-color: #0f2540; }}
  body[data-tier="IV"] .section h2 {{ color: #3a7aaa; border-color: #0f2540; letter-spacing: 2px; }}
  body[data-tier="IV"] .tier {{ background: #081828; color: #3a9ad5; border: 1px solid #0f3060; font-family: monospace; }}
  body[data-tier="IV"] .node-card {{ background: #060e1a; border-color: #0e2438; }}
  body[data-tier="IV"] .node-card.current {{ border-color: #2a6a9a; background: #060f1e; }}
  body[data-tier="IV"] .node-card.neighbor {{ border-color: #1a5a3a; }}
  body[data-tier="IV"] .node-name {{ color: #8a9eaf; }}
  body[data-tier="IV"] .entry-anomaly {{ color: #d4852a; }}
  body[data-tier="IV"] .entry-frag {{ color: #7a64ac; font-style: normal; font-family: monospace; }}
  /* Tier V — maximum containment, near-monochrome, institutional stark */
  body[data-tier="V"] {{ background: #030a14; color: #6a8090; font-family: 'Courier New', monospace; }}
  body[data-tier="V"] .header {{ background: #050d1a; border-bottom-color: #0a1e34; }}
  body[data-tier="V"] .header h1 {{ color: #2a6a9a; letter-spacing: 4px; font-size: 13px; text-transform: uppercase; }}
  body[data-tier="V"] .status-bar {{ background: #040b16; border-bottom-color: #091b30; }}
  body[data-tier="V"] .section {{ background: #050d1a; border-color: #0a1e34; }}
  body[data-tier="V"] .section h2 {{ color: #2a6a9a; border-color: #0a1e34; letter-spacing: 3px; font-size: 11px; }}
  body[data-tier="V"] .tier {{ background: #05121f; color: #2a6a9a; border: 1px solid #0a2040; font-family: monospace; letter-spacing: 2px; }}
  body[data-tier="V"] .node-card {{ background: #040b16; border-color: #091d32; }}
  body[data-tier="V"] .node-card.current {{ border-color: #1a5a8a; background: #040d1c; }}
  body[data-tier="V"] .node-card.neighbor {{ border-color: #1a4a30; }}
  body[data-tier="V"] .node-name {{ color: #6a8090; }}
  body[data-tier="V"] .status-value {{ color: #8a9eaf; }}
  body[data-tier="V"] .entry-anomaly {{ color: #c47020; }}
  body[data-tier="V"] .entry-frag {{ color: #5a5480; font-style: normal; font-family: monospace; font-size: 10px; }}
</style>
</head>
<body>
<div class="header">
  <h1 id="game-title">🏝️ NEIKOS: HUNDRED ISLANDS</h1>
  <span class="tier" id="tier-badge">{tier}</span>
  <span style="margin-left:auto;font-size:11px;color:#4b6080;">Seed {seed}</span>
</div>
<div class="status-bar">
  <div class="status-item"><span class="status-label">Island</span><span class="status-value" id="s-island">{island}</span></div>
  <div class="status-item"><span class="status-label">Location</span><span class="status-value" id="s-node">{node}</span></div>
  <div class="status-item"><span class="status-label">Tick</span><span class="status-value" id="s-tick">{tick}</span></div>
  <div class="status-item"><span class="status-label">Status</span><span class="status-value" id="s-tier">{tier}</span></div>
  <div class="status-item"><span class="status-label">Team</span><span class="status-value" id="s-team">0/6</span></div>
  <div class="status-item"><span class="status-label">Captured</span><span class="status-value" id="s-captured">0</span></div>
  <div class="status-item"><span class="status-label">Rating</span><span class="status-value" id="s-rating">1200</span></div>
  <div class="status-item"><span class="status-label">W/L</span><span class="status-value" id="s-wl">0/0</span></div>
</div>
<div class="main">
  <div class="section">
    <h2>📍 Current Location &amp; Neighbors</h2>
    <div style="margin-bottom:10px;display:flex;gap:8px;align-items:center;">
      <button class="btn btn-primary" onclick="doExplore()">🔍 Explore Current Node</button>
      <span id="explore-status" style="font-size:11px;color:#4b6080;"></span>
    </div>
    <div class="node-grid" id="node-grid"><span style="color:#4b6080">Loading map…</span></div>
  </div>
  <div class="section">
    <h2>🗺️ Local Intel</h2>
    <div id="local-area"><span style="color:#4b6080">Loading…</span></div>
  </div>
  <div class="section">
    <h2>🌿 Encounter</h2>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <button class="btn btn-primary" id="enc-btn" onclick="doEncounter()">⚡ Search for Creature</button>
      <span id="enc-status" style="font-size:11px;color:#4b6080;"></span>
    </div>
    <div id="encounter-area"></div>
  </div>
  <div class="section">
    <h2>🐾 Your Team</h2>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <button class="btn btn-ghost" id="btn-rest" onclick="doRestTeam()" style="display:none">🛌 Rest Team</button>
      <span id="rest-status" style="font-size:11px;color:#4b6080;"></span>
    </div>
    <div class="team-list" id="team-list"><span style="color:#4b6080">No creatures yet.</span></div>
  </div>
  <div class="section">
    <h2>🧬 Breed</h2>
    <div style="font-size:11px;color:#4b6080;margin-bottom:8px;">Select two team members to breed. Parents must share species, evolution line, or primary type. Both gain fatigue. Offspring starts at Lv.1 in the wild creature pool.</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;">
      <select id="breed-parent-a" style="background:#1e2a3a;color:#c9d5e4;border:1px solid #2e3e52;border-radius:4px;padding:4px 8px;font-size:12px;"><option value="">-- Parent A --</option></select>
      <select id="breed-parent-b" style="background:#1e2a3a;color:#c9d5e4;border:1px solid #2e3e52;border-radius:4px;padding:4px 8px;font-size:12px;"><option value="">-- Parent B --</option></select>
      <button class="btn btn-primary" id="btn-breed" onclick="doBreed()">🧬 Breed</button>
      <span id="breed-status" style="font-size:11px;color:#4b6080;"></span>
    </div>
    <div id="breed-result-area"></div>
  </div>
  <div class="section">
    <h2>⚔️ Battle</h2>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <button class="btn btn-ghost" onclick="loadTrainers()">🔄 Load Trainers</button>
      <span id="battle-status" style="font-size:11px;color:#4b6080;"></span>
    </div>
    <div class="trainer-list" id="trainer-list"><span style="color:#4b6080">Click "Load Trainers" to find opponents. You need at least 1 creature to battle.</span></div>
    <div id="battle-result-area"></div>
  </div>
  <div class="section">
    <h2>📜 Recent Fragments</h2>
    <div class="fragment-list" id="fragment-list"><span style="color:#4b6080">Loading…</span></div>
  </div>
  <div class="section">
    <h2>🔮 Hidden Knower</h2>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
      <button class="btn btn-knower" onclick="loadKnower()">👁 Sense Presence</button>
      <span id="knower-status" style="font-size:11px;color:#6d28d9;"></span>
    </div>
    <div id="knower-area"></div>
  </div>
  <div class="section">
    <h2>🏛️ Faction Standings</h2>
    <div id="faction-area"><span style="color:#4b6080">Loading…</span></div>
  </div>
  <div class="section">
    <h2>🪞 Narrative Identity</h2>
    <div id="identity-area"><span style="color:#4b6080">Loading…</span></div>
  </div>
  <div class="section">
    <h2>🌐 Expedition &amp; NGP+</h2>
    <div id="ngp-info" style="font-size:12px;color:#4b6080;margin-bottom:10px;">Loading expedition status…</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
      <button class="btn btn-primary" onclick="doCompleteExpedition()" id="ngp-complete-btn">✅ Complete Expedition (Begin New)</button>
      <button class="btn btn-ghost" onclick="doResetSimulation()" id="ngp-reset-btn">🔄 Reset Simulation (Tier I)</button>
    </div>
    <div id="ngp-status" style="font-size:11px;color:#4b6080;"></div>
    <div id="ngp-profile-area" style="margin-top:8px;"></div>
  </div>
  <div class="section">
    <h2>📡 Event Log</h2>
    <div id="log"><span style="color:#4b6080">Loading…</span></div>
    <div class="refresh-note">Auto-refreshes every 3s</div>
  </div>
</div>
<script>
const API = '';
let _activeEncounter = null;  // {{ instance_id, species_name, level, temperament }}
let stateR = {{}};  // module-level cache of last /api/state response (read by loadTrainers etc)
let mapR = {{}};    // module-level cache of last /api/map response

// ── Tier Visual Theme ─────────────────────────────────────────────────
// Maps API tier name → CSS data-tier value + title text
const _TIER_LABELS = {{
  TIER_I:   {{ dt: 'I',   title: '🏝️ NEIKOS: HUNDRED ISLANDS', seed_label: 'Seed' }},
  TIER_II:  {{ dt: 'II',  title: '🏝️ NEIKOS: HUNDRED ISLANDS', seed_label: 'Seed' }},
  TIER_III: {{ dt: 'III', title: '⚠ NEIKOS — ISLAND SECTOR', seed_label: 'Sector' }},
  TIER_IV:  {{ dt: 'IV',  title: '▣ NEIKOS CONTAINMENT PROTOCOL', seed_label: 'Partition' }},
  TIER_V:   {{ dt: 'V',   title: '▣ PROJECT HUNDRED — APEX VARIABLE', seed_label: 'Node' }},
}};
let _lastTierApplied = null;
function applyTierTheme(tierName) {{
  const meta = _TIER_LABELS[tierName] || _TIER_LABELS['TIER_I'];
  if (_lastTierApplied === meta.dt) return;  // no change
  _lastTierApplied = meta.dt;
  document.body.setAttribute('data-tier', meta.dt);
  const titleEl = document.getElementById('game-title');
  if (titleEl) titleEl.textContent = meta.title;
}}

async function refresh() {{
  try {{
    let fragR;
    [stateR, mapR, fragR] = await Promise.all([
      fetch(API + '/api/state').then(r=>r.json()),
      fetch(API + '/api/map').then(r=>r.json()),
      fetch(API + '/api/fragments').then(r=>r.json()),
    ]);
    // Status bar
    document.getElementById('s-island').textContent = stateR.island_name || '-';
    document.getElementById('s-node').textContent = stateR.player_location || '-';
    document.getElementById('s-tick').textContent = stateR.tick ?? '-';
    const _TIER_DISPLAY = {{
      TIER_I:   'Stable',
      TIER_II:  'Shifting',
      TIER_III: 'Unstable',
      TIER_IV:  'Volatile',
      TIER_V:   'Critical',
    }};
    const tierDisplayName = _TIER_DISPLAY[stateR.containment_tier] || stateR.containment_tier || '-';
    document.getElementById('s-tier').textContent = tierDisplayName;
    document.getElementById('tier-badge').textContent = tierDisplayName;
    applyTierTheme(stateR.containment_tier || 'TIER_I');
    const traj = stateR.trajectory || {{}};
    // Team
    const teamSize = stateR.player_team_size ?? 0;
    document.getElementById('s-team').textContent = teamSize + '/6';
    document.getElementById('s-captured').textContent = traj.creatures_captured ?? 0;
    document.getElementById('s-rating').textContent = stateR.player_rating != null ? Math.round(stateR.player_rating) : '1200';
    document.getElementById('s-wl').textContent = (stateR.player_wins ?? 0) + '/' + (stateR.player_losses ?? 0);
    const tl = document.getElementById('team-list');
    const teamData = stateR.player_team || [];
    if (!teamData.length) {{
      tl.innerHTML = '<span style="color:#4b6080">No creatures yet. Search for encounters to find wild creatures.</span>';
    }} else {{
      tl.innerHTML = teamData.map(c => {{
        const fat = c.fatigue ?? 0;
        const fatPct = Math.round(fat);
        const fatClass = fat < 33 ? 'fg-low' : fat < 66 ? 'fg-med' : 'fg-high';
        const fatLabel = fat < 33 ? 'Fresh' : fat < 66 ? 'Tired' : 'Exhausted';
        const loy = c.loyalty ?? 0;
        const xp = c.xp ?? 0;
        const xpNext = c.xp_to_next ?? 10;
        const xpFloor = c.xp_floor ?? 0;
        const xpProgress = xp - xpFloor;
        const xpPct = xpNext > 0 ? Math.min(100, Math.round(xpProgress / xpNext * 100)) : 100;
        const xpLabel = c.level >= 50 ? 'MAX' : `XP: ${{xpProgress}}/${{xpNext}}`;
        // Species snapshot (new fields from enriched /api/state)
        const typeStr = c.secondary_type ? `${{c.primary_type}}/${{c.secondary_type}}` : (c.primary_type || '?');
        const bstStr = c.bst != null ? `BST ${{c.bst}}` : '';
        const archStr = c.archetype || '';
        const passiveStr = c.passive ? `◈ ${{c.passive}}` : '';
        const evoStr = c.evolves_to ? `→ Lv.${{c.evo_level || '?'}}` : (c.evo_stage > 1 ? '★ Final form' : '✦ Singular');
        return `<div class="team-entry">
          <div class="te-row1">
            <span class="te-name">🐾 ${{c.species_name || c.species_id || '?'}}</span>
            <span class="te-meta">Lv.${{c.level || '?'}}</span>
            <span class="te-loyalty">♡ ${{Math.round(loy)}}%</span>
            <button class="btn btn-release" onclick="doRelease('${{c.instance_id}}', '${{c.species_name || c.species_id || '?'}}')">↩ Release</button>
          </div>
          <div class="te-species-info" style="display:flex;gap:8px;flex-wrap:wrap;font-size:10px;color:#7ea3cc;margin-bottom:3px;">
            ${{typeStr ? `<span style="background:#1e3a5f;padding:1px 5px;border-radius:3px;color:#93c5fd;">${{typeStr}}</span>` : ''}}
            ${{bstStr ? `<span title="Base Stat Total">${{bstStr}}</span>` : ''}}
            ${{archStr ? `<span style="color:#a78bfa;">${{archStr}}</span>` : ''}}
            ${{passiveStr ? `<span style="color:#6ee7b7;">${{passiveStr}}</span>` : ''}}
            ${{evoStr ? `<span style="color:#fbbf24;">${{evoStr}}</span>` : ''}}
          </div>
          <div class="te-fatigue-bar">
            <div class="te-fatigue-label">Fatigue: ${{fatLabel}} (${{fatPct}}%)</div>
            <div class="te-fatigue-track"><div class="te-fatigue-fill ${{fatClass}}" style="width:${{fatPct}}%"></div></div>
          </div>
          <div class="te-xp-bar">
            <div class="te-xp-label">${{xpLabel}}</div>
            <div class="te-xp-track"><div class="te-xp-fill" style="width:${{xpPct}}%"></div></div>
          </div>
        </div>`;
      }}).join('');
    }}
    // Populate breed parent dropdowns
    const _breedA = document.getElementById('breed-parent-a');
    const _breedB = document.getElementById('breed-parent-b');
    if (_breedA && _breedB) {{
      const _savedA = _breedA.value, _savedB = _breedB.value;
      const _opts = '<option value="">-- None --</option>' + teamData.map(c =>
        `<option value="${{c.instance_id}}">${{c.species_name || c.species_id || '?'}} Lv.${{c.level||'?'}} (${{c.instance_id}})</option>`
      ).join('');
      _breedA.innerHTML = _opts;
      _breedB.innerHTML = _opts;
      if (teamData.find(c => c.instance_id === _savedA)) _breedA.value = _savedA;
      if (teamData.find(c => c.instance_id === _savedB)) _breedB.value = _savedB;
    }}
    // Show/hide Rest button based on current node type
    const restBtn = document.getElementById('btn-rest');
    if (restBtn) {{
      const curNode = (mapR.nodes || []).find(n => (n.node_id || n.id) === stateR.player_location);
      const restEligible = curNode && (curNode.node_type === 'SETTLEMENT' || curNode.node_type === 'CITY');
      const hasTiredCreatures = teamData.some(c => (c.fatigue ?? 0) > 0);
      restBtn.style.display = (restEligible && hasTiredCreatures) ? 'inline-flex' : 'none';
    }}
    // Map
    const grid = document.getElementById('node-grid');
    const loc = stateR.player_location;
    const neighbors = new Set(mapR.neighbors || []);
    // Sort: current first, then neighbors, then rest -- so they're never cut by the 24-node slice
    const allNodes = (mapR.nodes || []).slice();
    allNodes.sort((a, b) => {{
      const aid = a.node_id || a.id, bid = b.node_id || b.id;
      const aScore = aid === loc ? 0 : neighbors.has(aid) ? 1 : 2;
      const bScore = bid === loc ? 0 : neighbors.has(bid) ? 1 : 2;
      return aScore - bScore;
    }});
    const playerGateState = mapR.player_gate_state || {{}};
    const _GATE_ICONS = {{ LEAGUE: '&#9876;', RESEARCH: '&#128300;', ECOLOGICAL: '&#127807;', ANOMALY: '&#9888;', ECONOMIC: '&#128178;', REPUTATION: '&#11088;' }};
    const _GATE_METRIC_LABELS = {{
      trainer_rating: 'rating', research_milestones: 'research', ecological_balance: 'ecology',
      anomaly_exposure: 'anomaly', economic_investment: 'economy', faction_standing: 'standing',
      exploration_score: 'explore', league_tier: 'tier',
    }};
    grid.innerHTML = allNodes.slice(0,24).map(n => {{
      const nid = n.node_id || n.id;
      const isCurrent = nid === loc;
      const isNeighbor = neighbors.has(nid);
      const biomeStr = Array.isArray(n.biome) ? n.biome[0] : (n.biome || '');
      // Gate badge logic: show only for neighbors (clicking matters)
      let gateBadge = '';
      let isGateLocked = false;
      if (isNeighbor && n.gate_detail) {{
        const gd = n.gate_detail;
        const metricVal = playerGateState[gd.metric] ?? 0;
        const needed = gd.effective_threshold;
        isGateLocked = metricVal < needed;
        const icon = _GATE_ICONS[gd.type] || '&#128274;';
        const metricLabel = _GATE_METRIC_LABELS[gd.metric] || gd.metric;
        const valDisplay = Number.isInteger(metricVal) ? metricVal : metricVal.toFixed(1);
        const needDisplay = Number.isInteger(needed) ? needed : needed.toFixed(1);
        const color = isGateLocked ? '#f87171' : '#4ade80';
        gateBadge = `<div class="node-gate" style="margin-top:4px;font-size:10px;color:${{color}}">${{icon}} ${{gd.type}} gate &mdash; ${{metricLabel}}: ${{valDisplay}}/${{needDisplay}}${{isGateLocked ? ' 🔒' : ' ✓'}}</div>`;
      }}
      const cls = isCurrent ? 'node-card current' : (isNeighbor && !isGateLocked) ? 'node-card neighbor' : isNeighbor ? 'node-card neighbor node-gated' : 'node-card';
      const action = isNeighbor ? `onclick="moveTo('${{nid}}')" title="${{isGateLocked ? 'Gate locked — see requirements below' : 'Move here'}}"` : '';
      return `<div class="${{cls}}" ${{action}}>
        <div class="node-name">${{n.name || nid}}</div>
        <div class="node-meta">${{n.node_type || biomeStr}}${{isCurrent ? ' &#9654; YOU ARE HERE' : ''}}${{isNeighbor && !isGateLocked ? ' &#9654; click to move' : ''}}</div>
        ${{gateBadge}}
      </div>`;
    }}).join('');
    // Fragments
    const fl = document.getElementById('fragment-list');
    // Show most-recently-discovered fragments first (reverse discovery order)
    const allFrags = (fragR.fragments || []).filter(f => f.discovered);
    const frags = allFrags.slice().reverse().slice(0,5);
    fl.innerHTML = frags.map(f => `<div class="fragment"><div class="ftype">${{f.type || 'UNKNOWN'}} · ${{f.title || ''}}</div><div class="ftext">${{f.body || ''}}</div></div>`).join('') || '<span style="color:#4b6080">No fragments discovered yet. Explore FACILITY, LANDMARK, or DUNGEON nodes.</span>';
    // Log
    const log = document.getElementById('log');
    const events = stateR.recent_events || [];
    _fmtEv = function(e) {{
      const t = e.tick || '?';
      const d = e.data || {{}};
      switch(e.type) {{
        case 'moved':
          return `<div class="entry entry-move">[T${{t}}] &#128205; Moved to <strong>${{d.name || d.node_id}}</strong> <span class="ev-meta">${{d.node_type || ''}}</span></div>`;
        case 'encounter': {{
          const sp = d.species || {{}};
          return `<div class="entry entry-enc">[T${{t}}] &#128049; Wild <strong>${{sp.name || '?'}}</strong> appeared! Lv.${{d.level||'?'}} ${{sp.primary_type||''}} ${{sp.rarity||''}}</div>`;
        }}
        case 'captured':
          return `<div class="entry entry-cap">[T${{t}}] &#9989; Captured <strong>${{d.species_name||'?'}}</strong> Lv.${{d.level||'?'}} &mdash; team ${{d.team_size}}/6</div>`;
        case 'capture_failed':
          return `<div class="entry entry-fail">[T${{t}}] &#10060; ${{d.message || (d.species_name||'?')+' escaped!'}}</div>`;
        case 'fragment_discovered':
          return `<div class="entry entry-frag">[T${{t}}] &#128196; Fragment: <em>${{d.title||d.fragment_id}}</em></div>`;
        case 'research_discovery':
          return `<div class="entry entry-research">[T${{t}}] &#128300; Research logged &mdash; score ${{d.research_score||'?'}}</div>`;
        case 'anomaly_event':
          return `<div class="entry entry-anomaly">[T${{t}}] &#9888;&#65039; Anomaly at ${{d.node_id||'?'}} (instab ${{d.instability != null ? (d.instability*100).toFixed(0)+'%' : '?'}})</div>`;
        case 'battle_result':
          return `<div class="entry entry-battle">[T${{t}}] ${{d.winner==='player'?'&#127942;':'&#128128;'}} Battle vs <strong>${{d.opponent_name||'?'}}</strong> &mdash; ${{d.winner==='player'?'Victory':'Defeat'}} (${{d.turns}} turns) Rating: ${{d.player_rating ? d.player_rating.toFixed(0) : '?'}}</div>`;
        case 'tick_update': {{
          const stability = d.ledger && d.ledger.stability_index != null ? d.ledger.stability_index.toFixed(1) : '?';
          return `<div class="entry entry-tick">[T${{t}}] &#9200; Ecosystem pulse &mdash; stability ${{stability}}</div>`;
        }}
        case 'tier_escalated': {{
          // BIBLE §21: never name the tier. Render as environmental/atmospheric narrative only.
          const atmosMap = {{
            TIER_II: '&#127756; The island feels different. Something has shifted.',
            TIER_III: '&#127757; Patterns you cannot name press at the edges of perception.',
            TIER_IV: '&#9888;&#65039; The environment has grown hostile. Systems strain.',
            TIER_V: '&#9888;&#65039; Everything is wrong. The substrate is failing.',
          }};
          const msg = atmosMap[d.current_tier] || '&#127756; The world has changed.';
          return `<div class="entry entry-anomaly" style="font-style:italic;">[T${{t}}] ${{msg}}</div>`;
        }}
        case 'released':
          return `<div class="entry entry-release">[T${{t}}] &#128039; Released ${{d.species_name||'?'}}</div>`;
        case 'rested': {{
          const n = (d.team_recovered || []).length;
          return `<div class="entry entry-release">[T${{t}}] &#128716; Rested ${{n}} creature${{n!==1?'s':''}} at ${{d.location||'?'}} &mdash; fatigue reduced</div>`;
        }}
        case 'breed_result': {{
          const traits = (d.traits || []).join(', ') || 'none';
          const depth = d.lineage_depth != null ? ` (lineage depth: ${{d.lineage_depth}})` : '';
          return `<div class="entry entry-cap">[T${{t}}] &#129516; Offspring created: ${{d.species||'?'}} ${{d.offspring_id||''}}&ndash; traits: ${{traits}}${{depth}}</div>`;
        }}
        case 'claimed': {{
          if (d.ok === false) {{
            return `<div class="entry entry-fail">[T${{t}}] &#10060; Claim failed &mdash; ${{d.message||'team full'}}</div>`;
          }}
          return `<div class="entry entry-cap">[T${{t}}] &#129309; ${{d.species_name||'?'}} joined your party! (loyalty: ${{d.loyalty||60}})</div>`;
        }}
        case 'memory_echo':
          return `<div class="entry entry-echo">[T${{t}}] &#10024; Memory echo&hellip; ${{d.message||''}}</div>`;
        case 'gate_blocked': {{
          const gateIcons = {{ LEAGUE: '&#9876;', RESEARCH: '&#128300;', ECOLOGICAL: '&#127807;', ANOMALY: '&#9888;', ECONOMIC: '&#128178;', REPUTATION: '&#11088;' }};
          const icon = gateIcons[d.gate_type] || '&#128274;';
          return `<div class="entry entry-fail">[T${{t}}] ${{icon}} Gate blocked &mdash; ${{d.gate_type || '?'}} &mdash; need ${{d.requirement || '?'}} &ge; ${{d.threshold != null ? d.threshold.toFixed(1) : '?'}}</div>`;
        }}
        case 'explored':
          return `<div class="entry entry-move">[T${{t}}] &#128269; Explored ${{d.node_type||'?'}}${{d.is_relay_node ? ' (relay)' : ''}}</div>`;
        case 'level_up':
          return `<div class="entry entry-cap" style="color:#fbbf24;font-weight:700;">[T${{t}}] &#11088; ${{d.species_name||'?'}} reached <strong>Level ${{d.new_level||'?'}}</strong>! (+${{d.xp_gained||0}} XP)</div>`;
        case 'evolved':
          return `<div class="entry entry-cap" style="color:#a78bfa;font-weight:700;border-left:3px solid #a78bfa;padding-left:6px;">[T${{t}}] &#10024; ${{d.old_species_name||'?'}} <span style="color:#6b7280">&#8594;</span> <strong style="color:#c4b5fd">${{d.new_species_name||'?'}}</strong> (Stage ${{d.new_evo_stage||'?'}}) at Lv${{d.level||'?'}}!</div>`;
        default:
          return `<div class="entry">[T${{t}}] ${{e.type}}: ${{JSON.stringify(d).slice(0,60)}}</div>`;
      }}
    }};
    log.innerHTML = events.slice(-20).reverse().map(_fmtEv).join('') || '<div class="entry">No events yet.</div>';
  }} catch(e) {{ console.error(e); }}
}}

async function moveTo(nodeId) {{
  const r = await fetch(API + '/api/move', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{node_id:nodeId}})}});
  const data = await r.json();
  if (!data.ok) {{
    const statusEl = document.getElementById('explore-status');
    if (statusEl) {{
      statusEl.style.color = '#e74c3c';
      // Build a richer message from the gate_blocked event if available
      const gateEv = (data.events || []).find(e => e.type === 'gate_blocked');
      if (gateEv && gateEv.data) {{
        const gd = gateEv.data;
        const gateLabels = {{ LEAGUE: 'League rank', RESEARCH: 'Research', ECOLOGICAL: 'Ecological balance', ANOMALY: 'Anomaly exposure', ECONOMIC: 'Economic standing', REPUTATION: 'Reputation' }};
        const label = gateLabels[gd.gate_type] || gd.gate_type || 'Unknown';
        statusEl.textContent = `\u26d4 Gate locked \u2014 need ${{label}} \u2265 ${{gd.threshold != null ? gd.threshold.toFixed(1) : '?'}} (yours: ${{(data.player_stats || {{}})[gd.requirement] != null ? (data.player_stats || {{}})[gd.requirement].toFixed(1) : '0.0'}})`;
      }} else {{
        statusEl.textContent = '\u26d4 ' + (data.error || 'Cannot move there');
      }}
      setTimeout(() => {{ statusEl.textContent = ''; statusEl.style.color = ''; }}, 4000);
    }}
    refresh();  // still refresh so gate_blocked appears in event log
    return;
  }}
  _activeEncounter = null;
  document.getElementById('encounter-area').innerHTML = '';
  refresh();
}}

async function doExplore() {{
  const btn = document.querySelector('.btn-primary');
  const status = document.getElementById('explore-status');
  btn.disabled = true;
  status.textContent = 'Exploring…';
  try {{
    const r = await fetch(API + '/api/explore', {{method:'POST'}});
    const data = await r.json();
    const events = data.events || [];
    if (events.length === 0) {{
      status.textContent = 'Nothing new found here.';
    }} else {{
      // Prioritize fragment_discovered summary
      const fragEv = events.find(e => e.type === 'fragment_discovered');
      if (fragEv) {{
        const d = fragEv.data || {{}};
        status.textContent = `Fragment discovered: "${{d.title || d.fragment_id}}" (${{d.total_discovered || '?'}} total)`;
      }} else if (events.find(e => e.type === 'anomaly_event')) {{
        const d = events.find(e => e.type === 'anomaly_event').data || {{}};
        status.textContent = `Anomaly detected! Instability: ${{(d.instability * 100).toFixed(0)}}%`;
      }} else if (events.find(e => e.type === 'research_discovery')) {{
        status.textContent = 'Research entry recorded.';
      }} else if (events.find(e => e.type === 'memory_echo')) {{
        status.textContent = 'Something stirs in this place…';
      }} else {{
        status.textContent = `Explored: ${{events.map(e => e.type).join(', ')}}`;
      }}
    }}
    refresh();
  }} catch(e) {{
    status.textContent = 'Error exploring.';
    console.error(e);
  }} finally {{
    btn.disabled = false;
  }}
}}

async function doEncounter() {{
  const btn = document.getElementById('enc-btn');
  const status = document.getElementById('enc-status');
  const area = document.getElementById('encounter-area');
  btn.disabled = true;
  status.textContent = 'Searching…';
  area.innerHTML = '';
  try {{
    const r = await fetch(API + '/api/encounter', {{method:'POST'}});
    const data = await r.json();
    if (data.no_encounter || !data.encounter) {{
      status.textContent = data.message || 'Nothing appeared.';
    }} else {{
      const enc = data.encounter;
      _activeEncounter = enc;
      const sp = enc.species || {{}};
      const docility = enc.temperament != null ? (1 - enc.temperament) * 100 : '?';
      const catchPct = enc.temperament != null ? Math.round((0.45 + (1 - enc.temperament) * 0.45) * 100) : '?';
      status.textContent = `A wild ${{sp.name || enc.instance_id}} appeared!`;
      area.innerHTML = `<div class="encounter-card">
        <div class="enc-name">${{sp.name || '???'}} <span style="font-size:12px;color:#4b6080">Lv.${{enc.level || '?'}}</span></div>
        <div class="enc-meta">
          Type: ${{sp.primary_type || '?'}} | Docility: ${{typeof docility === 'number' ? docility.toFixed(0) + '%' : '?'}} | Catch chance: ≈${{catchPct}}%
        </div>
        <div class="enc-actions">
          <button class="btn btn-capture" onclick="doCapture('${{enc.instance_id}}')">⚡ Capture</button>
          <button class="btn btn-flee" onclick="clearEncounter()">🏃 Flee</button>
        </div>
      </div>`;
    }}
    refresh();
  }} catch(e) {{
    status.textContent = 'Error.';
    console.error(e);
  }} finally {{
    btn.disabled = false;
  }}
}}

async function doCapture(instanceId) {{
  const area = document.getElementById('encounter-area');
  const status = document.getElementById('enc-status');
  // Disable capture/flee buttons
  area.querySelectorAll('button').forEach(b => b.disabled = true);
  status.textContent = 'Throwing…';
  try {{
    const r = await fetch(API + '/api/capture', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{instance_id:instanceId}})}});
    const data = await r.json();
    if (data.result === 'captured') {{
      const d = data.detail || {{}};
      status.textContent = `✅ Caught ${{d.species_name || '?'}}! Team: ${{data.team_size}}/6`;
      area.innerHTML = `<div style="padding:10px;color:#4ade80;font-size:13px;">
        ✅ ${{d.species_name || '?'}} (Lv.${{d.level || '?'}}) was added to your team!
        <br><span style="color:#4b6080;font-size:11px;">Total captured: ${{data.creatures_captured}}</span>
      </div>`;
      _activeEncounter = null;
    }} else if (data.result === 'capture_failed') {{
      const d = data.detail || {{}};
      status.textContent = `❌ ${{d.message || 'Escaped!'}}`;
      area.innerHTML = `<div style="padding:10px;color:#f87171;font-size:13px;">
        ❌ ${{d.message || d.species_name + ' escaped!'}}
        <br><span style="color:#4b6080;font-size:11px;">${{d.reason === 'team_full' ? 'Release a creature to make room.' : 'Try again or flee.'}}</span>
      </div>${{d.reason !== 'team_full' ? `<div style="margin-top:8px;"><button class="btn btn-capture" onclick="doCapture('${{instanceId}}')">⚡ Try Again</button> <button class="btn btn-flee" onclick="clearEncounter()">🏃 Flee</button></div>` : ''}}`;
    }} else {{
      status.textContent = data.error || 'Unknown result.';
      area.innerHTML = '';
    }}
    refresh();
  }} catch(e) {{
    status.textContent = 'Error during capture.';
    console.error(e);
  }}
}}

function clearEncounter() {{
  _activeEncounter = null;
  document.getElementById('encounter-area').innerHTML = '';
  document.getElementById('enc-status').textContent = '';
}}

async function doRelease(instanceId, speciesName) {{
  if (!confirm(`Release ${{speciesName}} back into the wild? This cannot be undone.`)) return;
  try {{
    const r = await fetch(API + '/api/release', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{instance_id:instanceId}})}});
    const data = await r.json();
    if (data.result === 'released') {{
      const d = data.detail || {{}};
      // Show brief notification in team section heading area
      const tl = document.getElementById('team-list');
      const note = document.createElement('div');
      note.style.cssText = 'color:#4b6080;font-size:11px;padding:4px 0;';
      note.textContent = d.message || `${{speciesName}} released. Team: ${{data.team_size}}/6`;
      tl.prepend(note);
      setTimeout(() => note.remove(), 3000);
    }} else {{
      alert((data.detail && data.detail.message) || data.error || 'Release failed.');
    }}
    refresh();
  }} catch(e) {{
    alert('Error during release: ' + e);
  }}
}}

async function doRestTeam() {{
  const btn = document.getElementById('btn-rest');
  const status = document.getElementById('rest-status');
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Resting…';
  try {{
    const r = await fetch(API + '/api/rest', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:'{{}}'}});
    const data = await r.json();
    if (data.ok && data.result === 'rested') {{
      const d = data.detail || {{}};
      if (status) {{ status.textContent = d.message || 'Team rested!'; setTimeout(() => {{ if (status) status.textContent = ''; }}, 4000); }}
    }} else {{
      const msg = (data.detail && data.detail.message) || data.error || 'Cannot rest here.';
      if (status) {{ status.textContent = '⚠ ' + msg; setTimeout(() => {{ if (status) status.textContent = ''; }}, 4000); }}
    }}
    refresh();
  }} catch(e) {{
    if (status) status.textContent = 'Error: ' + e;
  }} finally {{
    if (btn) btn.disabled = false;
  }}
}}

async function doBreed() {{
  const selA = document.getElementById('breed-parent-a');
  const selB = document.getElementById('breed-parent-b');
  const status = document.getElementById('breed-status');
  const area = document.getElementById('breed-result-area');
  const btn = document.getElementById('btn-breed');
  const parentA = selA ? selA.value : '';
  const parentB = selB ? selB.value : '';
  if (!parentA || !parentB) {{
    if (status) status.textContent = '⚠ Select both parents first.';
    return;
  }}
  if (parentA === parentB) {{
    if (status) status.textContent = '⚠ Parents must be different creatures.';
    return;
  }}
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Breeding…';
  if (area) area.innerHTML = '';
  try {{
    const r = await fetch(API + '/api/breed', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{parent_a_id: parentA, parent_b_id: parentB}})
    }});
    const data = await r.json();
    if (data.result === 'bred') {{
      const d = data.detail || {{}};
      const traits = (d.traits || []).join(', ') || 'none';
      const depth = d.lineage_depth != null ? `Lineage depth: ${{d.lineage_depth}}` : '';
      if (status) {{ status.textContent = `✅ Offspring created! (ID: ${{d.offspring_id || '?'}})`; setTimeout(() => {{ if (status) status.textContent = ''; }}, 5000); }}
      if (area) area.innerHTML = `<div style="padding:10px;background:#0d2a1a;border-radius:6px;border:1px solid #4ade80;font-size:12px;color:#c9d5e4;margin-top:8px;">
        <div style="color:#4ade80;font-weight:bold;margin-bottom:4px;">&#129516; Offspring: ${{d.species || '?'}} (Lv.1)</div>
        <div style="color:#7ee8a0;">Traits: ${{traits}}</div>
        ${{depth ? `<div style="color:#4b6080;font-size:11px;">${{depth}}</div>` : ''}}
        <div style="color:#4b6080;font-size:11px;margin-top:4px;">Breeds completed: ${{data.breeds_completed || '?'}}</div>
        <div style="margin-top:8px;">
          <button class="btn btn-sm" style="background:#1e3a5f;border:1px solid #4ade80;color:#4ade80;padding:3px 10px;cursor:pointer;" onclick="doClaimOffspring('${{d.offspring_id}}', this)">&#129309; Claim Offspring</button>
          <span id="claim-status-${{d.offspring_id}}" style="margin-left:8px;font-size:11px;color:#7ee8a0;"></span>
        </div>
      </div>`;
    }} else if (data.result === 'error' || !data.ok) {{
      const msg = data.error || 'Breeding failed.';
      if (status) {{ status.textContent = '&#10060; ' + msg; setTimeout(() => {{ if (status) status.textContent = ''; }}, 5000); }}
    }} else {{
      if (status) {{ status.textContent = 'Unknown result.'; setTimeout(() => {{ if (status) status.textContent = ''; }}, 3000); }}
    }}
    refresh();
  }} catch(e) {{
    if (status) status.textContent = 'Error: ' + e;
    console.error(e);
  }} finally {{
    if (btn) btn.disabled = false;
  }}
}}

async function doClaimOffspring(offspringId, btn) {{
  if (!offspringId) return;
  const statusEl = document.getElementById('claim-status-' + offspringId);
  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = 'Claiming…';
  try {{
    const r = await fetch(API + '/api/claim', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{instance_id: offspringId}})
    }});
    const data = await r.json();
    if (data.result === 'claimed') {{
      const d = data.detail || {{}};
      if (statusEl) statusEl.textContent = '✅ ' + (d.message || 'Joined your party!');
      if (btn) btn.textContent = '✅ Claimed';
      refresh();
    }} else {{
      const msg = data.error || 'Claim failed.';
      if (statusEl) statusEl.textContent = '❌ ' + msg;
      if (btn) btn.disabled = false;
    }}
  }} catch(e) {{
    if (statusEl) statusEl.textContent = 'Error: ' + e;
    if (btn) btn.disabled = false;
    console.error(e);
  }}
}}

async function loadTrainers() {{
  const status = document.getElementById('battle-status');
  const list = document.getElementById('trainer-list');
  status.textContent = 'Loading…';
  try {{
    // Pre-flight: check team fatigue from last known stateR
    if (stateR && stateR.player_team && stateR.player_team.length > 0) {{
      const allExhausted = stateR.player_team.every(c => (c.fatigue || 0) >= 80);
      if (allExhausted) {{
        list.innerHTML = '<div style="color:#f87171;padding:8px;font-size:12px;">⚠️ All creatures are exhausted (fatigue ≥ 80). Rest at a settlement or city before battling.</div>';
        status.textContent = 'Team exhausted — rest first.';
        return;
      }}
    }}
    const r = await fetch(API + '/api/trainers');
    const data = await r.json();
    const trainers = data.trainers || [];
    if (!trainers.length) {{
      list.innerHTML = '<span style="color:#4b6080">No trainers available near current location.</span>';
      status.textContent = '';
      return;
    }}
    const myRating = data.player_rating != null ? Math.round(data.player_rating) : 1200;
    const myWins = data.player_wins ?? 0;
    const myLosses = data.player_losses ?? 0;
    list.innerHTML = `<div style="font-size:11px;color:#60a5fa;margin-bottom:8px;padding:4px 0 4px 0;border-bottom:1px solid #1e3a5f;">Your rating: <strong>${{myRating}}</strong> &nbsp; W/L: ${{myWins}}/${{myLosses}}</div>` + trainers.map(t => {{
      const delta = t.rating - myRating;
      const deltaStr = delta > 0 ? `<span style="color:#f87171">+${{Math.round(delta)}}</span>` : `<span style="color:#4ade80">${{Math.round(delta)}}</span>`;
      return `<div class="trainer-card">
        <span class="tr-name">🧑 ${{t.name || t.trainer_id}}</span>
        <span class="tr-meta">Rating: ${{Math.round(t.rating || 1200)}} (${{deltaStr}}) &middot; ${{t.tier || 'LOCAL'}} &middot; ${{t.team_size || 0}} creatures</span>
        <button class="btn btn-battle" onclick="doBattle('${{t.trainer_id}}')">⚔️ Battle</button>
      </div>`;
    }}).join('');
    status.textContent = `${{trainers.length}} trainer${{trainers.length !== 1 ? 's' : ''}} found`;
  }} catch(e) {{
    status.textContent = 'Error loading trainers.';
    console.error(e);
  }}
}}

async function doBattle(trainerId) {{
  const status = document.getElementById('battle-status');
  const resultArea = document.getElementById('battle-result-area');
  // Disable all battle buttons during fight
  document.querySelectorAll('.btn-battle').forEach(b => b.disabled = true);
  status.textContent = 'Battling…';
  resultArea.innerHTML = '';
  try {{
    const r = await fetch(API + '/api/battle', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{trainer_id: trainerId}})
    }});
    const data = await r.json();
    if (data.error) {{
      status.textContent = data.error;
      resultArea.innerHTML = `<div style="color:#f87171;font-size:12px;padding:8px;">${{data.error}}</div>`;
      return;
    }}
    const won = data.winner === 'player';
    const cls = won ? 'battle-result won' : 'battle-result lost';
    const icon = won ? '🏆' : '💀';
    const msg = won ? `Victory against ${{data.opponent_name || trainerId}}!` : `Defeated by ${{data.opponent_name || trainerId}}.`;
    const moveLog = data.moves_used || [];
    // Collect level-up and evolution events from the event stream
    const allEvs = data.events || [];
    const levelUps = allEvs.filter(e => e.type === 'level_up');
    const evolutions = allEvs.filter(e => e.type === 'evolved');
    const notableHtml = [
      ...levelUps.map(e => {{
        const d2 = e.data || e;
        return `<div class="br-notable br-levelup">⭐ ${{d2.species_name || '?'}} reached <strong>Level ${{d2.new_level || '?'}}</strong>! (+${{d2.xp_gained || 0}} XP)</div>`;
      }}),
      ...evolutions.map(e => {{
        const d2 = e.data || e;
        return `<div class="br-notable br-evolved">✨ ${{d2.old_species_name || '?'}} <span style="color:#9ca3af">→</span> <strong style="color:#c4b5fd">${{d2.new_species_name || '?'}}</strong> (Stage ${{d2.new_evo_stage || '?'}})</div>`;
      }})
    ].join('');
    resultArea.innerHTML = `<div class="${{cls}}">
      <div class="br-winner">${{icon}} ${{msg}}</div>
      <div class="br-stats">
        Turns: ${{data.turns || '?'}} · Player remaining: ${{data.player_remaining ?? '?'}} · Opponent remaining: ${{data.opponent_remaining ?? '?'}}
        ${{data.player_rating ? ` · New rating: ${{Math.round(data.player_rating)}}` : ''}}
      </div>
      ${{notableHtml}}
      ${{moveLog.length ? `<div class="battle-log">${{moveLog.map(m => `<div>${{m}}</div>`).join('')}}</div>` : ''}}
    </div>`;
    status.textContent = won ? 'You won!' : 'You lost.';
    refresh();
  }} catch(e) {{
    status.textContent = 'Error during battle.';
    console.error(e);
  }} finally {{
    document.querySelectorAll('.btn-battle').forEach(b => b.disabled = false);
  }}
}}

async function loadNGPStatus() {{
  try {{
    const r = await fetch(API + '/api/expedition/status');
    const data = await r.json();
    const infoEl = document.getElementById('ngp-info');
    const profileEl = document.getElementById('ngp-profile-area');
    if (data.error) {{
      infoEl.textContent = data.error;
      return;
    }}
    const run = data.ngp_run || 0;
    infoEl.textContent = `Island: ${{data.island_name || '?'}} · Seed: ${{data.seed ?? '?'}} · Tick: ${{data.tick ?? '?'}} · Tier: ${{data.current_tier || '?'}} · Expedition #${{run + 1}}`;
    const sp = data.saved_profile;
    if (sp) {{
      const tierName = sp.completed_tier ? ('TIER_' + ['I','II','III','IV','V'][sp.completed_tier - 1] || sp.completed_tier) : '?';
      profileEl.innerHTML = `<div style="background:#101827;border:1px solid #1e3a5f;border-radius:6px;padding:8px;font-size:11px;color:#94a3b8;">
        <strong style="color:#60a5fa;">🧬 Saved Profile (Run #${{sp.run_count || '?'}})</strong><br>
        Axis: <span style="color:#e2e8f0;">${{sp.behavioral_axis || '?'}}</span> ·
        Completed Tier: <span style="color:#e2e8f0;">${{tierName}}</span><br>
        Anomaly Engagement: ${{(sp.anomaly_engagement_history || 0).toFixed(1)}} ·
        Disruption: ${{(sp.ecological_disruption_pattern || 0).toFixed(2)}} ·
        D/H Bias: ${{(sp.dominance_harmony_bias || 0).toFixed(2)}}
        ${{sp.echo_seeds?.length ? ` · Echo Seeds: ${{sp.echo_seeds.length}}` : ''}}
      </div>`;
    }} else {{
      profileEl.innerHTML = '<div style="font-size:11px;color:#4b6080;">No saved profile yet. Complete an expedition to create one.</div>';
    }}
  }} catch(e) {{ console.error('NGP status error:', e); }}
}}

async function doCompleteExpedition() {{
  const btn = document.getElementById('ngp-complete-btn');
  const status = document.getElementById('ngp-status');
  if (!confirm('Complete this expedition and begin a new one? Your behavioral profile will carry over.')) return;
  btn.disabled = true;
  status.textContent = '⏳ Completing expedition…';
  try {{
    const r = await fetch(API + '/api/expedition/complete', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{}})
    }});
    const data = await r.json();
    if (data.error) {{
      status.style.color = '#e74c3c';
      status.textContent = '❌ ' + data.error;
      return;
    }}
    const tier  = data.final_tier || '?';
    const axis  = data.behavioral_axis || '?';
    const ticks = data.ticks_played || '?';
    const newName = data.new_island?.island_name || '?';
    const newSeed = data.next_seed || '?';
    status.style.color = '#4ade80';
    status.textContent = `✅ Expedition complete (Tier: ${{tier}}, Axis: ${{axis}}, Ticks: ${{ticks}}) → New island: ${{newName}} (seed ${{newSeed}})`;
    setTimeout(() => {{ status.textContent = ''; status.style.color = ''; }}, 6000);
    _activeEncounter = null;
    document.getElementById('encounter-area').innerHTML = '';
    await loadNGPStatus();
    refresh();
  }} catch(e) {{
    status.style.color = '#e74c3c';
    status.textContent = '❌ Expedition complete failed: ' + e.message;
  }} finally {{
    btn.disabled = false;
  }}
}}

async function doResetSimulation() {{
  const btn = document.getElementById('ngp-reset-btn');
  const status = document.getElementById('ngp-status');
  if (!confirm('Reset simulation? This deletes your behavioral profile and returns to Tier I (seed 42). All progress is lost.')) return;
  btn.disabled = true;
  status.textContent = '⏳ Resetting simulation…';
  try {{
    const r = await fetch(API + '/api/expedition/reset', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{}})
    }});
    const data = await r.json();
    if (data.error) {{
      status.style.color = '#e74c3c';
      status.textContent = '❌ ' + data.error;
      return;
    }}
    const newName = data.new_island?.island_name || '?';
    const newSeed = data.new_seed || 42;
    status.style.color = '#94a3b8';
    status.textContent = `🔄 Simulation reset → ${{newName}} (seed ${{newSeed}}, Tier I)`;
    setTimeout(() => {{ status.textContent = ''; status.style.color = ''; }}, 5000);
    _activeEncounter = null;
    document.getElementById('encounter-area').innerHTML = '';
    await loadNGPStatus();
    refresh();
  }} catch(e) {{
    status.style.color = '#e74c3c';
    status.textContent = '❌ Reset failed: ' + e.message;
  }} finally {{
    btn.disabled = false;
  }}
}}

async function loadIdentity() {{
  const area = document.getElementById('identity-area');
  try {{
    const r = await fetch(API + '/api/outcome');
    const data = await r.json();
    if (data.error) {{
      area.innerHTML = '<span style="color:#4b6080">' + data.error + '</span>';
      return;
    }}
    const role = data.narrative_role || '—';
    const archetype = data.personal_archetype || '—';
    const condition = data.island_condition || '—';
    const summary = data.summary || '';
    area.innerHTML = `<div class="identity-panel">
      <div class="identity-role">&#x1F9ED; ${{role}}</div>
      <div class="identity-archetype">Personal archetype: ${{archetype}}</div>
      <div class="identity-condition">Island condition: ${{condition}}</div>
      <div class="identity-summary">${{summary}}</div>
    </div>`;
  }} catch(e) {{
    area.innerHTML = '<span style="color:#f87171">Error loading identity.</span>';
    console.error(e);
  }}
}}

async function loadFactions() {{
  const area = document.getElementById('faction-area');
  try {{
    const r = await fetch(API + '/api/factions');
    if (!r.ok) {{ area.innerHTML = '<span style="color:#4b6080">Faction data unavailable.</span>'; return; }}
    const data = await r.json();
    const factions = data.factions || [];
    if (!factions.length) {{ area.innerHTML = '<span style="color:#4b6080">No factions on this island.</span>'; return; }}
    const rows = factions.map(f => {{
      const standing = f.standing || 0;
      const pct = Math.abs(standing) * 100;
      const fillClass = standing > 0.05 ? 'fbar-positive' : (standing < -0.05 ? 'fbar-negative' : 'fbar-neutral');
      const valStr = (standing >= 0 ? '+' : '') + (standing * 100).toFixed(0);
      const barStyle = `width:${{Math.min(100,pct).toFixed(1)}}%;`;
      const archLabel = (f.archetype || '').replace(/_/g, ' ');
      return `<div class="faction-row">
        <div style="flex:1;min-width:0;">
          <div class="faction-name">${{f.name || f.faction_id}}</div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:3px;">
            <span class="faction-arch">${{archLabel}}</span>
            <span class="faction-territory">&#x1F5FA;&#xFE0F; ${{f.territory_count||0}} nodes</span>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          <div class="faction-bar-wrap">
            <div class="faction-bar-fill ${{fillClass}}" style="${{barStyle}}"></div>
          </div>
          <div class="faction-val">${{valStr}}</div>
        </div>
      </div>`;
    }});
    area.innerHTML = `<div class="faction-panel">${{rows.join('')}}</div>`;
  }} catch(e) {{
    area.innerHTML = '<span style="color:#f87171">Error loading factions.</span>';
    console.error(e);
  }}
}}

let _knowerFragmentIndex = 0;
let _knowerTotalFragments = 0;

async function loadKnower() {{
  const area = document.getElementById('knower-area');
  const status = document.getElementById('knower-status');
  status.textContent = 'Sensing\u2026';
  area.innerHTML = '';
  try {{
    const r = await fetch(API + '/api/knower');
    const data = await r.json();
    if (data.error) {{
      status.textContent = data.error;
      return;
    }}
    const unlocked = data.is_unlocked;
    if (!unlocked) {{
      const thresholds = data.unlock_thresholds || {{}};
      const keys = Object.entries(thresholds).map(([k,v]) => k.replace(/_/g,' ') + ' \u2265 ' + v).join(', ');
      area.innerHTML = `<div class="knower-panel">
        <div class="knower-name">\U0001f52e ${{data.name || 'Unknown'}}</div>
        <div class="knower-archetype">${{(data.archetype || '').replace(/_/g,' ')}}</div>
        <div class="knower-hint">${{data.hint || 'A presence waits.'}}</div>
        <div class="knower-locked">Not yet unlocked. Requirements: ${{keys || 'explore more'}}</div>
      </div>`;
      status.textContent = 'Presence sensed \u2014 not yet reachable';
    }} else {{
      _knowerFragmentIndex = 0;
      _knowerTotalFragments = data.total_fragments || 1;
      status.textContent = 'Knower unlocked';
      await doTalkKnower(0);
    }}
  }} catch(e) {{
    status.textContent = 'Error.';
    console.error(e);
  }}
}}

// ── NPC Voice helper ──────────────────────────────────────────────────────────
// nkSpeak(text, archetype, npcName): routes NPC speech through /api/speak
// (server-side: SAPI on standalone, Radio OS TTS provider on full stack).
// Fire-and-forget — never blocks the UI.
function nkSpeak(text, archetype, npcName) {{
  if (!text || !text.trim()) return;
  fetch(API + '/api/speak', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{text: text, archetype: archetype || 'DEFAULT', npc_name: npcName || ''}})
  }}).catch(() => {{/* voice failure is non-fatal */}});
}}

async function doTalkKnower(fragIdx) {{
  const area = document.getElementById('knower-area');
  const status = document.getElementById('knower-status');
  try {{
    const r = await fetch(API + '/api/knower/talk', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{fragment_index: fragIdx}})
    }});
    const data = await r.json();
    if (data.event_type === 'knower_locked') {{
      area.innerHTML = `<div class="knower-panel">
        <div class="knower-name">\U0001f52e ${{data.name || '?'}}</div>
        <div class="knower-hint">${{data.hint || 'Not yet unlocked.'}}</div>
      </div>`;
      return;
    }}
    _knowerTotalFragments = data.total_fragments || 1;
    _knowerFragmentIndex = data.fragment_index ?? fragIdx;
    const frag = data.fragment || '';
    area.innerHTML = `<div class="knower-panel">
      <div class="knower-name">\U0001f52e ${{data.name || '?'}}</div>
      <div class="knower-archetype">${{(data.archetype || '').replace(/_/g,' ')}}</div>
      <div class="knower-fragment">${{frag.replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>')}}</div>
      <div class="knower-nav">
        <button class="btn btn-knower" onclick="doTalkKnower(${{Math.max(0,_knowerFragmentIndex-1)}})" ${{_knowerFragmentIndex<=0?'disabled':''}}>&#x25C0; Prev</button>
        <span>${{_knowerFragmentIndex+1}} / ${{_knowerTotalFragments}}</span>
        <button class="btn btn-knower" onclick="doTalkKnower(${{_knowerFragmentIndex+1}})" ${{_knowerFragmentIndex>=_knowerTotalFragments-1?'disabled':''}}>Next &#x25B6;</button>
      </div>
    </div>`;
    status.textContent = `Fragment ${{_knowerFragmentIndex+1}} of ${{_knowerTotalFragments}}`;
    // Auto-speak: send dialogue to NPC voice pipeline (SAPI on standalone, TTS on full stack)
    nkSpeak(frag, data.archetype || 'DEFAULT', data.name || 'Knower');
  }} catch(e) {{
    status.textContent = 'Error.';
    console.error(e);
  }}
}}




const _RARITY_COLOR = {{
  common: '#9ca3af', uncommon: '#34d399', rare: '#60a5fa',
  elite: '#a78bfa', apex: '#f59e0b', anomaly: '#f43f5e'
}};

async function loadLocal() {{
  const area = document.getElementById('local-area');
  if (!area) return;
  try {{
    const r = await fetch(API + '/api/local');
    const d = await r.json();
    if (d.error) {{
      area.innerHTML = '<span style="color:#4b6080">' + d.error + '</span>';
      return;
    }}

    const relayBadge = d.is_relay_node
      ? '<span style="margin-left:6px;font-size:10px;padding:1px 5px;border-radius:4px;background:#1e3a5f;color:#60a5fa;">RELAY</span>'
      : '';
    const startBadge = d.is_start
      ? '<span style="margin-left:6px;font-size:10px;padding:1px 5px;border-radius:4px;background:#1e3a5f;color:#4ade80;">START</span>'
      : '';

    // Biome bar
    const bm = d.biome;
    const biomeHtml = `<div class="local-biome">
      🌡️ Temp <b>${{Math.round(bm.temperature*100)}}%</b> &nbsp;
      💧 Moisture <b>${{Math.round(bm.moisture*100)}}%</b> &nbsp;
      ⛰️ Elevation <b>${{Math.round(bm.elevation*100)}}%</b> &nbsp;
      🌱 Veg <b>${{Math.round(bm.vegetation_density*100)}}%</b> &nbsp;
      ⚡ Instability <b style="color:${{bm.instability_bias>0.35?'#f43f5e':bm.instability_bias>0.2?'#f59e0b':'#4ade80'}}">${{Math.round(bm.instability_bias*100)}}%</b>
    </div>`;

    // Encounter table
    let encHtml = '';
    if (!d.can_encounter) {{
      encHtml = '<p class="local-no-enc">No wild encounters at this node type.</p>';
    }} else if (!d.encounter_table) {{
      encHtml = '<p class="local-no-enc">No encounter data available.</p>';
    }} else {{
      const et = d.encounter_table;
      encHtml = `<div style="margin-bottom:4px;font-size:11px;color:#4b6080;">${{et.total_species}} species in local pool</div>`;
      for (const slot of et.slots) {{
        const tc = slot.tier.toLowerCase();
        const col = _RARITY_COLOR[tc] || '#9ca3af';
        const pct = Math.round(slot.encounter_rate * 100);
        const chips = slot.species.map(sp => {{
          const types = sp.secondary_type
            ? `${{sp.primary_type}}/${{sp.secondary_type}}`
            : sp.primary_type;
          const evoArrow = sp.evolves_to ? ' →' : '';
          return `<span class="enc-species-chip" title="BST: ${{sp.bst}} | ${{sp.archetype}} | ${{sp.passive}}${{evoArrow}}" style="color:${{col}}">${{sp.name}}</span>`;
        }}).join('');
        encHtml += `<div class="enc-tier">
          <div class="enc-tier-label t-${{tc}}" style="color:${{col}}">${{slot.tier.toUpperCase()}} <span style="font-weight:400;color:#4b6080">(${{pct}}% rate)</span></div>
          <div class="enc-species-list">${{chips}}</div>
        </div>`;
      }}
    }}

    // Neighbor gates
    let gatesHtml = '';
    if (d.neighbor_gates && d.neighbor_gates.length > 0) {{
      gatesHtml = '<div style="margin-top:10px;font-size:11px;font-weight:600;color:#4b6080;margin-bottom:4px;">NEARBY GATES</div>';
      for (const g of d.neighbor_gates) {{
        const cls = g.passable ? 'gate-open' : 'gate-locked';
        const icon = g.passable ? '✅' : '🔒';
        gatesHtml += `<div class="gate-row ${{cls}}">${{icon}} <b>${{g.node_name}}</b> — ${{g.gate_type}}: ${{g.metric}} ≥ ${{g.effective_threshold}} (you: ${{g.player_value}})</div>`;
      }}
    }}

    area.innerHTML = `<div class="local-panel">
      <div class="local-node-name">${{d.node_name}}${{relayBadge}}${{startBadge}}</div>
      <div class="local-meta">${{d.node_type}} · ${{d.region}} · tick ${{d.tick}}</div>
      ${{biomeHtml}}
      ${{encHtml}}
      ${{gatesHtml}}
    </div>`;
  }} catch(e) {{
    area.innerHTML = '<span style="color:#f87171">Error loading local intel.</span>';
    console.error(e);
  }}
}}

refresh();
loadNGPStatus();
loadIdentity();
loadFactions();
loadLocal();
setInterval(refresh, 3000);
setInterval(loadNGPStatus, 15000);
setInterval(loadIdentity, 15000);
setInterval(loadFactions, 20000);
setInterval(loadLocal, 5000);
</script>
</script>
</body>
</html>""")

        @app.get("/api/state")
        def get_state():
            if controller and controller._state:
                st = controller._state
                return {
                    "seed": st.seed,
                    "tick": st.tick,
                    "island_name": st.topology.island_name,
                    "climate": st.topology.climate.name if st.topology.climate else "TEMPERATE",
                    "node_count": st.topology.node_count,
                    "species_count": len(st.species_map),
                    "discovered_species": len(st.discovered_species) if hasattr(st, 'discovered_species') else 0,
                    "ledger": st.ledger.to_dict(),
                    "player_location": st.player_location,
                    "containment_tier": st.current_tier.name if st.current_tier else "TIER_I",
                    "trajectory": st.player_trajectory.to_dict(),
                    "player_team_size": len(st.player_team),
                    "player_team": [
                        {
                            "instance_id": iid,
                            "species_id": sid,
                            "level": st.creatures[iid].level if iid in st.creatures else 1,
                            "xp": st.creatures[iid].xp if iid in st.creatures else 0,
                            "xp_floor": xp_for_level(st.creatures[iid].level) if iid in st.creatures else 0,
                            "xp_to_next": xp_to_next_level(st.creatures[iid].level) if iid in st.creatures else 10,
                            "species_name": st.species_map[sid].name if sid in st.species_map else sid,
                            "fatigue": round(st.creatures[iid].fatigue, 1) if iid in st.creatures else 0.0,
                            "loyalty": round(st.creatures[iid].loyalty, 1) if iid in st.creatures else 0.0,
                            # Species snapshot: type, combat power, passive — lets UI show combat readiness
                            "primary_type": st.species_map[sid].primary_type.name if sid in st.species_map else None,
                            "secondary_type": st.species_map[sid].secondary_type.name if sid in st.species_map and st.species_map[sid].secondary_type else None,
                            "bst": st.species_map[sid].base_stats.bst if sid in st.species_map else None,
                            "passive": st.species_map[sid].passive_trait if sid in st.species_map else None,
                            "archetype": st.species_map[sid].stat_archetype.name if sid in st.species_map else None,
                            "evo_stage": st.species_map[sid].evolution_stage if sid in st.species_map else None,
                            "evo_line": st.species_map[sid].evolution_line_id if sid in st.species_map else None,
                            "evolves_to": st.species_map[sid].evolves_to if sid in st.species_map else None,
                            "evo_level": (
                                (16 if st.species_map[sid].evolution_stage == 1 else
                                 (36 if st.species_map[sid].evolution_stage == 2 else None))
                                if (sid in st.species_map and st.species_map[sid].evolves_to)
                                else None
                            ) if sid in st.species_map else None,
                        }
                        for iid, sid in st.player_team
                    ],
                    "recent_events": list(controller._recent_events[-20:]),
                    "player_rating": round(st.league.trainers["player"].rating, 1) if "player" in st.league.trainers else 1200,
                    "player_wins": st.league.trainers["player"].wins if "player" in st.league.trainers else 0,
                    "player_losses": st.league.trainers["player"].losses if "player" in st.league.trainers else 0,
                }
            return {"error": "not initialized"}

        @app.post("/api/command")
        async def post_command(cmd: dict):
            if controller:
                controller._cmd_q.put(cmd)
                return {"status": "queued"}
            return {"error": "not initialized"}

        @app.get("/api/map")
        def get_map():
            if controller and controller._state:
                st = controller._state
                current_node = st.topology.nodes.get(st.player_location)
                neighbors = list(current_node.neighbors) if current_node else []
                # Build player state snapshot so the UI can compare against gate thresholds
                best_standing = max(
                    (st.faction_standings.get(fid, 0.0) for fid in st.factions),
                    default=0.0,
                )
                player_gate_state = {
                    "trainer_rating": st.league.trainers.get("player", Trainer()).rating,
                    "faction_standing": best_standing,
                    "research_milestones": st.player_trajectory.research_investment / 20.0,
                    "ecological_balance": st.ledger.ecological_balance,
                    "anomaly_exposure": st.player_trajectory.anomaly_exposure,
                    "economic_investment": st.ledger.urbanization_level,
                    "exploration_score": st.player_trajectory.exploration_depth,
                    "league_tier": 1.0 + st.player_trajectory.competitive_focus / 33.0,
                }
                return {
                    "nodes": [nd.to_dict() for nd in st.topology.nodes.values()],
                    "start": st.topology.start_node_id,
                    "current": st.player_location,
                    "neighbors": neighbors,
                    "player_gate_state": player_gate_state,
                }
            return {"error": "not initialized"}

        @app.post("/api/move")
        async def post_move(data: dict):
            if not controller:
                return {"error": "not initialized"}
            node_id = data.get("node_id", "")
            if not node_id:
                return {"error": "node_id required"}
            import asyncio as _asyncio
            # Snapshot current location and event-seq BEFORE queuing command.
            # We use event_seq (not len) to detect when the controller thread fires
            # any event (moved OR error/gate_blocked) so we never silently succeed.
            before_seq = controller._event_seq
            loc_before = controller._state.player_location if controller._state else None
            controller._cmd_q.put({"action": "move", "target_node": node_id})
            # Poll up to 500 ms in 20 ms increments for any event to fire.
            deadline = 500
            waited = 0
            while waited < deadline:
                await _asyncio.sleep(0.02)
                waited += 20
                if controller._event_seq > before_seq:
                    break
            st = controller._state
            if not st:
                return {"ok": False, "error": "state unavailable"}
            # Determine new events fired (slice from after before_seq)
            n_new = controller._event_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            # Check if any event is an error or gate_blocked type (move was rejected)
            rejection = next(
                (e for e in new_events if e.get("type") in ("error", "gate_blocked")),
                None,
            )
            if rejection:
                # Include player_stats so the UI can show "yours: X.X" for gate reqs
                player_stats = {}
                try:
                    best_standing = max(
                        (st.faction_standings.get(fid, 0.0) for fid in st.factions),
                        default=0.0,
                    )
                    player_stats = {
                        "trainer_rating": st.league.trainers.get("player", Trainer()).rating,
                        "faction_standing": best_standing,
                        "research_milestones": st.player_trajectory.research_investment / 20.0,
                        "ecological_balance": st.ledger.ecological_balance,
                        "anomaly_exposure": st.player_trajectory.anomaly_exposure,
                        "economic_investment": st.ledger.urbanization_level,
                        "exploration_score": st.player_trajectory.exploration_depth,
                        "league_tier": 1.0 + st.player_trajectory.competitive_focus / 33.0,
                    }
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": rejection["data"].get("message") or rejection["data"].get("gate_type", "move rejected"),
                    "player_location": st.player_location,
                    "player_stats": player_stats,
                    "events": new_events,
                }
            current_node = st.topology.nodes.get(st.player_location)
            return {
                "ok": True,
                "player_location": st.player_location,
                "node_name": current_node.name if current_node else "",
                "neighbors": list(current_node.neighbors) if current_node else [],
                "events": new_events,
            }

        @app.post("/api/explore")
        async def post_explore():
            """Survey current node: may discover fragments, anomalies, research, or echoes.
            Returns the list of new UI events generated by the explore action.
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio
            # Snapshot event sequence before the action.
            # Use _event_seq (monotonically incrementing) instead of len(_recent_events)
            # because len() stays at the max (50) once the ring buffer is full, making
            # "did a new event fire?" checks always return False.
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "explore"})
            # Poll until the controller thread processes (up to 0.5s).
            # Fixed race: 0.15s flat sleep can miss events on slow Windows schedulers
            # when the controller's 0.1s poll cycle isn't aligned with the sleep.
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                after_seq = controller._event_seq
                if after_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            # Collect only the new events produced by this explore.
            # Take the last (after_seq - before_seq) events from the ring buffer.
            after_seq = controller._event_seq
            n_new = after_seq - before_seq
            if n_new > 0:
                new_events = list(controller._recent_events[-n_new:])
            else:
                new_events = []
            st = controller._state
            result: dict = {"ok": True, "events": new_events}
            if st:
                result["tick"] = st.tick
                result["player_location"] = st.player_location
                current_node = st.topology.nodes.get(st.player_location)
                result["node_type"] = current_node.node_type.name if current_node else "UNKNOWN"
            return result

        @app.post("/api/encounter")
        async def post_encounter():
            """Roll a random encounter at the player's current node.

            Returns the encounter event (species info + instance_id) if something
            appears, or a no_encounter event if the node has no encounter table /
            the roll comes up empty.  The instance_id in a successful response
            can be passed directly to POST /api/capture.
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "encounter"})
            # Poll until the controller thread processes the command (up to 0.5s).
            # A simple sleep can race with the controller's 0.1s poll cycle; polling
            # ensures we always see the new events even on slow/Windows schedulers.
            # Use _event_seq (not len) to avoid false-negative when buffer is full.
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                _after_seq = controller._event_seq
                if _after_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            _after_seq = controller._event_seq
            n_new = _after_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            st = controller._state
            result: dict = {"ok": True, "events": new_events}
            if st:
                result["tick"] = st.tick
                result["player_location"] = st.player_location
            # Surface encounter details at top-level for convenience
            for ev in new_events:
                if ev.get("type") == "encounter":
                    result["encounter"] = ev.get("data", {})
                    break
                if ev.get("type") == "no_encounter":
                    result["no_encounter"] = True
                    result["message"] = ev.get("data", {}).get("message", "Nothing appeared.")
                    break
            return result

        @app.post("/api/capture")
        async def post_capture(data: dict):
            """Attempt to capture a wild creature.

            Body: { "instance_id": "<wild creature instance id>" }

            The instance_id is obtained from a prior POST /api/encounter response.
            Returns ok=True with a 'captured' or 'capture_failed' event.
            """
            if not controller:
                return {"error": "not initialized"}
            instance_id = data.get("instance_id", "")
            if not instance_id:
                return {"error": "instance_id required"}
            import asyncio as _asyncio
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "capture", "instance_id": instance_id})
            # Poll until processed (up to 0.5s) to avoid timer/thread race.
            # Use _event_seq (not len) so a full ring buffer doesn't mask new events.
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                _after_seq = controller._event_seq
                if _after_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            _after_seq = controller._event_seq
            n_new = _after_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            st = controller._state
            result: dict = {"ok": True, "events": new_events}
            if st:
                result["tick"] = st.tick
                result["team_size"] = len(st.player_team)
                result["creatures_captured"] = st.player_trajectory.creatures_captured
            # Surface result at top-level
            for ev in new_events:
                if ev.get("type") in ("captured", "capture_failed", "error"):
                    result["result"] = ev.get("type")
                    result["detail"] = ev.get("data", {})
                    break
            return result

        @app.post("/api/release")
        async def post_release(data: dict):
            """Release a team creature back into the wild.

            Body: { "instance_id": "<team member instance id>" }

            The creature is removed from the player's team and its loyalty is reset.
            Returns ok=True with a 'released' event and updated team_size.
            """
            if not controller:
                return {"error": "not initialized"}
            instance_id = data.get("instance_id", "")
            if not instance_id:
                return {"error": "instance_id required"}
            import asyncio as _asyncio
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "release", "instance_id": instance_id})
            # Poll until processed (up to 0.5s)
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                _after_seq = controller._event_seq
                if _after_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            _after_seq = controller._event_seq
            n_new = _after_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            st = controller._state
            result: dict = {"ok": True, "events": new_events}
            if st:
                result["tick"] = st.tick
                result["team_size"] = len(st.player_team)
            for ev in new_events:
                if ev.get("type") in ("released", "error"):
                    result["result"] = ev.get("type")
                    result["detail"] = ev.get("data", {})
                    break
            return result

        @app.post("/api/rest")
        async def post_rest():
            """Rest the team at the current node (SETTLEMENT or CITY only).

            Reduces all team creature fatigue by 40%, boosts loyalty by 3.
            Returns ok=True with a 'rested' event and per-creature recovery data.
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "rest"})
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                if controller._event_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            n_new = controller._event_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            result: dict = {"ok": True, "events": new_events}
            st = controller._state
            if st:
                result["tick"] = st.tick
                result["team_size"] = len(st.player_team)
            for ev in new_events:
                if ev.get("type") in ("rested", "error"):
                    result["result"] = ev.get("type")
                    result["detail"] = ev.get("data", {})
                    if ev.get("type") == "error":
                        result["ok"] = False
                    break
            return result

        @app.post("/api/dialogue")
        async def post_dialogue(data: dict):
            """Make a dialogue stance choice with a local NPC.

            Body: {stance: str}
            Stances: competition_high | preservation_high | research_priority |
                     industrialization_high | anomaly_curiosity_high | neutral
            Returns: ok, credibility, standing_shifts, ideology snapshot.
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio

            _DIALOGUE_STANCES = {
                "competition_high": {
                    "competition": 0.4, "preservation": -0.1,
                    "label": "League supremacy — only the strongest trainers advance.",
                },
                "preservation_high": {
                    "preservation": 0.4, "competition": -0.1,
                    "label": "Neikos belong in the wild. Capture limits protect the ecosystem.",
                },
                "research_priority": {
                    "research_priority": 0.4, "anomaly_curiosity": 0.2,
                    "label": "Documenting anomalies advances everyone's understanding.",
                },
                "industrialization_high": {
                    "industrialization": 0.4, "preservation": -0.2,
                    "label": "Development brings prosperity to all settled zones.",
                },
                "anomaly_curiosity_high": {
                    "anomaly_curiosity": 0.4, "research_priority": 0.2,
                    "label": "The anomalies are not threats. They are invitations.",
                },
                "neutral": {
                    "label": "I keep my own counsel. Tell me what you know of this region.",
                },
            }

            stance = data.get("stance", "neutral")
            if stance not in _DIALOGUE_STANCES:
                return {"ok": False, "error": f"Unknown stance: {stance}"}

            stance_data = _DIALOGUE_STANCES[stance]
            delta_raw = {k: v for k, v in stance_data.items() if k != "label" and isinstance(v, float)}

            st = controller._state
            if not st:
                return {"ok": False, "error": "No island active"}

            # Snapshot standings before
            before = {fid: st.faction_standings.get(fid, 0.0) for fid in st.factions}

            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "dialogue", "delta": delta_raw})
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                if controller._event_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break

            n_new = controller._event_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []

            # Snapshot standings after
            after = {fid: st.faction_standings.get(fid, 0.0) for fid in st.factions}
            shifts = {
                fid: round(after[fid] - before[fid], 4)
                for fid in st.factions
                if abs(after[fid] - before[fid]) > 0.001
            }

            # Compute credibility from events
            credibility = 1.0
            for ev in new_events:
                if ev.get("type") == "dialogue_processed":
                    credibility = ev.get("data", {}).get("credibility", credibility)

            return {
                "ok": True,
                "stance": stance,
                "label": stance_data["label"],
                "credibility": round(credibility, 3),
                "standing_shifts": shifts,
                "faction_standings": after,
                "events": new_events,
            }

        @app.post("/api/breed")
        async def post_breed(data: dict):
            """Breed two team creatures together.

            Body: {parent_a_id: str, parent_b_id: str}
            Returns breed_result event with offspring id, genes, lineage_depth, traits.
            Both parents must be on the player's team and be compatible (same species,
            evolution line, or shared primary type).
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio
            parent_a = data.get("parent_a_id", "")
            parent_b = data.get("parent_b_id", "")
            if not parent_a or not parent_b:
                return {"ok": False, "error": "parent_a_id and parent_b_id required"}
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "breed",
                                   "parent_a_id": parent_a,
                                   "parent_b_id": parent_b})
            deadline = _asyncio.get_event_loop().time() + 0.8
            while True:
                await _asyncio.sleep(0.02)
                if controller._event_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            n_new = controller._event_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            result: dict = {"ok": True, "events": new_events, "events_count": len(new_events)}
            st = controller._state
            if st:
                result["tick"] = st.tick
                result["breeds_completed"] = st.player_trajectory.breeds_completed
            for ev in new_events:
                if ev.get("type") == "breed_result":
                    result["result"] = "bred"
                    result["detail"] = ev.get("data", {})
                    break
                if ev.get("type") == "error":
                    result["ok"] = False
                    result["result"] = "error"
                    result["error"] = ev.get("data", {}).get("message", "breed failed")
                    break
            return result

        @app.post("/api/claim")
        async def post_claim(data: dict):
            """Claim a bred offspring onto the player's team.

            Body: {instance_id: str}  — must start with 'bred_'
            Returns: {ok, result, detail, team_size}
            """
            if not controller:
                return {"error": "not initialized"}
            import asyncio as _asyncio
            instance_id = data.get("instance_id", "")
            if not instance_id:
                return {"ok": False, "error": "instance_id required"}
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "claim", "instance_id": instance_id})
            deadline = _asyncio.get_event_loop().time() + 0.5
            while True:
                await _asyncio.sleep(0.02)
                if controller._event_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            n_new = controller._event_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            result: dict = {"ok": True, "events": new_events, "events_count": len(new_events)}
            st = controller._state
            if st:
                result["team_size"] = len(st.player_team)
            for ev in new_events:
                if ev.get("type") == "claimed":
                    evd = ev.get("data", {})
                    if evd.get("ok", True):
                        result["result"] = "claimed"
                        result["detail"] = evd
                    else:
                        result["ok"] = False
                        result["result"] = "error"
                        result["error"] = evd.get("message", "claim failed")
                    break
                if ev.get("type") == "error":
                    result["ok"] = False
                    result["result"] = "error"
                    result["error"] = ev.get("data", {}).get("message", "claim failed")
                    break
            return result

        @app.post("/api/_reload")
        async def post_reload():
            """Hot-reload the Neikos web server to pick up plugin code changes.

            Signals uvicorn to exit (gracefully), clears the nk_web_started flag,
            and starts a new web server thread with the freshly imported module.
            Returns immediately; the server will be briefly unavailable during restart.
            """
            import asyncio as _asyncio
            import importlib as _importlib
            import threading as _threading

            async def _do_reload():
                await _asyncio.sleep(0.1)  # Let the response flush
                # Signal uvicorn to exit
                sv = runtime_stub.get("nk_uvicorn_server")
                if sv:
                    sv.should_exit = True
                # Clear the started flag so register_widgets will start a fresh thread
                runtime_stub.pop("nk_web_started", None)
                runtime_stub.pop("nk_uvicorn_server", None)
                # Re-import the plugin module to get fresh code
                await _asyncio.sleep(3.0)  # Give uvicorn time to stop (Windows needs more)
                try:
                    import plugins.neikos as _nk_mod
                    _importlib.reload(_nk_mod)
                    stop_ev = runtime_stub.get("stop_event", _threading.Event())
                    wt = _threading.Thread(
                        target=_nk_mod._start_web_server,
                        args=(stop_ev, runtime_stub),
                        daemon=True,
                        name="nk_web_server_reloaded",
                    )
                    wt.start()
                    runtime_stub["nk_web_started"] = True
                except Exception as _e:
                    _dbg(f"Reload error: {_e}")

            import asyncio as _asyncio
            _asyncio.create_task(_do_reload())
            return {"status": "reloading", "message": "Server will restart in ~2s"}

        @app.get("/api/trainers")
        def get_trainers():
            """List AI trainers available to battle.

            Returns trainers from the league, excluding the player, sorted by rating.
            Prioritises trainers whose league tier is appropriate for the player's
            current progression (battles_won) so beginners see LOCAL trainers first.
            """
            if not controller or not controller._state:
                return {"error": "not initialized"}
            st = controller._state
            battles_done = st.player_trajectory.battles_won + st.player_trajectory.battles_lost
            trainers_out = []
            for tid, tr in st.league.trainers.items():
                if tr.is_player or tid == "player":
                    continue
                trainers_out.append({
                    "trainer_id": tr.trainer_id,
                    "name": tr.name,
                    "rating": round(tr.rating, 1),
                    "tier": tr.tier.name if tr.tier else "LOCAL",
                    "team_size": len(tr.team_species_ids),
                    "wins": tr.wins,
                    "losses": tr.losses,
                })
            # Sort: closest rating to player first (after a few battles), else lowest first
            player_rating = st.league.trainers.get("player", Trainer()).rating
            if battles_done >= 3:
                trainers_out.sort(key=lambda t: abs(t["rating"] - player_rating))
            else:
                trainers_out.sort(key=lambda t: t["rating"])
            player_tr = st.league.trainers.get("player")
            return {
                "trainers": trainers_out[:10],
                "total": len(trainers_out),
                "player_rating": round(player_tr.rating, 1) if player_tr else 1200,
                "player_wins": player_tr.wins if player_tr else 0,
                "player_losses": player_tr.losses if player_tr else 0,
            }

        @app.post("/api/battle")
        async def post_battle(data: dict):
            """Trigger a battle against an AI trainer.

            Body: { "trainer_id": "<trainer id from GET /api/trainers>" }

            Returns battle result: winner, turns, player_remaining, opponent_remaining,
            moves_used (turn log), player_rating (updated ELO), opponent_name.
            Requires player to have at least 1 creature in their team.
            """
            if not controller:
                return {"error": "not initialized"}
            trainer_id = data.get("trainer_id", "")
            if not trainer_id:
                return {"error": "trainer_id required"}
            st = controller._state
            if not st:
                return {"error": "not initialized"}
            # Pre-flight checks surfaced before queuing
            if not st.player_team:
                return {"error": "No team - capture at least one creature before battling"}
            if trainer_id not in st.league.trainers:
                return {"error": f"Unknown trainer: {trainer_id}"}
            # Pre-flight: exhaustion guard (mirror of _cmd_battle guard, fast path)
            team_instances = [st.creatures.get(iid) for iid, _ in st.player_team[:3]]
            active = [c for c in team_instances if c is not None]
            if active and all(c.fatigue >= 80 for c in active):
                return {"error": (
                    "Your team is exhausted (fatigue \u2265 80). Rest them at a "
                    "settlement or city before battling."
                )}
            import asyncio as _asyncio
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "battle", "opponent_id": trainer_id})
            # Battle can take slightly longer than move/capture - poll up to 600ms.
            # Use _event_seq (not len) so a full ring buffer doesn't mask new events.
            deadline = _asyncio.get_event_loop().time() + 0.6
            while True:
                await _asyncio.sleep(0.02)
                _after_seq = controller._event_seq
                if _after_seq > before_seq:
                    break
                if _asyncio.get_event_loop().time() >= deadline:
                    break
            _after_seq = controller._event_seq
            n_new = _after_seq - before_seq
            new_events = list(controller._recent_events[-n_new:]) if n_new > 0 else []
            # Extract battle_result event fields to top-level for the UI
            result: dict = {"ok": True, "events": new_events}
            for ev in new_events:
                if ev.get("type") == "battle_result":
                    d = ev.get("data", {})
                    result["winner"] = d.get("winner", "unknown")
                    result["player_remaining"] = d.get("player_remaining", 0)
                    result["opponent_remaining"] = d.get("opponent_remaining", 0)
                    result["turns"] = d.get("turns", 0)
                    result["moves_used"] = d.get("moves_used", [])
                    result["player_rating"] = d.get("player_rating")
                    result["opponent_name"] = d.get("opponent_name", trainer_id)
                    break
                if ev.get("type") == "error":
                    result["error"] = ev.get("data", {}).get("message", "Battle error")
                    break
            if st:
                result["tick"] = st.tick
            return result

        @app.get("/api/species")
        def get_species():
            if controller and controller._state:
                return [sp.to_dict()
                        for sp in controller._state.species_map.values()]
            return {"error": "not initialized"}

        @app.get("/api/factions")
        def get_factions():
            """Return all island factions with player standing and territory counts."""
            if not controller or not controller._state:
                return {"error": "not initialized"}
            st = controller._state
            result = []
            for fid, faction in st.factions.items():
                standing = st.faction_standings.get(fid, 0.0)
                result.append({
                    "faction_id": fid,
                    "name": faction.name,
                    "archetype": faction.archetype.name,
                    "standing": round(standing, 4),
                    "influence_score": round(faction.influence_score, 2),
                    "territory_count": len(faction.territorial_nodes),
                    "public_sentiment": round(faction.public_sentiment, 3),
                    "ideology": faction.ideology.to_dict(),
                })
            # Sort: highest standing first
            result.sort(key=lambda f: f["standing"], reverse=True)
            return {"factions": result, "tick": st.tick}

        @app.get("/api/outcome")
        def get_outcome():
            if controller and controller._state:
                band = compute_outcome_band(
                    controller._state.ledger,
                    controller._state.player_trajectory)
                return describe_outcome_band(band)
            return {"error": "not initialized"}

        @app.get("/api/narrative")
        def get_narrative():
            if controller and controller._state:
                np = controller._state.narrative_profile
                return np.to_dict() if np else {"error": "no narrative profile"}
            return {"error": "not initialized"}

        @app.get("/api/tier")
        def get_tier():
            if controller and controller._state:
                st = controller._state
                return {
                    "base_tier":    st.base_tier.name,
                    "current_tier": st.current_tier.name,
                    "tier_value":   st.current_tier.value,
                    "description":  TIER_CHARACTERISTICS[st.current_tier].description,
                    "tick":         st.tick,
                }
            return {"error": "not initialized"}

        @app.get("/api/knower")
        def get_knower():
            if controller and controller._state:
                st = controller._state
                knower = st.hidden_knower
                if not knower:
                    return {"error": "no knower on this island"}
                unlocked = knower.is_unlocked(st.player_trajectory)
                data = knower.to_dict()
                data["is_unlocked"] = unlocked
                if not unlocked:
                    data["location_node_id"] = "???"
                    # Surface a per-archetype hint so the UI can tease the player
                    _KNOWER_HINT_LINES: dict = {
                        "RETIRED_ARCHIVIST":   "Someone who remembers more than they should lingers near the old facilities.",
                        "REGIONAL_GYM_LEADER": "A seasoned battler watches the circuit rankings. Impress them.",
                        "ISOLATED_RESEARCHER": "A researcher has gone dark. Find them where the anomalies cluster.",
                        "ELDERLY_HERMIT":      "An elder hermit retreated deep into the wild zones long ago.",
                        "ANONYMOUS_SIGNAL":    "Strange transmissions echo through the relay network.",
                    }
                    data["hint"] = _KNOWER_HINT_LINES.get(
                        knower.archetype.name,
                        "A presence waits somewhere on the island."
                    )
                return data
            return {"error": "not initialized"}

        @app.post("/api/knower/talk")
        async def post_knower_talk(data: dict = None):
            """
            Talk to the Hidden Knower.  The knower must be unlocked first.
            Body: { "fragment_index": <int, default 0> }
            Returns the knower_dialogue event (or knower_locked / error).
            """
            if not (controller and controller._state):
                return {"error": "not initialized"}
            st = controller._state
            knower = st.hidden_knower
            if not knower:
                return {"error": "no knower on this island"}

            fragment_index = int((data or {}).get("fragment_index", 0))

            import asyncio as _asyncio
            # Use event-seq counter (immune to ring-buffer full masking)
            before_seq = controller._event_seq
            controller._cmd_q.put({"action": "talk_knower", "fragment_index": fragment_index})
            deadline = 0.5
            elapsed = 0.0
            while elapsed < deadline:
                await _asyncio.sleep(0.02)
                elapsed += 0.02
                if controller._event_seq > before_seq:
                    break
            after_seq = controller._event_seq
            n_new = max(after_seq - before_seq, 0)
            if n_new > 0:
                events = list(controller._recent_events[-n_new:])
            else:
                events = []

            # Surface the dialogue / locked event to top-level; prefer non-error types
            result: dict = {"events": events}
            primary_types = ("knower_dialogue", "knower_locked")
            for ev in events:
                if ev.get("type") in primary_types:
                    result.update(ev.get("data", {}))
                    result["event_type"] = ev["type"]
                    break
            else:
                # Fall back to any informative event
                for ev in events:
                    if ev.get("type") == "error":
                        result.update(ev.get("data", {}))
                        result["event_type"] = "error"
                        break
            return result

        @app.get("/api/fragments")
        def get_fragments():
            if controller and controller._state:
                st = controller._state
                framing = st.narrative_profile.founder_framing if st.narrative_profile else {}
                traitor = FOUNDER_CANON.get(
                    framing.get("traitor", "voss"), FounderRecord("Dr. Voss","","","")
                ).name
                species_sample = next(iter(st.species_map.values()), None)
                ctx = {
                    "founder":       traitor,
                    "island_name":   st.topology.island_name,
                    "tier":          st.current_tier.name,
                    "species_name":  species_sample.name if species_sample else "the creature",
                    "anomaly_count": len(st.topology.anomaly_zone_ids),
                    "variance":      15.0 + (st.tick % 30),
                    "collapse_ticks": max(10, 200 - st.tick),
                }
                # Sort: discovered ones ordered by discovery sequence (newest last),
                # undiscovered ones after in POOL order.
                disc_order = {fid: i for i, fid in enumerate(st.discovered_fragments)}
                sorted_frags = sorted(
                    st.island_fragments,
                    key=lambda f: (0 if f.fragment_id in disc_order else 1,
                                   disc_order.get(f.fragment_id, 999))
                )
                return {
                    "total":      len(st.island_fragments),
                    "discovered": len(st.discovered_fragments),
                    "fragments": [
                        {
                            "fragment_id":   f.fragment_id,
                            "type":          f.ftype.name,
                            "title":         f.render_title(ctx),
                            "body":          f.render(ctx) if f.fragment_id in disc_order else None,
                            "mountain_code": f.mountain_code,
                            "discovered":    f.fragment_id in disc_order,
                        }
                        for f in sorted_frags
                    ],
                }
            return {"error": "not initialized"}

        @app.get("/api/local")
        def get_local():
            """
            Current node intel: node info, encounter table (full species data),
            encounter rates by rarity tier, gate status, and trainable actions.
            Useful for planning before exploring or encountering.
            """
            if not (controller and controller._state):
                return {"error": "not initialized"}
            st = controller._state
            node = st.topology.nodes.get(st.player_location)
            if not node:
                return {"error": "player location not found"}

            # --- Encounter rate table (probabilities from roll_encounter logic) ---
            _RARITY_RATES = {
                "anomaly":  0.02,
                "apex":     0.03,   # 0.05 - 0.02
                "elite":    0.10,   # 0.15 - 0.05
                "rare":     0.15,   # 0.30 - 0.15
                "uncommon": 0.25,   # 0.55 - 0.30
                "common":   0.45,   # remainder
            }

            def _species_brief(sid: str) -> dict:
                sp = st.species_map.get(sid)
                if not sp:
                    return {"species_id": sid}
                return {
                    "species_id":     sp.species_id,
                    "name":           sp.name,
                    "primary_type":   sp.primary_type.name,
                    "secondary_type": sp.secondary_type.name if sp.secondary_type else None,
                    "rarity":         sp.rarity.name,
                    "bst":            sp.base_stats.bst,
                    "archetype":      sp.stat_archetype.name,
                    "passive":        sp.passive_trait,
                    "evo_stage":      sp.evolution_stage,
                    "evolves_to":     sp.evolves_to,
                    "evo_level":      (
                                          (16 if sp.evolution_stage == 1
                                           else 36 if sp.evolution_stage == 2
                                           else None)
                                          if sp.evolves_to else None
                                      ),
                }

            et = st.encounter_tables.get(st.player_location)
            encounter_table = None
            can_encounter = node.node_type not in (
                NodeType.SETTLEMENT, NodeType.CITY, NodeType.GATE
            )
            if et and can_encounter:
                slots = []
                if et.common_slots:
                    slots.append({
                        "tier": "common",
                        "encounter_rate": _RARITY_RATES["common"],
                        "species": [_species_brief(s) for s in et.common_slots],
                    })
                if et.uncommon_slots:
                    slots.append({
                        "tier": "uncommon",
                        "encounter_rate": _RARITY_RATES["uncommon"],
                        "species": [_species_brief(s) for s in et.uncommon_slots],
                    })
                if et.rare_slots:
                    slots.append({
                        "tier": "rare",
                        "encounter_rate": _RARITY_RATES["rare"],
                        "species": [_species_brief(s) for s in et.rare_slots],
                    })
                if et.elite_slots:
                    slots.append({
                        "tier": "elite",
                        "encounter_rate": _RARITY_RATES["elite"],
                        "species": [_species_brief(s) for s in et.elite_slots],
                    })
                if et.apex_slot:
                    slots.append({
                        "tier": "apex",
                        "encounter_rate": _RARITY_RATES["apex"],
                        "species": [_species_brief(et.apex_slot)],
                    })
                if et.anomaly_slot:
                    slots.append({
                        "tier": "anomaly",
                        "encounter_rate": _RARITY_RATES["anomaly"],
                        "species": [_species_brief(et.anomaly_slot)],
                    })
                total_species = len(et.all_species())
                encounter_table = {
                    "node_id": st.player_location,
                    "total_species": total_species,
                    "slots": slots,
                }

            # --- Gate status for neighboring nodes ---
            neighbor_gates = []
            for nb_id in node.neighbors:
                nb = st.topology.nodes.get(nb_id)
                if nb and nb.gate:
                    g = nb.gate
                    metric_val = {
                        "trainer_rating":       st.league.trainers.get("player", Trainer()).rating,
                        "anomaly_exposure":     st.player_trajectory.anomaly_exposure,
                        "exploration_score":    st.player_trajectory.exploration_depth,
                        "research_milestones":  st.player_trajectory.research_investment / 20.0,
                        "faction_standing":     max(
                            (st.faction_standings.get(fid, 0.0) for fid in st.factions),
                            default=0.0,
                        ),
                        "ecological_balance":   st.ledger.ecological_balance,
                        "economic_investment":  st.ledger.urbanization_level,
                        "league_tier":          1.0 + st.player_trajectory.competitive_focus / 33.0,
                    }.get(g.primary_metric, 0.0)
                    effective_threshold = round(g.threshold - g.flex_buffer, 1)
                    neighbor_gates.append({
                        "node_id":   nb_id,
                        "node_name": nb.name,
                        "gate_type": g.gate_type.name,
                        "metric":    g.primary_metric,
                        "threshold": round(g.threshold, 1),
                        "effective_threshold": effective_threshold,
                        "player_value": round(metric_val, 1),
                        "passable": metric_val >= effective_threshold,
                    })

            # --- Node summary string ---
            relay_tag  = " [RELAY NODE]" if node.is_relay_node else ""
            anomaly_pct = int(node.biome.instability_bias * 100)
            summary = (
                f"{node.name} — {node.node_type.name} ({node.region.name})"
                f"{relay_tag} | instability {anomaly_pct}%"
                f" | {total_species if et and can_encounter else 0} encounter species"
            )

            return {
                "node_id":      node.node_id,
                "node_name":    node.name,
                "node_type":    node.node_type.name,
                "region":       node.region.name,
                "is_relay_node": node.is_relay_node,
                "is_start":     node.is_start,
                "biome": {
                    "temperature":        round(node.biome.temperature, 3),
                    "moisture":           round(node.biome.moisture, 3),
                    "elevation":          round(node.biome.elevation, 3),
                    "vegetation_density": round(node.biome.vegetation_density, 3),
                    "instability_bias":   round(node.biome.instability_bias, 3),
                },
                "neighbors":       list(node.neighbors),
                "can_encounter":   can_encounter,
                "encounter_table": encounter_table,
                "neighbor_gates":  neighbor_gates,
                "summary":         summary,
                "tick":            st.tick,
            }

        @app.get("/api/profile")
        def get_profile():
            profile = load_behavioral_profile()
            if profile:
                return profile.to_dict()
            return {"error": "no profile saved", "hint": "complete an expedition first"}

        @app.post("/api/speak")
        async def post_speak(data: dict):
            from .voice import speak_npc
            text = data.get("text", "")
            archetype = data.get("archetype", "DEFAULT")
            npc_name = data.get("npc_name", "")
            if text:
                speak_npc(text, archetype, npc_name)
            return {"status": "queued"}

        @app.get("/api/pucks")
        def get_pucks():
            from .spatial.esp32 import get_puck_manager
            pm = get_puck_manager()
            if not pm:
                return {"connected": 0}
            return {"connected": pm.connected_count()}

        @app.get("/api/island_preview/{seed}")
        def island_preview(seed: int):
            """Fast preview of island N without full init."""
            try:
                topo = generate_island_topology(seed)
                tier = _seed_to_base_tier(seed)
                return {
                    "seed": seed,
                    "name": topo.island_name,
                    "climate": topo.climate.name if topo.climate else "TEMPERATE",
                    "node_count": topo.node_count,
                    "base_tier": tier.name,
                    "active_types": [t.name for t in (topo.active_types or [])[:4]],
                }
            except Exception as e:
                return {"seed": seed, "name": f"Island {seed}", "climate": "UNKNOWN",
                        "node_count": 0, "base_tier": "TIER_I", "active_types": [], "error": str(e)}

        # ── 9. NGP+ Expedition Lifecycle ─────────────────────────────────────
        @app.post("/api/expedition/complete")
        async def post_expedition_complete(data: dict = None):
            """
            End the current expedition, save the behavioral profile to disk,
            and start a new island.

            Body (all optional):
              { "seed": <int, 0=auto-derive> }

            Returns expedition_ended event data (final_tier, behavioral_axis, etc.)
            plus new island info once re-initialized.
            """
            if not controller:
                return {"error": "not initialized"}
            if not controller._state:
                return {"error": "No island to complete"}

            import asyncio as _asyncio
            # Clear the stash so we don't accidentally return a stale result.
            controller._last_expedition_result = None
            before_seq = controller._event_seq
            next_seed = int((data or {}).get("seed", 0))
            controller._cmd_q.put({"action": "new_expedition", "seed": next_seed})

            # Poll up to 8 seconds (island re-init can take a moment).
            # _cmd_new_expedition sets _last_expedition_result before calling
            # init_island, so we only need the island_initialized event to know
            # everything is ready; we don't rely on finding expedition_ended in
            # the ring buffer (which can be flushed by the re-init event flood).
            deadline = 80  # 80 × 100ms = 8s
            init_ev = None
            for _ in range(deadline):
                await _asyncio.sleep(0.1)
                if controller._last_expedition_result is not None:
                    # Expedition ended; now wait for the new island to be ready
                    n_now = max(0, controller._event_seq - before_seq)
                    recent = list(controller._recent_events[-n_now:]) if n_now else []
                    init_ev = next((e for e in reversed(recent)
                                    if e.get("type") == "island_initialized"), None)
                    if init_ev:
                        break

            ended_data = controller._last_expedition_result
            if not ended_data:
                n_new = max(0, controller._event_seq - before_seq)
                new_events = list(controller._recent_events[-n_new:]) if n_new else []
                return {"error": "Expedition complete timed out or failed", "events": new_events}

            n_new = max(0, controller._event_seq - before_seq)
            new_events = list(controller._recent_events[-n_new:]) if n_new else []
            if init_ev is None:
                init_ev = next((e for e in reversed(new_events)
                                if e.get("type") == "island_initialized"), None)

            result = {
                "completed_seed":   ended_data.get("completed_seed"),
                "ticks_played":     ended_data.get("ticks_played"),
                "final_tier":       ended_data.get("final_tier"),
                "behavioral_axis":  ended_data.get("behavioral_axis"),
                "profile_path":     ended_data.get("profile_path"),
                "next_seed":        ended_data.get("next_seed"),
                "events":           new_events,
            }
            if init_ev:
                result["new_island"] = {
                    "island_name": init_ev["data"].get("island_name"),
                    "seed":        init_ev["data"].get("seed") or ended_data.get("next_seed"),
                    "tier":        init_ev["data"].get("ngp_run"),
                    "ngp_run":     init_ev["data"].get("ngp_run"),
                }
            return result

        @app.post("/api/expedition/reset")
        async def post_expedition_reset(data: dict = None):
            """
            Delete the NGP+ behavioral profile and reset to a clean Tier I island.

            Body (optional): { "seed": <int, default 42> }

            Returns simulation_reset + island_initialized events.
            """
            if not controller:
                return {"error": "not initialized"}

            import asyncio as _asyncio
            before_seq = controller._event_seq
            seed = int((data or {}).get("seed", 42))
            controller._cmd_q.put({"action": "reset_simulation", "seed": seed})

            deadline = 80
            for _ in range(deadline):
                await _asyncio.sleep(0.1)
                after_seq = controller._event_seq
                if after_seq > before_seq:
                    break

            n_new = max(0, controller._event_seq - before_seq)
            new_events = list(controller._recent_events[-n_new:]) if n_new else []

            reset_ev = next((e for e in reversed(new_events) if e.get("type") == "simulation_reset"), None)
            init_ev  = next((e for e in reversed(new_events) if e.get("type") == "island_initialized"), None)

            return {
                "profile_deleted": reset_ev["data"].get("profile_deleted") if reset_ev else None,
                "new_seed":        seed,
                "new_island": {
                    "island_name": init_ev["data"].get("island_name") if init_ev else None,
                    "seed":        init_ev["data"].get("seed", seed) if init_ev else seed,
                } if init_ev else None,
                "events": new_events,
            }

        @app.get("/api/expedition/status")
        def get_expedition_status():
            """Return current expedition metadata + NGP+ profile summary if present."""
            profile = load_behavioral_profile()
            result: dict = {}
            if controller and controller._state:
                st = controller._state
                result.update({
                    "seed":        st.seed,
                    "tick":        st.tick,
                    "island_name": st.topology.island_name,
                    "current_tier": st.current_tier.name,
                    "ngp_run":     st.ngp_profile.run_count if st.ngp_profile else 0,
                })
            if profile:
                result["saved_profile"] = profile.to_dict()
            else:
                result["saved_profile"] = None
            return result

        @app.get("/api/islands")
        def get_islands():
            """Preview all 100 islands.

            Served from the in-memory cache built by _build_island_cache().
            If the cache is still warming up, returns whatever seeds are ready
            so the UI can show partial results immediately.  Includes a
            'cache_ready' flag so callers know whether to poll for more.
            """
            with _NK_ISLAND_CACHE_LOCK:
                cached = dict(_NK_ISLAND_CACHE)

            if cached:
                # Return sorted list of whatever is already built.
                results = [cached[s] for s in sorted(cached)]
                return {
                    "islands": results,
                    "cache_ready": _NK_ISLAND_CACHE_BUILT.is_set(),
                    "cached_count": len(results),
                }

            # Cache not started yet (shouldn't happen after register_widgets,
            # but handle gracefully by generating synchronously for the
            # requested seeds only - first 10 - so the UI isn't blocked.
            results = []
            for s in range(1, 11):
                try:
                    topo = generate_island_topology(s)
                    tier = _seed_to_base_tier(s)
                    results.append({
                        "seed": s,
                        "name": topo.island_name,
                        "climate": topo.climate.name if topo.climate else "TEMPERATE",
                        "node_count": topo.node_count,
                        "base_tier": tier.name,
                        "active_types": [t.name for t in (topo.active_types or [])[:4]],
                    })
                except Exception:  # noqa: BLE001
                    results.append({"seed": s, "name": f"Island {s}", "climate": "UNKNOWN",
                                    "node_count": 0, "base_tier": "TIER_I", "active_types": []})
            return {"islands": results, "cache_ready": False, "cached_count": len(results)}

        try:
            from fastapi import WebSocket, WebSocketDisconnect
            from .spatial.esp32 import get_puck_manager as _gpm

            @app.websocket("/ws/puck")
            async def puck_ws(websocket: WebSocket):
                _dbg("WS /ws/puck: connection attempt")
                await websocket.accept()
                _dbg("WS /ws/puck: accepted")
                puck_id = None
                pm = _gpm()
                # Give the puck manager a reference to the running event loop
                # so it can schedule sends from the controller worker thread.
                if pm:
                    import asyncio as _aio
                    pm.set_loop(_aio.get_running_loop())
                try:
                    while True:
                        data = await websocket.receive_json()
                        if data.get("type") == "register":
                            puck_id = data["puck_id"]
                            node_id = data["node_id"]
                            if pm:
                                pm.register(puck_id, node_id, websocket)
                        elif data.get("type") == "interact":
                            if pm and puck_id:
                                pm.on_interact(puck_id)
                        elif data.get("type") == "ping":
                            await websocket.send_json({"type": "pong"})
                except WebSocketDisconnect:
                    if pm and puck_id:
                        pm.unregister(puck_id)

            @app.get("/api/puck/status")
            async def get_puck_status():
                pm = _gpm()
                if pm is None:
                    return {"ok": False, "error": "Puck manager not initialized",
                            "connected": 0, "pucks": []}
                return {"ok": True, **pm.status()}

        except Exception as _ws_err:
            import traceback as _tb
            print(f"[NK] WebSocket route setup FAILED: {_ws_err}", flush=True)
            print(_tb.format_exc(), flush=True)
            _dbg(f"WebSocket route setup failed: {_ws_err}\n{_tb.format_exc()}")

        # ── Save / Load endpoints ──────────────────────────

        @app.post("/api/save")
        async def post_save():
            """Manually save current game state to disk. Returns save metadata."""
            controller: NKController = runtime_stub.get("nk_controller")
            if not controller or not controller._state:
                return {"ok": False, "error": "No active game state"}
            try:
                path = save_game_state(controller._state)
                controller._last_save_tick = controller._state.tick
                st = controller._state
                return {
                    "ok": True,
                    "seed": st.seed,
                    "tick": st.tick,
                    "player_location": st.player_location,
                    "team_size": len(st.player_team),
                    "save_path": path,
                }
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @app.post("/api/load")
        async def post_load():
            """Reload save file onto the current controller. Resets to saved seed/tick."""
            controller: NKController = runtime_stub.get("nk_controller")
            if not controller:
                return {"ok": False, "error": "No controller"}
            data = load_game_state()
            if not data:
                return {"ok": False, "error": "No save file found"}
            seed = data.get("seed", 1)
            # Re-init the island from the saved seed, then overlay save data
            controller.init_island(seed=seed)
            ok = apply_saved_state(controller._state, data)
            if not ok:
                return {"ok": False, "error": "Seed mismatch or corrupted save"}
            st = controller._state
            return {
                "ok": True,
                "seed": st.seed,
                "tick": st.tick,
                "player_location": st.player_location,
                "team_size": len(st.player_team),
                "fragments_found": len(st.discovered_fragments),
            }

        @app.get("/api/save/status")
        async def get_save_status():
            """Return last-save metadata and whether a save file exists on disk."""
            import os as _sos
            save_path = _nk_save_path()
            exists = _sos.path.isfile(save_path)
            controller: NKController = runtime_stub.get("nk_controller")
            last_save_tick = getattr(controller, "_last_save_tick", -1) if controller else -1
            current_tick = controller._state.tick if (controller and controller._state) else 0
            result: Dict[str, Any] = {
                "save_exists": exists,
                "last_save_tick": last_save_tick,
                "current_tick": current_tick,
                "ticks_since_save": current_tick - last_save_tick if last_save_tick >= 0 else None,
            }
            if exists:
                try:
                    data = load_game_state()
                    if data:
                        result["saved_seed"] = data.get("seed")
                        result["saved_tick"] = data.get("tick")
                        result["saved_team_size"] = len(data.get("player_team", []))
                        result["saved_location"] = data.get("player_location")
                        result["saved_fragments"] = len(data.get("discovered_fragments", []))
                except Exception:
                    pass
            return result

        # Use wsproto for WebSocket handling: uvicorn 0.38's websockets impl is
        # broken with websockets v13+ (deprecated API), while wsproto works cleanly.
        config = uvicorn.Config(app, host="0.0.0.0", port=7700, log_level="info", ws="wsproto")
        server = uvicorn.Server(config)
        # Store server ref so /api/_reload can trigger graceful shutdown+restart
        runtime_stub["nk_uvicorn_server"] = server

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()

    except ImportError:
        _dbg("FastAPI/uvicorn not available - web server disabled")
    except Exception as e:
        _dbg(f"Web server error: {e}")


# ============================================================
# §15  WIDGET REGISTRATION (Radio OS Plugin Contract)
# ============================================================

def register_widgets(registry, runtime_stub):
    """Register Neikos: Hundred Islands with the Radio OS runtime."""
    import sys as _sys
    # Guard against double-registration (bookmark.py may call this twice)
    if runtime_stub.get("_nk_widgets_registered"):
        return
    runtime_stub["_nk_widgets_registered"] = True

    print("[NK REGW] register_widgets called", flush=True)
    _sys.stdout.flush()

    # Only start the NK controller and web server when running inside a
    # Neikos station.  Other stations still get the widget registered
    # but nothing heavy starts.
    import os as _os
    _station_id = _os.path.basename((runtime_stub.get("STATION_DIR") or "").rstrip("/\\"))
    _NK_STATIONS = {"NeikosExpedition", "NeikosIsland"}
    _is_nk_station = _station_id in _NK_STATIONS

    # ---- command & UI queues ----
    if "nk_cmd_q" not in runtime_stub:
        runtime_stub["nk_cmd_q"] = queue.Queue()
        _dbg("Created nk_cmd_q")

    if "nk_ui_q" not in runtime_stub:
        runtime_stub["nk_ui_q"] = queue.Queue()
        _dbg("Created nk_ui_q")

    # ---- Island preview cache (background, non-blocking) ----
    if "nk_island_cache_started" not in runtime_stub:
        runtime_stub["nk_island_cache_started"] = True
        _cache_thread = threading.Thread(
            target=_build_island_cache,
            daemon=True,
            name="nk_island_cache",
        )
        _cache_thread.start()
        _dbg("Island cache background build started")

    # ---- controller - NK stations only ----
    if _is_nk_station and "nk_controller" not in runtime_stub:
        controller = NKController(runtime_stub, {})
        runtime_stub["nk_controller"] = controller
        # Auto-initialize island with a default seed
        _save_data = load_game_state()
        _start_seed = _save_data.get("seed", 1) if _save_data else 1
        controller.init_island(seed=_start_seed)
        # Restore saved player state (team, trajectory, tier, etc.)
        if _save_data:
            _restored = apply_saved_state(controller._state, _save_data)
            if _restored:
                _dbg(f"[NK Save] Restored game state from disk (seed={_start_seed}, tick={controller._state.tick})")
            else:
                _dbg("[NK Save] Save file found but not applied (seed mismatch?)")
        controller.start()
        _dbg("Controller started (seed=1)")

        # Init ESP32 puck manager immediately after controller start
        try:
            from .spatial.esp32 import init_puck_manager as _init_pm
            _init_pm(controller)
            _dbg("Puck manager initialized")
        except Exception as _pm_err:
            _dbg(f"Puck manager init failed: {_pm_err}")

    # ---- NPC Voice pipeline - init once per NK station ----
    if _is_nk_station and "nk_voice_inited" not in runtime_stub:
        try:
            from .voice import init_voice as _init_voice
            _init_voice(runtime_stub)
            runtime_stub["nk_voice_inited"] = True
            _dbg("NPC voice queue initialized")
        except Exception as _ve:
            _dbg(f"NPC voice init failed: {_ve}")

    # ---- NK FastAPI (port 7700) - NK stations only ----
    if _is_nk_station and "nk_web_started" not in runtime_stub:
        stop_event = runtime_stub.get("stop_event", threading.Event())
        web_thread = threading.Thread(
            target=_start_web_server,
            args=(stop_event, runtime_stub),
            daemon=True,
            name="nk_web_server",
        )
        web_thread.start()
        runtime_stub["nk_web_started"] = True

    # ---- Svelte SPA (port 7555) - NOT needed for NK, NK has its own UI on 7700 ----
    # Do not start ftb_web_server here; it would log a "FTB Web UI" line that
    # confuses detectPluginUIs and loads the wrong app in the proxy.

    NK_API_PORT = int(__import__("os").environ.get("NK_API_PORT", "7700"))
    SVELTE_PORT = int(__import__("os").environ.get("FTB_WEB_PORT", "7555"))

    # Emit the web UI log line BEFORE registry.register() so detectPluginUIs
    # can find it. Only emit for NK stations.
    if _is_nk_station:
        print(f"[NK Web] Neikos: Hundred Islands Web UI starting on port {NK_API_PORT}", flush=True)

    def nk_widget_factory(parent_frame):
        """
        Neikos desktop widget - shows live island info and web UI link.
        The full game UI lives in the browser at the Svelte SPA.
        """
        try:
            import customtkinter as ctk

            frame = ctk.CTkFrame(parent_frame)
            frame.grid_columnconfigure(0, weight=1)

            # Title
            ctk.CTkLabel(
                frame,
                text="🏝️  Neikos: Hundred Islands",
                font=("Helvetica", 16, "bold"),
            ).pack(pady=(18, 4))

            # Subtitle
            ctk.CTkLabel(
                frame,
                text="Creature expedition · containment conspiracy",
                font=("Helvetica", 11),
                text_color="gray",
            ).pack(pady=(0, 14))

            # Web UI link (primary - Svelte SPA tab)
            ctk.CTkLabel(
                frame,
                text=f"🌐  Game UI  →  http://localhost:{SVELTE_PORT}/#neikos",
                font=("Helvetica", 13),
            ).pack(pady=3)

            # API backend link
            ctk.CTkLabel(
                frame,
                text=f"⚙️  API  →  http://localhost:{NK_API_PORT}/api/state",
                font=("Helvetica", 11),
                text_color="gray",
            ).pack(pady=2)

            # Live status line - refreshes from controller
            status_var = ctk.StringVar(value="Initializing…")
            status_label = ctk.CTkLabel(
                frame,
                textvariable=status_var,
                font=("Helvetica", 11),
                text_color="#7ec8a0",
            )
            status_label.pack(pady=(12, 4))

            def _refresh_status():
                try:
                    ctrl = runtime_stub.get("nk_controller")
                    if ctrl and ctrl._state:
                        st = ctrl._state
                        tier = getattr(st, "current_tier", None)
                        tier_str = tier.name if tier else "?"
                        loc = getattr(st, "player_location", "?")
                        seed = getattr(st, "seed", "?")
                        tick = getattr(st, "tick", 0)
                        status_var.set(
                            f"Island #{seed} · {tier_str} · Node {loc} · tick {tick}"
                        )
                    else:
                        status_var.set("No island loaded")
                except Exception:
                    pass
                frame.after(3000, _refresh_status)

            frame.after(1000, _refresh_status)
            return frame

        except ImportError:
            return None

    # This line is scanned by detectPluginUIs in web_server.py.
    registry.register("neikos", nk_widget_factory)
    _dbg("Widget registered: neikos")


# ============================================================
# §16  STANDALONE VERIFICATION
# ============================================================

if __name__ == "__main__":
    """Quick smoke test - generate one island and print summary."""
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    print(f"=== Neikos: Hundred Islands - Seed {seed} ===\n")

    # Generate topology
    topo = generate_island_topology(seed)
    print(f"Island: {topo.island_name}")
    print(f"Climate: {topo.climate.name}")
    print(f"Nodes: {topo.node_count}")
    print(f"Active Types: {[t.name for t in topo.active_types]}")

    # Node type distribution
    type_counts: Dict[str, int] = {}
    for nd in topo.nodes.values():
        tn = nd.node_type.name
        type_counts[tn] = type_counts.get(tn, 0) + 1
    print(f"Node distribution: {type_counts}")

    # Region distribution
    region_counts: Dict[str, int] = {}
    for nd in topo.nodes.values():
        rn = nd.region.name
        region_counts[rn] = region_counts.get(rn, 0) + 1
    print(f"Region distribution: {region_counts}")

    # Generate species
    species_map = generate_species_roster(topo)
    print(f"\nSpecies: {len(species_map)}")

    rarity_dist: Dict[str, int] = {}
    for sp in species_map.values():
        rn = sp.rarity.name
        rarity_dist[rn] = rarity_dist.get(rn, 0) + 1
    print(f"Rarity distribution: {rarity_dist}")

    type_dist: Dict[str, int] = {}
    for sp in species_map.values():
        tn = sp.primary_type.name
        type_dist[tn] = type_dist.get(tn, 0) + 1
    print(f"Type distribution: {type_dist}")

    evo_lines = set(sp.evolution_line_id for sp in species_map.values())
    print(f"Evolution lines: {len(evo_lines)}")

    # Generate encounters
    ledger = IslandLedger()
    ledger.set_baseline(seed)
    enc_tables = generate_encounter_tables(topo, species_map, ledger)
    total_slots = sum(len(et.all_species()) for et in enc_tables.values())
    print(f"\nEncounter tables: {len(enc_tables)} nodes, {total_slots} total slots")

    # Generate factions
    factions = generate_factions(topo)
    print(f"\nFactions: {len(factions)}")
    for fid, f in factions.items():
        print(f"  {f.name} ({f.archetype.name}): influence={f.influence_score:.1f}")

    # Generate trainers
    trainers = generate_ai_trainers(topo, species_map)
    print(f"\nAI Trainers: {len(trainers)}")
    top_5 = sorted(trainers.values(), key=lambda t: -t.rating)[:5]
    for t in top_5:
        print(f"  {t.name}: rating={t.rating:.0f} tier={t.tier.name}")

    # Outcome band for a hypothetical trajectory
    traj = PlayerTrajectory(
        competitive_focus=60, exploration_depth=30,
        research_investment=20, breeding_intensity=10,
        anomaly_exposure=5, risk_appetite=65,
    )
    band = compute_outcome_band(ledger, traj)
    desc = describe_outcome_band(band)
    print(f"\nOutcome Band: {band}")
    print(f"  Island: {desc['island_condition']}")
    print(f"  Personal: {desc['personal_archetype']}")
    print(f"  {desc['summary']}")

    # Validation checks
    print("\n=== Validation ===")
    # Check type coverage
    for t in topo.active_types:
        count = sum(1 for sp in species_map.values() if sp.primary_type == t)
        status = "✓" if count >= 10 else "✗"
        print(f"  {status} {t.name}: {count} species (need ≥10)")

    # Check gate count
    gate_count = sum(1 for nd in topo.nodes.values() if nd.gate)
    print(f"  {'✓' if gate_count >= 8 else '✗'} Gates: {gate_count} (need ≥8)")

    # Check loops (at least 1 per region with spine)
    print(f"  ✓ Node count in range: {120 <= topo.node_count <= 250}")

    # §19 - Tier system
    base_tier = _seed_to_base_tier(seed)
    print(f"\n=== §19 Containment Tier ===")
    print(f"  Base tier (seed {seed}): {base_tier.name}")
    sample_traj = PlayerTrajectory(anomaly_exposure=60, competitive_focus=70)
    computed_tier = compute_containment_tier(ledger, sample_traj)
    print(f"  Computed tier (high anomaly/competitive): {computed_tier.name}")
    print(f"  Tier desc: {TIER_CHARACTERISTICS[base_tier].description}")
    print(f"  ✓ Tier I-V defined: "
          f"{all(t in TIER_CHARACTERISTICS for t in ContainmentTier)}")

    # Founder framing variation
    print(f"\n=== §17 Founder Framing (5 seeds) ===")
    seen_framings = set()
    for s in [1, 7, 42, 99, 256]:
        framing = resolve_founder_framing(s)
        traitor_key = framing.get("traitor", "?")
        traitor_name = FOUNDER_CANON[traitor_key].name if traitor_key in FOUNDER_CANON else traitor_key
        key = tuple(sorted(framing.items()))
        seen_framings.add(key)
        print(f"  seed {s:>4}: traitor={traitor_name} | {framing}")
    print(f"  ✓ Framing variants seen: {len(seen_framings)} (expect >1)")

    # §18 - Behavioral axis
    print(f"\n=== §18 Behavioral Axis ===")
    axes_seen = set()
    for cf, ex, ri, bi, an in [(80,10,5,5,0),(5,80,5,5,5),(5,5,80,5,5),(5,5,5,5,80)]:
        t = PlayerTrajectory(competitive_focus=cf, exploration_depth=ex,
                             research_investment=ri, breeding_intensity=bi,
                             anomaly_exposure=an)
        ax = compute_behavioral_axis(t)
        axes_seen.add(ax)
        print(f"  cf={cf} ex={ex} ri={ri} bi={bi} an={an} → {ax.name}")
    print(f"  ✓ Distinct axes from 4 profiles: {len(axes_seen)}")

    # §20 - Narrative profile
    print(f"\n=== §20 Narrative Profile ===")
    np_prof = generate_island_narrative(seed, base_tier)
    print(f"  Primary mountains: {np_prof.primary_global_boulders}")
    print(f"  Primary mysteries: {np_prof.primary_mysteries}")
    print(f"  Active arcs: {np_prof.active_character_arcs}")
    print(f"  League conflict: {np_prof.primary_league_conflict}")
    print(f"  ✓ CA3 forced: {'CA3' in np_prof.active_character_arcs}")
    print(f"  ✓ 8-12 primary mountains: "
          f"{8 <= len(np_prof.primary_global_boulders) <= 12}")

    # §21 - Hidden Knower
    print(f"\n=== §21 Hidden Knower ===")
    knower = generate_hidden_knower(topo, np_prof, seed)
    print(f"  Archetype: {knower.archetype.name}")
    print(f"  Name: {knower.name}")
    print(f"  Location: {knower.location_node_id}")
    loc_node = topo.nodes.get(knower.location_node_id)
    print(f"  Node type: {loc_node.node_type.name if loc_node else '?'}")
    print(f"  Fragments: {len(knower.dialogue_fragments)}")
    print(f"  ✓ Location valid: {knower.location_node_id in topo.nodes}")

    # §22 - NGP+ profile
    print(f"\n=== §22 NGP+ Profile ===")
    import tempfile as _tf, os as _os_chk
    _tmp = _tf.mktemp(suffix=".json")
    sig = compute_behavioral_signature(traj, ledger, base_tier, seed)
    save_behavioral_profile(sig, _tmp)
    loaded_sig = load_behavioral_profile(_tmp)
    _os_chk.remove(_tmp)
    print(f"  Behavioral axis: {sig.behavioral_axis}")
    print(f"  Run count after merge: {loaded_sig.run_count}")
    print(f"  ✓ Save/load/merge: {loaded_sig is not None and loaded_sig.run_count == 1}")

    # §23 - Narrative roles
    print(f"\n=== §23 Narrative Outcome Roles ===")
    role = compute_narrative_role(band)
    desc_ext = describe_outcome_band(band)
    print(f"  Band {band} narrative role: {role}")
    print(f"  ✓ narrative_role in describe_outcome_band: {'narrative_role' in desc_ext}")
    print(f"  ✓ All 100 bands covered: {len(NARRATIVE_OUTCOME_ROLES) == 100}")

    # §24 - Echo events (need run_count >= 2 to generate)
    print(f"\n=== §24 Memory Echo System ===")
    fake_profile = BehavioralProfileSignature(
        behavioral_axis="CURIOUS", run_count=3,
        echo_seeds=[seed - 1, seed], anomaly_engagement_history=30.0
    )
    echoes = generate_echo_events(fake_profile, topo, seed)
    print(f"  Echoes generated (run #3): {len(echoes)}")
    if echoes:
        print(f"  First echo: [{echoes[0].echo_type}] {echoes[0].description[:60]}…")
    no_echoes = generate_echo_events(
        BehavioralProfileSignature(run_count=1), topo, seed
    )
    print(f"  Echoes at run #1 (expect 0): {len(no_echoes)}")
    print(f"  ✓ Echo gating: {len(no_echoes) == 0 and len(echoes) >= 2}")

    # §25 - Fragment system
    print(f"\n=== §25 Fragment System ===")
    frags = generate_island_fragments(np_prof, topo, seed)
    print(f"  Fragments selected for this island: {len(frags)}")
    types_present = {f.ftype.name for f in frags}
    print(f"  Fragment types present: {sorted(types_present)}")
    # Check render works
    sample_ctx = {
        "founder": "Dr. Voss", "island_name": topo.island_name,
        "tier": base_tier.name, "species_name": "Thornback",
        "anomaly_count": len(topo.anomaly_zone_ids),
        "variance": 20.0, "collapse_ticks": 150,
    }
    rendered = frags[0].render(sample_ctx) if frags else ""
    print(f"  First fragment: [{frags[0].ftype.name}] {frags[0].title}" if frags else "  (none)")
    print(f"  Render OK: {bool(rendered)}")
    print(f"  ✓ Fragment pool size: {len(FRAGMENT_POOL)} (expect ≥40)")
    print(f"  ✓ Island selection ≤20: {len(frags) <= 20}")

    print("\n✓ Island generation complete.")
