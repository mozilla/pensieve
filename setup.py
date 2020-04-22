from setuptools import setup


def text_from_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


test_dependencies = [
    "coverage",
    "pytest",
    "pytest-black",
    "pytest-cov",
    "pytest-flake8",
    "mypy",
]

extras = {
    "testing": test_dependencies,
}

setup(
    name="Pensieve",
    use_incremental=True,
    author="Mozilla Corporation",
    author_email="fx-data-dev@mozilla.org",
    description="Runs a thing that analyzes experiments",
    url="https://github.com/mozilla/pensieve",
    packages=["pensieve", "pensieve.tests", "pensieve.tests.integration"],
    package_data={"pensieve.tests": ["data/*"]},
    install_requires=[
        "attrs",
        "cattrs",
        "Click",
        "google-cloud-bigquery",
        "incremental",
        "mozanalysis",
        "pytz",
        "requests",
    ],
    setup_requires=["incremental"],
    tests_require=test_dependencies,
    extras_require=extras,
    long_description=text_from_file("README.md"),
    long_description_content_type="text/markdown",
    python_requires=">=3.6",
    entry_points="""
        [console_scripts]
        pensieve=pensieve.cli:cli
    """,
)
