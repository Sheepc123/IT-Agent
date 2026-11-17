"""Evidence tools (plan batch B-1).

Search and read are tested with small made-up documents inserted into the
rolled-back session, so they do not need the vendor PDFs. The last test checks
the question file against the really ingested guides and is skipped without them.
"""

from pathlib import Path

import pytest

from it_procurement_api.documents import B0_PATHS, DocumentError, find_document
from it_procurement_api.evidence import (
    RESULTS,
    VERIFICATION,
    EvidenceError,
    baseline_context,
    check_probe,
    format_ref,
    list_documents,
    load_questions,
    parse_ref,
    probe_pages,
    query_terms,
    read_evidence,
    score_chunk,
    search_documents,
    term_weight,
)
from sample_docs import NIC, SWITCH


def test_query_terms_keep_codes_and_drop_chinese_and_stop_words():
    assert query_terms("XXV710-DA2 网卡能否使用 7G17A03537 的 transceiver？") == ["xxv710-da2", "7g17a03537", "transceiver"]
    assert query_terms("Does the SR650 V3 support it, the SR650?") == ["sr650", "v3", "support"]
    assert query_terms("能否安装") == []


def test_codes_weigh_more_and_match_only_as_whole_tokens():
    assert (term_weight("transceiver"), term_weight("h100"), term_weight("7g17a03537")) == (1.0, 3.0, 6.0)
    terms = ["7g17a03537", "transceiver"]
    assert score_chunk("25G SR Transceiver | 7G17A03537 | AV1B", terms) == 7.0
    assert score_chunk("part 7G17A035371 is another transceiver", terms) == 1.0  # longer code is a different part
    assert score_chunk("nothing relevant", terms) == 0.0


def test_reference_round_trip_and_bad_references():
    ref = format_ref("NVIDIA_Notes_620_v1.0", 12, 3)
    assert ref == "NVIDIA_Notes_620_v1.0:p12:3" and parse_ref(ref) == ("NVIDIA_Notes_620_v1.0", 12, 3)
    for bad in ("", "LP0785", "LP0785:2:1", "LP0785:p2", "LP0785:pX:1"):
        with pytest.raises(EvidenceError):
            parse_ref(bad)


def test_check_probe():
    assert check_probe({"expect": "hit", "pages": [2]}, [2, 9]) == "OK"
    assert check_probe({"expect": "hit", "pages": [2]}, [9]) == "MISMATCH"
    assert check_probe({"expect": "hit"}, []) == "MISMATCH"
    assert check_probe({"expect": "miss"}, []) == "OK"
    assert check_probe({"expect": "miss"}, [4]) == "MISMATCH"
    assert check_probe({"expect": None}, [4]) == "OBSERVED"


def test_question_file_is_well_formed():
    questions = load_questions()
    known_documents = {Path(path).stem for path in B0_PATHS}
    assert len({q["id"] for q in questions}) == len(questions) >= 8
    for q in questions:
        assert q["task"] in ("T1", "T2", "T3") and q["status"] in ("CHECKED_AGAINST_CHUNKS", "PENDING")
        assert all(item["result"] in RESULTS for item in q["expected"])
        assert q["probes"] and all(p["document_key"] in known_documents for p in q["probes"])
        assert all(p["expect"] in ("hit", "miss", None) for p in q["probes"])
        assert all(item["document_key"] in known_documents for item in q["evidence"])
        if q["status"] == "CHECKED_AGAINST_CHUNKS":
            assert q["expected"] and q["evidence"] and all(p["expect"] is not None for p in q["probes"])
        else:
            assert q["expected"] == []  # no answer is claimed before it has been looked up


def test_search_ranks_the_exact_part_number_first(session, docs):
    hits = search_documents(session, "ZZNIC900 能否使用 ZZPN0000001 这个 zzmodule？")
    assert hits[0]["ref"] == f"{NIC}:p2:1" and hits[0]["kind"] == "TABLE" and hits[0]["score"] == 7.0
    assert hits[0]["preview"] == ["ZZPN0000001 | 25G SR zzmodule"]
    refs = [hit["ref"] for hit in hits]
    assert f"{NIC}:p2:0" not in refs  # one chunk per page
    assert f"{NIC}:p3:0" not in refs  # ZZPN00000012 is not ZZPN0000001
    assert f"{SWITCH}:p5:1" in refs  # mentions zzmodule only, ranked lower
    assert [hit["score"] for hit in hits] == sorted((hit["score"] for hit in hits), reverse=True)


def test_search_options(session, docs):
    both = search_documents(session, "ZZPN0000001", document_key=NIC, one_per_page=False)
    assert [hit["ref"] for hit in both] == [f"{NIC}:p2:1", f"{NIC}:p2:0"]
    assert search_documents(session, "ZZPN0000001", document_key=SWITCH) == []
    assert len(search_documents(session, "zzmodule", limit=1)) == 1
    assert search_documents(session, "能否安装") == []


def test_read_evidence_returns_text_and_origin(session, docs):
    evidence = read_evidence(session, f"{NIC}:p2:0")
    assert evidence["content"].endswith("1 Requires firmware 2.0")  # the footnote is in the page text
    assert (evidence["page_no"], evidence["kind"], evidence["revision"]) == (2, "TEXT", "test-rev")
    assert evidence["source_uri"] == f"https://example.invalid/{NIC}" and evidence["verification"] == VERIFICATION
    assert read_evidence(session, f"{NIC}:p2:1")["table_header"] == ["Part number", "Description"]
    for missing in (f"{NIC}:p9:0", "ZZTEST_NO_SUCH_GUIDE:p1:0"):
        with pytest.raises(EvidenceError):
            read_evidence(session, missing)


def test_list_documents_filters_by_key_or_model(session, docs):
    assert [d["document_key"] for d in list_documents(session, "zztest nic")] == [NIC]
    assert [d["document_key"] for d in list_documents(session, "ZZSW100")] == [SWITCH]
    assert {NIC, SWITCH} <= {d["document_key"] for d in list_documents(session)}


def test_baseline_context_is_one_search_cut_to_a_budget(session, docs):
    context = baseline_context(session, "ZZPN0000001 zzmodule", limit=2, max_chars=40)
    assert context["terms"] == ["zzpn0000001", "zzmodule"] and len(context["chunks"]) == 2
    assert context["chunks"][0]["ref"] == f"{NIC}:p2:1"
    assert all(len(chunk["content"]) <= 20 for chunk in context["chunks"])
    assert context["chunks"][0]["truncated"] is True


def test_checked_questions_match_the_ingested_guides(session):
    for question in load_questions():
        if question["status"] != "CHECKED_AGAINST_CHUNKS":
            continue
        for probe in question["probes"]:
            try:
                find_document(session, probe["document_key"])
            except DocumentError:
                pytest.skip("vendor guides are not ingested in this database")
            pages = probe_pages(session, probe)
            assert check_probe(probe, pages) == "OK", (question["id"], probe, pages)
