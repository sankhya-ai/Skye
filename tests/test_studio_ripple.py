"""Tests for the insert-with-ripple planner."""
import unittest

from skye.studio.ripple import plan_insert


def film():
    return {"tracks": [
        {"clips": [
            {"id": "a", "src": "tura.mp4", "at": 0, "time": [0, 12]},
            {"id": "b", "src": "login.webm", "at": 12, "time": [0, 5]},
        ]},
        {"clips": [
            {"id": "m", "src": "music.mp3", "at": 0, "time": [0, 30]},
        ]},
    ]}


class TestPlanInsert(unittest.TestCase):
    def test_moves_clips_at_or_after_the_point_on_every_track(self):
        ops = plan_insert(film(), 0, 12.0, {"src": "card.png", "len": 3}, 3)
        moves = [o for o in ops if o["op"] == "move"]
        self.assertIn({"op": "move", "clip": "b", "track": 0, "at": 15}, moves)
        self.assertIn({"op": "move", "clip": "m-rest", "track": 1, "at": 15}, moves)

    def test_splits_a_spanning_clip_and_moves_its_rest(self):
        ops = plan_insert(film(), 0, 6.0, {"src": "card.png", "len": 3}, 3)
        split = next(o for o in ops if o["op"] == "split")
        self.assertEqual(split["clip"], "a")
        self.assertEqual(split["at"], 6.0)
        self.assertEqual(split["time"], [0, 6])
        self.assertEqual(split["restTime"], [6, 12])
        self.assertEqual(split["restId"], "a-rest")
        self.assertIn({"op": "move", "clip": "a-rest", "track": 0, "at": 9}, ops)

    def test_stills_without_a_time_window_still_split(self):
        f = {"tracks": [{"clips": [{"id": "p", "src": "page.html", "at": 0, "len": 10}]}]}
        ops = plan_insert(f, 0, 4.0, {"src": "x.mp4", "time": [0, 3]}, 3)
        split = next(o for o in ops if o["op"] == "split")
        self.assertEqual(split["time"], [0, 4])
        self.assertEqual(split["restTime"], [4, 10])

    def test_inserts_last_with_the_at_baked_in(self):
        ops = plan_insert(film(), 0, 12.0, {"src": "card.png", "len": 3}, 3)
        self.assertEqual(ops[-1]["op"], "insert")
        self.assertEqual(ops[-1]["track"], 0)
        self.assertEqual(ops[-1]["at"], 12.0)
        self.assertEqual(ops[-1]["clip"]["src"], "card.png")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            plan_insert(film(), 9, 1.0, {"src": "x.png"}, 3)
        with self.assertRaises(ValueError):
            plan_insert(film(), 0, -1.0, {"src": "x.png"}, 3)
        with self.assertRaises(ValueError):
            plan_insert(film(), 0, 1.0, {"src": "x.png"}, 0)
        with self.assertRaises(ValueError):
            plan_insert(film(), 0, 1.0, {"len": 3}, 3)

    def test_requires_a_length(self):
        with self.assertRaises(ValueError):
            plan_insert({"tracks": [{"clips": [{"id": "z", "src": "z.mp4"}]}]}, 0, 0, {"src": "x.png"}, 3)


if __name__ == "__main__":
    unittest.main()
