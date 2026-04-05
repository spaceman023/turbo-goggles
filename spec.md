# PRISM — Pipeline for Recognition, Interpretation, and Structured Markup

## Project Summary

A desktop application (Tauri + React) wrapping a standalone Python CLI pipeline engine for multi-stage OCR processing of long PDF documents. Users visually build layered pipelines combining local OCR engines, preprocessing filters, LLM correction/formatting passes, fusion layers, and output formatters. Pipelines are saveable, shareable, and resumable on failure.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  Tauri Shell (Rust)                                  │
│  ┌───────────────────────────────────────────────┐  │
│  │  React Frontend                                │  │
│  │  • Pipeline Builder (visual DAG)              │  │
│  │  • Document Viewer + Diff                     │  │
│  │  • Layer Inspector + Prompt Editor            │  │
│  │  • Job Runner Console                         │  │
│  │  • Settings / Backend Configuration           │  │
│  └──────────────┬────────────────────────────────┘  │
│                 │ Tauri Commands (IPC)                │
│  ┌──────────────▼────────────────────────────────┐  │
│  │  Rust Backend                                  │  │
│  │  • Python process lifecycle management        │  │
│  │  • Job metadata DB (SQLite)                   │  │
│  │  • File system operations                     │  │
│  │  • Progress stream relay (stdout JSON lines)  │  │
│  └──────────────┬────────────────────────────────┘  │
└─────────────────┼───────────────────────────────────┘
                  │ subprocess (stdin/stdout JSONL)
┌─────────────────▼───────────────────────────────────┐
│  Python Pipeline Engine (standalone CLI)             │
│  • PDF rasterization (300 DPI)                      │
│  • Chunking strategies                              │
│  • OCR engine adapters                              │
│  • Preprocessing filters                            │
│  • LLM backend adapters (CLI + API)                 │
│  • Fusion reconciliation                            │
│  • Output formatters                                │
│  • Checkpoint/resume system                         │
│  • Jinja2 prompt templating                         │
└─────────────────────────────────────────────────────┘
```

### Communication Protocol

The Tauri Rust layer spawns the Python engine as a subprocess. Communication is via **newline-delimited JSON (JSONL)** on stdout:

```jsonc
// Progress update
{"type": "progress", "job_id": "...", "chunk": 3, "layer": 1, "status": "running", "message": "Running PaddleOCR..."}

// Layer complete
{"type": "layer_complete", "job_id": "...", "chunk": 3, "layer": 1, "output_file": "chunk-003/layer-1.paddleocr.txt"}

// Error / halt
{"type": "error", "job_id": "...", "chunk": 3, "layer": 2, "error": "Rate limit exceeded", "halted": true}

// Job complete
{"type": "complete", "job_id": "...", "output_file": "final/merged-output.md"}
```

The Rust layer relays these to the React frontend via Tauri event emitters. Commands from the frontend (start, halt, resume) go through Tauri IPC → Rust → Python stdin as JSONL commands.

---

## Layer Types

### 1. Preprocessing Layer

Operates on rasterized page images before OCR. Each preprocessor takes an image and returns a modified image.

| Preprocessor | Library | Purpose |
|---|---|---|
| Deskew | `deskew` / `scikit-image` | Straighten rotated scans |
| Denoise | OpenCV `fastNlMeansDenoising` | Remove scan noise |
| Binarize | OpenCV adaptive threshold | Convert to black/white for OCR |
| Contrast | PIL `ImageEnhance` | Boost text contrast |
| Sharpen | PIL / OpenCV unsharp mask | Sharpen blurry scans |
| Crop/Trim | OpenCV contour detection | Remove black borders |

**Config schema:**
```yaml
- type: preprocess
  engine: deskew
  options: {}
```

Preprocessors are chainable: `deskew → denoise → binarize → [OCR]`.

### 2. OCR Layer

Operates on a page image (post-preprocessing). Produces raw text output.

| Engine | Library | Strengths |
|---|---|---|
| Tesseract | `pytesseract` | Ubiquitous, many languages, solid baseline |
| EasyOCR | `easyocr` | Handwriting, noisy images, 80+ languages |
| PaddleOCR | `paddleocr` | Best overall accuracy, table/layout detection |
| Surya | `surya-ocr` | Excellent layout analysis, reading order |
| docTR | `doctr` | HuggingFace ecosystem, modern architecture |
| macOS Vision | `pyobjc` (Vision framework) | Free on Mac, surprisingly good, fast |

**Config schema:**
```yaml
- type: ocr
  engine: paddleocr
  options:
    languages: [en]
    # Engine-specific options passed through
```

### 3. LLM Layer

Operates on text (the accumulated output from prior layers). Sends text (and optionally the source image) to an LLM with a Jinja2 prompt template.

**Config schema:**
```yaml
- type: llm
  backend: openrouter          # codex | gemini | claude | openrouter
  model: google/gemini-2.5-flash  # only for openrouter
  prompt_file: prompts/ocr_correction.md
  include_image: false         # toggle: send source page image alongside text
  options:
    temperature: 0.1
    max_tokens: 4096
```

### 4. Fusion Layer

References two or more prior OCR/LLM layer outputs and uses an LLM to reconcile them into a single best-version output. Expressed as three separate layers in the pipeline but the fusion layer itself has a special config:

```yaml
# Layer 0: Tesseract OCR
- type: ocr
  engine: tesseract
  id: ocr-tesseract

# Layer 1: PaddleOCR
- type: ocr
  engine: paddleocr
  id: ocr-paddle

# Layer 2: Fusion
- type: fusion
  source_layers: [ocr-tesseract, ocr-paddle]  # references by id
  backend: openrouter
  model: anthropic/claude-sonnet-4
  prompt_file: prompts/fusion.md
  include_image: true
```

The fusion prompt template receives special variables:
```jinja2
You are reconciling multiple OCR outputs of the same document page.

{% for source in sources %}
=== OCR Output from {{ source.engine }} ===
{{ source.text }}
{% endfor %}

Produce the single most accurate transcription.
```

**Important architectural note:** Fusion source layers execute in parallel (they're independent OCR passes on the same input). The fusion layer itself waits for all sources to complete before executing. The pipeline engine handles this scheduling automatically — non-fusion layers execute sequentially, but fusion source layers are a parallelism opportunity.

### 5. Output Format Layer

Transforms the final text into a target format. Always the last layer(s) in the pipeline.

| Format | Output | Notes |
|---|---|---|
| Plain Text | `.txt` | Direct passthrough, minimal processing |
| Markdown | `.md` | Headings, lists, tables, code blocks detected/formatted |
| HTML | `.html` | Structured markup, potential for CSS styling |
| DOCX | `.docx` | Via `python-docx`, preserves basic formatting |

**Config schema:**
```yaml
- type: output
  format: markdown
  options:
    preserve_page_breaks: true   # insert page markers
    heading_detection: true      # attempt to detect headings from font size/bold
```

Output format layers can also be LLM-assisted — the user can attach a prompt that instructs the LLM to convert raw text to Markdown, for example. This is just an LLM layer with a formatting-focused prompt, but the UI can present it as an "Output Format" step.

---

## Chunking System

### Strategies

| Strategy | Description | Best For |
|---|---|---|
| `page` | Each page is an independent unit | Scanned documents, simple layouts |
| `sliding_window` | N pages per chunk, M overlap | Long documents with cross-page context |
| `semantic` | Split on paragraph/section breaks post-OCR, target token count | Clean OCR, narrative documents |
| `whole_document` | Entire document as one chunk | Short docs, huge context models (Gemini) |

### Config

```yaml
chunking:
  strategy: sliding_window
  chunk_size: 5              # pages per chunk
  overlap: 1                 # pages of overlap between chunks
  max_tokens: 8000           # soft cap; if exceeded, reduce chunk_size dynamically
  merge_strategy: prefer_later  # prefer_later | prefer_earlier | middle_priority
```

### Merge Strategies

When chunks overlap, the same page appears in multiple chunks. Merge resolves this:

- **`prefer_later`** — always use the version from the chunk where this page appeared later (simpler, usually fine)
- **`prefer_earlier`** — opposite
- **`middle_priority`** — prefer the version where the page was closest to the center of its chunk (most surrounding context, most accurate). This is the most sophisticated option.

### Token Estimation

Before sending a chunk to an LLM layer, estimate token count (word count × 1.3 as a rough heuristic, or use `tiktoken` for OpenAI models). If the chunk + prompt exceeds the model's context window, either:
1. Split the chunk further (automatic sub-chunking)
2. Warn the user in the job log

---

## Prompt Templating

Prompts are Jinja2 templates stored as `.md` files. Available variables:

| Variable | Type | Description |
|---|---|---|
| `{{ text }}` | string | The accumulated text output from prior layers |
| `{{ page_numbers }}` | list[int] | Page numbers in this chunk |
| `{{ chunk_index }}` | int | Chunk number (0-indexed) |
| `{{ total_chunks }}` | int | Total number of chunks |
| `{{ filename }}` | string | Source document filename |
| `{{ layer_name }}` | string | Current layer's ID/name |
| `{{ previous_layers }}` | list[dict] | Metadata about prior layers in the pipeline |
| `{{ sources }}` | list[dict] | (Fusion only) Text outputs from source layers |
| `{{ custom }}` | dict | User-defined key-value pairs from pipeline config |

### Example Prompts

**`prompts/ocr_correction.md`**
```jinja2
You are a precise OCR error corrector. Below is raw OCR output from page(s) {{ page_numbers | join(', ') }} of "{{ filename }}".

Fix OCR errors including:
- Character substitutions (rn→m, l→1, O→0)
- Broken words and spurious line breaks
- Missing or hallucinated punctuation
- Garbled headers/footers

Do NOT alter the meaning, add information, or summarize. Preserve the original structure.

=== OCR TEXT ===
{{ text }}
=== END OCR TEXT ===

Return only the corrected text.
```

**`prompts/markdown_format.md`**
```jinja2
Convert the following document text into clean Markdown. Detect and apply:
- Headings (from font size context, ALL CAPS lines, or structural cues)
- Bullet and numbered lists
- Tables (if columnar data is detected)
- Block quotes

Preserve all content exactly. Do not summarize or omit anything.

{{ text }}
```

**`prompts/legal_extraction.md`**
```jinja2
You are a legal document analyst. Extract the following structured data from this document:

- Case number(s)
- Court name
- Judge name
- Parties (plaintiff/defendant/petitioner/respondent)
- Filing date
- Key dates mentioned
- Charges or claims

Return as JSON. If a field is not found, use null.

{{ text }}
```

### Prompt Editor Features (GUI)

- Syntax-highlighted textarea with Jinja2 support
- Split view: template on left, live preview on right
- Preview substitutes a sample chunk so users can see the actual prompt that will be sent
- Token count estimate for the rendered prompt
- Variable autocomplete (type `{{` and get a dropdown)
- Save prompt to library; load from library

---

## LLM Backend Adapters

Each backend implements a common Python interface:

```python
class LLMBackend(ABC):
    @abstractmethod
    async def process(self, prompt: str, image: bytes | None = None) -> LLMResponse:
        """Send prompt (and optional image) to the LLM. Return response text."""
        ...

@dataclass
class LLMResponse:
    text: str
    tokens_in: int | None      # None if CLI-based (can't track)
    tokens_out: int | None
    cost: float | None          # None if CLI-based
    model: str
    duration_ms: int
```

### Backend Implementations

| Backend | Method | Image Support | Token Tracking | Notes |
|---|---|---|---|---|
| `codex` | `subprocess.run(["codex", ...])` | No (text-only CLI) | No | OpenAI Codex CLI; input via stdin/temp file |
| `gemini` | `subprocess.run(["gemini", ...])` | Yes (pass image path as arg) | No | Google Gemini CLI |
| `claude` | `subprocess.run(["claude", "-p", ...])` | Yes (pipe with image) | No | Claude Code CLI; `--output-format json` for structured output |
| `openrouter` | `requests.post()` to API | Yes (base64 in message) | Yes (from response) | Full control, any model, cost tracking |

### CLI Adapter Details

**Codex CLI:**
```python
result = subprocess.run(
    ["codex", "--quiet", "--prompt", rendered_prompt],
    capture_output=True, text=True, timeout=120
)
```

**Gemini CLI:**
```python
# Gemini CLI accepts file paths for multimodal
args = ["gemini", "-p", rendered_prompt]
if image_path and include_image:
    args.extend(["-f", image_path])
result = subprocess.run(args, capture_output=True, text=True, timeout=120)
```

**Claude Code CLI:**
```python
result = subprocess.run(
    ["claude", "-p", rendered_prompt, "--output-format", "json"],
    capture_output=True, text=True, timeout=120
)
response = json.loads(result.stdout)
text = response["result"]  # exact key TBD based on CLI output format
```

**OpenRouter API:**
```python
response = requests.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {api_key}"},
    json={
        "model": model_id,
        "messages": messages,  # includes image as base64 if toggled
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
)
data = response.json()
text = data["choices"][0]["message"]["content"]
tokens_in = data["usage"]["prompt_tokens"]
tokens_out = data["usage"]["completion_tokens"]
```

### Backend Health Checks

On app startup (and on-demand from Settings), verify each backend:

- **CLI backends:** `which codex`, `which gemini`, `which claude` — check exit code. Optionally send a trivial prompt to verify auth.
- **OpenRouter:** `GET https://openrouter.ai/api/v1/models` with API key — verify 200 response.

Display status as green/red indicators in Settings panel.

---

## Job System

### Directory Structure

```
~/.prism/
  config.json              # global settings (backends, API keys, defaults)
  prompts/                 # user's prompt library
    ocr_correction.md
    markdown_format.md
    ...
  pipelines/               # saved pipeline definitions
    high-accuracy.json
    legal-docs.json
    ...
  jobs/
    job-20260404-152301/
      manifest.json        # pipeline config + full progress state
      input/
        original.pdf
      pages/
        page-001.png       # rasterized at 300 DPI
        page-002.png
        ...
      chunks/
        chunk-001/
          layer-0.tesseract.txt
          layer-1.paddleocr.txt
          layer-2.fusion-claude.txt
          layer-3.markdown.txt
        chunk-002/
          layer-0.tesseract.txt
          layer-1.paddleocr.txt
          # ← halted here
        chunk-003/
          # ← not started
      final/
        merged-output.md
      cost-log.jsonl        # OpenRouter token/cost tracking
    job-20260404-160000/
      ...
  prism.db                 # SQLite: job index, batch metadata
```

### manifest.json

```jsonc
{
  "job_id": "job-20260404-152301",
  "created_at": "2026-04-04T15:23:01Z",
  "updated_at": "2026-04-04T15:45:12Z",
  "status": "halted",           // pending | running | halted | complete | failed
  "input_file": "input/original.pdf",
  "total_pages": 47,
  "pipeline": { /* full pipeline config */ },
  "chunking": { /* chunking config */ },
  "chunks": [
    {
      "id": "chunk-001",
      "pages": [1, 2, 3, 4, 5],
      "layers_completed": [0, 1, 2, 3],
      "status": "complete"
    },
    {
      "id": "chunk-002",
      "pages": [5, 6, 7, 8, 9],
      "layers_completed": [0, 1],
      "status": "halted",
      "error": "Rate limit exceeded on OpenRouter",
      "halted_at_layer": 2
    },
    {
      "id": "chunk-003",
      "pages": [9, 10, 11, 12, 13],
      "layers_completed": [],
      "status": "pending"
    }
  ]
}
```

### SQLite Schema (prism.db)

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,           -- job-20260404-152301
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,           -- pending|running|halted|complete|failed
    input_filename TEXT NOT NULL,
    total_pages INTEGER,
    total_chunks INTEGER,
    chunks_completed INTEGER DEFAULT 0,
    pipeline_name TEXT,             -- name of saved pipeline used
    batch_id TEXT,                  -- NULL if standalone, else links to batch
    error_message TEXT
);

CREATE TABLE batches (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,           -- running|halted|complete
    pipeline_name TEXT NOT NULL,
    total_jobs INTEGER,
    jobs_completed INTEGER DEFAULT 0
);

CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    layer_index INTEGER NOT NULL,
    backend TEXT NOT NULL,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    duration_ms INTEGER,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

### Resume Behavior

On `resume`:
1. Read `manifest.json` to find the first chunk with `status != "complete"`.
2. Within that chunk, find `halted_at_layer`.
3. Re-execute from that exact chunk × layer coordinate.
4. All prior layer outputs for that chunk are already on disk — no rework.

On `halt` (user-initiated or error):
1. Wait for the currently executing operation to finish (or timeout after 30s).
2. Update `manifest.json` with exact halt point.
3. Report status via JSONL to frontend.

---

## GUI Design

### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  PRISM                                          [Settings] [⚙]  │
├────────────┬─────────────────────────────────┬───────────────────┤
│            │                                 │                   │
│  Pipeline  │   Document Viewer               │  Layer Inspector  │
│  Builder   │                                 │                   │
│            │  ┌─────────┬─────────────────┐  │  Engine: [▾]      │
│ ┌────────┐ │  │         │                 │  │  Prompt: [Edit]   │
│ │Deskew  │ │  │  PDF    │  OCR Output     │  │  Image: [☐]      │
│ └────────┘ │  │  Page   │  (current       │  │                   │
│     ↓      │  │  View   │   layer)        │  │  ─── Options ──   │
│ ┌────────┐ │  │         │                 │  │  temperature: 0.1 │
│ │Tesseract│ │  │         │                 │  │  max_tokens: 4096 │
│ └────────┘ │  │         │                 │  │                   │
│     ↓      │  └─────────┴─────────────────┘  │                   │
│ ┌────────┐ │                                 │                   │
│ │PaddleOCR│ │  [Page] [Diff] [Side-by-Side]  │                   │
│ └────────┘ │                                 │                   │
│     ↓      │                                 │                   │
│ ┌────────┐ │                                 │                   │
│ │Fusion  │ │                                 │                   │
│ │(Claude)│ │                                 │                   │
│ └────────┘ │                                 │                   │
│     ↓      │                                 │                   │
│ ┌────────┐ │                                 │                   │
│ │Markdown│ │                                 │                   │
│ │Format  │ │                                 │                   │
│ └────────┘ │                                 │                   │
│            │                                 │                   │
│ [+ Layer]  │                                 │                   │
├────────────┴─────────────────────────────────┴───────────────────┤
│  Job Runner                                    [▶ Run] [⏸ Halt] │
│  ████████████░░░░░░░░  Chunk 7/15 • Layer 2/4 • PaddleOCR...    │
│  > Chunk 7: Running PaddleOCR on pages 31-35...                  │
│  > Chunk 6: ✓ Complete (4 layers, 12.3s)                         │
└──────────────────────────────────────────────────────────────────┘
```

### Pipeline Builder Panel (Left)

- Vertical stack of layer cards, connected by arrows
- Each card shows: layer type icon, engine name, brief config summary
- Drag handles for reorder
- Click card → populates Layer Inspector (right panel)
- Color-coded by type:
  - **Blue** — Preprocessing
  - **Green** — OCR
  - **Purple** — LLM
  - **Orange** — Fusion
  - **Gray** — Output Format
- `[+ Add Layer]` button at bottom opens a categorized picker
- Fusion layers show dotted lines back to their source layers (mini-DAG visualization)
- Right-click context menu: duplicate, delete, disable (skip without removing)

### Document Viewer Panel (Center)

Three view modes toggled by tabs:

**Page View:**
- Left: rendered PDF page (from rasterized PNG)
- Right: text output from the currently selected layer
- Synced scrolling
- Page thumbnails as a filmstrip along the bottom or side
- Click a layer card on the left to switch which layer's output is displayed

**Diff View:**
- Two dropdowns at top: "Compare [Layer A ▾] with [Layer B ▾]"
- Inline diff (red deletions, green additions) like VS Code
- Word-level diffing, not line-level

**Side-by-Side View:**
- All layer outputs for the current page shown as columns
- Horizontally scrollable if many layers
- Highlights differences between adjacent columns

### Layer Inspector Panel (Right)

Context-sensitive based on selected layer type:

**For Preprocessing:**
- Engine dropdown
- Engine-specific options (sliders, checkboxes)
- Preview: before/after thumbnail of a sample page

**For OCR:**
- Engine dropdown
- Language multi-select
- Engine-specific options

**For LLM:**
- Backend dropdown (from configured backends in Settings)
- Model picker (for OpenRouter: searchable model list; for CLIs: N/A)
- Prompt editor button → opens modal/drawer with:
  - Jinja2 template editor (left)
  - Live preview with sample data (right)
  - Token count estimate
  - Variable autocomplete
  - Save to / load from prompt library
- `Include source image` toggle
- Temperature slider
- Max tokens input

**For Fusion:**
- Source layers multi-select (populated from prior OCR/LLM layers)
- Backend + model picker (same as LLM)
- Prompt editor (with `{{ sources }}` variable available)
- `Include source image` toggle

**For Output Format:**
- Format dropdown (Plain Text, Markdown, HTML, DOCX)
- Format-specific options (preserve page breaks, heading detection, etc.)
- Optional LLM-assisted toggle (adds prompt editor for LLM-driven formatting)

### Job Runner Bar (Bottom)

- Expandable/collapsible console
- Progress bar: overall (chunks completed / total)
- Current status: "Chunk 7/15 • Layer 2/4 • Running PaddleOCR..."
- Log stream: scrolling text output with timestamps
- Buttons: Run, Halt, Resume
- For batch mode: tabs per document, or a summary view

### Settings Page

Accessed via gear icon. Tabs:

**Backends:**
- Per-backend configuration card:
  - **Codex CLI:** Path to binary, health check status (green/red)
  - **Gemini CLI:** Path to binary, health check status
  - **Claude CLI:** Path to binary, health check status
  - **OpenRouter:** API key input, balance display, health check
- "Test All" button runs health checks on everything

**Defaults:**
- Default chunking strategy and parameters
- Default DPI (locked to 300 but visible)
- Default output directory
- Default prompt for each layer type

**Pipeline Templates:**
- List of built-in and user-saved pipelines
- Import/export as JSON

### Pipeline Templates (Starting Screen)

When no job is active, the center panel shows a template gallery:

| Template | Pipeline |
|---|---|
| **Quick & Dirty** | Tesseract → Plain Text |
| **High Accuracy** | Deskew → Surya + PaddleOCR (fusion via Claude) → Markdown |
| **Legal Document** | Deskew → Denoise → Tesseract + PaddleOCR (fusion via Gemini) → OCR Correction (Claude) → Markdown → Legal Extraction (structured JSON) |
| **Handwritten Notes** | Deskew → Binarize → EasyOCR → Correction (GPT-4o with image) → Plain Text |
| **Budget Friendly** | Deskew → Tesseract → Correction (OpenRouter/Gemini Flash) → Markdown |
| **Kitchen Sink** | Deskew → Denoise → Tesseract + PaddleOCR + Surya (triple fusion) → Correction (Claude) → Formatting (Gemini) → Markdown |

Click a template to load it into the Pipeline Builder. User can then customize before running.

### Batch Mode

Accessed via File → Batch Process or drag-and-drop of a folder:

- Queue view: table of documents with columns: filename, pages, status, progress, current layer
- Shared pipeline: all documents use the same pipeline config
- Independent jobs: each document gets its own job directory
- Halt halts the current document; user can resume or skip to next
- Summary statistics on completion: total pages, total time, total cost (OpenRouter)

---

## Python CLI Interface

The pipeline engine is independently usable from the terminal:

```bash
# Run a pipeline
prism run --config pipeline.json --input document.pdf [--output-dir ./output]

# Resume a halted job
prism resume --job ~/.prism/jobs/job-20260404-152301

# Check job status
prism status --job ~/.prism/jobs/job-20260404-152301

# Diff two layers
prism diff --job ~/.prism/jobs/job-20260404-152301 --layers 0 2

# List available OCR engines
prism engines

# Validate a pipeline config
prism validate --config pipeline.json

# Batch process
prism batch --config pipeline.json --input-dir ./scans/ [--parallel 3]
```

### Pipeline JSON Format

```jsonc
{
  "name": "High Accuracy Legal",
  "description": "Dual OCR fusion with LLM correction for legal documents",
  "chunking": {
    "strategy": "sliding_window",
    "chunk_size": 5,
    "overlap": 1,
    "max_tokens": 8000,
    "merge_strategy": "middle_priority"
  },
  "layers": [
    {
      "id": "deskew",
      "type": "preprocess",
      "engine": "deskew",
      "options": {}
    },
    {
      "id": "ocr-tesseract",
      "type": "ocr",
      "engine": "tesseract",
      "options": { "languages": ["eng"] }
    },
    {
      "id": "ocr-paddle",
      "type": "ocr",
      "engine": "paddleocr",
      "options": { "languages": ["en"] }
    },
    {
      "id": "fusion",
      "type": "fusion",
      "source_layers": ["ocr-tesseract", "ocr-paddle"],
      "backend": "claude",
      "prompt_file": "prompts/fusion.md",
      "include_image": true,
      "options": {}
    },
    {
      "id": "correction",
      "type": "llm",
      "backend": "openrouter",
      "model": "anthropic/claude-sonnet-4",
      "prompt_file": "prompts/ocr_correction.md",
      "include_image": false,
      "options": { "temperature": 0.1, "max_tokens": 4096 }
    },
    {
      "id": "output",
      "type": "output",
      "format": "markdown",
      "options": { "preserve_page_breaks": true, "heading_detection": true }
    }
  ]
}
```

---

## Execution Engine Details

### Pipeline Execution Flow

```
For each chunk:
  1. Identify layer execution order (topological sort for fusion dependencies)
  2. For non-fusion layers: execute sequentially
  3. For fusion source layers: execute in parallel (they're independent)
  4. Fusion layer: wait for all sources, then execute
  5. Save each layer's output to disk immediately
  6. Update manifest.json after each layer completes
  7. On error: save state, emit halt event, stop
```

### Parallel Execution

Two levels of parallelism:

1. **Intra-chunk:** Fusion source layers run concurrently (e.g., Tesseract and PaddleOCR on the same pages simultaneously). CPU-bound, use `multiprocessing`.

2. **Inter-chunk (batch mode only):** Multiple chunks can be processed concurrently for I/O-bound LLM layers. Configurable concurrency limit (`--parallel N` in CLI, slider in GUI). Rate limiting handled per-backend:
   - OpenRouter: respect `retry-after` headers, exponential backoff
   - CLIs: limit to 1 concurrent call per CLI backend (they're not designed for parallel use)

### Error Handling

| Error Type | Behavior |
|---|---|
| OCR engine crash | Halt job, save state. User can swap engine and resume. |
| LLM rate limit | Retry 3x with exponential backoff (2s, 4s, 8s). Then halt. |
| LLM timeout | Retry 1x. Then halt. |
| LLM content filter | Log the refusal, halt. User can edit prompt and resume. |
| CLI not found | Halt immediately with clear error message. |
| Disk full | Halt immediately. |
| Invalid pipeline config | Fail before starting (validation step). |

All errors are logged to the job's log file and emitted as JSONL events to the GUI.

---

## Cost Tracking (OpenRouter)

Every OpenRouter API call appends to `cost-log.jsonl`:

```jsonc
{"timestamp": "2026-04-04T15:34:12Z", "job_id": "...", "chunk": "chunk-007", "layer": "correction", "model": "anthropic/claude-sonnet-4", "tokens_in": 3420, "tokens_out": 2890, "cost_usd": 0.0234, "duration_ms": 4521}
```

Also inserted into SQLite `cost_log` table for aggregate queries.

GUI shows:
- Running cost total in the Job Runner bar
- Per-job cost summary on completion
- Cost breakdown by layer (which layer is most expensive?)
- Cost history across jobs (Settings → Usage)

CLI backends show "N/A" for cost — the user knows they're on a subscription.

---

## Technology Stack

### Frontend (Tauri + React)
- **Tauri 2.x** — Rust backend, webview frontend
- **React 18+** with TypeScript
- **Tailwind CSS** — styling
- **Monaco Editor** or **CodeMirror 6** — prompt editor with Jinja2 syntax highlighting
- **react-diff-viewer** or similar — inline diff view
- **dnd-kit** — drag-and-drop for pipeline builder
- **react-pdf** or `<img>` tags for rasterized page display
- **Zustand** or **Jotai** — state management (lightweight)

### Backend (Python)
- **Python 3.11+**
- **click** — CLI framework
- **Jinja2** — prompt templating
- **pdf2image** (poppler) — PDF rasterization at 300 DPI
- **Pillow** — image preprocessing
- **OpenCV (cv2)** — advanced preprocessing (denoise, binarize, sharpen)
- **pytesseract** — Tesseract binding
- **easyocr** — EasyOCR
- **paddleocr** — PaddleOCR
- **surya-ocr** — Surya
- **python-doctr** — docTR
- **pyobjc** (macOS only) — Vision framework OCR
- **requests** — OpenRouter API calls
- **pydantic** — config validation
- **rich** — CLI progress display (when used standalone)

### System Dependencies
- **Poppler** — `pdftoppm` for PDF → image
- **Tesseract** — system install
- **PaddlePaddle** — for PaddleOCR (pip install)

---

## Development Phases

### Phase 1: Python Pipeline Engine (MVP)
- PDF rasterization
- Page-level chunking (simplest strategy)
- Tesseract + PaddleOCR adapters
- OpenRouter LLM adapter (most reliable, full-featured)
- Basic Jinja2 prompt templating ({{ text }}, {{ page_numbers }}, {{ filename }})
- Sequential execution (no parallelism)
- File-based checkpoint/resume (manifest.json)
- CLI interface: `run`, `resume`, `status`
- Plain text output format only

### Phase 2: Full OCR + LLM Suite
- All 6 OCR engines
- All preprocessing filters
- All 4 LLM backends (codex, gemini, claude CLI, openrouter)
- Fusion layer type
- Sliding window chunking + merge strategies
- Image passthrough toggle
- Markdown + HTML output formats
- Backend health checks
- Cost tracking (OpenRouter)

### Phase 3: Tauri GUI Shell
- Three-panel layout
- Pipeline builder with drag-and-drop
- Layer inspector (config panels per type)
- Document viewer (page view only)
- Job runner bar with progress + log
- Settings page (backends, defaults)
- Pipeline save/load
- SQLite job index
- JSONL communication protocol between Rust and Python

### Phase 4: Advanced GUI Features
- Diff view (layer-to-layer comparison)
- Side-by-side multi-layer view
- Prompt editor with live preview + token estimate
- Pipeline template gallery
- Batch mode (multi-document queue)
- DOCX output format
- Semantic chunking strategy
- Parallel execution (fusion sources + batch inter-chunk)

### Phase 5: Polish + Ecosystem
- Pipeline sharing (export/import with embedded prompts)
- Prompt library marketplace (local, maybe shared)
- OCR confidence scores (where engines provide them)
- Quality metrics (compare layer N vs layer N-1 — how much changed?)
- Keyboard shortcuts for common operations
- Dark mode (your earth-tone system would work beautifully here)
- Auto-update for Python dependencies

---

## Open Questions / Future Considerations

1. **Local LLM support (Ollama):** Would add an `ollama` backend type. Straightforward since Ollama exposes an OpenAI-compatible API. Not in MVP but trivial to add in Phase 2.

2. **Anthropic API direct:** You have API access. Adding a native `anthropic` backend (not CLI, not OpenRouter) would give you token tracking + lower latency. Worth adding alongside OpenRouter.

3. **TRACER integration:** This could feed directly into TRACER's evidence pipeline. The output (OCR'd text + structured extraction) maps to TRACER's evidence viewer. Future consideration: PRISM job outputs as a TRACER evidence type.

4. **hOCR preservation:** Some OCR engines (Tesseract, docTR) can output hOCR (HTML with bounding box coordinates). Preserving this through the pipeline would enable features like click-to-highlight-in-source-image. Significant complexity but powerful for legal review.

5. **GPU detection:** PaddleOCR, EasyOCR, and Surya all benefit from GPU. Auto-detect CUDA/MPS availability and configure engines accordingly. Surface GPU status in Settings.
