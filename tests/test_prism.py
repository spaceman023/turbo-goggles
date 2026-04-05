"""Tests for the PRISM Python CLI MVP — runs in sandbox without OCR engines."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Models ───────────────────────────────────────────────────────────────────

from prism.models import (
    ChunkState,
    ChunkStatus,
    ChunkingConfig,
    ChunkingStrategy,
    JobManifest,
    JobStatus,
    LayerConfig,
    LayerType,
    PipelineConfig,
    ProgressMessage,
    LayerCompleteMessage,
    ErrorMessage,
    CompleteMessage,
)


class TestModels:
    def test_layer_config_ocr(self):
        lc = LayerConfig(id="t1", type=LayerType.OCR, engine="tesseract")
        assert lc.type == LayerType.OCR
        assert lc.engine == "tesseract"
        assert lc.options == {}

    def test_layer_config_llm(self):
        lc = LayerConfig(
            id="llm1",
            type=LayerType.LLM,
            backend="openrouter",
            model="anthropic/claude-sonnet-4",
            prompt_file="prompts/ocr_correction.md",
            options={"temperature": 0.1},
        )
        assert lc.backend == "openrouter"
        assert lc.options["temperature"] == 0.1

    def test_layer_config_fusion(self):
        lc = LayerConfig(
            id="f1",
            type=LayerType.FUSION,
            source_layers=["t1", "p1"],
            backend="openrouter",
            model="x/y",
        )
        assert lc.source_layers == ["t1", "p1"]

    def test_pipeline_config_from_json(self):
        raw = {
            "name": "Test",
            "layers": [
                {"id": "ocr1", "type": "ocr", "engine": "tesseract"},
                {"id": "out", "type": "output", "format": "txt"},
            ],
        }
        pc = PipelineConfig.model_validate(raw)
        assert pc.name == "Test"
        assert len(pc.layers) == 2
        assert pc.chunking.strategy == ChunkingStrategy.PAGE

    def test_chunking_config_defaults(self):
        cc = ChunkingConfig()
        assert cc.strategy == ChunkingStrategy.PAGE
        assert cc.chunk_size == 1

    def test_chunk_state(self):
        cs = ChunkState(id="chunk-001", pages=[1, 2, 3])
        assert cs.status == ChunkStatus.PENDING
        assert cs.layers_completed == []

    def test_job_manifest(self):
        pc = PipelineConfig(
            layers=[LayerConfig(id="ocr1", type=LayerType.OCR, engine="tesseract")]
        )
        jm = JobManifest(job_id="job-test", input_file="test.pdf", pipeline=pc, chunking=pc.chunking)
        assert jm.status == JobStatus.PENDING
        assert jm.total_pages == 0

    def test_progress_message_json(self):
        pm = ProgressMessage(
            job_id="j1", chunk=0, layer=0, status="running", message="test"
        )
        data = json.loads(pm.model_dump_json())
        assert data["type"] == "progress"
        assert data["job_id"] == "j1"

    def test_error_message(self):
        em = ErrorMessage(job_id="j1", chunk=1, layer=2, error="boom", halted=True)
        data = json.loads(em.model_dump_json())
        assert data["halted"] is True

    def test_complete_message(self):
        cm = CompleteMessage(job_id="j1", output_file="final/out.txt")
        data = json.loads(cm.model_dump_json())
        assert data["type"] == "complete"


# ── Chunking ─────────────────────────────────────────────────────────────────

from prism.chunking import build_chunks


class TestChunking:
    def _make_pages(self, n: int) -> list[Path]:
        return [Path(f"page-{i+1:03d}.png") for i in range(n)]

    def test_page_strategy(self):
        pages = self._make_pages(5)
        config = ChunkingConfig(strategy=ChunkingStrategy.PAGE)
        chunks = build_chunks(pages, config)
        assert len(chunks) == 5
        assert chunks[0].pages == [1]
        assert chunks[4].pages == [5]
        assert all(c.status == ChunkStatus.PENDING for c in chunks)

    def test_sliding_window(self):
        pages = self._make_pages(10)
        config = ChunkingConfig(
            strategy=ChunkingStrategy.SLIDING_WINDOW, chunk_size=3, overlap=1
        )
        chunks = build_chunks(pages, config)
        # step = 3-1 = 2, so chunks: [1,2,3], [3,4,5], [5,6,7], [7,8,9], [9,10]
        assert len(chunks) == 5
        assert chunks[0].pages == [1, 2, 3]
        assert chunks[1].pages == [3, 4, 5]
        assert chunks[4].pages == [9, 10]

    def test_whole_document(self):
        pages = self._make_pages(7)
        config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_DOCUMENT)
        chunks = build_chunks(pages, config)
        assert len(chunks) == 1
        assert chunks[0].pages == list(range(1, 8))


# ── Templating ───────────────────────────────────────────────────────────────

from prism.templating import render_prompt, default_variables


class TestTemplating:
    def test_render_simple(self, tmp_path):
        tmpl = tmp_path / "test.md"
        tmpl.write_text("Pages: {{ page_numbers | join(', ') }}\n{{ text }}")
        result = render_prompt(tmpl, {"page_numbers": [1, 2], "text": "hello"})
        assert "Pages: 1, 2" in result
        assert "hello" in result

    def test_render_fusion_sources(self, tmp_path):
        tmpl = tmp_path / "fusion.md"
        tmpl.write_text(
            "{% for s in sources %}=== {{ s.engine }} ===\n{{ s.text }}\n{% endfor %}"
        )
        sources = [
            {"engine": "tesseract", "text": "AAA"},
            {"engine": "paddle", "text": "BBB"},
        ]
        result = render_prompt(tmpl, {"sources": sources})
        assert "=== tesseract ===" in result
        assert "BBB" in result

    def test_default_variables(self):
        v = default_variables(text="hi", page_numbers=[1], filename="doc.pdf")
        assert v["text"] == "hi"
        assert v["filename"] == "doc.pdf"
        assert v["sources"] == []

    def test_missing_template(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            render_prompt(tmp_path / "nope.md", {})


# ── Plain text formatter ─────────────────────────────────────────────────────

from prism.formatters.plain_text import format_plain_text


class TestPlainTextFormatter:
    def test_merge_chunks(self, tmp_path):
        out = tmp_path / "output.txt"
        format_plain_text(["Hello world", "Second page"], out)
        text = out.read_text()
        assert "Hello world" in text
        assert "Second page" in text
        assert "\n\n" in text

    def test_page_breaks(self, tmp_path):
        out = tmp_path / "output.txt"
        format_plain_text(["A", "B"], out, {"preserve_page_breaks": True})
        text = out.read_text()
        assert "\f\n" in text

    def test_empty_chunks_skipped(self, tmp_path):
        out = tmp_path / "output.txt"
        format_plain_text(["A", "", "  ", "B"], out)
        text = out.read_text()
        assert text == "A\n\nB"


# ── Engine registry ──────────────────────────────────────────────────────────

from prism.engines.registry import list_engines, get_engine


class TestEngineRegistry:
    def test_list_engines(self):
        engines = list_engines()
        assert "tesseract" in engines
        # Other engines only appear if their library is installed

    def test_unknown_engine(self):
        with pytest.raises(ValueError, match="Unknown OCR engine"):
            get_engine("nonexistent")


# ── Backend registry ─────────────────────────────────────────────────────────

from prism.backends.registry import get_backend


class TestBackendRegistry:
    def test_unknown_backend(self):
        with pytest.raises(ValueError, match="Unknown LLM backend"):
            get_backend("nonexistent")

    def test_openrouter_requires_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
                get_backend("openrouter", model="x/y")

    def test_openrouter_requires_model(self):
        with pytest.raises(ValueError, match="model"):
            get_backend("openrouter")


# ── JSONL emitter ────────────────────────────────────────────────────────────

from prism.jsonl import emit


class TestJSONL:
    def test_emit(self, capsys):
        msg = ProgressMessage(
            job_id="j1", chunk=0, layer=0, status="running", message="hi"
        )
        emit(msg)
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["type"] == "progress"
        assert data["message"] == "hi"


# ── Pipeline validation via CLI ──────────────────────────────────────────────

from click.testing import CliRunner
from prism.cli import main


class TestCLI:
    def test_validate_good(self, tmp_path):
        config = {
            "name": "Test",
            "layers": [
                {"id": "ocr1", "type": "ocr", "engine": "tesseract"},
                {"id": "out", "type": "output", "format": "txt"},
            ],
        }
        cfg_file = tmp_path / "pipeline.json"
        cfg_file.write_text(json.dumps(config))

        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "Valid pipeline" in result.output

    def test_validate_bad(self, tmp_path):
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text('{"layers": [{"id": "x"}]}')  # missing type

        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--config", str(cfg_file)])
        assert result.exit_code == 1
        assert "Invalid pipeline" in result.output

    def test_engines_command(self):
        runner = CliRunner()
        result = runner.invoke(main, ["engines"])
        assert result.exit_code == 0
        assert "tesseract" in result.output

    def test_status_no_job(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--job", str(tmp_path)])
        assert result.exit_code != 0  # no manifest.json

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert "0.1.0" in result.output


# ── Example config files parse correctly ─────────────────────────────────────


class TestExampleConfigs:
    def test_quick_tesseract(self):
        path = Path(__file__).parent.parent / "examples" / "quick_tesseract.json"
        raw = json.loads(path.read_text())
        pc = PipelineConfig.model_validate(raw)
        assert pc.name == "Quick Tesseract"
        assert len(pc.layers) == 2

    def test_dual_ocr_fusion(self):
        path = Path(__file__).parent.parent / "examples" / "dual_ocr_fusion.json"
        raw = json.loads(path.read_text())
        pc = PipelineConfig.model_validate(raw)
        assert pc.name == "Dual OCR Fusion"
        assert len(pc.layers) == 5
        fusion = [l for l in pc.layers if l.type == LayerType.FUSION]
        assert len(fusion) == 1
        assert fusion[0].source_layers == ["ocr-tesseract", "ocr-paddle"]


# ── Engine execution (mocked) ────────────────────────────────────────────────


class TestEngineExecution:
    def test_tesseract_engine_calls_pytesseract(self, tmp_path):
        import sys

        mock_pytesseract = MagicMock()
        mock_pytesseract.image_to_string.return_value = "Hello World"
        sys.modules["pytesseract"] = mock_pytesseract

        try:
            # Re-import to pick up the mocked module
            from prism.engines.tesseract import TesseractEngine

            engine = TesseractEngine()
            img = tmp_path / "test.png"
            img.write_bytes(b"fake")
            result = engine.run(img, {"languages": ["eng"]})

            assert result == "Hello World"
            mock_pytesseract.image_to_string.assert_called_once_with(str(img), lang="eng")
        finally:
            del sys.modules["pytesseract"]

    def test_paddle_engine_calls_paddleocr(self, tmp_path):
        import sys

        mock_paddleocr_mod = MagicMock()
        mock_ocr_instance = MagicMock()
        # Mock predict() (new API) to raise so it falls back to ocr() (old API)
        mock_ocr_instance.predict.side_effect = AttributeError("no predict")
        mock_ocr_instance.ocr.return_value = [
            [
                [[[0, 0], [100, 0], [100, 20], [0, 20]], ("Line one", 0.99)],
                [[[0, 30], [100, 30], [100, 50], [0, 50]], ("Line two", 0.95)],
            ]
        ]
        mock_paddleocr_mod.PaddleOCR.return_value = mock_ocr_instance
        sys.modules["paddleocr"] = mock_paddleocr_mod

        try:
            from prism.engines.paddle import PaddleEngine

            engine = PaddleEngine()
            engine._ocr = None  # force re-init
            img = tmp_path / "test.png"
            img.write_bytes(b"fake")
            result = engine.run(img, {"languages": ["en"]})

            assert "Line one" in result
            assert "Line two" in result
        finally:
            del sys.modules["paddleocr"]


# ── Pipeline engine (integration with mocks) ─────────────────────────────────


class TestPipelineEngine:
    def _setup_job(self, tmp_path):
        """Create a minimal job directory with fake rasterized pages."""
        job_dir = tmp_path / "job-test"
        pages_dir = job_dir / "pages"
        pages_dir.mkdir(parents=True)

        # Create fake page images
        from PIL import Image
        for i in range(1, 4):
            img = Image.new("RGB", (100, 100), "white")
            img.save(pages_dir / f"page-{i:03d}.png")

        pipeline = PipelineConfig(
            name="Test Pipeline",
            chunking=ChunkingConfig(strategy=ChunkingStrategy.PAGE),
            layers=[
                LayerConfig(id="ocr1", type=LayerType.OCR, engine="tesseract"),
                LayerConfig(id="out", type=LayerType.OUTPUT, format="txt"),
            ],
        )

        chunks = [
            ChunkState(id=f"chunk-{i:03d}", pages=[i])
            for i in range(1, 4)
        ]

        manifest = JobManifest(
            job_id="job-test",
            input_file="input/test.pdf",
            total_pages=3,
            pipeline=pipeline,
            chunking=pipeline.chunking,
            chunks=chunks,
        )

        (job_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
        return job_dir, manifest

    def _mock_engine(self, return_value="Page text here."):
        """Create a mock OCR engine."""
        mock_eng = MagicMock()
        mock_eng.run.return_value = return_value
        return mock_eng

    def test_full_run_with_mocked_ocr(self, tmp_path):
        from prism.engine import PipelineEngine

        job_dir, manifest = self._setup_job(tmp_path)

        mock_eng = self._mock_engine("Page text here.")
        with patch("prism.engine.get_engine", return_value=mock_eng):
            engine = PipelineEngine(job_dir, manifest)
            engine.run()

        # Check completion
        assert manifest.status == JobStatus.COMPLETE
        assert all(c.status == ChunkStatus.COMPLETE for c in manifest.chunks)

        # Check output files exist
        for i in range(1, 4):
            chunk_dir = job_dir / "chunks" / f"chunk-{i:03d}"
            assert (chunk_dir / "layer-0.tesseract.txt").exists()
            assert (chunk_dir / "layer-1.txt.txt").exists()

        # Check final merged output
        final = job_dir / "final" / "merged-output.txt"
        assert final.exists()
        text = final.read_text()
        assert "Page text here." in text

    def test_resume_after_halt(self, tmp_path):
        from prism.engine import PipelineEngine

        job_dir, manifest = self._setup_job(tmp_path)

        # Simulate: chunk 1 complete, chunk 2 halted at layer 0
        manifest.chunks[0].status = ChunkStatus.COMPLETE
        manifest.chunks[0].layers_completed = [0, 1]
        manifest.chunks[1].status = ChunkStatus.HALTED
        manifest.chunks[1].halted_at_layer = 0
        manifest.status = JobStatus.HALTED

        # Write chunk-001 output so it's already "done"
        c1_dir = job_dir / "chunks" / "chunk-001"
        c1_dir.mkdir(parents=True)
        (c1_dir / "layer-0.tesseract.txt").write_text("Page 1 text")
        (c1_dir / "layer-1.txt.txt").write_text("Page 1 text")

        mock_eng = self._mock_engine("Resumed text.")
        with patch("prism.engine.get_engine", return_value=mock_eng):
            engine = PipelineEngine(job_dir, manifest)
            engine.run()

        assert manifest.status == JobStatus.COMPLETE
        # Chunk 1 was skipped (already complete)
        # Chunks 2 and 3 were processed
        assert manifest.chunks[1].status == ChunkStatus.COMPLETE
        assert manifest.chunks[2].status == ChunkStatus.COMPLETE

    def test_error_halts_job(self, tmp_path):
        from prism.engine import PipelineEngine

        job_dir, manifest = self._setup_job(tmp_path)

        mock_eng = MagicMock()
        mock_eng.run.side_effect = RuntimeError("OCR crashed")
        with patch("prism.engine.get_engine", return_value=mock_eng):
            engine = PipelineEngine(job_dir, manifest)
            engine.run()

        assert manifest.status == JobStatus.HALTED
        assert manifest.chunks[0].status == ChunkStatus.HALTED
        assert manifest.chunks[0].error == "OCR crashed"
        assert manifest.chunks[0].halted_at_layer == 0

    def test_manifest_saved_on_disk(self, tmp_path):
        from prism.engine import PipelineEngine, load_job

        job_dir, manifest = self._setup_job(tmp_path)

        mock_eng = self._mock_engine("text")
        with patch("prism.engine.get_engine", return_value=mock_eng):
            engine = PipelineEngine(job_dir, manifest)
            engine.run()

        # Reload from disk and verify
        _, reloaded = load_job(job_dir)
        assert reloaded.status == JobStatus.COMPLETE
        assert reloaded.job_id == "job-test"
        assert len(reloaded.chunks) == 3


# ── Prompt files in repo ─────────────────────────────────────────────────────


class TestPromptFiles:
    def test_ocr_correction_renders(self):
        path = Path(__file__).parent.parent / "prompts" / "ocr_correction.md"
        vars = default_variables(
            text="sarnple text", page_numbers=[1, 2], filename="doc.pdf"
        )
        result = render_prompt(path, vars)
        assert "sarnple text" in result
        assert "1, 2" in result
        assert "doc.pdf" in result

    def test_fusion_renders(self):
        path = Path(__file__).parent.parent / "prompts" / "fusion.md"
        sources = [
            {"engine": "tesseract", "text": "AAA"},
            {"engine": "paddle", "text": "BBB"},
        ]
        vars = default_variables(
            page_numbers=[3], filename="test.pdf", sources=sources
        )
        result = render_prompt(path, vars)
        assert "tesseract" in result
        assert "AAA" in result
        assert "BBB" in result
