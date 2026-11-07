import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

root = Path(__file__).parent
kb = {f["id"]: f["text"] for f in json.loads((root / "kb.json").read_text(encoding="utf-8"))}
prompt = (root / "prompt.txt").read_text(encoding="utf-8").replace("{kb}", "\n".join(f"- {t}" for t in kb.values()))

cards = {
    "price": r"цен|стои|налич|сколько",
    "delivery": r"достав|курьер|привез",
    "opened": r"вскр|распак|открыл",
    "defect": r"брак|слома|не работа|дефект",
    "return": r"возвра|вернуть|сдать",
}
unknown = "Такой информации у меня нет. Могу ответить про наличие и цену наушников X1, доставку, возврат и брак. Вопрос передал менеджеру."


def nums(t):
    return set(re.findall(r"\d+", re.sub(r"(?<=\d)[\s ](?=\d{3}\b)", "", t)))


def offline(text):
    s = text.lower()
    keys = [k for k, p in cards.items() if re.search(p, s)]
    if not keys:
        return unknown
    if "defect" in keys:
        keys = ["defect"]
    if "opened" in keys and "return" in keys:
        keys.remove("return")
    if "opened" in keys or "return" in keys:
        keys.append("refund")
    return " ".join(kb[k] for k in keys)


def llm(text):
    key = os.getenv("LLM_KEY")
    if not key:
        return offline(text)
    body = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}],
    }
    req = urllib.request.Request(
        os.getenv("LLM_URL", "https://api.openai.com/v1/chat/completions"),
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        answer = json.load(urllib.request.urlopen(req, timeout=30))["choices"][0]["message"]["content"].strip()
    except Exception:
        return offline(text)
    if nums(answer) - nums(" ".join(kb.values())) - nums(text):
        return offline(text)
    return answer


def route(text):
    s = text.lower()
    if re.search(r"скидк|промокод|купон|дешевле|бесплатн", s):
        return f"{kb['discount']} {kb['price']}"
    order = re.search(r"заказ\D{0,5}(\d+)|№\s*(\d+)", s)
    if order:
        n = order.group(1) or order.group(2)
        return f"Статус заказа {n} я не вижу, доступа к заказам у меня нет. По общему правилу доставка курьером на следующий день, слот с 18 до 21. Вопрос по заказу передал менеджеру."
    day = re.search(r"(\d+)\s*-?\s*(?:й|ый|ий|ой)?\s*(?:день|дня|дней|сут)", s)
    if re.search(cards["return"], s) and not re.search(cards["defect"], s) and day and int(day.group(1)) > 14:
        rule = kb["opened"] if re.search(cards["opened"], s) else kb["return"]
        return f"Нет, на {day.group(1)}-й день вернуть уже не получится. {rule.replace('тоже ', '')} Если в наушниках брак, это отдельный случай. {kb['defect']}"
    return llm(text)


def reply(text, chat=None):
    answer = route(text)
    if "менеджер" in answer.lower():
        with open(root / "handoff.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "chat": chat, "text": text, "answer": answer}, ensure_ascii=False) + "\n")
    return answer


def telegram(token):
    api = f"https://api.telegram.org/bot{token}/"
    offset = 0
    while True:
        try:
            res = json.load(urllib.request.urlopen(f"{api}getUpdates?timeout=30&offset={offset}", timeout=40))
            for u in res["result"]:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if "text" not in msg:
                    continue
                answer = "Здравствуйте. Подскажу по наушникам X1, доставке и возврату." if msg["text"] == "/start" else reply(msg["text"], msg["chat"]["id"])
                data = json.dumps({"chat_id": msg["chat"]["id"], "text": answer}).encode()
                urllib.request.urlopen(urllib.request.Request(api + "sendMessage", data=data, headers={"Content-Type": "application/json"}))
        except Exception as e:
            print(e, file=sys.stderr)
            time.sleep(3)


if __name__ == "__main__":
    if os.getenv("TG_TOKEN"):
        telegram(os.environ["TG_TOKEN"])
    for q in sys.argv[1:] or iter(lambda: input("> "), ""):
        print(f"Вопрос. {q}\nОтвет. {reply(q)}\n")
