# ForkUniverse Creation Schema

## Purpose

This document defines the actual creation contract for ForkUniverse.

It answers:

- what the operator fills out
- what the LLM is allowed to fill out
- what the compiler must generate
- what the backend will run

This is the bridge between:

- flashy sandbox UI
- one-time LLM coherence pass
- deterministic simulation backend

## Creation Pipeline

```text
Sandbox Form
→ CreationRequest
→ UniverseBrief
→ LLM Schema Fill
→ CompiledWorldPackage
→ Query-Driven Tick Runtime
```

## Core Principle

The LLM is allowed to fill structured fields once during compilation.

The LLM is not allowed to:

- invent live world state during ticks
- bypass deterministic table generation
- act as the simulation itself

The engine must run from compiled tables.

## Stage 1: Sandbox Form

This is the user-facing product layer.

The UI can be cinematic and wild, but every answer must map to a structured field.

### Required Form Fields

- `universe_title`
- `premise`
- `setting_kind`
- `time_period`
- `story_mode`
- `world_scale`
- `starting_population`
- `seed_mode`

### Optional Form Fields

- `location_flavor`
- `genre_mix`
- `tone_mix`
- `operator_insert_mode`
- `operator_role_hint`
- `starting_context`
- `violence_ceiling`
- `romance_ceiling`
- `absurdity_ceiling`
- `institutional_density`
- `economic_harshness`
- `entropy_rate`
- `simulation_rate_preset`
- `execution_model`
- `world_seconds_per_real_second`
- `custom_seed`

## Stage 2: CreationRequest

This is the first backend-owned object. It should be direct, minimal, and operator-authored.

### CreationRequest shape

```json
{
  "schema_version": "forkuniverse.creation_request.v1",
  "universe_title": "Frontier Mirror",
  "premise": "A Wild West theme park populated by AI hosts and wealthy visitors starts destabilizing when memory glitches accumulate.",
  "setting_kind": "theme_park_western_scifi",
  "time_period": "near_future",
  "story_mode": "continuous",
  "world_scale": "district",
  "starting_population": 48,
  "seed_mode": "preset",
  "preset_id": "westworld_frontier",
  "custom_seed": "19383ybfgobblegork",
  "genre_mix": {
    "drama": 0.35,
    "mystery": 0.25,
    "thriller": 0.20,
    "comedy": 0.05,
    "romance": 0.15
  },
  "tone_mix": {
    "serious": 0.55,
    "melancholic": 0.20,
    "playful": 0.05,
    "grand": 0.20
  },
  "starting_context": "A new premium guest season begins during a heatwave and a subtle host memory drift event.",
  "operator_insert_mode": "observer",
  "operator_role_hint": "",
  "constraints": {
    "violence_ceiling": 0.60,
    "romance_ceiling": 0.40,
    "absurdity_ceiling": 0.15,
    "institutional_density": 0.80,
    "economic_harshness": 0.45,
    "entropy_rate": 0.55
  },
  "time_policy": {
    "execution_model": "on_demand",
    "preset": "adaptive_medium",
    "world_seconds_per_real_second": 60.0
  }
}
```

## Stage 3: UniverseBrief

This is a normalized internal object derived from the form.

It should:

- clean user inputs
- resolve enums and presets
- derive defaults
- produce a compact canonical prompt package for the LLM fill step

### UniverseBrief responsibilities

- canonicalize seed inputs
- map `setting_kind` to a ruleset family
- map `world_scale` to entity count ranges
- derive target table counts
- derive allowed institution types
- derive initial thread families
- derive initial prediction families

### UniverseBrief shape

```json
{
  "schema_version": "forkuniverse.universe_brief.v1",
  "ruleset_family": "westworld_frontier",
  "canonical_seed": "19383ybfgobblegork",
  "seed_hash": "sha256:...",
  "execution_model": "on_demand",
  "time_policy_preset": "adaptive_medium",
  "world_seconds_per_real_second": 60.0,
  "population_targets": {
    "major_characters": 12,
    "supporting_characters": 36,
    "organizations": 6,
    "districts": 4,
    "starting_threads": 10,
    "starting_predictions": 18
  },
  "pressure_profile": {
    "social": 0.65,
    "mystery": 0.70,
    "scarcity": 0.25,
    "institutional": 0.80,
    "romantic": 0.35,
    "entropy": 0.55
  },
  "compiler_prompt_inputs": {
    "premise": "...",
    "tone": "...",
    "genre_mix": {},
    "constraints": {},
    "allowed_world_vocab": []
  }
}
```

## Stage 4: LLM Schema Fill

This is the only place where the LLM gets to create world-specific coherence.

The LLM must return structured JSON only.

It should fill:

- naming banks
- role archetypes
- institution templates
- location templates
- character seeds
- starting relationship seeds
- thread templates
- prediction templates
- audio signature tendencies

The LLM should not fill:

- exact long-term tick outcomes
- live simulation events
- unconstrained prose blobs

## LLM Fill Contract

### Input

- `UniverseBrief`
- fixed compiler instructions
- allowed enum sets
- target row counts

### Output

- `CompilerFill`

### CompilerFill shape

```json
{
  "schema_version": "forkuniverse.compiler_fill.v1",
  "setting_profile": {
    "short_description": "A premium Wild West simulation park with layered corporate control and unstable host memory systems.",
    "social_logic": "Humans with money and authority distort a labor caste of artificial hosts while staff maintain the illusion of order.",
    "dominant_conflicts": [
      "memory instability",
      "class hierarchy",
      "guest abuse",
      "corporate secrecy"
    ]
  },
  "naming_banks": {
    "person_given": ["Mae", "Elias", "June"],
    "person_family": ["Mercer", "Vale", "Cross"],
    "organization": ["Del Oro Attractions", "Mesa Black Operations"],
    "district": ["Sweetwater Row", "Maintenance Spine"]
  },
  "role_archetypes": [
    {
      "archetype_id": "host_gunslinger",
      "label": "Host Gunslinger",
      "trait_bias": {
        "status_drive": 0.30,
        "obedience": 0.70,
        "curiosity": 0.45
      }
    }
  ],
  "institution_templates": [],
  "location_templates": [],
  "character_seeds": [],
  "relationship_templates": [],
  "thread_templates": [],
  "prediction_templates": [],
  "audio_tendencies": {
    "default_signatures": ["dramatic_hush", "meanwhile_transition"]
  }
}
```

## Stage 5: CompiledWorldPackage

This is the backend truth package.

It is the result of:

- user form inputs
- normalized brief
- LLM schema fill
- deterministic seeded expansion

The runtime consumes this package and nothing else.

## CompiledWorldPackage files

- `universe_brief.json`
- `compiler_fill.json`
- `world_tables.json`
- `coefficient_profile.json`
- `time_policy.json`
- `seed_manifest.json`

## Compiled Table Families

The compiler must produce at least these table groups.

### 1. `characters`

Each row should include:

- `character_id`
- `display_name`
- `archetype_id`
- `origin_location_id`
- `home_location_id`
- `organization_ids`
- `trait_vector`
- `resource_state`
- `desire_vector`
- `fear_vector`
- `stress_profile`
- `ledger_seed`

### 2. `relationships`

Each row should include:

- `relationship_id`
- `source_character_id`
- `target_character_id`
- `affection`
- `trust`
- `dependency`
- `resentment`
- `attraction`
- `loyalty`
- `fear`
- `history_depth`

### 3. `organizations`

Each row should include:

- `organization_id`
- `label`
- `type`
- `district_id`
- `power_score`
- `wealth_score`
- `policy_profile`
- `member_ids`
- `tension_profile`

### 4. `locations`

Each row should include:

- `location_id`
- `label`
- `location_type`
- `parent_location_id`
- `pressure_tags`
- `population_capacity`
- `economic_heat`
- `danger_heat`
- `symbolic_weight`

### 5. `obligations`

Each row should include:

- `obligation_id`
- `obligation_type`
- `holder_id`
- `counterparty_id`
- `start_tick`
- `due_tick`
- `stakes`
- `failure_cost`
- `success_reward`
- `status`

### 6. `macro_state`

Each row should include:

- `axis_id`
- `baseline`
- `current_value`
- `normalization_bias`
- `drift_rate`

### 7. `story_threads`

Each row should include:

- `thread_id`
- `title`
- `domain`
- `participant_ids`
- `status`
- `confidence`
- `urgency`
- `heat`
- `predicted_resolution_tick`
- `source_event_ids`

### 8. `predictions`

Each row should include:

- `prediction_id`
- `predictor_type`
- `predictor_id`
- `target_type`
- `target_id`
- `thread_id`
- `claim_type`
- `confidence`
- `horizon_ticks`
- `status`
- `resolution_outcome`

### 9. `memory_records`

Each row should include:

- `memory_id`
- `memory_tier`
- `owner_type`
- `owner_id`
- `summary`
- `source_event_ids`
- `decay_rate`
- `myth_weight`

### 10. `coefficients`

Each row should include:

- `coefficient_id`
- `scope`
- `name`
- `value`
- `description`

## Compiler Responsibilities

The compiler must do more than copy LLM outputs.

It must:

1. validate enums and ranges
2. apply seed-based expansion
3. generate IDs
4. expand templates into row counts
5. generate balanced relationship networks
6. generate obligations and institutional memberships
7. initialize thread and prediction rows
8. compute coefficient defaults
9. produce a valid deterministic package

## Seed Contract

Seed behavior must be explicit.

### Preset mode

- `preset_id + custom_seed + ruleset_version` defines a reproducible start

### Custom mode

- form inputs are normalized first
- the normalized request is hashed into a canonical creation identity
- the operator may still provide a custom seed override

### Important rule

The same normalized request, same ruleset version, and same seed must compile to the same world package.

## Tick Runtime Minimum Contract

Once compiled, the runtime must be able to operate without consulting the LLM.

The default expectation is that the runtime is query-driven rather than always running.

Required runtime inputs:

- `world_tables`
- `coefficient_profile`
- `time_policy`
- `seed_manifest`

Required runtime outputs:

- state mutations
- event ledger rows
- thread changes
- prediction settlements
- narrative surfaces

## Query-Driven Runtime

ForkUniverse should default to a query-driven runtime model.

That means:

- compiled universes do not need to stay open
- no daemon is required for a universe to remain "alive"
- elapsed real time since last inquiry is converted into owed simulation time
- ticks are computed on demand when a client asks for truth

The simplest version of the system is:

- Radio OS antenna asks for truth now
- ForkUniverse computes elapsed time
- ForkUniverse advances owed ticks
- ForkUniverse returns current truth plus important deltas

## First Real Implementation Target

If we want to action this quickly, the first implementation should support only:

- one `CreationRequest`
- one `UniverseBrief`
- one `CompilerFill`
- one `CompiledWorldPackage`

with only these table groups:

- `characters`
- `relationships`
- `organizations`
- `locations`
- `obligations`
- `story_threads`
- `predictions`
- `coefficients`

That is enough to prove the compiler model.
