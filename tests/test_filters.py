from batchvault.filters import PathFilter, load_patterns


def test_no_patterns_matches_everything():
    assert PathFilter().matches("a/b.txt")


def test_include_limits_matches():
    path_filter = PathFilter(include=["*.mp4"])
    assert path_filter.matches("media/a.mp4")
    assert not path_filter.matches("media/a.txt")


def test_exclude_wins_over_include():
    path_filter = PathFilter(include=["*.mp4"], exclude=["tmp/"])
    assert path_filter.matches("a.mp4")
    assert not path_filter.matches("tmp/a.mp4")


def test_recursive_glob():
    assert PathFilter(include=["docs/**/*.md"]).matches("docs/a/b/c.md")


def test_windows_separators_are_normalised():
    assert not PathFilter(exclude=["tmp/"]).matches("C:\\data\\tmp\\a.txt")


def test_patterns_from_file(tmp_path):
    patterns = tmp_path / "patterns.txt"
    patterns.write_text("# skip\n*.log\n\n*.tmp\n", encoding="utf-8")
    assert load_patterns([f"@{patterns}", "*.bak"]) == ["*.log", "*.tmp", "*.bak"]
