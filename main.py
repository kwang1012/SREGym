import asyncio
import csv
import sys
import threading

from rich.console import Console
from rich.prompt import Prompt

from srearena.conductor.conductor import Conductor
from srearena.conductor.conductor_api import run_api


def driver_loop(conductor: Conductor):
    """
    Deploy each problem and wait for HTTP grading via POST /submit.
    Returns a list of flattened dicts with results per problem.
    """

    async def driver():
        console = Console()
        # give the API  a moment to bind
        await asyncio.sleep(1)

        all_results = []
        for pid in conductor.problems.get_problem_ids():
            console.log(f"\n🔍 Starting problem: {pid}")
            conductor.problem_id = pid

            await conductor.start_problem()

            # Poll until grading completes
            while conductor.submission_stage != "done":
                await asyncio.sleep(1)

            console.log(f"✅ Completed {pid}: results={conductor.results}")

            snapshot = {"problem_id": pid}
            for stage, outcome in conductor.results.items():
                if isinstance(outcome, dict):
                    for k, v in outcome.items():
                        snapshot[f"{stage}.{k}"] = v
                else:
                    snapshot[stage] = outcome
            all_results.append(snapshot)

        return all_results

    # run the async driver and return its results
    return asyncio.run(driver())


def main():
    agent_name = Prompt.ask("[bold cyan]What would you like to call your agent?[/]", default="arena")
    conductor = Conductor()
    conductor.register_agent(agent_name)

    # -- kick off the driver in a background thread --
    #    it will deploy each problem and then wait for your HTTP POSTs to /submit
    driver_thread = threading.Thread(target=lambda: setattr(main, "results", driver_loop(conductor)), daemon=True)
    driver_thread.start()

    # -- start the API server in the MAIN thread --
    run_api(conductor)

    # once run_api returns (i.e. server shuts down), we know driver is done
    # fetch the results we stored on the `main` function object
    results = getattr(main, "results", [])

    if results:
        fieldnames = sorted({key for row in results for key in row.keys()})
        csv_path = f"{agent_name}_results.csv"
        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f"✅ Benchmark complete! Results written to {csv_path}")
    else:
        print("⚠️ No results to write.")

    sys.exit(0)


if __name__ == "__main__":
    main()
