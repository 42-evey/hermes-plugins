# evey-autonomy configuration

The autonomy plugin reads optional instance-specific config from:

- `$HERMES_AUTONOMY_CONFIG`, if set
- otherwise `$HERMES_HOME/evey-autonomy.json`

With no config file it runs on neutral defaults (operator `operator`, timezone
`UTC`, no tracked projects). Override any field via the config file or the
matching environment variable.

```json
{
  "operator_name": "operator",
  "timezone": "UTC",
  "heavy_model": "claude-sonnet-4-6",
  "cheap_model": "claude-haiku-4-5",
  "bridge_peer_names": ["claude-code", "pr-agent"],
  "projects": [
    {"name": "my-app", "path": "/path/to/my-app", "base_branch": "main", "importance": 8}
  ],
  "disabled_sources": []
}
```

## Environment overrides

These win over the config file:

- `HERMES_OPERATOR_NAME`
- `HERMES_TIMEZONE`
- `HERMES_AUTONOMY_HEAVY_MODEL`
- `HERMES_AUTONOMY_CHEAP_MODEL`
- `HERMES_AUTONOMY_CONFIG` (path to the config file)

## Project signals

Add local git repositories to `projects` and the decide loop surfaces
uncommitted changes, non-default branches, and unpushed commits as low-cost
signals. Each entry takes `name`, `path`, `base_branch`, and `importance`.

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
