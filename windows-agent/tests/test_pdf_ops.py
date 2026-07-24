"""Milestone 3 — pdf.* executor (read-only).

Two layers of tests:
  1. The PdfExecutor in isolation (each action's success + failure paths).
  2. The full pipeline via the Dispatcher (AllowAllPolicy, no pdf verifiers),
     proving execution succeeds and verification is SKIPPED for these read-only
     actions.

Test PDFs are built on the fly with PyMuPDF itself (`fitz`) inside `tmp_path`, so
the tests are fully self-contained — no committed binary fixtures.
"""

import fitz

from windows_agent.contracts import Action, ActionStatus, VerificationStatus
from windows_agent.execution import ActionRegistry, Dispatcher
from windows_agent.executors.pdf_ops import PdfExecutor, register_pdf_executor
from windows_agent.policy import AllowAllPolicy
from windows_agent.verification import VerificationRegistry


def _action(type_: str, target=None, parameters=None) -> Action:
    return Action(
        action_id="a1",
        task_id="t1",
        sequence=0,
        type=type_,
        target=str(target) if target is not None else None,
        parameters=parameters or {},
        reason="test",
    )


def _make_pdf(path, pages, *, metadata=None, encrypt_pw=None) -> None:
    """Write a small PDF with one page per string in `pages`.

    Text is inserted line-by-line so multi-line content stays within the page
    bounds and is therefore fully extractable by `get_text()`.
    """
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        y = 72
        for line in text.split("\n"):
            page.insert_text((72, y), line)
            y += 14
    if metadata:
        doc.set_metadata(metadata)
    if encrypt_pw:
        doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256,
                 user_pw=encrypt_pw, owner_pw=encrypt_pw)
    else:
        doc.save(str(path))
    doc.close()


# ── PdfExecutor unit tests ─────────────────────────────────────────────────
async def test_page_count(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["one", "two", "three"])
    res = await PdfExecutor().execute(_action("pdf.page_count", pdf))
    assert res.success is True
    assert res.evidence["page_count"] == 3


async def test_get_metadata_round_trip(tmp_path):
    pdf = tmp_path / "meta.pdf"
    meta = {
        "title": "The Title",
        "author": "An Author",
        "subject": "A Subject",
        "keywords": "alpha, beta",
        "creator": "The Creator",
        "producer": "The Producer",
    }
    _make_pdf(pdf, ["page"], metadata=meta)
    res = await PdfExecutor().execute(_action("pdf.get_metadata", pdf))
    assert res.success is True
    got = res.evidence["metadata"]
    assert got["title"] == "The Title"
    assert got["author"] == "An Author"
    assert got["subject"] == "A Subject"
    assert got["keywords"] == "alpha, beta"
    assert got["page_count"] == 1


async def test_get_metadata_missing_fields_are_null(tmp_path):
    pdf = tmp_path / "bare.pdf"
    _make_pdf(pdf, ["page"])  # no metadata set
    res = await PdfExecutor().execute(_action("pdf.get_metadata", pdf))
    assert res.success is True
    assert res.evidence["metadata"]["title"] is None
    assert res.evidence["metadata"]["page_count"] == 1


async def test_read_text_single_page(tmp_path):
    pdf = tmp_path / "pages.pdf"
    _make_pdf(pdf, ["Alpha page", "Beta page", "Gamma page"])
    res = await PdfExecutor().execute(_action("pdf.read_text", pdf, {"page": 1}))
    assert res.success is True
    assert "Beta page" in res.evidence["text"]
    assert "Alpha" not in res.evidence["text"]
    assert res.evidence["pages_read"] == 1
    assert res.evidence["truncated"] is False


async def test_read_text_page_range(tmp_path):
    pdf = tmp_path / "pages.pdf"
    _make_pdf(pdf, ["Alpha page", "Beta page", "Gamma page"])
    res = await PdfExecutor().execute(
        _action("pdf.read_text", pdf, {"start_page": 0, "end_page": 1})
    )
    assert res.success is True
    assert "Alpha page" in res.evidence["text"]
    assert "Beta page" in res.evidence["text"]
    assert "Gamma" not in res.evidence["text"]
    assert res.evidence["pages_read"] == 2


async def test_read_text_whole_document_by_default(tmp_path):
    pdf = tmp_path / "pages.pdf"
    _make_pdf(pdf, ["Alpha page", "Beta page", "Gamma page"])
    res = await PdfExecutor().execute(_action("pdf.read_text", pdf))
    assert res.success is True
    assert res.evidence["pages_read"] == 3
    assert "Gamma page" in res.evidence["text"]


async def test_read_text_bounded(tmp_path):
    pdf = tmp_path / "big.pdf"
    _make_pdf(pdf, ["\n".join(["Hello World"] * 40)])
    res = await PdfExecutor().execute(
        _action("pdf.read_text", pdf, {"max_chars": 50})
    )
    assert res.success is True
    assert res.evidence["truncated"] is True
    assert len(res.evidence["text"]) == 50


async def test_search_hit_counts_and_pages(tmp_path):
    pdf = tmp_path / "search.pdf"
    _make_pdf(pdf, ["apple apple banana", "apple", "cherry"])
    res = await PdfExecutor().execute(
        _action("pdf.search", pdf, {"query": "apple"})
    )
    assert res.success is True
    assert res.evidence["matches"] == [{"page": 0, "count": 2}, {"page": 1, "count": 1}]
    assert res.evidence["total_matches"] == 3
    assert res.evidence["truncated"] is False


async def test_search_respects_max_results(tmp_path):
    pdf = tmp_path / "search.pdf"
    _make_pdf(pdf, ["apple apple", "apple", "apple"])
    res = await PdfExecutor().execute(
        _action("pdf.search", pdf, {"query": "apple", "max_results": 1})
    )
    assert res.success is True
    assert len(res.evidence["matches"]) == 1
    assert res.evidence["truncated"] is True


# ── error paths (structured failures, never exceptions) ─────────────────────
async def test_missing_file(tmp_path):
    res = await PdfExecutor().execute(_action("pdf.page_count", tmp_path / "nope.pdf"))
    assert res.success is False
    assert res.error.code == "file_not_found"


async def test_not_a_pdf(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is definitely not a pdf file")
    res = await PdfExecutor().execute(_action("pdf.page_count", junk))
    assert res.success is False
    assert res.error.code == "not_a_pdf"


async def test_encrypted_pdf_fails_closed(tmp_path):
    pdf = tmp_path / "locked.pdf"
    _make_pdf(pdf, ["secret"], encrypt_pw="hunter2")
    res = await PdfExecutor().execute(_action("pdf.read_text", pdf))
    assert res.success is False
    assert res.error.code == "encrypted_pdf"


async def test_read_text_page_out_of_range(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["only one page"])
    res = await PdfExecutor().execute(_action("pdf.read_text", pdf, {"page": 5}))
    assert res.success is False
    assert res.error.code == "page_out_of_range"


async def test_read_text_rejects_bad_index(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["a page"])
    res = await PdfExecutor().execute(_action("pdf.read_text", pdf, {"page": -1}))
    assert res.success is False
    assert res.error.code == "invalid_parameters"


async def test_search_empty_query(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["some words here"])
    res = await PdfExecutor().execute(_action("pdf.search", pdf, {"query": "   "}))
    assert res.success is False
    assert res.error.code == "invalid_parameters"


# ── End-to-end via the Dispatcher (policy + execution, verification SKIPPED) ─
def _pipeline():
    reg = ActionRegistry()
    register_pdf_executor(reg)
    # No pdf verifiers exist (read-only actions) → registry returns SKIPPED.
    vreg = VerificationRegistry()
    return Dispatcher(reg, AllowAllPolicy(), verification=vreg)


async def test_pipeline_page_count_skips_verification(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["a", "b"])
    result = await _pipeline().dispatch(_action("pdf.page_count", pdf))
    assert result.status == ActionStatus.SUCCESS
    assert result.evidence["page_count"] == 2
    # Read-only → no verifier registered → SKIPPED.
    assert result.verification.status == VerificationStatus.SKIPPED


async def test_pipeline_read_text_bounded_evidence(tmp_path):
    pdf = tmp_path / "big.pdf"
    # ~2400 extractable chars across 5 pages → exceeds the dispatcher's string cap.
    _make_pdf(pdf, ["\n".join(["Hello World"] * 40)] * 5)
    result = await _pipeline().dispatch(_action("pdf.read_text", pdf))
    assert result.status == ActionStatus.SUCCESS
    assert result.verification.status == VerificationStatus.SKIPPED
    # The dispatcher bounds long strings before they leave the pipeline.
    assert result.evidence["text"].endswith("…[truncated]")


async def test_pipeline_search_failure_is_failed(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, ["hello"])
    result = await _pipeline().dispatch(_action("pdf.search", pdf, {"query": ""}))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "invalid_parameters"


async def test_register_pdf_executor_covers_all_types():
    reg = ActionRegistry()
    register_pdf_executor(reg)
    for t in ("pdf.page_count", "pdf.get_metadata", "pdf.read_text", "pdf.search"):
        assert reg.has_action(t)
