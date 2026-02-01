# UConn Isomorphic Question LLM

Generates fully original, isomorphic multiple-choice questions from a set of source questions using an agent workflow (normalize → extract → verify → generate → LaTeX format).

## Features

- **5-Agent Pipeline**: Normalizer → Extracter → Verifier → Generator → LaTeX Formatter
- **PDF/DOCX Support**: Upload files directly, agents extract questions automatically
- **LaTeX Output**: Generated questions are formatted as a downloadable `.tex` file
- **Web Interface**: Modern glass-morphism UI for easy interaction
- **Real-time Status**: Watch agents process your questions in real-time

## Repo layout

- Python backend (repo root)
  - `server.py` — FastAPI server (main entry point)
  - `main.py` — CLI workflow runner
  - `run.py` — Server startup script
  - `Prompts/` — Agent instruction prompts
  - `Outputs/` — Generated output files
- `Site/` — Frontend web interface
  - `index.html` — Main HTML page
  - `styles.css` — Styling
  - `script.js` — JavaScript logic
- `Question Data Files/` — Source PDFs/DOCX used as input material
- `GPT Builder Versions/` — Prompt/schema iterations and reference artifacts

## Prerequisites

- Python 3.10+ recommended
- An OpenAI API key set as `OPENAI_API_KEY` environment variable

## Install

Create and activate a virtualenv, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or install manually:

```bash
pip install fastapi uvicorn python-multipart openai pydantic openai-agents
```

## Run the Web Server

Start the FastAPI server:

```bash
python run.py
```

Then open http://localhost:8000 in your browser.

Options:

- `--port PORT` — Port to run on (default: 8000)
- `--host HOST` — Host to bind to (default: 0.0.0.0)
- `--reload` — Enable auto-reload for development

## Run CLI (Developer Usage)

You can also run the workflow directly from the command line:

```bash
python main.py path/to/questions.pdf
```

Options:

- `-i, --instructions` — Additional instructions for generation
- `-n, --num-questions` — Number of isomorphic questions per source (default: 1)

Example:

```bash
python main.py "../Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## How It Works

1. **Normalizer**: Parses user request into configuration (difficulty, theme, etc.)
2. **Extracter**: Reads PDF/DOCX files and extracts questions using code interpreter
3. **Verifier**: Validates answers and adds explanations
4. **Generator**: Creates new isomorphic questions that test the same concepts
5. **LaTeX Formatter**: Formats all questions into a downloadable `.tex` file

## Output Files

After running, you'll find in `Outputs/<timestamp>/`:

- `final_result.json` — Complete workflow results
- `isomorphic_questions.tex` — LaTeX formatted questions
- `1_normalizer.json` through `5_latex.json` — Individual agent outputs

## Deploy (AWS + Cloudflare)

See `DEPLOYMENT_AWS.md`.

## Deploy (Raspberry Pi + nginx)

See `DEPLOYMENT_RPI_NGINX.md`.
