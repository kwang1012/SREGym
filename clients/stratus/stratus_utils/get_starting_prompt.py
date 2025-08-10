import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def get_init_prompts(self, app_summary):
    with open(self.prompts_file_path, "r") as file:
        data = yaml.safe_load(file)
        sys_prompt = data["system"].format(max_round=self.max_round)
        user_prompt = data["user"]
        prompts = []
        if sys_prompt:
            prompts.append(SystemMessage(sys_prompt))
        if user_prompt:
            prompts.append(HumanMessage(user_prompt))
        return prompts
