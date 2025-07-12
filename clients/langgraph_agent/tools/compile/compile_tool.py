import logging
import os.path
from pathlib import Path
from typing import Annotated, Optional, Union

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langchain_core.messages import ToolMessage
from pathlib import Path

import subprocess

from clients.langgraph_agent.state import State
from clients.langgraph_agent.tools.text_editing.flake8_utils import flake8, format_flake8_output  # type: ignore
from clients.langgraph_agent.tools.text_editing.windowed_file import (  # type: ignore
    FileNotOpened,
    TextNotFound,
    WindowedFile,
)

@tool("compile_postgresql_server", description= "Compile PostgreSQL server code")

def compile_postgresql_server(
tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict, InjectedState] = None,
) -> str: 
    """Compile PostgreSQL server code."""
    logger = logging.getLogger(__name__)
    logger.info("Compiling PostgreSQL server code...")
    
    workdir = Path(state.get("work_dir", "")).resolve()

    if not workdir.exists():
        return f"Work directory {workdir} does not exist. Please set the workdir in the state."

    env = os.environ.copy()
    env["PATH"] = str(Path.home() / "pgsql/bin") + ":" + env["PATH"]
    homedir = str(Path.home())

    if not workdir.exists():
        return f"Work directory {workdir} does not exist. Please set the workdir in the state."

    cmds = [
        f"./configure --prefix={homedir}/pgsql",
        "make",
        "make install",
        f"{homedir}/pgsql/bin/initdb -D {homedir}/pgsql/data",
        f"{homedir}/pgsql/bin/pg_ctl -D {homedir}/pgsql/data -l logfile start",
        f"{homedir}/pgsql/bin/createdb test",
        f"{homedir}/pgsql/bin/psql test"
    ]

    
    output = ""
    for cmd in cmds:
        process = subprocess.run(cmd, cwd= workdir, shell=True, capture_output=True, text=True, env=env)
        output += f"$ {cmd}\n{process.stdout}\n{process.stderr}\n"
    return ToolMessage(
        tool_call_id=tool_call_id,
        content=output
    )