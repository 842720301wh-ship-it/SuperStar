"""离线验证平台完成标记、跨卡片状态与重跑核验。"""
import json
import re
import types
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock

from test_regressions import COURSE, POINT, load_definitions


def decode_namespace():
    ns = {"json": json, "re": re, "Any": Any, "Dict": Dict, "List": List,
          "Tuple": Tuple, "Optional": Optional, "logger": Mock()}
    load_definitions("api/decode.py", {"is_passed", "decode_course_card", "_extract_job_info",
                                      "_process_attachment_cards", "_process_work_task",
                                      "_process_read_task", "_extract_points_from_chapter"}, ns)
    return ns


def card_page(cards):
    return "<script>mArg = " + json.dumps({"defaults": {"knowledgeid": "k"},
                                           "attachments": cards}) + ";</script>"


def work_card(jobid="work-j", passed=False):
    return {"type": "workid", "job": True, "jobid": jobid, "isPassed": passed,
            "property": {"name": "a name with spaces"}}


class CompletionParsingTests(unittest.TestCase):
    def setUp(self):
        self.ns = decode_namespace()

    def test_only_explicit_passed_values_are_skipped(self):
        for flag in (True, 1, "true", " TRUE ", "1", False, 0, "false", "0", "", None, "unknown"):
            with self.subTest(flag=flag):
                passed = flag in (True, 1, "true", " TRUE ", "1")
                jobs, info = self.ns["decode_course_card"](card_page([work_card(passed=flag)]))
                self.assertEqual(len(jobs), 0 if passed else 1)
                self.assertEqual(info["completed_job_ids"], ["work-j"] if passed else [])

    def test_mixed_cards_keep_pending_jobs_and_preserve_spaces(self):
        cards = [work_card("work-done", True), work_card("work-pending", "false")]
        jobs, info = self.ns["decode_course_card"](card_page(cards))
        self.assertEqual([j["jobid"] for j in jobs], ["work-pending"])
        self.assertEqual(info["completed_job_ids"], ["work-done"])
        parser = Mock(return_value=[])
        self.ns["_process_attachment_cards"] = parser
        self.ns["decode_course_card"](card_page(cards))
        self.assertEqual(parser.call_args.args[0][0]["property"]["name"], "a name with spaces")

    def test_invalid_or_locked_pages_do_not_confirm_completion(self):
        for page in ("登录页面", "章节未开放", "mArg = {};"):
            jobs, info = self.ns["decode_course_card"](page)
            self.assertEqual(jobs, [])
            self.assertFalse(info.get("completed_job_ids"))

    def test_platform_empty_string_card_is_valid_without_tasks(self):
        for value in ('""', "''"):
            jobs, info = self.ns["decode_course_card"]('<script>var mArg = ' + value + ';</script>暂无内容')
            self.assertEqual(jobs, [])
            self.assertTrue(info["card_parsed"])
            self.assertTrue(info["empty_card"])
            self.assertEqual(info["unprocessed_count"], 0)

    def test_platform_removes_job_flag_from_finished_work(self):
        # 实际只读核对：已完成作业没有 isPassed，平台移除了 job 标记。
        card = work_card(passed=None)
        card.pop("job")
        jobs, info = self.ns["decode_course_card"](card_page([card]))
        self.assertEqual(jobs, [])
        self.assertEqual(info["completed_job_ids"], ["work-j"])
        self.assertEqual(info["unprocessed_count"], 0)

    def test_unknown_active_task_cannot_be_treated_as_finished(self):
        card = work_card()
        card["type"] = "unknown"
        jobs, info = self.ns["decode_course_card"](card_page([card]))
        self.assertEqual(jobs, [])
        self.assertEqual(info["unprocessed_count"], 1)

    def test_chapter_completion_checks_all_tips_and_unlock_state(self):
        for tips, count, finished in ((["剩余任务", "已完成"], "2", True),
                                      (["未完成"], "2", False),
                                      ([], "0", True), (["需要解锁"], "0", False)):
            with self.subTest(tips=tips, count=count):
                point = Mock(attrs={"id": "cur123"})
                point.get.side_effect = point.attrs.get
                point.select_one.side_effect = lambda selector: (
                    types.SimpleNamespace(text="测试章节") if selector == "a.clicktitle"
                    else types.SimpleNamespace(attrs={"value": count}))
                point.select.return_value = [types.SimpleNamespace(get_text=lambda strip=True, t=t: t) for t in tips]
                chapter = Mock()
                chapter.find_all.return_value = [types.SimpleNamespace(div=point)]
                parsed = self.ns["_extract_points_from_chapter"](chapter)[0]
                self.assertEqual(parsed["has_finished"], finished)
                self.assertEqual(parsed["need_unlock"], any("解锁" in t for t in tips))

    def test_missing_count_on_completed_heading_does_not_invent_pending_task(self):
        # 真实 5.1 教师：已完成标记存在，knowledgeJobCount 字段不存在。
        for tips, expected_count, finished in ((["已完成"], 0, True),
                                              (["未完成"], 1, False),
                                              ([], 1, False),
                                              (["已完成", "需要解锁"], 1, True)):
            with self.subTest(tips=tips):
                point = Mock(attrs={"id": "cur1230998390"})
                point.get.side_effect = point.attrs.get
                point.select_one.side_effect = lambda selector: (
                    types.SimpleNamespace(text="5.1 教师") if selector == "a.clicktitle" else None)
                point.select.return_value = [types.SimpleNamespace(get_text=lambda strip=True, t=t: t) for t in tips]
                chapter = Mock()
                chapter.find_all.return_value = [types.SimpleNamespace(div=point)]
                parsed = self.ns["_extract_points_from_chapter"](chapter)[0]
                self.assertEqual(parsed["jobCount"], expected_count)
                self.assertEqual(parsed["has_finished"], finished)


class CompletionRequestTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.ns = {"logger": Mock(), "SessionManager": types.SimpleNamespace(get_session=lambda: self.session),
                   "decode_course_card": decode_namespace()["decode_course_card"]}
        load_definitions("api/base.py", {"get_job_list", "is_job_finished"}, self.ns, "Chaoxing")
        self.cx = types.SimpleNamespace(rate_limiter=Mock(), study_emptypage=Mock())

    def response(self, text):
        return types.SimpleNamespace(status_code=200, text=text, raise_for_status=Mock())

    def test_finished_ids_survive_later_empty_cards_without_empty_page_write(self):
        self.session.get.side_effect = [self.response(card_page([work_card(passed=True)]))] + [
            self.response("空卡片")] * 6
        jobs, info = self.ns["get_job_list"](self.cx, COURSE, POINT)
        self.assertEqual(jobs, [])
        self.assertEqual(info["completed_job_ids"], ["work-j"])
        self.cx.study_emptypage.assert_not_called()

    def test_pending_job_records_card_number_for_completion_refresh(self):
        self.session.get.side_effect = [self.response("空卡片"), self.response(card_page([work_card()]))] + [
            self.response("空卡片")] * 5
        jobs, _ = self.ns["get_job_list"](self.cx, COURSE, POINT)
        self.assertEqual(jobs[0]["card_num"], "1")

    def test_completion_refresh_matches_exact_job_in_its_card(self):
        for cards, expected in (([work_card(passed=True)], True),
                                ([work_card(passed="false")], False),
                                ([work_card("work-other", True)], False), ([], False)):
            with self.subTest(cards=cards):
                self.session.get.reset_mock()
                self.session.get.return_value = self.response(card_page(cards))
                result = self.ns["is_job_finished"](self.cx, COURSE,
                    {"jobid": "work-j", "card_num": "2"}, {"knowledgeid": "k"})
                self.assertEqual(result, expected)
                self.session.get.assert_called_once()
                self.assertEqual(self.session.get.call_args.kwargs["params"]["num"], "2")

    def test_unknown_card_number_checks_all_cards(self):
        self.session.get.side_effect = [self.response("空卡片")] * 6 + [
            self.response(card_page([work_card(passed=True)]))]
        self.assertTrue(self.ns["is_job_finished"](self.cx, COURSE, {"jobid": "work-j"}, {"knowledgeid": "k"}))
        self.assertEqual(self.session.get.call_count, 7)

    def test_http_failure_cannot_confirm_completion(self):
        response = self.response("登录页")
        response.raise_for_status.side_effect = OSError("403")
        self.session.get.return_value = response
        with self.assertRaises(OSError):
            self.ns["is_job_finished"](self.cx, COURSE, {"jobid": "work-j", "card_num": "0"}, {"knowledgeid": "k"})

    def test_unreadable_task_cards_cannot_mark_empty_page_completed(self):
        self.session.get.return_value = self.response("登录页或异常页面")
        with self.assertRaisesRegex(RuntimeError, "未读取到有效任务卡片"):
            self.ns["get_job_list"](self.cx, COURSE, POINT)
        self.cx.study_emptypage.assert_not_called()

    def test_readonly_inspection_never_marks_empty_page_completed(self):
        self.session.get.return_value = self.response(card_page([]))
        self.ns["get_job_list"](self.cx, COURSE, POINT, mark_empty=False)
        self.cx.study_emptypage.assert_not_called()

    def test_completed_heading_without_task_cards_never_writes_completion(self):
        self.session.get.return_value = self.response('mArg = "";')
        jobs, info = self.ns["get_job_list"](self.cx, COURSE,
            dict(POINT, has_finished=True, jobCount=0))
        self.assertEqual(jobs, [])
        self.assertEqual(info["unprocessed_count"], 0)
        self.cx.study_emptypage.assert_not_called()

    def test_login_page_cannot_confirm_even_zero_count_completed_heading(self):
        self.session.get.return_value = self.response('登录页面')
        with self.assertRaisesRegex(RuntimeError, "未读取到有效任务卡片"):
            self.ns["get_job_list"](self.cx, COURSE, dict(POINT, has_finished=True, jobCount=0))
        self.cx.study_emptypage.assert_not_called()

    def test_pending_card_in_zero_count_heading_still_executes(self):
        self.session.get.side_effect = [self.response(card_page([work_card()]))] + [self.response('mArg = "";')] * 6
        jobs, _ = self.ns["get_job_list"](self.cx, COURSE,
            dict(POINT, has_finished=True, jobCount=0))
        self.assertEqual([job["jobid"] for job in jobs], ["work-j"])
        self.cx.study_emptypage.assert_not_called()

    def test_finished_work_without_ispassed_can_be_confirmed(self):
        card = work_card(passed=None)
        card.pop("job")
        self.session.get.return_value = self.response(card_page([card]))
        self.assertTrue(self.ns["is_job_finished"](self.cx, COURSE,
            {"jobid": "work-j", "card_num": "1"}, {"knowledgeid": "k"}))

    def test_video_log_does_not_treat_string_false_as_passed(self):
        ns = {"logger": Mock(), "re": re, "Optional": Optional,
              "requests": types.SimpleNamespace(Session=object),
              "is_passed": decode_namespace()["is_passed"], "get_timestamp": lambda: "0"}
        load_definitions("api/base.py", {"video_progress_log"}, ns, "Chaoxing")
        cx = types.SimpleNamespace(video_log_limiter=Mock(), get_enc=Mock(return_value="e"),
                                   get_uid=Mock(return_value="u"))
        job = {"jobid": "j", "objectid": "o", "otherinfo": "nodeId_1-", "rt": "1",
               "videoFaceCaptureEnc": "", "attDuration": "", "attDurationEnc": ""}
        for rt in ("1", ""):
            job["rt"] = rt
            for flag, expected in (("false", False), ("0", False), (False, False), ("true", True), (True, True)):
                with self.subTest(rt=rt, flag=flag):
                    session = Mock()
                    session.get.return_value = types.SimpleNamespace(status_code=200, text="{}", json=lambda: {"isPassed": flag})
                    passed, state = ns["video_progress_log"](cx, session, COURSE, job, {}, "token", 100, 100, headers={"test": "1"})
                    self.assertIs(passed, expected)
                    self.assertEqual(state, 200)


if __name__ == "__main__":
    unittest.main()
