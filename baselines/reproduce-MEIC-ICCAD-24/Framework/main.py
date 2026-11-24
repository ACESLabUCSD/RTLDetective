import os
import glob
import json
import subprocess
import time
from datetime import datetime

from file import *
import Debug_Files as DF
import GPTs
import Normal_GPT
from Update_csv import update_csv
import vllm_api

_PROCESS_T0 = time.time()


def Create_Work_Dir(design_path, work_path):
    if not os.path.exists(work_path):
        os.makedirs(work_path)
    else:
        shutil.rmtree(work_path)
        os.makedirs(work_path)
    os.makedirs(os.path.join(work_path, "design"))


SIM_TIMEOUT = int(os.environ.get("MEIC_SIM_TIMEOUT", 10))


def Simulate(Do_file, sim_path) -> float:
    """iverilog/vvp in place of ModelSim's wave.do (vlog ./design/*.v; vsim work.tb).

    Writes the same two reports main.py reads: design/vcompile.txt, ending in vlog's
    "Errors: N, Warnings: M" summary line, and design/vsim.txt with the testbench output
    (only when the design compiles, so a compile error goes to DebugGPT as a compile report)."""
    sim_start_time = time.time()
    design_dir = os.path.join(sim_path, "design")
    vvp = os.path.join(sim_path, "sim.vvp")
    srcs = sorted(glob.glob(os.path.join(design_dir, "*.v")))
    comp = subprocess.run(["iverilog", "-g2012", "-Wall", "-I", design_dir, "-o", vvp] + srcs,
                          capture_output=True, text=True)
    lines = [l for l in comp.stderr.splitlines() if l.strip()]
    errors = sum("error" in l.lower() for l in lines) or (1 if comp.returncode else 0)
    warnings = sum("warning" in l.lower() for l in lines)
    with open(os.path.join(design_dir, "vcompile.txt"), "w", encoding="utf-8") as f:
        f.write(comp.stderr + f"\nErrors: {errors}, Warnings: {warnings}\n")
    if comp.returncode == 0:
        try:
            sim = subprocess.run(["vvp", "-n", vvp], capture_output=True, text=True,
                                 timeout=SIM_TIMEOUT, cwd=design_dir)
            out = sim.stdout + sim.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            out += f"\nSimulation timed out after {SIM_TIMEOUT}s\n"
        with open(os.path.join(design_dir, "vsim.txt"), "w", encoding="utf-8") as f:
            f.write(out)
    if os.path.exists(vvp):
        os.remove(vvp)
    sim_end_time = time.time()
    return sim_end_time - sim_start_time


def Start_Debug(json_data):
    Debug_start_time = time.time()
    Design_name = json_data.get("Design_name")
    Origin_design_path = json_data.get("Origin_design_path")
    work_root = os.environ.get("MEIC_WORK_DIR", os.path.join(Root_path, json_data.get("Work_Dir_name")))
    work_path = os.path.join(work_root, Design_name)
    work_design_path = os.path.join(work_path, "design")

    Create_Work_Dir(Origin_design_path, work_path)
    Debug_Files = DF.DEBUG_FILES(json_data)
    Debug_Files.Create_Design(work_design_path)
    Debug_Files.Create_Testbench(work_design_path)
    Sim_time_list = []
    Debug_time_list = []

    Score_time_list = []

    One_sim_time = Simulate(Root_path, work_path)
    modelsim_done(work_path)
    Sim_time_list.append(One_sim_time)

    max_i = int(os.environ.get("MEIC_MAX_ITER", 10))
    GPT = GPTs.GPTS(
        "DebugGPT", "asst_HvM228Prmecq2ILVkS7GkLYx"
    ) 
    ScoreGPT = Normal_GPT.GPT("ScoreGPT")
    try:
        Debug_Success = False
        for i in range(max_i):
            # LLM usage and time so far, after i fix attempts: gives the cost at an iteration budget
            # of i (run_meic_testset.sh / tools/compare_cost.py read these lines).
            print("MEIC_ITER " + json.dumps(dict(iteration=i, elapsed_s=round(time.time() - _PROCESS_T0, 2),
                                                 **vllm_api.USAGE)), flush=True)
            spec = Debug_Files.Get_Spec_Content()
            design = Debug_Files.Get_Design_Content()
            tb = Debug_Files.Get_Testbench_Content()
            compile = Debug_Files.Get_Compile_Report(work_design_path)
            sim = Debug_Files.Get_Simulation_Report(work_design_path)

            # if "Errors: 0" in compile and "Your Design Passed" in sim:
            sim_passed = sim is not None and "[PASS]" in sim and "[FAIL]" not in sim
            if "Errors: 0" in compile and sim_passed:
                print("\n\n-System: RTL code fixed with " + str(i) + " iteration(s)")
                Debug_Success = True
                break

            if sim is not None:
                Question = f"The Spec(design description) is\n\n{spec}\n\nThe Design Code is\n\n{design}\n\nThe Simulation Report is\n\n{sim}\n\nPlease fix the error according to the simulation report."
            else:
                Question = f"The Spec(design description) is\n\n{spec}\n\nThe Design Code is\n\n{design}\n\nThe Compile Report is\n\n{compile}\n\nPlease fix the error according to the compile report."

            for _ in range(3):
                try:
                    FIX, One_debug_time = GPT.ASK_GPTs(
                        Question
                        + "\nOffer just corrected Verilog design code without testbench, no explanation. "
                    )
                    Debug_time_list.append(One_debug_time)
                    break
                except Exception as e:
                    print(e)
                    continue
            print("\n-GPTFIXer:\n" + FIX)

            filelineTemp = len(design.split("\n"))
            fixed_code = code_fetch(FIX)
            origin_code = design
            folder_rename(work_path, "design", f"design({str(i)})")
            os.makedirs(work_design_path)
            Debug_Files.Update_Design_Content(fixed_code)
            Debug_Files.Create_Design(work_design_path)

            if fixed_code:
                fileline = len(fixed_code.split("\n"))
                if (
                    fileline > filelineTemp * 2 / 5 + 2
                ):  # Avoid Answering Extreme Laziness
                    scoretext, One_score_time = ScoreGPT.Get_Code_Score(
                        spec, origin_code, fixed_code
                    )
                    # scoretext = '30'
                    Score_time_list.append(One_score_time)
                    print("\n-GPTJUDGE:\n" + scoretext)
                    # scoretext = ScoreGPT.ASK_GPTs(code)
                    score = score_fetch(scoretext)
                    print("\n-System: " + scoretext)

                    if int(score[1]) + 4 >= int(score[0]):
                        print("\n-System: Move on")
                        Debug_Files.Create_Testbench(work_design_path)

                        One_sim_time = Simulate(Root_path, work_path)
                        modelsim_done(work_path)
                        Sim_time_list.append(One_sim_time)
                    else:
                        print("\n-System: Please fix the code")
                        redo(work_path, i)
                        Debug_Files.Rollback_Design_Content(work_design_path)
                else:
                    print("\n-System: New code is too short")
                    redo(work_path, i)
                    i = i - 1
                    Debug_Files.Rollback_Design_Content(work_design_path)
            else:
                print("\n-System: No code generatedexit")
                redo(work_path, i)
                i = i - 1
                Debug_Files.Rollback_Design_Content(work_design_path)

        Debug_end_time = time.time()
        Total_time = Debug_end_time - Debug_start_time
        if Debug_Success:
            os.mkdir(os.path.join(work_path, "Success Output"))
            Debug_Files.Create_Design(os.path.join(work_path, "Success Output"))
            with open(
                os.path.join(work_path, "Success Output", f"{str(i)} times.txt"),
                "a",
                encoding="UTF-8",
            ) as file:
                file.write(
                    f"RTL code fixed with {Total_time} seconds and {str(i)} iteration(s)"
                )

        else:
            os.mkdir(os.path.join(work_path, "Fail Output"))
            Debug_Files.Create_Design(os.path.join(work_path, "Fail Output"))
            with open(
                os.path.join(work_path, "Fail Output", f"{str(i+1)} times.txt"),
                "a",
                encoding="UTF-8",
            ) as file:
                file.write(
                    f"RTL code failed with {Total_time} seconds and {str(i+1)} iteration(s)"
                )

        Sim_time = sum(Sim_time_list)
        Debug_time = sum(Debug_time_list)
        Score_time = sum(Score_time_list)
        return (
            Debug_Success,
            Debug_Files.Get_Design_Content(),
            i,
            Total_time,
            Sim_time,
            Debug_time,
            Score_time,
        )
    except Exception as e:
        print(e)
        return False, "", 0, 0, 0, 0, 0


def main():
    start_time = time.time()
    init_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
    Json_Start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    Config_path = os.environ.get("MEIC_CONFIG", os.path.join(Root_path, "config.json"))
    with open(Config_path, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    output_json = os.path.join("Output", f"Output_{init_time}.json")
    output_csv = os.path.join("Output", f"Stat_{init_time}.csv")
    os.makedirs("Output", exist_ok=True)
    Debug_list = []
    module_list = []
    Debug_list.append({"module_name": json_data["Design_name"], "module_list": module_list})
    output_content = {"Start time": Json_Start_time}
    (
        Debug_Success,
        design_content,
        Iterations,
        Total_time,
        Sim_time,
        Debug_time,
        Score_time,
    ) = Start_Debug(json_data)
    
    module_list.append(
        {
            "Name": json_data["Design_name"],
            "Is_Success": bool(Debug_Success),
            "Error_type": "func" if "func" in json_data["Design_File"] else "syc",
            "Iterations": Iterations,
            "Total_time": round(Total_time, 2),
            "Sim_time": round(Sim_time, 2),
            "Debug_time": round(Debug_time, 2),
            "Score_time": round(Score_time, 2),
            "Trans_time": round(Total_time - Sim_time - Debug_time - Score_time, 2),
        }
    )
    Debug_list[0]["module_list"] = module_list
    output_content["Debug_list"] = Debug_list
    with open(output_json, "w", encoding="UTF-8") as file:
        json.dump(output_content, file, indent=4)
    update_csv(output_content, output_csv)
    Json_End_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output_content["End time"] = Json_End_time
    end_time = time.time()
    output_content["Total time"] = round(end_time - start_time, 2)
    with open(output_json, "w", encoding="UTF-8") as file:
        json.dump(output_content, file, indent=4)
if __name__ == "__main__":
    try:
        Root_path = os.path.dirname(os.path.realpath(__file__))
    except:
        Root_path = os.getcwd()
    main()
