"""PDF chunking (plan batch B-0).

The pure functions are tested with plain data. The tests that read a real PDF
are skipped when pdfplumber or the local data/raw/manuals/ files are not available.
"""

import pytest
from sqlalchemy import func, select

from it_procurement_api.db.models import DocumentChunk, DocumentVersion
from it_procurement_api.documents import (
    DOCS_ROOT,
    EXTRACTION_VERSION,
    DocumentError,
    catalog_entries,
    clean_cell,
    extract_oriented_text,
    extract_page_chunks,
    is_page_background,
    ingest_document,
    matching_lines,
    page_chunks,
    page_of,
    search_chunks,
    table_to_chunk,
)
from it_procurement_api.fixtures import TENANT_ID, load_fixture_set

NIC_GUIDE = "nic/ThinkSystem_XXV710_DA2/LP0785_XXV710_DA2_Product_Guide.pdf"


def test_clean_cell_makes_one_line():
    assert clean_cell(" 25Gb\nSFP28  SR ") == "25Gb SFP28 SR"
    assert clean_cell(None) == ""


def test_table_keeps_header_and_rows():
    header, text = table_to_chunk([["Part number", "Description"], ["7G17A03537", "25G SR\nSFP28"], [None, ""]])
    assert header == ["Part number", "Description"]
    assert text == "Part number | Description\n7G17A03537 | 25G SR SFP28"  # empty row dropped


def test_layout_boxes_are_not_tables():
    assert table_to_chunk([["only one row", "x"]]) is None
    assert table_to_chunk([["one column"], ["still one"]]) is None
    assert table_to_chunk([[None, None], ["", ""]]) is None


def test_page_chunks_put_text_first_and_number_tables():
    chunks = page_chunks(4, "  Supported transceivers\n1. Footnote text  ", [[["a"]], [["h1", "h2"], ["1", "2"]]])
    assert [(c.page_no, c.ordinal, c.kind) for c in chunks] == [(4, 0, "TEXT"), (4, 1, "TABLE")]
    assert chunks[0].content == "Supported transceivers\n1. Footnote text"
    assert chunks[1].table_header == ["h1", "h2"]
    assert page_chunks(5, "   ", []) == []


@pytest.fixture
def nic_entry():
    pytest.importorskip("pdfplumber")
    if not (DOCS_ROOT / NIC_GUIDE).is_file():
        pytest.skip("data/raw/manuals/ is not available locally")
    return catalog_entries()[NIC_GUIDE]


NIC_KEY = "LP0785_XXV710_DA2_Product_Guide"


def chunk_count(session) -> int:
    """Chunks of the NIC guide only: the database may already hold ingested documents."""
    return session.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .where(DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.document_key == NIC_KEY)
    )


def versions(session) -> list[DocumentVersion]:
    return list(session.scalars(
        select(DocumentVersion).where(DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.document_key == NIC_KEY)
    ))


def test_ingest_real_guide_is_repeatable(session, nic_entry):
    first = ingest_document(session, nic_entry, max_pages=3)
    assert first["document_key"] == NIC_KEY and first["pages"] == 3
    count = chunk_count(session)
    assert count >= 3  # at least the text of each page

    (version,) = versions(session)
    assert version.content_hash == nic_entry["sha256"] and version.page_count == 3
    assert version.source_uri.startswith("https://")
    assert page_of(session, NIC_KEY, 1)[0].kind == "TEXT"

    ingest_document(session, nic_entry, max_pages=3)
    assert chunk_count(session) == count
    assert len(versions(session)) == 1


def test_changed_file_is_refused(session, nic_entry):
    before = chunk_count(session)
    with pytest.raises(DocumentError, match="sha256 mismatch"):
        ingest_document(session, {**nic_entry, "sha256": "0" * 64}, max_pages=1)
    assert chunk_count(session) == before


def test_search_finds_exact_words_and_respects_document_filter(session, nic_entry):
    ingest_document(session, nic_entry, max_pages=3)
    hits = search_chunks(session, "xxv710 ethernet", document_key=NIC_KEY)
    assert hits and all(version.document_key == NIC_KEY for version, _ in hits)
    assert all("xxv710" in chunk.content.lower() and "ethernet" in chunk.content.lower() for _, chunk in hits)
    assert search_chunks(session, "no-such-part-number-zzz", document_key=NIC_KEY) == []
    assert search_chunks(session, "100%_", document_key=NIC_KEY) == []  # wildcards are literal
    assert search_chunks(session, "   ") == []


def test_matching_lines_preview():
    content = "Part number | Description\n7G17A03537 | 25G SR SFP28\nother"
    assert matching_lines(content, "7g17a03537") == ["7G17A03537 | 25G SR SFP28"]


def test_fixture_reset_keeps_documents(session, nic_entry):
    ingest_document(session, nic_entry, max_pages=1)
    count = chunk_count(session)
    load_fixture_set(session, "FX-A-BASE")
    assert chunk_count(session) == count


# Regression expectations checked against rendered local vendor PDF pages.
SSD_GUIDE = "ssd/ThinkSystem_5400_PRO_SATA/LP1634_5400_PRO_SSD_Product_Guide.pdf"
H100_GUIDE = "gpu/ThinkSystem_NVIDIA_H100_PCIe/LP1732_H100_PCIe_Product_Guide.pdf"
MEMORY_GUIDE = "memory/ThinkSystem_TruDDR5/LP1021_ThinkSystem_Memory_Summary.pdf"


def real_page_chunks(path, page_no):
    pdfplumber = pytest.importorskip("pdfplumber")
    if not (DOCS_ROOT / path).is_file():
        pytest.skip("data/raw/manuals/ is not available locally")
    with pdfplumber.open(DOCS_ROOT / path) as pdf:
        return extract_page_chunks(page_no, pdf.pages[page_no - 1])


def matrix_value(chunk, row_key, column):
    index = chunk.table_header.index(column)
    row = next(line.split(" | ") for line in chunk.content.splitlines() if line.startswith(row_key + " | "))
    assert len(row) == len(chunk.table_header)
    return row[index]


@pytest.mark.parametrize("direction", [90, -90])
def test_quarter_turn_headers_follow_coordinates_not_character_order(direction):
    label = "SR650 V3"
    chars = []
    for index, char in enumerate(label):
        top = 100 - index * 6 if direction == 90 else index * 6
        chars.append({
            "text": char, "x0": 0, "x1": 10, "top": top, "bottom": top + 6,
            "doctop": top, "upright": False, "size": 10,
            "matrix": (0, 1, -1, 0, 0, 0) if direction == 90 else (0, -1, 1, 0, 0, 0),
        })
    assert extract_oriented_text(list(reversed(chars))) == label


def test_only_large_white_unstroked_backgrounds_are_removed():
    background = {"object_type": "rect", "fill": True, "stroke": False,
                  "non_stroking_color": (1, 1, 1), "width": 480, "height": 650}
    assert is_page_background(background, 612, 792)
    assert not is_page_background({**background, "width": 1}, 612, 792)
    assert not is_page_background({**background, "non_stroking_color": (.75, .75, .75)}, 612, 792)
    assert not is_page_background({**background, "stroke": True}, 612, 792)


def test_ssd_matrices_keep_server_columns_separate():
    chunks = real_page_chunks(SSD_GUIDE, 7)
    first, second = [chunk for chunk in chunks if chunk.kind == "TABLE"]
    assert (len(first.table_header), len(second.table_header)) == (22, 12)
    assert first.table_header[:2] == ["Part Number", "Description"]
    assert matrix_value(first, "4XB7A82288", "2S Intel V3/V4 / SR650 V3 (7D75 / 7D76)") == "N"
    assert matrix_value(first, "4XB7A82288", "1S V3 / ST250 V3 (7DCF / 7DCE)") == "Y"
    assert matrix_value(second, "4XB7A82288", "AI Servers / SR675 V3 (7D9Q / 7D9R)") == "N"
    assert "SR650 V3" in chunks[0].content and "056RS" not in chunks[0].content


def test_gpu_matrix_distinguishes_gpu_and_bridge_rows():
    chunks = real_page_chunks(H100_GUIDE, 6)
    first, second = [chunk for chunk in chunks if chunk.kind == "TABLE"]
    column = "AI Servers / SR675 V3 (7D9Q / 7D9R)"
    assert matrix_value(second, "BQQV", column) == "N"
    assert matrix_value(second, "4X67A71309", column) == "Y"
    assert "NVLink bridge (for PCIe adapters only, not SXM; order 3 per pair of GPUs)" in second.content
    assert "Contains 8 separate GPUs" in second.content
    assert len(first.table_header) == 22


def test_memory_matrix_preserves_positive_and_negative_server_values():
    chunks = real_page_chunks(MEMORY_GUIDE, 11)
    table, = [chunk for chunk in chunks if chunk.kind == "TABLE"]
    assert matrix_value(table, "4X77A88052", "2S Intel V3/V4 / SR650 V3 (7D75 / 7D76)") == "Y"
    assert matrix_value(table, "4X77A88052", "1S V3 / ST250 V3 (7DCF / 7DCE)") == "N"


def test_gpu_os_matrix_keeps_footnote_markers_and_following_notes():
    chunks = real_page_chunks(H100_GUIDE, 8)
    table, = [chunk for chunk in chunks if chunk.kind == "TABLE"]
    row = "Ubuntu 22.04 LTS or later with HWE kernel"
    assert matrix_value(table, row, "SR665") == "Y 1"
    assert matrix_value(table, row, "SR655 V3") == "Y 2"
    assert matrix_value(table, row, "SR650 V3 (5th Gen Xeon)") == "Y 4"
    assert "Following page notes (verify applicability):" in table.content
    for text in ("1 HW is not supported with EPYC 7002 processors.", "Support Tip TT1064", "Support Tip TT1591"):
        assert text in table.content and text in chunks[0].content


def test_reingest_persists_restored_headers_and_extraction_version(session):
    real_page_chunks(SSD_GUIDE, 7)  # skip if the original is unavailable
    entry = catalog_entries()[SSD_GUIDE]
    ingest_document(session, entry, max_pages=7)
    key = "LP1634_5400_PRO_SSD_Product_Guide"
    hits = search_chunks(session, "SR650 V3 4XB7A82288", document_key=key)
    version, table = next((version, chunk) for version, chunk in hits if chunk.kind == "TABLE" and chunk.page_no == 7)
    assert version.extraction_version == EXTRACTION_VERSION == "pdfplumber-page-table-v2"
    assert matrix_value(table, "4XB7A82288", "2S Intel V3/V4 / SR650 V3 (7D75 / 7D76)") == "N"
