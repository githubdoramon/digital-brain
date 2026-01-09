---
name: homeassistant
description: Controls the user's home and office - smart plugs, lights, scenes, automations. Can be used whenever the user is asking to execute automations. Turn things on and off, change scenes, get camera streams/pictures
metadata: {"requires":{"env":["HA_TOKEN","HA_URL"]},"primaryEnv":"HA_TOKEN"}
---

# Home Assistant

Control smart home (or office) devices via Home Assistant's MCP (Model Context Protocol) integration.

## How to Use

Use the `home_assistant` tool to interact with Home Assistant. This tool connects to HA's MCP server which exposes the Assist API.

### Step 1: Discover Available Tools

First, ALWAYS list what tools are available from Home Assistant. DO NOT GUESS TOOL NAMES.

```
home_assistant(action="list_tools")
```

This returns the MCP tools exposed by HA, which typically include tools for:
- Controlling devices (lights, switches, climate, covers)
- Running automations and scripts
- Getting entity states
- Calling services

### Step 2: Call a Tool

Once you know what tools are available, call them:

```
home_assistant(action="call_tool", tool_name="<tool_name>", arguments={...})
```

The available tools and their arguments depend on what Home Assistant exposes via its MCP server configuration.

## Critical Rules

1. **NEVER guess entity IDs** - Always discover entities first using the available MCP tools
2. **Check tool availability** - Use `list_tools` first to see what's available
3. **Use exact entity IDs** - Entity IDs are case-sensitive and must match exactly

## Common Entity Domains

When working with entities, they follow the pattern `domain.entity_name`:

- `switch.*` - Smart plugs, generic switches
- `light.*` - Lights (Hue, LIFX, etc.)
- `scene.*` - Pre-configured scenes
- `automation.*` - Automations
- `script.*` - Scripts
- `climate.*` - Thermostats, HVAC
- `cover.*` - Blinds, garage doors
- `media_player.*` - TVs, speakers
- `sensor.*` - Temperature, humidity, motion sensors
- `binary_sensor.*` - On/off sensors (doors, windows)
- `fan.*` - Fans
- `lock.*` - Smart locks

## Typical Workflow

1. User asks to control something (e.g., "turn on the living room lights")
2. Call `home_assistant(action="list_tools")` to see available tools
3. Use the appropriate tool to list/find entities matching the user's request
4. Call the control tool with the correct entity ID
5. Confirm the action to the user

## Notes

- The MCP server exposes tools based on what's configured in Home Assistant's "Exposed Entities" settings
- If a tool isn't available, the user may need to expose more entities in HA's settings
- Authentication is handled automatically via the HA_TOKEN environment variable
