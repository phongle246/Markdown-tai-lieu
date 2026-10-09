import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import fixtures  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def isolated_home(tmp_path_factory):
    os.environ["T2MD_HOME"] = str(tmp_path_factory.mktemp("t2md_home"))
    os.environ["T2MD_DEV"] = "1"


@pytest.fixture(scope="session")
def book_pdf(tmp_path_factory):
    p = tmp_path_factory.mktemp("pdfs") / "book.pdf"
    fixtures.make_book(p)
    return p


@pytest.fixture(scope="session")
def converted(tmp_path_factory, book_pdf):
    """A fully converted project (all three chapters)."""
    from textbook2md import service
    from textbook2md.pipeline import JobControl, convert_many
    root = tmp_path_factory.mktemp("proj") / "kb"
    p = service.create_project(book_pdf, root)
    convert_many(p, [c.key for c in p.chapters], {"ocr_enabled": True, "ai_enabled": False}, JobControl())
    return p


def read(p, name):
    return next((p.root / "chapters").glob(f"{name}*.md")).read_text(encoding="utf-8")
