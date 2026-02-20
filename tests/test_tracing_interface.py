"""
Tests for src/tracing/interface.py — TracingComponent ABC and registry.

Ralph Run 2 of the Observability System build plan.
"""

from typing import Any, Dict

import pytest

from src.tracing.interface import (
    TracingComponent,
    validate_trace_schema,
    register_component,
    get_registered_components,
    REGISTERED_COMPONENTS,
)


# ============================================================================
# Concrete test implementations
# ============================================================================

class MockRubricComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "rubric_loader"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "decisions": ["rubric_loaded", "intent_activation"],
            "outputs": ["total_principles", "activated_count"],
        }


class MockQualityComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "quality_scorer"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "decisions": ["quality_assessment"],
            "outputs": ["overall_score", "passing", "gaps"],
        }


class EmptyDecisionsComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "empty_decisions"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "decisions": [],
            "outputs": ["some_output"],
        }


class MissingDecisionsComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "bad_no_decisions"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "outputs": ["x"],
        }


class MissingOutputsComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "bad_no_outputs"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "decisions": ["x"],
        }


class InvalidDecisionsTypeComponent(TracingComponent):
    @property
    def component_name(self) -> str:
        return "bad_decisions_type"

    def get_trace_schema(self) -> Dict[str, Any]:
        return {
            "decisions": "not_a_list",
            "outputs": ["x"],
        }


# ============================================================================
# TestTracingComponent
# ============================================================================

class TestTracingComponent:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            TracingComponent()

    def test_concrete_implementation(self):
        comp = MockRubricComponent()
        assert comp.component_name == "rubric_loader"
        schema = comp.get_trace_schema()
        assert "decisions" in schema
        assert "outputs" in schema
        assert "rubric_loaded" in schema["decisions"]

    def test_component_name_required(self):
        """Subclass without component_name raises TypeError."""
        with pytest.raises(TypeError):
            class NoName(TracingComponent):
                def get_trace_schema(self):
                    return {"decisions": [], "outputs": []}
            NoName()

    def test_get_trace_schema_required(self):
        """Subclass without get_trace_schema raises TypeError."""
        with pytest.raises(TypeError):
            class NoSchema(TracingComponent):
                @property
                def component_name(self):
                    return "test"
            NoSchema()


# ============================================================================
# TestValidateTraceSchema
# ============================================================================

class TestValidateTraceSchema:
    def test_valid_schema(self):
        comp = MockRubricComponent()
        result = validate_trace_schema(comp)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_missing_decisions(self):
        comp = MissingDecisionsComponent()
        result = validate_trace_schema(comp)
        assert result["valid"] is False
        assert any("decisions" in e for e in result["errors"])

    def test_missing_outputs(self):
        comp = MissingOutputsComponent()
        result = validate_trace_schema(comp)
        assert result["valid"] is False
        assert any("outputs" in e for e in result["errors"])

    def test_empty_decisions(self):
        """Empty decisions list is valid (component may only emit outputs)."""
        comp = EmptyDecisionsComponent()
        result = validate_trace_schema(comp)
        assert result["valid"] is True

    def test_invalid_decisions_type(self):
        comp = InvalidDecisionsTypeComponent()
        result = validate_trace_schema(comp)
        assert result["valid"] is False
        assert any("list" in e for e in result["errors"])


# ============================================================================
# TestComponentRegistry
# ============================================================================

class TestComponentRegistry:
    def setup_method(self):
        """Clear registry before each test."""
        REGISTERED_COMPONENTS.clear()

    def test_register_component(self):
        comp = MockRubricComponent()
        register_component(comp)
        assert "rubric_loader" in REGISTERED_COMPONENTS
        assert REGISTERED_COMPONENTS["rubric_loader"]["component_name"] == "rubric_loader"

    def test_register_invalid_schema(self):
        comp = MissingDecisionsComponent()
        with pytest.raises(ValueError, match="Invalid trace schema"):
            register_component(comp)

    def test_get_registered_components(self):
        register_component(MockRubricComponent())
        register_component(MockQualityComponent())
        result = get_registered_components()
        assert len(result) == 2
        assert "rubric_loader" in result
        assert "quality_scorer" in result

    def test_duplicate_registration(self):
        """Registering same name twice updates (no error)."""
        register_component(MockRubricComponent())
        register_component(MockRubricComponent())  # Should not raise
        assert len(REGISTERED_COMPONENTS) == 1

    def test_registry_returns_copy(self):
        """Modifying returned dict doesn't affect registry."""
        register_component(MockRubricComponent())
        result = get_registered_components()
        result["fake_component"] = {"fake": True}
        assert "fake_component" not in REGISTERED_COMPONENTS
