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
