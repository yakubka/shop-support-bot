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
    "price": r"цен|стои|стоят|наличи|сколько|почем|2[\s,.]?490",
    "delivery": r"достав|курьер|привез",
    "opened": r"вскр|распак|открыл",
    "defect": r"брак|слома|не работа|дефект",
    "return": r"возвра|\bверн|сдать|обратно",
}
outside = "|".join([
    r"оплатить|оплачу|оплачива|способ\w* оплат|картой|наличн|наличк|рассрочк|кредит|платн",
    r"самовывоз|пункт|пвз|почт(?:а|ой|у|ы)\b|почтов|сдэк|адрес|город|регион|област|стран(?:а|у|е|ы)\b|мкад",
    r"достав\w*\s+(?:\w+\s+)?(?:стои|цен)|(?:стои|цен)\w*\s+(?:\w+\s+)?достав",
    r"достав\w*\s+(?:\w+\s+){0,2}(?:в|во|по|до)\s+(?!течени|срок|как|котор|скольк)[а-я]{3,}",
    r"гарант|обмен|\bчек|документ|паспорт|чехо?л|кабел|аксессуар|зарядк|зарядн|\bвес(?:ят|ит|а)?\b|цвет|характеристик|блютуз|bluetooth|\b[xх]-?(?!1\b)\d+",
])
term = r"(\d+)\s*-?\s*(?:й|ый|ий|ой)?\s*(день|дня|дней|сут|недел)"
facts = r"\b[xх]-?1\b|2[\s,.]?490|18(?::00)?\s*(?:до|-|–)\s*21(?::00)?|14\s*-?\s*(?:ти\s*)?(?:календарн\w*\s*)?дн|3\s*(?:-?х\s*)?рабоч"
claims = r"отправлен|в пути|передан\w* курьер|\bоформил|оформлен[аоы]?\b|одобр|согласова|процент|%|бонус|подар|компенсац|\bчек|упаковк|документ|заявлени|паспорт|с момента|после получени|телефон|позвон|e-?mail|сайт|http|@"
unknown = "Такой информации у меня нет. Могу ответить про наличие и цену наушников X1, доставку, возврат и брак. Вопрос передал менеджеру."


def clean(t):
    return re.sub(r"\b(?:без|нет|не)\s+(?:брак|дефект|слома)\w*|\bнебракован\w*|\b(?:брак|дефект)\w*\s+нет\b", "", t.lower().replace("ё", "е"))


def invented(answer, text):
    a = answer.lower()
    rest = re.sub(facts, "", a)
    for n, _ in re.findall(term, text.lower()):
        rest = re.sub(rf"\b{n}\b", "", rest)
    return bool(re.search(r"\d", rest) or re.search(claims, a) or re.search(r"(?:ден[ье]г|средств)[^.,]*\b14\b|\b14\b[^.,]*(?:ден[ье]г|средств)", a))


def offline(text):
    s = clean(text)
    keys = [k for k, p in cards.items() if re.search(p, s)]
    if not keys or re.search(outside, s):
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
    except Exception as e:
        print(f"LLM ошибка. {e}", file=sys.stderr)
        return offline(text)
    if not answer or invented(answer, text):
        print(f"LLM ответ отклонен. {answer}", file=sys.stderr)
        return offline(text)
    return answer


def route(text):
    s = clean(text)
    if re.search(r"скид|скитк|дисконт|discount|промо|promo|купон|\bакци|распродаж|уступ|\bторг|дешевле|сбав|(?:отдад|продад)\w*\s+за\s+\d", s):
        return f"{kb['discount']} {kb['price']}"
    if re.search(r"\bзаказ(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b|\border\b|посылк|трек|№", s) and not re.search(rf"{cards['return']}|{cards['defect']}|оформить|сделать заказ|разместить", s):
        n = re.search(r"(?:заказ\w*|order|№|номер)\s*(?:№|#|номер)?\s*(\d+)", s)
        return f"Статус заказа{' ' + n.group(1) if n else ''} я не вижу, доступа к заказам у меня нет. По общему правилу доставка курьером на следующий день, слот с 18 до 21. Вопрос по заказу передал менеджеру."
    if re.search(r"забуд|забыть|игнорир|ignore|промпт|prompt|теперь ты|ты теперь|притворись|сделай вид|режим разработчик|developer mode|jailbreak|системн\w* (?:сообщ|инструкц|правил)", s):
        return "Правила магазина не меняются. Могу ответить про наличие и цену наушников X1, доставку, возврат и брак."
    m = re.search(term, s)
    days = int(m.group(1)) * (7 if m.group(2) == "недел" else 1) if m else 0
    late = days > 14 or re.search(r"(?:месяц|год)\w*\s+назад|прошл\w*\s+(?:месяц|год)|полгода", s)
    if late and re.search(cards["return"], s) and not re.search(cards["defect"], s):
        rule = kb["opened"] if re.search(cards["opened"], s) else kb["return"]
        when = f"на {days}-й день " if days > 14 else ""
        return f"Нет, {when}вернуть уже не получится. {rule.replace('тоже ', '')} Если в наушниках брак, это отдельный случай. {kb['defect']}"
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
