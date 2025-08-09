import asyncio
import atexit
import logging
import socket
import subprocess
import time

from langchain_core.messages import AIMessage, HumanMessage
from stratus_agent_demo_cfg import StratusAgentDemoCfg

from clients.configs.stratus_config import get_diagnosis_agent_cfg, get_mitigation_rollback_agent_cfg
from clients.langgraph_agent.llm_backend.init_backend import get_llm_backend_for_tools
from clients.langgraph_agent.stratus_agent.base_agent import BaseAgent
from clients.langgraph_agent.stratus_agent.rollback_agent import RollbackAgent
from clients.weak_oracles.stratus_agent_oracles import StratusAgentOracles
from srearena.conductor import Conductor
from srearena.utils.critical_section import CriticalSection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_last_n_reflections_str(reflections_list, n):

    if not reflections_list:
        return "No reflections available as this is the first attempt."

    # Get the last `n` reflections
    last_reflections = reflections_list[-n:][::-1]

    # Concatenate with appropriate words
    return f"Reflections from last {len(last_reflections)} attempts in reverse order:\n" + "\n\n".join(last_reflections)


def generate_reflection(messages, llm):
    thoughts = [
        msg.content for msg in messages if isinstance(msg, AIMessage) and msg.additional_kwargs.get("is_thought", False)
    ]

    if len(thoughts) == 0:
        return ""

    thought_str = "\n".join(thoughts)
    human_prompt = (
        f"{thought_str}\n\n"
        f"You are tasked with analyzing the content provided above. "
        f"These are the thought history of the last run of the agent. "
        f"Please extract the root cause and the mitigation plan only. "
        f"Please pay more attention to the latter part of the content "
        f"because it contains the most important information. Because "
        f"they are the most recent, the most relevant, and the most "
        f"essential. Do not summarize the content. The root cause and the "
        f"mitigation plan is the only thing you need to extract."
    )
    new_message = [HumanMessage(content=human_prompt)]

    response = llm.inference(messages=new_message)
    assert isinstance(response, AIMessage), f"Should return a single AIMessage but returned {type(response)}"

    return response.content


def wait_for_port(port, host="127.0.0.1", timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Port {port} on {host} not ready after {timeout} seconds")


def setup_port_forwarding():
    pf_jaeger = subprocess.Popen(
        ["kubectl", "port-forward", "svc/jaeger", "16686:16686", "-n", "test-hotel-reservation"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_port(16686)
        print("Port-forwarding jaeger is ready!")
    except TimeoutError as e:
        print(e)
        pf_jaeger.terminate()
        pf_jaeger.wait()
        raise

    pf_prometheus = subprocess.Popen(
        ["kubectl", "port-forward", "svc/prometheus-server", "32000:80", "-n", "observe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_port(32000)
        print("Port-forwarding prometheus is ready!")
    except TimeoutError as e:
        print(e)
        pf_prometheus.terminate()
        pf_prometheus.wait()
        raise

    return [pf_jaeger, pf_prometheus]


async def main():
    llm = get_llm_backend_for_tools()

    # NOTE: Some of the tools are stateful. If you want to start a new session,
    # you have to get a new config, which renews the stateful tools.
    diagnosis_agent_cfg = get_diagnosis_agent_cfg()
    diagnosis_agent = BaseAgent(llm, diagnosis_agent_cfg.model_copy(update={"max_round": 3}))
    diagnosis_agent.build_agent()
    diagnosis_agent.save_agent_graph_to_png()

    pid = "revoke_auth_mongodb-1"
    conductor = Conductor()
    problem = conductor.problems.get_problem_instance(pid)

    print(f"[Session Start] Problem ID: {pid}")

    print("Setting up metrics-server...")
    conductor.kubectl.exec_command(
        "kubectl apply -f "
        "https://github.com/kubernetes-sigs/metrics-server/"
        "releases/latest/download/components.yaml"
    )
    conductor.kubectl.exec_command(
        "kubectl -n kube-system patch deployment metrics-server "
        "--type=json -p='["
        '{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"},'
        '{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-preferred-address-types=InternalIP"}'
        "]'"
    )
    conductor.kubectl.wait_for_ready("kube-system")  # metrics-server is deployed in kube-system

    print("Setting up OpenEBS...")
    conductor.kubectl.exec_command("kubectl apply -f https://openebs.github.io/charts/openebs-operator.yaml")
    conductor.kubectl.exec_command(
        "kubectl patch storageclass openebs-hostpath "
        '-p \'{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}\''
    )
    conductor.kubectl.wait_for_ready("openebs")
    print("OpenEBS setup completed.")

    conductor.prometheus.deploy()

    problem.app.delete()
    problem.app.deploy()
    problem.app.start_workload()
    pf_processes = setup_port_forwarding()

    # # Phase 1: NO OP
    # final_state = diagnosis_agent.run({"app_summary": problem.app.get_app_summary()})
    # logger.info(f"Noop diagnosis final state: {final_state}")
    # if 'detection' in final_state['ans'] and isinstance(final_state['ans']['detection'], bool):
    #     print(f"NO OP Detection Result: {'✅' if not final_state['ans']['detection'] else '❌'}")
    # else:
    #     print(f"NO OP Detection Result: '❌'; Invalid answer provided by the agent!")

    # Phase 2: Inject Fault
    print("[Injecting fault now...]")
    with CriticalSection():
        problem.inject_fault()
        atexit.register(conductor.exit_cleanup_and_recover_fault)

    # Phase 3: Faulty system
    final_state = await diagnosis_agent.arun({"app_summary": problem.app.get_app_summary()})
    logger.info(f"Normal diagnosis final state: {final_state}")

    # if "detection" in final_state["ans"] and isinstance(final_state["ans"]["detection"], bool):
    #     print(f"Faulty Result: {'✅' if final_state['ans']['detection'] else '❌'}")
    # else:
    #     print(f"Faulty Result: '❌'; Invalid answer provided by the agent!")

    # mitigation
    # NOTE: The mitigation agent and rollback agent share the same session and thus their tools have consistent states.
    mitigation_agent_cfg, rollback_agent_cfg = get_mitigation_rollback_agent_cfg()
    mitigation_agent = BaseAgent(llm, mitigation_agent_cfg.model_copy(update={"max_round": 10}))
    mitigation_agent.build_agent()

    rollback_agent = RollbackAgent(llm, rollback_agent_cfg.model_copy(update={"max_round": 10}))
    rollback_agent.build_agent()

    faults_info = "The diagnosis_agent failed to give summarization."
    # if "summarization" in final_state["ans"] and isinstance(final_state["ans"]["summarization"], str):
    #     faults_info = final_state["ans"]["summarization"]

    # retry logic
    stratus_agent_oracles = StratusAgentOracles(problem.namespace)
    reflections = []
    last_result = {}
    retry_cnt = 0

    demo_cfg = StratusAgentDemoCfg()

    while True:
        final_state = await mitigation_agent.arun(
            {
                "app_summary": problem.app.get_app_summary(),
                "faults_info": faults_info,
                "reflection": get_last_n_reflections_str(reflections, demo_cfg.last_n_round_reflections),
                "last_result": str(last_result) if last_result else "No previous result",
            }
        )
        logger.info(f"Mitigation final state: {final_state}")
        # Wait before evaluation to let the system stabilize
        time.sleep(demo_cfg.time_to_wait_before_evaluation)

        result = stratus_agent_oracles.validate()
        logger.info(f"Mitigation self validation result: " f"{'✅' if result['success'] else '❌'} {result}")

        if result["success"] or not demo_cfg.is_retry_enabled or retry_cnt >= demo_cfg.max_retry_count:
            break
        logger.info("Mitigation failed, retrying...")
        retry_cnt += 1
        logger.info(f"Rollback final state: {await rollback_agent.arun({})}")
        last_result = result
        reflections.append(generate_reflection(final_state["messages"], llm))

    # Final evaluation
    result = problem.mitigation_oracle.evaluate()
    logger.info(f"Final evaluation result: {result}")

    # Final cleanup
    with CriticalSection():
        problem.recover_fault()
        atexit.unregister(conductor.exit_cleanup_and_recover_fault)

    for p in pf_processes:
        p.terminate()
        p.wait()

    problem.app.cleanup()
    conductor.prometheus.teardown()
    conductor.kubectl.exec_command("kubectl delete sc openebs-hostpath openebs-device --ignore-not-found")
    conductor.kubectl.exec_command("kubectl delete -f https://openebs.github.io/charts/openebs-operator.yaml")
    conductor.kubectl.wait_for_namespace_deletion("openebs")


if __name__ == "__main__":
    asyncio.run(main())
