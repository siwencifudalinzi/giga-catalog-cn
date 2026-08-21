import json
import re
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 compatibility
    tomllib = None


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "refresh-catalog.yml"
DEPLOY_WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "deploy-catalog.yml"
)
NETLIFY_PATH = REPOSITORY_ROOT / "netlify.toml"
INDEX_PATH = REPOSITORY_ROOT / "public" / "index.html"
GITIGNORE_PATH = REPOSITORY_ROOT / ".gitignore"
GITATTRIBUTES_PATH = REPOSITORY_ROOT / ".gitattributes"
DEFAULT_BRANCH_GUARD_PATTERN = re.compile(
    r"github\.ref == format\('(?P<template>refs/heads/\{0\})',\s*"
    r"github\.event\.repository\.default_branch\)"
)


def evaluate_default_branch_guard(workflow, ref, default_branch):
    match = DEFAULT_BRANCH_GUARD_PATTERN.search(workflow)
    if match is None:
        raise AssertionError("workflow has no branch-type-safe default branch guard")
    expected_ref = match.group("template").format(default_branch)
    return ref == expected_ref


class RefreshWorkflowConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            WORKFLOW_PATH.is_file(),
            "the catalog refresh workflow must exist",
        )
        self.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_schedules_dispatch_and_concurrency_are_bounded(self):
        schedule_entries = re.findall(
            r"- cron: ['\"](?P<cron>[^'\"]+)['\"]",
            self.workflow,
        )
        self.assertEqual(
            schedule_entries,
            [
                "17 19 * * *",
                "30 3 * * *",
                "47 20 * * 6",
            ],
        )
        self.assertNotRegex(self.workflow, r"(?m)^\s+timezone:")
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertRegex(
            self.workflow,
            r"(?ms)^\s+mode:\s*$.*?^\s+type:\s+choice\s*$.*?"
            r"^\s+options:\s*\[incremental,\s*audit,\s*links-only\]\s*$",
        )
        self.assertRegex(self.workflow, r"(?m)^\s+start_id:\s*$")
        self.assertRegex(self.workflow, r"(?m)^\s+end_id:\s*$")
        self.assertRegex(
            self.workflow,
            r"(?ms)^permissions:\s*$\s+contents:\s+write\s*$",
        )
        self.assertRegex(
            self.workflow,
            r"(?ms)^concurrency:\s*$.*?^\s+group:\s+"
            r"catalog-refresh-\$\{\{\s*github\.repository\s*\}\}-"
            r"\$\{\{\s*github\.ref\s*\}\}\s*$.*?"
            r"^\s+cancel-in-progress:\s+false\s*$",
        )
        self.assertNotRegex(self.workflow, r"(?m)^\s+queue:")
        self.assertRegex(
            self.workflow,
            r"(?ms)^\s+refresh:\s*$\s+if:\s+github\.ref == "
            r"format\('refs/heads/\{0\}',\s*"
            r"github\.event\.repository\.default_branch\)\s*$",
        )

    def test_branch_guard_rejects_same_named_tags(self):
        cases = (
            ("refs/heads/master", "master", True),
            ("refs/tags/master", "master", False),
            ("refs/heads/feature", "master", False),
            ("refs/heads/release/v1", "release/v1", True),
            ("refs/tags/release/v1", "release/v1", False),
        )
        for ref, default_branch, expected in cases:
            with self.subTest(ref=ref, default_branch=default_branch):
                self.assertEqual(
                    evaluate_default_branch_guard(
                        self.workflow,
                        ref,
                        default_branch,
                    ),
                    expected,
                )

    def test_refresh_inputs_are_validated_before_building_argument_array(self):
        self.assertIn("actions/checkout@v7", self.workflow)
        self.assertIn("actions/setup-python@v7", self.workflow)
        self.assertIn("actions/setup-node@v7", self.workflow)
        self.assertIn("python-version: '3.11'", self.workflow)
        self.assertIn("node-version: '24'", self.workflow)
        self.assertIn(
            "python -m pip install -r requirements.txt",
            self.workflow,
        )
        self.assertIn('REFRESH_ARGS=()', self.workflow)
        self.assertIn(
            'case "$INPUT_MODE" in',
            self.workflow,
        )
        self.assertIn(
            'case "$SCHEDULE" in',
            self.workflow,
        )
        self.assertIn(
            '"17 19 * * *") MODE="links-only"',
            self.workflow,
        )
        self.assertIn(
            '"30 3 * * *") MODE="incremental"',
            self.workflow,
        )
        self.assertIn(
            '"47 20 * * 6") MODE="audit"',
            self.workflow,
        )
        self.assertIn(
            '[[ "$INPUT_START_ID" =~ ^[1-9][0-9]*$ ]]',
            self.workflow,
        )
        self.assertIn(
            '[[ "$INPUT_END_ID" =~ ^[1-9][0-9]*$ ]]',
            self.workflow,
        )
        self.assertIn(
            'REFRESH_ARGS+=(--start-id "$INPUT_START_ID")',
            self.workflow,
        )
        self.assertIn(
            'REFRESH_ARGS+=(--end-id "$INPUT_END_ID")',
            self.workflow,
        )
        self.assertIn(
            'python scripts/refresh.py --mode "$MODE" "${REFRESH_ARGS[@]}"',
            self.workflow,
        )
        self.assertIn(
            "python scripts/sync_official_tags.py --max-products 50",
            self.workflow,
        )
        self.assertLess(
            self.workflow.index('python scripts/refresh.py --mode "$MODE"'),
            self.workflow.index("python scripts/sync_official_tags.py --max-products 50"),
        )
        run_blocks = "\n".join(
            line for line in self.workflow.splitlines() if not line.lstrip().startswith("env:")
        )
        self.assertNotRegex(
            run_blocks,
            r"python scripts/refresh\.py[^\n]*\$\{\{\s*inputs\.",
        )

    def test_validation_precedes_path_limited_guarded_commit(self):
        test_command = (
            "python -m unittest discover -s tests/python -v && npm run test:js"
        )
        self.assertIn(test_command, self.workflow)
        self.assertNotIn("npm test", self.workflow)
        stage_command = (
            "git add -- data/raw data/state data/update-summary.json "
            "public/data/catalog.json public/data/catalog-core.json "
            "public/data/catalog-tags.json public/data/featured-covers.json "
            "public/media/featured-covers"
        )
        self.assertIn(stage_command, self.workflow)
        self.assertNotIn("git add -A", self.workflow)
        self.assertNotIn("git add .", self.workflow)
        self.assertIn("git diff --cached --quiet", self.workflow)
        self.assertIn('changed=true', self.workflow)
        self.assertIn(
            'if: steps.changes.outputs.changed == \'true\'',
            self.workflow,
        )
        self.assertIn(
            'git config user.name "github-actions[bot]"',
            self.workflow,
        )
        self.assertIn(
            'git config user.email '
            '"41898282+github-actions[bot]@users.noreply.github.com"',
            self.workflow,
        )
        self.assertIn('git commit -m "data: refresh catalog"', self.workflow)
        self.assertIn("git push", self.workflow)
        self.assertLess(
            self.workflow.index(test_command),
            self.workflow.index(stage_command),
        )

    def test_refresh_has_no_deployment_or_netlify_credentials(self):
        self.assertNotIn("Deploy catalog to Netlify", self.workflow)
        self.assertNotIn("netlify-cli", self.workflow)
        self.assertNotIn("NETLIFY_AUTH_TOKEN", self.workflow)
        self.assertNotIn("NETLIFY_SITE_ID", self.workflow)
        self.assertNotIn("secrets.", self.workflow)


class DeployWorkflowConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            DEPLOY_WORKFLOW_PATH.is_file(),
            "the split production deployment workflow must exist",
        )
        self.workflow = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_only_default_branch_non_bot_push_or_successful_refresh_runs(self):
        self.assertRegex(
            self.workflow,
            r"(?ms)^on:\s*$.*?^\s+push:\s*$.*?"
            r"^\s+workflow_run:\s*$.*?"
            r"^\s+workflows:\s*\[\"Refresh catalog\"\]\s*$.*?"
            r"^\s+types:\s*\[completed\]\s*$",
        )
        self.assertRegex(
            self.workflow,
            r"(?s)github\.event_name == 'push'.*?"
            r"github\.ref == format\('refs/heads/\{0\}',\s*"
            r"github\.event\.repository\.default_branch\).*?"
            r"!endsWith\(github\.actor, '\[bot\]'\).*?\|\|.*?"
            r"github\.event_name == 'workflow_run'.*?"
            r"github\.event\.workflow_run\.conclusion == 'success'.*?"
            r"github\.event\.workflow_run\.head_branch == "
            r"github\.event\.repository\.default_branch",
        )

    def test_push_branch_guard_rejects_same_named_tags(self):
        cases = (
            ("refs/heads/master", "master", True),
            ("refs/tags/master", "master", False),
            ("refs/heads/feature", "master", False),
            ("refs/heads/release/v1", "release/v1", True),
            ("refs/tags/release/v1", "release/v1", False),
        )
        for ref, default_branch, expected in cases:
            with self.subTest(ref=ref, default_branch=default_branch):
                self.assertEqual(
                    evaluate_default_branch_guard(
                        self.workflow,
                        ref,
                        default_branch,
                    ),
                    expected,
                )

    def test_checks_out_current_default_branch_with_pages_permissions_and_concurrency(self):
        self.assertRegex(
            self.workflow,
            r"(?ms)^permissions:\s*$\s+contents:\s+read\s*$\s+"
            r"pages:\s+write\s*$\s+id-token:\s+write\s*$",
        )
        self.assertRegex(
            self.workflow,
            r"(?ms)^concurrency:\s*$.*?^\s+group:\s+"
            r"catalog-production-\$\{\{\s*github\.repository\s*\}\}\s*$.*?"
            r"^\s+cancel-in-progress:\s+false\s*$",
        )
        self.assertIn("actions/checkout@v7", self.workflow)
        self.assertRegex(
            self.workflow,
            r"(?ms)uses:\s+actions/checkout@v7\s+with:\s+"
            r"ref:\s+\$\{\{\s*github\.event\.repository\.default_branch\s*\}\}",
        )
        self.assertNotIn("github.event.workflow_run.head_sha", self.workflow)
        self.assertNotIn("github.sha", self.workflow)

    def test_runs_all_tests_and_uses_no_deployment_secret(self):
        self.assertIn(
            "python -m unittest discover -s tests/python -v",
            self.workflow,
        )
        self.assertIn("npm run test:js", self.workflow)
        self.assertNotIn("secrets.", self.workflow)
        self.assertNotIn("NETLIFY_AUTH_TOKEN", self.workflow)
        self.assertNotIn("NETLIFY_SITE_ID", self.workflow)

    def test_uploads_only_public_and_deploys_with_official_pages_actions(self):
        self.assertIn(
            "actions/configure-pages@v5",
            self.workflow,
        )
        self.assertIn(
            "actions/upload-pages-artifact@v4",
            self.workflow,
        )
        self.assertIn(
            "path: public",
            self.workflow,
        )
        self.assertIn(
            "actions/deploy-pages@v4",
            self.workflow,
        )
        self.assertRegex(self.workflow, r"(?m)^\s+environment:\s*$")
        self.assertRegex(self.workflow, r"(?m)^\s+name:\s+github-pages\s*$")
        self.assertRegex(
            self.workflow,
            r"(?m)^\s+url:\s+\$\{\{\s*steps\.deployment\.outputs\.page_url\s*\}\}\s*$",
        )
        self.assertNotIn("netlify-cli", self.workflow)


class NetlifyConfigTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NETLIFY_PATH.is_file(), "netlify.toml must exist")
        self.source = NETLIFY_PATH.read_text(encoding="utf-8")
        self.config = (
            tomllib.loads(self.source)
            if tomllib is not None
            else None
        )

    def test_only_public_is_published_without_a_build_or_spa_rewrite(self):
        self.assertRegex(
            self.source,
            r'(?ms)^\[build\]\s*$\s*publish\s*=\s*"public"\s*$',
        )
        self.assertNotRegex(self.source, r"(?m)^\s*command\s*=")
        self.assertNotIn("[[redirects]]", self.source)
        if self.config is not None:
            self.assertEqual(self.config["build"]["publish"], "public")
            self.assertNotIn("command", self.config["build"])
            self.assertNotIn("redirects", self.config)

    def test_security_headers_are_applied_to_every_served_path(self):
        headers = self._header_values("/*")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertEqual(
            headers["Permissions-Policy"],
            "camera=(), geolocation=(), microphone=()",
        )
        csp = headers["Content-Security-Policy"]
        for directive in (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' https: data:",
            "connect-src 'self'",
        ):
            self.assertIn(directive, csp)

    def test_remote_image_cdn_is_restricted_to_giga_title_assets(self):
        if self.config is not None:
            patterns = self.config.get("images", {}).get("remote_images", [])
        else:
            match = re.search(
                r"(?m)^\s*remote_images\s*=\s*(\[[^\n]+\])\s*$",
                self.source,
            )
            self.assertIsNotNone(match, "missing images.remote_images")
            patterns = json.loads(match.group(1))
        self.assertEqual(
            patterns,
            [r"https:\/\/www\.giga-web\.jp\/db_titles\/.*"],
        )
        pattern = re.compile(patterns[0])
        self.assertIsNotNone(
            pattern.fullmatch(
                "https://www.giga-web.jp/db_titles/spsf/spsf-44.jpg",
            ),
        )
        for disallowed in (
            "https://www.giga-web.jp/",
            "https://www.giga-web.jp/assets/banner.jpg",
            "https://evil.example/db_titles/spsf/spsf-44.jpg",
            "https://www.giga-web.jp.evil.example/db_titles/x.jpg",
        ):
            self.assertIsNone(pattern.fullmatch(disallowed))

    def test_unversioned_assets_use_short_revalidated_caches(self):
        self.assertEqual(
            self._header_values("/")["Cache-Control"],
            "public, max-age=0, must-revalidate",
        )
        self.assertEqual(
            self._header_values("/index.html")["Cache-Control"],
            "public, max-age=0, must-revalidate",
        )
        catalog_cache = self._header_values("/data/catalog.json")[
            "Cache-Control"
        ]
        self.assertLessEqual(self._max_age(catalog_cache), 300)
        self.assertIn("must-revalidate", catalog_cache)

        for path in ("/css/*", "/js/*"):
            asset_cache = self._header_values(path)["Cache-Control"]
            self.assertLessEqual(self._max_age(asset_cache), 3600)
            self.assertIn("must-revalidate", asset_cache)

        release_cache = self._header_values(
            "/.well-known/giga-release.json"
        )["Cache-Control"]
        self.assertEqual(release_cache, "no-store")
        covers_cache = self._header_values(
            "/media/featured-covers/g/*"
        )["Cache-Control"]
        self.assertEqual(
            covers_cache,
            "public, max-age=31536000, immutable",
        )
        manifest_cache = self._header_values(
            "/data/featured-covers.json"
        )["Cache-Control"]
        self.assertLessEqual(self._max_age(manifest_cache), 300)
        self.assertIn("must-revalidate", manifest_cache)

    def _header_values(self, path):
        if self.config is not None:
            matches = [
                block["values"]
                for block in self.config.get("headers", [])
                if block.get("for") == path
            ]
            self.assertEqual(len(matches), 1, f"expected one header block for {path}")
            return matches[0]

        blocks = re.split(r"(?m)^\[\[headers\]\]\s*$", self.source)[1:]
        for block in blocks:
            path_match = re.search(r'(?m)^\s*for\s*=\s*"([^"]+)"\s*$', block)
            if path_match and path_match.group(1) == path:
                values = {}
                for key, value in re.findall(
                    r'(?m)^\s*([A-Za-z-]+)\s*=\s*"([^"]*)"\s*$',
                    block,
                ):
                    if key != "for":
                        values[key] = value
                return values
        self.fail(f"missing header block for {path}")

    @staticmethod
    def _max_age(value):
        match = re.search(r"(?:^|,\s*)max-age=(\d+)(?:,|$)", value)
        if match is None:
            raise AssertionError(f"missing max-age in {value!r}")
        return int(match.group(1))


class FrontendPerformanceHintTests(unittest.TestCase):
    def test_first_render_data_is_preloaded_once_with_matching_cors_mode(self):
        source = INDEX_PATH.read_text(encoding="utf-8")
        head = source.split("</head>", 1)[0]
        for path in (
            "data/catalog-core.json",
            "data/featured-covers.json",
        ):
            with self.subTest(path=path):
                pattern = (
                    r'<link\s+rel="preload"\s+href="'
                    + re.escape(path)
                    + r'"\s+as="fetch"\s+crossorigin="anonymous"'
                    + r'(?:\s+fetchpriority="high")?\s*>'
                )
                self.assertEqual(len(re.findall(pattern, head)), 1)

    def test_catalog_preload_is_high_priority_and_module_graph_is_parallelized(self):
        source = INDEX_PATH.read_text(encoding="utf-8")
        head = source.split("</head>", 1)[0]
        self.assertRegex(
            head,
            r'<link\s+rel="preload"\s+href="data/catalog-core\.json"\s+'
            r'as="fetch"\s+crossorigin="anonymous"\s+fetchpriority="high"\s*>',
        )
        for path in (
            "js/app.js",
            "js/catalog.js",
            "js/render.js",
            "js/favorites.js",
            "js/tags.js",
            "js/runtime-tags.js",
        ):
            with self.subTest(path=path):
                pattern = (
                    r'<link\s+rel="modulepreload"\s+href="'
                    + re.escape(path)
                    + r'"\s*>'
                )
                self.assertEqual(len(re.findall(pattern, head)), 1)
        self.assertNotIn('href="data/catalog.json"', head)
        self.assertNotIn('href="data/catalog-tags.json"', head)


class LocalDeploymentStateTests(unittest.TestCase):
    def test_netlify_cli_state_is_ignored(self):
        ignored_paths = {
            line.strip()
            for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(".netlify/", ignored_paths)

    def test_generated_provenance_files_are_checked_out_with_lf(self):
        self.assertTrue(
            GITATTRIBUTES_PATH.is_file(),
            ".gitattributes must pin generated data to LF on every OS",
        )
        attributes = GITATTRIBUTES_PATH.read_text(encoding="utf-8").splitlines()
        for pattern in (
            "data/raw/*.json text eol=lf",
            "data/raw/*.csv binary",
            "data/state/*.json text eol=lf",
            "data/update-summary.json text eol=lf",
            "public/data/*.json text eol=lf",
            "*.html text eol=lf",
            "*.css text eol=lf",
            "*.svg text eol=lf",
        ):
            self.assertIn(pattern, attributes)


if __name__ == "__main__":
    unittest.main()
