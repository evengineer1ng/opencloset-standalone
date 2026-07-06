# Broadcast Grammar

Broadcast Grammar is the runtime layer that keeps a Radio OS station from sounding like disconnected prompt fragments.

## Responsibility Split

- Python runtime decides when a transition is needed, why it is needed, and what type it is.
- The station meta-profile decides how transitions should feel.
- The LLM decides the final wording.

The LLM should not decide whether a transition is needed.

## Current Implementation

- `broadcast_grammar.py` provides the detector, show-state memory, transition request schema, style presets, and deterministic fallback wording.
- `plugins/meta/generated.py` reads `broadcast_grammar` from `meta_plugin_spec.json`, detects transitions during `generate_script()`, and passes structured transition requests into the LLM prompt.
- `radio_os_studio.py` includes a Simulator tab transition demo so authors can preview weather -> coding -> operations -> coding movement from the current spec.
- `.oradio` export bundles `broadcast_grammar.py` with generated-meta stations so transition behavior stays artifact-portable.

## Transition Request Shape

```json
{
  "type": "transition",
  "transition_reason": "topic_shift",
  "transition_mode": "turning_now_to",
  "from_topic": "hockey",
  "from_topic_label": "hockey",
  "to_topic": "coding_harness",
  "to_topic_label": "coding harness",
  "from_segment": "score",
  "to_segment": "test_failed",
  "heat_delta": 0.05,
  "priority_delta": 5.0,
  "style": "news_desk",
  "interruption": false
}
```

## Reasons Detected

- `topic_shift`
- `signal_priority_shift`
- `segment_change`
- `heat_change`
- `story_completion`
- `story_return`

## Meta-Profile Fields

`meta_plugin_spec.json` may define:

```json
{
  "broadcast_grammar": {
    "style": "news_desk",
    "interruption_tolerance": 0.65,
    "recap_behavior": "brief",
    "callback_behavior": "return with one sentence of context",
    "segment_pacing": "steady",
    "urgency_handling": "interrupt for high-priority changes",
    "source_topics": {
      "hockey_feed": "hockey"
    },
    "topic_labels": {
      "coding_harness": "the coding desk"
    }
  }
}
```

Built-in styles currently include `mission_control`, `news_desk`, `sports_broadcast`, `casual_podcast`, and `hype_bro_radio`.

## Next Work

- Move Broadcast Grammar deeper into the shared producer scheduler if non-generated meta-plugins need it globally.
- Add audible Studio transition demo output instead of text-only demo lines.
- Teach the editor UI to expose grammar fields as friendly station-format controls, not raw JSON only.
