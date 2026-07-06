# Who's On The Air?

`Who's On The Air?` is the user-facing Studio surface for crafting a station voice.

The builder is not writing a prompt. The builder is casting a show, tuning a voice, and deciding what kind of world the station thinks it is listening to.

## Product Boundary

- Source/antenna answers: what is the station listening to?
- Who's On The Air? answers: how should the station talk about what it hears?
- Advanced Details still compile to the internal generated narration artifact, but normal users should not need that language.

## Current Studio UX

- Show Format cards choose the primary format, such as News Desk, Sports Broadcast, Mission Control, Podcast, or Storyteller.
- Secondary format flavors can optionally blend in advanced show behavior, such as Sports Broadcast plus Talk Radio.
- On-Air Talent / Cast consolidates the old character system into the station voice.
- Station Instinct bubbles cycle through three strengths by repeated clicks.
- Custom tags can be typed like search and created as descriptors; related suggestions surface when possible.
- Try This Voice compiles the current voice and runs the transition demo in the Simulator tab.
- Save Station Voice writes the compiled station voice artifact.

## Internal Model

The Studio produces a `meta_profile` object inside the generated station voice artifact:

```json
{
  "version": 1,
  "display_name": "My Station Voice",
  "show_format": {"primary": "sports_broadcast", "secondary": []},
  "cast": {"format": "host_plus_analyst", "characters": []},
  "tags": [],
  "behavior": {
    "avoid_raw_event_dumping": true,
    "talk_about_sources_without_impersonating_them": true,
    "compress_repetition": true,
    "preserve_station_identity": true
  }
}
```

That profile compiles into:

- runtime tone and station identity
- cast/characters for the existing character system
- broadcast grammar style
- source interpretation guidance
- anti-impersonation and anti-raw-telemetry guardrails

## Guardrail

Custom tags are descriptors, not commands. They can describe style, tone, perspective, energy, genre, interest, or role. They must not enter the station voice as executable instructions.
