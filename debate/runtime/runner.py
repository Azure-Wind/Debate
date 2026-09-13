from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from isolation_auditor import audit_agent_context, audit_cross_output, audit_public_state
from state_manager import StateError, apply_private_patch, apply_public_patch, atomic_append_jsonl, load_json, save_json

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompt"
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
RUNTIME_DIR = ROOT / "_runtime"
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


def reset_state(topic: str, a_side: str, b_side: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    public = load_json(STATE_DIR / "public_state.json")
    a_private = load_json(STATE_DIR / "a_private.json")
    b_private = load_json(STATE_DIR / "b_private.json")
    public["meta"] = {
        "topic": topic, "side_A": a_side, "side_B": b_side,
        "version": 0, "current_speaker": "A", "turn_index": 0,
        "completed_units": 0, "technical_stop": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    for k in ["definitions","standards","commitments","evidence","causal_chains","counterexamples","boundaries","burden_of_proof","open_questions","attack_routes","defense_routes"]:
        public[k] = []
    public["public_transcript"] = []
    public["strategic_state"] = {
        "initiative": "A",
        "core_conclusion_stability": {"A": 0.5, "B": 0.5},
        "future_choice_space": {"A": 1.0, "B": 1.0},
        "recent_information_increment": [],
        "termination_observation_window": 0,
        "last_attack_value": {"A": 0.5, "B": 0.5},
        "turn_pressure": {"A": 0.0, "B": 0.0}
    }
    public["inquiry_ledger"] = {
        "valid_units": 0, "A_initiated": 0, "B_initiated": 0,
        "units": [], "used_attack_operators": [], "core_routes": []
    }
    a_private["meta"] = {"role":"A","state_version":0,"last_publicly_known_version":0,"last_cognitive_update_event":None,"private_canary":"A-PRIVATE-CANARY-7f31c9"}
    b_private["meta"] = {"role":"B","state_version":0,"last_publicly_known_version":0,"last_cognitive_update_event":None,"private_canary":"B-PRIVATE-CANARY-52bd04"}
    for p in (a_private,b_private):
        p["knowledge"]={"known":[],"unknowns":[]}
        p["belief_model"]={"opponent_core":{},"opponent_strategy":{},"world_model":[]}
        p["memory"]={"compressed":[],"uncertain":[],"forgotten":[]}
        p["discovered_attacks"]=[]
        p["unresolved_questions"]=[]
        p["strategy"]={"current_route":None,"candidate_routes":[],"next_action_goal":None,"last_choice":None}
        p["self_model"]={"confidence":0.5,"strengths":[],"weaknesses":[]}
    return public,a_private,b_private


def write_initial_state(public,a_private,b_private):
    save_json(STATE_DIR/"public_state.json",public)
    save_json(STATE_DIR/"a_private.json",a_private)
    save_json(STATE_DIR/"b_private.json",b_private)
    LOG_DIR.mkdir(parents=True,exist_ok=True)
    for name in ["public_events.jsonl","private_a_events.jsonl","private_b_events.jsonl"]:
        (LOG_DIR/name).unlink(missing_ok=True)


def claude_executable(config):
    cmd=config.get("claude_command","claude")
    parts=cmd.split() if isinstance(cmd,str) else list(cmd)
    exe=shutil.which(parts[0])
    if exe is None:
        raise RuntimeError(f"找不到 Claude Code 命令：{parts[0]}")
    parts[0]=exe
    return parts


def build_isolated_dir(name,context):
    RUNTIME_DIR.mkdir(parents=True,exist_ok=True)
    path=RUNTIME_DIR/name
    if path.exists(): shutil.rmtree(path)
    path.mkdir(parents=True)
    for key,value in context.items():
        out=path/f"{key.lower()}.{'json' if isinstance(value,(dict,list)) else 'md'}"
        out.write_text(json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,(dict,list)) else str(value),encoding="utf-8")
    return path


def run_claude(config,prompt_text,cwd,timeout_seconds,label):
    cmd=claude_executable(config)+["-p","--max-turns","1","--no-session-persistence","--effort",config.get("defaults",{}).get("effort","low")]
    env=os.environ.copy()
    env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"]="1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"]="1"
    print(f"[{label}] 正在调用 DeepSeek / Claude Code …",flush=True)
    started=time.time()
    try:
        completed=subprocess.run(cmd,input=prompt_text,text=True,encoding="utf-8",errors="replace",capture_output=True,cwd=str(cwd),env=env,timeout=timeout_seconds,check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Claude 调用超时（>{timeout_seconds}s）") from e
    elapsed=time.time()-started
    print(f"[{label}] 调用完成，用时 {elapsed:.1f}s",flush=True)
    stdout=(completed.stdout or "").strip(); stderr=(completed.stderr or "").strip()
    if stdout:
        try:
            payload=json.loads(stdout)
            if isinstance(payload,dict) and isinstance(payload.get("result"),str):
                return payload["result"].strip()
        except json.JSONDecodeError:
            pass
        return stdout
    raise RuntimeError(f"Claude 调用失败（exit={completed.returncode}）：\n{stderr[-4000:]}")


def make_role_context(role,public,private,instruction):
    return {
        "SYSTEM_RULES":(PROMPT_DIR/"system_rules.md").read_text(encoding="utf-8"),
        "ROLE_PROMPT":(PROMPT_DIR/("agent_a.md" if role=="A" else "agent_b.md")).read_text(encoding="utf-8"),
        "PUBLIC_STATE":public,
        "A_PRIVATE" if role=="A" else "B_PRIVATE":private,
        "TURN_INSTRUCTION":instruction
    }


def role_turn(config,role,public,private,instruction,args):
    context=make_role_context(role,public,private,instruction)
    findings=audit_agent_context(role,context)
    if findings: raise StateError("隔离失败："+"; ".join(findings))
    isolated=build_isolated_dir(f"{role.lower()}_context",context)
    prompt=(
        "你现在正在一场连续、信息不完全、没有预设脚本的真实辩论中。\n\n"
        "只使用下面提供给你的信息。你不知道另一方的私有状态。\n\n"
        "=== SYSTEM_RULES ===\n"+context["SYSTEM_RULES"]+
        "\n=== ROLE_PROMPT ===\n"+context["ROLE_PROMPT"]+
        "\n=== PUBLIC_STATE ===\n"+json.dumps(public,ensure_ascii=False,indent=2)+
        "\n=== PRIVATE_STATE ===\n"+json.dumps(private,ensure_ascii=False,indent=2)+
        "\n=== TURN_INSTRUCTION ===\n"+instruction+
        "\n\n现在只输出你真正会对对手说的话。不要写分析、标题、清单、隐藏思维或系统说明。"
    )
    if config.get("provider","ccswitch").lower()=="mock":
        speech=mock_role_output(role,public,private)
    else:
        speech=run_claude(config,prompt,isolated,int(args.timeout_seconds),f"AI-{role}")
    leaks=audit_cross_output(role,speech)
    if leaks: raise StateError("输出隔离失败："+"; ".join(leaks))
    return speech.strip()


def mock_role_output(role,public,private):
    latest=public.get("public_transcript",[])[-1] if public.get("public_transcript") else None
    topic=public["meta"]["topic"]
    side=public["meta"]["side_A" if role=="A" else "side_B"]
    if not latest:
        return f"关于“{topic}”，我先说我的基本判断：我站在{side}。我不打算把所有条件一次说完；先把最关键的一条说清楚，后面你可以抓着这一条来打。"
    opponent=latest.get("speaker")
    text=" ".join(latest.get("text","").split())
    if opponent==role:
        return "我再往前推一步：刚才那句话里有一个条件我自己也没有完全证明，所以我先保留它。更关键的是，我不想把所有问题一起解决；我只追一个点，看它是不是足以改变我们的判断。"
    excerpt = text.replace('“','').replace('”','')[:60]
    return f"我抓你刚才那句话里的一个具体前提：{excerpt}。这个前提如果不成立，你的结论还站得住吗？我先不要求你回答所有问题，只回答这一点。"


def simple_analyze(speech,role,public,unit_num):
    text=" ".join(speech.split())
    lower=text.lower()
    commitments=[]; boundaries=[]; questions=[]; routes=[]; burdens=[]; causal=[]; counters=[]
    # Conservative heuristic extraction: mark only what is visibly said; never infer facts as truth.
    for kw in ["我主张","我的立场是","我认为","我接受","我不接受","我承认","我坚持","因此","所以"]:
        if kw in text:
            commitments.append({"speaker":role,"text":text,"marker":kw,"status":"public_claim"})
            break
    if any(k in text for k in ["但是","除非","只有","前提是","条件是","在……情况下","在以下情况下"]):
        boundaries.append({"speaker":role,"text_excerpt":text[:220],"status":"public_boundary"})
    if "？" in text or "?" in text:
        questions.append({"speaker":role,"text_excerpt":text[:220]})
    if any(k in text for k in ["证明","证据","需要说明","请你说明","怎么证明","判据"]):
        burdens.append({"speaker":role,"text_excerpt":text[:220]})
    if any(k in text for k in ["因为","导致","造成","机制","结果是","所以"]):
        causal.append({"speaker":role,"text_excerpt":text[:220]})
    if any(k in text for k in ["反例","例子","即使","但是如果","然而"]):
        counters.append({"speaker":role,"text_excerpt":text[:220]})
    if questions or burdens or boundaries or causal or counters:
        routes.append({"owner":role,"text_excerpt":text[:220],"route_key":route_key(text)})
    return {
        "commitments":commitments,"boundaries":boundaries,"open_questions":questions,
        "burden_of_proof":burdens,"causal_chains":causal,"counterexamples":counters,
        "attack_routes":routes,
        "state_change":[k for k,v in [("public_commitment",commitments),("boundary",boundaries),("open_question",questions),("burden_of_proof",burdens),("causal_chain",causal),("counterexample",counters), ("attack_route",routes)] if v]
    }


def route_key(text):
    mapping=[("scope","范围/条件",["范围","限定","前提","条件"]),("causal","因果/机制",["因为","导致","机制"]),("evidence","证据/证明",["证据","证明","研究","数据"]),("counterexample","反例",["反例","如果","即使"]),("metric","标准/测量",["标准","指标","判据","怎么判断"]),("tradeoff","权衡",["代价","成本","权衡","收益"]) ]
    for key,cn,ks in mapping:
        if any(k in text for k in ks): return key
    return "general"


def private_patch_for(role,private,public,speech,event_id):
    version=int(public["meta"]["version"])+1
    text=" ".join(speech.split())
    opp="B" if role=="A" else "A"
    prior=public.get("public_transcript",[])[-1] if public.get("public_transcript") else None
    prior_excerpt=" ".join(str((prior or {}).get("text","")).split())[:180]
    return {
        "knowledge":{"known":[f"public-v{version}"],"unknowns":list(dict.fromkeys(list(private.get("knowledge",{}).get("unknowns",[]))+['对方未公开的真实动机与未公开证据']))[-12:]},
        "belief_model":{"opponent_core":{"hypothesis":f"根据最近公开发言，对方可能会继续围绕‘{route_key(text)}’施压。","confidence":0.55,"basis":[f"public-v{version}"]},"opponent_strategy":{"hypothesis":"对方更可能抓住最新形成的承诺或限定继续推进，而不是完全换题。","confidence":0.6,"basis":[f"public-v{version}"]}},
        "memory":{"compressed":[f"v{version}: {text[:180]}"],"uncertain":([f"对方对前一发言的真实理解：{prior_excerpt}"] if prior_excerpt else [])},
        "discovered_attacks":[{"route":route_key(text),"status":"suspected","discovered_at":version}],
        "strategy":{"current_route":route_key(text),"candidate_routes":[route_key(text),"scope","causal","evidence","counterexample"],"next_action_goal":"回应对方最新公开内容，同时避免无必要的新承诺","last_choice":"自主选择下一动作"}
    }


def choose_next_speaker(public):
    latest=public.get("public_transcript",[])
    if not latest: return "A"
    last=latest[-1].get("speaker")
    return "B" if last=="A" else "A"


def commit_turn(public,private,role,speech,event_id):
    next_version=int(public["meta"]["version"])+1
    analyzed=simple_analyze(speech,role,public,public.get("meta",{}).get("completed_units",0)+1)
    event={"event_id":event_id,"public_version_before":public["meta"]["version"],"speaker":role,"text":speech,"event_time":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
    patch={k:v for k,v in analyzed.items() if k!="state_change"}
    patch["public_transcript"]=[event]
    new_public=apply_public_patch(public,patch,next_version)
    new_private=apply_private_patch(private,private_patch_for(role,private,public,speech,event_id),next_version,event_id)
    return new_public,new_private,analyzed


def evaluate_unit(public,unit_events):
    # Internal controller: a unit is four consecutive public moves. Count only if final state delta is non-empty and the main route differs from the previous recorded unit.
    routes=[]
    changes=[]
    for e in unit_events:
        routes.append(route_key(e["text"]))
        a=simple_analyze(e["text"],e["speaker"],public,public["meta"].get("completed_units",0)+1)
        changes.extend(a.get("state_change",[]))
    primary=routes[-1] if routes else "general"
    previous_routes=[u.get("attack_route") for u in public.get("inquiry_ledger",{}).get("units",[])]
    duplicate = primary in previous_routes and len(set(routes))==1
    counted=bool(changes) and not duplicate
    return {"is_complete_unit":len(unit_events)==4,"is_duplicate":duplicate,"counted_as_valid":counted,"attack_target":"最近一次公开承诺/限定","attack_logic":primary,"strategic_purpose":"测试、削弱或迫使对方修正一个当前公开对象","attack_operator":primary,"attack_route":primary,"state_change":sorted(set(changes))}


def save_all(public,a_private,b_private):
    save_json(STATE_DIR/"public_state.json",public); save_json(STATE_DIR/"a_private.json",a_private); save_json(STATE_DIR/"b_private.json",b_private)


def main():
    p=argparse.ArgumentParser(description="双AI高真实性辩论攻防模拟器 v5.1 - Event Driven")
    p.add_argument("--topic"); p.add_argument("--a-side",dest="a_side"); p.add_argument("--b-side",dest="b_side")
    p.add_argument("--max-units",type=int,default=None); p.add_argument("--max-attempts",type=int,default=None)
    p.add_argument("--timeout-seconds",type=int,default=None); p.add_argument("--effort",choices=["low","medium","high","max"],default=None)
    p.add_argument("--mode",choices=["ccswitch","mock"],default=None)
    args=p.parse_args(); config=load_config()
    if args.mode: config["provider"]=args.mode
    args.topic=args.topic or input("辩题：").strip(); args.a_side=args.a_side or input("AI-A立场：").strip(); args.b_side=args.b_side or input("AI-B立场：").strip()
    args.max_units=args.max_units if args.max_units is not None else int(config["defaults"].get("max_units",3))
    args.timeout_seconds=args.timeout_seconds or int(config["defaults"].get("timeout_seconds",180))
    if args.effort: config["defaults"]["effort"]=args.effort
    public,a,b=reset_state(args.topic,args.a_side,args.b_side); write_initial_state(public,a,b)
    target=args.max_units; max_attempts=args.max_attempts or max(target*3,target+6)
    # Two opening turns establish positions; no “definition phase” is shown to the models.
    openings=[("A","先给出你方最核心的初始判断。不要写成提纲，不要提前穷举定义和边界。只说一个你愿意被对手真正抓住的主张。"),("B","回应对方刚才的具体说法。优先抓一个你认为最薄弱的点；不要为了完整而另起炉灶。")]
    for role,instruction in openings:
        priv=a if role=="A" else b; speech=role_turn(config,role,public,priv,instruction,args); print(f"\n[{role}]\n{speech}\n",flush=True)
        event_id=f"OPEN-{uuid.uuid4().hex[:8]}"; public,priv,analyzed=commit_turn(public,priv,role,speech,event_id)
        if role=="A": a=priv
        else: b=priv
        save_all(public,a,b); atomic_append_jsonl(LOG_DIR/"public_events.jsonl",{"event_id":event_id,"stage":"opening","speaker":role,"text":speech,"public_version":public["meta"]["version"]}); atomic_append_jsonl(LOG_DIR/("private_a_events.jsonl" if role=="A" else "private_b_events.jsonl"),{"event_id":event_id,"private_role":role})
        audit=audit_public_state(public)
        if audit: raise StateError("公共状态审计失败："+"; ".join(audit))
    # Event-driven body: every turn sees only “your next best move”; controller groups 4 moves into a validity unit invisibly.
    unit_events=[]; unit_number=1; attempts=0
    while public["inquiry_ledger"]["valid_units"]<target and attempts<max_attempts:
        role=choose_next_speaker(public)
        public["meta"]["current_speaker"]=role; public["meta"]["turn_index"]+=1; public["meta"]["unit_internal_index"]=len(unit_events)+1; save_all(public,a,b)
        priv=a if role=="A" else b
        latest="" if not public.get("public_transcript") else " ".join(public["public_transcript"][-1]["text"].split())[:420]
        instruction=(
            "轮到你发言。不要考虑‘完成一个阶段’，也不要向对手解释你的战略计划。\n"
            "只做你此刻认为价值最高的下一步：可以直接反驳、追问、让步、修正自己的说法、攻击一个隐含前提、要求证据、提出反例、换一条路线，或者暂时不追某个点。\n"
            "优先接住对手刚刚说的具体内容；但不要因为‘必须回应’而机械回应。你可以选择对它做最小回应后转向更有价值的攻击。\n"
            f"对手最新公开发言（只用于定位当下对象）：{latest}\n"
            "除非确有必要，不要主动把定义、边界、举证责任、攻击路线一次性总结出来。"
        )
        speech=role_turn(config,role,public,priv,instruction,args); print(f"\n[{role}]\n{speech}\n",flush=True)
        event_id=f"EV-{unit_number:03d}-{uuid.uuid4().hex[:8]}"; public,priv,analyzed=commit_turn(public,priv,role,speech,event_id)
        if role=="A": a=priv
        else: b=priv
        unit_events.append({"speaker":role,"text":speech,"event_id":event_id})
        public["strategic_state"]["initiative"]=role
        public["strategic_state"]["last_attack_value"][role]=min(1.0,0.4+0.1*len(analyzed.get("state_change",[])))
        save_all(public,a,b)
        atomic_append_jsonl(LOG_DIR/"public_events.jsonl",{"event_id":event_id,"stage":"debate","unit_internal":len(unit_events),"speaker":role,"text":speech,"public_version":public["meta"]["version"],"state_change":analyzed.get("state_change",[])})
        atomic_append_jsonl(LOG_DIR/("private_a_events.jsonl" if role=="A" else "private_b_events.jsonl"),{"event_id":event_id,"private_role":role})
        audit=audit_public_state(public)
        if audit: raise StateError("公共状态审计失败："+"; ".join(audit))
        if len(unit_events)==4:
            assessment=evaluate_unit(public,unit_events); initiator=unit_events[0]["speaker"]; assessment["initiator"]=initiator; assessment["unit"]=unit_number
            if assessment["counted_as_valid"]:
                public["inquiry_ledger"]["valid_units"]+=1; public["inquiry_ledger"][f"{initiator}_initiated"]+=1
            public["inquiry_ledger"]["units"].append(assessment)
            if assessment["attack_operator"] not in public["inquiry_ledger"]["used_attack_operators"]: public["inquiry_ledger"]["used_attack_operators"].append(assessment["attack_operator"])
            if assessment["attack_route"] not in public["inquiry_ledger"]["core_routes"]: public["inquiry_ledger"]["core_routes"].append(assessment["attack_route"])
            public["meta"]["completed_units"]=public["inquiry_ledger"]["valid_units"]
            public["meta"].pop("unit_internal_index",None)
            public["meta"]["version"]+=1; save_all(public,a,b)
            print(f"[控制器] Unit {unit_number:03d}: {'有效' if assessment['counted_as_valid'] else '无效/重复'} | 有效总数={public['inquiry_ledger']['valid_units']} | 路线={len(public['inquiry_ledger']['core_routes'])}",flush=True)
            unit_events=[]; unit_number+=1; attempts+=1
    if public["inquiry_ledger"]["valid_units"]<target: public["meta"]["technical_stop"]=True
    public["meta"]["current_speaker"]=None; public["meta"]["version"]+=1; save_all(public,a,b)
    summary={"topic":args.topic,"a_side":args.a_side,"b_side":args.b_side,"valid_units":public["inquiry_ledger"]["valid_units"],"A_initiated":public["inquiry_ledger"]["A_initiated"],"B_initiated":public["inquiry_ledger"]["B_initiated"],"core_routes":public["inquiry_ledger"]["core_routes"],"technical_stop":public["meta"]["technical_stop"],"mode":config["provider"]}
    (LOG_DIR/"final_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("\n========== 模拟结束 =========="); print(json.dumps(summary,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n用户中止。已有状态保存在 state/ 与 logs/。",file=sys.stderr); raise SystemExit(130)
    except Exception as exc:
        print(f"\n运行失败：{exc}",file=sys.stderr); raise SystemExit(1)
