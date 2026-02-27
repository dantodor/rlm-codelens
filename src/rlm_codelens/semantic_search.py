"""Local semantic search analysis using jina-grep.

Provides GPU-accelerated semantic code search via jina-grep-cli (Apple Silicon).
When jina-grep is installed, this module automatically enriches architecture
analysis with semantic module classification, anti-pattern detection, hidden
dependency pre-filtering, and architecturally significant file identification.

When jina-grep is not installed, all methods gracefully return empty defaults
and the rest of the pipeline operates unchanged.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from rlm_codelens.repo_scanner import RepositoryStructure

JINA_GREP_AVAILABLE = shutil.which("jina-grep") is not None

# Labels for zero-shot module classification
LAYER_LABELS: Dict[str, str] = {
    "data model database schema ORM migration": "data",
    "business logic service domain handler processor": "business",
    "API endpoint route controller REST GraphQL": "api",
    "utility helper common shared library": "util",
    "configuration settings constants": "config",
    "test fixture mock": "test",
}

# Queries for hidden dependency pre-filtering (code model, nl2code)
HIDDEN_DEP_QUERIES = [
    "dynamic import loading plugin registry",
    "importlib import_module __import__ getattr reflection",
    "dependency injection container factory provider",
    "runtime module loading eval exec compile",
]

# Queries for semantic anti-pattern detection
ANTI_PATTERN_QUERIES: Dict[str, tuple] = {
    "god class doing too many responsibilities": ("god_module_semantic", "high"),
    "tight coupling between unrelated modules": ("tight_coupling_semantic", "medium"),
    "circular dependency workaround hack": ("circular_dep_workaround", "medium"),
    "hardcoded configuration magic number": ("hardcoded_config", "low"),
    "duplicated copy pasted code logic": ("code_duplication", "medium"),
    "deeply nested callback pyramid of doom": ("deep_nesting", "low"),
}

# Queries for identifying architecturally significant files
SIGNIFICANCE_QUERIES = [
    "core architecture main entry point initialization",
    "public API interface contract",
    "complex business logic algorithm",
    "database connection persistence storage",
    "middleware interceptor cross-cutting concern",
    "event handler message queue async processing",
]

# Regex to parse jina-grep output: file:line:score:content
_OUTPUT_RE = re.compile(r"^(.+?):(\d+):([\d.]+):(.*)$")


class SemanticSearchAnalyzer:
    """Performs local semantic analysis using jina-grep.

    Args:
        structure: Scanned repository structure.
        repo_path: Filesystem path to the repository (for jina-grep to search).
        verbose: Print progress messages.
        model: Model for text-based semantic queries.
        code_model: Model for code search (nl2code) queries.
        score_threshold: Minimum similarity score to include results.
    """

    def __init__(
        self,
        structure: RepositoryStructure,
        repo_path: str,
        verbose: bool = True,
        model: str = "jina-embeddings-v3",
        code_model: str = "jina-code-embeddings-1.5b",
        score_threshold: float = 0.3,
    ):
        self.structure = structure
        self.repo_path = str(Path(repo_path).resolve())
        self.verbose = verbose
        self.model = model
        self.code_model = code_model
        self.score_threshold = score_threshold

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [Semantic] {msg}")

    def _run_jina_grep(
        self,
        args: List[str],
        timeout: int = 120,
    ) -> List[Dict[str, Any]]:
        """Run jina-grep with the given arguments and parse output.

        Args:
            args: Arguments to pass to jina-grep.
            timeout: Timeout in seconds.

        Returns:
            List of parsed result dicts with file, line, score, content keys.
        """
        if not JINA_GREP_AVAILABLE:
            return []

        cmd = ["jina-grep", *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.repo_path,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    self._log(f"jina-grep stderr: {stderr[:200]}")
                return []
            return self._parse_output(result.stdout)
        except FileNotFoundError:
            self._log("jina-grep not found in PATH")
            return []
        except subprocess.TimeoutExpired:
            self._log(f"jina-grep timed out after {timeout}s")
            return []
        except subprocess.SubprocessError as e:
            self._log(f"jina-grep error: {e}")
            return []

    def _parse_output(self, raw_output: str) -> List[Dict[str, Any]]:
        """Parse jina-grep output lines into structured results.

        Expected format: file:line_number:score:content

        Args:
            raw_output: Raw stdout from jina-grep.

        Returns:
            List of result dicts, filtered by score threshold.
        """
        results: List[Dict[str, Any]] = []
        for line in raw_output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            match = _OUTPUT_RE.match(line)
            if not match:
                continue
            file_path, line_no, score_str, content = match.groups()
            try:
                score = float(score_str)
            except ValueError:
                continue
            if score < self.score_threshold:
                continue
            # Normalize path to be relative to repo root
            try:
                rel_path = str(Path(file_path).relative_to(self.repo_path))
            except ValueError:
                rel_path = file_path
            results.append(
                {
                    "file": rel_path,
                    "line": int(line_no),
                    "score": score,
                    "content": content,
                }
            )
        return results

    def classify_modules_semantic(self) -> Dict[str, str]:
        """Classify modules into architectural layers using zero-shot classification.

        Runs jina-grep with -e flags for each layer label, then assigns each
        module to the layer with the highest similarity score.

        Returns:
            Dict mapping module path to layer name.
        """
        if not JINA_GREP_AVAILABLE:
            return {}

        self._log("Classifying modules into architectural layers...")

        # Build -e args for zero-shot classification
        e_args: List[str] = []
        label_order: List[str] = []
        for label_text, layer_name in LAYER_LABELS.items():
            e_args.extend(["-e", label_text])
            label_order.append(layer_name)

        results = self._run_jina_grep([*e_args, self.repo_path])

        if not results:
            self._log("No classification results from jina-grep")
            return {}

        # Aggregate: for each file, track the best score per layer
        file_scores: Dict[str, Dict[str, float]] = {}
        for r in results:
            f = r["file"]
            if f not in file_scores:
                file_scores[f] = {}
            # Map result back to a layer — pick the layer with best score
            for layer in LAYER_LABELS.values():
                if layer not in file_scores[f] or r["score"] > file_scores[f][layer]:
                    file_scores[f][layer] = r["score"]

        # Assign each known module to its highest-scoring layer
        classifications: Dict[str, str] = {}
        known_modules = set(self.structure.modules.keys())
        for file_path, scores in file_scores.items():
            if file_path in known_modules:
                best_layer = max(scores, key=lambda k: scores[k])
                classifications[file_path] = best_layer

        self._log(f"Classified {len(classifications)} modules semantically")
        return classifications

    def prefilter_hidden_deps(self) -> List[str]:
        """Identify files likely to contain hidden/dynamic dependencies.

        Uses code-model semantic search to find files with dynamic import
        patterns, plugin registries, etc.

        Returns:
            List of file paths that likely contain hidden dependencies.
        """
        if not JINA_GREP_AVAILABLE:
            return []

        self._log("Pre-filtering for hidden dependencies...")

        candidate_files: set = set()
        for query in HIDDEN_DEP_QUERIES:
            results = self._run_jina_grep(
                [
                    "--model", self.code_model,
                    "--task", "nl2code",
                    query,
                    self.repo_path,
                ]
            )
            for r in results:
                candidate_files.add(r["file"])

        self._log(f"Found {len(candidate_files)} candidate files for hidden deps")
        return sorted(candidate_files)

    def detect_anti_patterns_semantic(self) -> List[Dict[str, Any]]:
        """Detect anti-patterns using semantic search.

        Queries the codebase for common anti-pattern descriptions and
        returns results compatible with the anti_patterns list format.

        Returns:
            List of anti-pattern dicts with type, module, details, severity keys.
        """
        if not JINA_GREP_AVAILABLE:
            return []

        self._log("Detecting anti-patterns via semantic search...")

        anti_patterns: List[Dict[str, Any]] = []
        seen_files: Dict[str, str] = {}  # file -> highest severity

        severity_rank = {"high": 3, "medium": 2, "low": 1}

        for query, (ap_type, severity) in ANTI_PATTERN_QUERIES.items():
            results = self._run_jina_grep([query, self.repo_path])
            for r in results:
                file_path = r["file"]
                # Deduplicate: keep highest severity per file
                existing_severity = seen_files.get(file_path)
                if existing_severity:
                    if severity_rank.get(severity, 0) <= severity_rank.get(
                        existing_severity, 0
                    ):
                        continue
                seen_files[file_path] = severity
                anti_patterns.append(
                    {
                        "type": ap_type,
                        "module": file_path,
                        "details": f"Semantic match (score={r['score']:.2f}): {r['content'][:120]}",
                        "severity": severity,
                    }
                )

        self._log(f"Found {len(anti_patterns)} semantic anti-patterns")
        return anti_patterns

    def identify_significant_files(self, top_n: int = 30) -> List[Dict[str, Any]]:
        """Identify the most architecturally significant files.

        Runs multiple semantic queries and aggregates scores per file
        to find files that are most architecturally important.

        Args:
            top_n: Maximum number of files to return.

        Returns:
            List of dicts with path, score, matched_queries keys,
            sorted by aggregate score descending.
        """
        if not JINA_GREP_AVAILABLE:
            return []

        self._log("Identifying architecturally significant files...")

        file_data: Dict[str, Dict[str, Any]] = {}

        for query in SIGNIFICANCE_QUERIES:
            results = self._run_jina_grep([query, self.repo_path])
            for r in results:
                f = r["file"]
                if f not in file_data:
                    file_data[f] = {"score": 0.0, "matched_queries": []}
                file_data[f]["score"] += r["score"]
                if query not in file_data[f]["matched_queries"]:
                    file_data[f]["matched_queries"].append(query)

        # Sort by aggregate score
        ranked = sorted(file_data.items(), key=lambda x: x[1]["score"], reverse=True)

        significant = [
            {
                "path": path,
                "score": round(data["score"], 3),
                "matched_queries": data["matched_queries"],
            }
            for path, data in ranked[:top_n]
        ]

        self._log(f"Identified {len(significant)} significant files")
        return significant

    def run_all(self) -> Dict[str, Any]:
        """Run all semantic analysis steps and return combined results.

        Returns:
            Dict with classifications, anti_patterns, hidden_dep_candidates,
            significant_files keys.
        """
        if not JINA_GREP_AVAILABLE:
            self._log("jina-grep not installed, skipping semantic analysis")
            return {}

        results: Dict[str, Any] = {}

        try:
            results["classifications"] = self.classify_modules_semantic()
        except Exception as e:
            self._log(f"classify_modules_semantic failed: {e}")
            results["classifications"] = {}

        try:
            results["hidden_dep_candidates"] = self.prefilter_hidden_deps()
        except Exception as e:
            self._log(f"prefilter_hidden_deps failed: {e}")
            results["hidden_dep_candidates"] = []

        try:
            results["anti_patterns"] = self.detect_anti_patterns_semantic()
        except Exception as e:
            self._log(f"detect_anti_patterns_semantic failed: {e}")
            results["anti_patterns"] = []

        try:
            results["significant_files"] = self.identify_significant_files()
        except Exception as e:
            self._log(f"identify_significant_files failed: {e}")
            results["significant_files"] = []

        return results
