# Elite Trade Crew

This bridge lets Radio OS act as the spoken crew layer while EDAPGui continues to do what it is already good at: route execution, supercruise approach, docking, refuel and waypoint trading.

It now includes an in-runtime crew console widget, so you do not need to edit route JSON by hand once the station is running.

It also includes a guided setup wizard and a managed Ollama starter that pins the local Ollama server to the GTX 1080 Ti.

## Architecture

- Radio OS feed plugin: `plugins/elite_trade_crew.py`
- Radio OS UI widget: `plugins/elite_trade_crew.py` via `register_widgets()`
- Route planner format: `stations/PiperCrew/trade_loop.sample.json`
- EDAP waypoint compiler + bridge: `elite_edap_bridge.py`
- Managed Ollama launcher: `start-ollama-1080ti.ps1`
- Sample station: `stations/PiperCrew/manifest.yaml`

The plugin compiles a simple trade-loop plan into EDAP's `waypoints.json` shape, writes it to disk, and can push that file to a running EDAP instance over EDMesg.

## Why this seam

EDAP already exposes the exact external control surface needed for this project:

- `LoadWaypointFileAction`
- `StartWaypointAssistAction`
- `StopAllAssistsAction`
- `GenericAction(name="WriteTCEShoppingList")`

That means Radio OS does not need to reimplement flight control, OCR, docking, or autopilot state machines.

## Setup

1. In EDAP, enable EDMesg in `configs/AP.json`:

```json
{
  "EnableEDMesg": true,
  "EDMesgActionsPort": 15570,
  "EDMesgEventsPort": 15571
}
```

2. Launch EDAP normally and make sure your ship is calibrated before you try fully automatic looping.

3. In Radio OS, start the `PiperCrew` station.

4. The `Elite Trade Crew` widget should appear automatically in the right panel when the station feed is enabled. If it does not, use the runtime toolbar `Add Widget` action and select `elite_trade_crew`.

5. Use the widget `Setup Wizard` button if you want guided first-run setup. It can:
  - detect the preferred GPU
  - start Ollama pinned to the GTX 1080 Ti
  - fill the EDAP path from the downloaded copy
  - load a sample route

6. Use the main widget to edit route legs, save the loop, and sync or start it from inside Radio OS.

7. Once you trust the route, enable `Auto start after sync` in the widget if you want the route to arm itself after every push.

## Ollama GPU pinning

Your machine currently reports:

- GPU 0: RTX 5060 Ti
- GPU 1: GTX 1080 Ti

The managed launcher targets the GTX 1080 Ti by preference and starts Ollama with `CUDA_VISIBLE_DEVICES` set to that GPU UUID, which is safer than relying on plain index ordering alone.

You can also start it outside the widget from the Windows launcher with option `6`.

## Route format

Each entry in `legs` becomes one EDAP waypoint row.

- `system_name`: Elite system name
- `station_name`: station or carrier name
- `buy_commodities`: items EDAP should buy at that stop
- `sell_commodities`: items EDAP should sell at that stop
- `system_bookmark_type` / `system_bookmark_number`: optional bookmark targeting helpers
- `repeat`: if true, the bridge appends an EDAP `REPEAT` row for endless loops

## Inara workflow

The current implementation does not scrape Inara directly.

The intended first workflow is:

1. Find the loop in Inara.
2. Copy the buy and sell stations into the widget fields.
3. Save the plan and let Radio OS narrate and push the loop to EDAP.

That keeps the automation bounded and predictable while still giving you a persistent crew + autopilot setup.

## TCE option

If you use TCE alongside EDAP, set `write_tce_shopping_list: true` in the station manifest. The bridge will ask EDAP to regenerate its TCE shopping list after the route update.