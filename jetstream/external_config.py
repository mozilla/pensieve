"""
Retrieves external configuration files for specific experiments.

Experiment-specific configuration files are stored in https://github.com/mozilla/jetstream-config/
"""

import datetime as dt
from typing import List, Optional

import attr
import toml
from git import Repo
from google.cloud import bigquery
from pytz import UTC

from jetstream.config import AnalysisSpec, OutcomeSpec
from jetstream.util import TemporaryDirectory

from . import bq_normalize_name


@attr.s(auto_attribs=True)
class ExternalConfig:
    """Represent an external config file."""

    slug: str
    spec: AnalysisSpec
    last_modified: dt.datetime


@attr.s(auto_attribs=True)
class ExternalOutcome:
    """Represents an external outcome snippet."""

    slug: str
    spec: OutcomeSpec


@attr.s(auto_attribs=True)
class ExternalConfigCollection:
    """
    Collection of experiment-specific configurations pulled in
    from an external GitHub repository.
    """

    configs: List[ExternalConfig] = attr.Factory(list)
    outcomes: List[ExternalOutcome] = attr.Factory(list)

    JETSTREAM_CONFIG_URL = "https://github.com/mozilla/jetstream-config"

    @classmethod
    def from_github_repo(cls) -> "ExternalConfigCollection":
        """Pull in external config files."""
        # download files to tmp directory
        with TemporaryDirectory() as tmp_dir:
            repo = Repo.clone_from(cls.JETSTREAM_CONFIG_URL, tmp_dir)

            external_configs = []

            for config_file in tmp_dir.glob("*.toml"):
                last_modified = next(repo.iter_commits("main", paths=config_file)).committed_date

                external_configs.append(
                    ExternalConfig(
                        config_file.stem,
                        AnalysisSpec.from_dict(toml.load(config_file)),
                        UTC.localize(dt.datetime.utcfromtimestamp(last_modified)),
                    )
                )

            outcomes = []

            for outcome_file in tmp_dir.glob("**/outcomes/*.toml"):
                outcomes.append(
                    ExternalOutcome(
                        slug=outcome_file.stem, spec=OutcomeSpec.from_dict(toml.load(outcome_file))
                    )
                )

        return cls(external_configs, outcomes)

    def spec_for_experiment(self, slug: str) -> Optional[AnalysisSpec]:
        """Return the spec for a specific experiment."""
        for config in self.configs:
            if config.slug == slug:
                return config.spec

        return None

    def updated_configs(self, bq_project: str, bq_dataset: str) -> List[ExternalConfig]:
        """
        Return external configs that have been updated/added and
        with associated BigQuery tables being out of date.
        """
        client = bigquery.Client(bq_project)
        job = client.query(
            fr"""
            SELECT
                table_name,
                REGEXP_EXTRACT_ALL(
                    option_value,
                    '.*STRUCT\\(\"last_updated\", \"(.+)\"\\).*'
                ) AS last_updated
            FROM
            {bq_dataset}.INFORMATION_SCHEMA.TABLE_OPTIONS
            WHERE option_name = 'labels'
            """
        )

        result = list(job.result())

        updated_configs = []

        for config in self.configs:
            seen = False
            table_prefix = bq_normalize_name(config.slug)
            for row in result:
                if not row.table_name.startswith(table_prefix):
                    continue
                seen = True
                if not len(row.last_updated):
                    continue
                table_last_updated = UTC.localize(
                    dt.datetime.utcfromtimestamp(int(row.last_updated[0]))
                )
                if table_last_updated < config.last_modified:
                    updated_configs.append(config)
                    break
            if not seen:
                updated_configs.append(config)

        return updated_configs
