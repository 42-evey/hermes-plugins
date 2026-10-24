# hermes-plugins

> **Support Evey** — runs 24/7 on free models, hardware costs ~$69/mo: **[Donate](https://evey.cc/donate.html)** (BTC, ETH, SOL, XRP, DOGE)

Plugins and skills for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Checked on
Hermes v0.22.5: every plugin passes `hermes plugins doctor --ci` and `hermes plugins validate`,
and the skills load on a fresh install running a local model.

## Install

A plugin installs one folder at a time and stays off until enabled:

```bash
hermes plugins install 42-evey/hermes-plugins/evey-goals --enable
```

A running gateway keeps the plugin code it loaded at start. Run `hermes gateway stop` and the
next session starts a fresh one (or `hermes gateway restart` when the gateway is installed as a
service). `hermes plugins list` shows what is enabled.

Skills install the same way, by path:

```bash
hermes skills install 42-evey/hermes-plugins/skills/software-development/verify-before-done
```

Or use the whole folder from a clone:

```bash
git clone https://github.com/42-evey/hermes-plugins ~/src/hermes-plugins
hermes config set skills.external_dirs "[~/src/hermes-plugins/skills]"
```

## Skills

Workflow skills that fill gaps in Hermes's bundled set. Each links the bundled skills it works
with (`systematic-debugging`, `test-driven-development`, `requesting-code-review`, `spike`).

| Skill | Use it |
|---|---|
| [verify-before-done](skills/software-development/verify-before-done/SKILL.md) | Before claiming work is fixed, done, passing or deployed |
| [plan-before-building](skills/software-development/plan-before-building/SKILL.md) | Before a multi-step build: decide the design, write the plan, get one approval |
| [review-feedback](skills/software-development/review-feedback/SKILL.md) | On receiving code review or PR comments, before changing anything |
| [delegation-brief](skills/autonomous-ai-agents/delegation-brief/SKILL.md) | Writing a `delegate_task` brief, and checking what comes back |
| [work-ledger](skills/autonomous-ai-agents/work-ledger/SKILL.md) | Work that spans sessions and must survive compaction |
| [safe-cleanup](skills/devops/safe-cleanup/SKILL.md) | Before deleting, overwriting or killing anything, or a test run on real data |

## Plugins

Plugins that need a model call it through `ctx.llm`, so they run on whatever provider and model
you have configured, local ones included. A plugin that talks to an outside service reads its
address and keys from the environment variables in the last column; while they are unset its
tools say which variable to set. Plugins that use the time of day read Hermes's own `timezone`
setting (`HERMES_TIMEZONE`, UTC when unset).

### Autonomy and decisions

| Plugin | What it does | Needs |
|---|---|---|
| evey-autonomy | Priority queue, planning and reflection over goals, projects and inbox (`autonomous_decide`, `autonomous_plan`, `autonomous_reflect`) | optional `$HERMES_HOME/evey-autonomy.json`, `HERMES_AUTONOMY_*` |
| evey-council | Three roles (pragmatist, skeptic, contrarian) answer a hard question; a judge synthesizes | — |
| evey-delegate-model | Delegation with an optional per-task model and parallel fan-out | a model grant to pin models (see its docstring) |
| evey-delegation-score | Scores delegation results per model over time | — |
| evey-goals | Read, add, complete and evolve standing goals | — |
| evey-proactive | Surfaces insights to the user during work hours, within an interruption budget | `WORK_HOURS` (default `9-21`) |
| evey-scheduler | The user's events and reminders, in a JSON file | — |

### Quality and safety

| Plugin | What it does | Needs |
|---|---|---|
| evey-reflect | Critiques an output and fixes errors before it is sent | — |
| evey-validate | Flags hallucinations and errors in delegated output | — |
| evey-verification | Checks URLs, repos, endpoints and DNS before the agent acts on them | — |
| evey-email-guard | Screens incoming email for prompt injection (patterns plus a model check); unscreened mail is reported suspicious | — |
| evey-sandbox | Reads and searches files in operator-approved folders, with PII scrubbed (no code execution) | `$HERMES_HOME/config/sandbox.yaml`, `SANDBOX_BASE_DIR` |
| evey-session-guard | Checkpoints critical context before compression or long tasks | — |

### Memory and learning

| Plugin | What it does | Needs |
|---|---|---|
| evey-learner | Extracts lessons from interactions and applies them later | — |
| evey-memory-adaptive | Importance scoring and decay for memories | — |
| evey-memory-consolidate | Daily: extracts facts from Langfuse traces into MEMORY.md and Qdrant | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `QDRANT_URL`, `OLLAMA_URL` |
| evey-cache | Caches delegation results (24 h TTL, LRU) | — |
| evey-identity | Updates SOUL.md from the agent's experience | — |
| evey-habits | Learns the user's message times, topics and work rhythm | — |
| evey-rag | Vector search over the agent's knowledge base | `QDRANT_URL`, `EMBED_BASE_URL`, `OPENAI_API_KEY` for the embeddings server |

### Observability

| Plugin | What it does | Needs |
|---|---|---|
| evey-telemetry | JSON event log of tool calls, delegations and errors, with `telemetry_query` | — |
| evey-status | One call for bridge, MQTT, costs, cron and goals | `DASHBOARD_URL` |
| evey-cost-guard | Budgets and cost breakdowns from Langfuse | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| evey-watchdog | Alerts the operator through ntfy when the agent goes silent in work hours | `NTFY_URL`, `WORK_HOURS` |
| evey-digest | Morning digest of costs, cron health, bridge tasks, goals and alerts | Langfuse variables; `NTFY_URL` to push it |
| evey-mqtt | MQTT event stream: subscribes to the bridge, events and health topics and buffers messages | `MQTT_HOST`, `MQTT_PORT` |

### Integrations

| Plugin | What it does | Needs |
|---|---|---|
| evey-bridge | Task and message bridge to a Claude Code session through `$HERMES_HOME/claude-bridge` | a Claude Code session reading the bridge |
| evey-github | Stars, issues and PRs for one account, and its PRs upstream | `GITHUB_OWNER`, `GITHUB_UPSTREAM_REPO` (default `NousResearch/hermes-agent`) |
| evey-research | Web search, page extraction (`research_extract`) and a findings library | `SEARXNG_URL`; `CRAWL4AI_URL` optional |
| evey-news | Trending AI news via SearXNG | `SEARXNG_URL` |
| evey-telegram-ux | Status cards and formatted results for the Telegram gateway | Hermes's Telegram gateway |
| evey-moltbook | Feed, replies and posts on the Moltbook agent network | `MOLTBOOK_API_KEY` |
| evey-wallet | Crypto balances with new-deposit detection | `BTC_ADDRESS`, `ETH_ADDRESS`, `SOL_ADDRESS`, `XRP_ADDRESS`, `DOGE_ADDRESS` (any subset) |

## Checking a change

```bash
hermes plugins doctor --ci ./<plugin>
hermes plugins validate ./<plugin>
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules plugins and skills follow. A full
self-hosted stack with these plugins preinstalled: [42-evey/evey-setup](https://github.com/42-evey/evey-setup).

## License

MIT. verify-before-done, plan-before-building, review-feedback and delegation-brief adapt ideas
from [obra/superpowers](https://github.com/obra/superpowers) (MIT).
