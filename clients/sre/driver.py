import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml
from langchain_core.messages import HumanMessage, SystemMessage

# Add SREGym root to path
sregym_root = Path(__file__).resolve().parent
if str(sregym_root) not in sys.path:
    sys.path.insert(0, str(sregym_root))

from clients.sre.sre_agent import SREAgent
from logger import init_logger

init_logger()

logger = logging.getLogger("all.agent.driver")


def get_api_base_url() -> str:
    """Get the conductor API base URL."""
    host = os.getenv("API_HOSTNAME", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def get_app_info() -> dict:
    """Get application info from conductor API."""
    api_url = f"{get_api_base_url()}/get_app"
    logger.info(f"Fetching app info from {api_url}")

    try:
        response = requests.get(api_url)
        response.raise_for_status()
        app_info = response.json()
        logger.info(f"App info: {app_info}")
        return app_info
    except Exception as e:
        logger.error(f"Failed to get app info: {e}")
        raise


def get_problem_id() -> str:
    """Get current problem ID from conductor API."""
    api_url = f"{get_api_base_url()}/get_problem"
    logger.info(f"Fetching problem ID from {api_url}")

    try:
        response = requests.get(api_url)
        response.raise_for_status()
        problem_data = response.json()
        problem_id = problem_data.get("problem_id")
        logger.info(f"Problem ID: {problem_id}")
        return problem_id
    except Exception as e:
        logger.error(f"Failed to get problem ID: {e}")
        raise


def wait_for_ready_stage(timeout: int = 300) -> str:
    """
    Wait for conductor to reach a submission-ready stage (diagnosis or mitigation).

    Args:
        timeout: Maximum seconds to wait

    Returns:
        Current stage name

    Raises:
        TimeoutError: If timeout is reached before ready
    """
    import time

    api_url = f"{get_api_base_url()}/status"
    allowed_stages = {"diagnosis", "mitigation"}
    start_time = time.time()

    logger.info("Waiting for conductor to reach submission-ready stage...")

    while time.time() - start_time < timeout:
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            status_data = response.json()
            stage = status_data.get("stage")

            if stage in allowed_stages:
                logger.info(f"Conductor ready at stage: {stage}")
                return stage
            else:
                logger.debug(f"Current stage: {stage}, waiting for {allowed_stages}...")
                time.sleep(1)

        except Exception as e:
            logger.debug(f"Error checking status: {e}, retrying...")
            time.sleep(1)

    raise TimeoutError(f"Conductor did not reach ready stage within {timeout} seconds")


def save_results(
    logs_dir: Path,
    problem_id: str,
    return_code: int,
    usage_metrics: dict,
) -> None:
    """
    Save run results to JSON file.

    Args:
        logs_dir: Directory containing logs
        problem_id: Problem identifier
        return_code: Claude Code return code
        usage_metrics: Token usage metrics
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = logs_dir / f"claudecode_results_{problem_id}_{timestamp}.json"

    results = {
        "problem_id": problem_id,
        "timestamp": timestamp,
        "return_code": return_code,
        "success": return_code == 0,
        "usage_metrics": usage_metrics,
    }

    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Saved results to {results_file}")


async def main():
    """Main entry point for SRE agent driver."""
    parser = argparse.ArgumentParser(description="Run SRE agent on SREGym tasks")
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("MODEL_ID", "llama3.1-8b"),
        help="Model to use for SRE agent (default: from MODEL_ID env var or llama3.1-8b)",
    )
    parser.add_argument(
        "--logs-dir",
        type=str,
        default=os.environ.get("AGENT_LOGS_DIR", "./logs/sre_agent"),
        help="Directory to store logs (default: ./logs/sre_agent)",
    )
    parser.add_argument(
        "--sessions-dir",
        type=str,
        default=None,
        help="SRE agent sessions directory (default: logs-dir/sessions)",
    )
    parser.add_argument(
        "--no-auto-install",
        action="store_true",
        help="Disable auto-installation of SRE agent CLI if not found",
    )

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("Starting SRE agent for SREGym")
    logger.info(f"Model: {args.model}")
    logger.info(f"Logs directory: {args.logs_dir}")
    logger.info("=" * 80)

    # Wait for conductor to be ready
    try:
        stage = wait_for_ready_stage(timeout=300)
        logger.info(f"Conductor is ready at stage: {stage}")
    except TimeoutError as e:
        logger.error(f"Timeout waiting for conductor: {e}")
        sys.exit(1)

    # Get problem information
    try:
        app_info = get_app_info()
        problem_id = get_problem_id()
    except Exception as e:
        logger.error(f"Failed to get problem information: {e}")
        sys.exit(1)

    # Build instruction
    diagnosis_agent_prompts = yaml.safe_load(open("./clients/sre/agent_prompts.yaml"))
    messages = [
        SystemMessage(diagnosis_agent_prompts["system"]),
        HumanMessage(
            diagnosis_agent_prompts["user"].format(
                app_name=app_info["app_name"],
                app_namespace=app_info["namespace"],
                app_description=app_info["descriptions"],
            )
        ),
    ]

    # Initialize SRE agent
    logs_dir = Path(args.logs_dir)

    agent = SREAgent(
        logs_dir=logs_dir,
        model_name=args.model,
    )

    # Run SRE agent
    logger.info("Starting SRE agent execution...")
    return_code = await agent.arun(messages)

    # Get usage metrics
    usage_metrics = agent.get_usage_metrics()

    # Save results
    # save_results(logs_dir, problem_id, return_code, usage_metrics)

    # Log summary
    logger.info("=" * 80)
    logger.info("SRE agent execution completed")
    logger.info(f"Return code: {return_code}")
    logger.info(f"Usage metrics: {usage_metrics}")
    logger.info("=" * 80)

    sys.exit(return_code)


if __name__ == "__main__":
    asyncio.run(main())
