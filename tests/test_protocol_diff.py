from snodo.compiler.models import Mode, Protocol, Validator
from snodo.protocols import list_templates, template_protocol
from snodo.protocols.diff import diff_protocols


def _protocol(**updates):
    values = dict(
        protocol_id="sample", name="Sample",
        modes=[Mode(mode_id="build", name="Build")],
        validators=[Validator(validator_id="check", validator_type="quality")],
        initial_mode="build",
    )
    values.update(updates)
    return Protocol(**values)


def test_identical_protocols_have_empty_diff():
    protocol = _protocol()
    assert diff_protocols(protocol, protocol).is_empty


def test_every_shipped_template_has_empty_self_diff():
    for name in list_templates():
        protocol = template_protocol(name)
        assert diff_protocols(protocol, protocol).is_empty, name


def test_reports_all_difference_categories():
    template = _protocol(validators=[Validator(validator_id="check", validator_type="quality", scope="wave")])
    project = _protocol(
        validators=[
            Validator(validator_id="check", validator_type="security", evaluation_phase="post_execute",
                      criteria=["changed"], tools=["read_file"], severity_cap="warn", scope="task"),
            Validator(validator_id="extra", validator_type="quality"),
        ],
        modes=[Mode(mode_id="build", name="Build", tools=["approve"], validators=["extra"],
                    transitions={"done": "review"}, delivery="push_branch"),
               Mode(mode_id="review", name="Review")],
        initial_mode="review", disagreement_policy="majority",
        protected_paths=["README.md"], write_allowed_prefixes=["src/"],
        global_constraints=[{"constraint_id": "rule", "description": "rule"}],
        execution={"delivery": "push_branch"},
    )
    result = diff_protocols(project, template)
    assert result.validators_only_in_project == ("extra",)
    assert result.changed_validators[0].validator_id == "check"
    assert set(result.changed_validators[0].changes) == {
        "validator_type", "evaluation_phase", "criteria", "tools", "severity_cap", "scope"
    }
    assert result.modes_added == ("review",)
    assert result.changed_modes[0].mode_id == "build"
    assert set(result.changed_modes[0].changes) == {"tools", "validators", "transitions", "delivery"}
    assert set(result.settings) == {
        "initial_mode", "disagreement_policy", "protected_paths", "write_allowed_prefixes",
        "global_constraints", "delivery",
    }


def test_reports_template_only_validators_and_removed_modes():
    project = _protocol(modes=[Mode(mode_id="other", name="Other")], validators=[Validator(validator_id="another", validator_type="quality")])
    result = diff_protocols(project, _protocol())
    assert result.validators_only_in_template == ("check",)
    assert result.modes_removed == ("build",)


def test_tool_order_does_not_count_as_change():
    template = _protocol(validators=[Validator(validator_id="check", validator_type="quality", tools=["read_file", "git_log"])])
    project = _protocol(validators=[Validator(validator_id="check", validator_type="quality", tools=["git_log", "read_file"])])
    assert diff_protocols(project, template).is_empty


def test_test_command_change_is_project_local():
    template = _protocol(validators=[Validator(validator_id="check", validator_type="quality", scope="wave", tooling={"test_command": "pytest"})])
    project = _protocol(validators=[Validator(validator_id="check", validator_type="quality", scope="wave", tooling={"test_command": "pytest tests/unit"})])
    result = diff_protocols(project, template)
    assert result.changed_validators == ()
    assert result.settings == {}
    assert result.project_local["validators.check.test_command"].template == "pytest"
    assert result.project_local["validators.check.test_command"].project == "pytest tests/unit"
