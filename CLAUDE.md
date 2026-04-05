# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PRISM (Pipeline for Recognition, Interpretation, and Structured Markup) is a Python CLI tool that processes PDFs through configurable multi-stage OCR pipelines. It rasterizes PDF pages to images, runs OCR engines, optionally sends results through LLM correction/fusion layers, and produces merged text output. Jobs support checkpoint/resume — if a chunk fails, the pipeline halts and can be resumed from the failure point.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Install with OCR engine support (tesseract/paddleocr)
pip install -e ".[ocr,dev]"

# Run all tests
pytest

# Run a single test class or test
pytest tests/test_prism.py::TestChunking
pytest tests/test_prism.py::TestPipelineEngine::test_full_run_with_mocked_ocr

# Run CLI
prism run --config examples/quick_tesseract.json --input doc.pdf
prism resume --job ~/.prism/jobs/job-XXXXXXXX-XXXXXX
prism status --job ~/.prism/jobs/job-XXXXXXXX-XXXXXX
prism validate --config examples/dual_ocr_fusion.json
prism engines
```

## Architecture

The pipeline processes a PDF through a sequence of **layers** defined in a JSON config. Each layer has a type: `ocr`, `llm`, `fusion`, `output`, or `preprocess`.

### Processing flow

1. **Rasterize** (`rasterize.py`) — PDF pages become `page-NNN.png` at 300 DPI via `pdf2image`
2. **Chunk** (`chunking.py`) — Pages are grouped into chunks (per-page, sliding window, or whole-document)
3. **Execute layers** (`engine.py: PipelineEngine`) — Each chunk passes through every layer sequentially. Text accumulates: each layer receives the previous layer's output
4. **Merge** (`formatters/plain_text.py`) — Final layer outputs from all chunks are concatenated into `final/merged-output.txt`

### Two registry systems

- **OCR engines** (`engines/`) — `OCREngine` base class. Implementations: `tesseract`, `paddleocr`. Registry in `engines/registry.py` with lazy init.
- **LLM backends** (`backends/`) — `LLMBackend` base class. Implementation: `openrouter` (requires `OPENROUTER_API_KEY` env var). Registry in `backends/registry.py`.

### Fusion layers

Fusion layers differ from regular LLM layers: they pull output from multiple named `source_layers` (by layer `id`) rather than using the accumulated text from the previous layer. This enables multi-engine OCR reconciliation (e.g., compare Tesseract vs PaddleOCR output).

### Prompt templating

LLM and fusion layers use Jinja2 templates from `prompts/`. Available template variables: `text`, `page_numbers`, `chunk_index`, `total_chunks`, `filename`, `layer_name`, `sources` (fusion only). Prompt files are resolved in order: job dir, `~/.prism/`, cwd.

### JSONL IPC

Progress/completion/error messages are emitted as JSONL to stdout (`jsonl.py`). All Rich UI output goes to stderr. This separation supports integration with a Tauri frontend.

### Job directory layout

```
~/.prism/jobs/job-YYYYMMDD-HHMMSS/
  manifest.json          # JobManifest — full pipeline state, checkpointed on every state change
  input/doc.pdf
  pages/page-001.png ...
  chunks/chunk-001/layer-0.tesseract.txt, layer-1.openrouter.txt ...
  final/merged-output.txt
```

## Key conventions

- All models are Pydantic v2 (`models.py`). Pipeline configs are validated with `model_validate()`.
- Build system is Hatch (`hatchling`). Source layout: `src/prism/`.
- Tests mock OCR engines and LLM backends — they run without external dependencies. Use `unittest.mock.patch` on registry functions (`prism.engine.get_engine`, etc.).
- Requires Python >= 3.11.
