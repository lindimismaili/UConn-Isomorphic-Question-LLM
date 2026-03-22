# 🧠 UConn Isomorphic Question Generator

Generate original, isomorphic physics multiple-choice questions from source PDFs/DOCX/TXT using a 5-agent workflow:

`Normalizer -> Extracter -> Verifier -> Generator -> LaTeX Formatter`

## ✨ Features

- 5-agent pipeline with structured outputs
- Web UI + FastAPI backend
- PDF/DOCX/TXT upload support
- LaTeX + compiled PDF output (`isomorphic_questions.tex`, `isomorphic_questions.pdf`)
- Single shared OpenAI container per workflow for all agent/tool runtime work
- OpenAI Evals report per run

## 🗂️ Repository Layout

- `backend/`
  - `backend/src/server.py` FastAPI API server
  - `backend/src/main.py` CLI workflow runner
  - `backend/src/run.py` server startup wrapper
  - `backend/src/evaluation.py` OpenAI Evals integration
  - `backend/data/` prompts, skills, agents config, eval data
  - `backend/uploads/` uploaded files by session
  - `backend/outputs/` workflow outputs by timestamp
- `frontend/`
  - Vite + React web UI (dev server + build)
- `dist/`
  - Built frontend output served by FastAPI (created by `npm -C frontend run build`)
- Historical artifacts
  - `archive/`

## 📋 Prerequisites

- Python 3.10+
- `OPENAI_API_KEY` set in environment (or `.env`)

## ⚙️ Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
npm install
```

## ▶️ Run Server on Local Machine

```bash
npm start
```

Open [http://localhost:8000](http://localhost:8000).

### Dev UI (hot reload)

- `npm run dev`

## 🖥️ Run CLI

```bash
python3 -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## 📦 Output Files

Each run writes to `backend/outputs/<timestamp>/`:

- `final_result.json`
- `isomorphic_questions.tex`
- `isomorphic_questions.pdf`
- `1_normalizer.json` ... `5_latex.json`
- `evaluation_report.json` (if eval ran)
- `evaluation_dataset.jsonl` (if eval ran)

## 📚 Deployment Docs

- Local/dev runbook: `docs/RUN.md`
- Live/cloud notes: `docs/LIVE.md`
- Raspberry Pi + Nginx deployment: `docs/DEPLOYMENT_RPI_NGINX.md`

## 🚀 Auto-Deploy Setup (Brand New Raspberry Pi)

Use this once on a fresh Pi to make deploy run automatically after every `git pull`.

### 1. 🧰 Install Git (if needed)

```bash
sudo apt update
sudo apt install -y git
```

This updates package lists and installs Git on the Pi.

### 2. 👤 Set Git username and email

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

This sets your global Git identity for commits and GitHub operations.

### 3. 🔐 Set up GitHub authentication (PAT or SSH)

Choose one option:

Option A (recommended): SSH key

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

This creates an SSH keypair, loads it into the SSH agent, and prints the public key to add in GitHub.

Then:

1. Copy the printed public key.
2. GitHub -> Settings -> SSH and GPG keys -> New SSH key.
3. Paste key and save.
4. Test:

```bash
ssh -T git@github.com
```

This verifies your Pi can authenticate to GitHub over SSH.

Option B: HTTPS + Personal Access Token (PAT)

1. On GitHub, create a PAT with at least `repo` access.
2. Configure Git credential storage on Pi:

```bash
git config --global credential.helper store
```

This saves your HTTPS credentials locally so you do not need to enter the PAT every pull.

3. On first `git clone`/`git pull`, use:

- GitHub username as username
- [REDACTED] as password

### 4. 📁 Clone the repo

SSH clone (recommended):

```bash
mkdir -p /home/triforcelindim/Scripts
cd /home/triforcelindim/Scripts
git clone git@github.com:<your-username>/UConn-IsomorphicQuestionLLM.git
cd UConn-IsomorphicQuestionLLM
```

This creates your scripts folder, clones the repo via SSH, and enters the project directory.

HTTPS clone:

```bash
mkdir -p /home/triforcelindim/Scripts
cd /home/triforcelindim/Scripts
git clone https://github.com/<your-username>/UConn-IsomorphicQuestionLLM.git
cd UConn-IsomorphicQuestionLLM
```

This creates your scripts folder, clones the repo via HTTPS, and enters the project directory.

### 5. 🔧 Enable repo hooks

```bash
git config core.hooksPath .githooks
chmod +x .githooks/post-merge .githooks/post-rewrite raspi-deploy.sh
```

This enables the versioned hook folder and makes the hook/deploy scripts executable.

### 6. ✅ Run first deploy manually

```bash
bash deploy/raspi-deploy.sh
```

This now installs required OS packages on the Pi if they are missing, builds the frontend, syncs the app into `/var/www/uconn`, copies `.env` when present, and configures nginx + systemd.

### 7. 🔄 Normal update flow (from now on)

```bash
cd /home/triforcelindim/Scripts/UConn-IsomorphicQuestionLLM
git pull
```

This pulls updates and automatically triggers deploy if new changes are fetched.

If the pull brings changes, deploy runs automatically via `.githooks/post-merge` (or `.githooks/post-rewrite` for rebase pulls).

### 8. 📜 Check deploy logs

```bash
tail -n 100 deploy-hook.log
```

This shows the latest deploy hook output so you can troubleshoot quickly.

Notes:

- `git push` to GitHub does not deploy by itself on the Pi.
- Auto-deploy is triggered when you run `git pull` on the Pi.
