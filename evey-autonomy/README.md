# Evey Autonomy plugin customization

The autonomy plugin now reads optional instance-specific config from:

- `$HERMES_AUTONOMY_CONFIG`, if set
- otherwise `$HERMES_HOME/evey-autonomy.json`

If no file exists, defaults are tuned for Brendan's Hermes instance:

```json
{
  "operator_name": "Brendan",
  "timezone": "America/Los_Angeles",
  "heavy_model": "claude-sonnet-4-6",
  "cheap_model": "claude-haiku-4-5",
  "bridge_peer_names": ["claude-code", "mother", "pr-agent", "copilot"],
  "projects": [
    {"name": "aries-app", "path": "/home/node/aries-app", "base_branch": "master", "importance": 9}
  ],
  "disabled_sources": []
}
```

## What changed from the original Evey-specific version

- Timezone is configurable; default is `America/Los_Angeles`, not hardcoded Berlin.
- Model recommendations are configurable; defaults use Hermes/Claude slugs instead of Evey/OpenClaw aliases.
- Routing recommends Hermes-native tools (`delegate_task`, `web_search`, `terminal`, `patch`, `send_message`, `claude_bridge_check`).
- Bridge language no longer assumes only “Mother”; peer names are configurable.
- Adds `projects` collection for local git repositories such as `/home/node/aries-app`.
- Planning includes Brendan/Hermes workflows like PR/project checks and Gmail maintenance.

## Install locally

```bash
cp -r evey-autonomy ~/.hermes/plugins/
# restart gateway or start a new Hermes session
```

## Disable noisy sources

```json
{
  "disabled_sources": ["time", "projects"]
}
```

Available sources: `bridge`, `goals`, `projects`, `memory`, `cron`, `time`.
