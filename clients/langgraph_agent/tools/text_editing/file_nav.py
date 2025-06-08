from clients.langgraph_agent.k8s_agent import State


def open_file(state: State, open_file_path: str):
    return State(
        messages=state["messages"],
        curr_file=open_file_path,
        curr_line=state["curr_line"],
    )


def goto_line(state: State, goto_line_num: int):
    return State(
        messages=state["messages"],
        curr_file=state["curr_file"],
        curr_line=goto_line_num,
    )
