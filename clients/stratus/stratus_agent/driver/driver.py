import asyncio
from pathlib import Path

import yaml

from clients.stratus.stratus_agent.diagnosis_agent import main as diagnosis_task_main
from clients.stratus.stratus_agent.localization_agent import main as localization_task_main
from clients.stratus.stratus_agent.mitigation_agent import main as mitigation_agent_main
from clients.stratus.stratus_agent.rollback_agent import main as rollback_agent_main
from clients.stratus.stratus_utils.get_logger import get_logger

logger = get_logger()


async def mitigation_task_main():
    # run rollback, reflect, and retry for mitigation and rollback agent
    # note: not implementing a `mitigation_task_main()` like other agents above for rollback, reflect, and retry is due to these considerations
    #   1. keep each agent's main() method only about running that specific agent's loop until agent's submission
    #   2. mitigation agent is special as when we refer to "mitigation" as a task for the Stratus agent, we refer to the
    #      rollback, reflect, retry pipeline, which uses rollback agent too. Implementing logic about rollback agent
    #      inside mitigation agent's method seems against good SE practice.

    # getting some configs
    file_parent_dir = Path(__file__).resolve().parent.parent
    mitigation_agent_config_path = file_parent_dir.parent / "configs" / "mitigation_agent_config.yaml"
    mitigation_agent_config = yaml.safe_load(open(mitigation_agent_config_path, "r"))
    mitigation_agent_max_step = mitigation_agent_config["max_step"]
    mitigation_agent_prompt_path = file_parent_dir.parent / "configs" / mitigation_agent_config["prompts_path"]
    mitigation_agent_max_retry_attempts = mitigation_agent_config["max_retry_attempts"]
    mitigation_agent_retry_mode = mitigation_agent_config["retry_mode"]

    rollback_agent_config_path = file_parent_dir.parent / "configs" / "rollback_agent_config.yaml"
    rollback_agent_config = yaml.safe_load(open(rollback_agent_config_path, "r"))
    rollback_agent_max_step = rollback_agent_config["max_step"]
    rollback_agent_prompt_path = file_parent_dir.parent / "configs" / rollback_agent_config["prompts_path"]

    # mitigation task in plain English:
    if mitigation_agent_retry_mode == "none":
        # if the retry mode is none, just run mitigation agent once.
        await mitigation_agent_main()
    elif mitigation_agent_retry_mode == "naive":
        # if the retry mode is naive, run mitigation agent with retry but no rollback agent.
        pass
    elif mitigation_agent_retry_mode == "validate":
        # if the retry mode is validation, run mitigation agent with rollback and weak oracle.
        pass


async def main():
    # run diagnosis agent 2 times
    # here, running the file's main function should suffice.
    # 1 for noop diagnosis
    logger.info("*" * 25 + "Starting [diagnosis agent] for [NOOP detection]" + "*" * 25)
    await diagnosis_task_main()
    logger.info("*" * 25 + "Finished [diagnosis agent]" + "*" * 25)

    # 1 for faulty diagnosis
    logger.info("*" * 25 + "Starting [diagnosis agent] for [Faulty detection]" + "*" * 25)
    await diagnosis_task_main()
    logger.info("*" * 25 + "Finished [diagnosis agent]" + "*" * 25)

    # run localization agent 1 time for localization
    # (BTS it's just diagnosis agent with different prompts)
    # here, running the file's main function should suffice
    logger.info("*" * 25 + "Starting [localization agent] for [localization]" + "*" * 25)
    await localization_task_main()
    logger.info("*" * 25 + "Finished [localization agent]" + "*" * 25)

    # run mitigation task 1 time for mitigation
    # it includes retry logics
    logger.info("*" * 25 + "Starting [mitigation agent] for [mitigation]" + "*" * 25)
    await mitigation_task_main()
    logger.info("*" * 25 + "Finished [mitigation agent]" + "*" * 25)


if __name__ == "__main__":
    asyncio.run(main())
