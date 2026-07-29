import unittest

from agent.contracts import (
    FieldMapping,
    SourceFieldReference,
    SourceJoin,
    TargetField,
)
from agent.sql_validation import validate_sql


class SqlValidationTest(unittest.TestCase):
    expected_fields = ["person_id", "gender_concept_id"]

    def _validate(self, sql: str):
        """Validate a small Snowflake SELECT against the expected fields."""
        return validate_sql(
            sql=sql,
            dialect="snowflake",
            expected_fields=self.expected_fields,
            output_format="sql",
        )

    def test_accepts_exact_fields_in_target_order(self):
        """The expected fields in the expected order should pass."""
        result = self._validate(
            "SELECT 1 AS person_id, 8507 AS gender_concept_id"
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.errors, ())

    def test_rejects_missing_field(self):
        """A missing target field should be reported."""
        result = self._validate("SELECT 1 AS person_id")

        self.assertFalse(result.valid)
        self.assertIn(
            "missing target fields: gender_concept_id",
            result.errors,
        )

    def test_rejects_extra_field(self):
        """A field outside the target schema should be reported."""
        result = self._validate(
            "SELECT 1 AS person_id, 8507 AS gender_concept_id, "
            "'x' AS unexpected_field"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "unexpected target fields: unexpected_field",
            result.errors,
        )

    def test_rejects_wrong_field_order(self):
        """Correct fields in the wrong target-schema order should fail."""
        result = self._validate(
            "SELECT 8507 AS gender_concept_id, 1 AS person_id"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "target fields are not in target-schema order",
            result.errors,
        )

    def test_rejects_select_star(self):
        """Wildcard projection cannot prove target-field coverage."""
        result = self._validate("SELECT * FROM cai_01_patient")

        self.assertFalse(result.valid)
        self.assertIn(
            "SELECT * is not allowed; target columns must be explicit",
            result.errors,
        )


class MappingSqlValidationTest(unittest.TestCase):
    expected_fields = ["person_id", "gender_concept_id", "month_of_birth"]
    field_mappings = [
        FieldMapping(
            target_field="person_id",
            action="map",
            source_fields=[
                SourceFieldReference(
                    model="cai_01_patient",
                    field="patient_id",
                )
            ],
            transformation="Cast patient_id to integer.",
        ),
        FieldMapping(
            target_field="gender_concept_id",
            action="derive",
            source_fields=[
                SourceFieldReference(
                    model="cai_01_patient",
                    field="sex",
                )
            ],
            transformation="Map sex to a concept ID.",
        ),
    ]

    def _validate(self, sql: str):
        """Validate source lineage as well as target-column coverage."""
        return validate_sql(
            sql=sql,
            dialect="snowflake",
            expected_fields=self.expected_fields,
            output_format="sql",
            field_mappings=self.field_mappings,
        )

    def test_accepts_declared_source_lineage_and_unmapped_null(self):
        """Mapped fields should use declared sources and unmapped fields NULL."""
        result = self._validate(
            "SELECT "
            "CAST(patient_id AS INTEGER) AS person_id, "
            "CASE WHEN sex = 'Male' THEN 8507 ELSE 0 END "
            "AS gender_concept_id, "
            "CAST(NULL AS INTEGER) AS month_of_birth "
            "FROM cai_01_patient"
        )

        self.assertTrue(result.valid)

    def test_rejects_null_for_mapped_field(self):
        """A map action cannot be silently replaced with NULL."""
        result = self._validate(
            "SELECT "
            "NULL AS person_id, "
            "sex AS gender_concept_id, "
            "NULL AS month_of_birth "
            "FROM cai_01_patient"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "person_id cannot output NULL for action map",
            result.errors,
        )

    def test_rejects_undeclared_source_field(self):
        """A target expression cannot introduce an undeclared source column."""
        result = self._validate(
            "SELECT "
            "wrong_id AS person_id, "
            "sex AS gender_concept_id, "
            "NULL AS month_of_birth "
            "FROM cai_01_patient"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "person_id does not use declared source field "
            "cai_01_patient.patient_id",
            result.errors,
        )
        self.assertIn(
            "person_id uses undeclared source field(s): wrong_id",
            result.errors,
        )

    def test_rejects_non_null_for_unmapped_field(self):
        """An optional target without a mapping must remain NULL."""
        result = self._validate(
            "SELECT "
            "patient_id AS person_id, "
            "sex AS gender_concept_id, "
            "1 AS month_of_birth "
            "FROM cai_01_patient"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "month_of_birth must output NULL for action unmapped",
            result.errors,
        )


class TypedNullValidationTest(unittest.TestCase):
    target_field = TargetField(
        name="month_of_birth",
        data_type="integer",
        required=False,
    )

    def _validate(self, sql: str, dialect: str = "snowflake"):
        """Validate one unmapped field against its target datatype."""
        return validate_sql(
            sql=sql,
            dialect=dialect,
            expected_fields=["month_of_birth"],
            field_mappings=[],
            target_fields=[self.target_field],
        )

    def test_accepts_null_cast_to_target_type(self):
        """A NULL cast to the declared target datatype should pass."""
        result = self._validate(
            "SELECT CAST(NULL AS INTEGER) AS month_of_birth"
        )

        self.assertTrue(result.valid)

    def test_rejects_untyped_null(self):
        """A bare NULL does not establish the output column datatype."""
        result = self._validate("SELECT NULL AS month_of_birth")

        self.assertFalse(result.valid)
        self.assertIn(
            "month_of_birth must use typed NULL: CAST(NULL AS integer)",
            result.errors,
        )

    def test_rejects_wrong_null_type(self):
        """The NULL cast must match the target-schema datatype."""
        result = self._validate(
            "SELECT CAST(NULL AS VARCHAR(50)) AS month_of_birth"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "month_of_birth NULL type must be integer, found VARCHAR(50)",
            result.errors,
        )

    def test_accepts_dialect_equivalent_datetime_type(self):
        """Generic datetime may use PostgreSQL's equivalent TIMESTAMP type."""
        target = TargetField(
            name="birth_datetime",
            data_type="datetime",
            required=False,
        )

        result = validate_sql(
            sql="SELECT CAST(NULL AS TIMESTAMP) AS birth_datetime",
            dialect="postgres",
            expected_fields=["birth_datetime"],
            field_mappings=[],
            target_fields=[target],
        )

        self.assertTrue(result.valid)

    def test_accepts_snowflake_timestamp_ntz_for_datetime(self):
        """Snowflake TIMESTAMP_NTZ is its equivalent of generic datetime."""
        target = TargetField(
            name="birth_datetime",
            data_type="datetime",
            required=False,
        )

        result = validate_sql(
            sql=(
                "SELECT CAST(NULL AS TIMESTAMP_NTZ) "
                "AS birth_datetime"
            ),
            dialect="snowflake",
            expected_fields=["birth_datetime"],
            field_mappings=[],
            target_fields=[target],
        )

        self.assertTrue(result.valid)


class MappingTableSqlValidationTest(unittest.TestCase):
    mapping = FieldMapping(
        target_field="gender_concept_id",
        action="map",
        source_fields=[
            SourceFieldReference(
                model="cai_01_patient",
                field="sex",
            )
        ],
        transformation="Use the controlled gender lookup.",
        mapping_table_name="mapping_person_gender_concept_id",
    )

    def _validate(self, sql: str):
        """Validate one conventional mapping-table lookup."""
        return validate_sql(
            sql=sql,
            dialect="snowflake",
            expected_fields=["gender_concept_id"],
            output_format="sql",
            field_mappings=[self.mapping],
            source_models=["cai_01_patient"],
            declared_joins=[],
        )

    def test_accepts_declared_mapping_table_lookup(self):
        """A mapping relation should be joined on its source-value column."""
        result = self._validate(
            "SELECT gm.gender_concept_id AS gender_concept_id "
            "FROM cai_01_patient AS p "
            "LEFT JOIN mapping_person_gender_concept_id AS gm "
            "ON p.sex = gm.sex"
        )

        self.assertTrue(result.valid)

    def test_rejects_missing_mapping_table_lookup(self):
        """A mapping-table field cannot silently use an inline source value."""
        result = self._validate(
            "SELECT p.sex AS gender_concept_id "
            "FROM cai_01_patient AS p"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "SQL does not reference declared mapping tables: "
            "mapping_person_gender_concept_id",
            result.errors,
        )

    def test_rejects_wrong_mapping_table_join(self):
        """The lookup join must match each declared source field by name."""
        result = self._validate(
            "SELECT gm.gender_concept_id AS gender_concept_id "
            "FROM cai_01_patient AS p "
            "LEFT JOIN mapping_person_gender_concept_id AS gm "
            "ON p.patient_id = gm.sex"
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                error.startswith(
                    "SQL contains invalid mapping-table join:"
                )
                for error in result.errors
            )
        )


class JoinSqlValidationTest(unittest.TestCase):
    source_models = ["source_patient", "source_visit"]
    declared_joins = [
        SourceJoin(
            join_type="left",
            left=SourceFieldReference(
                model="source_patient",
                field="patient_id",
            ),
            right=SourceFieldReference(
                model="source_visit",
                field="patient_id",
            ),
        )
    ]

    def _validate(self, sql: str):
        """Validate SQL relations and joins against a two-model mapping."""
        return validate_sql(
            sql=sql,
            dialect="snowflake",
            expected_fields=["person_id"],
            source_models=self.source_models,
            declared_joins=self.declared_joins,
        )

    def test_accepts_declared_join_with_aliases(self):
        """A declared join type and equality should pass."""
        result = self._validate(
            "SELECT p.patient_id AS person_id "
            "FROM source_patient AS p "
            "LEFT JOIN source_visit AS v "
            "ON p.patient_id = v.patient_id"
        )

        self.assertTrue(result.valid)

    def test_rejects_undeclared_source_model(self):
        """Physical source relations must be declared by the mapping."""
        result = self._validate(
            "SELECT p.patient_id AS person_id "
            "FROM source_patient AS p "
            "LEFT JOIN source_lookup AS l "
            "ON p.patient_id = l.patient_id"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "SQL references undeclared source models: source_lookup",
            result.errors,
        )

    def test_rejects_cross_join(self):
        """Cross joins cannot substitute for a declared field equality."""
        result = self._validate(
            "SELECT p.patient_id AS person_id "
            "FROM source_patient AS p "
            "CROSS JOIN source_visit AS v"
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "cross joins and joins without ON are not allowed",
            result.errors,
        )

    def test_rejects_wrong_join_type(self):
        """An inner join cannot replace a declared left join."""
        result = self._validate(
            "SELECT p.patient_id AS person_id "
            "FROM source_patient AS p "
            "INNER JOIN source_visit AS v "
            "ON p.patient_id = v.patient_id"
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                error.startswith("SQL contains undeclared join:")
                for error in result.errors
            )
        )
        self.assertTrue(
            any(
                error.startswith("SQL is missing declared left join:")
                for error in result.errors
            )
        )

    def test_rejects_wrong_join_fields(self):
        """Join keys must match the exact declared source fields."""
        result = self._validate(
            "SELECT p.patient_id AS person_id "
            "FROM source_patient AS p "
            "LEFT JOIN source_visit AS v "
            "ON p.patient_id = v.visit_id"
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                error.startswith("SQL contains undeclared join:")
                for error in result.errors
            )
        )


class UnionAllSqlValidationTest(unittest.TestCase):
    source_models = ["current_person", "historical_person"]
    union_all_models = ["current_person", "historical_person"]
    field_mappings = [
        FieldMapping(
            target_field="person_id",
            action="derive",
            source_fields=[
                SourceFieldReference(
                    model="current_person",
                    field="patient_id",
                ),
                SourceFieldReference(
                    model="historical_person",
                    field="patient_id",
                ),
            ],
        )
    ]

    def _validate(self, sql: str):
        return validate_sql(
            sql=sql,
            dialect="snowflake",
            expected_fields=["person_id"],
            field_mappings=self.field_mappings,
            source_models=self.source_models,
            declared_joins=[],
            union_all_models=self.union_all_models,
        )

    def test_accepts_one_explicit_branch_per_union_model(self):
        result = self._validate(
            "SELECT c.patient_id AS person_id FROM current_person AS c "
            "UNION ALL "
            "SELECT h.patient_id AS person_id FROM historical_person AS h"
        )

        self.assertTrue(result.valid, result.errors)

    def test_rejects_union_distinct(self):
        result = self._validate(
            "SELECT c.patient_id AS person_id FROM current_person AS c "
            "UNION "
            "SELECT h.patient_id AS person_id FROM historical_person AS h"
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any("UNION DISTINCT is not allowed" in error for error in result.errors)
        )

    def test_rejects_a_missing_declared_union_branch(self):
        result = self._validate(
            "SELECT c.patient_id AS person_id FROM current_person AS c"
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any("must use UNION ALL" in error for error in result.errors)
        )

    def test_accepts_typed_null_when_a_branch_has_no_source_mapping(self):
        mapping = FieldMapping(
            target_field="person_id",
            action="map",
            source_fields=[
                SourceFieldReference(
                    model="current_person",
                    field="patient_id",
                )
            ],
        )
        target = TargetField(
            name="person_id",
            data_type="integer",
            required=True,
        )

        result = validate_sql(
            sql=(
                "SELECT c.patient_id AS person_id "
                "FROM current_person AS c UNION ALL "
                "SELECT CAST(NULL AS INTEGER) AS person_id "
                "FROM historical_person AS h"
            ),
            dialect="snowflake",
            expected_fields=["person_id"],
            field_mappings=[mapping],
            target_fields=[target],
            source_models=self.source_models,
            declared_joins=[],
            union_all_models=self.union_all_models,
        )

        self.assertTrue(result.valid, result.errors)

    def test_accepts_one_mapping_table_lookup_per_union_branch(self):
        mapping = FieldMapping(
            target_field="person_id",
            action="derive",
            source_fields=[
                SourceFieldReference(
                    model="current_person",
                    field="patient_id",
                ),
                SourceFieldReference(
                    model="historical_person",
                    field="patient_id",
                ),
            ],
            mapping_table_name="mapping_person_person_id",
        )

        result = validate_sql(
            sql=(
                "SELECT m.person_id AS person_id "
                "FROM current_person AS c "
                "LEFT JOIN mapping_person_person_id AS m "
                "ON c.patient_id = m.patient_id UNION ALL "
                "SELECT m.person_id AS person_id "
                "FROM historical_person AS h "
                "LEFT JOIN mapping_person_person_id AS m "
                "ON h.patient_id = m.patient_id"
            ),
            dialect="snowflake",
            expected_fields=["person_id"],
            field_mappings=[mapping],
            source_models=self.source_models,
            declared_joins=[],
            union_all_models=self.union_all_models,
        )

        self.assertTrue(result.valid, result.errors)


if __name__ == "__main__":
    unittest.main()
