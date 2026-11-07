from tools.validate_contribution import validate_header, validate_pr_body


def test_accepts_organization_commit_format() -> None:
    assert validate_header("feat(nlp): add sanitized log processing") == []


def test_rejects_invalid_type_and_scope() -> None:
    assert validate_header("feature(NLP): Added processing")


def test_requires_organization_pr_sections() -> None:
    errors = validate_pr_body("feat(nlp): add parser", "## Summary\nImplemented")
    assert any("## Verification" in error for error in errors)


def test_scoped_pr_is_not_treated_as_breaking() -> None:
    body = "\n".join((
        "## Summary",
        "Add parser",
        "## Related issue or task",
        "Task",
        "## Verification",
        "Passed",
        "## Notes for reviewers",
        "None",
    ))
    assert validate_pr_body("feat(nlp): add parser", body) == []


def test_breaking_pr_requires_migration_footer() -> None:
    body = "\n".join((
        "## Summary",
        "Change contract",
        "## Related issue or task",
        "Task",
        "## Verification",
        "Passed",
        "## Notes for reviewers",
        "Review migration",
    ))
    errors = validate_pr_body("feat(api)!: change processing contract", body)
    assert errors == ["breaking PR title requires a BREAKING CHANGE: footer"]

