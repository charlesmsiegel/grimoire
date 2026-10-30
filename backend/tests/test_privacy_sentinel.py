"""The suite-wide sentinel must actually fail a test that writes to a home directory."""

from pathlib import Path

CONFTEST = Path(__file__).with_name("conftest.py")


def test_sentinel_fails_a_test_that_writes_into_home(pytester):
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(
        """
        from pathlib import Path

        def test_leaks_into_home():
            (Path.home() / "leak.txt").write_text("synthetic")

        def test_stays_inside_tmp(tmp_path):
            (tmp_path / "ok.txt").write_text("synthetic")
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*redirected home or library directory*leak.txt*"])
