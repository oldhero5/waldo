"""Logic and branching blocks — control workflow execution flow."""

from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class ConditionalBlock(BlockBase):
    name = "conditional"
    display_name = "If / Else"
    description = "Continue execution only if a condition is met (e.g., detection count > 0)."
    category = "logic"
    input_ports = [
        Port("value", "any", "Value to check"),
        Port("data", "any", "Data to pass through if condition is true", required=False),
    ]
    output_ports = [
        Port("passed", "any", "Data if condition was true"),
        Port("result", "any", "Boolean result of the check"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        value = inputs.get("value")
        data = inputs.get("data", value)
        operator = self.config.get("operator", "exists")
        threshold = self.config.get("threshold", 0)

        passed = False
        if operator == "exists":
            passed = value is not None and value != 0 and value != [] and value != ""
        elif operator == "gt":
            passed = isinstance(value, int | float) and value > threshold
        elif operator == "lt":
            passed = isinstance(value, int | float) and value < threshold
        elif operator == "eq":
            passed = value == threshold
        elif operator == "gte":
            passed = isinstance(value, int | float) and value >= threshold

        return BlockResult(
            outputs={"passed": data if passed else None, "result": passed},
            metadata={"condition": operator, "threshold": threshold, "passed": passed},
        )

    def _config_schema(self) -> dict:
        return {
            "operator": {"type": "string", "default": "exists", "label": "Operator (exists, gt, lt, eq, gte)"},
            "threshold": {"type": "number", "default": 0, "label": "Threshold value"},
        }


class ExpressionBlock(BlockBase):
    name = "expression"
    display_name = "Expression"
    description = "Evaluate a simple math or string expression on input values."
    category = "logic"
    input_ports = [
        Port("a", "any", "First value"),
        Port("b", "any", "Second value", required=False),
    ]
    output_ports = [Port("result", "any", "Expression result")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        a = inputs.get("a", 0)
        b = inputs.get("b", 0)
        op = self.config.get("operation", "add")

        if op == "add":
            result = a + b
        elif op == "subtract":
            result = a - b
        elif op == "multiply":
            result = a * b
        elif op == "divide":
            result = a / b if b != 0 else 0
        elif op == "format":
            template = self.config.get("template", "{a}")
            result = template.replace("{a}", str(a)).replace("{b}", str(b))
        else:
            result = a

        return BlockResult(outputs={"result": result}, metadata={"operation": op})

    def _config_schema(self) -> dict:
        return {
            "operation": {
                "type": "string",
                "default": "add",
                "label": "Operation (add, subtract, multiply, divide, format)",
            },
            "template": {"type": "string", "default": "{a}", "label": "Format template (for format operation)"},
        }
