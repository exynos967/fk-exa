# API Key Generator - Multi-Service Edition

[中文说明](./README.md)

This repository is the multi-service registration toolkit and aggregation API
upstream for:

- registering `Firecrawl` / `Exa` keys
- validating that the keys actually work before feeding a unified search layer

## Current Status

- `Firecrawl`: available
- `Exa`: available

It is not the main product repository for `MySearch`.  
If you want the public-facing unified search console, MCP, Skill, and Social / X
integration, use the standalone project:

- [MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)

## What this repo is for

- real local browser-based registration
- email API based verification link / code retrieval
- immediate live API validation after a key is extracted
- optional upload into unified proxy pools
- optional use as one upstream provider source for `MySearch` / `MySearch-Proxy`

## Features

- Multi-service launcher: choose Firecrawl / Exa at startup
- Automatic environment bootstrap: checks `venv`, dependencies, and browsers
- Unified mail layer: supports Cloudflare Mail API, DuckMail, and Cloud Mail
- Multi-domain support: choose the active domain at runtime
- Concurrent registration: batch and parallel runs are supported
- Background browser mode: headless by default, visible mode when debugging
- Real usability verification: validates the key against the official API
- Automatic upload to proxy pools with a per-service marker
- A clean upstream for unified search gateways such as `MySearch-Proxy`
- Cross-platform startup: Windows, macOS, and Linux

## Paired Console Preview

### MySearch Proxy Hero

![MySearch Console Hero](./docs/screenshots/mysearch-console-hero.jpg)

### MySearch Proxy Workspaces

![MySearch Console Workspaces](./docs/screenshots/mysearch-console-workspaces.jpg)

## Quick Start

### 1. Clone

```bash
git clone https://github.com/skernelx/tavily-key-generator.git
cd tavily-key-generator
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in your mail settings and optional upload settings.

If `SERVER_URL` and `SERVER_ADMIN_PASSWORD` are configured, successful registrations can be uploaded automatically.

### 3. Run

macOS / Linux:

```bash
python3 run.py
```

or:

```bash
./start_auto.sh
```

Windows:

```bat
start_auto.bat
```

Current launcher options:

```text
1. Firecrawl
2. Exa
```

Notes:

- `Exa` accounts are saved to `exa_accounts.txt`
- the saved format is `email,EMAIL_OTP_ONLY,api_key`

## Recommended Pairing

If you want to turn the generated keys into a public, reusable unified search
service, the recommended deployment target is:

- [MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)

That project provides:

- a unified console
- Firecrawl / Social / X routing
- MCP + Skill installation docs
- official API and compatible gateway support
- final integration guidance for Codex / Claude Code

This repository stays focused on:

- producing working keys
- verifying them with live requests
- serving as the upstream aggregation source

The local `proxy/` directory now keeps only a migration README and no longer
ships the runnable implementation.

## Relationship to MySearch

This repo only handles:

- Firecrawl / Exa key registration
- live key validation
- optional aggregation API serving for `MySearch` / `MySearch-Proxy`

All `MySearch` installation, MCP usage, unified console, social gateway, and
Codex / Claude Code integration docs now live in:

- [MySearch-Proxy](https://github.com/skernelx/MySearch-Proxy)
