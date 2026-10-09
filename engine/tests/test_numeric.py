from textbook2md import numeric


def test_tokens_cover_doses_ranges_percent_and_comparators():
    toks = numeric.tokens("Give 15 mg/kg/day (95% CI 1.2–3.4), SpO2 ≥ 94, <5 years, 1,000 mL ±2")
    assert {"15", "95%", "1.2", "3.4", "≥94", "<5", "1,000", "±2"} <= set(toks)


def test_markdown_syntax_is_ignored():
    md = "---\nx: 1\n---\n<!-- source_page: 7 -->\n| a | 5 |\n|---|---|\n**3 mg**<sup>2</sup> [Table 1.2](t.md#x) ![Figure 9](a.png)\n> 4 doses"
    toks = numeric.tokens(numeric.strip_markdown(md))
    assert "7" not in toks and "9" not in toks and "1" not in toks      # front matter / comment / image alt / link target
    assert toks.count("5") == 1 and "3" in toks and "4" in toks and "1.2" in toks


def test_missing_changed_unexpected():
    src = [("15", 1, "mg/kg"), ("0.5", 1, "%"), ("12", 2, ""), ("30", 2, "")]
    md = [("15", 1), ("0.6", 1), ("30", 2), ("99", 2)]
    issues = {i.kind: i for i in numeric.compare(src, md)}
    assert issues["CHANGED_NUMBER"].source == "0.5" and issues["CHANGED_NUMBER"].markdown == "0.6"
    assert issues["CHANGED_NUMBER"].severity in ("HIGH", "CRITICAL")
    assert issues["MISSING_NUMBER"].source == "12"
    assert issues["UNEXPECTED_NUMBER"].markdown == "99"


def test_changed_dose_is_critical():
    iss = numeric.compare([("15", 3, "mg/kg/day")], [("51", 3)])
    assert iss[0].kind == "CHANGED_NUMBER" and iss[0].severity == "CRITICAL"


def test_identical_is_clean():
    assert numeric.compare([("1", 1, ""), ("2", 1, "")], [("2", 1), ("1", 1)]) == []
