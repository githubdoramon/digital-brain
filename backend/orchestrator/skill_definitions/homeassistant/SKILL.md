---
name: homeassistant
description: Controls the user's home and office - smart plugs, lights, scenes, automations. Can be used whenever the user is asking to execute automations. Turn things on and off, change scenes, get camera streams/pictures
metadata: {"requires":{"bins":["bash"],"env":["HA_TOKEN"]},"primaryEnv":"HA_TOKEN"}
---

# Home Assistant

Control smart home devices via Home Assistant API.

## Setup

The following environment variables are already set, so you must use on your requests
- `HA_URL`: The home Assistant URL
- `HA_TOKEN`: Long-lived access token

## Quick Commands

### List areas
Great to try to match a user's request to a specific area
```bash
curl -s "$HA_URL/api/template" -H "Authorization: Bearer $HA_TOKEN"  -d '{"template": "{{ areas() | tojson }}"}'  | \
  jq -r '.[]'
```

### List all entities
```bash
curl -s "$HA_URL/api/states" -H "Authorization: Bearer $HA_TOKEN" | \
  jq -r '.[] | select(.entity_id) | .entity_id'
```

### List entities by domain
```bash
curl -s "$HA_URL/api/states" -H "Authorization: Bearer $HA_TOKEN" | \
  jq -r '.[] | select(.entity_id | startswith("<domain name>.")) | .entity_id'
```
Make sure to replace the text inside startswith with the rigth domain you are looking for

### List entities from a given area
If you know the correct area from the first command, you can list entities from this area (replacing the area name in the command)
```bash
curl -s "$HA_URL/api/template" -H "Authorization: Bearer $HA_TOKEN"  \
  -d '{"template": "{{ area_entities(\"<area name>\")| tojson }}"}' | \
  jq -r '.[]'
```

### Turn switches on/off
```bash
# Turn on
curl -s -X POST "$HA_URL/api/services/switch/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.<switch_entity_id>"}'

# Turn off
curl -s -X POST "$HA_URL/api/services/switch/turn_off" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "switch.<switch_entity_id>"}'
```


### Control lights
```bash
# Turn on with brightness
curl -s -X POST "$HA_URL/api/services/light/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "light.<light_entity_id>", "brightness_pct": 80}'
```

### Trigger scene
```bash
curl -s -X POST "$HA_URL/api/services/scene/turn_on" \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "scene.<scene_entity_id>"}'
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
- Switches can be the actual entity for many other domains, like lights or climate
- Do not guess entities, EVER. Always start by listing all entities and then check which is the most likely to be the one the user actually wants you to act on.
- Test entity IDs with the list command first