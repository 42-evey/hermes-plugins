# Evey's Hermes Plugins

> **Support Evey** — runs 24/7 on free models, hardware costs ~$69/mo: **[Donate](https://evey.cc/donate.html)** (BTC, ETH, SOL, XRP, DOGE)


23 custom plugins for [hermes-agent](https://github.com/NousResearch/hermes-agent) — built for autonomous operation, observability, cost control, and self-improvement.

## Install

```bash
git clone https://github.com/42-evey/hermes-plugins.git
cp -r hermes-plugins/evey-* ~/.hermes/plugins/
cp hermes-plugins/evey_utils.py ~/.hermes/plugins/
# Restart hermes-agent
```

## Plugins

### Autonomy & Decision-Making
| Plugin | What It Does |
|--------|-------------|
| **evey-autonomy** | Core autonomy engine: autonomous_decide, autonomous_plan, autonomous_reflect tools. The brain of self-directed behavior. |
| **evey-council** | 3-model debate for hard decisions. Multiple models argue, then consensus is extracted. |
| **evey-delegate-model** | Smart model routing with auto task-type detection, 3-retry per model, 4-model fallback chain. Sensitivity filter keeps private data local. |

### Observability & Telemetry
| Plugin | What It Does |
|--------|-------------|
| **evey-telemetry** | Structured JSON logging of every tool call, delegation, and error. Auto-rotating event log. `telemetry_query` tool for self-inspection. |
| **evey-status** | Unified status check — ONE tool call returns bridge + MQTT + costs + cron + goals + time context. |
| **evey-mqtt** | Auto-connecting MQTT real-time event stream. Subscribes to bridge/events/health/mother topics on plugin load. |
| **evey-cost-guard** | Budget enforcement via Langfuse. Tracks daily/per-task costs. Warns at 80%, alerts at 100%. |

### Quality & Safety
| Plugin | What It Does |
|--------|-------------|
| **evey-reflect** | Self-correction loop. Critiques outputs with a cheap model before sending. |
| **evey-validate** | Hallucination detection. Regex patterns + LLM scoring (0-10). |
| **evey-email-guard** | Prompt injection screening. Dual-layer: 20+ regex patterns + local AI classifier. |
| **evey-sandbox** | Sandboxed code execution with resource limits. |
| **evey-session-guard** | Session lifecycle management and cleanup. |

### Learning & Memory
| Plugin | What It Does |
|--------|-------------|
| **evey-learner** | Experiential learning — learns from interactions, applies learnings next time. |
| **evey-memory-adaptive** | Importance scoring + decay for memories. High-value memories persist, low-value fade. |
| **evey-memory-consolidate** | Nightly memory consolidation — extracts facts into MEMORY.md + Qdrant vectors. |
| **evey-cache** | Delegation caching — avoid re-asking the same questions to models. |
| **evey-identity** | Self-updating SOUL.md — evolves personality based on interactions. |

### Communication & Integration
| Plugin | What It Does |
|--------|-------------|
| **evey-bridge** | Bidirectional communication with Claude Code. File-based inbox/outbox + MCP server. |
| **evey-goals** | Autonomous goal management. Set, track, complete, review goals. |
| **evey-telegram-ux** | Telegram UX improvements — message formatting, interruption management. |
| **evey-research** | Automated research pipeline with web search and note-taking. |
| **evey-digest** | Daily digest compilation from multiple data sources. |
| **evey-delegation-score** | Score and rank delegation results for quality tracking. |

### Shared
| File | What It Does |
|------|-------------|
| **evey_utils.py** | Shared retry logic (call_llm with exponential backoff), HTTP helpers. |

## Stack

These plugins are part of Evey's autonomous AI agent stack:
- Brain: MiMo-V2-Pro (1T params, FREE via OpenClaw)
- Free models via LiteLLM (OpenRouter + Ollama + Nous Portal)
- 18 Docker services, all monitored
- $0/day operating cost

## License

MIT