"""
Smoke tests for BoxR. These don't try to verify behavior end-to-end —
they just confirm that each module imports cleanly and basic operations
on empty/temp data don't crash.

Run with: python3 -m pytest tests/  (or just python3 tests/test_smoke.py)
"""

import os
import sys
import tempfile
import unittest

# Make sure we can import the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImports(unittest.TestCase):
    """Every module should import without errors."""

    def test_init(self):
        import boxraudio
        self.assertTrue(hasattr(boxraudio, "__version__"))
        self.assertTrue(hasattr(boxraudio, "TAGLINE"))

    def test_all_modules_import(self):
        from boxraudio import (
            banner, cache, config, ui, scanner, operations,
            transaction, preflight, pipeline, stats, audits,
            spectrum, fingerprint, resume, interactive, sanitize,
            commands, errors, constants,
        )
        # Just touching them is the test
        self.assertIsNotNone(banner)


class TestConstants(unittest.TestCase):
    """Constants module should be importable and have all expected values."""

    def test_constants_exist(self):
        from boxraudio.constants import (
            SLOW_FILE_THRESHOLD_SECS,
            CACHE_WRITE_BATCH_SIZE,
            SPECTRUM_WINDOW_POSITIONS,
            SPECTRUM_WINDOW_DURATION_SECS,
            SPECTRUM_LOUDNESS_GATE_DBFS,
            SPECTRUM_SLOPE_STEEP,
            SPECTRUM_SLOPE_MODERATE,
            SPECTRUM_NOISE_FLOOR_DB,
            SPECTRUM_CUTOFF_TOLERANCE,
            QUARANTINE_MAX_AGE_SECS,
            CHECKPOINT_MAX_AGE_SECS,
            DEFAULT_ART_SIZE_THRESHOLD_MB,
            FINGERPRINT_MATCH_THRESHOLD,
        )
        self.assertEqual(SPECTRUM_LOUDNESS_GATE_DBFS, -30.0)


class TestCache(unittest.TestCase):
    """Cache should create itself, accept entries, and read them back."""

    def test_create_and_use(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from boxraudio.cache import TagCache
            cache = TagCache(db_path)
            self.assertEqual(cache.count(), 0)
            self.assertEqual(cache.schema_version(), 1)

            cache.put(
                filepath="/test/file.flac",
                mtime=1000, size=1000,
                artist="a", album="b", title="c",
                format="flac",
            )
            self.assertEqual(cache.count(), 1)

            entry = cache.get("/test/file.flac", 1000, 1000)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["artist"], "a")

            matches = cache.find_by_tags("a", "b", "c")
            self.assertIn("/test/file.flac", matches)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


class TestConfig(unittest.TestCase):
    """Config loading should handle missing files gracefully and validate YAML."""

    def test_missing_file(self):
        from boxraudio.config import load_config
        cfg = load_config("/tmp/nonexistent_boxraudio_config.yaml")
        self.assertIn("defaults", cfg)
        self.assertIn("profiles", cfg)

    def test_path_expansion(self):
        from boxraudio.config import expand_paths
        result = expand_paths({"source": "~/test"})
        self.assertTrue(result["source"].startswith("/"))
        self.assertNotIn("~", result["source"])


class TestErrors(unittest.TestCase):
    """Error translation should map known patterns and pass through unknown."""

    def test_known_pattern(self):
        from boxraudio.errors import translate_error
        msg = translate_error("expected bytes", "/some/file.flac")
        self.assertIn("sanitiz", msg.lower())

    def test_unknown_pattern(self):
        from boxraudio.errors import translate_error
        msg = translate_error("something weird I've never seen")
        self.assertEqual(msg, "something weird I've never seen")


class TestSpectrum(unittest.TestCase):
    """Spectrum module should report availability without crashing."""

    def test_availability(self):
        from boxraudio import spectrum
        # is_available returns a bool either way
        result = spectrum.is_available()
        self.assertIsInstance(result, bool)


class TestFingerprint(unittest.TestCase):
    """Fingerprint module should report availability without crashing."""

    def test_availability(self):
        from boxraudio import fingerprint
        result = fingerprint.is_available()
        self.assertIsInstance(result, bool)


class TestSanitize(unittest.TestCase):
    """Sanitize defaults should be sane."""

    def test_default_keep_tags(self):
        from boxraudio.sanitize import DEFAULT_KEEP_TAGS, ALWAYS_STRIP_PATTERNS
        self.assertIn("artist", DEFAULT_KEEP_TAGS)
        self.assertIn("title", DEFAULT_KEEP_TAGS)
        self.assertIn("replaygain_track_gain", DEFAULT_KEEP_TAGS)
        # MusicBrainz should always be stripped
        self.assertIn("musicbrainz_", ALWAYS_STRIP_PATTERNS)


class TestStatsAndTransaction(unittest.TestCase):
    """Stats and transaction databases should initialize cleanly."""

    def test_stats_init(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            from boxraudio.stats import StatsTracker
            tracker = StatsTracker(db_path)
            run_id = tracker.start_run(profile="test")
            self.assertIsNotNone(run_id)
            tracker.record_metrics(files_moved=10, files_deleted=5)
            tracker.record_metrics(files_moved=3)
            totals = tracker.aggregate_totals()
            # Until we end the run it doesn't show in completed aggregates,
            # but the run row should exist
            tracker.end_run("complete")
            totals = tracker.aggregate_totals()
            self.assertEqual(totals.get("files_moved"), 13)
            self.assertEqual(totals.get("files_deleted"), 5)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_transaction_init(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        with tempfile.TemporaryDirectory() as quarantine:
            try:
                from boxraudio.transaction import TransactionLog
                tx = TransactionLog(db_path=db_path, quarantine=quarantine)
                session_id = tx.start_session(description="test")
                self.assertIsNotNone(session_id)
                tx.end_session("complete")
                last = tx.last_session()
                self.assertEqual(last["id"], session_id)
            finally:
                try:
                    os.unlink(db_path)
                except OSError:
                    pass


class TestCLIParser(unittest.TestCase):
    """The CLI parser should build and parse known flags without errors."""

    def test_parser_builds(self):
        # Load the CLI module from the boxraudio_cli file (no .py extension)
        import importlib.util
        cli_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "boxraudio_cli",
        )
        spec = importlib.util.spec_from_loader(
            "boxraudio_cli",
            importlib.machinery.SourceFileLoader("boxraudio_cli", cli_path),
        )
        cli_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli_mod)
        parser = cli_mod.build_parser()

        # Parse a few different command shapes
        args = parser.parse_args(["-V"])
        self.assertTrue(args.version)

        args = parser.parse_args(["--profile", "ipod", "--run"])
        self.assertEqual(args.profile, ["ipod"])
        self.assertTrue(args.run)

        args = parser.parse_args([
            "-Q", "-t", "/some/path", "--no-reasons",
        ])
        self.assertTrue(args.audit_quality)
        self.assertEqual(args.target, "/some/path")
        self.assertTrue(args.no_reasons)

        args = parser.parse_args([
            "-S", "-t", "/some/path", "--strip-art", "--keep-tag", "foo",
        ])
        self.assertTrue(args.sanitize_tags)
        self.assertTrue(args.strip_art)
        self.assertEqual(args.keep_tag, ["foo"])

        args = parser.parse_args([
            "--profile", "a", "--profile", "b", "--explain",
        ])
        self.assertEqual(args.profile, ["a", "b"])
        self.assertTrue(args.explain)


def main():
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
