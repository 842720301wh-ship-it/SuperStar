"""顺序解锁回归：前置核验完成后才允许执行下一章。"""
import copy
import types
import unittest
from unittest.mock import Mock

from test_regressions import COURSE, POINT, CONFIG, main_namespace


class SequentialUnlockTests(unittest.TestCase):
    def setUp(self):
        self.ns = main_namespace()
        self.cx = Mock()
        self.first = dict(POINT, title="1.2 前置章节")
        self.second = dict(POINT, id="2", title="1.3 后续章节", need_unlock=True)
        self.tasks = [self.ns["ChapterTask"](index=i, point=copy.deepcopy(p))
                      for i, p in enumerate((self.first, self.second))]
        self.success = self.ns["ChapterResult"].SUCCESS

    def run_chapters(self):
        self.ns["run_sequential_chapters"](self.cx, COURSE, self.tasks, 1)

    def test_next_chapter_waits_for_predecessor_verification(self):
        finished = set()
        events = []
        def chapter(cx, course, point, speed):
            events.append("process-" + point["id"])
            self.assertTrue(point["verify_finished"])
            if point["id"] == "2":
                self.assertIn("1", finished)
                self.assertFalse(point["need_unlock"])
            return self.success
        def verify(cx, course, point):
            events.append("verify-" + point["id"])
            finished.add(point["id"])
            return True
        def points(*args):
            return {"points": [dict(self.first, has_finished="1" in finished),
                               dict(self.second, need_unlock="1" not in finished)]}
        self.cx.get_course_point.side_effect = points
        self.ns["process_chapter"] = chapter
        self.ns["verify_chapter_finished"] = verify
        self.run_chapters()
        self.assertEqual(events, ["process-1", "verify-1", "process-2", "verify-2"])

    def test_failed_predecessor_does_not_consume_successor_retries(self):
        for result in ("INCOMPLETE", "NOT_OPEN", "ERROR"):
            with self.subTest(result=result):
                self.cx.get_course_point.return_value = {"points": [self.first, self.second]}
                self.ns["process_chapter"] = Mock(return_value=self.ns["ChapterResult"][result])
                self.ns["verify_chapter_finished"] = Mock()
                self.tasks[1].tries = 0
                with self.assertRaisesRegex(RuntimeError, "阻塞于前置章节"):
                    self.run_chapters()
                self.assertEqual(self.ns["process_chapter"].call_count, 5)
                self.assertTrue(all(call.args[2]["id"] == "1" for call in self.ns["process_chapter"].call_args_list))
                self.assertEqual(self.tasks[1].tries, 0)
                self.ns["verify_chapter_finished"].assert_not_called()

    def test_unconfirmed_success_cannot_advance(self):
        self.cx.get_course_point.return_value = {"points": [self.first, self.second]}
        self.ns["process_chapter"] = Mock(return_value=self.success)
        self.ns["verify_chapter_finished"] = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "INCOMPLETE"):
            self.run_chapters()
        self.assertEqual(self.tasks[1].tries, 0)

    def test_initial_finished_snapshot_does_not_skip_pending_cards(self):
        point = dict(self.first, has_finished=True, verify_finished=True)
        self.cx.get_job_list.return_value = ([{"type": "workid"}], {})
        self.ns["process_job"] = Mock(return_value=self.ns["StudyResult"].SUCCESS)
        self.assertEqual(self.ns["process_chapter"](self.cx, COURSE, point, 1), self.success)
        self.ns["process_job"].assert_called_once()

    def test_verification_requires_chapter_and_task_completion(self):
        for chapter_done, jobs, info, expected in (
            (True, [], {"completed_job_ids": ["j"]}, True),
            (False, [], {"completed_job_ids": ["j"]}, False),
            (True, [{"type": "workid"}], {"card_parsed": True}, False),
            (True, [], {"notOpen": True}, False),
            (True, [], {}, False),
        ):
            with self.subTest(chapter_done=chapter_done, jobs=jobs, info=info):
                self.cx.get_course_point.return_value = {"points": [dict(self.first, has_finished=chapter_done)]}
                self.cx.get_job_list.return_value = (jobs, info)
                self.assertEqual(self.ns["verify_chapter_finished"](self.cx, COURSE, dict(self.first)), expected)
                self.assertFalse(self.cx.get_job_list.call_args.kwargs["mark_empty"])

    def test_courses_with_locks_automatically_use_sequential_processing(self):
        for locked, configured in ((True, False), (False, True)):
            with self.subTest(locked=locked, configured=configured):
                self.cx.get_course_point.side_effect = [
                    {"hasLocked": locked, "points": [self.first]},
                    {"points": [dict(self.first, has_finished=True)]}]
                self.ns["run_sequential_chapters"] = Mock()
                self.ns["JobProcessor"] = Mock()
                self.ns["process_course"](self.cx, COURSE, dict(CONFIG, sequential=configured))
                self.ns["run_sequential_chapters"].assert_called_once()
                self.ns["JobProcessor"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
