import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


# FIXME: name of class is misleading for now
class LanggraphToolConfig(BaseModel):
    prometheus_mcp_url: str = Field(
        description="url for prometheus mcp server", default=f"{os.environ['MCP_SERVER_URL']}/prometheus/sse"
    )
    jaeger_mcp_url: str = Field(
        description="url for jaeger mcp server", default=f"{os.environ['MCP_SERVER_URL']}/jaeger/sse"
    )
    kubectl_mcp_url: str = Field(
        description="url for kubectl mcp server", default=f"{os.environ['MCP_SERVER_URL']}/kubectl_mcp_tools/sse"
    )
    submit_mcp_url: str = Field(
        description="url for submit mcp server", default=f"{os.environ['MCP_SERVER_URL']}/submit/sse"
    )

    min_len_to_sum: int = Field(
        description="Minimum length of text that will be summarized " "first before being input to the main agent.",
        default=200,
        ge=50,
    )

    use_summaries: bool = Field(description="Whether or not using summaries for too long texts.", default=True)
