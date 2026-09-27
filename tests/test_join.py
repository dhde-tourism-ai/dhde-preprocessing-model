from dhde_preprocessing import join


def test_missing_data_repo_is_reported_not_raised(monkeypatch, tmp_path):
    # Regression: a teammate's build crashed with a raw FileNotFoundError
    # because the data repos weren't in the workspace root.
    monkeypatch.setenv("DHDE_WORKSPACE_ROOT", str(tmp_path))

    def _loader(cfg):
        raise FileNotFoundError(2, "No such file or directory", str(tmp_path / "some-repo/x.csv"))

    df, report = join._run_loader("camera", _loader, {"node_key": "n"})
    assert df is None and report.status == "error"
    assert any("some-repo/x.csv" in n.replace("\\", "/") for n in report.notes)
    assert any("scripts/fetch_data.py" in n and "DHDE_WORKSPACE_ROOT" in n for n in report.notes)
