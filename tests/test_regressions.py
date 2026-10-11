"""离线回归：直接编译源码中的目标定义，隔离网络、账号和第三方库初始化。"""
import ast
import copy
import enum
import json
import random
import re
import sys
import threading
import traceback
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path
from queue import PriorityQueue
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.answer_check import check_answer, cut, normalize_completion_answer, normalize_shortanswer_answer


def load_definitions(path, names, namespace, class_name=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf8"))
    nodes = tree.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    selected = [node for node in nodes if getattr(node, "name", None) in names]
    if len(selected) != len(names):
        raise AssertionError(f"Missing definitions in {path}: {names}")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / path), "exec"), namespace)


BASE = {"Enum": enum.Enum}
load_definitions("api/base.py", {"StudyResult"}, BASE)
StudyResult = BASE["StudyResult"]
COURSE = {"title": "测试课程", "courseId": "c", "clazzId": "class", "cpi": "p"}
POINT = {"id": "1", "title": "测试章节", "has_finished": False, "jobCount": "1"}
CONFIG = {"jobs": 2, "speed": 1, "notopen_action": "retry"}


def main_namespace():
    module = types.ModuleType("offline_main")
    sys.modules[module.__name__] = module
    module.__dict__.update({
        "enum": enum, "sys": sys, "threading": threading, "time": types.SimpleNamespace(sleep=Mock()),
        "traceback": traceback, "ThreadPoolExecutor": ThreadPoolExecutor, "dataclass": dataclass,
        "field": field, "PriorityQueue": PriorityQueue, "RLock": threading.RLock, "Any": Any,
        "Chaoxing": object, "StudyResult": StudyResult, "logger": Mock(), "tqdm": Mock(),
        "Notification": Mock(), "LoginError": RuntimeError,
    })
    load_definitions("main.py", {"ChapterResult", "log_error", "ChapterTask", "JobProcessor", "str_to_bool",
                                "process_job", "process_chapter", "process_course", "format_time", "main",
                                "verify_chapter_finished", "run_sequential_chapters"}, module.__dict__)
    return module.__dict__


class AnswerNormalizationTests(unittest.TestCase):
    def test_shortanswer_keeps_paragraphs_and_punctuation(self):
        expected = "1. 第一要点：A、B。\n2. 第二要点：3.14 / New York。"
        for answer in (expected, expected.split("\n"), {"Answer": expected.split("\n")},
                       json.dumps({"Answer": expected}, ensure_ascii=False)):
            with self.subTest(answer=answer):
                self.assertEqual(normalize_shortanswer_answer(answer), expected)

    def test_empty_and_malformed_shortanswers_are_rejected(self):
        for answer in (None, " ", [], [" ", "正文"], [None], {"wrong": "正文"}, 123):
            with self.subTest(answer=answer):
                self.assertIsNone(normalize_shortanswer_answer(answer))
                self.assertFalse(check_answer(answer, "shortanswer", Mock()))

    def test_supported_formats_preserve_order(self):
        for answer in (["甲", "乙"], "甲\n乙", "甲|乙", "甲#乙", "甲；乙",
                       '{"Answer":["甲","乙"]}', '```json\n["甲","乙"]\n```'):
            with self.subTest(answer=answer):
                self.assertEqual(normalize_completion_answer(answer, 2), ["甲", "乙"])

    def test_wrong_count_and_empty_slots_are_rejected(self):
        for answer in (None, [], ["甲"], ["甲", ""], ["甲", None], "甲\n\n乙", "甲", [True, "乙"]):
            with self.subTest(answer=answer):
                self.assertIsNone(normalize_completion_answer(answer, 2))

    def test_single_blank_preserves_punctuation_and_spaces(self):
        self.assertEqual(normalize_completion_answer("3.14, New York / A-B", 1), ["3.14, New York / A-B"])

    def test_completion_type_validation_handles_none(self):
        self.assertFalse(check_answer(None, "completion", Mock()))


class WorkflowStatusTests(unittest.TestCase):
    def setUp(self):
        self.ns = main_namespace()

    def processor(self, action="retry"):
        task = self.ns["ChapterTask"](0, copy.deepcopy(POINT))
        return self.ns["JobProcessor"](Mock(), COURSE, [task], dict(CONFIG, notopen_action=action))

    def run_processor(self, processor):
        errors = []
        def run():
            try:
                processor.run()
            except BaseException as exc:
                errors.append(exc)
        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        runner.join(timeout=3)
        self.assertFalse(runner.is_alive(), "Queue hung instead of reporting the task outcome")
        return errors

    def test_successful_workers_finish(self):
        self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"].SUCCESS)
        self.assertEqual(self.run_processor(self.processor()), [])

    def test_exhausted_or_skipped_chapters_fail_run(self):
        for result, action in (("NOT_OPEN", "retry"), ("NOT_OPEN", "continue"), ("ERROR", "retry"), ("INCOMPLETE", "retry")):
            with self.subTest(result=result, action=action):
                self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"][result])
                p = self.processor(action)
                errors = self.run_processor(p)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], RuntimeError)
                self.assertEqual(len(p.failed_tasks), 1)

    def test_worker_exception_retries_without_hanging(self):
        self.ns["process_chapter"] = Mock(side_effect=ValueError("invalid response"))
        p = self.processor()
        self.assertIsInstance(self.run_processor(p)[0], RuntimeError)
        self.assertEqual(p.tasks[0].tries, 5)

    def test_transient_error_can_recover(self):
        self.ns["process_chapter"] = Mock(side_effect=[ValueError("transient"), self.ns["ChapterResult"].SUCCESS])
        self.assertEqual(self.run_processor(self.processor()), [])

    def test_stale_failure_recovers_only_after_platform_confirms_completion(self):
        for result in ("ERROR", "INCOMPLETE", "NOT_OPEN"):
            with self.subTest(result=result):
                self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"][result])
                p = self.processor()
                p.chaoxing.get_course_point.return_value = {"points": [dict(POINT, has_finished=True)]}
                self.assertEqual(self.run_processor(p), [])
                self.assertEqual(p.tasks[0].result, self.ns["ChapterResult"].SUCCESS)
                self.assertEqual(p.failed_tasks, [])

    def test_missing_or_unreadable_platform_state_does_not_clear_failure(self):
        for response in ({"points": []}, {"points": [POINT]}):
            self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"].INCOMPLETE)
            p = self.processor()
            p.chaoxing.get_course_point.return_value = response
            self.assertIsInstance(self.run_processor(p)[0], RuntimeError)
        p = self.processor()
        p.chaoxing.get_course_point.side_effect = OSError("offline")
        self.assertIsInstance(self.run_processor(p)[0], RuntimeError)

    def test_completed_chapter_skips_all_job_requests(self):
        cx = Mock()
        self.assertEqual(self.ns["process_chapter"](cx, COURSE, dict(POINT, has_finished=True), 1),
                         self.ns["ChapterResult"].SUCCESS)
        cx.get_job_list.assert_not_called()

    def test_saved_work_is_incomplete_chapter(self):
        cx = Mock()
        cx.get_job_list.return_value = ([{"type": "workid"}], {})
        self.ns["process_job"] = Mock(return_value=StudyResult.INCOMPLETE)
        self.assertEqual(self.ns["process_chapter"](cx, COURSE, POINT, 1), self.ns["ChapterResult"].INCOMPLETE)

    def test_empty_page_failure_is_chapter_error(self):
        cx = Mock()
        cx.get_job_list.return_value = ([], {"emptyPageResult": StudyResult.ERROR})
        self.assertEqual(self.ns["process_chapter"](cx, COURSE, POINT, 1), self.ns["ChapterResult"].ERROR)

    def test_course_checks_platform_state(self):
        for latest in ([POINT], [], [dict(POINT, has_finished=True)], [dict(POINT, jobCount=0)]):
            with self.subTest(latest=latest):
                cx = Mock()
                cx.get_course_point.side_effect = [{"points": [POINT]}] + [{"points": latest}] * 3
                self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"].SUCCESS)
                original = self.ns["tqdm"].format_sizeof
                if not latest or not latest[0]["has_finished"]:
                    with self.assertRaisesRegex(RuntimeError, "平台仍有未完成"):
                        self.ns["process_course"](cx, COURSE, dict(CONFIG))
                else:
                    self.ns["process_course"](cx, COURSE, dict(CONFIG))
                self.assertIs(self.ns["tqdm"].format_sizeof, original)

    def test_empty_course_does_not_pass(self):
        cx = Mock()
        cx.get_course_point.return_value = {"points": []}
        with self.assertRaisesRegex(RuntimeError, "未读取到章节"):
            self.ns["process_course"](cx, COURSE, dict(CONFIG))

    def test_main_propagates_failure_and_does_not_notify_success(self):
        self.ns.update(init_config=Mock(return_value=({}, {}, {})), configure_console_logger=Mock(),
                       init_chaoxing=Mock(), filter_courses=Mock(return_value=[COURSE]),
                       process_course=Mock(side_effect=RuntimeError("unfinished")))
        self.ns["init_chaoxing"].return_value.login.return_value = {"status": True}
        with self.assertRaisesRegex(RuntimeError, "unfinished"):
            self.ns["main"]()
        sent = str(self.ns["Notification"].mock_calls)
        self.assertNotIn("所有课程学习任务已完成", sent)

    def test_keyboard_interrupt_has_nonzero_exit(self):
        self.ns["init_config"] = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(SystemExit) as caught:
            self.ns["main"]()
        self.assertEqual(caught.exception.code, 130)

    def test_live_failure_is_not_success(self):
        self.ns.update(Live=Mock(), LiveProcessor=Mock())
        self.ns["LiveProcessor"].run_live.return_value = False
        self.assertEqual(self.ns["process_job"](Mock(), COURSE, {"type": "live", "jobid": "j"}, {}, 1), StudyResult.ERROR)


class WorkSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get.return_value = types.SimpleNamespace(status_code=200, text="question page")
        self.session.post.return_value = types.SimpleNamespace(status_code=200, json=lambda: {"status": True, "msg": "ok"})
        self.tiku = Mock(DISABLE=False, COVER_RATE=0.9, AUTO_SUBMIT=False)
        self.tiku.get_submit_params.return_value = ""
        self.cx = types.SimpleNamespace(tiku=self.tiku, kwargs={}, rollback_times=0,
                                        is_job_finished=Mock(return_value=False))
        self.ns = {"StudyResult": StudyResult, "logger": Mock(), "re": re, "random": random,
                   "time": types.SimpleNamespace(sleep=Mock()), "SequenceMatcher": SequenceMatcher,
                   "SessionManager": types.SimpleNamespace(get_session=lambda: self.session),
                   "decode_questions_info": Mock(), "normalize_completion_answer": normalize_completion_answer,
                   "normalize_shortanswer_answer": normalize_shortanswer_answer,
                   "cut": cut, "requests": types.SimpleNamespace(exceptions=types.SimpleNamespace(RequestException=OSError)),
                   "MaxRetryExceeded": RuntimeError}
        load_definitions("api/base.py", {"study_work", "get_job_list"}, self.ns, "Chaoxing")

    def run_work(self, answer, count=2, fields=None, submit="", threshold=0.9, question_type="completion", answer_field="answer123"):
        q = {"id": "123", "title": "测试填空", "options": "", "type": "completion",
             "blank_count": count, "blank_fields": fields if fields is not None else [f"answerEditor123{i+1}" for i in range(count)],
             "answerField": {"answer123": "", "answertype123": "2"}}
        if question_type == "shortanswer":
            q.update(type="shortanswer", shortanswer_field=answer_field)
            q["answerField"]["answertype123"] = "4"
        elif question_type in ("single", "multiple", "judgement"):
            q.update(type=question_type, options="A. 甲\nB. 乙\nC. 丙" if question_type != "judgement" else "")
            q["answerField"]["answertype123"] = {"single": "0", "multiple": "1", "judgement": "3"}[question_type]
        self.ns["decode_questions_info"].return_value = {"questions": [q]}
        self.tiku.query.return_value = answer
        self.tiku.get_submit_params.return_value = submit
        self.tiku.COVER_RATE = threshold
        result = self.ns["study_work"](self.cx, COURSE, {"jobid": "work-j", "enc": "e"},
                                       {"knowledgeid": "k", "ktoken": "t", "cpi": "p"})
        return result, self.session.post.call_args.kwargs["data"]

    def test_multiple_blanks_use_separate_page_fields(self):
        result, payload = self.run_work(["甲", "乙"])
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["answerEditor1231"], "甲")
        self.assertEqual(payload["answerEditor1232"], "乙")
        self.assertNotIn("answer123", payload)
        self.assertEqual(payload["pyFlag"], "")

    def test_shortanswer_submits_paragraphs_in_page_field(self):
        result, payload = self.run_work(["第一段", "第二段"], question_type="shortanswer", answer_field="answerEditor123")
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["answerEditor123"], "第一段\n第二段")
        self.assertEqual(payload["answertype123"], "4")
        self.assertNotIn("answer123", payload)

    def test_shortanswer_json_string_does_not_split_into_characters(self):
        result, payload = self.run_work('{"Answer":"完整简答正文"}', question_type="shortanswer")
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["answer123"], "完整简答正文")

    def test_missing_shortanswer_gets_nonempty_fallback(self):
        result, payload = self.run_work(" ", question_type="shortanswer", threshold=0)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["pyFlag"], "")
        self.assertTrue(payload["answer123"])

    def test_shortanswer_save_does_not_count_as_completed(self):
        result, payload = self.run_work("完整正文", question_type="shortanswer", submit="1")
        self.assertEqual(result, StudyResult.INCOMPLETE)
        self.assertEqual(payload["answer123"], "完整正文")

    def test_missing_blank_gets_fallback_for_every_slot(self):
        result, payload = self.run_work(["甲"], threshold=0)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["pyFlag"], "")
        self.assertTrue(payload["answerEditor1231"])
        self.assertTrue(payload["answerEditor1232"])

    def test_automatic_mode_submits_with_zero_ai_coverage(self):
        self.tiku.AUTO_SUBMIT = True
        for question_type in ("completion", "shortanswer"):
            with self.subTest(question_type=question_type):
                result, payload = self.run_work(None, question_type=question_type, threshold=0.5)
                self.assertEqual(result, StudyResult.SUCCESS)
                self.assertEqual(payload["pyFlag"], "")

    def test_query_exception_uses_fallback_and_submits(self):
        self.tiku.AUTO_SUBMIT = True
        self.tiku.query.side_effect = ConnectionError("offline")
        result, payload = self.run_work(None)
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertTrue(payload["answerEditor1231"])
        self.assertTrue(payload["answerEditor1232"])
        self.assertEqual(payload["pyFlag"], "")

    def test_all_objective_questions_get_fallback_and_submit(self):
        self.tiku.AUTO_SUBMIT = True
        for kind in ("single", "multiple", "judgement"):
            with self.subTest(kind=kind):
                result, payload = self.run_work(None, question_type=kind)
                self.assertEqual(result, StudyResult.SUCCESS)
                self.assertEqual(payload["pyFlag"], "")
                answer = payload["answer123"]
                if kind == "judgement":
                    self.assertIn(answer, ("true", "false"))
                else:
                    self.assertTrue(set(answer).issubset(set("ABC")))
                    self.assertIn(len(answer), (1,) if kind == "single" else (2, 3))

    def test_automatic_mode_has_priority_over_save_setting(self):
        self.tiku.AUTO_SUBMIT = True
        result, payload = self.run_work(None, submit="1")
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["pyFlag"], "")

    def test_unknown_multiblank_fields_still_do_not_fake_completion(self):
        self.tiku.AUTO_SUBMIT = True
        result, payload = self.run_work(None, fields=[])
        self.assertEqual(result, StudyResult.INCOMPLETE)
        self.assertEqual(payload["pyFlag"], "1")

    def test_explicit_save_is_incomplete(self):
        result, payload = self.run_work(["甲", "乙"], submit="1")
        self.assertEqual(result, StudyResult.INCOMPLETE)
        self.assertEqual(payload["answerEditor1231"], "甲")

    def test_single_blank_uses_existing_aggregate_field(self):
        result, payload = self.run_work("New York", count=1, fields=[])
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["answer123"], "New York")

    def test_repeated_field_names_preserve_all_answers(self):
        result, payload = self.run_work("甲\n乙", fields=["answer123", "answer123"])
        self.assertEqual(result, StudyResult.SUCCESS)
        self.assertEqual(payload["answer123"], ["甲", "乙"])

    def test_unrecognized_multi_blank_fields_do_not_submit(self):
        result, payload = self.run_work("甲\n乙", fields=[])
        self.assertEqual(result, StudyResult.INCOMPLETE)
        self.assertEqual(payload["pyFlag"], "1")

    def test_disabled_tiku_does_not_pass(self):
        self.cx.tiku = None
        self.assertEqual(self.ns["study_work"](self.cx, COURSE, {}, {}), StudyResult.INCOMPLETE)

    def test_completed_work_skips_ai_and_submission(self):
        self.cx.is_job_finished.return_value = True
        result = self.ns["study_work"](self.cx, COURSE, {"jobid": "work-j"}, {"knowledgeid": "k"})
        self.assertEqual(result, StudyResult.SUCCESS)
        self.tiku.query.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()

    def test_completed_work_is_recognized_with_tiku_disabled(self):
        self.cx.tiku = None
        self.cx.is_job_finished.return_value = True
        self.assertEqual(self.ns["study_work"](self.cx, COURSE, {"jobid": "work-j"}, {"knowledgeid": "k"}),
                         StudyResult.SUCCESS)
        self.session.post.assert_not_called()

    def test_reviewed_rejection_requires_explicit_task_completion(self):
        self.session.post.return_value = types.SimpleNamespace(
            status_code=200, json=lambda: {"status": False, "msg": "已批阅作业，不允许重复提交！"})
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                self.cx.is_job_finished.side_effect = [False, confirmed]
                result, _ = self.run_work(["甲", "乙"])
                self.assertEqual(result, StudyResult.SUCCESS if confirmed else StudyResult.ERROR)

    def test_reviewed_page_skips_question_parser_only_after_confirmation(self):
        self.session.get.return_value = types.SimpleNamespace(status_code=200, text="作业已批阅")
        self.cx.is_job_finished.side_effect = [False, True]
        result = self.ns["study_work"](self.cx, COURSE, {"jobid": "work-j", "enc": "e"},
                                       {"knowledgeid": "k", "ktoken": "t", "cpi": "p"})
        self.assertEqual(result, StudyResult.SUCCESS)
        self.ns["decode_questions_info"].assert_not_called()
        self.tiku.query.assert_not_called()
        self.session.post.assert_not_called()

    def test_unavailable_completion_check_still_saves_missing_answers(self):
        self.cx.is_job_finished.side_effect = OSError("offline")
        result, payload = self.run_work(None)
        self.assertEqual(result, StudyResult.INCOMPLETE)
        self.assertEqual(payload["pyFlag"], "1")

    def test_card_http_failure_is_not_empty_success(self):
        self.session.get.return_value = types.SimpleNamespace(status_code=403)
        self.cx.rate_limiter = Mock()
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            self.ns["get_job_list"](self.cx, COURSE, POINT)


class QuestionParsingTests(unittest.TestCase):
    def test_shortanswer_uses_editor_field_and_ignores_hidden_fields(self):
        ns = {"re": re, "Dict": Dict, "Any": Any, "logger": Mock(), "_extract_title": lambda *_: "论述要点"}
        load_definitions("api/decode.py", {"_process_question", "_get_question_type"}, ns)
        div = Mock(attrs={"data": "123"})
        div.find.side_effect = lambda tag, **kwargs: types.SimpleNamespace(attrs={"data": "4"}) if kwargs.get("class_") == "TiMu" else None
        div.find_all.return_value = [types.SimpleNamespace(get=attrs.get) for attrs in (
            {"name": "answer123", "type": "hidden"}, {"name": "answerEditor123"})]
        question = ns["_process_question"](div)
        self.assertEqual(question["type"], "shortanswer")
        self.assertEqual(question["shortanswer_field"], "answerEditor123")

    def test_blank_count_and_names_are_taken_from_page(self):
        ns = {"re": re, "Dict": Dict, "Any": Any, "logger": Mock(), "_extract_title": lambda *_: "填入两个空"}
        load_definitions("api/decode.py", {"_process_question", "_get_question_type"}, ns)
        elements = []
        for attrs in ({"name": "answer123", "type": "hidden"}, {"name": "answerEditor1231"},
                      {"name": "answerEditor1232"}, {"name": "unrelated"}):
            elements.append(types.SimpleNamespace(get=attrs.get))
        div = Mock(attrs={"data": "123"})
        div.find.side_effect = lambda tag, **kwargs: types.SimpleNamespace(attrs={"data": "2"}) if kwargs.get("class_") == "TiMu" else None
        div.find_all.return_value = elements
        question = ns["_process_question"](div)
        self.assertEqual(question["blank_count"], 2)
        self.assertEqual(question["blank_fields"], ["answerEditor1231", "answerEditor1232"])


class AnswerCacheTests(unittest.TestCase):
    def test_shortanswer_list_is_normalized_before_caching(self):
        cache = Mock()
        cache.get_cache.return_value = None
        ns = {"Optional": Optional, "sub": re.sub, "CacheDAO": lambda: cache, "logger": Mock(),
              "normalize_completion_answer": normalize_completion_answer,
              "normalize_shortanswer_answer": normalize_shortanswer_answer, "check_answer": check_answer}
        load_definitions("api/answer.py", {"query"}, ns, "Tiku")
        tiku = Mock(DISABLE=False, name="test")
        tiku._query.return_value = ["第一段", "第二段"]
        answer = ns["query"](tiku, {"type": "shortanswer", "title": "简答"})
        self.assertEqual(answer, "第一段\n第二段")
        cache.add_cache.assert_called_once_with("简答", answer)

    def test_invalid_cached_blank_count_queries_again(self):
        cache = Mock()
        cache.get_cache.return_value = "甲乙"
        ns = {"Optional": Optional, "sub": re.sub, "CacheDAO": lambda: cache, "logger": Mock(),
              "normalize_completion_answer": normalize_completion_answer, "check_answer": check_answer}
        load_definitions("api/answer.py", {"query"}, ns, "Tiku")
        tiku = Mock(DISABLE=False, name="test")
        tiku._query.return_value = ["甲", "乙"]
        answer = ns["query"](tiku, {"type": "completion", "title": "填空", "blank_count": 2})
        self.assertEqual(answer, "甲\n乙")
        tiku._query.assert_called_once()
        cache.add_cache.assert_called_once_with("填空", "甲\n乙")


class AIRetryTests(unittest.TestCase):
    def setUp(self):
        self.ns = {"wraps": wraps, "copy": copy, "logger": Mock(), "time": Mock(), "check_answer": check_answer,
                   "normalize_completion_answer": normalize_completion_answer,
                   "normalize_shortanswer_answer": normalize_shortanswer_answer}
        load_definitions("api/answer.py", {"retry_ai_answer"}, self.ns)
        self.tiku = types.SimpleNamespace(true_list=["正确"], false_list=["错误"])
        self.question = {"type": "shortanswer", "title": "测试问题"}

    def query(self, raw_query, question=None):
        return self.ns["retry_ai_answer"](raw_query)(self.tiku, question or self.question)

    def test_success_does_not_retry(self):
        raw = Mock(return_value="完整正文")
        self.assertEqual(self.query(raw), "完整正文")
        raw.assert_called_once()
        self.ns["time"].sleep.assert_not_called()

    def test_empty_response_retries_once(self):
        for empty in (None, "", " ", [], {"Answer": []}):
            with self.subTest(empty=empty):
                raw = Mock(side_effect=[empty, "恢复后的正文"])
                self.assertEqual(self.query(raw), "恢复后的正文")
                self.assertEqual(raw.call_count, 2)

    def test_network_exception_retries_once(self):
        for error in (TimeoutError("timeout"), ConnectionError("offline")):
            with self.subTest(error=error):
                raw = Mock(side_effect=[error, "恢复后的正文"])
                self.assertEqual(self.query(raw), "恢复后的正文")
                self.assertEqual(raw.call_count, 2)

    def test_three_retries_stop_after_four_requests(self):
        for failures in ([None] * 4, [TimeoutError(), ConnectionError(), TimeoutError(), None], [None, " ", [], None]):
            with self.subTest(failures=failures):
                raw = Mock(side_effect=failures)
                self.assertIsNone(self.query(raw))
                self.assertEqual(raw.call_count, 4)

    def test_third_retry_can_recover(self):
        raw = Mock(side_effect=[None, TimeoutError(), " ", "恢复后的正文"])
        self.assertEqual(self.query(raw), "恢复后的正文")
        self.assertEqual(raw.call_count, 4)

    def test_fallback_model_uses_own_retries_without_mutating_primary(self):
        self.tiku.model = "primary"
        self.tiku.fallback_model = "LongCat-2.5-Preview"
        models = []
        def raw(provider, question):
            models.append(provider.model)
            return "备用模型正文" if provider.model != "primary" else None
        self.assertEqual(self.query(raw), "备用模型正文")
        self.assertEqual(models, ["primary"] * 4 + ["LongCat-2.5-Preview"])
        self.assertEqual(self.tiku.model, "primary")

    def test_both_models_stop_after_bounded_retries(self):
        self.tiku.model_name = "primary"
        self.tiku.fallback_model = "LongCat-2.5-Preview"
        raw = Mock(return_value=None)
        self.assertIsNone(self.query(raw))
        self.assertEqual(raw.call_count, 8)

    def test_same_backup_model_is_not_requested_twice(self):
        self.tiku.model = self.tiku.fallback_model = "primary"
        raw = Mock(return_value=None)
        self.assertIsNone(self.query(raw))
        self.assertEqual(raw.call_count, 4)

    def test_incomplete_blank_answer_retries(self):
        raw = Mock(side_effect=[["甲"], ["甲", "乙"]])
        self.assertEqual(self.query(raw, {"type": "completion", "title": "两空", "blank_count": 2}), ["甲", "乙"])
        self.assertEqual(raw.call_count, 2)

    def test_invalid_judgement_retries(self):
        raw = Mock(side_effect=["不知道", "正确"])
        self.assertEqual(self.query(raw, {"type": "judgement", "title": "判断题"}), "正确")
        self.assertEqual(raw.call_count, 2)

    def test_fallback_gets_ai_retry_before_next_provider(self):
        ns = dict(self.ns, Optional=Optional)
        load_definitions("api/answer.py", {"_query"}, ns, "TikuFallback")
        first = Mock(name="ai")
        raw = Mock(side_effect=[None, "AI 恢复后正文"])
        first._query.side_effect = lambda q: self.query(raw, q)
        second = Mock(name="other")
        fallback = types.SimpleNamespace(providers=[first, second], name="回退题库")
        self.assertEqual(ns["_query"](fallback, self.question), "AI 恢复后正文")
        self.assertEqual(raw.call_count, 2)
        second._query.assert_not_called()


class AIAnswerTests(unittest.TestCase):
    def test_openai_shortanswer_string_and_array_are_parsed(self):
        client = Mock()
        ns = {"re": re, "json": json, "copy": copy, "logger": Mock(), "time": Mock(), "OpenAI": Mock(return_value=client),
              "normalize_shortanswer_answer": normalize_shortanswer_answer,
              "normalize_completion_answer": normalize_completion_answer, "check_answer": check_answer, "wraps": wraps}
        load_definitions("api/answer.py", {"retry_ai_answer"}, ns)
        load_definitions("api/answer.py", {"_query"}, ns, "AI")
        tiku = types.SimpleNamespace(http_proxy="", endpoint="test", key="test", model="test",
                                     _wait_for_interval=Mock(), _completion_kwargs=lambda **kwargs: kwargs)
        for value, expected in (("完整简答", "完整简答"), (["第一段", "第二段"], "第一段\n第二段")):
            with self.subTest(value=value):
                client.chat.completions.create.return_value = types.SimpleNamespace(choices=[
                    types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({"Answer": value})))])
                self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), expected)
                prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
                self.assertIn("简答题", prompt)
                self.assertEqual(ns["OpenAI"].call_args.kwargs["max_retries"], 0)

        client.chat.completions.create.reset_mock()
        client.chat.completions.create.side_effect = [TimeoutError(), types.SimpleNamespace(choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(content='{"Answer":"网络恢复后的正文"}'))])]
        self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), "网络恢复后的正文")
        self.assertEqual(client.chat.completions.create.call_count, 2)

        client.chat.completions.create.reset_mock()
        tiku.fallback_model = "LongCat-2.5-Preview"
        client.chat.completions.create.side_effect = [TimeoutError()] * 4 + [types.SimpleNamespace(choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(content='{"Answer":"备用模型正文"}'))])]
        self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), "备用模型正文")
        self.assertEqual([call.kwargs["model"] for call in client.chat.completions.create.call_args_list],
                         ["test"] * 4 + ["LongCat-2.5-Preview"])
        self.assertEqual(tiku.model, "test")

    def test_siliconflow_shortanswer_has_prompt_and_preserves_text(self):
        requests = Mock()
        ns = {"re": re, "json": json, "copy": copy, "logger": Mock(), "time": Mock(), "requests": requests,
              "normalize_shortanswer_answer": normalize_shortanswer_answer,
              "normalize_completion_answer": normalize_completion_answer, "check_answer": check_answer, "wraps": wraps}
        load_definitions("api/answer.py", {"retry_ai_answer"}, ns)
        load_definitions("api/answer.py", {"_query"}, ns, "SiliconFlow")
        ns["time"].time.return_value = 100
        tiku = types.SimpleNamespace(api_key="test", model_name="test", last_request_time=None, api_endpoint="test", min_interval=3)
        response = requests.post.return_value
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": '{"Answer":"完整简答"}'}}]}
        self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), "完整简答")
        prompt = requests.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("简答题", prompt)

        requests.post.reset_mock()
        failed_response = Mock(status_code=503, text="temporarily unavailable")
        requests.post.side_effect = [failed_response, response]
        self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), "完整简答")
        self.assertEqual(requests.post.call_count, 2)

        requests.post.reset_mock()
        tiku.fallback_model = "LongCat-2.5-Preview"
        requests.post.side_effect = [failed_response] * 4 + [response]
        self.assertEqual(ns["_query"](tiku, {"type": "shortanswer", "title": "简述原因", "options": ""}), "完整简答")
        self.assertEqual([call.kwargs["json"]["model"] for call in requests.post.call_args_list],
                         ["test"] * 4 + ["LongCat-2.5-Preview"])
        self.assertEqual(tiku.model_name, "test")


class UnattendedStartupTests(unittest.TestCase):
    def test_connection_failure_continues_without_input(self):
        tiku = Mock()
        tiku.get_tiku_from_config.return_value = tiku
        tiku.check_llm_connection.return_value = False
        ns = {"logger": Mock(), "Account": Mock(), "Tiku": Mock(return_value=tiku), "Chaoxing": Mock()}
        load_definitions("main.py", {"init_chaoxing"}, ns)
        with patch("builtins.input") as ask:
            result = ns["init_chaoxing"]({"username": "test-user", "password": "test-password"},
                                         {"provider": "AI", "check_llm_connection": "true"})
        ask.assert_not_called()
        self.assertIs(result, ns["Chaoxing"].return_value)
        tiku.check_llm_connection.assert_called_once()


if __name__ == "__main__":
    unittest.main()
