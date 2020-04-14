import datetime as dt
from google.cloud import bigquery
import mock
import os
import pytz
import pytest

from mozanalysis.metrics import Metric, DataSource, agg_sum
from google.api_core.exceptions import NotFound
from pensieve.analysis import Analysis, BigQueryClient
from pensieve.experimenter import Experiment, Variant


@pytest.mark.integration
class TestAnalysisIntegration:
    project_id = os.getenv("GOOGLE_PROJECT_ID")

    if project_id is None:
        print("GOOGLE_PROJECT_ID is not set.")
        assert False

    test_project = "pensieve-integration-test"
    test_dataset = "test"
    # contains the tables filled with test data required to run metrics analysis
    static_dataset = "test_data"

    client = bigquery.client.Client(project_id)

    clients_daily_source = "pensieve/tests/data/test_clients_daily.ndjson"
    events_source = "pensieve/tests/data/test_events.ndjson"
    get_key_udf = "pensieve/tests/data/get_key.sql"

    @pytest.fixture(autouse=True)
    def setup(self):
        # remove all tables previously created
        try:
            self.client.delete_dataset(self.test_dataset, delete_contents=True, not_found_ok=True)
            self.client.create_dataset(self.test_dataset)
        except NotFound:
            pass

        try:
            self.client.get_table(f"{self.static_dataset}.clients_daily")
        except NotFound:
            table_ref = self.client.create_table(f"{self.static_dataset}.clients_daily")
            job_config = bigquery.LoadJobConfig()
            job_config.source_format = bigquery.SourceFormat.NDJSON
            job_config.autodetect = True

            with open(self.clients_daily_source, "rb") as source_file:
                job = self.client.load_table_from_file(
                    source_file, table_ref, job_config=job_config
                )

            job.result()  # Waits for table load to complete.

        try:
            self.client.get_table(f"{self.static_dataset}.events")
        except NotFound:
            table_ref = self.client.create_table(f"{self.static_dataset}.events")
            job_config = bigquery.LoadJobConfig()
            job_config.source_format = bigquery.SourceFormat.NDJSON
            job_config.autodetect = True

            with open(self.events_source, "rb") as source_file:
                job = self.client.load_table_from_file(
                    source_file, table_ref, job_config=job_config
                )

            job.result()  # Waits for table load to complete.

        # create required UDF
        with open(self.get_key_udf, "r") as source_file:
            self.client.query(source_file.read()).result()

    def bq_execute(cls, *args, **kwargs):
        # to use the test project and dataset, we need to change the SQL query
        # generated by mozanalysis
        query = args[0].replace("moz-fx-data-shared-prod", cls.project)
        query = query.replace("telemetry", "test_data")

        dataset = bigquery.dataset.DatasetReference.from_string(
            cls.dataset, default_project=cls.project,
        )

        destination_table = None

        if len(args) > 1:
            destination_table = args[1]

        kwargs = {}
        if destination_table:
            kwargs["destination"] = dataset.table(destination_table)
            kwargs["write_disposition"] = bigquery.job.WriteDisposition.WRITE_TRUNCATE
        config = bigquery.job.QueryJobConfig(default_dataset=dataset, **kwargs)
        job = cls.client.query(query, config)
        # block on result
        return job.result(max_results=1)

    def test_metrics(self):
        experiment = Experiment(
            slug="test-experiment",
            type="rollout",
            start_date=dt.datetime(2020, 3, 30, tzinfo=pytz.utc),
            end_date=dt.datetime(2020, 6, 1, tzinfo=pytz.utc),
            proposed_enrollment=7,
            variants=[
                Variant(is_control=False, slug="branch1", ratio=0.5),
                Variant(is_control=True, slug="branch2", ratio=0.5),
            ],
            normandy_slug="test-experiment",
        )

        with mock.patch.object(BigQueryClient, "execute", new=TestAnalysisIntegration.bq_execute):
            analysis = Analysis(self.project_id, self.test_dataset, experiment)

            test_clients_daily = DataSource(
                name="clients_daily", from_expr=f"`{self.project_id}.test_data.clients_daily`",
            )

            test_active_hours = Metric(
                name="active_hours",
                data_source=test_clients_daily,
                select_expr=agg_sum("active_hours_sum"),
            )

            analysis.STANDARD_METRICS = [test_active_hours]

            analysis.run(current_date=dt.datetime(2020, 4, 12), dry_run=False)

        query_job = self.client.query(
            f"""
            SELECT
              COUNT(*) as count
            FROM `{self.project_id}.{self.test_dataset}.test_experiment_week_1`
        """
        )

        result = query_job.result()

        for row in result:
            assert row["count"] == 2