"""pytest configuration: --update-snapshot flag for golden tests."""


def pytest_addoption(parser):
    parser.addoption(
        "--update-snapshot", action="store_true", default=False,
        help="Overwrite golden snapshot files instead of asserting against them.",
    )
