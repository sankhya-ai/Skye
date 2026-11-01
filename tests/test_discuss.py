"""The team line: posting, cursors, acks, and cross-process ordering."""

import threading

from skye import discuss


def test_post_and_read_with_cursor(tmp_path):
    first = discuss.post(tmp_path, "m1", author="backend-dev", kind="broadcast",
                         body="api v2 decided")
    assert first["seq"] == 1
    second = discuss.post(tmp_path, "m1", author="frontend-dev", kind="question",
                          body="does v2 keep /users?")
    page = discuss.read(tmp_path, "m1", since=first["seq"])
    assert [e["id"] for e in page["entries"]] == [second["id"]]
    assert page["cursor"] == second["seq"]
    assert discuss.read(tmp_path, "m1", since=second["seq"])["entries"] == []


def test_contract_changes_track_acks(tmp_path):
    change = discuss.post(tmp_path, "m1", author="backend-dev", kind="contract-change",
                          body="POST /cart/items now takes {sku, qty}",
                          requires_ack=["frontend-dev", "tester"])
    waiting = discuss.unacked(tmp_path, "m1", ["frontend-dev", "tester"])
    assert [c["id"] for c in waiting] == [change["id"]]
    discuss.ack(tmp_path, "m1", change["id"], author="frontend-dev", note="adapting the cart calls")
    waiting = discuss.unacked(tmp_path, "m1", ["frontend-dev", "tester"])
    assert waiting[0]["waiting_on"] == ["tester"]
    discuss.ack(tmp_path, "m1", change["id"], author="tester")
    assert discuss.unacked(tmp_path, "m1", ["frontend-dev", "tester"]) == []


def test_validation(tmp_path):
    for bad_call in [
        lambda: discuss.post(tmp_path, "m1", author="NOPE", kind="note", body="x"),
        lambda: discuss.post(tmp_path, "m1", author="pm", kind="weird", body="x"),
        lambda: discuss.post(tmp_path, "m1", author="pm", kind="note", body="  "),
        lambda: discuss.post(tmp_path, "m1", author="pm", kind="contract-change", body="x"),
        lambda: discuss.ack(tmp_path, "m1", "msg-ghost", author="pm"),
    ]:
        try:
            bad_call()
            raise AssertionError("accepted a bad post")
        except discuss.DiscussionError:
            pass


def test_concurrent_posts_keep_monotonic_seq(tmp_path):
    def worker(role):
        for i in range(10):
            discuss.post(tmp_path, "m1", author=role, kind="note", body=f"{role} {i}")
    threads = [threading.Thread(target=worker, args=(r,))
               for r in ("backend-dev", "frontend-dev", "tester")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seqs = [e["seq"] for e in discuss.read(tmp_path, "m1", limit=100)["entries"]]
    assert seqs == list(range(1, 31))


def test_tail_text_is_bounded_and_attributed(tmp_path):
    for i in range(20):
        discuss.post(tmp_path, "m1", author="pm", kind="note", body=f"note {i} " + "x" * 400)
    tail = discuss.tail_text(tmp_path, "m1", limit=8, chars=2000)
    assert len(tail) <= 2000
    assert "pm (note)" in tail and "note 19" in tail and "note 0" not in tail
