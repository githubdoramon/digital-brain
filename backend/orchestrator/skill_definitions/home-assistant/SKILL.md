---
name: homeassistant
description: Controls the user's home and office - smart plugs, lights, scenes, automations. Can be used whenever the user is asking to execute automations. Turn things on and off, change scenes, get camera streams/pictures
homepage: https://www.home-assistant.io/
metadata: {"requires":{"bins":["bash"],"env":["HA_TOKEN"]},"primaryEnv":"HA_TOKEN"}
---

# Home Assistant

Control smart home devices via Home Assistant API.

## Setup

The following environment variables will be set
- `HA_URL`: The home Assistant URL
- `HA_TOKEN`: Long-lived access token

## Quick Commands

### List entities by domain
```bash
curl -s "$HA_URL/api/states" -H "Authorization: Bearer $HA_TOKEN" | \
  jq -r '.[] | select(.entity_id | startswith("switch.")) | .entity_id'
```

### Turn on/off
```bash
# Turn on
curl -s -X POST "$HA_URL/api/services/switch/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.office_lamp"}'

# Turn off
curl -s -X POST "$HA_URL/api/services/switch/turn_off" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.office_lamp"}'
```

### Control lights
```bash
# Turn on with brightness
curl -s -X POST "$HA_URL/api/services/light/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "light.living_room", "brightness_pct": 80}'
```

### Trigger scene
```bash
curl -s -X POST "$HA_URL/api/services/scene/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "scene.movie_time"}'
```

### Call any service
```bash
curl -s -X POST "$HA_URL/api/services/{domain}/{service}" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "...", ...}'
```

### Get entity state
```bash
curl -s "$HA_URL/api/states/{entity_id}" -H "Authorization: Bearer $HA_TOKEN"
```

## Entity Domains

- `switch.*` — Smart plugs, generic switches
- `light.*` — Lights (Hue, LIFX, etc.)
- `scene.*` — Pre-configured scenes
- `automation.*` — Automations
- `climate.*` — Thermostats
- `cover.*` — Blinds, garage doors
- `media_player.*` — TVs, speakers
- `sensor.*` — Temperature, humidity, etc.

## Notes

- API returns JSON by default
- Long-lived tokens don't expire — store securely
- List entities to find what is the right one the user is asking to be changed
- Switches can be the actual entity for many other domains, like lights or climate
- Test entity IDs with the list command first

