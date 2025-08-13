import logging

import requests
from fastmcp import FastMCP

from clients.stratus.configs.langgraph_tool_configs import LanggraphToolConfig

logger = logging.getLogger("Submission MCP Server")
logger.info("Starting Submission MCP Server")

langgraph_tool_config = LanggraphToolConfig()

mcp = FastMCP("Submission MCP Server")


@mcp.tool(name="submit")
def submit(ans: str) -> str:
    """Submit task result to benchmark

    Args:
        ans (str): task result that the agent submits

    Returns:
        str: http return code of benchmark submission server
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
        return str(response.status_code)
    except Exception as e:
        logger.error(f"[submit_mcp] HTTP submission failed: {e}")
        return "error"
