"""Evidence tools over the parsed vendor documents (plan batch B-1). Read-only.

    uv run python -m it_procurement_api.evidence search "XXV710-DA2 7G17A03537 transceiver"
    uv run python -m it_procurement_api.evidence read LP0785_XXV710_DA2_Product_Guide:p2:1
    uv run python -m it_procurement_api.evidence probe

`list_documents`, `search_documents` and `read_evidence` are the functions the
agent will call as tools; `baseline_context` is the fixed "retrieve once" flow
the agent is compared against. No model is called here.

A chunk is addressed by `<document_key>:p<page>:<ordinal>`. References are
repeatable for the same source and extraction version, but table ordinals can
change when the parser changes. Re-read evidence after re-ingestion and retain
its extraction_version/content_hash with any result. Chunk text is automatically
parsed; a few regression pages were visually checked, not the entire corpus.
"""

import argparse
import json
import re
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import new_session
from .db.models import DocumentChunk, DocumentVersion
from .documents import DocumentError, find_document, matching_lines, search_chunks
from .fixtures import TENANT_ID
from .settings import REPO_ROOT

QUESTIONS_PATH = REPO_ROOT / "evals" / "b1_questions.json"
VERIFICATION = "PARSED_NOT_HUMAN_CHECKED"
RESULTS = ("PASS", "FAIL", "INSUFFICIENT")

_TERM = re.compile(r"[A-Za-z0-9]+(?:[-./+][A-Za-z0-9]+)*")
_REF = re.compile(r"^(?P<key>.+):p(?P<page>\d+):(?P<ordinal>\d+)$")
_STOPWORDS = frozenset(
    "a an and are be can do does for how in is it many of on or the this to what which with".split()
)


class EvidenceError(ValueError):
    pass


# --- query terms and scoring (pure) ---------------------------------------------


def query_terms(query: str, max_terms: int = 12) -> list[str]:
    """Lower-case latin terms of a question, in order, without stop words or repeats.

    The documents are English, so the Chinese words of a question are dropped;
    model names and part numbers are what carries a query.
    """
    terms: list[str] = []
    for raw in _TERM.findall(query):
        term = raw.lower()
        if len(term) >= 2 and term not in _STOPWORDS and term not in terms:
            terms.append(term)
    return terms[:max_terms]


def term_weight(term: str) -> float:
    """Codes count more than words: 6 for part-number-like terms, 3 for short codes, 1 for words."""
    if any(c.isdigit() for c in term) and any(c.isalpha() for c in term):
        if len(term) >= 8:
            return 6.0
        if len(term) >= 4:
            return 3.0
    return 1.0


def term_matches(term: str, lowered_content: str) -> bool:
    """Codes must match as a whole token (7G17A03537 is not found inside 7G17A035371); words match as substrings."""
    if term_weight(term) > 1:
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered_content) is not None
    return term in lowered_content


def score_chunk(content: str, terms: list[str]) -> float:
    lowered = content.lower()
    return sum(term_weight(term) for term in terms if term_matches(term, lowered))


def format_ref(document_key: str, page_no: int, ordinal: int) -> str:
    return f"{document_key}:p{page_no}:{ordinal}"


def parse_ref(ref: str) -> tuple[str, int, int]:
    match = _REF.match(ref.strip())
    if not match:
        raise EvidenceError(f"not a chunk reference: {ref!r} (expected <document_key>:p<page>:<ordinal>)")
    return match["key"], int(match["page"]), int(match["ordinal"])


# --- tools ----------------------------------------------------------------------


def list_documents(session: Session, query: str | None = None) -> list[dict]:
    """Ingested documents; with a query, only those whose key or models contain every term."""
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.status == "ACTIVE")
        .order_by(DocumentVersion.document_key)
    ).all()
    terms = query_terms(query or "")
    documents = []
    for version in versions:
        haystack = " ".join([version.document_key, *map(str, version.applicable_models)]).lower()
        if all(term in haystack for term in terms):
            documents.append({
                "document_key": version.document_key, "doc_kind": version.doc_kind,
                "applicable_models": version.applicable_models, "revision": version.revision,
                "page_count": version.page_count,
            })
    return documents


def search_documents(
    session: Session, query: str, document_key: str | None = None, limit: int = 5, one_per_page: bool = True
) -> list[dict]:
    """Chunks ranked by which query terms they contain; part numbers weigh most.

    A chunk needs at least one term. Ties go to tables, then document, page.
    With `one_per_page`, only the best chunk of each page is returned (the page
    text and its tables repeat each other).
    """
    terms = query_terms(query)
    if not terms:
        return []
    statement = (
        select(DocumentVersion, DocumentChunk)
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .where(
            DocumentVersion.tenant_id == TENANT_ID, DocumentVersion.status == "ACTIVE",
            or_(*[DocumentChunk.content.icontains(term, autoescape=True) for term in terms]),
        )
    )
    if document_key:
        statement = statement.where(DocumentVersion.document_key == document_key)

    scored = []
    for version, chunk in session.execute(statement).all():
        score = score_chunk(chunk.content, terms)
        if score > 0:
            scored.append((score, version, chunk))
    scored.sort(key=lambda row: (
        -row[0], 0 if row[2].kind == "TABLE" else 1, row[1].document_key, row[2].page_no, row[2].ordinal,
    ))

    hits: list[dict] = []
    seen_pages: set[tuple[str, int]] = set()
    for score, version, chunk in scored:
        page = (version.document_key, chunk.page_no)
        if one_per_page and page in seen_pages:
            continue
        seen_pages.add(page)
        hits.append({
            "ref": format_ref(version.document_key, chunk.page_no, chunk.ordinal),
            "document_key": version.document_key, "page_no": chunk.page_no, "kind": chunk.kind,
            "score": score, "preview": matching_lines(chunk.content, " ".join(terms)),
        })
        if len(hits) >= limit:
            break
    return hits


def read_evidence(session: Session, ref: str) -> dict:
    """The full text of one chunk with where it comes from. Evidence is quoted from this."""
    document_key, page_no, ordinal = parse_ref(ref)
    try:
        version = find_document(session, document_key)
    except DocumentError as exc:
        raise EvidenceError(str(exc)) from exc
    chunk = session.scalars(
        select(DocumentChunk).where(
            DocumentChunk.document_version_id == version.id, DocumentChunk.page_no == page_no,
            DocumentChunk.ordinal == ordinal,
        )
    ).one_or_none()
    if chunk is None:
        raise EvidenceError(f"no such chunk: {ref}")
    return {
        "ref": format_ref(document_key, page_no, ordinal), "document_key": document_key,
        "revision": version.revision, "source_uri": version.source_uri, "page_no": page_no,
        "kind": chunk.kind, "table_header": chunk.table_header, "content": chunk.content,
        "content_hash": chunk.content_hash, "extraction_version": version.extraction_version,
        "verification": VERIFICATION,
    }


def baseline_context(session: Session, question: str, limit: int = 5, max_chars: int = 6000) -> dict:
    """Fixed flow: one search with the question, the top chunks, cut to a character budget."""
    per_chunk = max(1, max_chars // max(1, limit))
    chunks = []
    for hit in search_documents(session, question, limit=limit):
        evidence = read_evidence(session, hit["ref"])
        chunks.append({
            "ref": evidence["ref"], "document_key": evidence["document_key"], "page_no": evidence["page_no"],
            "kind": evidence["kind"], "content": evidence["content"][:per_chunk],
            "truncated": len(evidence["content"]) > per_chunk,
        })
    return {"question": question, "terms": query_terms(question), "limit": limit, "chunks": chunks}


# --- question file and probes ---------------------------------------------------


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def probe_pages(session: Session, probe: dict) -> list[int]:
    """Pages of one document whose parsed text contains every word of the probe term."""
    hits = search_chunks(session, probe["term"], probe["document_key"], limit=500)
    return sorted({chunk.page_no for _, chunk in hits})


def check_probe(probe: dict, pages: list[int]) -> str:
    """OK or MISMATCH against the expectation; OBSERVED when the probe has none yet."""
    expect = probe.get("expect")
    if expect is None:
        return "OBSERVED"
    if expect == "miss":
        return "OK" if not pages else "MISMATCH"
    if expect == "hit":
        return "OK" if pages and set(probe.get("pages", [])) <= set(pages) else "MISMATCH"
    raise EvidenceError(f"unknown probe expectation: {expect!r}")


def baseline_recall(session: Session, question: dict, limit: int = 5) -> tuple[int, int]:
    """(found, expected): expected evidence pages that the fixed flow's top chunks include."""
    expected = {(item["document_key"], item["page"]) for item in question.get("evidence", [])}
    found = {(hit["document_key"], hit["page_no"]) for hit in search_documents(session, question["question"], limit=limit)}
    return len(expected & found), len(expected)


def run_probes(session: Session, questions: list[dict]) -> bool:
    """Print every probe result and the baseline recall. Returns False if any probe mismatches."""
    all_ok = True
    for question in questions:
        print(f"{question['id']} [{question['status']}] {question['question']}")
        for probe in question["probes"]:
            pages = probe_pages(session, probe)
            outcome = check_probe(probe, pages)
            all_ok = all_ok and outcome != "MISMATCH"
            print(f"  {outcome:<9} {probe['term']!r} in {probe['document_key']}: pages {pages}")
        if question.get("evidence"):
            found, expected = baseline_recall(session, question)
            print(f"  baseline top-5 pages: {found}/{expected} expected evidence pages")
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m it_procurement_api.evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="ranked chunks for a question or part number")
    search.add_argument("query")
    search.add_argument("--document")
    search.add_argument("--limit", type=int, default=5)
    read = commands.add_parser("read", help="print one chunk by reference")
    read.add_argument("ref")
    probe = commands.add_parser("probe", help="check the question file against the ingested documents")
    probe.add_argument("path", nargs="?", type=Path, default=QUESTIONS_PATH)
    args = parser.parse_args()

    with new_session() as session:
        if args.command == "search":
            for hit in search_documents(session, args.query, args.document, args.limit):
                print(f"{hit['score']:>5.1f}  {hit['ref']}  {hit['kind']}")
                for line in hit["preview"]:
                    print(f"         {line}")
        elif args.command == "read":
            evidence = read_evidence(session, args.ref)
            print(f"{evidence['ref']}  {evidence['kind']}  revision {evidence['revision']}  [{evidence['verification']}]")
            print(evidence["source_uri"])
            print(evidence["content"])
        elif not run_probes(session, load_questions(args.path)):
            raise SystemExit("at least one probe does not match the ingested documents")


if __name__ == "__main__":
    main()
