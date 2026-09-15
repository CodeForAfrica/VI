"""Focused tests for ingestion deadline and database-log safety helpers."""
import time
import unittest
from unittest import mock

from dashboard.services import mediacloud_ingestion_service as ingestion


class MediaCloudTimeoutTests(unittest.TestCase):
    def test_default_timeout_is_bounded(self):
        self.assertEqual(
            ingestion._mediacloud_request_timeout(),
            ingestion.MEDIACLOUD_TIMEOUT_SECONDS,
        )

    def test_timeout_is_capped_by_remaining_deadline(self):
        now = time.time()
        with mock.patch.object(ingestion.time, "time", return_value=now):
            timeout = ingestion._mediacloud_request_timeout(now + 6)
        self.assertEqual(timeout, min(ingestion.MEDIACLOUD_TIMEOUT_SECONDS, 5))

    def test_expired_deadline_skips_request(self):
        self.assertIsNone(
            ingestion._mediacloud_request_timeout(time.time() - 1)
        )


class DatabaseLoggingSafetyTests(unittest.TestCase):
    def test_raw_insert_includes_required_inference_state(self):
        self.assertIn("inference_status", ingestion.db_columns)
        self.assertIn("inference_attempts", ingestion.db_columns)

    def test_sql_parameters_are_hidden(self):
        self.assertTrue(ingestion.engine.hide_parameters)

    def test_driver_code_is_extracted_without_rendering_exception(self):
        class Original:
            sqlstate = "23505"

        class DatabaseFailure(Exception):
            orig = Original()

            def __str__(self):
                raise AssertionError("raw database exception must not be rendered")

        self.assertEqual(ingestion._database_error_code(DatabaseFailure()), "23505")


class ScrapeFailureTests(unittest.TestCase):
    class Search:
        TIMEOUT_SECS = None

        def story_list(self, *args, **kwargs):
            return ([{
                "url": "https://example.org/article",
                "publish_date": None,
                "media_name": "Example",
                "language": "en",
            }], None)

    def test_missing_scrape_content_is_logged_and_counted_without_crashing(self):
        events = []
        with mock.patch.object(ingestion, "mc_search", self.Search()), \
             mock.patch.object(ingestion, "TARGET_COLLECTION_IDS", {"Kenya": 1}), \
             mock.patch.object(ingestion, "ACTOR_COLLECTION_IDS", {"France": 2}), \
             mock.patch.object(ingestion, "QUERY_BY_COUNTRY", {"Kenya": "Kenya"}), \
             mock.patch.object(ingestion.time, "sleep"), \
             mock.patch.object(ingestion, "url_exists", return_value=False), \
             mock.patch.object(
                 ingestion, "scrape_full_text_robust",
                 return_value=(None, {
                     "error_code": "scrape_request_failed",
                     "error_type": "Timeout",
                     "attempts": 2,
                 }),
             ), \
             mock.patch.object(ingestion, "cache", mock.Mock()):
            result = ingestion.main(
                event_logger=lambda level, event, **fields:
                events.append((level, event, fields))
            )

        self.assertEqual(result["scrape_failed"], 1)
        self.assertEqual(result["inserted"], 0)
        self.assertTrue(any(event == "article_scrape_failed"
                            for _, event, _ in events))

    def test_insert_explicitly_sets_pending_state_and_attempt_count(self):
        class Transaction:
            def __enter__(self):
                return object()

            def __exit__(self, *args):
                return False

        fake_engine = mock.Mock()
        fake_engine.begin.return_value = Transaction()
        inserted_rows = []

        def capture_insert(frame, *args, **kwargs):
            inserted_rows.append(frame.iloc[0].to_dict())

        content = "Kenya " + ("article text " * 100)
        with mock.patch.object(ingestion, "mc_search", self.Search()), \
             mock.patch.object(ingestion, "TARGET_COLLECTION_IDS", {"Kenya": 1}), \
             mock.patch.object(ingestion, "ACTOR_COLLECTION_IDS", {"France": 2}), \
             mock.patch.object(ingestion, "QUERY_BY_COUNTRY", {"Kenya": "Kenya"}), \
             mock.patch.object(ingestion.time, "sleep"), \
             mock.patch.object(ingestion, "url_exists", return_value=False), \
             mock.patch.object(
                 ingestion, "scrape_full_text_robust",
                 return_value=(content, {"attempts": 1, "http_status": 200}),
             ), \
             mock.patch.object(ingestion, "engine", fake_engine), \
             mock.patch.object(ingestion.pd.DataFrame, "to_sql",
                               autospec=True, side_effect=capture_insert), \
             mock.patch.object(ingestion, "cache", mock.Mock()):
            result = ingestion.main(event_logger=lambda *args, **kwargs: None)

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(inserted_rows[0]["inference_status"], "pending")
        self.assertEqual(inserted_rows[0]["inference_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
