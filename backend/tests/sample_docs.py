"""Two made-up vendor guides for tests. Not real products.

ZZPN0000001 is the NIC's transceiver, ZZPN0000002 the switch's; ZZPN00000012 is
a different, longer part number that must not match ZZPN0000001.
"""

import uuid
from datetime import datetime, timezone
from hashlib import sha256

from it_procurement_api.db.models import DocumentChunk, DocumentVersion
from it_procurement_api.fixtures import IMPORTER, TENANT_ID, fixture_id

NIC = "ZZTEST_NIC_GUIDE"
SWITCH = "ZZTEST_SWITCH_GUIDE"

_GUIDES = {
    NIC: ("ZZNIC900", [
        (1, 0, "TEXT", "Overview of the ZZNIC900 adapter. Optical zzmodule options are listed below."),
        (2, 0, "TEXT", "Table 2. Supported zzmodule\nZZPN0000001 QQ1 25G SR zzmodule\n1 Requires firmware 2.0"),
        (2, 1, "TABLE", "Part number | Description\nZZPN0000001 | 25G SR zzmodule"),
        (3, 0, "TEXT", "ZZPN00000012 is a different part."),
        (4, 0, "TEXT", "ZZLONGPAGE " + "filler words " * 600),
    ]),
    SWITCH: ("ZZSW100", [(5, 1, "TABLE", "Description | Part number\n25G SR zzmodule | ZZPN0000002")]),
}


def add_sample_docs(session) -> None:
    base = dict(tenant_id=TENANT_ID, created_by=fixture_id("principal", IMPORTER))
    for key, (model, chunks) in _GUIDES.items():
        version = DocumentVersion(
            id=uuid.uuid4(), document_key=key, revision="test-rev", source_uri=f"https://example.invalid/{key}",
            object_uri=f"test/{key}.pdf", content_hash=f"hash-{key}",
            captured_at=datetime(2026, 11, 13, tzinfo=timezone.utc), applicable_models=[model],
            doc_kind="compatibility", page_count=5, extraction_version="test", **base,
        )
        session.add(version)
        session.flush()
        session.add_all([
            DocumentChunk(
                id=uuid.uuid4(), document_version_id=version.id, page_no=page, ordinal=ordinal, kind=kind,
                content=content, table_header=content.splitlines()[0].split(" | ") if kind == "TABLE" else None,
                content_hash=sha256(content.encode()).hexdigest(), **base,
            )
            for page, ordinal, kind, content in chunks
        ])
        session.flush()
