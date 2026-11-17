"""Parse vendor PDFs from data/raw/manuals/ into page and table chunks (plan batch B-0).

    uv run python -m it_procurement_api.documents ingest          # the nine B-0 guides
    uv run python -m it_procurement_api.documents list
    uv run python -m it_procurement_api.documents show LP0785_XXV710_DA2_Product_Guide 3

Each page gives one TEXT chunk (the page text) and one TABLE chunk per table the
parser finds. Quarter-turn text is restored before extracting matrix headers.
Following footnote text is retained as explicitly unverified context, not as an
automatically resolved cell-to-note relation. Cross-page tables are not merged.
Table ordinals can change between extraction versions; re-read references after
re-ingestion. Parsed chunks still need checking against the original PDF.
"""

import argparse
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .db import new_session
from .db.models import DocumentChunk, DocumentVersion
from .fixtures import IMPORTER, TENANT_ID, fixture_id
from .settings import REPO_ROOT

DOCS_ROOT = REPO_ROOT / "data" / "raw" / "manuals"
EXTRACTION_VERSION = "pdfplumber-page-table-v2"

# Guides needed by the first investigation tasks (docs/业务B-Agent实施方案.md, T1-T3).
B0_PATHS = (
    "gpu_server/ThinkSystem_SR675_V3/LP1611_SR675_V3_Product_Guide.pdf",
    "gpu/ThinkSystem_NVIDIA_H100_PCIe/LP1732_H100_PCIe_Product_Guide.pdf",
    "driver/NVIDIA_R620/NVIDIA_Data_Center_GPU_Driver_Release_Notes_620_v1.0.pdf",
    "nic/ThinkSystem_XXV710_DA2/LP0785_XXV710_DA2_Product_Guide.pdf",
    "switch/ThinkSystem_NE2572/LP0608_NE2572_Product_Guide.pdf",
    "storage_server/ThinkSystem_SR650_V3/LP1601_SR650_V3_Product_Guide.pdf",
    "storage_server/ThinkSystem_SR650_V3/SR650_V3_System_Configuration_Guide.pdf",
    "ssd/ThinkSystem_5400_PRO_SATA/LP1634_5400_PRO_SSD_Product_Guide.pdf",
    "memory/ThinkSystem_TruDDR5/LP1021_ThinkSystem_Memory_Summary.pdf",
)


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class ChunkData:
    page_no: int
    ordinal: int  # 0 = page text, 1.. = tables in reading order
    kind: str  # TEXT | TABLE
    content: str
    table_header: list[str] | None = None


def clean_cell(cell: str | None) -> str:
    """A table cell as one line: no line breaks, single spaces."""
    return re.sub(r"\s+", " ", cell or "").strip()


def table_to_chunk(rows: list[list[str | None]]) -> tuple[list[str], str] | None:
    """Returns (header, text) for a usable table, or None.

    A usable table has at least two non-empty rows and two columns; anything
    smaller is usually a layout box, not data. The first row is taken as the header.
    """
    cleaned = [[clean_cell(cell) for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(row)]
    if len(cleaned) < 2 or max(len(row) for row in cleaned) < 2:
        return None
    return cleaned[0], "\n".join(" | ".join(row) for row in cleaned)


def page_chunks(page_no: int, page_text: str, tables: list[list[list[str | None]]]) -> list[ChunkData]:
    chunks: list[ChunkData] = []
    if page_text.strip():
        chunks.append(ChunkData(page_no, 0, "TEXT", page_text.strip()))
    ordinal = 0
    for rows in tables:
        usable = table_to_chunk(rows)
        if usable:
            ordinal += 1
            header, content = usable
            chunks.append(ChunkData(page_no, ordinal, "TABLE", content, header))
    return chunks


def extract_oriented_text(chars: list[dict]) -> str:
    """Read horizontal and +/-90-degree text in its actual writing direction.

    Transform character boxes, not strings: reversing a string breaks words and
    cannot reliably handle multiline headers. Other orientations use the normal
    pdfplumber extraction. Groups are kept separate to avoid merging columns.
    """
    from pdfplumber.utils import extract_text

    groups: dict[int, list[dict]] = {0: [], 90: [], -90: []}
    for char in chars:
        a, b, c, d, _, _ = char.get("matrix", (1, 0, 0, 1, 0, 0))
        direction = 0
        if abs(a) < 1e-6 and abs(d) < 1e-6 and b * c < 0:
            direction = 90 if b > 0 else -90
        if direction == 90:
            x0, x1, top, bottom = -char["bottom"], -char["top"], char["x0"], char["x1"]
        elif direction == -90:
            x0, x1, top, bottom = char["top"], char["bottom"], -char["x1"], -char["x0"]
        else:
            groups[0].append(char)
            continue
        groups[direction].append({
            **char, "x0": x0, "x1": x1, "top": top, "bottom": bottom,
            "doctop": top, "upright": True,
        })
    return "\n".join(extract_text(group) or "" for group in groups.values() if group)


def is_page_background(obj: dict, width: float, height: float) -> bool:
    """Only discard large, white, unstroked fill rectangles from table detection.

    Lenovo PDFs draw grid lines with thin filled rectangles, so lines_strict
    would remove the real grid too. Cell shading and actual borders are kept.
    """
    white = obj.get("non_stroking_color") in (1, (1,), (1, 1, 1), (0, 0, 0, 0))
    return bool(
        obj.get("object_type") == "rect" and obj.get("fill") and not obj.get("stroke")
        and white and obj.get("width", 0) > width / 2 and obj.get("height", 0) > height / 2
    )


def chars_in_box(chars: list[dict], box: tuple) -> list[dict]:
    """Assign characters by their centers, without clipping neighboring cells."""
    x0, top, x1, bottom = box
    return [char for char in chars
            if x0 <= (char["x0"] + char["x1"]) / 2 < x1
            and top <= (char["top"] + char["bottom"]) / 2 < bottom]


def extract_table_rows(table) -> list[list[str | None]]:
    """Extract individual cells and flatten the two-level rotated matrix header.

    For a merged parent heading, use its geometric span to find the parent of
    each leaf column. Missing cells remain empty; support values are never filled.
    """
    rows, rotated_rows = [], []
    for row in table.rows:
        chars = chars_in_box(table.page.chars, row.bbox)
        cells = [chars_in_box(chars, box) if box else [] for box in row.cells]
        rotated_rows.append(any(not char["upright"] for cell in cells for char in cell))
        rows.append([extract_oriented_text(cell) if box else None for cell, box in zip(cells, row.cells)])
    if len(rows) >= 2 and not rotated_rows[0] and rotated_rows[1]:
        header = []
        for index, leaf in enumerate(rows[1]):
            box = table.rows[1].cells[index]
            parent = rows[0][index]
            if box:
                midpoint = (box[0] + box[2]) / 2
                parent = next((rows[0][i] for i, cell in enumerate(table.rows[0].cells)
                               if cell and cell[0] <= midpoint < cell[2]), None)
            header.append(" / ".join(clean_cell(value) for value in (parent, leaf) if clean_cell(value)))
        rows = [header, *rows[2:]]
    return rows


def extract_page_chunks(page_no: int, page) -> list[ChunkData]:
    """Separate real grids, restore rotated text, and retain nearby footnotes."""
    page_text = extract_oriented_text(page.chars)
    grids = page.filter(lambda obj: not is_page_background(obj, page.width, page.height))
    tables = sorted(grids.find_tables(), key=lambda table: (table.bbox[1], table.bbox[0]))
    chunks = page_chunks(page_no, page_text, [])
    ordinal = 0
    for index, table in enumerate(tables):
        usable = table_to_chunk(extract_table_rows(table))
        if usable is None:
            continue
        header, content = usable
        # Keep following notes outside the grid. Do not label them as proven
        # restrictions on this particular table or silently drop their markers.
        end = tables[index + 1].bbox[1] if index + 1 < len(tables) else page.height - 40
        if end > table.bbox[3]:
            following = extract_oriented_text(chars_in_box(
                page.chars, (table.bbox[0], table.bbox[3], table.bbox[2], end),
            ))
            following = re.split(r"(?m)^Table \d+\.", following, maxsplit=1)[0].strip()
            if re.match(r"^(?:[*†‡]|\d+[.)]?\s)", following):
                content += "\n\nFollowing page notes (verify applicability):\n" + following
        ordinal += 1
        chunks.append(ChunkData(page_no, ordinal, "TABLE", content, header))
    return chunks


def parse_pdf(path: Path, max_pages: int | None = None) -> Iterator[list[ChunkData]]:
    """Yields the chunks of each page, in page order."""
    import pdfplumber  # imported here so the rest of the module works without it

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            if max_pages is not None and page_no > max_pages:
                return
            yield extract_page_chunks(page_no, page)
            page.flush_cache()  # large guides: do not keep every page's layout in memory


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def catalog_entries() -> dict[str, dict]:
    catalog = json.loads((DOCS_ROOT / "catalog.json").read_text(encoding="utf-8"))
    return {entry["path"]: entry for entry in catalog["documents"]}


def ingest_document(session: Session, entry: dict, max_pages: int | None = None) -> dict:
    """Parse one catalogued PDF and store its chunks. Re-running replaces the chunks.

    The file must match the sha256 recorded when it was downloaded. The caller
    owns the transaction.
    """
    path = DOCS_ROOT / entry["path"]
    if not path.is_file():
        raise DocumentError(f"missing file: {entry['path']}")
    digest = file_sha256(path)
    if digest != entry["sha256"]:
        raise DocumentError(f"sha256 mismatch for {entry['path']}: the file differs from the catalogued download")

    key = path.stem
    captured_at = datetime.fromisoformat(entry["captured_at_utc"])
    base = dict(tenant_id=TENANT_ID, created_by=fixture_id("principal", IMPORTER))
    version_id = fixture_id("document", f"{key}:{digest}")
    version = session.get(DocumentVersion, version_id)
    if version is None:
        version = DocumentVersion(
            id=version_id, document_key=key, revision=captured_at.date().isoformat(),
            source_uri=entry["source_url"], object_uri=entry["path"], content_hash=digest,
            captured_at=captured_at, applicable_models=[entry["model"]], doc_kind=entry["kind"],
            extraction_version=EXTRACTION_VERSION, **base,
        )
        session.add(version)
        session.flush()
    else:
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == version_id))
        version.extraction_version = EXTRACTION_VERSION

    pages = tables = 0
    for chunks in parse_pdf(path, max_pages):
        pages += 1
        tables += sum(1 for chunk in chunks if chunk.kind == "TABLE")
        session.add_all([
            DocumentChunk(
                id=uuid.uuid4(), document_version_id=version_id, page_no=chunk.page_no, ordinal=chunk.ordinal,
                kind=chunk.kind, content=chunk.content, table_header=chunk.table_header,
                content_hash=sha256(chunk.content.encode("utf-8")).hexdigest(), **base,
            )
            for chunk in chunks
        ])
        session.flush()
    version.page_count = pages
    session.flush()
    return {"document_key": key, "pages": pages, "tables": tables}


def find_document(session: Session, document_key: str) -> DocumentVersion:
    version = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.document_key == document_key,
            DocumentVersion.status == "ACTIVE",
        )
    ).first()
    if version is None:
        raise DocumentError(f"document not ingested: {document_key}")
    return version


def page_of(session: Session, document_key: str, page_no: int) -> list[DocumentChunk]:
    """Chunks of one page, text first, for checking against the PDF."""
    version = find_document(session, document_key)
    return list(session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == version.id, DocumentChunk.page_no == page_no)
        .order_by(DocumentChunk.ordinal)
    ))


def search_chunks(
    session: Session, query: str, document_key: str | None = None, limit: int = 10
) -> list[tuple[DocumentVersion, DocumentChunk]]:
    """Chunks containing every word of the query (case-insensitive), tables first.

    Plain substring matching: good for exact part numbers and model names.
    There is no ranking by relevance yet.
    """
    terms = query.split()
    if not terms:
        return []
    statement = (
        select(DocumentVersion, DocumentChunk)
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .where(DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.status == "ACTIVE")
    )
    for term in terms:
        statement = statement.where(DocumentChunk.content.icontains(term, autoescape=True))
    if document_key:
        statement = statement.where(DocumentVersion.document_key == document_key)
    statement = statement.order_by(
        DocumentChunk.kind, DocumentVersion.document_key, DocumentChunk.page_no, DocumentChunk.ordinal
    ).limit(limit)
    return [(version, chunk) for version, chunk in session.execute(statement)]


def matching_lines(content: str, query: str, limit: int = 3) -> list[str]:
    """The first lines of a chunk that mention any query word, for a short preview."""
    terms = [term.lower() for term in query.split()]
    lines = [line for line in content.splitlines() if any(term in line.lower() for term in terms)]
    return [line[:200] for line in lines[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m it_procurement_api.documents")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="parse PDFs; with no paths, the nine B-0 guides")
    ingest.add_argument("paths", nargs="*", help="paths relative to data/raw/manuals/, as in catalog.json")
    commands.add_parser("list", help="ingested documents with page and chunk counts")
    show = commands.add_parser("show", help="print the chunks of one page")
    show.add_argument("document_key")
    show.add_argument("page", type=int)
    search = commands.add_parser("search", help="chunks containing every word, e.g. a part number")
    search.add_argument("query")
    search.add_argument("--document", help="limit to one document key")
    search.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    with new_session() as session:
        if args.command == "search":
            for version, chunk in search_chunks(session, args.query, args.document, args.limit):
                print(f"{version.document_key} p.{chunk.page_no} #{chunk.ordinal} {chunk.kind}")
                for line in matching_lines(chunk.content, args.query):
                    print(f"    {line}")
            return
        if args.command == "ingest":
            entries = catalog_entries()
            for path in args.paths or B0_PATHS:
                if path not in entries:
                    raise SystemExit(f"not in catalog.json: {path}")
                result = ingest_document(session, entries[path])
                session.commit()  # one document at a time, so a late failure keeps earlier work
                print(f"{result['document_key']}: {result['pages']} pages, {result['tables']} tables")
        elif args.command == "list":
            rows = session.execute(
                select(DocumentVersion.document_key, DocumentVersion.page_count, func.count(DocumentChunk.id))
                .outerjoin(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
                .where(DocumentVersion.tenant_id == TENANT_ID)
                .group_by(DocumentVersion.document_key, DocumentVersion.page_count)
                .order_by(DocumentVersion.document_key)
            ).all()
            for key, pages, chunks in rows:
                print(f"{key}: {pages} pages, {chunks} chunks")
        else:
            for chunk in page_of(session, args.document_key, args.page):
                print(f"--- page {chunk.page_no} #{chunk.ordinal} {chunk.kind} ---")
                print(chunk.content)


if __name__ == "__main__":
    main()
