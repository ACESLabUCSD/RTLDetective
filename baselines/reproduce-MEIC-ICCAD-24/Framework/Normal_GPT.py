# 初始化配置
import vllm_api
import os
import json
import time
from datetime import datetime
import copy

class GPT:
    try:
        current_dir = os.path.dirname(os.path.realpath(__file__))
    except:
        current_dir = os.getcwd()
    message = []
    user_msg = {"role": "user", "content": ""}
    gpt_msg = {"role": "assistant", "content": ""}

    def __init__(self, log_name) -> None:
        self.init_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.json_name = f"{log_name}_{self.init_time}.json"

        log_dir = os.environ.get("MEIC_LOG_DIR", os.path.join(self.current_dir, "json"))
        self.json_dir = os.path.join(log_dir, self.json_name)
        self.json_content = {"time": self.init_time}

        os.makedirs(log_dir, exist_ok=True)
        with open(self.json_dir, "w", encoding="UTF-8") as file:
            json.dump(self.json_content, file, indent=4)
        print(f"Save to json: '{self.json_name}'")

    def save_json(self):
        with open(self.json_dir, "w", encoding="UTF-8") as file:
            json.dump(self.json_content, file, indent=4, ensure_ascii=False)
        return

    def ASK_GPT(self, data):
        self.user_msg["content"] = data
        self.message.append(copy.deepcopy(self.user_msg))
        ask_time = datetime.now().strftime("%H:%M:%S")
        gpt_res = vllm_api.chat(self.message, vllm_api.temperature_for("score"))
        self.gpt_msg["role"] = "assistant"
        self.gpt_msg["content"] = gpt_res
        self.message.append(copy.deepcopy(self.gpt_msg))

        response_time = datetime.now().strftime("%H:%M:%S")

        self.json_content[ask_time] = [{"User": data}]
        self.json_content[response_time] = [{"GPT": gpt_res}]
        self.save_json()
        return self.gpt_msg["content"]

    def ASK_GPT_Single(
        self,
        temperture=0.7,
        message=[
            {
                "role": "system",
                "content": "system message",
            },  #
            {
                "role": "user",
                "content": "message",
            },  #
            {"role": "user", "content": "message"},
        ],
    ):
        ask_time = datetime.now().strftime("%H:%M:%S")
        gpt_res = vllm_api.chat(message, vllm_api.temperature_for("score"))
        response_time = datetime.now().strftime("%H:%M:%S")

        self.json_content[ask_time] = message
        self.json_content[response_time] = [{"GPT": gpt_res}]
        self.save_json()
        return gpt_res

    def Get_Code_Score(self, spec, code0, code1, temperture=0.7):
        score_start_time = time.time()
        scoretext = self.ASK_GPT_Single(
            message=[
                {
                    "role": "system",
                    "content": """You are an expert in HDL design, especially skilled in verilog code writing, correction and explanation.
                    The user will provide you with a designed Spec (design description), as well as an original code and a modified code.
                    Please rate these two codes according to syntax and functionality, ranging from 0 to 100.
                    Only the total score of the two is given, no introduction is needed
                    Answer in the following format:
original code: (total score)
modified code: (total score)""",
                },  #
                {
                    "role": "user",
                    "content": f"The Spec(design description) is\n\n{spec}\n\nThe original code is\n\n{code0}\n\nThe modified code is\n\n{code1}",
                },  #
            ],
        )
        score_end_time = time.time()
        return scoretext, score_end_time - score_start_time

    def History(self, num=-1):
        last_key = list(self.json_content)[num]
        last_value = self.json_content[last_key]
        Response = last_value[0]
        key = list(Response)[-1]
        return Response[key]

    def ReGenerate_Response(self, num=1):
        for i in range(2 * num - 1):
            last_key = list(self.json_content)[-1]
            del self.json_content[last_key]
        Q = self.History()
        last_key = list(self.json_content)[-1]
        del self.json_content[last_key]
        return self.ASK_GPT(Q)


if __name__ == "__main__":
    GPT = GPT("test")
    try:
        while True:
            user_input = input("\n-User: ").strip()
            if user_input == "exit":
                break
            gpt_res = GPT.ASK_GPT(user_input)
            print("\n-GPT: " + gpt_res)
    except Exception as e:
        print(e)

