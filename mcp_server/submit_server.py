import asyncio
import logging
import traceback

import requests
from fastmcp import FastMCP

from clients.stratus.configs.langgraph_tool_configs import LanggraphToolConfig
from clients.stratus.stratus_utils.get_logger import get_logger
from clients.stratus.tools.localization import get_resource_uid

logger = get_logger()
logger.info("Starting Submission MCP Server")

langgraph_tool_config = LanggraphToolConfig()

mcp = FastMCP("Submission MCP Server")


@mcp.tool(name="submit")
def submit(ans: str) -> dict[str, str]:
    """Submit task result to benchmark

    Args:
        ans (str): task result that the agent submits

    Returns:
        dict[str]: http response code and response text of benchmark submission server
    """

    logger.info("[submit_mcp] submit mcp called")
    # FIXME: reference url from config file, remove hard coding
    url = langgraph_tool_config.benchmark_submit_url
    headers = {"Content-Type": "application/json"}
    # Match curl behavior: send "\"yes\"" when ans is "yes"
    payload = {"solution": f'"{ans}"'}

    try:
        response = requests.post(url, json=payload, headers=headers)
        logger.info(f"[submit_mcp] Response status: {response.status_code}, text: {response.text}")
        return {"status": str(response.status_code), "text": str(response.text)}

    except Exception as e:
        logger.error(f"[submit_mcp] HTTP submission failed: {e}")
        return {"status": "N/A", "text": f"[submit_mcp] HTTP submission failed: {e}"}


@mcp.tool(name="localization")
async def localization(
    resource_type: str,
    resource_name: str,
    namespace: str,
) -> dict[str, str]:
    try:
        cmd = [
            "kubectl",
            "get",
            resource_type,
            resource_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.metadata.uid}",
        ]
        logger.info(f"[localization_mcp] Running command: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode().strip()
            logger.error(f"[localization_mcp] Error retrieving UID: {err_msg}")
            return {"uid": f"Error: {err_msg}"}
        uid = stdout.decode().strip()
        logger.info(f"[localization_mcp] Retrieved UID: {uid}")
        return {"uid": uid}
    except Exception as e:
        logger.error(f"[localization_mcp] Exception occurred: {e}")
        logger.error(traceback.format_exc())
        return {"uid": f"Exception: {e}"}
