import json
import re


def normalize_shortanswer_answer(answer):
    """将文本或分段答案整理为简答正文，保留标点和段落。"""
    if isinstance(answer, str):
        text = answer.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        if text.startswith(("[", "{")):
            try:
                answer = json.loads(text)
            except ValueError:
                answer = text
        else:
            answer = text
    if isinstance(answer, dict):
        answer = next((answer[key] for key in ("Answer", "answers", "answer") if key in answer), None)
        return normalize_shortanswer_answer(answer)
    if isinstance(answer, (list, tuple)):
        if not answer or any(not isinstance(part, str) or not part.strip() for part in answer):
            return None
        answer = "\n".join(part.strip() for part in answer)
    if isinstance(answer, str):
        return answer.strip() or None
    return None


def normalize_completion_answer(answer, blank_count=None):
    """保留填空顺序，校验每个空；不按逗号、空格或小数点切分答案。"""
    if isinstance(answer, str):
        text = answer.strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        if text.startswith(("[", "{")):
            try:
                answer = json.loads(text)
            except ValueError:
                answer = text
        else:
            answer = text
    if isinstance(answer, dict):
        answer = next((answer[key] for key in ("Answer", "answers", "answer", "blanks") if key in answer), None)
        return normalize_completion_answer(answer, blank_count)
    if isinstance(answer, str):
        if blank_count == 1:
            parts = [answer]
        else:
            parts = re.split(r"\r?\n|\||#", answer)
            if len(parts) == 1 and blank_count and blank_count > 1:
                parts = re.split(r"[;；]", answer)
    elif isinstance(answer, (list, tuple)):
        parts = list(answer)
    else:
        return None
    if not parts or any(not isinstance(part, (str, int, float)) or isinstance(part, bool) for part in parts):
        return None
    parts = [str(part).strip() for part in parts]
    if any(not part for part in parts) or (blank_count is not None and len(parts) != blank_count):
        return None
    return parts


def check_single(answer):
    if answer is None:
        return False

    text = str(answer).strip()
    if not text:
        return False

    # 单选答案文本中常见逗号（中英文）是句内标点，不应据此判定为多选。
    # 仅在出现明显“多段答案”分隔符时，才判定为非单选。
    strong_delimiters = ["\n", "|", "#", "\t", "\r", "、"]
    for sep in strong_delimiters:
        parts = [p.strip() for p in text.split(sep) if p.strip()]
        if len(parts) > 1:
            return False

    return True


def check_multiple(answer):
    _t = cut(answer)
    if _t is not None and len(_t) > 0:
        return True
    return False


def check_judgement(answer, true_list, false_list):
    if answer in true_list:
        return 1
    elif answer in false_list:
        return 0
    else:
        return -1


def check_completion(answer):
    return normalize_completion_answer(answer) is not None


def check_answer(answer, type, tiku):  # 只会写小杯代码，这里用个tiku感觉怪怪的，但先这么写着
    if type == 'single':
        if check_single(answer) and check_judgement(answer, tiku.true_list, tiku.false_list) == -1:
            return True
    elif type == 'multiple':
        if check_multiple(answer) and check_judgement(answer, tiku.true_list, tiku.false_list) == -1:
            return True
    elif type == 'completion':
        if check_completion(answer):
            return True
    elif type == 'shortanswer':
        return normalize_shortanswer_answer(answer) is not None
    elif type == 'judgement':
        if check_judgement(answer, tiku.true_list, tiku.false_list) != -1:
            return True
    else:  # 未知类型不匹配
        return True
    return False


def cut(answer):
    cut_char = [
        "\n",
        ",",
        "，",
        "|",
        "\r",
        "\t",
        "#",
        "*",
        "-",
        "_",
        "+",
        "@",
        "~",
        "/",
        "\\",
        ".",
        "&",
        " ",
        "、",
    ]
    if answer is None:
        return None

    answer = str(answer)
    for char in cut_char:
        if char not in answer:
            continue
        res = [opt.strip() for opt in answer.split(char) if opt.strip()]
        if res:
            return res
    stripped = answer.strip()
    return [stripped] if stripped else None
