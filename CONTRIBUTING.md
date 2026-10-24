# Contributing

Pull requests are welcome. Keep each one to a single plugin or skill.

## Plugins

- One plugin per top-level folder: `plugin.yaml` plus `__init__.py` with `register(ctx)`.
- Self-contained. Hermes installs a plugin by copying only its folder
  (`hermes plugins install 42-evey/hermes-plugins/<plugin>`), so never import from a sibling
  folder or the repo root.
- Model calls go through `ctx.llm.complete(...)`, which uses the provider and model the user
  has configured. Do not hard-code model ids, API keys or proxy URLs.
- External services (MQTT, a vector store, a push server, an API) are read from environment
  variables with no deployment-specific default. When one is unset, the tool returns a line
  naming the variable to set instead of failing.
- `provides_tools` in `plugin.yaml` lists exactly the tools `register()` adds.
- Both checks pass, run from the repo root:

  ```bash
  hermes plugins doctor --ci ./<plugin>
  hermes plugins validate ./<plugin>
  ```

## Skills

- Layout: `skills/<category>/<name>/SKILL.md`, with `name` equal to the folder name.
- `description` is at most 60 characters: the skill index in the system prompt cuts it there,
  and it is what the model reads when choosing a skill.
- Generic: no names of a particular deployment, person, host or service.
- Every line is an instruction or a fact the agent acts on. Detail needed only sometimes goes
  in `references/` and is linked from `SKILL.md`.
- Check that Hermes lists it: point `skills.external_dirs` at your clone's `skills/` folder,
  then run `hermes skills list`.

## Reporting issues

Open a GitHub issue with the Hermes version (`hermes --version`), the plugin or skill, what you
ran, and the output.
