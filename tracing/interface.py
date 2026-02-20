"""
TracingComponent interface for IRE observability system.

Defines the contract for components that emit trace data.
Components don't need to inherit from this ABC directly — they
just need to call trace.record() at decision points. The ABC exists
to document the contract and validate during testing.

Usage:
    class MyComponent(TracingComponent):
        @property
        def component_name(self) -> str:
            return "my_component"

        def get_trace_schema(self) -> Dict[str, Any]:
            return {
                "decisions": ["decision_a", "decision_b"],
                "outputs": ["output_x", "output_y"]
            }

    register_component(MyComponent())
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level registry of components that emit traces
REGISTERED_COMPONENTS: Dict[str, Dict[str, Any]] = {}


class TracingComponent(ABC):
    """
    Interface for components that emit trace data.

    Every component in the research pipeline that makes decisions
    must implement this to participate in the tracing system.
    """

    @property
    @abstractmethod
    def component_name(self) -> str:
        """Unique name for this component in traces (e.g., 'rubric_loader')."""
        ...

    @abstractmethod
    def get_trace_schema(self) -> Dict[str, Any]:
        """
        Return the schema of decisions this component can emit.

        Used for documentation and validation. Example:
        {
            "decisions": ["rubric_loaded", "intent_activation"],
            "outputs": ["total_principles", "activated_count"]
        }
        """
        ...


def validate_trace_schema(component: TracingComponent) -> Dict[str, Any]:
    """
    Validate that a component's trace schema has required keys.

    Returns:
        {"valid": True/False, "errors": [...]}
    """
    errors = []
    schema = component.get_trace_schema()

    if not isinstance(schema, dict):
        return {"valid": False, "errors": ["get_trace_schema() must return a dict"]}

    if "decisions" not in schema:
        errors.append("Schema missing required key: 'decisions'")
    elif not isinstance(schema["decisions"], list):
        errors.append("'decisions' must be a list")

    if "outputs" not in schema:
        errors.append("Schema missing required key: 'outputs'")
    elif not isinstance(schema["outputs"], list):
        errors.append("'outputs' must be a list")

    return {"valid": len(errors) == 0, "errors": errors}


def register_component(component: TracingComponent) -> None:
    """
    Register a component in the trace registry.

    Validates the schema and raises ValueError if invalid.

    Args:
        component: A TracingComponent instance

    Raises:
        ValueError: If the component's trace schema is invalid
    """
    validation = validate_trace_schema(component)
    if not validation["valid"]:
        raise ValueError(
            f"Invalid trace schema for '{component.component_name}': "
            f"{', '.join(validation['errors'])}"
        )

    REGISTERED_COMPONENTS[component.component_name] = {
        "component_name": component.component_name,
        "schema": component.get_trace_schema(),
    }
    logger.info(f"Registered tracing component: {component.component_name}")


def get_registered_components() -> Dict[str, Dict[str, Any]]:
    """
    Return a copy of the registered components registry.

    Returns:
        Dict mapping component_name to registration info.
    """
    return dict(REGISTERED_COMPONENTS)
