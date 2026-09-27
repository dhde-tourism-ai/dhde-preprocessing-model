import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fetch_data", Path(__file__).resolve().parent.parent / "scripts" / "fetch_data.py")
fetch_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_data)


def test_required_repos_reads_repo_keys_and_csv_paths():
    cfgs = [
        {"sources": {"camera": {"gates": [{"person_csv": "people-repo/full/cam-a/Person.csv"},
                                          {"face_csv": "people-repo/full/cam-a/Face.csv"}]},
                     "hotel": {"repo": "hotel-repo"},
                     "survey": {"repo": "fukui-kanko-survey"}}},
        {"sources": {"camera": {"gates": [{"license_plate_csv": "people-repo/full/cam-b/LicensePlate.csv"}]}}},
    ]
    repos = fetch_data.required_repos(cfgs)
    assert repos["hotel-repo"] == []                               # whole repo
    assert repos["people-repo"] == ["full/cam-a/", "full/cam-b/"]  # only the sensor folders used
    assert repos["fukui-kanko-survey"] == ["all.csv", "area.csv"]  # known big repo, sparse


def test_every_configured_repo_is_covered():
    from dhde_preprocessing.config import list_configured_nodes, load_node_config
    repos = fetch_data.required_repos([load_node_config(k) for k in list_configured_nodes()])
    assert {"fukui-kanko-people-flow-data", "fukui-kanko-survey", "fukui-kanko-trend-data"} <= set(repos)
