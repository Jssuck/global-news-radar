# Global News Radar

**A self-hosted, open-source (MIT) monitoring platform that watches mainstream media outlets worldwide, 24/7 — fetching news the moment it is published.**

[简体中文](README.zh-CN.md) | English

---

## What It Does

Global News Radar locks onto mainstream media in every country — wire services, national newspapers, public broadcasters — and monitors them around the clock. The moment a source publishes, the platform fetches the article, cleans it through a three-stage pipeline, and organizes it into a searchable, structured news stream.

- **Global source registry** — curated seed list of ~1,000–2,500 national-tier mainstream outlets across 30–50 languages, built from GDELT domain data × Media Cloud national lists × manual curation × community contributions
- **Near-real-time fetching** — WebSub push where available, conditional GET (ETag/Last-Modified) with adaptive per-source polling intervals; new articles land within minutes
- **Geo-block awareness** — when an outlet only allows its own country's IPs, the platform detects it (HTTP 451, geo-worded 403, region redirect, truncation) and shows a plain-text hint: *"This source only allows access from IPs in {country} — please add a {country} proxy."* You bring your own proxy; the system never silently reroutes
- **Three-stage data pipeline** — rule-based cleaning (extraction, language detection, dedup) → LLM cleaning (schema-validated correction) → LLM organization (summary, translation, NER, topic tags, cross-source event merging)
- **Full-stack app** — news stream dashboard, source management, proxy configuration, user authentication with admin registration approval, and a REST API

## Why Another News Aggregator?

Existing open-source tools each solve a slice: RSS readers only read feeds, change detectors only detect changes. None of them close the loop of *global source coverage + near-real-time monitoring + article-level structured cleaning + geo-block handling* — and most of the big ones (RSSHub, FreshRSS, Firecrawl core) are AGPL/GPL, which limits reuse. Global News Radar is MIT from day one and reuses the permissively licensed best-of-breed components (trafilatura, newspaper4k, feedparser) instead of reinventing them.

> **Compliance red line**: the platform never redistributes full article text. It serves titles, very short extracts, and links back to the original publisher; robots.txt is always respected.

## Architecture at a Glance

```
Source Registry → Scheduler (adaptive, per-source) → Fetch Workers (HTTP + headless fallback)
      │                      │
      ▼                      ▼
Geo-block Detector      Message Queue
      │                      │
      ▼                      ▼
User Hint ("add a {country} proxy")   Processing Pipeline
                              Rule Cleaning → LLM Cleaning → LLM Organization
                                      │
                                      ▼
              PostgreSQL + pgvector · Object Storage · Search Engine
                                      │
                                      ▼
                        REST API ← Auth & Approval → Web Dashboard
```

Full design: [docs/design/design-v1.0.md](docs/design/design-v1.0.md)

## Tech Stack

| Layer | Choice | License |
|---|---|---|
| Frontend | Next.js + TypeScript + Tailwind CSS + shadcn/ui | MIT |
| Backend & crawler | FastAPI (Python) + httpx + Playwright | MIT / Apache-2.0 |
| Extraction | trafilatura (primary) + newspaper4k/readability (fallback) | Apache-2.0 / MIT |
| Queue | Celery + RabbitMQ (broker, network service) | BSD / MPL-2.0 |
| Storage | PostgreSQL + pgvector · Meilisearch · MinIO/S3 · Valkey | PostgreSQL / Apache-2.0 / MIT / AGPL(service) / BSD |
| LLM | Pluggable provider: local open models (Qwen etc.) by default, OpenAI-compatible API optional | — |
| Auth | Better Auth + Casbin RBAC, Mastodon-style registration approval | MIT / Apache-2.0 |
| Deploy | Docker Compose, `.env` one-command self-hosting | — |

## Project Status

**Incubating — M1 done, M2/M3 features landed, gates pending.** The v1.0 design is finalized and the crawling core is on `main`: 443 seed sources (60+ countries, 40+ languages), RSS→sitemap discovery cascade, robots.txt compliance, per-host politeness, adaptive polling, and the trafilatura fallback chain — see the [M1 gate report](docs/harness/gate-reports/m1-gate-report.md) (PASS).

Since v0.1.0 the single-process app has gained: three-level proxy bindings (source > country > global) injected into the fetch path, geo medium signals (redirect-page / body-truncation) with alt-country egress contrast confirmation, GDELT aggregate degraded mode for confirmed geo-restricted sources, a pluggable LLM cleaning stage (OpenAI-compatible provider, CleanedArticle schema gate + deterministic grounding checks, one-shot retry → DLQ, per-call token accounting), two-pass KNN+UnionFind event clustering with an organized-event LLM stage, an optional Playwright render fallback for anti-bot sources under a daily budget, session/API-key auth with a pending→approved|rejected registration state machine, role-based access (admin/editor/viewer), token-bucket rate limiting, 35 REST endpoints including an SSE event stream, and three server-rendered UIs (news feed, source board, admin console). Formal M2/M3 gate measurement is still pending.

### Roadmap

| Milestone | Scope | Target |
|---|---|---|
| M0 Bootstrap | repo, governance, CI, harness docs | — (done) |
| M1 Crawling Core | seed registry, scheduler, RSS/sitemap fetching, rule cleaning | ✅ done — 440 sources onboarded, extraction success 95.8% |
| M2 Geo & LLM Pipeline | geo-block detection + proxy hints, LLM cleaning & organization | 🚧 implemented — golden-set geo accuracy + 72h metrics pending |
| M3 Full-stack App | dashboard, auth + approval, REST API v1 | 🚧 35 endpoints live (incl. SSE); contract tests pending |
| M4 Hardening & v1.0 | observability, docs, release | v1.0.0 tagged |

Acceptance criteria and gate reviews are enforced per [docs/harness/](docs/harness/).

## The Harness (How This Project Stays on Track)

This repo ships a complete project-level harness — the engineering constitution that governs progress, quality, and releases:

- [00 · Overview](docs/harness/00-overview.md) — roles, pillars, how the pieces connect
- [01 · Lifecycle](docs/harness/01-lifecycle.md) — project/version/milestone/security lifecycles
- [02 · Progress Control](docs/harness/02-progress-control.md) — biweekly iterations, gate reviews, KPIs, escalation
- [03 · Acceptance Criteria](docs/harness/03-acceptance-criteria.md) — quantified M0–M4 gates and Definition of Done
- [04 · Code Quality](docs/harness/04-code-quality.md) — lint/test/review rubric, CI gates, license rules
- [05 · Enforcement](docs/harness/05-harness-enforcement.md) — GitHub Actions workflows, `gate_check.py` contract

## Project Skills

Reusable agent skills encoding this project's operational knowledge, usable both by humans-as-runbooks and by AI agents:

| Skill | Purpose |
|---|---|
| [`skills/news-source-onboarding`](skills/news-source-onboarding) | 5-step procedure to add/audit a media source (metadata schema, 4-level discovery cascade, acceptance bar) + `validate_source.py` |
| [`skills/geo-block-triage`](skills/geo-block-triage) | Signal scoring for geo-block vs anti-bot vs legal-block, user hint templates, proxy binding rules + `geo_verdict.py` |
| [`skills/cleaning-pipeline-qc`](skills/cleaning-pipeline-qc) | Quality gates & weekly sampling for the three-stage pipeline, golden-set regression, DLQ handling + `qc_sample.py` |
| [`skills/milestone-gate-review`](skills/milestone-gate-review) | Gate review procedure, evidence rules, verdict grading + `gate_check.py` |

## Quick Start (MVP)

A minimal end-to-end prototype lives on the [`mvp` branch](https://github.com/Jssuck/global-news-radar/tree/mvp): seed sources → RSS fetch → trafilatura extraction → dedup → geo-block hint → FastAPI endpoints → simple dashboard.

```bash
git clone -b mvp https://github.com/Jssuck/global-news-radar.git
cd global-news-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://127.0.0.1:8000 (news feed) · /sources (source board) · /docs (OpenAPI)
```

Optional: copy `.env.example` to `.env` to tune polling interval etc. Run the offline test suite with `pip install -r requirements-dev.txt && python -m pytest tests -q`. MVP scope, simplifications, and gaps vs the v1.0 design: [docs/mvp/mvp-spec.md](docs/mvp/mvp-spec.md).

## Contributing

We welcome source additions (the easiest first contribution — one YAML file per outlet), parsers, translations, and core code. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [harness docs](docs/harness/) first; every PR must pass the code-quality gates. Media sources are community-maintained under a source-maintainer responsibility model, inspired by RSSHub's route contributors.

## License

[MIT](LICENSE) © Global News Radar contributors. The license covers code only — fetched news content belongs to its publishers; this platform never redistributes full articles.
