"""Unit tests for semantic_search module."""

from unittest.mock import MagicMock, patch

import pytest

from rlm_codelens.semantic_search import SemanticSearchAnalyzer


@pytest.fixture
def mock_structure():
    """Create a minimal RepositoryStructure-like object."""
    mod1 = MagicMock()
    mod1.classes = [{"name": "UserModel"}]
    mod1.functions = [{"name": "get_user"}]
    mod1.imports = ["sqlalchemy"]
    mod1.from_imports = []
    mod1.lines_of_code = 100
    mod1.package = "app.models"

    mod2 = MagicMock()
    mod2.classes = []
    mod2.functions = [{"name": "handle_request"}]
    mod2.imports = ["flask"]
    mod2.from_imports = []
    mod2.lines_of_code = 50
    mod2.package = "app.views"

    structure = MagicMock()
    structure.modules = {
        "app/models.py": mod1,
        "app/views.py": mod2,
    }
    structure.name = "test-repo"
    return structure


@pytest.fixture
def analyzer(mock_structure, tmp_path):
    """Create a SemanticSearchAnalyzer with mocked structure."""
    return SemanticSearchAnalyzer(
        structure=mock_structure,
        repo_path=str(tmp_path),
        verbose=False,
        score_threshold=0.3,
    )


class TestAvailability:
    """Tests for jina-grep availability detection."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", False)
    def test_not_available_returns_empty(self, analyzer):
        assert analyzer.classify_modules_semantic() == {}
        assert analyzer.prefilter_hidden_deps() == []
        assert analyzer.detect_anti_patterns_semantic() == []
        assert analyzer.identify_significant_files() == []
        assert analyzer.run_all() == {}


class TestParseOutput:
    """Tests for output parsing."""

    def test_parse_standard_format(self, analyzer):
        raw = "app/models.py:10:0.85:class UserModel:\napp/views.py:5:0.72:def index():\n"
        results = analyzer._parse_output(raw)
        assert len(results) == 2
        assert results[0]["file"] == "app/models.py"
        assert results[0]["line"] == 10
        assert results[0]["score"] == 0.85
        assert results[0]["content"] == "class UserModel:"

    def test_parse_empty_output(self, analyzer):
        assert analyzer._parse_output("") == []
        assert analyzer._parse_output("\n\n") == []

    def test_parse_malformed_lines_skipped(self, analyzer):
        raw = "this is not valid output\napp/models.py:10:0.85:valid line\n"
        results = analyzer._parse_output(raw)
        assert len(results) == 1
        assert results[0]["file"] == "app/models.py"

    def test_parse_filters_below_threshold(self, analyzer):
        raw = "app/models.py:10:0.15:low score\napp/views.py:5:0.85:high score\n"
        results = analyzer._parse_output(raw)
        assert len(results) == 1
        assert results[0]["file"] == "app/views.py"

    def test_parse_invalid_score(self, analyzer):
        raw = "app/models.py:10:notanumber:some content\n"
        results = analyzer._parse_output(raw)
        assert len(results) == 0


class TestClassifyModules:
    """Tests for semantic module classification."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_classify_modules(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[
            {"file": "app/models.py", "line": 10, "score": 0.85, "content": "class UserModel:"},
        ]):
            result = analyzer.classify_modules_semantic()
            assert "app/models.py" in result

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_classify_empty_results(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[]):
            result = analyzer.classify_modules_semantic()
            assert result == {}


class TestPrefilterHiddenDeps:
    """Tests for hidden dependency pre-filtering."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_prefilter_returns_paths(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[
            {"file": "app/plugins.py", "line": 5, "score": 0.9, "content": "importlib.import_module"},
        ]):
            result = analyzer.prefilter_hidden_deps()
            assert "app/plugins.py" in result


class TestDetectAntiPatterns:
    """Tests for semantic anti-pattern detection."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_detect_returns_anti_patterns(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[
            {"file": "app/models.py", "line": 1, "score": 0.8, "content": "class GodObject:"},
        ]):
            result = analyzer.detect_anti_patterns_semantic()
            assert len(result) >= 1
            ap = result[0]
            assert "type" in ap
            assert "module" in ap
            assert "details" in ap
            assert "severity" in ap


class TestIdentifySignificant:
    """Tests for significant file identification."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_identify_returns_ranked(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[
            {"file": "app/main.py", "line": 1, "score": 0.9, "content": "def main():"},
            {"file": "app/utils.py", "line": 1, "score": 0.5, "content": "def helper():"},
        ]):
            result = analyzer.identify_significant_files(top_n=5)
            assert len(result) >= 1
            assert "path" in result[0]
            assert "score" in result[0]
            assert "matched_queries" in result[0]


class TestRunAll:
    """Tests for the run_all orchestrator."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_run_all_returns_all_keys(self, analyzer):
        with patch.object(analyzer, "_run_jina_grep", return_value=[]):
            result = analyzer.run_all()
            assert "classifications" in result
            assert "hidden_dep_candidates" in result
            assert "anti_patterns" in result
            assert "significant_files" in result

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_run_all_handles_exceptions(self, analyzer):
        with patch.object(
            analyzer, "classify_modules_semantic", side_effect=RuntimeError("fail")
        ), patch.object(
            analyzer, "prefilter_hidden_deps", return_value=[]
        ), patch.object(
            analyzer, "detect_anti_patterns_semantic", return_value=[]
        ), patch.object(
            analyzer, "identify_significant_files", return_value=[]
        ):
            result = analyzer.run_all()
            assert result["classifications"] == {}


class TestSubprocessHandling:
    """Tests for subprocess error handling."""

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_file_not_found(self, analyzer):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = analyzer._run_jina_grep(["test", "."])
            assert result == []

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_timeout(self, analyzer):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = analyzer._run_jina_grep(["test", "."])
            assert result == []

    @patch("rlm_codelens.semantic_search.JINA_GREP_AVAILABLE", True)
    def test_nonzero_exit(self, analyzer):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        with patch("subprocess.run", return_value=mock_result):
            result = analyzer._run_jina_grep(["test", "."])
            assert result == []
