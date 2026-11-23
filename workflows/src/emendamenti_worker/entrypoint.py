"""Entrypoint del worker: descubre e serve ogni workflow del package emendamenti_worker.workflows."""

import asyncio

import mistralai.workflows as workflows


async def main() -> None:
    workflow_classes = workflows.discover_all_workflows_in_package("emendamenti_worker.workflows")
    await workflows.run_worker(workflow_classes)


if __name__ == "__main__":
    asyncio.run(main())
