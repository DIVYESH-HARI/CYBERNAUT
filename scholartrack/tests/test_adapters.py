from app.adapters import CardAdapter, TableAdapter
from conftest import fixture

BASE = "http://example.test/list"


def test_table_adapter_reads_columns_by_header_not_position():
    res = TableAdapter().parse(fixture("table_ok.html"), BASE, {})
    assert [n.title for n in res.notices] == ["Alpha Merit Scholarship", "Beta Research Grant", "Gamma Fellowship"]
    first = res.notices[0]
    assert first.url == "http://example.test/n/t1"
    assert first.fields["due_text"] == "31 March 2027, 23:59 IST"
    assert first.fields["provider"] == "Alpha Trust"


def test_table_adapter_survives_malformed_rows_missing_fields_and_duplicates():
    res = TableAdapter().parse(fixture("table_malformed.html"), BASE, {})
    titles = [n.title for n in res.notices]
    assert titles == ["Valid Row", "Bad Date Row"]
    assert any("missing title" in w for w in res.warnings)
    assert any("missing link" in w for w in res.warnings)
    assert any("duplicate" in w for w in res.warnings)


def test_card_adapter_reads_labelled_fields_and_badges():
    res = CardAdapter().parse(fixture("cards_ok.html"), BASE, {})
    assert [n.title for n in res.notices] == ["Delta Fellowship", "Epsilon Grant"]
    assert res.notices[0].fields["due_text"] == "15 Apr 2027, 18:00 UTC+01:00"
    assert "Withdrawn" in res.notices[1].extra_status
    assert any("card 3 skipped" in w for w in res.warnings)


def test_layout_change_returns_no_notices_with_warning():
    for adapter in (TableAdapter(), CardAdapter()):
        res = adapter.parse(fixture("layout_changed.html"), BASE, {})
        assert res.notices == [] and res.warnings
