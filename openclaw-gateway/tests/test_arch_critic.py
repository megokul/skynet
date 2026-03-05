from pathlib import Path

from orchestration.arch_critic import evaluate_architecture_refs, load_arch_rules


def test_arch_critic_detects_layer_violation():
    rules = {
        "layers": [
            {
                "name": "frontend",
                "path_prefixes": ["frontend"],
                "can_import_layers": ["shared"],
            },
            {
                "name": "backend",
                "path_prefixes": ["backend"],
                "can_import_layers": ["shared"],
            },
            {
                "name": "shared",
                "path_prefixes": ["shared"],
                "can_import_layers": ["shared"],
            },
        ]
    }
    refs = [
        {"from_path": "frontend/app/main.ts", "to_module": "backend.auth.service", "ref_kind": "import"},
    ]
    findings = evaluate_architecture_refs(refs=refs, rules=rules)
    assert findings
    assert findings[0]["code"] == "ARCH_LAYER_VIOLATION"
    assert findings[0]["severity"] == "high"


def test_load_arch_rules_reads_json_yaml_payload(tmp_path: Path):
    rules_file = tmp_path / "architecture_rules.yaml"
    rules_file.write_text(
        '{"layers":[{"name":"frontend","path_prefixes":["frontend"],"can_import_layers":["shared"]}]}',
        encoding="utf-8",
    )
    loaded = load_arch_rules(str(rules_file))
    assert loaded["layers"][0]["name"] == "frontend"
