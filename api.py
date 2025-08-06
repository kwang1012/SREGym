import pyfiglet
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# Note: no direct import of Conductor here; will be provided by set_conductor
app = FastAPI()
conductor = None


def set_conductor(c):
    """Provide the Conductor instance to the API module."""
    global conductor
    conductor = c


class SubmitRequest(BaseModel):
    solution: str


@app.post("/submit")
async def submit_solution(req: SubmitRequest):
    """
    Accepts a detection solution, evaluates it, and returns the updated results.
    """
    if conductor is None or conductor.submission_stage is None:
        raise HTTPException(status_code=400, detail="No problem has been started")

    # Build and wrap the submit command in a Markdown code block
    cmd = f"submit({req.solution})"
    wrapped = f"```\n{cmd}\n```"

    # Parse the API call
    try:
        parsed = conductor.parser.parse(wrapped)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Parsing error: {e}")

    if parsed.get("api_name") != "submit":
        raise HTTPException(status_code=400, detail="Invalid submit command")

    # Execute the environment step through the conductor
    resp = await conductor.ask_env(wrapped)

    # Return the updated results after this submission
    return conductor.results


@app.get("/status")
async def get_status():
    """
    Returns the current submission stage.
    """
    if conductor is None:
        raise HTTPException(status_code=400, detail="No problem has been started")
    return {"stage": conductor.submission_stage}


def run_api(c, host: str = "0.0.0.0", port: int = 8000):
    """
    Start the API server using the provided Conductor instance.
    """
    set_conductor(c)

    console = Console()
    ascii_art = pyfiglet.figlet_format("SREArena")
    console.print(
        Panel(ascii_art, title="SREArena API Server", subtitle=f"Access at http://{host}:{port}", style="bold green")
    )
    endpoints = """
**Available Endpoints**
- **POST /submit**: Submit a detection solution (JSON body: { "solution": "<your-solution>" }) and receive evaluation results
- **GET /status**: Get the current submission stage
    """
    console.print(Markdown(endpoints))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    # standalone fallback: create a new Conductor if needed
    from srearena.conductor import Conductor

    c = Conductor()
    run_api(c)
