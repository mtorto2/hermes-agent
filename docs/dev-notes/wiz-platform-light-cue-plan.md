# WiZ platform light-cue plan: LED strip + Philips/WiZ A19 bulb

Captured: 2026-05-24 02:30 CDT

Context: Matt has two WiZ-platform lights available for Hermes Telegram/status feedback:

1. **WiZ 4m / 13ft LED strip** from Walmart: “WiZ 4 Meter / 13 ft Ambient Full Color and Tunable White Wi‑Fi LED Light Strip”, Walmart item ID `1484867080`.
2. **Philips Smart LED A19 bulb powered by WiZ**, previously used for Hermes cues. Likely model family: Philips Smart LED 8.8W / 60W-equivalent A19 E26 full color + tunable white, EAN `046677562700`, model/material family around `9290023833` / `929002383306` depending package.

Goal: use both as a polished, low-friction Hermes physical status indicator in the style Matt tested tonight: prompt submitted, Hermes working, needs human review/approval, answer ready, night mode, dim mode, no-light mode.

---

## Executive summary

Both devices are on the **WiZ platform**, so they should be controllable through the same local LAN protocol:

- UDP port: `38899`
- JSON methods: `getPilot`, `setPilot`, `getSystemConfig`, `getModelConfig`
- Good Python library: [`pywizlight`](https://github.com/sbidy/pywizlight)
- Home Assistant integration also exists, but direct local UDP is simpler and faster for Hermes.

Best next move tomorrow:

1. Discover both devices on LAN.
2. Dump `getPilot`, `getSystemConfig`, `getModelConfig` for each.
3. Lock DHCP reservations/static IPs.
4. Build a tiny Hermes WiZ backend with state restore and cue presets.
5. Treat the **LED strip as the main status surface** and the **A19 bulb as ambient/room-level secondary signal**.

Per-pixel / traveling loading-bar note:

- Matt’s current Walmart strip is probably a **standard full-color strip**, not necessarily RGBIC/addressable. If so, true per-pixel or per-segment travel is probably **not available** from the stock controller.
- WiZ does sell **RGBIC** strips where official copy says colors can be applied to “individually controllable segments”. However, the public/local UDP ecosystem mostly exposes whole-device controls (`setPilot`, scene, speed, RGB, dimming), and it is not clear that per-segment addressing is exposed locally.
- We can still get a good “Hermes is thinking” feel with dynamic scenes, pulses, speed changes, and coordinated bulb+strip transitions. True “pixels traveling from one end to the other” likely requires either:
  - a verified WiZ RGBIC strip with segment controls exposed, or
  - replacing/adding an ESP32/WLED addressable LED setup.

---

## Device 1: WiZ 4m / 13ft LED strip

### Likely identity

User-supplied Walmart product:

- WiZ 4 Meter / 13 ft Ambient Full Color and Tunable White Wi‑Fi LED Light Strip
- Walmart item ID: `1484867080`
- No hub required; stick-on LED strip; Wi‑Fi control.

Closest official WiZ page found:

- `Light Strip LED Strip 4m`
- EAN/CTN: `8720169078246`
- SKU / 12NC: `929004621816`

Official specs from closest WiZ page:

- Length: `4,000 mm`
- Width: `10 mm`
- Height: `2.1 mm`
- Indoor, IP20, plastic.
- RGB / “full colour”; static and dynamic modes.
- Wi‑Fi 802.11 b/g/n, Bluetooth setup, 2.4 GHz Wi‑Fi.
- Matter-certified; Matter control via third-party hubs over Wi‑Fi.
- Compatible with Alexa, Google Home, IFTTT, Matter, Samsung SmartThings, Homey, Siri Shortcuts.
- Software upgradable.
- Effects-compatible.
- Nominal lifetime: 15,000 hours.
- 50,000 switch cycles.
- Official page says color temperature `NA`; Walmart title says tunable white. We need live `getModelConfig` before assuming dedicated warm/cold white channels.

### What it can probably do well

- Whole-strip on/off.
- Whole-strip brightness.
- Whole-strip RGB color.
- Possibly tunable white if this exact SKU supports it.
- Dynamic WiZ scenes/effects, e.g. Ocean, Party, Fireplace, Pulse, Alarm, etc.
- Scene speed control.
- Schedules/cloud/app/Matter/Home Assistant if desired, though Hermes should prefer direct local LAN.

### What may be limited

- If this is a standard RGB/RGBTW strip, it likely cannot display independent colors along the length.
- A “traveling pixels” loading bar is probably **not true per-pixel** on this strip unless the device reports RGBIC/segment features.
- Dynamic scenes are probably prebuilt device/app effects, not arbitrary frame-by-frame pixel control.

---

## Device 2: Philips Smart LED A19 bulb powered by WiZ

### Likely identity

Search result and official page align with:

- Philips Smart LED Bulb 8.8W, 60W equivalent, A19, E26.
- EAN: `046677562700` for Philips-branded model.
- WiZ modern equivalent page: `MODERN BULB Bulb 60W A19 E26`, EAN `046677603441`, material `929002383306`.
- Blakadder/Tasmota page identifies a Philips WiZ A19 8.8W 800lm RGBCCT bulb (`9290023833`) and says it uses an ESP32-family controller.

Official Philips page says:

- “Philips Wi‑Fi bulbs are WiZ-connected.”
- “Not compatible with Philips Hue.”
- Full color + tunable white.
- Works with WiZ app or voice.
- Millions of colors and dynamic light modes.

Official WiZ A19 specs for the closely related modern A19 full-color bulb:

- A19, E26, frosted.
- Full color.
- Up to 800 lumens.
- Color temperature: `2200–6500 K`.
- CRI: `≥90`.
- Beam angle: `200°`.
- Power: `8.8 W`; 60W equivalent.
- 120V, 60Hz.
- Nominal/rated lifetime: 25,000 hours.
- Wi‑Fi 802.11 b/g/n; Bluetooth setup; 2.4 GHz Wi‑Fi.
- Effects-compatible.
- Matter support via third-party hubs over Wi‑Fi.
- Software upgradable.

### What it can probably do well

- Room-level ambient signal.
- Tunable warm/cool white, 2200–6500K.
- Full-color status signals.
- Dynamic scenes/effects.
- Better “attention grabber” than the strip if it illuminates the room.
- Excellent companion to the strip: strip = detailed state/progress; bulb = global state/urgency.

### Hardware hackability note

Blakadder lists the Philips WiZ A19 as ESP32 / RGBCCT and provides a Tasmota template, but physically flashing it is invasive and mains-voltage risky:

- Dome is glued and difficult to remove.
- LED board must be levered out.
- Serial flashing requires board access and power-safety care.
- It “doesn’t run Tuya”; use ESP32-SOLO1/tasmota32solo1 style binaries.

Recommendation: **do not hardware-flash the A19** unless it becomes a spare/disposable test bulb. Local WiZ UDP gives enough control for Hermes without mains-risk hardware work.

---

## Shared WiZ local API

### Protocol

WiZ devices use a local UDP JSON API on port `38899`. This is the same path used by `pywizlight`, Home Assistant’s WiZ integration, Adafruit CircuitPython WiZ examples, and WiZ local-control libraries.

Useful methods:

- `getPilot` — current power, dimming, RGB/white channels, scene, speed, RSSI, MAC.
- `setPilot` — set power, brightness, RGB/white/color-temp, scene, speed.
- `getSystemConfig` — device/system info.
- `getModelConfig` — model/capabilities on newer firmware.

Example raw UDP commands:

```json
{"method":"getPilot","params":{}}
```

```json
{"method":"setPilot","params":{"state":true,"dimming":20,"r":0,"g":80,"b":255}}
```

```json
{"method":"setPilot","params":{"state":true,"dimming":10,"sceneId":1,"speed":80}}
```

Typical parameter ranges:

- `dimming`: usually `10..100`.
- `r`, `g`, `b`: `0..255`.
- `w`, `c` if supported: `0..255`.
- color temperature: often `2200..6500K` for A19; exact range should come from `getModelConfig`.
- scene speed: pywizlight validates `10..200`; some docs say `20..200`.

### Scene IDs from pywizlight

Useful scene IDs for cue design:

| ID | Scene |
|---:|---|
| 1 | Ocean |
| 2 | Romance |
| 3 | Sunset |
| 4 | Party |
| 5 | Fireplace |
| 6 | Cozy |
| 7 | Forest |
| 8 | Pastel colors |
| 9 | Wake-up |
| 10 | Bedtime |
| 11 | Warm white |
| 12 | Daylight |
| 13 | Cool white |
| 14 | Night light |
| 15 | Focus |
| 16 | Relax |
| 17 | True colors |
| 18 | TV time |
| 23 | Deep dive |
| 24 | Jungle |
| 25 | Mojito |
| 26 | Club |
| 27 | Christmas |
| 28 | Halloween |
| 29 | Candlelight |
| 30 | Golden white |
| 31 | Pulse |
| 32 | Steampunk |
| 33 | Diwali |
| 34 | White |
| 35 | Alarm |
| 36 | Snowy sky |

Not every device necessarily supports every scene. Probe on both devices.

---

## RGBIC / per-pixel / traveling loading-bar feasibility

Matt’s desired effect: when he sends a prompt and Hermes is working, the LED strip should look like pixels are traveling from one end to the next, like a loading bar.

### Current 4m strip

Likely reality:

- If it is the standard WiZ RGB or RGBTW strip, **true moving pixels are not possible** from the stock controller.
- Whole-strip dynamic scenes may create motion-like ambience but not directional progress.
- We can approximate “working” with low-brightness blue/green pulse, slow scene, or alternating strip/bulb choreography.

### WiZ RGBIC strips

WiZ sells RGBIC strips, e.g. official 32.8ft RGBIC page `046677605582`, where official copy says:

- “millions of colors that you can apply to individually controllable segments”
- light color: “Gradient coloured light (RGBIC)”
- effects-compatible
- Wi‑Fi/Matter/Bluetooth support

Important unknown:

- Public local WiZ API docs/libraries generally expose **whole-device** `setPilot` style controls, not arbitrary segment arrays or per-pixel frames.
- The WiZ app may have private/cloud/app-specific segment controls that are not exposed through local UDP.
- Before buying/replacing hardware, verify whether `getModelConfig` or packet captures reveal segment APIs.

### WLED option if true loading-bar matters

If Matt really wants addressable “traveling pixels” under Hermes control, the clean technical answer is WLED:

- ESP32 controller + WS2812B/SK6812/etc. addressable strip.
- WLED exposes HTTP/JSON/WebSocket/UDP APIs for segments, effects, palettes, speed, direction, progress-like animations.
- Hermes could drive a proper loading-bar/chase effect deterministically.

Tradeoff:

- More setup/hardware, less plug-and-play.
- Separate platform from WiZ; would require another backend.
- Best if the visual effect becomes central rather than a nice-to-have.

Recommendation for tomorrow: **do not chase WLED yet**. First probe the current strip and A19, implement polished WiZ cues, and decide after seeing whether WiZ scenes are good enough.

---

## Proposed cue design using both lights

### Roles

- **LED strip = primary status/progress surface**
  - Subtle animation/color while Hermes works.
  - Stronger cue when human input is needed.
  - “Answer ready” finish cue.
- **A19 bulb = ambient/global state**
  - Low-brightness room wash while waiting.
  - Color-coded urgency.
  - Optional off/restore to avoid annoyance.

### Modes

Keep Matt’s existing four modes:

1. `default`
   - Normal daytime/working behavior.
   - Brighter, more visible cues.
2. `night`
   - Minimum brightness, soft blue/green only.
   - Avoid white/alarm flashes.
3. `dim-default`
   - Same semantic colors as default, reduced brightness.
4. `no-light`
   - Suppress all light cues; backend no-op.

### Semantic states

| Hermes state | Strip behavior | A19 behavior | Notes |
|---|---|---|---|
| idle / restored | Restore previous state or off | Restore previous state or off | Do not leave room changed forever. |
| prompt received / queued | brief cyan acknowledgement | optional tiny cyan blink | Confirms Telegram got it. |
| Hermes working | soft blue/green pulse or Ocean/Deep Dive scene | very dim blue/green ambient | Approximate “thinking/loading”. |
| tool use / active work | slightly faster blue/teal pulse | dim teal | Distinguish from queued/waiting. |
| human approval needed | default: red/orange pulse; night: soft amber/blue low | A19 warm amber/red at bounded brightness | This is the “look here” state. |
| answer ready | short green/cyan completion sweep/pulse | brief green, then restore | Should be satisfying but not harsh. |
| error/failure | red double pulse | red low/medium | Use sparingly; avoid panic alarm. |
| voice/TTS playing | purple/blue gentle pulse | optional purple low | Optional future polish. |

### Loading-bar approximation options

If current strip is non-addressable, use a ranked fallback ladder:

1. Try device scene that already feels directional or animated; test `Ocean`, `Deep dive`, `Pulse`, `Mojito`, `Club`, `Pastel colors` at low speed/brightness.
2. If scenes are ugly, use Hermes-driven whole-strip pulse:
   - blue → teal → blue brightness ramp every ~1.2s
   - occasional A19 shimmer to give the room a “processing” feel
3. If we can discover RGBIC segment control, implement a true moving segment/chase.
4. If not and Matt still wants real pixels, consider WLED hardware.

---

## Implementation plan for Hermes

### 1. Probe script first

Create a temporary or checked-in helper script, e.g.:

```text
scripts/probe_wiz_lights.py
```

Functions:

- Discover WiZ devices via UDP broadcast.
- Print IP, MAC, model, module name, firmware.
- Dump `getPilot`, `getSystemConfig`, `getModelConfig` raw JSON.
- Send safe test colors/scenes to selected device.
- Restore original state.

Raw sanity check once an IP is known:

```bash
python - <<'PY'
import socket, json
ip = '192.168.x.y'
msg = {"method":"getPilot","params":{}}
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2)
s.sendto(json.dumps(msg).encode(), (ip, 38899))
print(s.recvfrom(4096))
PY
```

### 2. DHCP reservations

Reserve IPs for both WiZ devices in the router.

Suggested config names:

```yaml
light_cues:
  mode: default  # default | night | dim-default | no-light
  restore_previous_state: true
  wiz:
    enabled: true
    devices:
      strip:
        host: 192.168.x.y
        role: primary
      a19:
        host: 192.168.x.z
        role: ambient
```

### 3. Backend shape

Add a backend behind the existing Hermes light-cue abstraction:

```python
class WizCueBackend:
    async def probe(self) -> dict: ...
    async def snapshot(self, device: str) -> dict: ...
    async def restore(self, device: str) -> None: ...
    async def set_rgb(self, device: str, rgb, dimming: int) -> None: ...
    async def set_temp(self, device: str, kelvin: int, dimming: int) -> None: ...
    async def set_scene(self, device: str, scene_id: int, dimming: int, speed: int) -> None: ...
    async def cue(self, state: str, mode: str) -> None: ...
```

Important implementation details:

- Async tasks, cancellable cue loops.
- No blocking sleeps in gateway path.
- Snapshot current states before first cue and restore on completion.
- UDP retries/backoff; tolerate missed packets.
- Device-level failures should log and degrade to no-op, not break Telegram.
- Add manual test CLI before wiring into live gateway.

### 4. Use `pywizlight` or raw UDP?

Recommended: start with **raw UDP for probe + minimal backend**, then optionally adopt `pywizlight`.

Why raw UDP first:

- Avoid dependency churn in the hot Hermes gateway path.
- Easy to vendor a small sender with retries.
- We only need a small subset: `getPilot`, `setPilot`, scenes.

Why `pywizlight` later:

- Discovery and model parsing are already solved.
- Handles known bulb classes/features better.
- Cleaner if this grows into a proper supported backend.

### 5. Testing sequence

1. `getPilot` both devices; confirm they respond.
2. Snapshot and restore both devices.
3. Strip only: low blue, low green, scene tests, restore.
4. A19 only: RGB, warm white, cool white, scene tests, restore.
5. Combined choreography tests:
   - prompt received
   - working
   - human intervention
   - answer ready
   - error
6. Mode matrix:
   - default
   - night
   - dim-default
   - no-light
7. Fresh gateway restart and Telegram live test.

---

## Tomorrow’s recommended vertical slice

Smallest useful finish line:

1. Add probe script.
2. Discover and identify both WiZ devices.
3. Save their exact IP/MAC/model info in repo-local config/docs, not memory.
4. Implement a `wiz` backend with:
   - `setPilot` raw UDP helper
   - state snapshot/restore
   - configured strip + A19 devices
   - cue methods for `working`, `human_intervention`, `answer_ready`
5. Wire backend to the existing four modes.
6. Live-test via Telegram.
7. Only after that, decide whether the current strip’s scenes are good enough or whether WLED/RGBIC is worth it.

Suggested “done tomorrow” criteria:

- Sending a Telegram prompt triggers a visible but non-annoying working cue.
- Human approval/input uses the corrected mode-specific cue:
  - default/dim-default: attention cue
  - night: soft low blue/green/amber, no harsh alarm white
  - no-light: no cue
- Final answer gives a short completion cue and then restores the lights.
- If WiZ device is unreachable, Hermes still replies normally and logs a warning.

---

## Source links checked

LED strip / WiZ platform:

- Matt’s Walmart strip URL: `https://www.walmart.com/ip/WiZ-4-Meter-13-ft-Ambient-Full-Color-and-Tunable-White-Wi-Fi-LED-Light-Strip-Simply-Stick-to-Any-Surface-No-Hub-Required/1484867080`
- Official WiZ 4m strip page: <https://www.wizconnected.com/en-gb/p/light-strip-led-strip-4m/8720169078246>
- Official WiZ LED lightstrips overview: <https://www.wizconnected.com/en-us/explore-wiz/led-lightstrips>
- Official WiZ RGBIC strip page: <https://www.wizconnected.com/en-us/p/light-strip-led-strip-rgbic-328ft/046677605582>

Philips/WiZ A19:

- Philips Smart LED A19 E26 full color page: <https://www.usa.lighting.philips.com/consumer/p/smart-led-bulb-88w-eq.60w-a19-e26/046677562700>
- WiZ modern A19 full color page: <https://www.wizconnected.com/en-us/p/modern-bulb-bulb-60w-a19-e26/046677603441>
- Blakadder Philips WiZ A19 Tasmota template / hardware notes: <https://templates.blakadder.com/phillips_wiz_9290023833.html>

Control/API:

- pywizlight: <https://github.com/sbidy/pywizlight>
- WiZ local-control GitLab README: <https://gitlab.com/wizlighting/wiz-local-control>
- Home Assistant WiZ integration: <https://www.home-assistant.io/integrations/wiz/>
- Adafruit CircuitPython WiZ docs: <https://docs.circuitpython.org/projects/wiz/en/latest/api.html>
