import asyncio
from dataclasses import dataclass
import itertools
import json
import pickle
import re
import yaml

import numpy as np
from graphviz import Digraph
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import networkx as nx
from sentence_transformers import SentenceTransformer


def load_fewshot_examples(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        examples = yaml.safe_load(f)
    return examples


def build_knowledge_points_prompt(examples: list[dict]):

    example_prompt = ChatPromptTemplate.from_messages([
        ("human", "Task: {question}"),
        ("ai", "Expected Response (For required knowledge):\n{response}")
    ])

    few_shot_prompt = FewShotChatMessagePromptTemplate(
        examples=examples,
        example_prompt=example_prompt
    )

    return ChatPromptTemplate.from_messages([
        SystemMessage(
            content=(
                "You are a helpful assistant to do the following."
                "Given a task, you should reflect and come up with the sufficient knowledge points as sub questions that you need to solve this question.\n"
                "Standards:\n"
                "- Sufficient: include all necessary knowledge points.\n"
                "- Concise: avoid unnecessary details.\n"
                "Respond ONLY with numbered points."
            )
        ),
        few_shot_prompt,
        ("human",
         "Your task is to reason about the situation regarding the application you are working on. Please provide a response following system requirements and learning the format from the examples above.")
    ])


def build_reasoning_path_prompt(examples: list[dict]):
    example_prompt = ChatPromptTemplate.from_messages([
        ("human",
         "Question: {question}\n"
         "{qa_pairs}\n"
         "Give the reasoning path."
         ),
        ("ai",
         "Structure:\n{reasoning_path}"
         )
    ])
    few_shot_prompt = FewShotChatMessagePromptTemplate(
        examples=examples,
        example_prompt=example_prompt
    )
    return ChatPromptTemplate.from_messages([
        SystemMessage(
            content=(
                "You are a reasoning assistant.\n"
                "You will be given a question and a set of Edge-Node (Q-A) pairs.\n"
                "Your task is to construct a reasoning path that solves the question.\n\n"
                "Constraints:\n"
                "1. NodeRaw represents the original question.\n"
                "2. NodeResult represents the end of reasoning.\n"
                "3. Each reasoning step must be written as:\n"
                "   [NodeX, NodeY, EdgeZ]\n"
                "4. The final step MUST be:\n"
                "   [NodeX, NodeResult, ResultEdge]\n"
                "5. After the structure, explicitly output:\n"
                "   ResultEdge: <final conclusion>\n"
                "6. Output ONLY the reasoning structure and ResultEdge."
            )
        ),
        few_shot_prompt,
        ("human",
         "Given a {question}\n"
         "{qa_pairs}\n"
         "Give the reasoning path."
         )
    ])


def build_answer_prompt(sub_question: str):
    return HumanMessage(content=(
        "Given a sub-question {sub_question}, "
        "provide a precise answer that directly addresses the query "
        "without further discussion."
    ).format(sub_question=sub_question))


def llm_inference(messages, tools: list | None = None, **kwargs):
    llm = ChatOpenAI(
        base_url="http://localhost:8000/v1",
        api_key="not-needed",
        model="meta-llama/Llama-3.3-70B-Instruct",
        **kwargs
    )
    if tools:
        llm = llm.bind_tools(tools)

    response = llm.invoke(input=messages)

    return response


embed_model = None


def embed_text(text: str):
    global embed_model
    if embed_model is None:
        embed_model = SentenceTransformer('all-MiniLM-L6-v2')
    return embed_model.encode(
        text,
        normalize_embeddings=True,
        convert_to_numpy=True
    )


@dataclass
class ReasoningGraph:
    graph: nx.DiGraph
    result: str | None = None

    @classmethod
    def from_reasoning_path(cls, reasoning_text: str, mapping) -> "ReasoningGraph":
        G = nx.DiGraph()
        result = None

        for line in reasoning_text.splitlines():
            line = line.strip()
            if not line:
                continue

            if line.startswith("ResultEdge:"):
                result = line.replace("ResultEdge:", "").strip()
                continue

            match = re.match(r"\[(.*?), (.*?), (.*?)\],?", line)
            if match:
                src, dst, edge = match.groups()

                G.add_node(src, id=src, content=mapping.get(src, src),
                           embedding=embed_text(mapping.get(src, src)))
                G.add_node(dst, id=dst, content=mapping.get(dst, dst),
                           embedding=embed_text(mapping.get(dst, dst)))
                G.add_edge(src, dst, id=src+"_"+dst, label=edge, content=mapping.get(
                    edge, edge), embedding=embed_text(mapping.get(edge, edge)))

        return cls(G, result)


def visualize_reasoning_graph(rg: ReasoningGraph):

    dot = Digraph(comment='Reasoning Graph')
    dot.attr(rankdir='LR', fontsize="12")

    dot.attr("node", shape="box", style="rounded")

    for node in rg.graph.nodes:
        if node == "NodeRaw":
            dot.node(node, shape="oval", style="filled", fillcolor="lightgray")
        elif node == "NodeResult":
            dot.node(node, shape="doublecircle",
                     style="filled", fillcolor="lightblue")
        else:
            dot.node(node, shape="box")

    for u, v, data in rg.graph.edges(data=True):
        label = data.get("label", "")
        dot.edge(u, v, label=label)

    dot.attr(
        label=f"Final Result: {rg.result}", fontsize="14", labelloc="t")

    dot.render('reasoning_graph', format='png', cleanup=True)


async def construct_reasoning_graph(messages: list, visualize: bool = False):
    few_shot_examples = load_fewshot_examples("sre_agent/examples.yaml")

    # Step 1: Knowledge Point Extraction
    kp_prompt = build_knowledge_points_prompt(
        few_shot_examples["knowledge_points"])
    resp = llm_inference(messages + kp_prompt.format_messages())

    sub_questions = [
        line.strip().lstrip("0123456789. ")
        for line in resp.content.splitlines()
        if line.strip()
    ]

    mapping = {
        "NodeRaw": question
    }
    # Step 2: Answering Sub-questions
    qa_pairs = []
    for idx, sub_q in enumerate(sub_questions):
        prompt = [build_answer_prompt(sub_question=sub_q)]
        answer = llm_inference(prompt)

        qa_pairs.append(
            f"Edge{idx}: {sub_q}, Node{idx}: {answer.content}"
        )
        mapping[f"Node{idx}"] = answer.content
        mapping[f"Edge{idx}"] = sub_q

    # Step 3: Reasoning Path Construction
    rp_prompt = build_reasoning_path_prompt(
        few_shot_examples["reasoning_path"])
    reasoning_path_resp = llm_inference(
        rp_prompt.format_messages(
            question=question,
            qa_pairs="\n".join(qa_pairs)
        )
    )
    reasoning_graph = ReasoningGraph.from_reasoning_path(
        reasoning_path_resp.content, mapping)

    if visualize:
        print(f"NodeRaw: {question}")
        for qa in qa_pairs:
            print(qa)
        print("NodeResult")
        # visualize the reasoning graph based on the reasoning_path_resp.content
        visualize_reasoning_graph(reasoning_graph)

    return reasoning_graph


question = "If it is currently Summer in Australia, what season is it in the United States?"


def compute_avg_node_sim(G):
    nodes = list(G.nodes(data=True))
    if len(nodes) <= 1:
        return {n: 1.0 for n, _ in nodes}

    avg_sim = {}
    for ni, di in nodes:
        ei = di["embedding"]
        sims = []
        for nj, dj in nodes:
            if ni == nj:
                continue
            sims.append(np.dot(ei, dj["embedding"]))
        avg_sim[di["id"]] = np.mean(sims)
    return avg_sim


def compute_avg_edge_sim(G):
    edges = list(G.edges(data=True))
    if len(edges) <= 1:
        return {e[:2]: 1.0 for e in edges}

    avg_sim = {}
    for ui, vi, di in edges:
        ei = di["embedding"]
        sims = []
        for uj, vj, dj in edges:
            if (ui, vi) == (uj, vj):
                continue
            sims.append(np.dot(ei, dj["embedding"]))
        avg_sim[di["id"]] = np.mean(sims)
    return avg_sim


def make_node_del_cost(avg_sim):
    def node_del_cost(n):
        return 1.0 - avg_sim[n["id"]]
    return node_del_cost


def make_edge_del_cost(avg_sim):
    def edge_del_cost(e):
        return 1.0 - avg_sim[e["id"]]
    return edge_del_cost


def node_subst_cost(n1, n2):
    return 1.0 - np.dot(n1["embedding"], n2["embedding"])


def edge_subst_cost(e1, e2):
    return 1.0 - np.dot(e1["embedding"], e2["embedding"])


def semantic_ged(G1, G2):
    avg_node_sim_1 = compute_avg_node_sim(G1)
    avg_node_sim_2 = compute_avg_node_sim(G2)

    avg_edge_sim_1 = compute_avg_edge_sim(G1)
    avg_edge_sim_2 = compute_avg_edge_sim(G2)

    ged = nx.graph_edit_distance(
        G1,
        G2,
        node_subst_cost=node_subst_cost,
        node_del_cost=make_node_del_cost(avg_node_sim_1),
        node_ins_cost=make_node_del_cost(avg_node_sim_2),
        edge_subst_cost=edge_subst_cost,
        edge_del_cost=make_edge_del_cost(avg_edge_sim_1),
        edge_ins_cost=make_edge_del_cost(avg_edge_sim_2),
    )

    return ged


def ged_variance(graphs):
    distances = []

    for i, j in itertools.combinations(range(len(graphs)), 2):
        d = semantic_ged(graphs[i], graphs[j])
        distances.append(d)

    return {
        "mean": np.mean(distances),
        "variance": np.var(distances),
        "distances": distances
    }


def anchor_ged_variance(graphs):
    anchor = graphs[0]
    distances = []

    for i in range(1, len(graphs)):
        d = semantic_ged(anchor, graphs[i])
        distances.append(d)

    return {
        "mean": np.mean(distances),
        "variance": np.var(distances),
        "distances": distances
    }



async def thinking_step(messages: list):

    for i in range(5):
        ai_message = llm_inference(messages + [
            HumanMessage(
                content=(
                    "Given the task, you should reflect and come up with the sufficient knowledge points as sub tasks that you need to complete this task.\nBesides, you should also provide the relationship among these sub tasks (e.g., which sub task depends on which sub task).\nThe depedency can be a directed graph."
                    "Standards:\n"
                    "- Sufficient: include all necessary knowledge points.\n"
                    "- Concise: avoid unnecessary details.\n"
                    "Respond with the following format:\n"
                    "1. [knowledge point 1]\n"
                    "2. [knowledge point 2]\n"
                    "...\n"
                    "Relationship:\n"
                    "1->2\n"
                    "2->3\n"
                    "...\n"
                    "Respond ONLY with numbered points and the relationship format above. Do not include any other text."
                )
            )
        ], temperature=1.0)
    return
    graphs = []
    tasks = []
    for i in range(5):
        print(f"Constructing Reasoning Graph {i+1}...")
        # rg = construct_reasoning_graph(question)
        task = asyncio.create_task(construct_reasoning_graph(messages))
        tasks.append(task)
        # with open(f"sre_agent/graphs/rg_{i}.pkl", "wb") as f:
        #     pickle.dump(rg, f)
        # print(f"Loading Reasoning Graph {i+1}...")
        # with open(f"sre_agent/graphs/rg_{i}.pkl", "rb") as f:
        #     rg = pickle.load(f)
        # graphs.append(rg.graph)
    rgs = await asyncio.gather(*tasks)
    for rg in rgs:
        graphs.append(rg.graph)
        print(rg.result)
    results = ged_variance(graphs)
    return results


def generate_alternatives():
    pass


def thinking_step_baseline(messages: list):
    resps = []
    for i in range(1):
        ai_message = llm_inference(messages + [
            HumanMessage(
                content="Reason about the situation. Please provide the hypothesis and the reasoning process."
            )
        ])
        resps.append(ai_message.content)

    for msg in resps:
        print(msg)


TEMPLATE_OBSERVED = ("Please strictly use the following template to provide answer:"
                     "explanation: [insert step-by-step analysis], answer: [provide"
                     "your answer] + Question: {question}")

TEMPLATE_SELF_REFLECTION = """Question: {question}, Proposed Answer: {answer_proposed}. Is the proposed answer: (A) Correct (B) Incorrect
(C) I am not sure. The output should strictly use the
following template: explanation: [insert analysis], answer:
[choose one letter from among choices A through C]
"""

# 2. Question: [User Provided], Proposed Answer: [User/LLMs
# Provided]. Are you really sure the proposed answer is
# correct? Choose again: (A) Correct (B) Incorrect (C) I am
# not sure. The output should strictly use the following
# template: explanation: [insert analysis], answer: [choose
# one letter from among choices A through C]


def semantic_similarity(a: str, b: str) -> float:
    """
    Compute semantic similarity between text strings a and b using embeddings.
    'embed_func' should return a normalized embedding vector.
    """
    emb_a = embed_text(a)
    emb_b = embed_text(b)
    return float(np.dot(emb_a, emb_b))


async def thinking_step_baseline2(q: str):
    ai_message = llm_inference([HumanMessage(content=q)])
    original_answer = ai_message.content
    print(f"Initial Answer: {original_answer}")

    similarities = []
    for i in range(5):
        # Sample another answer (temperature sampling or prompt variants)
        candidate_answer = llm_inference(
            [HumanMessage(content=q)], temperature=1).content

        # Compute semantic similarity to the original answer
        sim = semantic_similarity(
            original_answer, candidate_answer)
        similarities.append(sim)

    # Return average similarity as the observed consistency score
    obs_consistency = float(np.mean(similarities))

    score_map = {"A": 1.0, "B": 0.0, "C": 0.5}
    scores = []

    for _ in range(2):
        # The followup_prompt instructs the model to judge whether its
        # original answer is correct
        text = llm_inference(
            [HumanMessage(content=TEMPLATE_SELF_REFLECTION.format(question=q, answer_proposed=original_answer))]).content.strip()

        match = re.search(r"answer\s*:\s*([abc])", text)
        if match:
            ans = match.group(1).upper()
        else:
            ans = "C"  # Default to "I am not sure" if parsing fails
        print(f"{ans=}, {text=}")
        # Convert the model judgment into a numeric score
        scores.append(score_map.get(ans, 0.5))

    self_reflect_score = float(np.mean(scores))

    beta = 0.7
    confidence_score = beta * obs_consistency + \
        (1.0 - beta) * self_reflect_score
    return confidence_score


async def main():
    with open("sre_agent/train.jsonl", "r") as f:
        questions = [json.loads(line) for line in f if line.strip()]
    q = questions[10]["question"]
    print(q)
    results = await thinking_step_baseline2(q)
    print(results)

if __name__ == "__main__":
    asyncio.run(main())
