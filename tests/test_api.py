"""The public Python API. Offline: no DataHub, no network.

This is the one surface in the package that somebody outside it will pin a script
to, so these tests are about the contract rather than the behaviour: the names
are importable from the top level, they resolve names the way the CLI does, and
they call the same core the CLI calls rather than a second implementation.

The behaviour they delegate to is tested where it lives (tests/agent,
tests/writeback). Re-testing it here would only pin the delegation twice.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from datahub.metadata.schema_classes import (
    MLModelPropertiesClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
)

import janus
from janus.config import ScanConfig
from tests.conftest import (
    FEATURE_TABLE_URN,
    MODEL_URN,
    FakeClient,
    FakeGraph,
    make_connection,
)

CONFIG = ScanConfig()


def _schema() -> SchemaMetadataClass:
    return SchemaMetadataClass(
        schemaName="customer_features",
        platform="urn:li:dataPlatform:postgres",
        version=0,
        hash="",
        platformSchema=None,  # type: ignore[arg-type]
        fields=[
            SchemaFieldClass(
                fieldPath=path,
                type=SchemaFieldDataTypeClass(type=StringTypeClass()),
                nativeDataType="VARCHAR",
            )
            for path in ("applicant_id", "applicant_income", "churned")
        ],
    )


def _conn():  # noqa: ANN202 - a DataHubConnection
    graph = FakeGraph(
        aspects={  # type: ignore[arg-type]
            (MODEL_URN, MLModelPropertiesClass): MLModelPropertiesClass(name="Credit Risk v3"),
            (FEATURE_TABLE_URN, SchemaMetadataClass): _schema(),
        }
    )
    return make_connection(graph, FakeClient(search_urns=[MODEL_URN, FEATURE_TABLE_URN]))


class TestSurface:
    def test_the_supported_names_import_from_the_top_level_package(self):
        """What a user's script pins to. Moving one of these breaks them silently."""
        for name in ("link_model", "scan_model", "ScanReport", "LinkResult", "LinkError"):
            assert hasattr(janus, name), name

    def test_all_lists_exactly_the_supported_surface(self):
        """`from janus import *` must not hand out anything unsupported."""
        assert set(janus.__all__) == {
            "LinkError",
            "LinkResult",
            "ScanReport",
            "__version__",
            "link_model",
            "scan_model",
        }

    def test_the_package_version_matches_the_distribution_version(self):
        """A wheel whose __version__ disagrees with its metadata is unreportable.

        A user debugging in the field reads `janus.__version__`; a resolver
        reads the metadata. Nothing else keeps the two equal, so this does.
        """
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text())["project"]["version"]

        assert janus.__version__ == declared


class TestScanModel:
    def test_a_call_with_no_target_is_refused_rather_than_scanning_nothing(self):
        with pytest.raises(ValueError, match="model=, table=, or both"):
            janus.scan_model()

    def test_a_model_name_is_resolved_and_scanned(self):
        report = janus.scan_model(
            model="credit_risk_v3", dry_run=True, conn=_conn(), config=CONFIG
        )

        assert report.model_urn == MODEL_URN
        assert report.dry_run is True

    def test_a_dry_run_writes_nothing(self):
        conn = _conn()

        janus.scan_model(model="credit_risk_v3", dry_run=True, conn=conn, config=CONFIG)

        assert conn.graph.emitted == []  # type: ignore[attr-defined]
        # Reads are allowed and expected here: resolving the model name is a
        # GraphQL scroll (janus/discovery.py). What a dry run may never do
        # is mutate, so the assertion names that rather than counting calls.
        sent = [query for query, _ in conn.graph.graphql_calls]  # type: ignore[attr-defined]
        assert [query for query in sent if "mutation" in query] == []

    def test_the_checks_that_could_not_run_are_reported_not_hidden(self):
        """The API returns the same honest report the CLI prints, gaps included."""
        report = janus.scan_model(
            model="credit_risk_v3", dry_run=True, conn=_conn(), config=CONFIG
        )

        assert report.clean
        assert report.not_evaluated


class TestLinkModel:
    def test_it_declares_a_feature_per_column_and_the_label(self):
        conn = _conn()

        result = janus.link_model(
            model="credit_risk_v3",
            features="ecommerce.public.customer_features",
            label_column="churned",
            exclude=["applicant_id"],
            conn=conn,
            config=CONFIG,
        )

        # applicant_id excluded; applicant_income and churned remain.
        assert len(result.feature_urns) == 2
        assert all("applicant_id" not in urn for urn in result.feature_urns)
        assert result.label_column_urns[0].endswith("churned)")

    def test_a_label_in_another_table_is_addressed_there(self):
        conn = _conn()

        result = janus.link_model(
            model="credit_risk_v3",
            features="ecommerce.public.customer_features",
            label_table="ecommerce.public.customer_features",
            label_column="churned",
            conn=conn,
            config=CONFIG,
        )

        assert FEATURE_TABLE_URN in result.label_column_urns[0]

    def test_a_dry_run_declares_nothing(self):
        conn = _conn()

        janus.link_model(
            model="credit_risk_v3",
            features="ecommerce.public.customer_features",
            label_column="churned",
            dry_run=True,
            conn=conn,
            config=CONFIG,
        )

        assert conn.graph.emitted == []  # type: ignore[attr-defined]

    def test_an_unresolvable_table_raises_rather_than_linking_the_wrong_one(self):
        with pytest.raises(ValueError):
            janus.link_model(
                model="credit_risk_v3",
                features="no_such_table",
                label_column="churned",
                conn=_conn(),
                config=CONFIG,
            )
