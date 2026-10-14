"""시간·사건·회복 정책 검증. 카메라·모델 추론 없이 관측을 재현한다."""

import unittest

from app.detection_service import DrowsinessDecisionEngine


class EventRiskTests(unittest.TestCase):
    def setUp(self):
        self.engine = DrowsinessDecisionEngine(model_threshold=0.02)
        self.engine.left_baseline, self.engine.right_baseline = 0.30, 0.28
        self.now = 10.0

    def observe(self, seconds, *, eyes="closed", score=None):
        values = {"closed": (0.15, 0.14), "open": (0.30, 0.28),
                  "partial": (0.23, 0.21), "one": (0.15, 0.28)}
        left, right = values[eyes]
        result = self.engine.update(left, right, score, self.now)
        for _ in range(round(seconds * 20)):
            self.now = round(self.now + 0.05, 5)
            result = self.engine.update(left, right, score, self.now)
        return result

    def event(self):
        result = self.observe(1.3)
        self.observe(0.6, eyes="open")
        return result

    def test_one_event_does_not_advance_to_two_at_two_seconds(self):
        result = self.observe(2.5)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["recent_event_count"], 1)

    def test_two_distinct_events_raise_two_and_third_after_warning_raises_three(self):
        self.assertEqual(self.event()["level"], 1)
        self.assertEqual(self.event()["level"], 2)
        result = self.event()
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["transition_reason"], "repeated_after_level_2")

    def test_continuous_three_second_closure_is_immediate_emergency(self):
        result = self.observe(3.0)
        self.assertEqual(result["level"], 3)
        self.assertTrue(result["emergency_condition"])
        self.assertEqual(result["recent_event_count"], 1)

    def test_model_score_magnitude_cannot_assign_severity(self):
        result = self.observe(4.0, eyes="partial", score=1.0)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["recent_event_count"], 1)
        self.assertFalse(result["emergency_condition"])

    def test_level_one_requires_ten_seconds_of_open_eyes(self):
        self.observe(1.3)
        self.assertEqual(self.observe(9.95, eyes="open")["level"], 1)
        self.assertEqual(self.observe(0.05, eyes="open")["level"], 0)

    def test_level_two_goes_directly_to_zero_ten_seconds_after_acknowledgement(self):
        self.event()
        self.observe(1.3)
        self.engine.acknowledge_alarm(self.now)
        self.assertEqual(self.observe(9.95, eyes="open")["level"], 2)
        self.assertEqual(self.observe(0.05, eyes="open")["level"], 0)
        self.assertEqual(len(self.engine.event_times), 0)
        self.assertEqual(self.event()["level"], 1)

    def test_level_three_stays_latched_even_after_open_eyes(self):
        self.observe(3.0)
        result = self.observe(60.0, eyes="open", score=1.0)
        self.assertEqual(result["level"], 3)
        self.assertTrue(result["risk_latched"])
        self.assertFalse(result["emergency_condition"])

    def test_high_closure_ratio_does_not_block_requested_ten_second_recovery(self):
        self.observe(2.8)
        self.observe(0.6, eyes="open")
        self.observe(2.8)
        self.engine.acknowledge_alarm(self.now)
        result = self.observe(10, eyes="open")
        self.assertEqual(result["level"], 0)
        self.assertGreater(result["ear_closure_ratio"], 0.10)

    def test_level_two_without_acknowledgement_cannot_recover(self):
        self.event()
        self.observe(1.3)
        result = self.observe(60, eyes="open")
        self.assertEqual(result["level"], 2)
        self.assertTrue(result["alarm_ack_required"])
        self.assertEqual(result["recovery_seconds"], 0)

    def test_normal_time_before_acknowledgement_is_not_counted(self):
        self.observe(3)
        self.observe(30, eyes="open")
        self.engine.acknowledge_alarm(self.now)
        self.assertEqual(self.observe(9.95, eyes="open")["level"], 3)
        self.assertEqual(self.observe(0.05, eyes="open")["level"], 0)

    def test_duplicate_acknowledgement_does_not_restart_timer(self):
        self.observe(3)
        self.engine.acknowledge_alarm(self.now)
        self.observe(5, eyes="open")
        self.engine.acknowledge_alarm(self.now)
        self.assertEqual(self.observe(5, eyes="open")["level"], 0)

    def test_new_event_cancels_previous_acknowledgement(self):
        self.event()
        self.observe(1.3)
        self.engine.acknowledge_alarm(self.now)
        self.observe(1, eyes="open")
        result = self.observe(1.3)
        self.assertEqual(result["level"], 3)
        self.assertTrue(result["alarm_ack_required"])
        self.assertEqual(self.observe(20, eyes="open")["level"], 3)

    def test_missing_face_restarts_acknowledged_recovery(self):
        self.observe(3)
        self.engine.acknowledge_alarm(self.now)
        self.observe(9, eyes="open")
        self.engine.interrupt_evidence()
        self.now += 20
        self.assertEqual(self.observe(1, eyes="open")["level"], 3)
        self.assertEqual(self.observe(9, eyes="open")["level"], 0)

    def test_new_emergency_requires_new_acknowledgement(self):
        self.event()
        self.observe(1.3)
        self.engine.acknowledge_alarm(self.now)
        result = self.observe(1.7)
        self.assertTrue(result["emergency_condition"])
        self.assertTrue(result["alarm_ack_required"])

    def test_new_suspicious_eye_state_interrupts_recovery(self):
        self.observe(1.3)
        self.observe(9, eyes="open")
        self.observe(0.5, eyes="partial")
        self.assertEqual(self.observe(1, eyes="open")["level"], 1)
        self.assertEqual(self.observe(9, eyes="open")["level"], 0)

    def test_repeated_short_blinks_preserve_recovery_at_all_levels(self):
        for level in (1, 2, 3):
            with self.subTest(level=level):
                self.setUp()
                if level == 1:
                    self.observe(1.3)
                elif level == 2:
                    self.event()
                    self.observe(1.3)
                else:
                    self.observe(3)
                if level > 1:
                    self.engine.acknowledge_alarm(self.now)
                for _ in range(4):
                    self.observe(2, eyes="open", score=1)
                    self.observe(0.2, eyes="closed", score=1)
                result = self.observe(1.2, eyes="open", score=1)
                self.assertEqual(result["level"], 0)

    def test_recovery_waits_for_reopening_when_blink_crosses_deadline(self):
        self.observe(1.3)
        self.observe(9.9, eyes="open")
        result = self.observe(0.2, eyes="closed")
        self.assertEqual(result["level"], 1)
        self.assertLess(result["recovery_seconds"], 10)
        self.assertEqual(self.observe(0, eyes="open")["level"], 0)

    def test_prolonged_closure_restarts_recovery_without_a_new_event(self):
        self.observe(3)
        self.engine.acknowledge_alarm(self.now)
        self.observe(9, eyes="open")
        self.observe(0.6, eyes="closed", score=1)
        self.assertEqual(self.observe(1, eyes="open")["level"], 3)
        self.assertEqual(self.observe(9, eyes="open")["level"], 0)

    def test_partial_blink_transitions_do_not_restart_recovery(self):
        self.observe(1.3)
        self.observe(9.5, eyes="open")
        self.observe(0.1, eyes="partial")
        self.observe(0.2, eyes="closed")
        self.observe(0.1, eyes="partial")
        self.assertEqual(self.observe(0.1, eyes="open")["level"], 0)

    def test_reopening_after_tolerance_restarts_recovery_at_low_fps(self):
        self.observe(1.3)
        self.observe(9.5, eyes="open")
        self.observe(0, eyes="closed")
        self.now += 0.5
        result = self.observe(0, eyes="open")
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["recovery_seconds"], 0)
        self.assertEqual(self.observe(10, eyes="open")["level"], 0)

    def test_face_loss_during_blink_discards_previous_recovery(self):
        self.observe(3)
        self.engine.acknowledge_alarm(self.now)
        self.observe(9.5, eyes="open")
        self.observe(0.1, eyes="closed")
        self.engine.interrupt_evidence()
        self.now += 0.1
        self.assertEqual(self.observe(0.5, eyes="open")["level"], 3)
        self.assertEqual(self.observe(9.5, eyes="open")["level"], 0)

    def test_missing_face_neither_clears_risk_nor_completes_recovery(self):
        self.observe(1.3)
        self.observe(9.0, eyes="open")
        self.engine.reset_transient()
        self.now += 60
        self.assertEqual(self.observe(1.0, eyes="open")["level"], 1)
        self.assertEqual(self.observe(9.0, eyes="open")["level"], 0)

    def test_missing_face_does_not_split_one_event_into_multiple_events(self):
        self.observe(1.3)
        self.engine.interrupt_evidence()
        self.now += 2.0
        result = self.observe(1.4)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["recent_event_count"], 1)

    def test_unconfirmed_reopening_cannot_create_second_event(self):
        self.observe(1.3)
        self.observe(0.3, eyes="open")
        result = self.observe(1.3)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["recent_event_count"], 1)

    def test_expired_event_does_not_count_toward_level_two(self):
        self.event()
        self.observe(31, eyes="open")
        self.assertEqual(self.event()["level"], 1)

    def test_short_blinks_and_one_eye_do_not_trigger(self):
        self.observe(0.4)
        self.observe(0.6, eyes="open")
        self.assertEqual(self.observe(4, eyes="one", score=1)["level"], 0)

    def test_large_unobserved_gap_does_not_accumulate_closed_time(self):
        self.observe(0.5)
        self.now += 10
        result = self.observe(0.5)
        self.assertEqual(result["level"], 0)
        self.assertLess(result["observed_window_seconds"], 2)


if __name__ == "__main__":
    unittest.main()
