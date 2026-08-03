from __future__ import annotations

import ast
import json
import math
import re
import tomllib

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import CodeType
from typing import Any


class MetricSpecError(ValueError):
    pass


# ---------------------------------------------------------------------
# Safe numerical functions
# ---------------------------------------------------------------------

def _relu(x: float) -> float:
    return max(0.0, float(x))


def _clip(x: float, lower: float, upper: float) -> float:
    x = float(x)
    lower = float(lower)
    upper = float(upper)

    if lower > upper:
        raise ValueError("clip lower bound exceeds upper bound")

    return min(max(x, lower), upper)


def _sigmoid(x: float) -> float:
    x = float(x)

    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)

    z = math.exp(x)
    return z / (1.0 + z)


def _softplus(x: float) -> float:
    x = float(x)

    # Numerically stable implementation.
    if x > 50:
        return x

    if x < -50:
        return math.exp(x)

    return math.log1p(math.exp(x))


SAFE_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log1p": math.log1p,
    "exp": math.exp,
    "tanh": math.tanh,
    "relu": _relu,
    "clip": _clip,
    "sigmoid": _sigmoid,
    "softplus": _softplus,
}


# ---------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------

FEATURE_PATHS = frozenset({
    "fspl",
    "dist_m",
    "delta_diffra",

    "terrain.core",
    "terrain.fresnel",
    "terrain.boundary",

    "vegetation.core",
    "vegetation.fresnel",
    "vegetation.boundary",

    "buildings.core",
    "buildings.fresnel",
    "buildings.boundary",

    "terrain_peak.count",

    "terrain_peak.vv_0",
    "terrain_peak.d_norm_0",
    "terrain_peak.vv_1",
    "terrain_peak.d_norm_1",
    "terrain_peak.vv_2",
    "terrain_peak.d_norm_2",

    "max_obstruction_angle_rad",
    "tx_rx_elevation_angle_rad",
    "tx_near_terminal_clearance_m",
    "rx_near_terminal_clearance_m",

    "freq_mhz"
})


# ---------------------------------------------------------------------
# Specification parsing
# ---------------------------------------------------------------------

def _parse_metric_spec(spec: str) -> dict[str, Any]:
    if not isinstance(spec, str) or not spec.strip():
        raise MetricSpecError(
            "Metric specification must be a nonempty string"
        )

    try:
        parsed = json.loads(spec)
    except json.JSONDecodeError:
        try:
            parsed = tomllib.loads(spec)
        except tomllib.TOMLDecodeError as error:
            raise MetricSpecError(
                "Metric specification is neither valid JSON nor valid TOML"
            ) from error

    if not isinstance(parsed, dict):
        raise MetricSpecError(
            "Metric specification root must be an object or table"
        )

    expressions = parsed.get("expressions")

    if not isinstance(expressions, dict) or not expressions:
        raise MetricSpecError(
            "Metric specification requires a nonempty "
            "'expressions' object or table"
        )

    reserved_names = {
        "params",
        "terrain",
        "vegetation",
        "buildings",
        "terrain_peak",
    }

    normalized_expressions: dict[str, str] = {}

    for name, expression in expressions.items():
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or name.startswith("_")
        ):
            raise MetricSpecError(
                f"Invalid expression name: {name!r}"
            )

        if (
            name in SAFE_FUNCTIONS
            or name in FEATURE_PATHS
            or name in reserved_names
        ):
            raise MetricSpecError(
                f"Reserved expression name: {name!r}"
            )

        if not isinstance(expression, str) or not expression.strip():
            raise MetricSpecError(
                f"Expression {name!r} must be a nonempty string"
            )

        normalized_expressions[name] = expression.strip()

    result = parsed.get("result")

    if (
        not isinstance(result, str)
        or result not in normalized_expressions
    ):
        raise MetricSpecError(
            "'result' must name one expression declared "
            "in 'expressions'"
        )

    parameters = parsed.get("parameters", {})

    if not isinstance(parameters, dict):
        raise MetricSpecError(
            "'parameters' must be an object or table"
        )

    normalized_parameters: dict[str, float] = {}

    for name, value in parameters.items():
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or name.startswith("_")
        ):
            raise MetricSpecError(
                f"Invalid parameter name: {name!r}"
            )

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            raise MetricSpecError(
                f"Parameter {name!r} must be numeric"
            )

        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            raise MetricSpecError(
                f"Parameter {name!r} must be finite"
            )

        normalized_parameters[name] = numeric_value

    return {
        "version": parsed.get("version", 1),
        "result": result,
        "expressions": normalized_expressions,
        "parameters": normalized_parameters,
    }

# ---------------------------------------------------------------------
# Expression reference conversion
# ---------------------------------------------------------------------

def _attribute_path(node: ast.AST) -> str | None:
    parts: list[str] = []

    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value

    if not isinstance(node, ast.Name):
        return None

    parts.append(node.id)
    parts.reverse()

    return ".".join(parts)

class _ReferenceRewriter(ast.NodeTransformer):
    def __init__(
        self,
        feature_paths: set[str] | frozenset[str],
        parameter_names: set[str],
        expression_names: set[str],
    ):
        self.feature_paths = set(feature_paths)

        self.parameter_paths = {
            f"params.{name}"
            for name in parameter_names
        }

        self.expression_names = set(expression_names)

        self.dependencies: set[str] = set()

        self.reference_to_alias: dict[str, str] = {}
        self.alias_to_reference: dict[str, str] = {}

    def _alias_for(self, reference: str) -> str:
        alias = self.reference_to_alias.get(reference)

        if alias is None:
            alias = (
                f"__metric_value_"
                f"{len(self.reference_to_alias)}"
            )

            self.reference_to_alias[reference] = alias
            self.alias_to_reference[alias] = reference

        return alias

    def visit_Attribute(
        self,
        node: ast.Attribute,
    ) -> ast.AST:
        path = _attribute_path(node)

        if path is None:
            raise MetricSpecError(
                "Invalid attribute expression"
            )

        if (
            path not in self.feature_paths
            and path not in self.parameter_paths
        ):
            raise MetricSpecError(
                f"Unknown metric variable: {path}"
            )

        return ast.copy_location(
            ast.Name(
                id=self._alias_for(path),
                ctx=ast.Load(),
            ),
            node,
        )

    def visit_Name(
        self,
        node: ast.Name,
    ) -> ast.AST:
        name = node.id

        if name in SAFE_FUNCTIONS:
            return node

        # Scalar features such as fspl, dist_m and freq_mhz.
        if name in self.feature_paths:
            return ast.copy_location(
                ast.Name(
                    id=self._alias_for(name),
                    ctx=ast.Load(),
                ),
                node,
            )

        # A reference to another named expression.
        if name in self.expression_names:
            self.dependencies.add(name)
            return node

        raise MetricSpecError(
            f"Unknown name in metric expression: {name}"
        )

class _NumericConstantRewriter(ast.NodeTransformer):
    """
    Convert numeric constants to float.

    This prevents expressions such as 10 ** 100000000 from triggering
    expensive arbitrary-precision integer calculations.
    """

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bool):
            return node

        if isinstance(node.value, (int, float)):
            value = float(node.value)

            if not math.isfinite(value):
                raise MetricSpecError(
                    "Numeric constants must be finite"
                )

            return ast.copy_location(
                ast.Constant(value=value),
                node,
            )

        raise MetricSpecError(
            "Only numeric and Boolean constants are allowed"
        )


# ---------------------------------------------------------------------
# AST validation
# ---------------------------------------------------------------------

_ALLOWED_NODE_TYPES = (
    ast.Expression,

    ast.BinOp,
    ast.UnaryOp,
    ast.IfExp,
    ast.Compare,
    ast.Call,

    ast.Name,
    ast.Load,
    ast.Constant,

    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,

    ast.UAdd,
    ast.USub,

    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


class _ExpressionValidator(ast.NodeVisitor):
    MAX_NODES = 200
    MAX_EXPRESSION_LENGTH = 4000

    def __init__(self):
        self.node_count = 0

    def generic_visit(self, node: ast.AST) -> None:
        self.node_count += 1

        if self.node_count > self.MAX_NODES:
            raise MetricSpecError(
                "Metric expression is too complex"
            )

        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise MetricSpecError(
                f"Unsupported expression element: "
                f"{type(node).__name__}"
            )

        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            raise MetricSpecError(
                "Only direct calls to approved functions are allowed"
            )

        if node.func.id not in SAFE_FUNCTIONS:
            raise MetricSpecError(
                f"Function {node.func.id!r} is not allowed"
            )

        if node.keywords:
            raise MetricSpecError(
                "Keyword arguments are not allowed"
            )

        self.generic_visit(node)


# ---------------------------------------------------------------------
# Feature resolution
# ---------------------------------------------------------------------

_PEAK_PATTERN = re.compile(
    r"^(vv|d_norm)_(0|1|2)$"
)


def _finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise ValueError(
            f"Feature {name!r} must be numeric"
        )

    result = float(value)

    if not math.isfinite(result):
        raise ValueError(
            f"Feature {name!r} must be finite"
        )

    return result


def _resolve_feature(
    features: Mapping[str, Any],
    path: str,
) -> float:
    if path == "terrain_peak.count":
        peaks = features.get("terrain_peaks_vv") or []

        if not isinstance(peaks, list):
            raise ValueError(
                "'terrain_peaks_vv' must be a list"
            )

        return float(len(peaks))

    if path.startswith("terrain_peak."):
        field = path.split(".", 1)[1]
        match = _PEAK_PATTERN.fullmatch(field)

        if match is None:
            raise ValueError(
                f"Invalid terrain peak path: {path}"
            )

        field_name, index_text = match.groups()
        index = int(index_text)

        peaks = features.get("terrain_peaks_vv") or []

        if not isinstance(peaks, list):
            raise ValueError(
                "'terrain_peaks_vv' must be a list"
            )

        # Structurally absent peaks have harmless defaults.
        if index >= len(peaks):
            return 0.0

        peak = peaks[index]

        if not isinstance(peak, Mapping):
            raise ValueError(
                f"Terrain peak {index} must be an object"
            )

        source_key = (
            "v_v"
            if field_name == "vv"
            else "d_norm"
        )

        if source_key not in peak:
            raise ValueError(
                f"Terrain peak {index} is missing {source_key!r}"
            )

        return _finite_float(
            peak[source_key],
            path,
        )

    value: Any = features

    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise KeyError(
                f"Required metric feature is missing: {path}"
            )

        value = value[part]

    return _finite_float(value, path)


# ---------------------------------------------------------------------
# Compiled metric
# ---------------------------------------------------------------------

def _topological_order(
    dependencies: Mapping[str, set[str]],
    result_name: str,
) -> tuple[str, ...]:
    """
    Validate the complete dependency graph and return only the
    expressions required to calculate result_name.
    """

    order: list[str] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        status = state.get(name, 0)

        if status == 2:
            return

        if status == 1:
            start = stack.index(name)
            cycle = stack[start:] + [name]

            raise MetricSpecError(
                "Circular expression dependency: "
                + " -> ".join(cycle)
            )

        state[name] = 1
        stack.append(name)

        for dependency in sorted(dependencies[name]):
            visit(dependency)

        stack.pop()
        state[name] = 2
        order.append(name)

    # Validate cycles in every declared expression, including unused ones.
    for name in dependencies:
        visit(name)

    required: set[str] = set()

    def collect_required(name: str) -> None:
        if name in required:
            return

        required.add(name)

        for dependency in dependencies[name]:
            collect_required(dependency)

    collect_required(result_name)

    return tuple(
        name
        for name in order
        if name in required
    )

@dataclass(frozen=True)
class CompiledMetric:
    expressions: Mapping[str, str]
    parameters: Mapping[str, float]

    codes: Mapping[str, CodeType]

    alias_to_reference: Mapping[
        str,
        Mapping[str, str],
    ]

    evaluation_order: tuple[str, ...]
    result_name: str

    def __call__(
        self,
        features: Mapping[str, Any],
    ) -> float:
        derived_values: dict[str, float] = {}

        for expression_name in self.evaluation_order:
            local_values: dict[str, float] = {
                **derived_values
            }

            references = self.alias_to_reference[
                expression_name
            ]

            for alias, reference in references.items():
                if reference.startswith("params."):
                    parameter_name = reference.split(
                        ".",
                        1,
                    )[1]

                    local_values[alias] = self.parameters[
                        parameter_name
                    ]

                else:
                    local_values[alias] = _resolve_feature(
                        features,
                        reference,
                    )

            try:
                result = eval(
                    self.codes[expression_name],
                    {
                        "__builtins__": {},
                        **SAFE_FUNCTIONS,
                    },
                    local_values,
                )

            except (
                ArithmeticError,
                ValueError,
                TypeError,
                NameError,
            ) as error:
                raise ValueError(
                    f"Metric expression "
                    f"{expression_name!r} failed: {error}"
                ) from error

            if isinstance(result, bool):
                raise ValueError(
                    f"Metric expression "
                    f"{expression_name!r} returned "
                    f"a Boolean value"
                )

            try:
                value = float(result)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Metric expression "
                    f"{expression_name!r} did not return "
                    f"a number"
                ) from error

            if not math.isfinite(value):
                raise ValueError(
                    f"Metric expression "
                    f"{expression_name!r} returned a "
                    f"non-finite value: {value}"
                )

            derived_values[expression_name] = value

        metric = derived_values[self.result_name]

        if metric < 0:
            raise ValueError(
                f"Final metric expression returned a "
                f"negative value: {metric}"
            )

        return metric
    
def compile_metric_spec(
    spec: str,
) -> CompiledMetric:
    parsed = _parse_metric_spec(spec)

    expression_names = set(parsed["expressions"])

    codes: dict[str, CodeType] = {}

    aliases: dict[
        str,
        Mapping[str, str],
    ] = {}

    dependencies: dict[
        str,
        set[str],
    ] = {}

    for expression_name, expression in (
        parsed["expressions"].items()
    ):
        if (
            len(expression)
            > _ExpressionValidator.MAX_EXPRESSION_LENGTH
        ):
            raise MetricSpecError(
                f"Expression {expression_name!r} "
                f"is too long"
            )

        try:
            tree = ast.parse(
                expression,
                mode="eval",
            )

        except SyntaxError as error:
            raise MetricSpecError(
                f"Invalid expression "
                f"{expression_name!r}: {error.msg}"
            ) from error

        rewriter = _ReferenceRewriter(
            feature_paths=FEATURE_PATHS,
            parameter_names=set(parsed["parameters"]),
            expression_names=expression_names,
        )

        tree = rewriter.visit(tree)
        tree = _NumericConstantRewriter().visit(tree)

        ast.fix_missing_locations(tree)

        _ExpressionValidator().visit(tree)

        codes[expression_name] = compile(
            tree,
            filename=(
                f"<metric-expression:"
                f"{expression_name}>"
            ),
            mode="eval",
        )

        aliases[expression_name] = dict(
            rewriter.alias_to_reference
        )

        dependencies[expression_name] = set(
            rewriter.dependencies
        )

    evaluation_order = _topological_order(
        dependencies,
        parsed["result"],
    )

    return CompiledMetric(
        expressions=parsed["expressions"],
        parameters=parsed["parameters"],
        codes=codes,
        alias_to_reference=aliases,
        evaluation_order=evaluation_order,
        result_name=parsed["result"],
    )
