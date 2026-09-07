# WiZ 4m / 13ft LED strip research for Hermes light cues

Captured: 2026-05-24 02:13 CDT

Context: Matt is using a Walmart WiZ 4m / 13ft Ambient Full Color + Tunable White Wi‑Fi LED strip for Hermes Telegram/light-cue feedback. Goal is better control, possible “hackability”, and future implementation ideas.

## Product identity / likely capabilities

User-supplied Walmart product:

- WiZ 4 Meter / 13 ft Ambient Full Color and Tunable White Wi‑Fi LED Light Strip
- Walmart item ID in URL: `1484867080`
- No hub required, adhesive strip, Wi‑Fi control.

Closest official WiZ page found:

- `Light Strip LED Strip 4m`, EAN/CTN `8720169078246`, SKU / 12NC `929004621816`.
- Official specs page says:
  - Length: 4,000 mm; width: 10 mm; height: 2.1 mm.
  - Indoor / IP20 / plastic.
  - RGB / “full colour”; supports static + dynamic modes.
  - Wi‑Fi 802.11 b/g/n, Bluetooth setup, 2.4 GHz Wi‑Fi.
  - Matter-certified; Matter control is “via third-party hubs (Wi‑Fi)”.
  - Compatible with Alexa, Google Home, IFTTT, Matter, Samsung SmartThings, Homey, Siri Shortcuts.
  - Software upgradable.
  - Effects feature compatible.
  - Defined support period: minimum 60 months after introduction.
  - Nominal lifetime: 15,000 hours; 50,000 switch cycles.

Caveat: Walmart title says “tunable white”; the closest official GB page text is mostly RGB-only / color-temperature NA. Before relying on C/W channels, ask the device directly with `getModelConfig` / `getSystemConfig` or check the exact model in the WiZ app. Newer US SKUs may differ from the GB `8720169078246` page.

## Best control lane: local UDP API

WiZ devices are unusually friendly for local LAN control. They listen on UDP port `38899` and accept JSON commands. This is what `pywizlight`, Home Assistant’s WiZ integration, and WiZ local-control libraries use.

Useful API methods:

- `getPilot` — current state: on/off, dimming, RGB / white channels, scene, speed, RSSI, MAC.
- `setPilot` — set state, brightness, RGB/white/color temperature, scene, speed.
- `getSystemConfig` — device/system info, including MAC.
- `getModelConfig` — newer firmware model/capability info.

Example raw UDP messages:

```json
{"method":"getPilot","params":{}}
```

```json
{"method":"setPilot","params":{"state":true,"dimming":20,"r":0,"g":80,"b":255}}
```

```json
{"method":"setPilot","params":{"state":true,"dimming":10,"sceneId":1,"speed":80}}
```

Typical ranges from WiZ local-control docs / pywizlight:

- UDP port: `38899`.
- Brightness / `dimming`: usually `10..100`.
- RGB: `r`, `g`, `b` each `0..255`.
- Warm/cold white channels if supported: `w`, `c` each `0..255`.
- Color temp in some APIs: ~`2200..6500K` for local-control examples; WiZ protocol can report wider ranges depending on device.
- Dynamic scene speed: pywizlight validates `10..200`; WiZ local-control docs mention `20..200`.
- Scenes: local-control docs mention scene IDs `1..28`; pywizlight currently supports more (`1..35`) because newer WiZ scenes exist.

## Python option: pywizlight

Library: <https://github.com/sbidy/pywizlight>

Pros:

- Python-native; matches Hermes codebase well.
- Handles UDP retries/backoff, discovery, state parsing, scenes, RGB/RGBW/RGBWW capability classes.
- Used by Home Assistant ecosystem.

Potential Hermes approach:

```python
from pywizlight import wizlight, PilotBuilder

bulb = wizlight("192.168.x.y")
await bulb.turn_on(PilotBuilder(brightness=20, rgb=(0, 80, 255)))
state = await bulb.updateState()
```

Implementation note: for gateway responsiveness, avoid blocking sleeps; implement cue lifecycle as async tasks with cancellation and final restore/off behavior.

## Discovery / test commands

Discovery is usually UDP broadcast on port `38899` using a `registration` message. pywizlight already supports discovery; raw discovery is possible but easier to let a library handle it.

For a direct one-device sanity test once the light IP is known:

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

Then try a non-annoying blue/green cue:

```bash
python - <<'PY'
import socket, json
ip = '192.168.x.y'
msg = {"method":"setPilot","params":{"state":True,"dimming":10,"r":0,"g":80,"b":255}}
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps(msg).encode(), (ip, 38899))
PY
```

## Matter / Home Assistant / ecosystem options

- Home Assistant has a built-in WiZ integration: <https://www.home-assistant.io/integrations/wiz/>
- Matter works through a third-party hub, but Matter may expose only generic light controls and not every WiZ dynamic effect.
- The WiZ app supports schedules, Music Sync, SpaceSense, scenes/effects, cloud control, and partner integrations.
- For Hermes, local UDP is probably faster and gives direct cue control without adding Home Assistant as a dependency.

## “Hackability” assessment

Software-level hackability: good.

- LAN UDP control is straightforward, unauthenticated on the local network, and supported by multiple open-source libraries.
- We can likely build a thin Hermes light-cue service with direct IP control, state restore, mode presets, and fallback no-op behavior.

Hardware-level hackability: possible but not the first move.

- If the strip/controller is a standard RGB or RGB+CCT strip, the controller could theoretically be replaced with an ESP32/WLED controller.
- But the stock WiZ controller already gives local control. Replacing it only makes sense if we need per-segment addressable effects, open firmware, or full offline behavior beyond what WiZ exposes.
- If this is not the RGBIC model, expect one color/effect across the whole strip, not per-pixel addressability. RGBIC variants can display multiple colors at once; normal RGB/RGBTW strips cannot.
- Do not attempt firmware flashing on the stock controller unless treating it as disposable; WiZ hardware/firmware is not as open as ESPHome/WLED devices.

## Hermes-specific ideas for next pass

1. Add a small WiZ backend behind the current light-cue abstraction.
   - Config: `lights.wiz.hosts`, default brightness, night brightness, timeout/restore policy.
   - Backend methods: `set_rgb`, `set_scene`, `pulse`, `restore_previous`, `off`.
2. On startup or first cue, call `getPilot` and cache prior state so cues can restore instead of leaving the strip changed.
3. Use mode presets:
   - `default`: waiting alarm / red answer cue at normal brightness.
   - `night`: soft blue/green, minimum brightness, no harsh white.
   - `dim-default`: same color logic as default but lower dimming.
   - `no-light`: backend disabled / no-op.
4. Prefer direct RGB for deterministic cues; use dynamic scenes only after testing scene IDs on this exact strip.
5. Add a tiny CLI probe script for Matt:
   - discover WiZ devices
   - dump `getPilot`, `getSystemConfig`, `getModelConfig`
   - send test cue
   - restore/off
6. Consider network stability:
   - Assign a DHCP reservation/static IP for the strip.
   - Retry UDP commands a few times; WiZ UDP can miss packets.
   - Keep LAN-only assumptions explicit.
7. Privacy/security note:
   - Because local UDP control has no real auth, only trusted LAN/VLAN devices should be able to reach the light.

## Sources checked

- User Walmart URL for product identity: `https://www.walmart.com/ip/WiZ-4-Meter-13-ft-Ambient-Full-Color-and-Tunable-White-Wi-Fi-LED-Light-Strip-Simply-Stick-to-Any-Surface-No-Hub-Required/1484867080`
- Official WiZ 4m product page: <https://www.wizconnected.com/en-gb/p/light-strip-led-strip-4m/8720169078246>
- Official WiZ strip overview: <https://www.wizconnected.com/en-us/explore-wiz/led-lightstrips>
- Official WiZ US lightstrip product listing: <https://www.wizconnected.com/en-us/products/lightstrips>
- Home Assistant WiZ integration: <https://www.home-assistant.io/integrations/wiz/>
- pywizlight: <https://github.com/sbidy/pywizlight>
- WiZ local-control GitLab README: <https://gitlab.com/wizlighting/wiz-local-control>
- Adafruit CircuitPython WiZ library docs: <https://docs.circuitpython.org/projects/wiz/en/latest/api.html>
