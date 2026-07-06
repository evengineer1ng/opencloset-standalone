# Neikos: Hundred Islands — Module Map
# 
# Source of truth: plugins/neikos/__init__.py
# The package is currently one clean monolith that imports fine.
# New features go in submodules. Extraction happens section by section over time.
#
# SECTION MAP (line references into __init__.py)
# ──────────────────────────────────────────────
# §1   ~51–120    core/rng.py          SeededRNG, _det_hash
# §2   ~121–200   core/types.py        NkType, TYPE_MATRIX, type_multiplier
# §3   ~201–350   core/enums.py        NodeType, MacroRegion, RarityTier, etc.
# §3   ~350–600   core/models.py       BiomeVector, MapNode, IslandTopology, etc.
# §4   ~600–900   world/topology.py    generate_island_topology, helpers
# §5   ~900–1600  world/species.py     generate_species_roster, helpers
# §6   ~1600–1800 world/encounter.py   EncounterTable, generate_encounter_tables, roll_encounter
# §7   ~1800–2200 combat/battle.py     simulate_battle, BattleCreature, BattleResult
# §8   ~2200–2400 combat/breeding.py   breed_creatures, PopulationGenePool
# §9   ~2400–2800 world/factions.py    Faction, IdeologyVector, generate_factions, diffuse_faction_influence
# §10  ~2800–3000 progression/outcomes.py  IslandLedger, compute_outcome_band, describe_outcome_band
# §11  ~3000–3050 world/factions.py    compute_gate_thresholds
# §12  ~3050–3200 progression/trajectory.py  PlayerTrajectory, DialogueDelta
# §17  ~3200–3400 narrative/founder.py FOUNDER_CANON, FounderRecord, resolve_founder_framing
# §18  ~3400–3500 progression/trajectory.py  BehavioralAxis, compute_behavioral_axis
# §19  ~3500–3700 progression/tiers.py ContainmentTier, TIER_CHARACTERISTICS, compute_containment_tier
# §20  ~3700–4200 narrative/fragments.py  GLOBAL_MOUNTAINS, NarrativeMountain, ISLAND_MYSTERY_POOL
# §21  ~4200–???  narrative/knower.py  HiddenKnower, generate_hidden_knower
# §22  ~???       progression/ngp.py   BehavioralProfileSignature, save/load profile
# §23  ~???       progression/outcomes.py  NARRATIVE_OUTCOME_ROLES, compute_narrative_role
# §24  ~???       narrative/echo.py    EchoEvent, generate_echo_events
# §25  ~???       narrative/fragments.py  FRAGMENT_POOL, NarrativeFragment, generate_island_fragments
# §26  ~???       spatial/sublocations.py  Sublocation, SubpageLayout, generate_world_sublocations
# §13  ~5700–6200 controller.py        NKController, IslandState
# §14  ~6200–6600 server.py            _start_web_server
# §15  ~6600–6700 __init__.py          register_widgets
# §16  ~6700+     __init__.py          __main__ smoke test
#
# SUBMODULE STUBS
# ───────────────
# core/rng.py, core/enums.py, core/types.py — re-export from __init__
# All submodule __init__.py files are empty stubs until extraction
#
# EXTRACTION PRIORITY (order to pull sections out)
# ─────────────────────────────────────────────────
# 1. combat/battle.py  (most likely to grow with game dev)
# 2. narrative/        (fragments, knower, founder, echo)
# 3. progression/      (tiers, trajectory, ngp, outcomes)
# 4. world/            (topology, species, factions, encounter)
# 5. spatial/          (sublocations)
# 6. controller.py / server.py (separate out last)
