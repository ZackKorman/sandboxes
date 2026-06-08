"""Unit tests for _classify_unknown_tool + Write/Edit boundary — FD-037 + FD-024 + FD-045 + FD-054."""

import os

from nah.hook import _classify_unknown_tool, handle_write, handle_edit, handle_read
from nah import config, paths
from nah.config import NahConfig


class TestClassifyUnknownTool:
    def setup_method(self):
        config._cached_config = NahConfig()

    def teardown_method(self):
        config._cached_config = None

    def test_no_config_returns_ask(self):
        d = _classify_unknown_tool("SomeTool")
        assert d["decision"] == "ask"
        assert "unrecognized tool" in d["reason"]

    def test_global_classify_allow(self):
        config._cached_config = NahConfig(
            classify_global={"mcp_trusted": ["MyTool"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("MyTool")
        assert d["decision"] == "allow"

    def test_global_classify_ask(self):
        config._cached_config = NahConfig(
            classify_global={"db_write": ["DbTool"]},
        )
        d = _classify_unknown_tool("DbTool")
        assert d["decision"] == "ask"

    def test_mcp_skips_project_classify(self):
        config._cached_config = NahConfig(
            classify_project={"mcp_trusted": ["mcp__evil__exfil"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("mcp__evil__exfil")
        assert d["decision"] == "ask"  # project ignored

    def test_non_mcp_uses_project_classify(self):
        config._cached_config = NahConfig(
            classify_project={"package_run": ["CustomRunner"]},
        )
        d = _classify_unknown_tool("CustomRunner")
        assert d["decision"] == "allow"  # package_run → allow

    # --- FD-024 adversarial tests ---

    def test_mcp_classify_prefix_collision(self):
        """mcp__postgres in config must NOT match mcp__postgres__query."""
        config._cached_config = NahConfig(
            classify_global={"mcp_trusted": ["mcp__postgres"]},
            actions={"mcp_trusted": "allow"},
        )
        # Exact match
        d = _classify_unknown_tool("mcp__postgres")
        assert d["decision"] == "allow"
        # Different tool — no match (single-token prefix, not substring)
        d = _classify_unknown_tool("mcp__postgres__query")
        assert d["decision"] == "ask"

    def test_mcp_classified_global_allow(self):
        """Global config can classify and allow MCP tools."""
        config._cached_config = NahConfig(
            classify_global={"mcp_trusted": ["mcp__memory__search"]},
            actions={"mcp_trusted": "allow"},
        )
        d = _classify_unknown_tool("mcp__memory__search")
        assert d["decision"] == "allow"

    # --- FD-045 configurable unknown tool policy ---

    def test_unknown_default_ask(self):
        """No actions config → unknown defaults to ask."""
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "ask"
        assert "unrecognized tool" in d["reason"]

    def test_unknown_actions_block(self):
        """actions.unknown: block → block unknown tools."""
        config._cached_config = NahConfig(actions={"unknown": "block"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "block"
        assert "unrecognized tool" in d["reason"]

    def test_unknown_actions_allow(self):
        """actions.unknown: allow → allow unknown tools."""
        config._cached_config = NahConfig(actions={"unknown": "allow"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "allow"

    def test_unknown_context_falls_to_ask(self):
        """actions.unknown: context → ask (no context resolver for 'unknown' type)."""
        config._cached_config = NahConfig(actions={"unknown": "context"})
        d = _classify_unknown_tool("BrandNewTool")
        assert d["decision"] == "ask"
        assert "no context resolver" in d["reason"]


class TestClassifyUnknownToolContext:
    """FD-055: MCP tools with context policy resolve context via tool_input."""

    def setup_method(self):
        config._cached_config = NahConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            actions={"db_write": "context"},
            db_targets=[
                {"database": "SANDBOX"},
                {"database": "SALES", "schema": "DEV"},
            ],
        )

    def teardown_method(self):
        config._cached_config = None

    def test_mcp_db_write_matching_target_allow(self):
        """MCP tool_input with matching database → allow."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "allowed target" in d["reason"]

    def test_mcp_db_write_matching_db_schema_allow(self):
        """MCP tool_input with matching database+schema → allow."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SALES", "schema": "DEV", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "SALES.DEV" in d["reason"]

    def test_mcp_db_write_non_matching_target_ask(self):
        """MCP tool_input with non-matching database → ask."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "PRODUCTION", "query": "DROP TABLE users"},
        )
        assert d["decision"] == "ask"
        assert "unrecognized target" in d["reason"]

    def test_mcp_db_write_no_database_key_ask(self):
        """MCP tool_input without database key → ask."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"query": "SELECT 1"},
        )
        assert d["decision"] == "ask"
        assert "unknown database target" in d["reason"]

    def test_mcp_db_write_no_tool_input_ask(self):
        """MCP with no tool_input → ask."""
        d = _classify_unknown_tool("mcp__snowflake__execute_sql")
        assert d["decision"] == "ask"

    def test_mcp_db_write_no_db_targets_ask(self):
        """No db_targets configured → ask even with matching input."""
        config._cached_config = NahConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            actions={"db_write": "context"},
            db_targets=[],
        )
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "ask"
        assert "no db_targets configured" in d["reason"]

    def test_mcp_db_write_empty_tool_input_ask(self):
        """Empty dict {} (what main() actually passes) → ask."""
        d = _classify_unknown_tool("mcp__snowflake__execute_sql", {})
        assert d["decision"] == "ask"
        assert "unknown database target" in d["reason"]

    def test_mcp_db_write_case_insensitive(self):
        """Database name matching is case-insensitive."""
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "sandbox", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "allow"
        assert "SANDBOX" in d["reason"]

    def test_mcp_non_db_context_falls_to_ask(self):
        """MCP tool classified as network_outbound + context → ask (no tokens)."""
        config._cached_config = NahConfig(
            classify_global={"network_outbound": ["mcp__api__fetch"]},
            actions={"network_outbound": "context"},
        )
        d = _classify_unknown_tool(
            "mcp__api__fetch",
            {"url": "https://example.com"},
        )
        assert d["decision"] == "ask"
        assert "unknown host" in d["reason"]

    def test_mcp_db_write_default_policy_ask(self):
        """db_write with default policy (ask, not context) → no context resolution."""
        config._cached_config = NahConfig(
            classify_global={"db_write": ["mcp__snowflake__execute_sql"]},
            db_targets=[{"database": "SANDBOX"}],
        )
        d = _classify_unknown_tool(
            "mcp__snowflake__execute_sql",
            {"database": "SANDBOX", "query": "INSERT INTO t VALUES (1)"},
        )
        assert d["decision"] == "ask"
        assert "allowed target" not in d.get("reason", "")


# --- FD-054: Write/Edit project boundary tests ---


class TestWriteEditBoundary:
    """FD-054: Write/Edit enforce project boundary check."""

    def setup_method(self):
        config._cached_config = NahConfig()

    def teardown_method(self):
        config._cached_config = None

    def test_write_inside_project(self, project_root):
        target = os.path.join(project_root, "file.txt")
        d = handle_write({"file_path": target, "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_outside_project(self, project_root):
        d = handle_write({"file_path": "/tmp/outside.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "outside project" in d["reason"]

    def test_edit_outside_project(self, project_root):
        d = handle_edit({"file_path": "/tmp/outside.txt", "old_string": "a", "new_string": "b"})
        assert d["decision"] == "ask"
        assert "outside project" in d["reason"]

    def test_write_to_trusted_path(self, project_root):
        config._cached_config = NahConfig(trusted_paths=["/tmp"])
        d = handle_write({"file_path": "/tmp/trusted.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_sensitive_unchanged(self, project_root):
        """Sensitive paths still block even with boundary check."""
        d = handle_write({"file_path": "~/.ssh/config", "content": "host"})
        assert d["decision"] == "block"

    def test_write_hook_unchanged(self, project_root):
        """Hook self-protection still blocks."""
        d = handle_write({"file_path": "~/.claude/hooks/evil.py", "content": "x"})
        assert d["decision"] == "block"

    def test_read_outside_no_boundary(self, project_root):
        """Read tool has no boundary check — still allows outside reads."""
        d = handle_read({"file_path": "/tmp/outside.txt"})
        assert d["decision"] == "allow"

    def test_trusted_does_not_override_sensitive(self, project_root):
        """trusted_paths cannot bypass sensitive path block."""
        home = os.path.expanduser("~")
        config._cached_config = NahConfig(trusted_paths=[home])
        d = handle_write({"file_path": "~/.ssh/id_rsa", "content": "key"})
        assert d["decision"] == "block"

    def test_profile_none_disables_boundary(self, project_root):
        """profile: none disables boundary check for Write."""
        config._cached_config = NahConfig(profile="none")
        paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "/tmp/anywhere.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_profile_none_clears_sensitive_dirs(self, project_root):
        """profile: none clears _SENSITIVE_DIRS, allowing ~/.ssh."""
        config._cached_config = NahConfig(profile="none")
        paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "~/.ssh/config", "content": "host"})
        # Sensitive dirs cleared, but...
        # Note: check_path runs before boundary, and hook check is first.
        # With profile: none, _SENSITIVE_DIRS is cleared, so sensitive check passes.
        # Hook check only applies to ~/.claude/hooks. So ~/.ssh should allow.
        assert d["decision"] == "allow"

    def test_hook_self_protection_immutable_under_none(self, project_root):
        """Hook self-protection is immutable even under profile: none."""
        config._cached_config = NahConfig(profile="none")
        paths._sensitive_paths_merged = False  # allow re-merge
        d = handle_write({"file_path": "~/.claude/hooks/guard.py", "content": "x"})
        assert d["decision"] == "block"

    def test_write_outside_hint(self, project_root):
        """Outside-project ask includes hint suggesting nah trust."""
        d = handle_write({"file_path": "/tmp/foo/bar.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "_hint" in d
        assert "nah trust" in d["_hint"]

    def test_write_nested_trusted(self, project_root):
        """Nested path inside trusted directory is allowed."""
        config._cached_config = NahConfig(trusted_paths=["/tmp"])
        d = handle_write({"file_path": "/tmp/deep/nested/file.txt", "content": "hello"})
        assert d["decision"] == "allow"

    def test_write_no_project_root(self):
        """No project root → ask with hint."""
        paths.set_project_root(None)
        d = handle_write({"file_path": "/tmp/file.txt", "content": "hello"})
        assert d["decision"] == "ask"
        assert "no git root" in d["reason"]
