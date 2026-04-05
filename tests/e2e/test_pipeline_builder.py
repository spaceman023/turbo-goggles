"""End-to-end Playwright test: create a pipeline in the builder, run it, verify output."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

# ── Fixtures ─────────────────────────────────────────────────────────────────

SERVER_PORT = 8450
BASE_URL = f"http://localhost:{SERVER_PORT}"
TEST_PDF = Path(__file__).parent.parent / "fixtures" / "test-2page.pdf"


@pytest.fixture(scope="module")
def server():
    """Start the PRISM web server for the test session."""
    proc = subprocess.Popen(
        ["prism", "ui", "--port", str(SERVER_PORT), "--no-open"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for _ in range(30):
        try:
            import urllib.request
            urllib.request.urlopen(f"{BASE_URL}/api/settings", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("Server failed to start")

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── Helpers ──────────────────────────────────────────────────────────────────

def build_pipeline(page: Page, layers: list[str], name: str = "Test") -> None:
    """Open builder, set name/PDF, add layers from the picker."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    page.click("#btn-new-job")
    expect(page.locator("#view-builder")).to_be_visible()

    page.locator("#builder-name").fill(name)
    page.locator("#builder-workers").fill("1")
    page.locator("#builder-pdf").set_input_files(str(TEST_PDF))

    for label in layers:
        page.click("#btn-add-layer")
        expect(page.locator("#add-layer-picker")).to_be_visible()
        page.click(f"#add-layer-picker .picker-item:has-text('{label}')")

    expect(page.locator(".layer-card")).to_have_count(len(layers))


def run_and_wait(page: Page, timeout: int = 120_000) -> None:
    """Click Run, wait for the detail view to show completion."""
    page.click("#btn-run-pipeline")

    # Wait for detail view with running badge
    expect(page.locator("#view-detail")).to_be_visible(timeout=10_000)
    expect(page.locator("#detail-header .job-badge")).to_be_visible(timeout=10_000)

    # Wait for the badge to change to "complete" (inside detail header only)
    expect(page.locator("#detail-header .job-badge.complete")).to_be_visible(timeout=timeout)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_homepage_loads(server, page: Page):
    page.goto(BASE_URL)
    expect(page.locator("header h1")).to_have_text("PRISM")


def test_backend_health_shows_cli(server, page: Page):
    resp = page.request.get(f"{BASE_URL}/api/backends/health")
    assert resp.ok
    data = resp.json()
    available = [k for k, v in data.items() if v["available"]]
    assert len(available) > 0, f"No backends available: {data}"


def test_builder_opens_and_adds_layers(server, page: Page):
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    page.click("#btn-new-job")
    expect(page.locator("#view-builder")).to_be_visible()

    # Add OCR
    page.click("#btn-add-layer")
    page.click("#add-layer-picker .picker-item:has-text('Tesseract')")
    expect(page.locator(".layer-card")).to_have_count(1)

    # Add output
    page.click("#btn-add-layer")
    page.click("#add-layer-picker .picker-item:has-text('Text')")
    expect(page.locator(".layer-card")).to_have_count(2)

    # Delete first layer
    page.locator(".layer-card").first.locator(".layer-card-delete").click()
    expect(page.locator(".layer-card")).to_have_count(1)


def test_build_and_run_tesseract_only(server, page: Page):
    """Tesseract OCR → text output. No LLM, should complete fast."""
    build_pipeline(page, ["Tesseract", "Text"], name="OCR Only Test")
    run_and_wait(page, timeout=60_000)

    # Verify output rendered
    expect(page.locator("#detail-output-section")).to_be_visible()
    output_text = page.locator("#detail-output").inner_text()
    assert len(output_text) > 20, f"Output too short: {output_text[:100]}"


def test_build_and_run_with_claude_cli(server, page: Page):
    """Tesseract → Claude CLI correction → text output. Full CLI backend test."""
    # Check claude is available
    resp = page.request.get(f"{BASE_URL}/api/backends/health")
    health = resp.json()
    if not health.get("claude", {}).get("available"):
        pytest.skip("Claude CLI not available")

    build_pipeline(page, ["Tesseract", "Claude", "Text"], name="Claude CLI Test")

    # Configure the Claude LLM layer - click it to select
    page.locator(".layer-card").nth(1).click()
    expect(page.locator("#builder-inspector")).to_contain_text("Backend")

    run_and_wait(page, timeout=180_000)

    expect(page.locator("#detail-output-section")).to_be_visible()
    output_text = page.locator("#detail-output").inner_text()
    assert len(output_text) > 20, f"Output too short: {output_text[:100]}"


def test_build_and_run_with_codex_cli(server, page: Page):
    """Tesseract → Codex CLI correction → text output."""
    resp = page.request.get(f"{BASE_URL}/api/backends/health")
    health = resp.json()
    if not health.get("codex", {}).get("available"):
        pytest.skip("Codex CLI not available")

    build_pipeline(page, ["Tesseract", "Codex", "Text"], name="Codex CLI Test")
    page.locator(".layer-card").nth(1).click()

    run_and_wait(page, timeout=180_000)

    expect(page.locator("#detail-output-section")).to_be_visible()
    output_text = page.locator("#detail-output").inner_text()
    assert len(output_text) > 20, f"Output too short: {output_text[:100]}"


def test_build_and_run_with_gemini_cli(server, page: Page):
    """Tesseract → Gemini CLI correction → text output."""
    resp = page.request.get(f"{BASE_URL}/api/backends/health")
    health = resp.json()
    if not health.get("gemini", {}).get("available"):
        pytest.skip("Gemini CLI not available")

    build_pipeline(page, ["Tesseract", "Gemini", "Text"], name="Gemini CLI Test")
    page.locator(".layer-card").nth(1).click()

    run_and_wait(page, timeout=180_000)

    expect(page.locator("#detail-output-section")).to_be_visible()
    output_text = page.locator("#detail-output").inner_text()
    assert len(output_text) > 20, f"Output too short: {output_text[:100]}"


# ── OCR Engine tests ─────────────────────────────────────────────────────────


def _ocr_engine_test(server, page: Page, engine_name: str, picker_label: str):
    """Generic: build pipeline with a specific OCR engine -> text output, run it."""
    resp = page.request.get(f"{BASE_URL}/api/engines/health")
    health = resp.json()
    if not health.get(engine_name, {}).get("available"):
        pytest.skip(f"{engine_name} not installed")

    build_pipeline(page, [picker_label, "Text"], name=f"{engine_name} test")
    # Heavy engines (paddleocr, easyocr, surya, doctr) need longer for first-run model download
    timeout = 300_000 if engine_name in ("paddleocr", "easyocr", "surya", "doctr") else 120_000
    run_and_wait(page, timeout=timeout)

    expect(page.locator("#detail-output-section")).to_be_visible()
    output_text = page.locator("#detail-output").inner_text()
    assert len(output_text) > 10, f"{engine_name} output too short: {output_text[:100]}"


def test_ocr_paddleocr(server, page: Page):
    _ocr_engine_test(server, page, "paddleocr", "PaddleOCR")


def test_ocr_easyocr(server, page: Page):
    _ocr_engine_test(server, page, "easyocr", "EasyOCR")


def test_ocr_macos_vision(server, page: Page):
    _ocr_engine_test(server, page, "macos_vision", "macOS Vision")


def test_ocr_surya(server, page: Page):
    _ocr_engine_test(server, page, "surya", "Surya")


def test_ocr_doctr(server, page: Page):
    _ocr_engine_test(server, page, "doctr", "docTR")


# ── Retry test ───────────────────────────────────────────────────────────────


def test_retry_job(server, page: Page):
    """Run a simple job, then retry it."""
    build_pipeline(page, ["Tesseract", "Text"], name="Retry Source")
    run_and_wait(page, timeout=60_000)
    expect(page.locator("#detail-output-section")).to_be_visible()

    page.click("#detail-header >> text=Retry")
    expect(page.locator("#detail-header .job-badge")).to_be_visible(timeout=10_000)
    expect(page.locator("#detail-header .job-badge.complete")).to_be_visible(timeout=60_000)
    expect(page.locator("#detail-output-section")).to_be_visible()
