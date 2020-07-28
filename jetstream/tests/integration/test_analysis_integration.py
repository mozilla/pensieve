import datetime as dt
from google.cloud import bigquery
from pathlib import Path
import datetime
from unittest import mock
import mozanalysis
import pytz
import pytest
import random
import string

from mozanalysis.metrics import Metric, DataSource, agg_sum
from google.api_core.exceptions import NotFound

from jetstream import AnalysisPeriod
from jetstream.analysis import Analysis
from jetstream.config import AnalysisSpec, Summary
from jetstream.experimenter import Experiment, Variant
from jetstream.statistics import BootstrapMean


TEST_DIR = Path(__file__).parent.parent


class TestAnalysisIntegration:
    project_id = "jetstream-integration-test"

    # generate a random test dataset to avoid conflicts when running tests in parallel
    test_dataset = "test_" + "".join(random.choice(string.ascii_lowercase) for i in range(10))
    # contains the tables filled with test data required to run metrics analysis
    static_dataset = "test_data"

    @pytest.fixture(scope="class")
    def client(self):
        self._client = getattr(self, "_client", None) or bigquery.client.Client(self.project_id)
        return self._client

    clients_daily_source = TEST_DIR / "data" / "test_clients_daily.ndjson"
    events_source = TEST_DIR / "data" / "test_events.ndjson"

    @pytest.fixture(autouse=True)
    def setup(self, client):
        # remove all tables previously created

        client.delete_dataset(self.test_dataset, delete_contents=True, not_found_ok=True)
        client.create_dataset(self.test_dataset)

        try:
            client.get_table(f"{self.static_dataset}.clients_daily")
        except NotFound:
            table_ref = client.create_table(f"{self.static_dataset}.clients_daily")
            job_config = bigquery.LoadJobConfig()
            job_config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON
            job_config.autodetect = True

            with open(self.clients_daily_source, "rb") as source_file:
                job = client.load_table_from_file(source_file, table_ref, job_config=job_config)

            job.result()  # Waits for table load to complete.

        try:
            client.get_table(f"{self.static_dataset}.events")
        except NotFound:
            table_ref = client.create_table(f"{self.static_dataset}.events")
            job_config = bigquery.LoadJobConfig()
            job_config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON
            job_config.autodetect = True

            with open(self.events_source, "rb") as source_file:
                job = client.load_table_from_file(source_file, table_ref, job_config=job_config)

            job.result()  # Waits for table load to complete.

        yield

        client.delete_dataset(self.test_dataset, delete_contents=True, not_found_ok=True)

    def test_metrics(self, client):
        experiment = Experiment(
            slug="test-experiment",
            type="rollout",
            status="Live",
            start_date=dt.datetime(2020, 3, 30, tzinfo=pytz.utc),
            end_date=dt.datetime(2020, 6, 1, tzinfo=pytz.utc),
            proposed_enrollment=7,
            variants=[
                Variant(is_control=False, slug="branch1", ratio=0.5),
                Variant(is_control=True, slug="branch2", ratio=0.5),
            ],
            normandy_slug="test-experiment",
        )

        orig = mozanalysis.experiment.Experiment.build_query

        def build_query_test_project(instance, *args, **kwargs):
            # to use the test project and dataset, we need to change the SQL query
            # generated by mozanalysis
            query = orig(instance, args[0], args[1], args[2], args[3])
            query = query.replace("moz-fx-data-shared-prod.udf.get_key", "mozfun.map.get_key")
            query = query.replace("moz-fx-data-shared-prod", self.project_id)
            query = query.replace("telemetry", self.static_dataset)
            return query

        config = AnalysisSpec().resolve(experiment)

        test_clients_daily = DataSource(
            name="clients_daily", from_expr=f"`{self.project_id}.test_data.clients_daily`",
        )

        test_active_hours = Metric(
            name="active_hours",
            data_source=test_clients_daily,
            select_expr=agg_sum("active_hours_sum"),
        )

        config.metrics = {
            AnalysisPeriod.WEEK: [
                Summary(test_active_hours, BootstrapMean(ref_branch_label="branch1"))
            ]
        }

        analysis = Analysis(self.project_id, self.test_dataset, config)

        with mock.patch.object(
            mozanalysis.experiment.Experiment, "build_query", new=build_query_test_project
        ):
            analysis.run(current_date=dt.datetime(2020, 4, 12, tzinfo=pytz.utc), dry_run=False)

        query_job = client.query(
            f"""
            SELECT
              *
            FROM `{self.project_id}.{self.test_dataset}.test_experiment_week_1`
            ORDER BY enrollment_date DESC
        """
        )

        expected_metrics_results = [
            {
                "client_id": "bbbb",
                "branch": "branch2",
                "enrollment_date": datetime.date(2020, 4, 3),
                "num_enrollment_events": 1,
                "analysis_window_start": 0,
                "analysis_window_end": 6,
            },
            {
                "client_id": "aaaa",
                "branch": "branch1",
                "enrollment_date": datetime.date(2020, 4, 2),
                "num_enrollment_events": 1,
                "analysis_window_start": 0,
                "analysis_window_end": 6,
            },
        ]

        for i, row in enumerate(query_job.result()):
            for k, v in expected_metrics_results[i].items():
                assert row[k] == v

        assert (
            client.get_table(f"{self.project_id}.{self.test_dataset}.test_experiment_weekly")
            is not None
        )
        assert (
            client.get_table(
                f"{self.project_id}.{self.test_dataset}.statistics_test_experiment_week_1"
            )
            is not None
        )

        stats = client.list_rows(
            f"{self.project_id}.{self.test_dataset}.statistics_test_experiment_week_1"
        ).to_dataframe()

        count_by_branch = stats.query("statistic == 'count'").set_index("branch")
        assert count_by_branch.loc["branch1", "point"] == 1.0
        assert count_by_branch.loc["branch2", "point"] == 1.0

        assert (
            client.get_table(
                f"{self.project_id}.{self.test_dataset}.statistics_test_experiment_weekly"
            )
            is not None
        )
