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
