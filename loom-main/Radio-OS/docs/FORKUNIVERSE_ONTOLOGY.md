# ForkUniverse Ontology

## Purpose

ForkUniverse needs more than prompts, seeds, and tables.

It needs a backend vocabulary of world concepts that can be simulated.

This is not a normal dictionary.

This is an executable world ontology.

A normal dictionary says:

- what a word means

ForkUniverse's ontology says:

- what a concept does to a world

## Why This Exists

If ForkUniverse is supposed to simulate life, death, love, lust, loss, debt, faith, shame, crime, inheritance, illness, status, hunger, weather, migration, and memory, those things cannot remain only English words.

They must become structured primitives.

That is how the engine avoids slop.

The LLM should not invent the universe from nothing.

The backend should know:

- what concepts exist
- how they create pressure
- what they intensify
- what they decay into
- what kinds of threads and predictions they spawn
- what kinds of radio surfaces they emit

## Core Idea

Each concept is a simulation object.

Example:

```json
{
  "concept_id": "love",
  "label": "Love",
  "category": "relationship_force",
  "affects": ["loyalty", "risk_tolerance", "jealousy", "sacrifice", "attention"],
  "creates_threads": [
    "confession",
    "betrayal",
    "marriage",
    "jealousy",
    "protective_action"
  ],
  "decays_with": ["neglect", "betrayal", "distance"],
  "intensifies_with": ["shared_danger", "kindness", "proximity"]
}
```

That is useful to the engine.

## Concept Registry

ForkUniverse should maintain a concept registry.

Possible names:

- ForkUniverse Concept Registry
- World Ontology
- Executable Dictionary

The registry should be versioned and live alongside rulesets.

## Concept Categories

At minimum, the ontology should support these category families:

- `life_cycle`
- `relationship_force`
- `obligation`
- `resource_pressure`
- `status_force`
- `belief_force`
- `memory_force`
- `body_state`
- `institutional_force`
- `scarcity_force`
- `violence_force`
- `myth_force`
- `environmental_force`
- `desire_force`
- `fear_force`

Example domain buckets:

- Life
- Death
- Love
- Power
- Money
- Health
- Family
- Status
- Faith
- Crime
- War
- Work
- Memory
- Weather
- Technology
- Myth
- Desire
- Fear
- Obligation
- Scarcity
- Discovery
- Decay

## Concept Record Contract

Each concept record should define:

- `concept_id`
- `label`
- `category`
- `description`
- `affects`
- `creates_events`
- `creates_threads`
- `creates_predictions`
- `decays_with`
- `intensifies_with`
- `resolution_modes`
- `failure_modes`
- `radio_surfaces`
- `default_coefficients`
- `tags`

## Example Concepts

### Love

Love is not just sentiment.

It is a force that can:

- increase loyalty
- increase sacrifice
- increase jealousy
- increase risk tolerance
- create confession / marriage / betrayal / protective-action threads

### Debt

Debt is not just bookkeeping.

It is a force that can:

- increase stress
- increase dependency
- increase power imbalance
- create repayment, blackmail, desperate-choice, and favor-owed threads

### Rumor

Rumor is not just dialogue.

It is a force that can:

- distort reputation
- raise fear
- weaken trust
- amplify mythology
- create surfaces like whispers, bulletins, denials, and panics

### Grief

Grief is not just sadness.

It can:

- lower initiative
- intensify memory
- destabilize promises
- create withdrawal, revenge, denial, and memorialization threads

## Ontology and Compiler Relationship

The ontology sits between:

- `CreationRequest`
- `CompilerFill`

The form says:

- haunted coastal town
- 18 characters
- horror / mystery

The ontology says:

- use concept families like disappearance, secrecy, fear, taboo, isolation, rumor, guilt, investigation, death

The compiler uses that to generate:

- character seeds
- institutions
- relationship tensions
- starting threads
- prediction templates
- pressure coefficients

## Ontology and LLM Relationship

The ontology constrains the LLM.

The LLM should:

- phrase
- texture
- name
- elaborate

But the ontology should ground:

- what concepts are available
- what they do mechanically
- which surfaces they emit

The backend might know:

- Character A owes debt to Character B
- Character A loves Character C
- Character C needs medicine
- a crime opportunity surfaced

The LLM can then narrate that well.

But the world logic should not depend on the LLM inventing debt pressure from scratch.

## Ontology and Runtime

At runtime, the engine should be able to ask:

- what concepts are active here?
- what pressures do they apply?
- what threads can they spawn?
- what failure modes do they favor?
- what radio surfaces should they emit?

This makes the ontology a reusable middle layer for:

- compiler generation
- tick logic
- narrative surface generation
- future balancing

## First Implementation Target

The first ontology slice does not need everything.

It should include a compact but meaningful starter registry:

- love
- lust
- death
- grief
- debt
- hunger
- illness
- ambition
- rivalry
- rumor
- status
- shame
- faith
- promise
- secret
- contract
- crime
- inheritance
- migration
- weather

If those are structured well, ForkUniverse starts gaining real semantic bones.
