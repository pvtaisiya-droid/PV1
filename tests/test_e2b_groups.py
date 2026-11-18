from copy import deepcopy


def source(identifier, path, *, case="CASE-1", version="1", receiver="A", **sections):
    return {"id": identifier, "path": path, "file_name": path.rsplit("/", 1)[-1], "role": "xml",
            "normalized": {"message": {"worldwide_case_id": case, "sender": "Partner", "receiver": receiver,
                                        "message_identifier": identifier, "message_version": version},
                           "patient": {}, "reporter": {}, "literature": {}, "narrative": {},
                           "products": [], "reactions": [], **sections}}


def test_complementary_sources_are_order_independent_and_traceable():
    from app.e2b_groups import build_case_groups
    a = source("a", "case/a.xml", patient={"sex": "female", "age_value": None, "birth_date": {"raw": None, "precision": None}})
    b = source("b", "case/b.xml", receiver="B", patient={"sex": "female", "age_value": 42,
                "birth_date": {"raw": "19840101", "precision": "day"}})
    original = deepcopy([a, b])
    group = build_case_groups([a, b])[0]
    assert group == build_case_groups([b, a])[0]
    assert not group["conflicts"]
    assert group["merged"]["patient"]["age_value"] == 42
    assert group["provenance"]["patient.age_value"][0]["file_id"] == "b"
    assert len(group["messages"]) == 2
    assert [a, b] == original


def test_conflict_has_no_implicit_winner_and_accepts_only_source_choices():
    import pytest
    from app.e2b_groups import build_case_groups
    records = [source("a", "a.xml", patient={"sex": "male", "weight": 0}),
               source("b", "b.xml", patient={"sex": "female", "weight": 70})]
    group = build_case_groups(records)[0]
    assert group["unresolved_count"] == 2
    assert group["merged"]["patient"]["sex"] is None
    resolved = build_case_groups(records, {"CASE-1": {"patient.sex": "b", "patient.weight": "a"}})[0]
    assert resolved["unresolved_count"] == 0
    assert resolved["merged"]["patient"] == {"sex": "female", "weight": 0}
    with pytest.raises(ValueError, match="unavailable source"):
        build_case_groups(records, {"CASE-1": {"patient.sex": "unrelated"}})


def test_latest_version_does_not_restore_omitted_clinical_rows():
    from app.e2b_groups import build_case_groups
    old = source("a", "a.xml", version="1", products=[{"reported_product_name": "Removed drug"}])
    latest = source("b", "b.xml", version="2", patient={"age_value": 42})
    complement = source("c", "c.xml", version="02", patient={"weight": 60})
    group = build_case_groups([old, latest, complement])[0]
    assert len(group["messages"]) == 3
    assert not group["unresolved_count"]
    assert group["merged"]["products"] == []
    assert group["merged"]["patient"] == {"age_value": 42, "weight": 60}


def test_duplicate_named_rows_are_not_paired_arbitrarily():
    from app.e2b_groups import build_case_groups
    rows = [{"reported_product_name": "Drug", "dose_value": 1}, {"reported_product_name": "Drug", "dose_value": 2}]
    group = build_case_groups([source("a", "a.xml", products=rows)])[0]
    assert group["unresolved_count"] == 1
    assert group["conflicts"][0]["reason"] == "ambiguous_rows"


def test_unnamed_product_can_be_grouped_by_inn_without_merging_repeated_prescriptions():
    from app.e2b_groups import build_case_groups
    row = {"reported_product_name": None, "active_substances": ["Test substance"], "dose_value": "10"}
    a = source("a", "a.xml", products=[row])
    b = source("b", "b.xml", products=[row])
    group = build_case_groups([a, b])[0]
    assert not group["unresolved_count"] and group["merged"]["products"] == [row]
    assert group == build_case_groups([b, a])[0]
    repeated = build_case_groups([source("c", "c.xml", products=[row, row])])[0]
    assert repeated["unresolved_count"] == 1


def test_folders_assign_documents_without_defining_case_identity():
    from app.e2b_groups import build_case_groups
    records = [source("a", "case-a/a.xml"), source("b", "case-b/b.xml", case="CASE-2"),
               source("c", "other-folder/c.xml"),
               {"id": "pdf-a", "path": "case-a/CIOMS.pdf", "role": "cioms"},
               {"id": "pdf-b", "path": "case-b/source.pdf", "role": "pdf"},
               {"id": "common", "path": "common.pdf", "role": "pdf"}]
    groups = build_case_groups(records)
    assert len(groups) == 2
    assert set(groups[0]["item_ids"]) == {"a", "c"}
    assert set(groups[0]["attachment_ids"]) == {"a", "c", "pdf-a"}
    assert set(groups[1]["attachment_ids"]) == {"b", "pdf-b"}
    assert all("common" not in group["attachment_ids"] for group in groups)


def test_missing_identity_and_local_identity_do_not_merge_unrelated_senders():
    from app.e2b_groups import build_case_groups
    a = source("a", "a.xml", case=None)
    b = source("b", "b.xml", case=None)
    assert len(build_case_groups([a, b])) == 2
    for record in [a, b]:
        record["normalized"]["message"]["local_sender_case_id"] = "LOCAL-1"
    b["normalized"]["message"]["sender"] = "Other partner"
    assert len(build_case_groups([a, b])) == 2


def test_group_import_defaults_to_disabled_in_production(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("PV_APP_MODE", "prod")
    monkeypatch.delenv("PV_E2B_MULTI_CASE_IMPORT_ENABLED", raising=False)
    assert not Settings().e2b_group_import_enabled
