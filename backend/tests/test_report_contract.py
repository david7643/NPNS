from datetime import datetime, date
from types import SimpleNamespace
import unittest
from app.routers.report import build_report_data, date_bounds
from app.schemas import ReportSummaryResponse, ReportDetailResponse, ReportByDateResponse


class ReportContractTests(unittest.TestCase):
    def test_korean_date_bounds_cross_the_utc_date_boundary(self):
        self.assertEqual(date_bounds(date(2026, 10, 13), 540),
                         (datetime(2026, 10, 12, 15), datetime(2026, 10, 13, 15)))

    def test_legacy_utc_query_still_defaults_to_utc(self):
        self.assertEqual(date_bounds(date(2026, 10, 13), 0),
                         (datetime(2026, 10, 13), datetime(2026, 10, 14)))
    def test_all_response_models_preserve_chart_levels(self):
        session = SimpleNamespace(id=1, started_at=datetime(2026, 10, 12, 13),
                                  ended_at=datetime(2026, 10, 12, 14))
        logs = [SimpleNamespace(timestamp=datetime(2026, 10, 12, 13, 10),
                                drowsy_level=level, latitude=None, longitude=None) for level in (1, 2, 3)]
        data = build_report_data(session, logs)
        by_date = {**data, 'date': '2026-10-12', 'session_count': 1}
        for model, values in [(ReportSummaryResponse, data), (ReportDetailResponse, data),
                              (ReportByDateResponse, by_date)]:
            with self.subTest(model=model.__name__):
                chart = model(**values).model_dump()['chart_data'][0]
                self.assertEqual(chart, dict(hour=13, count=3, level1=1, level2=1, level3=1, max_level=3))
