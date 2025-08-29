# services/move.py (지역명 기반 이동 체크)

import json, os

REGION_FILE = "data/region_data.json"
GRAPH_FILE = "data/region_graph.json"

# 유효한 지역인지 확인
def is_valid_region(name: str) -> bool:
    region = load_region()
    return name in region

# 연결되어 있는지 확인 (이동 가능 여부)
def is_connected(region_from: str, region_to: str) -> bool:
    if region_from == region_to:
        return False  # 자기자신으로 이동 불가
    graph = load_graph()
    return region_to in graph.get(region_from, [])

# 지역 불러오기
def load_region():
    if not os.path.exists(REGION_FILE):
        return {}
    with open(REGION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# 그래프 불러오기
def load_graph():
    if not os.path.exists(GRAPH_FILE):
        return {}
    with open(GRAPH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# 지역이 이동 가능 상태인지 확인
def passable(region_name: str) -> bool:
    region = load_region().get(region_name)
    if not region:
        return False
    if region.get("owner") == "X":
        return False  # 봉쇄된 지역 예시
    return True
