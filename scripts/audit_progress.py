"""只读核对课程章节和任务卡片，不学习、不提交、不标记空页面完成。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup
from api.base import SessionManager
from api.decode import decode_course_card, decode_course_point, is_passed
from api.logger import configure_console_logger
from main import init_chaoxing, load_config_from_file


def inspect_course(chaoxing, course, prefixes):
    session = SessionManager.get_session()
    response = session.get(
        "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse",
        params={"courseid": course["courseId"], "clazzid": course["clazzId"], "cpi": course["cpi"], "ut": "s"},
    )
    response.raise_for_status()
    points = decode_course_point(response.text)["points"]
    if not points:
        raise RuntimeError("未读取到章节，无法核对")
    soup = BeautifulSoup(response.text, "lxml")
    print("AUDIT_COURSE " + json.dumps({"title": course["title"], "id": course["courseId"], "chapters": len(points)}, ensure_ascii=False), flush=True)
    for point in points:
        node = soup.find(id="cur" + point["id"])
        tips = [{"text": tag.get_text(" ", strip=True), "class": tag.get("class", []),
                 "style": tag.get("style", ""), "parent_class": tag.parent.get("class", [])}
                for tag in node.select("span.bntHoverTips")] if node else []
        print("AUDIT_CHAPTER " + json.dumps(dict(point, tips=tips), ensure_ascii=False), flush=True)
        if not point["title"].startswith(tuple(prefixes)):
            continue
        for number in "0123456":
            chaoxing.rate_limiter.limit_rate()
            card_response = session.get("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards", params={
                "clazzid": course["clazzId"], "courseid": course["courseId"], "knowledgeid": point["id"],
                "cpi": course["cpi"], "ut": "s", "mooc2": 1, "num": number, "v": "2025-0424-1038-3"})
            card_response.raise_for_status()
            from re import search, DOTALL
            matched = search(r"\bmArg\s*=\s*(\{.*?\})\s*;", card_response.text, DOTALL)
            cards = json.loads(matched[1]).get("attachments", []) if matched else []
            status = {"chapter": point["title"], "card": number,
                      "not_open": "章节未开放" in card_response.text, "parsed": bool(matched),
                      "tasks": [{"id": card.get("jobid"), "type": card.get("type"),
                                 "job": card.get("job"), "isPassed": card.get("isPassed"),
                                 "property_keys": sorted(card.get("property", {})),
                                 "passed": is_passed(card.get("isPassed")),
                                 "playTime": card.get("playTime"),
                                 "name": card.get("property", {}).get("name", card.get("property", {}).get("title", ""))}
                                for card in cards]}
            print("AUDIT_CARD " + json.dumps(status, ensure_ascii=False), flush=True)
            jobs, info = decode_course_card(card_response.text)
            print("AUDIT_DECODE " + json.dumps({"chapter": point["title"], "card": number,
                "pending_jobs": [{"id": job.get("jobid"), "type": job.get("type")} for job in jobs],
                "completed_job_ids": info.get("completed_job_ids", []),
                "unprocessed_count": info.get("unprocessed_count", 0)}, ensure_ascii=False), flush=True)
            if status["not_open"]:
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.ini")
    parser.add_argument("--chapter-prefix", nargs="+", default=["1.1 ", "1.2 ", "4.1 ", "4.2 ", "4.3 ", "4.4 "])
    args = parser.parse_args()
    common, tiku, _ = load_config_from_file(args.config)
    tiku["check_llm_connection"] = "false"
    configure_console_logger("INFO")
    chaoxing = init_chaoxing(common, tiku)
    if not chaoxing.login(login_with_cookies=False).get("status"):
        raise RuntimeError("只读核对登录失败")
    selected = set(common["course_list"])
    courses = [course for course in chaoxing.get_course_list() if str(course["courseId"]) in selected]
    if not courses:
        raise RuntimeError("未找到指定课程")
    for course in courses:
        inspect_course(chaoxing, course, args.chapter_prefix)


if __name__ == "__main__":
    main()
