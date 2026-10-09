from textbook2md.textclean import (BOLD, ITALIC, SUP, JoinLog, Span, Vocab, escape_md, join_span_lines, normalize_chars,
                                   spans_plain, spans_to_md)

L = lambda t, bits=0: [Span(t, bits)]


def join(lines, vocab=None):
    log = JoinLog()
    return spans_plain(join_span_lines([L(x) for x in lines], vocab, log)), log


def test_ligatures_and_spaces():
    assert normalize_chars("ﬁnd ﬂu eﬀect oﬃce") == "find flu effect office"
    assert normalize_chars("a b​c") == "a bc"


def test_line_joining_makes_one_paragraph():
    txt, _ = join(["The child was", "seen in clinic and", "treated."])
    assert txt == "The child was seen in clinic and treated."


def test_dehyphenation_with_vocab_evidence():
    v = Vocab(); v.feed("the pediatric ward")
    txt, log = join(["a pedi-", "atric patient"], v)
    assert txt == "a pediatric patient"
    assert log.dehyphenated == ["pedi-atric"]


def test_real_hyphen_is_preserved():
    v = Vocab(); v.feed("long-term follow-up")
    txt, log = join(["long-", "term follow"], v)
    assert txt == "long-term follow"
    assert log.kept_hyphen == ["long-term"]


def test_uncertain_hyphen_kept_and_logged():
    txt, log = join(["a zzqx-", "plomb value"], Vocab())
    assert txt == "a zzqx-plomb value" and log.uncertain == ["zzqx-plomb"]


def test_hyphen_before_digit_or_capital_never_joined_with_space():
    txt, _ = join(["COVID-", "19 and Epstein-", "Barr virus, 10-", "20 mg"])
    assert txt == "COVID-19 and Epstein-Barr virus, 10-20 mg"


def test_soft_hyphen_always_removed():
    txt, _ = join(["inter­", "action"])
    assert txt == "interaction"


def test_span_markdown_bold_italic_sup():
    md = spans_to_md([Span("Dose ", 0), Span("important", BOLD), Span(" and ", 0), Span("Homo sapiens", ITALIC), Span("1,2", SUP)])
    assert md == "Dose **important** and *Homo sapiens*<sup>1,2</sup>"


def test_markdown_escaping_does_not_change_text():
    assert escape_md("a*b <i> `x`") == "a\\*b \\<i> \\`x\\`"
    assert spans_to_md([Span("5 < 6 and 7 > 3", 0)]) == "5 < 6 and 7 > 3"


def test_page_marker_span_rendered_inline():
    md = spans_to_md([Span("end of page.", 0), Span("start of next", 0, 3)])
    assert "<!-- source_page: 3 -->" in md
