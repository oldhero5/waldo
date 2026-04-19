"""Base class for all workflow blocks."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Port:
    """Describes an input or output port on a block."""

    name: str
    type: str  # "image", "detections", "text", "any", "number"
    description: str = ""
    required: bool = True


@dataclass
class BlockResult:
    """Output from a block execution."""

    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)  # timing, counts, etc.


class BlockBase(ABC):
    """Abstract base for all workflow blocks."""

    # Override in subclasses
    name: str = "block"
    display_name: str = "Block"
    description: str = ""
    category: str = "general"
    input_ports: list[Port] = []
    output_ports: list[Port] = []

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        """Run the block on the given inputs. Returns outputs dict."""
        ...

    def validate_inputs(self, inputs: dict[str, Any]) -> list[str]:
        """Check that required inputs are present. Returns list of errors."""
        errors = []
        for port in self.input_ports:
            if port.required and port.name not in inputs:
                errors.append(f"Missing required input: {port.name}")
        return errors

    def to_schema(self) -> dict:
        """Return a JSON-serializable schema for the frontend block palette."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "inputs": [
                {"name": p.name, "type": p.type, "description": p.description, "required": p.required}
                for p in self.input_ports
            ],
            "outputs": [{"name": p.name, "type": p.type, "description": p.description} for p in self.output_ports],
            "config_schema": self._config_schema(),
        }

    def _config_schema(self) -> dict:
        """Override to provide configuration UI schema."""
        return {}
