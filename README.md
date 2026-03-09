<div align="center">

```
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        TCC — TERMINAL COMMAND CENTER                         ║
║        Codename: JARVIS  v2.0                                ║
║                                                              ║
║   Control every device you own — from one terminal.         ║
║   Phone · Laptop · Desktop · Server · All at once.          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS%20%7C%20Android-lightgrey?style=flat-square)
![Cost](https://img.shields.io/badge/Cost-%240%20%2F%20month-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-success?style=flat-square)

**Fully offline. Zero cloud. Zero cost. Runs on your own hardware forever.**

</div>

---

## What Is This?

TCC (Terminal Command Center) is a **self-hosted, offline-first terminal engine** that lets you control all your personal devices — Android phone, Windows/Linux/macOS laptop, remote server — from a **single terminal session**, from anywhere in the world.

No cloud. No subscriptions. No API keys. No GUI required. Just your terminal and your devices.

```
You type:    phone screenshot
JARVIS:  ✓ Screenshot saved → ./screenshots/phone_20260309_120533.png  [118ms]

You type:    good morning
JARVIS:  ✓ Running skill: morning
           → browser opened
           → phone volume set to 10
           → system stats displayed
           → battery: 74%
           → all notify "Good morning. System is ready."
         ✓ Skill complete  [1.4s total]
```

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [Device Setup](#device-setup)
- [Skills & Automation](#skills--automation)
- [Natural Language (LLM)](#natural-language-llm)
- [Remote Access via Tailscale](#remote-access-via-tailscale)
- [Running a Device Agent](#running-a-device-agent)
- [Configuration Reference](#configuration-reference)
- [Security Model](#security-model)
- [Roadmap](#roadmap)

---

## Features

| Feature | Status | Notes |
|---|---|---|
| Interactive REPL shell | ✅ Live | prompt_toolkit, command history, auto-suggest |
| Local system control | ✅ Live | info, screenshot, open apps, run commands, lock |
| Android phone control | ✅ Live | ADB — screenshot, battery, launch, volume, push/pull |
| Remote device agents | ✅ Live | HTTP agents on Windows / Linux / macOS / Android |
| Device discovery | ✅ Live | Config registry + HTTP health check + Tailscale |
| Structured logging | ✅ Live | Rotating daily logs, latency tracking, filters |
| Skills / automation | ✅ Live | YAML multi-step workflows (morning, focus, backup…) |
| Cron scheduler | ✅ Live | Run skills on a schedule (07:00 daily, weekdays…) |
| Natural language input | ✅ Ready | Ollama (local LLM, offline) — enable in config |
| Pattern-match fallback | ✅ Live | Works offline — `open chrome`, `battery`, `lock` |
| Broadcast commands | ✅ Live | `all notify "message"` — hits every device |
| Tailscale remote access | ✅ Ready | Global mesh VPN, free tier, no port forwarding |
| Windows agent | ✅ Live | PowerShell backend, mss screenshot |
| Linux / macOS agent | ✅ Live | Shell backend, psutil, scrot/mss screenshot |
| Android Termux agent | ✅ Live | Runs on-device without ADB |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER TERMINAL                                │
│            (Windows · Linux · macOS · Termux)                       │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  raw text
                          ▼
              ┌───────────────────────┐
              │    COMMAND PARSER     │  tokenize → Intent object
              │    src/parser.py      │  target · action · args · flags
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │  PATTERN MATCH / NLP  │  bare verbs → system target
              │  (offline fallback)   │  LLM (Ollama) if enabled
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │   COMMAND ROUTER      │  target → IP → transport
              │   src/router.py       │
              └──────┬────────┬───────┘
                     │        │
         ┌───────────▼─┐  ┌───▼────────────────────────┐
         │   LOCAL      │  │    NETWORK LAYER            │
         │   EXECUTOR   │  │    HTTP REST / ADB          │
         │  (system)    │  └───────────────┬─────────────┘
         └──────┬───────┘                  │
                │                 ┌────────▼────────────────┐
                │                 │   DEVICE AGENT          │
                │                 │   agent/agent.py        │
                │                 │   (runs on each device) │
                │                 └────────┬────────────────┘
                │                          │
                │                 ┌────────▼────────────────┐
                │                 │   OS HANDLER            │
                │                 │   handlers/windows.py   │
                │                 │   handlers/linux.py     │
                │                 │   handlers/android.py   │
                │                 └────────┬────────────────┘
                │                          │
                └──────────────────────────┤
                                           ▼
                              ┌────────────────────────┐
                              │   JSON RESPONSE         │
                              │   back to terminal      │
                              └────────────────────────┘
```

### Optional Intelligence Layer

```
                  ┌────────────────────────────────┐
                  │    LOCAL LLM (Ollama)           │
                  │    Mistral 7B / LLaMA 3 8B      │
                  │    Runs offline · Free · Fast   │
                  │                                 │
                  │  "open youtube on my phone"     │
                  │         ↓                       │
                  │  { target: "phone",             │
                  │    action: "launch",            │
                  │    args:  { app: "youtube" } }  │
                  └────────────────────────────────┘
```

### Command Flow (step by step)

```
1. User types:    phone volume 8
                      │
2. Parser:        Intent { target="phone", action="volume", args={level:"8"} }
                      │
3. Router:        Looks up "phone" in config.toml → IP 100.98.44.7 port 7070
                      │
4. HTTP POST:     http://100.98.44.7:7070/command
                  Headers: Authorization: Bearer <token>
                  Body:    { action: "volume", args: { level: "8" } }
                      │
5. Agent:         Checks token ✓  →  Checks permissions ✓  →  Calls handler
                      │
6. Handler:       adb shell media volume --stream 3 --set 8
                      │
7. Response:      { status: "success", message: "Volume: 8/15", latency_ms: 55 }
                      │
8. Terminal:      ✓ Volume set to 8/15  [55ms]
                      │
9. Logger:        [2026-03-09 12:05:55] INFO  phone volume 8   success  55ms
```

---

## Project Structure

```
tcc/
│
├── main.py                     ← Entry point  (python main.py)
├── config.toml                 ← Your devices, tokens, LLM settings
├── requirements.txt            ← pip dependencies
├── pyproject.toml              ← Package metadata
├── install.bat                 ← Windows one-click install
│
├── src/                        ← Core engine
│   ├── cli.py                  ← Interactive REPL shell
│   ├── parser.py               ← Command grammar tokenizer
│   ├── router.py               ← Target → device → HTTP dispatcher
│   ├── executor.py             ← Local 'system' command runner
│   ├── discovery.py            ← Device discovery (config + ping + Tailscale)
│   ├── llm.py                  ← Ollama natural language adapter
│   ├── scheduler.py            ← Cron-style skill scheduler
│   └── logger.py               ← Structured logging with rotation
│
├── modules/                    ← Device-specific command modules
│   ├── system.py               ← Local PC: info, screenshot, open, run, lock…
│   ├── phone.py                ← Android ADB: screenshot, battery, launch…
│   ├── files.py                ← File operations: ls, copy, move, delete
│   ├── network.py              ← Ping, Tailscale status, port check
│   └── notify.py               ← Cross-platform desktop notifications
│
├── agent/                      ← Remote device agent (deploy on each device)
│   ├── agent.py                ← Flask HTTP server (port 7070)
│   ├── agent.toml              ← Per-device config: name, token, permissions
│   └── handlers/
│       ├── windows.py          ← Windows PowerShell backend
│       ├── linux.py            ← Linux/macOS shell backend
│       └── android.py          ← Android Termux backend
│
├── skills/                     ← YAML automation workflows
│   ├── morning.yaml            ← "good morning" → browser + volume + stats
│   ├── shutdown.yaml           ← "good night" → lock + notify
│   ├── focus.yaml              ← "focus mode" → silence + lock phone
│   └── backup.yaml             ← "backup" → pull DCIM from phone
│
├── screenshots/                ← Screenshot output directory
└── logs/
    └── tcc.log                 ← Rotating log (daily, 7-day retention)
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Windows / Linux / macOS
- (Optional) Android Platform Tools (`adb`) for phone control
- (Optional) Tailscale for remote access

### 2. Install

```bash
git clone https://github.com/gintama1018/GINTAMA-BOT.git
cd GINTAMA-BOT

pip install -r requirements.txt
```

Or on Windows, double-click **`install.bat`**.

### 3. Configure

Edit **`config.toml`** to register your devices:

```toml
[devices.phone]
name       = "phone"
type       = "android"
ip         = ""           # leave empty → uses ADB (USB/Wi-Fi)
port       = 7070
auth_token = "your-secret-token"
transport  = "adb"

[devices.laptop]
name       = "laptop"
type       = "windows"
ip         = "192.168.1.50"   # LAN IP or Tailscale IP
port       = 7070
auth_token = "your-secret-token"
transport  = "lan"
```

### 4. Launch

```bash
python main.py
```

You'll see:

```
╭──────────────────────────────────────────────────────╮
│  TCC — Terminal Command Center                       │
│  Codename: JARVIS  v2.0                              │
│                                                      │
│  1 device(s) online  │  Tailscale: OFF  │  LLM: OFF  │
│                                                      │
│  Type help for commands, devices to list devices.   │
╰──────────────────────────────────────────────────────╯
>
```

---

## Command Reference

### Targets

| Target | What it controls |
|---|---|
| `system` | The local machine running TCC |
| `phone` | Android device (ADB or agent) |
| `laptop` | Secondary computer (HTTP agent) |
| `server` | Remote headless machine |
| `all` | Broadcast to every registered device |

> **Shorthand**: If you skip the target and use a known verb, TCC assumes `system`.
> `open chrome` = `system open chrome` · `screenshot` = `system screenshot`

### Actions

```
COMMAND                          DESCRIPTION
─────────────────────────────────────────────────────────────────
<target> info                    CPU, RAM, disk, hostname
<target> screenshot              Capture screen → saves to ./screenshots/
<target> battery                 Battery % and charging status
<target> launch / open <app>     Open an application by name
<target> lock                    Lock the screen
<target> volume <0-15>           Set media volume (Android / system)
<target> brightness <0-255>      Set screen brightness (Android)
<target> push <src> <dst>        Transfer file TO device
<target> pull <src> <dst>        Retrieve file FROM device
<target> ls <path>               List directory contents
<target> run "<command>"         Execute raw shell command
<target> notify <message>        Send notification to device
<target> reboot                  Restart device
<target> shutdown                Power off device
```

### Special Commands

```
devices                  List all registered devices + online status
devices --refresh        Force re-scan
logs                     Show last 50 log entries
logs --last 20           Show last N entries
logs --level ERROR       Filter by level (DEBUG/INFO/WARNING/ERROR)
logs --device phone      Filter by device name
skills                   List all available automation skills
help                     Show full command reference
clear                    Clear screen
exit / quit              Close TCC
```

### Live Examples

```bash
# Phone control
phone screenshot
phone battery
phone volume 8
phone brightness 200
phone launch youtube
phone push ./report.pdf /sdcard/Documents/
phone pull /sdcard/DCIM/photo.jpg ./downloads/
phone lock
phone notify "Meeting in 5 minutes"

# Local system
system info
system open chrome
system open notepad
system screenshot
system battery
system run "ipconfig"
system ls C:\Users

# Remote laptop
laptop info
laptop screenshot
laptop run "df -h"

# Broadcast
all notify "Dinner is ready"

# Shorthand (no target needed — assumes 'system')
open chrome
screenshot
battery
info
lock
notify "Hello"
volume 8

# Skills
good morning
focus mode
good night
backup

# Discovery & logs
devices
devices --refresh
logs --last 10
logs --level ERROR
```

---

## Device Setup

### Android Phone (ADB Mode — Recommended)

No app install needed on the phone.

**Step 1 — Enable Developer Options on your phone:**
- Settings → About Phone → tap **Build Number** 7 times
- Go back → Developer Options → enable **USB Debugging**

**Step 2 — Install ADB on your PC:**
```bash
# Windows (via winget)
winget install Google.PlatformTools

# Or download from:
# https://developer.android.com/tools/releases/platform-tools
```

**Step 3 — Connect:**
```bash
# USB:
adb devices       # should show your device

# Wi-Fi (Android 11+):
adb pair <phone-ip>:<pair-port>        # from Developer Options → Wireless debugging
adb connect <phone-ip>:5555
```

**Step 4 — Configure TCC:**
```toml
[devices.phone]
type      = "android"
ip        = ""         # empty = ADB mode
transport = "adb"
```

### Windows / Linux / macOS Remote Device (Agent Mode)

Deploy the agent on any remote machine:

```bash
# On the remote machine:
git clone https://github.com/gintama1018/GINTAMA-BOT.git
cd GINTAMA-BOT
pip install flask psutil mss

# Edit agent/agent.toml — set your token and device name
python main.py --agent
```

The agent starts a local HTTP server on port 7070. Then in your TCC `config.toml`:

```toml
[devices.laptop]
type       = "windows"
ip         = "192.168.1.50"
port       = 7070
auth_token = "same-token-as-agent.toml"
transport  = "lan"
```

---

## Skills & Automation

Skills are **YAML-defined multi-step workflows** triggered by a phrase.

### Example — Morning Routine

```yaml
# skills/morning.yaml
name: morning
trigger:
  - "good morning"
  - "morning routine"
  - "start my day"
steps:
  - system open chrome
  - system info
  - phone battery
  - phone volume 10
  - all notify "Good morning. System is ready."
```

Type `good morning` → all 5 steps run in sequence.

### Built-in Skills

| Skill | Triggers | What it does |
|---|---|---|
| `morning` | `good morning`, `morning routine`, `start my day` | Open browser · system stats · battery · volume up · notify all |
| `shutdown` | `good night`, `end of day`, `wrap up` | Notify · lock phone · lock system |
| `focus` | `focus mode`, `deep work`, `do not disturb` | Phone silent · phone locked · notify |
| `backup` | `backup`, `backup photos`, `sync phone` | Pull DCIM from phone → local `./backups/photos/` |

### Add Your Own Skill

Create any `.yaml` file in the `skills/` folder:

```yaml
name: deploy
trigger:
  - "deploy"
  - "push to server"
steps:
  - system run "git pull"
  - system run "npm run build"
  - server run "systemctl restart myapp"
  - all notify "Deploy complete."
```

### Scheduled Skills

In `config.toml`:

```toml
[schedule]
morning = "07:00 daily"
backup  = "23:00 daily"
focus   = "09:00 weekdays"
```

---

## Natural Language (LLM)

TCC has a built-in **offline LLM adapter** using [Ollama](https://ollama.ai) (free, local, no API key).

**Setup:**
```bash
# Install Ollama (ollama.ai)
ollama pull mistral    # or: llama3, phi3, qwen2.5

# Enable in config.toml:
[llm]
enabled = true
model   = "mistral"
```

**Then:**
```
> open youtube on my phone        →  phone launch youtube
> what's my laptop's disk usage   →  laptop run "df -h"
> take a screenshot               →  system screenshot
> silence my phone                →  phone volume 0
```

> **Works without LLM too.** Any bare action verb (`open`, `launch`, `screenshot`, `battery`, `lock`, `info`, `volume`, `notify`…) automatically routes to the local system without configuration.

---

## Remote Access via Tailscale

Control all your devices from **anywhere in the world** — free, no port forwarding.

```
┌────────────┐    Tailscale       ┌──────────────────┐
│  Your      │    mesh VPN        │  Phone           │
│  Terminal  │◄──────────────────►│  100.98.44.7     │
│  (laptop)  │    encrypted       │  (at home)       │
└────────────┘    peer-to-peer    └──────────────────┘
      │                                    │
      │                           ┌────────▼─────────┐
      └──────────────────────────►│  Server          │
                                   │  100.87.12.9     │
                                   └──────────────────┘
```

**Setup:**
1. Install Tailscale on all devices: [tailscale.com/download](https://tailscale.com/download)
2. Sign in on each device — they join the same mesh automatically
3. Use Tailscale IPs (`100.x.x.x`) in `config.toml`:

```toml
[devices.phone]
ip        = "100.98.44.7"
transport = "tailscale"

[devices.server]
ip        = "100.87.12.9"
transport = "tailscale"
```

Free for personal use: up to 100 devices.

---

## Running a Device Agent

The agent is a **lightweight Flask server** (< 30MB RAM) that runs on any device.

```bash
# Start agent on any device:
python main.py --agent
```

```
==================================================
  TCC Device Agent v2.0
  Device : my-laptop (windows)
  Port   : 7070
  Auth   : configured
  Allowed: ['battery', 'info', 'launch', 'lock', 'ls', 'notify', 'pull', 'push', 'screenshot', 'status', 'volume']
  Denied : ['reboot', 'run', 'shutdown']
==================================================
```

Configure permissions per-device in `agent/agent.toml`:

```toml
[agent]
device_name  = "home-laptop"
device_type  = "windows"
listen_port  = 7070
listen_host  = "0.0.0.0"      # change to Tailscale IP for security
auth_token   = "your-secret"

[permissions]
allowed_actions = ["screenshot", "battery", "info", "launch", "volume", "lock", "notify", "push", "pull", "ls"]
denied_actions  = ["reboot", "shutdown", "run"]
```

---

## Configuration Reference

Full `config.toml` reference:

```toml
[tcc]
version        = "2.0"
log_level      = "INFO"       # DEBUG | INFO | WARNING | ERROR
log_dir        = "logs"
screenshot_dir = "screenshots"

# ── Devices ──────────────────────────────────────────────────────────
[devices.phone]
name       = "phone"
type       = "android"        # android | windows | linux | darwin
ip         = ""               # empty = ADB mode (no agent needed)
port       = 7070
auth_token = "change-me"
transport  = "adb"            # lan | tailscale | adb

[devices.laptop]
name       = "laptop"
type       = "windows"
ip         = "192.168.1.50"
port       = 7070
auth_token = "change-me"
transport  = "lan"

# ── LLM (optional) ───────────────────────────────────────────────────
[llm]
enabled              = false
host                 = "http://localhost:11434"
model                = "mistral"
timeout              = 30
confidence_threshold = 0.85

# ── Scheduler (optional) ─────────────────────────────────────────────
[schedule]
# morning = "07:00 daily"
# backup  = "23:00 daily"
# focus   = "09:00 weekdays"
```

---

## Security Model

| Layer | How it's secured |
|---|---|
| **Auth** | Every agent request requires a `Bearer` token in the header — checked before any action |
| **Network** | Agents bind to LAN or Tailscale IP only — never exposed to public internet |
| **Permissions** | Each agent has an explicit `allowed_actions` list — dangerous commands (`run`, `reboot`, `shutdown`) are denied by default |
| **Injection prevention** | `shell=False` on **all** subprocess calls. Arguments passed as lists, never interpolated strings |
| **Log safety** | Auth tokens are never written to logs — only source IPs on auth failures |
| **ADB** | TCP mode restricted to known IPs; Android 11+ cryptographic pairing |

---

## Roadmap

| Phase | Description | Status |
|---|---|---|
| 0 — Foundation | REPL · parser · local executor · logger | ✅ Done |
| 1 — Device Agents | HTTP agents · Windows/Linux/Android handlers | ✅ Done |
| 2 — Networking | Router · LAN discovery · Tailscale integration | ✅ Done |
| 3 — Android | Full ADB control — screenshot, launch, push/pull | ✅ Done |
| 4 — Remote Access | Tailscale mesh · global routing | ✅ Done |
| 5 — LLM | Ollama adapter · natural language · fallback | ✅ Done |
| 6 — Skills | YAML workflows · scheduler · built-in skills | ✅ Done |
| 7 — Cross-platform | Windows/Linux/macOS · installer | ✅ Done |
| 8 — Voice | Whisper.cpp local speech-to-text | 🔜 Planned |
| 9 — Web Dashboard | FastAPI + HTML, LAN only, no cloud | 🔜 Planned |
| 10 — iOS | Shortcuts app bridge (limited) | 🔜 Planned |

---

## Cost

```
Component          Tool                    Cost
────────────────────────────────────────────────
CLI Framework      Python 3.11+            $0
Natural Language   Ollama (local)          $0
LLM Models         Mistral / LLaMA 3       $0
Mesh Network       Tailscale (personal)    $0
Android Bridge     ADB                     $0
Remote Access      SSH + Tailscale         $0
Task Scheduling    Built-in scheduler      $0
Hosting            Your own hardware       $0
APIs               None                    $0
────────────────────────────────────────────────
TOTAL              Forever                 $0 / month
```

---

## License

MIT — use it, fork it, build on it.

---

<div align="center">

**TCC — Terminal Command Center**
*Because you should own your tools.*

</div>
