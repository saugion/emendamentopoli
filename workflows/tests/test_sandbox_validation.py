"""Regression test: every discovered workflow must validate inside the Temporal
workflow sandbox with the same restrictions the managed worker uses.

httpx subclasses urllib.request.Request at import time and urllib.request is
restricted in the sandbox, so no module reachable from a workflow module may
import httpx at module level: import it lazily inside the activity functions.
"""

import asyncio

import mistralai.workflows as workflows
import temporalio.workflow
from mistralai.workflows.core.sandbox import get_sandbox_restrictions
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner


def test_all_workflows_validate_in_sandbox():
    classes = workflows.discover_all_workflows_in_package("amendments_worker.workflows")
    assert classes

    async def run():
        runner = SandboxedWorkflowRunner(restrictions=get_sandbox_restrictions())
        for cls in classes:
            defn = temporalio.workflow._Definition.from_class(cls)
            runner.prepare_workflow(defn)

    asyncio.run(run())


def test_workflow_run_methods_avoid_nondeterministic_calls():
    """The run method executes inside the sandbox: datetime.now/utcnow/today are
    restricted there. Row building must happen inside activities instead."""
    import ast
    import inspect
    import textwrap

    classes = workflows.discover_all_workflows_in_package("amendments_worker.workflows")
    assert classes
    for cls in classes:
        src = textwrap.dedent(inspect.getsource(cls.run))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "datetime"
                and node.func.value.attr == "datetime"
                and node.func.attr in {"now", "utcnow", "today"}
            ):
                raise AssertionError(
                    f"{cls.__name__}.run calls datetime.{node.func.attr}() in workflow code"
                )


def test_workflow_run_methods_only_await_activities():
    """The run method executes inside the sandbox: it may only await functions
    decorated with @workflows.activity (activities run outside the sandbox), so
    a plain helper doing I/O would crash with RestrictedWorkflowAccessError."""
    import ast
    import inspect
    import textwrap
    from pathlib import Path

    pkg = Path(workflows.__file__).resolve().parent
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text())
        activities = set()
        module_funcs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_funcs.add(node.name)
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    if isinstance(target, ast.Attribute) and target.attr == "activity":
                        activities.add(node.name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
                src = textwrap.dedent(inspect.getsource(node))
                run_tree = ast.parse(src)
                called = {
                    sub.value.func.id
                    for sub in ast.walk(run_tree)
                    if isinstance(sub, ast.Await)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Name)
                }
                local = {
                    n.name for n in ast.walk(run_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                bad = {c for c in called - activities - local if c in module_funcs}
                assert not bad, f"{path.name}: run awaits plain functions {sorted(bad)}"
