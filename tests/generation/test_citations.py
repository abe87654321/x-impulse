"""FR-05-02 / FR-05-05a / FR-05-20：逐句引用绑定与证据快照。"""

from __future__ import annotations

from dataclasses import replace

from kb.generation.citations import CitationBinder, split_sentences
from kb.retrieval.models import RetrievedChunk
from tests.retrieval.fakes import make_record


def _evidence(*records) -> list[RetrievedChunk]:
    return [RetrievedChunk(record=record, score=1.0) for record in records]


def _chunk(chunk_id: str, doc_id: str) -> RetrievedChunk:
    record = replace(
        make_record(chunk_id, doc_id=doc_id),
        page_no=3,
        section_path="员工手册 > 报销制度",
        clause_no="第二条",
        doc_version="7",
    )
    return RetrievedChunk(record=record, score=1.0)


def test_every_grounded_sentence_is_kept_with_citations():
    e1 = _chunk("c1", "doc_1")
    e2 = _chunk("c2", "doc_2")
    binder = CitationBinder([e1, e2])

    answer = binder.bind("报销需在三十日内提交[1]。员工应如实填写报销单[2]。")

    assert answer.refused is False
    assert [s.text for s in answer.sentences] == [
        "报销需在三十日内提交。",
        "员工应如实填写报销单。",
    ]
    assert [c.chunk_id for c in answer.sentences[0].citations] == ["c1"]
    assert [c.chunk_id for c in answer.sentences[1].citations] == ["c2"]


def test_citation_snapshot_records_hash_version_and_locator():
    e1 = _chunk("c1", "doc_1")
    answer = CitationBinder([e1]).bind("事实句[1]。")

    citation = answer.citations[0]
    record = e1.record
    assert citation.chunk_content_hash == record.chunk_content_hash
    assert citation.chunk_version == "1"
    assert citation.doc_version == "7"
    assert citation.page_no == 3
    assert citation.section_path == "员工手册 > 报销制度"
    assert citation.clause_no == "第二条"
    assert (citation.char_start, citation.char_end) == (0, 10)


def test_sentence_without_marker_is_dropped():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("没有依据的句子。有依据的句子[1]。")

    assert [s.text for s in answer.sentences] == ["有依据的句子。"]
    assert answer.sentences[0].sentence_index == 0


def test_invalid_marker_only_is_dropped():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("编造内容[9]。")

    assert answer.refused is True
    assert answer.refusal_reason == "no_grounded_sentence"


def test_partial_valid_markers_keep_only_valid_citations():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("混合引用[9][1]。")

    assert len(answer.sentences) == 1
    assert [c.chunk_id for c in answer.citations] == ["c1"]


def test_duplicate_markers_are_deduplicated():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("重复标注[1][1]。")

    assert len(answer.citations) == 1


def test_marker_after_sentence_punctuation_stays_with_previous_sentence():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("事实句。[1]")

    assert len(answer.sentences) == 1
    assert answer.text == "事实句。"


def test_markers_are_removed_from_display_text():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("事实句[1]。")

    assert "[1]" not in answer.text
    assert answer.text == "事实句。"


def test_sentence_indices_are_renumbered_after_dropping():
    answer = CitationBinder(_evidence(make_record("c1"), make_record("c2"))).bind(
        "坏句。好句一[1]。好句二[2]。"
    )

    assert [s.sentence_index for s in answer.sentences] == [0, 1]
    assert [c.sentence_index for c in answer.citations] == [0, 1]


def test_no_evidence_refuses_without_calling_model():
    answer = CitationBinder([]).bind("任意内容[1]。")

    assert answer.refused is True
    assert answer.refusal_reason == "no_evidence"
    assert answer.citations == ()


def test_all_sentences_unbound_refuses():
    answer = CitationBinder(_evidence(make_record("c1"))).bind("第一句。第二句。")

    assert answer.refused is True
    assert answer.refusal_reason == "no_grounded_sentence"


def test_split_sentences_handles_chinese_and_newlines():
    assert split_sentences("第一句。第二句！\n第三句?") == ["第一句。", "第二句！", "第三句?"]
