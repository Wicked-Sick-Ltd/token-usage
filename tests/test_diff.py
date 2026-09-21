from conftest import assistant, usage, user, write_jsonl


def two_transcripts(tmp_path):
    a = write_jsonl(tmp_path / "a.jsonl", [
        user("2026-06-12T10:00:00Z", command="/review"),
        assistant("2026-06-12T10:00:01Z", usage(out=100_000), request_id="r1"),
        user("2026-06-12T11:00:00Z", command="/old-only"),
        assistant("2026-06-12T11:00:01Z", usage(out=1_000), request_id="r2"),
    ])
    b = write_jsonl(tmp_path / "b.jsonl", [
        user("2026-06-12T12:00:00Z", command="/review"),
        assistant("2026-06-12T12:00:01Z", usage(out=40_000), request_id="r3"),
    ])
    return a, b


def test_diff_joins_labels_and_orders_by_abs_delta(tu, tmp_path):
    a, b = two_transcripts(tmp_path)
    d = tu.diff_data(a, b, tu.load_pricing())
    by_label = {r["label"]: r for r in d["rows"]}
    assert by_label["/review"]["delta_output"] == -60_000
    assert by_label["/old-only"]["b_cost"] is None        # missing on the new side
    assert d["rows"][0]["label"] == "/review"             # biggest |Δ cost| first


def test_render_diff_table(tu, tmp_path):
    a, b = two_transcripts(tmp_path)
    out = tu.render_diff(tu.diff_data(a, b, tu.load_pricing()))
    assert "| Activity | A cost | B cost | Δ cost | Δ output |" in out
    assert "—" in out                                     # missing side renders as —


def test_diff_unpriceable_side_renders_unknown_not_savings(tu, tmp_path):
    a = write_jsonl(tmp_path / "a.jsonl", [
        user("2026-06-12T10:00:00Z", command="/review"),
        assistant("2026-06-12T10:00:01Z", usage(out=100_000), request_id="r1"),
    ])
    b = write_jsonl(tmp_path / "b.jsonl", [
        user("2026-06-12T12:00:00Z", command="/review"),
        assistant("2026-06-12T12:00:01Z", usage(out=100_000), request_id="r2",
                  model="totally-unknown-model"),
    ])
    d = tu.diff_data(a, b, tu.load_pricing())
    row = d["rows"][0]
    assert row["b_cost"] is None
    assert row["delta_cost"] is None            # unknown, NOT a negative "saving"
    out = tu.render_diff(d)
    total_line = next(l for l in out.splitlines() if "Total" in l)
    assert "**—**" in total_line                 # total delta also unknown


def test_diff_tied_deltas_order_alphabetically(tu, tmp_path):
    entries_a, entries_b = [], []
    for i, cmd in enumerate(("/gamma", "/alpha", "/beta")):
        entries_a.append(user(f"2026-06-12T10:0{i}:00Z", command=cmd))
        entries_a.append(assistant(f"2026-06-12T10:0{i}:01Z", usage(out=1_000),
                                   request_id=f"ra{i}"))
        entries_b.append(user(f"2026-06-12T12:0{i}:00Z", command=cmd))
        entries_b.append(assistant(f"2026-06-12T12:0{i}:01Z", usage(out=1_000),
                                   request_id=f"rb{i}"))
    a = write_jsonl(tmp_path / "a.jsonl", entries_a)
    b = write_jsonl(tmp_path / "b.jsonl", entries_b)
    d = tu.diff_data(a, b, tu.load_pricing())
    assert [r["label"] for r in d["rows"]] == ["/alpha", "/beta", "/gamma"]


def test_render_diff_discloses_activity_only_and_its_warnings(tu, tmp_path):
    # Every other Cursor surface names a non-exact measurement; a diff of two
    # activity-only sessions renders "$0.00 vs $0.00, Δ $0.00", which reads as
    # "this change was free" rather than "nothing here was measured".
    a, b = two_transcripts(tmp_path)
    d = tu.diff_data(a, b, tu.load_pricing())
    d["runtime"] = "cursor"
    d["measurement"] = "activity_only"
    d["warnings"] = ["missing Cursor bubble 'ghost' for composer 'comp-b'"]
    out = tu.render_diff(d)
    assert "activity-only" in out
    assert "unmeasured" in out
    assert "ghost" in out


def test_render_diff_discloses_a_partial_measurement(tu, tmp_path):
    a, b = two_transcripts(tmp_path)
    d = tu.diff_data(a, b, tu.load_pricing())
    d["runtime"] = "cursor"
    d["measurement"] = "partial"
    out = tu.render_diff(d)
    assert "Measurement: partial" in out
    assert "lower bounds" in out
    # The two sides are separate sessions: the wording must not claim otherwise.
    assert "this session" not in out


def test_render_diff_claude_default_says_nothing_about_measurement(tu, tmp_path):
    a, b = two_transcripts(tmp_path)
    out = tu.render_diff(tu.diff_data(a, b, tu.load_pricing()))
    assert "Measurement:" not in out
    assert "warning" not in out.lower()


def test_worst_measurement_picks_the_least_confident_side(tu):
    assert tu.worst_measurement("exact", "exact") == "exact"
    assert tu.worst_measurement("exact", "partial") == "partial"
    assert tu.worst_measurement("partial", "exact") == "partial"
    assert tu.worst_measurement("partial", "activity_only") == "activity_only"
    assert tu.worst_measurement("activity_only", "exact") == "activity_only"


def test_render_diff_total_math_and_signs(tu, tmp_path):
    a = write_jsonl(tmp_path / "a.jsonl", [
        user("2026-06-12T10:00:00Z", command="/review"),
        assistant("2026-06-12T10:00:01Z", usage(out=100_000), request_id="r1"),
    ])
    b = write_jsonl(tmp_path / "b.jsonl", [
        user("2026-06-12T12:00:00Z", command="/review"),
        assistant("2026-06-12T12:00:01Z", usage(out=40_000), request_id="r2"),
    ])
    out = tu.render_diff(tu.diff_data(a, b, tu.load_pricing()))
    # fable output $50/MTok: A=$5.00, B=$2.00, Δ=-$3.00
    total_line = next(l for l in out.splitlines() if "Total" in l)
    assert "**$5.00**" in total_line and "**$2.00**" in total_line
    assert "**-$3.00**" in total_line
    assert "-60.0k" in total_line
