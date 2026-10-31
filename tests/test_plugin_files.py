import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
MARKET = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class PluginFilesTests(unittest.TestCase):
    def test_versions_agree_everywhere(self):
        from picnic_mcp.server import SERVER_INFO

        self.assertEqual(PLUGIN["version"], PYPROJECT["project"]["version"])
        self.assertEqual(SERVER_INFO["version"], PYPROJECT["project"]["version"])

    def test_launcher_is_a_locked_uv_run_without_a_shell(self):
        server = PLUGIN["mcpServers"]["picnic"]
        self.assertEqual(server["command"], "uv")
        self.assertEqual(server["args"][:2], ["run", "--locked"])
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", server["args"])
        self.assertNotIn("-c", server["args"])

    def test_no_credential_is_configured_and_only_the_country_is_asked_for(self):
        self.assertEqual(set(PLUGIN["userConfig"]), {"country"})
        text = json.dumps(PLUGIN)
        for word in ("password", "token", "secret", "api_key"):
            self.assertNotIn(word, text.lower().replace("no password is entered here", ""), word)

    def test_country_options_match_what_the_client_supports(self):
        self.assertEqual(PLUGIN["userConfig"]["country"]["options"], ["nl", "de", "be", "fr"])
        self.assertIn(PLUGIN["userConfig"]["country"]["default"], PLUGIN["userConfig"]["country"]["options"])

    def test_plugin_name_is_a_valid_identifier_and_listed_in_the_marketplace(self):
        self.assertRegex(PLUGIN["name"], r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
        self.assertEqual([p["name"] for p in MARKET["plugins"]], [PLUGIN["name"]])

    def test_readme_license_and_skill_exist_with_enough_content(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        prose = re.sub(r"```.*?```", "", readme, flags=re.S)
        self.assertGreaterEqual(len(prose.split()), 40)
        self.assertTrue((ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"))
        skill = (ROOT / "skills" / "picnic-shopping" / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"---\nname: ([\w-]+)\ndescription: (.+)\n---\n", skill.replace("\r\n", "\n"))
        self.assertTrue(match, "SKILL.md needs name and description front matter")

    def test_skill_mentions_only_tools_that_exist(self):
        from picnic_mcp.server import TOOLS

        skill = (ROOT / "skills" / "picnic-shopping" / "SKILL.md").read_text(encoding="utf-8")
        for name in set(re.findall(r"`([a-z_]+)`", skill)):
            if name in {"kind", "needed", "probably_at_home", "pantry", "alternative", "loose"}:
                continue
            self.assertIn(name, TOOLS, f"skill mentions unknown tool {name}")

    def test_readme_lists_every_tool(self):
        from picnic_mcp.server import TOOLS

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in TOOLS:
            self.assertIn(f"`{name}`", readme, name)

    def test_text_files_contain_no_em_dashes(self):
        files = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in {".py", ".md", ".json", ".toml", ".yml"} and ".venv" not in p.parts and ".git" not in p.parts and "__pycache__" not in p.parts]
        offenders = [str(p.relative_to(ROOT)) for p in files if chr(0x2014) in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
