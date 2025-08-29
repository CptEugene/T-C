import json

SEASON_MAP = {
    1: "중세",
    2: "근대",
    3: "해상"
}

def get_current_season():
    with open("data/game_settings.json", "r", encoding="utf-8") as f:
        settings = json.load(f)
    return SEASON_MAP.get(settings.get("season_number", 1), "중세")

CURRENT_SEASON = get_current_season()

BOT_TOKEN = "MTM5NzQzOTQ5NTY1NzM1NzQzNA.GjGZav.Yhu34zJOcvdIIX3rQUfOdFiMhWUyC_sey3JNz8"