import os
import json
import time
from datetime import datetime

import vllm_api

# The original DebugGPT is an OpenAI Assistant (ID hard-coded in main.py) whose instructions are not in
# this repo. Lines 1-5 below are the debug agent's runtime system prompts, verbatim from the MEIC paper
# (arXiv 2405.06840v1, Figure 7). 
DEBUG_SYSTEM = "\n".join([
    "You are an expert in the field of IC design, proficient in Verilog. I will provide you with the design "
    "Spec., design code, as well as the compilation logs and simulation logs. Please modify the design code "
    "based on this information. Do not drastically change the structure of code.",
    "If there are comments in the code, modify the code with reference to the comments and retain these "
    "comments after modification.",
    "If the Compile fails, I will provide you with the compilation logs, please modify the corresponding "
    "lines of design code based on the information in the logs.",
    "If the Compile passes, I will only provide you with the simulation logs. The simulation logs contain "
    "error information during simulation. Based on the information, please modify the design code to make "
    "it functionally right.",
    "Offer corrected Verilog design code omitting testbench. Please fix the error(s) according to the design "
    "specification and logs.",
    "Give the complete corrected design in a single ```verilog code block.",   # not in the paper
])


class GPTS:
    try:
        current_dir = os.path.dirname(os.path.realpath(__file__))
    except:
        current_dir = os.getcwd()
    message = []
    user_msg = {"role": "user", "content": ""}
    gpt_msg = {"role": "assistant", "content": ""}
    thread_id = ""
    ASSISTANT_ID = ""

    def __init__(self, log_name, ASSISTANT_ID="asst_cJrrHWwpW3r6BOCK3AVH7vSS") -> None:
        # vLLM: the Assistants thread becomes a message list kept for the whole design.
        self.thread = [{"role": "system", "content": DEBUG_SYSTEM}]
        self.ASSISTANT_ID = ASSISTANT_ID

        self.init_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.json_name = f"{log_name}_{self.init_time}.json"

        log_dir = os.environ.get("MEIC_LOG_DIR", os.path.join(self.current_dir, "json"))
        self.json_dir = os.path.join(log_dir, self.json_name)
        self.json_content = {"time": self.init_time}

        os.makedirs(log_dir, exist_ok=True)
        with open(self.json_dir, "w", encoding="UTF-8") as file:
            json.dump(self.json_content, file, indent=4)
        print(f"Save to json: '{self.json_name}'")

    def save_json(self) -> None:
        with open(self.json_dir, "w", encoding="UTF-8") as file:
            json.dump(self.json_content, file, indent=4, ensure_ascii=False)
        return

    def ASK_GPTs(self, data, stream=True) -> str:
        ask_start_time = time.time()
        self.user_msg["content"] = data
        self.message.append(self.user_msg)
        ask_time = datetime.now().strftime("%H:%M:%S")
        self.thread.append({"role": "user", "content": data})
        gpt_res = vllm_api.chat_thread(self.thread, vllm_api.temperature_for("debug"))
        self.thread.append({"role": "assistant", "content": gpt_res})
        print("\nGPTS > " + gpt_res, flush=True)
        self.gpt_msg["content"] = gpt_res
        self.message.append(self.gpt_msg)

        response_time = datetime.now().strftime("%H:%M:%S")

        self.json_content[ask_time] = [{"User": data}]
        self.json_content[response_time] = [{"GPTS": gpt_res}]
        self.save_json()
        ask_end_time = time.time()
        return self.gpt_msg["content"], ask_end_time - ask_start_time

    def History(self, num=-1) -> str:
        last_key = list(self.json_content)[num]
        last_value = self.json_content[last_key]
        Response = last_value[0]
        key = list(Response)[-1]
        return Response[key]


if __name__ == "__main__":
    # message = []
    # gpt_msg = []
    GPTS = GPTS("gpts")
    try:
        while True:
            user_input = input("\n-User: ").strip()
            if user_input == "exit":
                break
            gpt_res = GPTS.ASK_GPTs(user_input, stream=True)
            print("\n-GPT: " + gpt_res)
    except Exception as e:
        print(e)

