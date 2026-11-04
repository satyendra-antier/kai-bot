"""Temporal worker. `python -m kai.worker`."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from kai import activities
from kai.config import REDIS_URL, TASK_QUEUE, TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE
from kai.workflows import ConversationWorkflow, RunWorkflow

ACTIVITY_FNS = [
    activities.load_contracts,
    activities.interpret_turn,
    activities.waiting_prompt,
    activities.append_message,
    activities.fetch_step,
    activities.act_step,
    activities.persist_run,
    activities.judge_line_activity,
]


async def _heartbeat() -> None:
    import redis

    client = redis.Redis.from_url(REDIS_URL)
    while True:
        try:
            client.set("worker:heartbeat", "1", ex=15)
        except Exception:
            pass
        await asyncio.sleep(5)


async def main() -> None:
    client = None
    last_error = None
    for _ in range(60):
        try:
            client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
            break
        except Exception as exc:  # Temporal may still be creating the namespace.
            last_error = exc
            await asyncio.sleep(2)
    if client is None:
        raise RuntimeError(f"temporal is not reachable at {TEMPORAL_ADDRESS}: {last_error}")
    asyncio.create_task(_heartbeat())
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ConversationWorkflow, RunWorkflow],
        activities=ACTIVITY_FNS,
        activity_executor=ThreadPoolExecutor(max_workers=8),
        workflow_runner=SandboxedWorkflowRunner(restrictions=SandboxRestrictions.default.with_passthrough_modules("kai")),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
