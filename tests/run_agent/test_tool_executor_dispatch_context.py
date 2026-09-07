"""Regression guard for sequential tool-dispatch agent context."""

import ast
import inspect
import textwrap

from agent.tool_executor import execute_tool_calls_sequential


def test_sequential_registry_dispatches_preserve_agent_context():
    """Both quiet and normal registry paths retain agent for run-usage receipts."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(execute_tool_calls_sequential)))
    registry_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "handle_function_call"
    ]

    assert len(registry_calls) == 2
    assert all(
        any(
            keyword.arg == "agent"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "agent"
            for keyword in call.keywords
        )
        for call in registry_calls
    )
