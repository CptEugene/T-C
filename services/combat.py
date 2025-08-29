# combat.py
import json
import random
from typing import List, Dict

ADVANTAGE = {
    "전열": "기동",
    "기동": "사격",
    "사격": "돌격",
    "돌격": "전열"
}
DISADVANTAGE = {
    v: k for k, v in ADVANTAGE.items()
}

def load_battle_dialogue(season: int):
    path = f"data/battle_dialogue_season{season}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def 상성보정(att, def_):
    if ADVANTAGE.get(att) == def_:
        return 0.2
    elif DISADVANTAGE.get(att) == def_:
        return -0.2
    return 0.0

# === 추가: 페이즈 라벨 헬퍼 ===
def _phase_tag(phase: str, role: str) -> str:
    """
    phase: '기동전' | '사격전' | '돌격전' | '총공세' | '피해' | '도망' ...
    role : '공격' | '수비' | '공통'
    """
    p = (phase or "").strip()
    r = (role or "").strip()
    if r and r != "공통":
        return f"[{p}/{r}]"
    return f"[{p}]"

# === 교체: 대사출력 (라벨 자동 부착) ===
def 대사출력(dialogue_data, phase, role, player, unit, damage=None):
    pool = dialogue_data.get(phase, {}).get(role, []) or dialogue_data.get(phase, {}).get("공통", [])
    if not pool:
        return None
    템플릿 = random.choice(pool)
    라벨 = _phase_tag(phase, role)
    본문 = 템플릿.format(player=player, unit=unit, damage=damage or "")
    return f"{라벨} {본문}"

def 페이즈_전투(dialogue, phase, attacker, defender, 로그, role):
    for att in attacker:
        if att["체력"] <= 0:
            continue

        if phase == "사격전":
            targets = [d for d in defender if d["공격유형"] == "돌격" and d["체력"] > 0]
        elif phase == "기동전":
            targets = [d for d in defender if d["공격유형"] == "기동" and d["체력"] > 0]
        else:
            targets = [d for d in defender if d["체력"] > 0]

        if not targets:
            continue

        target = random.choice(targets)
        modifier = 상성보정(att["공격유형"], target["공격유형"])
        damage = int(att["공격력"] * (1 + modifier))
        target["체력"] -= damage

        line1 = 대사출력(dialogue, phase, role, att["플레이어"], att["이름"])
        if line1: 로그.append(line1)
        line2 = 대사출력(dialogue, "피해", "공통", target["플레이어"], target["이름"], damage)
        if line2: 로그.append(line2)

def 전투처리(공격측: List[Dict], 수비측: List[Dict], season: int):
    dialogue = load_battle_dialogue(season)
    로그 = []

    # ✈️ 항공전/항공공격은 시즌 3에서만 사용
    if season >= 3:
        공격_전투기 = [u for u in 공격측 if "전투기" in u["이름"] and u["체력"] > 0]
        수비_전투기 = [u for u in 수비측 if "전투기" in u["이름"] and u["체력"] > 0]

        공격제공 = sum(u["공격력"] for u in 공격_전투기)
        수비제공 = sum(u["공격력"] for u in 수비_전투기)

        로그.append("✈️ 항공 제공권 전투 시작!")
        if 공격제공 > 수비제공:
            제공권 = "공격"
            로그.append("✅ 공격측이 제공권을 확보했습니다.")
        elif 수비제공 > 공격제공:
            제공권 = "수비"
            로그.append("✅ 수비측이 제공권을 확보했습니다.")
        else:
            제공권 = None
            로그.append("⚖️ 제공권은 동등합니다.")

        # ✈️ 항공공격 - 뇌격기 출격
        if 제공권 == "공격":
            공격_뇌격기 = [u for u in 공격측 if "뇌격기" in u["이름"] and u["체력"] > 0]
            페이즈_전투(dialogue, "기동전", 공격_뇌격기, 수비측, 로그, "공격")
        elif 제공권 == "수비":
            수비_뇌격기 = [u for u in 수비측 if "뇌격기" in u["이름"] and u["체력"] > 0]
            페이즈_전투(dialogue, "기동전", 수비_뇌격기, 공격측, 로그, "수비")

    # 기존 전투 페이즈
    페이즈_전투(dialogue, "기동전", 공격측, 수비측, 로그, "공격")
    페이즈_전투(dialogue, "기동전", 수비측, 공격측, 로그, "수비")

    페이즈_전투(dialogue, "사격전", 공격측, 수비측, 로그, "공격")
    페이즈_전투(dialogue, "사격전", 수비측, 공격측, 로그, "수비")

    페이즈_전투(dialogue, "돌격전", 공격측, 수비측, 로그, "공격")
    페이즈_전투(dialogue, "돌격전", 수비측, 공격측, 로그, "수비")

    페이즈_전투(dialogue, "총공세", 공격측, 수비측, 로그, "공격")
    페이즈_전투(dialogue, "총공세", 수비측, 공격측, 로그, "수비")

    공격체력 = sum(u["체력"] for u in 공격측 if u["체력"] > 0)
    수비체력 = sum(u["체력"] for u in 수비측 if u["체력"] > 0)

    승자 = "공격측 승리" if 공격체력 > 수비체력 else "수비측 승리"
    로그.append(f"✅ 전투 종료! 결과: {승자}")

    return 로그
